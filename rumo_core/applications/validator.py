"""Anti-invention validator for document lines (spec section 2).

A line is valid only when everything it states can be traced to the profile
items it cites (source_refs). Common words are free, so the line may be
rephrased or translated. Checked, in order:

1. source: every cited id exists in this profile version (and so does a bullet's parent);
2. numbers, dates, percentages and spelled-out numbers or months: each must appear in the cited items;
3. technical terms, proper names and posting skill words: each must appear in the profile
   (the job's exact title and company are allowed);
4. e-mail, link or phone: must be the profile's own;
5. claims of years of experience or of a level: must have a basis in the cited items or the profile.

Pure: no DB, no LLM. Reasons are PT-BR strings for the person to read.
"""

import re
from dataclasses import dataclass

from rumo_core.applications.lines import DocLine
from rumo_core.applications.profile_index import full_profile_text, item_texts
from rumo_core.interview.suggestions import lexicon_supported, numbers_supported
from rumo_core.matching.ai_score import (
    _BARE_DOMAIN,
    _CONTACT,
    _SENTENCE_SPLIT,
    JobView,
    _fold,
    _job_skill_words,
    _known,
    _salient_terms_at,
    _technical,
    _words,
)
from rumo_core.profile.schema import CanonicalProfile

MAX_LINE_CHARS = 600

R_SOURCE = "Sem origem no seu perfil"
R_NUMBER = "Número, data ou valor que não está no item citado: {token}"
R_TERM = "Termo que não está no seu perfil: {token}"
R_CONTACT = "E-mail, link ou telefone que não é do seu perfil"
R_CLAIM = "Afirma anos de experiência ou nível que o seu perfil não traz"
R_EMPTY = "Linha vazia"
R_LONG = "Linha longa demais (máx. 600 caracteres)"

# Equivalent level words (accent-folded). A level claim needs its group in the profile.
LEVEL_GROUPS: tuple[tuple[str, ...], ...] = (
    ("senior", "sênior"),
    ("pleno", "mid-level"),
    ("junior", "júnior"),
    ("especialista", "expert", "experte"),
    ("fluente", "fluent", "fliessend", "fließend"),
    ("nativo", "native", "muttersprache", "muttersprachlich"),
    ("avancado", "advanced", "fortgeschritten"),
    ("lider", "lead", "leitend"),
    ("principal", "staff"),
)

_NUM_WORDS = (
    "um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|quinze|"
    "vinte|trinta|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    "fifteen|twenty|thirty|ein|eins|zwei|drei|vier|funf|sechs|sieben|acht|neun|zehn|"
    "elf|zwolf|zwanzig"
)
_YEARS_CLAIM = re.compile(
    rf"(?<!\w)(\d+(?:[.,]\d+)?|{_NUM_WORDS})\s+(anos?|years?|jahre?n?)(?!\w)"
)
_YEAR_UNIT = re.compile(r"(?<!\w)(anos?|years?|jahre?n?)(?!\w)")
_NUM_TOKEN = re.compile(r"\d+(?:[.,]\d+)*%?")
_WORD = re.compile(r"\w+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_DE_MULTI = re.compile(r"(?<!\w)[A-ZÄÖÜ]\w*(?:\s+[A-ZÄÖÜ]\w*)+")
_DE_SUFFIX = re.compile(
    r"(?<!\w)([A-ZÄÖÜ]\w*)\s+(?:GmbH|AG|SE|KG|Inc|Ltd|LLC|S\.A\.)(?!\w)"
)


@dataclass(frozen=True)
class ValidationContext:
    index: dict[str, str]
    profile_folded: str
    profile_words: frozenset[str]
    contacts_folded: str
    phones: tuple[str, ...]
    job_skill_words: frozenset[str]
    job_exact: tuple[str, ...]
    locale: str


def build_context(profile: CanonicalProfile, job: JobView | None, locale: str) -> ValidationContext:
    index = item_texts(profile)
    contacts = [
        profile.basics.email,
        *profile.basics.links,
        *(p.url for p in profile.projects),
        profile.basics.phone,
    ]
    contacts_text = "\n".join(c for c in contacts if c and c.strip())
    haystack = "\n".join([
        full_profile_text(profile),
        *index.values(),
        profile.basics.name,
        profile.basics.location,
        profile.basics.headline,
        contacts_text,
    ])
    folded = _fold(haystack).casefold()
    phone_digits = re.sub(r"\D", "", profile.basics.phone or "")
    phones = (phone_digits,) if len(phone_digits) >= 8 else ()
    return ValidationContext(
        index=index,
        profile_folded=folded,
        profile_words=frozenset(_words(haystack)),
        contacts_folded=_fold(contacts_text).casefold(),
        phones=phones,
        job_skill_words=frozenset(_job_skill_words(job)),
        job_exact=tuple(v.strip() for v in ((job.title, job.company_name) if job else ()) if v and v.strip()),
        locale=locale,
    )


def _has(word: str, hay_folded: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(_fold(word).casefold()) + r"(?!\w)"
    return re.search(pattern, hay_folded) is not None


def _in_profile(token: str, ctx: ValidationContext) -> bool:
    base = _fold(token).casefold()
    candidates = {base}
    if len(base) > 3 and base.endswith("s"):
        candidates.add(base[:-1])  # APIs -> API
    return any(_has(c, ctx.profile_folded) for c in candidates)


def _strip_job_exact(text: str, exact: tuple[str, ...]) -> str:
    for value in exact:
        text = re.sub(r"(?<!\w)" + re.escape(value) + r"(?!\w)", " ", text, flags=re.IGNORECASE)
    return text


def _number_reasons(text: str, cited: list[str]) -> list[str]:
    out = []
    for m in _NUM_TOKEN.finditer(text):
        if not numbers_supported(m.group(0), cited):
            out.append(R_NUMBER.format(token=m.group(0)))
    for m in _WORD.finditer(text):
        if not lexicon_supported(m.group(0), cited):
            out.append(R_NUMBER.format(token=m.group(0)))
    return out


def _checked_terms(sentence: str, locale: str) -> list[str]:
    salient = [t for t, _ in _salient_terms_at(sentence)]
    if locale != "de":
        return salient
    # German capitalises every noun, so a lone capitalised noun is not a proper name
    out = [t for t in salient if _technical(t) or t.isupper()]
    lead = len(sentence) - len(sentence.lstrip())
    for m in _DE_MULTI.finditer(sentence):
        words = m.group(0).split()
        if m.start() == lead:
            words = words[1:]  # sentence-initial capital is grammar, not a name
        out.extend(words)
    out.extend(m.group(1) for m in _DE_SUFFIX.finditer(sentence))
    return list(dict.fromkeys(out))


def _term_reasons(stripped: str, ctx: ValidationContext) -> list[str]:
    out: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(stripped):
        if not sentence.strip():
            continue
        for token in _checked_terms(sentence, ctx.locale):
            if not _in_profile(token, ctx):
                out.append(R_TERM.format(token=token))
    for word in sorted(_words(stripped)):
        if word in ctx.job_skill_words and not _known(word, ctx.profile_words):
            out.append(R_TERM.format(token=word))
    return list(dict.fromkeys(out))


def _contact_reasons(text: str, ctx: ValidationContext) -> list[str]:
    for m in _CONTACT.finditer(text):
        if _fold(m.group(0)).casefold().rstrip(".,;:)") not in ctx.contacts_folded:
            return [R_CONTACT]
    for m in _BARE_DOMAIN.finditer(text):
        if m.group(1).isupper():
            continue  # ASP.NET, Socket.IO
        domain = _fold(m.group(0)).casefold()
        if domain not in ctx.contacts_folded and domain not in ctx.profile_folded:
            return [R_CONTACT]
    for m in _PHONE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 10 and not any(digits.endswith(p) or p.endswith(digits) for p in ctx.phones):
            return [R_CONTACT]
    return []


def _claim_reasons(stripped: str, cited: list[str], ctx: ValidationContext) -> list[str]:
    out: list[str] = []
    folded = _fold(stripped).casefold()
    cited_folded = _fold(" ".join(cited)).casefold()
    for m in _YEARS_CLAIM.finditer(folded):
        number = m.group(1)
        basis = numbers_supported(number, cited) or lexicon_supported(number, cited)
        if not (basis and _YEAR_UNIT.search(cited_folded)):
            out.append(R_CLAIM)
            break
    for group in LEVEL_GROUPS:
        if any(_has(w, folded) for w in group) and not any(_has(w, ctx.profile_folded) for w in group):
            out.append(R_CLAIM)
    return list(dict.fromkeys(out))


def validate_line(line: DocLine, ctx: ValidationContext) -> list[str]:
    if line.origin == "fact":
        return []
    text = line.text.strip()
    if not text:
        return [R_EMPTY]
    if len(line.text) > MAX_LINE_CHARS:
        return [R_LONG]
    refs = line.source_refs
    if (
        not refs
        or any(ref not in ctx.index for ref in refs)
        or (line.parent is not None and line.parent not in ctx.index)
    ):
        return [R_SOURCE]

    cited = [ctx.index[ref] for ref in refs]
    stripped = _strip_job_exact(text, ctx.job_exact)
    reasons: list[str] = []
    reasons += _number_reasons(text, cited)
    reasons += _term_reasons(stripped, ctx)
    reasons += _contact_reasons(text, ctx)
    reasons += _claim_reasons(stripped, cited, ctx)
    return list(dict.fromkeys(reasons))


def validate_lines(lines: list[DocLine], ctx: ValidationContext) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in lines:
        reasons = validate_line(line, ctx)
        if reasons:
            out[line.id] = reasons
    return out
