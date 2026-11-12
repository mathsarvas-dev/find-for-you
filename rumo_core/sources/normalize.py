"""Rule-based normalisation of raw job postings.

Pure functions: no network, no database. Conservative by design: whenever a
rule is unsure it returns unknown/None, because a wrong value is worse than a
missing one.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser

from rumo_core.sources.base import RawJob

# --------------------------------------------------------------------------
# html_to_text
# --------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "template"}
_BLOCK_TAGS = {
    "p", "br", "li", "ul", "ol", "div", "tr", "table", "section", "article",
    "header", "footer", "blockquote", "pre", "hr", "dt", "dd", "h1", "h2",
    "h3", "h4", "h5", "h6",
}  # fmt: skip
_INLINE_WS = re.compile(r"[ \t\r\f\v ​]+")
_TAG_RE = re.compile(r"<[^>]*>")


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip: str | None = None

    def handle_starttag(self, tag, attrs):
        if self._skip is not None:
            return
        if tag in _SKIP_TAGS:
            self._skip = tag
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if self._skip is None and tag in _BLOCK_TAGS and tag != "li":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self._skip is not None:
            if tag == self._skip:
                self._skip = None
            return
        if tag in _BLOCK_TAGS and tag != "li":
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip is None:
            self.parts.append(data.replace("\n", " "))


def _tidy(text: str) -> str:
    lines = [_INLINE_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    bullet = False
    for ln in lines:
        if ln == "-":
            bullet = True
            continue
        if not ln:
            if bullet:
                continue
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(("- " + ln) if bullet else ln)
        bullet = False
    return "\n".join(out).strip()


def html_to_text(html: str) -> str:
    if not html:
        return ""
    try:
        parser = _TextParser()
        parser.feed(html)
        parser.close()
        raw = "".join(parser.parts)
    except Exception:  # noqa: BLE001 - malformed input must never raise
        raw = unescape(_TAG_RE.sub("\n", html))
    return _tidy(raw)


# --------------------------------------------------------------------------
# parse_location
# --------------------------------------------------------------------------

_COUNTRY_NAMES: dict[str, list[str]] = {
    "US": ["us", "usa", "united states", "united states of america", "eua",
           "estados unidos", "estados unidos da america", "vereinigte staaten"],
    "CA": ["canada", "kanada"],
    "MX": ["mexico", "mexiko"],
    "BR": ["brazil", "brasil", "brasilien"],
    "AR": ["argentina", "argentinien"],
    "CL": ["chile"],
    "CO": ["colombia", "kolumbien"],
    "GB": ["uk", "united kingdom", "great britain", "england", "scotland", "wales",
           "reino unido", "grossbritannien", "vereinigtes konigreich"],
    "IE": ["ireland", "irland", "irlanda"],
    "DE": ["germany", "deutschland", "alemanha", "alemania"],
    "AT": ["austria", "osterreich"],
    "CH": ["switzerland", "schweiz", "suica", "suisse"],
    "FR": ["france", "frankreich", "franca"],
    "ES": ["spain", "espana", "spanien", "espanha"],
    "PT": ["portugal"],
    "IT": ["italy", "italia", "italien"],
    "NL": ["netherlands", "the netherlands", "niederlande", "holanda",
           "paises baixos", "holland"],
    "BE": ["belgium", "belgien", "belgica"],
    "SE": ["sweden", "schweden", "suecia"],
    "NO": ["norway", "norwegen", "noruega"],
    "DK": ["denmark", "danemark", "dinamarca"],
    "FI": ["finland", "finnland", "finlandia"],
    "PL": ["poland", "polen", "polonia"],
    "CZ": ["czech republic", "czechia", "tschechien"],
    "RO": ["romania", "rumanien"],
    "IN": ["india", "indien"],
    "SG": ["singapore", "singapur", "singapura"],
    "JP": ["japan", "japao"],
    "AU": ["australia", "australien"],
    "NZ": ["new zealand", "neuseeland", "nova zelandia"],
    "IL": ["israel"],
    "AE": ["united arab emirates", "uae", "vereinigte arabische emirate",
           "emirados arabes unidos"],
    "ZA": ["south africa", "sudafrika", "africa do sul"],
}  # fmt: skip
_CODES = set(_COUNTRY_NAMES)
_NAME_TO_CODE = {n: c for c, names in _COUNTRY_NAMES.items() for n in names}
_US_STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)  # fmt: skip
_AMBIGUOUS = _US_STATES & _CODES  # CA, DE, IN, AR, CO, IL
# (folded city, ambiguous code) -> country; only well-known, unambiguous cities.
_CITY_HINTS: dict[tuple[str, str], str] = {
    **{(c, "CA"): "US" for c in ("san francisco", "los angeles", "san diego", "san jose", "sacramento", "oakland", "palo alto", "mountain view", "sunnyvale", "santa clara", "irvine", "santa monica")},
    **{(c, "CA"): "CA" for c in ("toronto", "vancouver", "montreal", "ottawa", "calgary")},
    **{(c, "CO"): "US" for c in ("denver", "boulder")},
    **{(c, "IL"): "US" for c in ("chicago",)},
    **{(c, "IL"): "IL" for c in ("tel aviv", "jerusalem", "haifa")},
    **{(c, "IN"): "US" for c in ("indianapolis",)},
    **{(c, "IN"): "IN" for c in ("bangalore", "bengaluru", "mumbai", "delhi", "new delhi", "hyderabad", "pune", "chennai")},
    **{(c, "DE"): "DE" for c in ("berlin", "munich", "munchen", "hamburg", "frankfurt", "cologne", "koln")},
    **{(c, "AR"): "AR" for c in ("buenos aires", "cordoba")},
}  # fmt: skip
_REGION_WORDS = {
    "remote", "remoto", "hybrid", "anywhere", "worldwide", "global", "emea",
    "apac", "latam", "europe", "americas", "north america", "international",
}  # fmt: skip
_MULTI_SPLIT = re.compile(r"\s*;\s*|\s*\|\s*|\s+or\s+|\s+/\s+")
_REMOTE_PREFIX = re.compile(
    r"^(?:fully\s+)?(?:remote|remoto)(?:\s*[-–—:,]\s*|\s*\(\s*|\s+(?:in|within|from)\s+|\s+)(?P<r>.+?)\s*\)?$",
    re.I,
)
_REMOTE_SUFFIX = re.compile(r"^(?P<r>.+?)\s*[-–—,(]?\s*\(?\s*(?:remote|remoto)\s*\)?$", re.I)


def _fold(text: str) -> str:
    d = unicodedata.normalize("NFKD", text)
    d = "".join(ch for ch in d if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", d.casefold().replace(".", "")).strip()


def _country(token: str, short_ok: bool) -> str | None:
    f = _fold(token)
    code = _NAME_TO_CODE.get(f)
    if code:
        return code
    up = f.upper()
    if short_ok and len(f) == 2 and up in _CODES and up not in _AMBIGUOUS:
        return up
    return None


def _clean_city(city: str) -> str | None:
    city = city.strip()
    if not city or not re.search(r"[^\W\d_]", city) or _fold(city) in _REGION_WORDS:
        return None
    return city


def _parse_single(text: str) -> tuple[str | None, str | None]:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    none = (None, None)
    if not parts or len(parts) > 3:
        return none
    if len(parts) == 1:
        code = _country(parts[0], short_ok=False)
        return (code, None) if code else none
    city, last = parts[0], parts[-1]
    if len(parts) == 3:
        code = _country(last, short_ok=False)
        if not code:
            return none
        if code == "US" and parts[1].upper() not in _US_STATES:
            return none
        return code, _clean_city(city)
    code = _country(last, short_ok=False)
    if code:
        return code, _clean_city(city)
    up = last.upper()
    if len(last) == 2 and last.isalpha():
        if up in _US_STATES and up not in _AMBIGUOUS:
            return "US", _clean_city(city)
        if up in _AMBIGUOUS:
            hint = _CITY_HINTS.get((_fold(city), up))
            return (hint, _clean_city(city)) if hint else none
        code = _country(last, short_ok=True)
        if code:
            return code, _clean_city(city)
    return none


def parse_location(text: str) -> tuple[str | None, str | None]:
    """Return (ISO alpha-2 country, city); (None, None) when not unambiguous."""
    if not text or not text.strip():
        return None, None
    first = _MULTI_SPLIT.split(text.strip())[0].strip()
    if not first:
        return None, None
    m = _REMOTE_PREFIX.match(first) or _REMOTE_SUFFIX.match(first)
    if m:
        return _parse_single(m.group("r"))
    return _parse_single(first)


# --------------------------------------------------------------------------
# detect_work_model
# --------------------------------------------------------------------------

_STRONG = {
    "remote": re.compile(r"\bremote(?:ly)?\b|\bremot[oa]\b|\bhome[- ]?office\b|\bwork from home\b|\bteletrabalho\b", re.I),
    "hybrid": re.compile(r"\bhybrid\b|\bh[ií]brid[oa]\b|\bhybride\b", re.I),
    "onsite": re.compile(r"\bon[- ]?site\b|\bpresencial\b|\bvor ort\b", re.I),
}  # fmt: skip
_EXPLICIT = {
    "remote": re.compile(
        r"\b(?:fully|100%|completely|entirely|totalmente|vollst[aä]ndig|komplett)\s+remot[eo]\b"
        r"|\b100\s?%\s+remot[eo]\b"
        r"|\bthis is an?\s+(?:fully\s+|100%\s+)?remote\s+(?:position|role|job|opportunity)\b",
        re.I,
    ),
    "hybrid": re.compile(
        r"\bhybrid\s+(?:role|position|work\s+model|work\s+schedule|setup|model)\b"
        r"|\bthis is an?\s+hybrid\b"
        r"|\b(?:modelo|regime|trabalho|vaga|posi[cç][aã]o)\s+h[ií]brid[oa]\b"
        r"|\bhybrid(?:es)?\s+(?:arbeit\w*|modell)\b",
        re.I,
    ),
    "onsite": re.compile(
        r"\bon-?site\s+(?:required|role|position)\b"
        r"|\bthis (?:role|position|job)? ?is\s+(?:an?\s+)?on-?site\b"
        r"|\b(?:vaga|trabalho|modelo|regime|100%|totalmente)\s+presencial\b"
        r"|\b(?:arbeit\w*|stelle|t[aä]tigkeit)\s+vor ort\b",
        re.I,
    ),
}  # fmt: skip
_NEGATION = re.compile(r"(?:\bnot|n't|\bnicht|\bn[aã]o|\bnever)\s+(?:a\s+|an\s+)?$", re.I)


def _signals(patterns: dict[str, re.Pattern], text: str, check_negation: bool) -> set[str]:
    found = set()
    for kind, pat in patterns.items():
        for m in pat.finditer(text):
            if check_negation and _NEGATION.search(text[max(0, m.start() - 12) : m.start()]):
                continue
            found.add(kind)
            break
    return found


def detect_work_model(location_text: str, title: str, description: str) -> str:
    strong = _signals(_STRONG, f"{title or ''}\n{location_text or ''}", False)
    if len(strong) == 1:
        return next(iter(strong))
    if len(strong) > 1:
        return "unknown"
    explicit = _signals(_EXPLICIT, description or "", True)
    return next(iter(explicit)) if len(explicit) == 1 else "unknown"


# --------------------------------------------------------------------------
# detect_seniority
# --------------------------------------------------------------------------

_LEAD_ROLE = r"(?:engineer|developer|designer|architect|scientist|researcher|programmer|analyst|software|devops|sre)"
_SENIORITY_RULES: list[tuple[str, re.Pattern]] = [
    ("intern", re.compile(r"\b(?:intern|internship|estagi[aá]ri[oa]|estagio|praktikant(?:in)?|werkstudent\w*|trainee)\b")),
    ("director", re.compile(r"\b(?:head of|vp|vice president|director|diretor[a]?|direktor(?:in)?)\b")),
    ("manager", re.compile(r"\b(?:manager|gerente)\b")),
    ("lead", re.compile(
        r"\bprincipal\b"
        r"|\bstaff\b(?!\s+(?:accountant|nurse|attorney|writer|assistant|augmentation|auditor))"
        r"|\b(?:team|tech|technical|engineering|squad|tribe)\s+lead\b"
        r"|\bl[ií]der\s+t[eé]cnico\b"
        rf"|\blead\s+{_LEAD_ROLE}\b"
        r"|\blead$"
    )),
    ("senior", re.compile(r"\b(?:senior|sr|snr|s[eê]nior)\b")),
    ("junior", re.compile(r"\b(?:junior|jr|j[uú]nior|entry[- ]level)\b")),
    ("mid", re.compile(r"\b(?:pleno|mid[- ]?level|intermedi[aá]rio)\b")),
]  # fmt: skip


def detect_seniority(title: str) -> str:
    t = (title or "").casefold().strip()
    t = re.sub(r"[\s.,;:()\[\]|/-]+$", "", t)  # trailing punctuation: "Team Lead)"
    for level, pat in _SENIORITY_RULES:
        if pat.search(t):
            return level
    return "unknown"


# --------------------------------------------------------------------------
# detect_language
# --------------------------------------------------------------------------

_STOPWORDS = {
    "en": set("the and of to in is are you we our with for that this will have be as on your team who work from their an by or at it can about".split()),
    "pt": set("de da do das dos e o os as um uma para com em na no nas nos que por se ao sua seu nossa nosso você vocês é ser são mais como também não equipe trabalho experiência".split()),
    "de": set("der die das und ist sind wir du sie ein eine einen mit für von zu im in den dem des auf bei auch nicht werden oder als wie aus uns ihre unser unsere deine dein nach über mehr sich dich um".split()),
}  # fmt: skip
_shared = {w for w in set().union(*_STOPWORDS.values()) if sum(w in s for s in _STOPWORDS.values()) > 1}
_STOPWORDS = {k: v - _shared for k, v in _STOPWORDS.items()}
_WORD = re.compile(r"[^\W\d_]+")


def detect_language(text: str) -> str:
    sample = (text or "")[:1500]
    short = len(sample.strip()) < 200
    words = [w.casefold() for w in _WORD.findall(sample)]
    counts = sorted(((sum(w in sw for w in words), lang) for lang, sw in _STOPWORDS.items()), reverse=True)
    (best, lang), (second, _) = counts[0], counts[1]
    min_hits, margin = (4, 2.0) if short else (6, 1.5)
    if best < min_hits or best < margin * second:
        return "unknown"
    return lang


# --------------------------------------------------------------------------
# salary
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SalaryInfo:
    """Single stated amount -> min only (max None); ranges fill both."""

    min: Decimal | None
    max: Decimal | None
    currency: str
    period: str | None


_PAY_RE = re.compile(
    r"\b(?:salary|salaries|compensation|pay\s+range|pay\s+band|pay\s+rate|base\s+pay|wages?|"
    r"remuneration|ote|on-target\s+earnings|per\s+(?:year|annum|hour|month)|annual(?:ly)?|"
    r"hourly|gehalt|jahresgehalt|verg[uü]tung|bezahlung|brutto|pro\s+(?:jahr|stunde|monat)|"
    r"sal[aá]rio|remunera[cç][aã]o|ao\s+ano|por\s+(?:m[eê]s|hora|ano)|salaire|sueldo)\b"
    r"|/\s?(?:yr|year|hr|hour|month|mo|h)\b",
    re.I,
)


def has_pay_context(text: str) -> bool:
    return bool(text) and _PAY_RE.search(text) is not None


_SYMBOLS = {"US$": "USD", "R$": "BRL", "A$": "AUD", "C$": "CAD", "NZ$": "NZD", "S$": "SGD", "$": "USD", "€": "EUR", "£": "GBP"}
_CODES_CUR = "USD|EUR|GBP|BRL|AUD|CAD|CHF|NZD|SGD|SEK|NOK|DKK|PLN|INR|MXN|JPY"
_CUR = rf"(?:(?<![A-Za-z])(?:US\$|R\$|A\$|C\$|NZ\$|S\$|\$)|€|£|(?<![A-Za-z])(?:{_CODES_CUR})(?![A-Za-z]))"
_NUM = r"\d[\d.,]*\d|\d"
_K = r"[kK](?![A-Za-z0-9])"
_RANGE_SEP = r"(?:\s*[-–—]\s*|\s+(?:to|bis|a|at[eé])\s+)"
_SALARY_RE = re.compile(
    rf"(?P<c1>{_CUR})\s?(?P<a1>{_NUM})(?P<k1>{_K})?"
    rf"(?:{_RANGE_SEP}(?P<c2>{_CUR})?\s?(?P<a2>{_NUM})(?P<k2>{_K})?)?"
    r"|"
    rf"(?P<b1>{_NUM})(?P<bk1>{_K})?"
    rf"(?:{_RANGE_SEP}(?P<b2>{_NUM})(?P<bk2>{_K})?)?"
    r"\s?(?P<c3>€|EUR\b|USD\b|GBP\b|BRL\b|CHF\b|£|euros?\b|reais\b|dollars?\b)",
    re.I,
)
_CUR_WORDS = {"euro": "EUR", "euros": "EUR", "reais": "BRL", "dollar": "USD", "dollars": "USD"}
_UNIT_AFTER = re.compile(r"\s?(?:billion|million|trillion|bn|mm|[bm])\b", re.I)
_NON_PAY = re.compile(r"\b(?:equity|stock|bonus|rsus?|funding|raised|valuation|arr|revenue|options|investment)\b", re.I)
_CODE_BEFORE = re.compile(rf"(?<![A-Za-z])({_CODES_CUR})\s?$")
_CODE_AFTER = re.compile(rf"^\s?\(?({_CODES_CUR})(?![A-Za-z])")
_PERIODS = [
    ("year", re.compile(r"^\s?(?:/\s?(?:yr|year|annum|ano|jahr)\b|(?:per|a|pro|por|ao)\s+(?:year|annum|ano|jahr)\b|annual(?:ly)?\b|p\.?a\.?\b|j[aä]hrlich\b)", re.I)),
    ("month", re.compile(r"^\s?(?:/\s?(?:mo|month|m[eê]s|monat)\b|(?:per|a|pro|por|ao)\s+(?:month|m[eê]s|monat)\b|monthly\b|mensal\b|monatlich\b)", re.I)),
    ("hour", re.compile(r"^\s?(?:/\s?(?:hr|hour|h|hora|stunde)\b|(?:per|an|pro|por)\s+(?:hour|hora|stunde)\b|hourly\b|st[uü]ndlich\b)", re.I)),
]  # fmt: skip
_PERIOD_BEFORE = [
    ("year", re.compile(r"\b(?:annual|yearly|jahres\w*|anual)\W{0,3}$", re.I)),
    ("hour", re.compile(r"\bhourly\W{0,3}$", re.I)),
    ("month", re.compile(r"\bmonthly\W{0,3}$", re.I)),
]


def _parse_amount(s: str, k: bool) -> Decimal | None:
    try:
        if "." in s and "," in s:
            dec = "." if s.rfind(".") > s.rfind(",") else ","
            thou = "," if dec == "." else "."
            s = s.replace(thou, "").replace(dec, ".")
        else:
            sep = "." if "." in s else ("," if "," in s else None)
            if sep:
                groups = s.split(sep)
                if len(groups) > 2:
                    if not all(len(g) == 3 for g in groups[1:]) or not 1 <= len(groups[0]) <= 3:
                        return None
                    s = "".join(groups)
                elif len(groups[1]) == 3 and 1 <= len(groups[0]) <= 3 and not k:
                    s = "".join(groups)
                elif len(groups[1]) in (1, 2) and groups[0]:
                    s = groups[0] + "." + groups[1]
                else:
                    return None
        value = Decimal(s)
    except InvalidOperation:
        return None
    return value * 1000 if k else value


def _pre_clause(text: str, pos: int) -> str:
    before = text[max(0, pos - 40) : pos]
    cut = max(before.rfind("\n"), before.rfind(";"), before.rfind(". "))
    return before[cut + 1 :]


def _post_clause(text: str, pos: int) -> str:
    after = text[pos : pos + 25]
    m = re.search(r"[,;\n]|\.\s", after)
    return after[: m.start()] if m else after


def extract_salary(text: str) -> SalaryInfo | None:
    """First explicit salary (range or amount with pay context); None if unsure."""
    for m in _SALARY_RE.finditer(text or ""):
        if m.group("a1") is not None:
            sym, a1, k1, a2, k2 = m.group("c1"), m.group("a1"), m.group("k1"), m.group("a2"), m.group("k2")
            c2 = m.group("c2")
            code = _SYMBOLS.get(sym.upper()) or sym.upper()
            if c2 and (_SYMBOLS.get(c2.upper()) or c2.upper()) != code:
                continue
        else:
            a1, k1, a2, k2 = m.group("b1"), m.group("bk1"), m.group("b2"), m.group("bk2")
            raw = m.group("c3")
            code = _SYMBOLS.get(raw.upper()) or _CUR_WORDS.get(raw.lower()) or raw.upper()
        end = m.end()
        before_txt, after_txt = text[: m.start()], text[end:]
        if _UNIT_AFTER.match(after_txt):
            continue
        if _NON_PAY.search(_pre_clause(text, m.start())) or _NON_PAY.search(_post_clause(text, end)):
            continue
        cb, ca = _CODE_BEFORE.search(before_txt[-5:]), _CODE_AFTER.match(after_txt)
        explicit = (cb or ca)
        if explicit:
            code = explicit.group(1).upper()
        lo, hi = _parse_amount(a1, bool(k1)), (_parse_amount(a2, bool(k2)) if a2 else None)
        if lo is None or (a2 and hi is None):
            continue
        if hi is not None:
            if k2 and not k1 and lo < 1000:
                lo *= 1000
            elif k1 and not k2 and hi < 1000:
                hi *= 1000
        window = text[max(0, m.start() - 80) : end + 80]
        if hi is None and not _PAY_RE.search(window):
            continue
        if lo <= 0 or (hi is not None and (hi <= 0 or lo > hi)):
            return None
        period = None
        for name, pat in _PERIODS:
            if pat.match(after_txt):
                period = name
                break
        if period is None:
            tail = before_txt[-30:]
            for name, pat in _PERIOD_BEFORE:
                if pat.search(tail):
                    period = name
                    break
        return SalaryInfo(min=lo, max=hi, currency=code, period=period)
    return None


# --------------------------------------------------------------------------
# normalize_job
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedJob:
    description: str
    country: str | None
    city: str | None
    work_model: str
    seniority: str
    language: str
    department: str | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    salary_period: str | None
    salary_known: bool
    content_hash: str
    fingerprint: str


def _sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _fp_norm(text: str) -> str:
    """NFKC, lowercase, punctuation -> space, collapsed whitespace (accents kept)."""
    t = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"\s+", " ", re.sub(r"[\W_]+", " ", t)).strip()


def normalize_job(raw: RawJob) -> NormalizedJob:
    description = html_to_text(raw.description_html)
    country, city = parse_location(raw.location_text)
    salary = extract_salary(description)
    return NormalizedJob(
        description=description,
        country=country,
        city=city,
        work_model=detect_work_model(raw.location_text, raw.title, description),
        seniority=detect_seniority(raw.title),
        language=detect_language(f"{raw.title}\n{description}"),
        department=raw.department,
        salary_min=salary.min if salary else None,
        salary_max=salary.max if salary else None,
        salary_currency=salary.currency if salary else None,
        salary_period=salary.period if salary else None,
        salary_known=salary is not None,
        # sha256 of raw (title, location_text, description text, department)
        content_hash=_sha(raw.title, raw.location_text, description, raw.department or ""),
        # sha256 of (normalised title, normalised location_text); id-independent
        fingerprint=_sha(_fp_norm(raw.title), _fp_norm(raw.location_text)),
    )
