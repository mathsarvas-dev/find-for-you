"""Rule-based normalisation of raw job postings.

Pure functions: no network, no database. Conservative by design: whenever a
rule is unsure it returns unknown/None, because a wrong value is worse than a
missing one.

All inputs come from untrusted third-party text, so every public function
caps its input (see the _MAX_* constants) and uses only linear-time regexes.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser

from rumo_core.sources.base import RawJob

_MAX_TITLE = 300
_MAX_LOCATION = 300
_MAX_DEPARTMENT = 200
_MAX_HTML = 200_000
_MAX_PAY_TEXT = 60_000
_MAX_SCAN = 20_000
_MAX_CANDIDATES = 300


def _cap(text, limit: int) -> str:
    return text[:limit] if isinstance(text, str) else ""


def _ws(text: str) -> str:
    return " ".join(text.split())


# --------------------------------------------------------------------------
# html_to_text
# --------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "template"}
_BLOCK_TAGS = {
    "p", "br", "ul", "ol", "div", "tr", "table", "section", "article",
    "header", "footer", "blockquote", "pre", "hr", "dt", "dd", "h1", "h2",
    "h3", "h4", "h5", "h6",
}  # fmt: skip
_CELL_TAGS = {"td", "th"}
_BULLET = "\x01"
_INLINE_WS = re.compile(r"[ \t\r\f\v ​]+")
_TAG_RE = re.compile(r"<[^>]*>")


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip: str | None = None
        self._text_chars = 0  # non-space characters emitted so far
        self._li: list[tuple[int, int]] = []  # (index in parts, text_chars) per open <li>

    def handle_starttag(self, tag, attrs):
        if self._skip is not None:
            return
        if tag in _SKIP_TAGS:
            self._skip = tag
        elif tag == "li":
            self._li.append((len(self.parts), self._text_chars))
            self.parts.append(f"\n{_BULLET}")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if self._skip is None and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self._skip is not None:
            if tag == self._skip:
                self._skip = None
            return
        if tag == "li":
            if self._li:
                idx, chars = self._li.pop()
                if chars == self._text_chars:  # empty item: drop its bullet
                    self.parts[idx] = ""
            self.parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in _CELL_TAGS:
            self.parts.append(" ")

    def handle_data(self, data):
        if self._skip is None:
            data = data.replace(_BULLET, "").replace("\n", " ")
            self._text_chars += len(data) - data.count(" ")
            self.parts.append(data)


def _tidy(text: str) -> str:
    lines = [_INLINE_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    bullet = False
    for ln in lines:
        if ln.startswith(_BULLET):
            content = ln[1:].strip()
            if not content:
                bullet = True
                continue
            out.append("- " + content)
            bullet = False
            continue
        if not ln:
            if bullet:
                continue
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(("- " + ln) if bullet else ln)
        bullet = False
    res = [
        ln
        for i, ln in enumerate(out)
        if not (ln == "" and 0 < i < len(out) - 1 and out[i - 1].startswith("- ") and out[i + 1].startswith("- "))
    ]
    return "\n".join(res).strip()


_TAG_NAME = re.compile(r"(/?)([A-Za-z][A-Za-z0-9]{0,15})")


def _sanitize_html(html: str) -> str:
    """Linear pre-pass so the stdlib parser cannot go quadratic on junk markup.

    Comments and declarations are dropped, tags are rewritten to bare names
    (attributes are never needed), and any "<" that does not open a tag closed
    by ">" before the next "<" is escaped to "&lt;".
    """
    out: list[str] = []
    n = len(html)
    pos = last = 0
    gt = -1  # cached index of the next ">" at or after the scan position (n + 1: none)
    while True:
        i = html.find("<", pos)
        if i == -1:
            break
        pos = i + 1
        if html.startswith("<!--", i):
            out.append(html[last:i])
            j = html.find("-->", i + 4)
            if j == -1:
                last = n
                break
            last = pos = j + 3
            continue
        m = _TAG_NAME.match(html, i + 1)
        bang = html[i + 1 : i + 2] in ("!", "?")
        if gt < i:
            gt = html.find(">", i)
            if gt == -1:
                gt = n + 1
        if (not m and not bang) or gt > n or html.find("<", i + 1, gt) != -1:
            out.append(html[last:i])
            out.append("&lt;")
            last = i + 1
            continue
        out.append(html[last:i])
        if m and not bang:
            selfclose = "/" if gt > 0 and html[gt - 1] == "/" and not m.group(1) else ""
            out.append(f"<{m.group(1)}{m.group(2)}{selfclose}>")
        last = pos = gt + 1
    out.append(html[last:])
    return "".join(out)


def html_to_text(html: str) -> str:
    """Plain text from HTML. Never raises; input is capped at 200,000 chars.

    Drops script/style/noscript/template, turns block tags into line breaks and
    list items into "- " bullets, decodes entities and collapses whitespace.
    Link targets are never emitted. Malformed markup degrades to a tag-strip.
    """
    if not html or not isinstance(html, str):
        return ""
    html = _sanitize_html(html[:_MAX_HTML])
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
# All patterns below run on whitespace-collapsed text (single spaces).
_MULTI_SPLIT = re.compile(r" ?; ?| ?\| ?| or | / ", re.I)
_REMOTE_PREFIX = re.compile(
    r"^(?:fully )?(?:remote|remoto)(?: ?[-–—:,] ?| ?\( ?| (?:in|within|from) | )(?P<r>.+?) ?\)?$",
    re.I,
)
_REMOTE_SUFFIX = re.compile(r"^(?P<r>.+?) ?[-–—,(]? ?\(? ?(?:remote|remoto|hybrid|on-?site|presencial) ?\)?$", re.I)


def _fold(text: str) -> str:
    d = unicodedata.normalize("NFKD", text)
    d = "".join(ch for ch in d if not unicodedata.combining(ch))
    return " ".join(d.casefold().replace(".", "").split())


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


def _resolve(parts: list[str]) -> tuple[str | None, str | None]:
    none = (None, None)
    city, last = parts[0], parts[-1]
    if len(parts) == 1:
        code = _country(city, short_ok=False)
        return (code, None) if code else none
    if len(parts) == 3:
        code = _country(last, short_ok=False)
        if not code or (code == "US" and parts[1].upper() not in _US_STATES):
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


def _parse_single(text: str) -> tuple[str | None, str | None]:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts or len(parts) > 3:
        return None, None
    named = [_country(p, short_ok=False) for p in parts]
    distinct = {c for c in named if c}
    if len(distinct) > 1:  # "USA, Canada", "Germany, Austria, Switzerland"
        return None, None
    code, city = _resolve(parts)
    if code is None or (distinct and distinct != {code}):
        return None, None
    if len(parts) > 1 and named[0]:  # a country in the city slot is never a city
        city = None
    return code, city


_REGION_TOKENS = {t for w in _REGION_WORDS for t in w.split()} | {"fully", "only", "on", "site", "onsite", "presencial", "office", "in"}
_MODEL_PREFIX = re.compile(r"^(?:hybrid|on-?site|presencial|h[ií]brid[oa]|vor ort|in-?office)(?: ?[-–—:,] ?| )(?:(?:in der|in|im|em|no|na|at) )?(?=.)", re.I)


def _is_region(part: str) -> bool:
    return all(t in _REGION_TOKENS for t in re.findall(r"[a-z]+", _fold(part)))


def _parse_part(part: str) -> tuple[str | None, str | None]:
    part = _MODEL_PREFIX.sub("", part, count=1)
    m = _REMOTE_PREFIX.match(part) or _REMOTE_SUFFIX.match(part)
    return _parse_single(m.group("r") if m else part)


def parse_location(text: str) -> tuple[str | None, str | None]:
    """Return (ISO alpha-2 country, city); (None, None) when not unambiguous.

    Conservative: more than one distinct country anywhere in the text (comma
    lists or ; | / or separators) gives (None, None); regions like EMEA and
    bare cities without a country are never guessed.
    """
    t = _ws(_cap(text, _MAX_LOCATION))
    if not t:
        return None, None
    parts = [p.strip() for p in _MULTI_SPLIT.split(t) if p.strip()]
    if not parts:
        return None, None
    results = [_parse_part(p) for p in parts]
    countries = {c for c, _ in results if c}
    if len(countries) != 1:
        return None, None
    country = next(iter(countries))
    if len(parts) == 1:
        return results[0]
    # several locations: any unresolved part that is not a pure region/remote
    # word ("Toronto, ON", a bare city...) may be another country -> unknown
    if not all(c or _is_region(p) for (c, _), p in zip(results, parts)):
        return None, None
    cities = {city for c, city in results if c}
    city = next(iter(cities)) if len(cities) == 1 and None not in cities else None
    return country, city


# --------------------------------------------------------------------------
# detect_work_model
# --------------------------------------------------------------------------

_KW = {
    "remote": r"remote(?:ly)?|remot[oa]|home[- ]?office|teletrabalho|work from home",
    "hybrid": r"hybrid|h[ií]brid[oa]|hybride",
    "onsite": r"on[- ]?site|presencial|vor ort",
}
_LOCATION_SIGNALS = {k: re.compile(rf"\b(?:{v})\b", re.I) for k, v in _KW.items()}
# In a title the keyword counts only when bracketed, after a separator, or as
# the last word: "(Remote)", "- Hybrid", "Engineer Remote"; never "Hybrid Cloud
# Engineer" or "Remote Sensing Scientist".
_TITLE_SIGNALS = {
    k: re.compile(rf"(?:^|[-–—|,:/(\[]\s{{0,3}}|\s)(?:{v})\b(?=\s{{0,3}}(?:[-–—|,:/)\]]|$))", re.I)
    for k, v in _KW.items()
}
_ROLE = r"(?:role|position|job|opportunity)"
_FULLY = r"(?:fully|completely|entirely)"
_EXPLICIT = {
    "remote": re.compile(
        rf"\b(?:this|the|our)\s+{_ROLE}\s+is\s+(?:an?\s+)?(?:{_FULLY}\s+|100\s?%\s+)?remote\b(?![- ]?(?:friendly|optional|eligible))"
        rf"|\b(?:{_FULLY}|100\s?%)\s+remote\s+{_ROLE}\b"
        rf"|\bthis\s+is\s+an?\s+remote\s+{_ROLE}\b"
        r"|\bremote[- ]first\s+(?:role|position|job)\b"
        r"|\b(?:vaga|posi[cç][aã]o|oportunidade|cargo)\s+(?:[eé]\s+)?(?:(?:100\s?%|totalmente)\s+)?remot[ao]\b"
        r"|\b(?:stelle|position|rolle)\s+ist\s+(?:(?:komplett|vollst[aä]ndig|100\s?%)\s+)?remote\b"
        r"|\b(?:komplett|vollst[aä]ndig|100\s?%)\s+remote\s+(?:stelle|position|rolle)\b",
        re.I,
    ),
    "hybrid": re.compile(
        r"\bhybrid\s+(?:role|position|job|work\s+model|work\s+schedule|setup|model)\b"
        r"|\b(?:this|the|our)\s+(?:role|position|job)\s+is\s+(?:an?\s+)?hybrid\b"
        r"|\bthis\s+is\s+an?\s+hybrid\s+(?:role|position|job)\b"
        r"|\b(?:vaga|posi[cç][aã]o|modelo|regime|trabalho)\s+h[ií]brid[oa]\b"
        r"|\bhybrid(?:es)?\s+(?:arbeit\w*|modell)\b",
        re.I,
    ),
    "onsite": re.compile(
        r"\bon-?site\s+(?:required|role|position)\b"
        r"|\b(?:this|the|our)\s+(?:role|position|job)\s+is\s+(?:an?\s+)?on-?site\b"
        r"|\bthis\s+is\s+an?\s+on-?site\s+(?:role|position|job)\b"
        r"|\b(?:vaga|trabalho|modelo|regime)\s+presencial\b"
        r"|\b(?:100\s?%|totalmente)\s+presencial\b"
        r"|\b(?:arbeit\w*|stelle|t[aä]tigkeit)\s+vor ort\b",
        re.I,
    ),
}
_NEGATION = re.compile(r"(?:\bnot|n't|\bnicht|\bn[aã]o|\bnever)\s+(?:a\s+|an\s+)?$", re.I)


_CONDITIONAL = re.compile(r"\b(?:if|whether|unless|when|should|in case|ob|falls|se|caso|quando)\b", re.I)
_ALTERNATIVE = re.compile(r"^\W{0,3}(?:or|oder|ou)\s+(?:fully\s+)?(?:remote|hybrid|on-?site|onsite|office|h[ií]brid\w*|presencial|vor ort)", re.I)


def _is_statement(text: str, m: re.Match) -> bool:
    """False for negated, conditional ("if the role is remote") or alternative ("remote or hybrid") phrasing."""
    if _NEGATION.search(text[max(0, m.start() - 12) : m.start()]):
        return False
    pre = text[max(0, m.start() - 200) : m.start()]
    pre = re.split(r"[.!?\n;:]", pre)[-1]
    return not _CONDITIONAL.search(pre) and not _ALTERNATIVE.match(text[m.end() : m.end() + 30])


def _signals(patterns: dict[str, re.Pattern], text: str, check_negation: bool = False) -> set[str]:
    found = set()
    for kind, pat in patterns.items():
        for m in pat.finditer(text):
            if check_negation and not _is_statement(text, m):
                continue
            found.add(kind)
            break
    return found


def detect_work_model(location_text: str, title: str, description: str) -> str:
    """remote | hybrid | onsite | unknown.

    Title and location decide first; both remote and onsite/hybrid signals
    there give "unknown". The description counts only with an explicit
    sentence about this role ("this role is fully remote", "hybrid role");
    culture/benefit mentions ("remote-friendly", "fully remote teams") never do.
    """
    strong = _signals(_TITLE_SIGNALS, _ws(_cap(title, _MAX_TITLE)))
    strong |= _signals(_LOCATION_SIGNALS, _ws(_cap(location_text, _MAX_LOCATION)))
    if len(strong) == 1:
        return next(iter(strong))
    if len(strong) > 1:
        return "unknown"
    explicit = _signals(_EXPLICIT, _cap(description, _MAX_SCAN), True)
    return next(iter(explicit)) if len(explicit) == 1 else "unknown"


# --------------------------------------------------------------------------
# detect_seniority
# --------------------------------------------------------------------------

_LEAD_ROLE = r"(?:engineer|developer|designer|architect|scientist|researcher|programmer|analyst|software|devops|sre)"
_NOT_SENIORITY = re.compile(
    r"\bchief of staff\b|\b(?:assistant|assistente)\s+(?:to|of|do|da|de)\b|(?:director|vp|manager)(?:'s|’s)\s+(?:assistant|assistente)"
    r"|\bexecutive assistant\b"
)
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
    """intern | junior | mid | senior | lead | manager | director | unknown.

    Title only, word-boundary matches only; higher-level words win
    ("Senior Manager" is manager). Assistants to executives and "Chief of
    Staff" are unknown. Anything unsure is "unknown".
    """
    t = _ws(_cap(title, _MAX_TITLE)).casefold().rstrip(" .,;:()[]|/-")
    if _NOT_SENIORITY.search(t):
        return "unknown"
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
    """pt | en | de | unknown, from stopword counts in the first 1500 chars.

    Needs a clear margin over the runner-up and a minimum number of hits
    (stricter for short texts); otherwise "unknown". Based on the text, not
    the country.
    """
    sample = _cap(text, 1500)
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


# Strong salary words only: period words alone ("annual", "per year") do not count.
_PAY_RE = re.compile(
    r"\b(?:salary|salaries|compensation|pay\s+range|pay\s+band|pay\s+rate|base\s+pay|wages?|"
    r"remuneration|ote|on-target\s+earnings|gehalt|jahresgehalt|verg[uü]tung|"
    r"sal[aá]rio|remunera[cç][aã]o|salaire|sueldo)\b",
    re.I,
)


def has_pay_context(text: str) -> bool:
    """True when the text mentions pay with a strong word (salary, compensation,
    pay range, base pay, OTE, Gehalt, salário, remuneração, Vergütung, wage).
    Bare period words ("annual", "per year", "hourly") do not count."""
    return isinstance(text, str) and _PAY_RE.search(text[:200_000]) is not None


_SYMBOLS = {"US$": "USD", "R$": "BRL", "A$": "AUD", "C$": "CAD", "NZ$": "NZD", "S$": "SGD", "$": "USD", "€": "EUR", "£": "GBP", "ZŁ": "PLN"}
_CCY = "USD|EUR|GBP|BRL|AUD|CAD|CHF|NZD|SGD|SEK|NOK|DKK|PLN|CZK|JPY|INR|MXN|ARS|CLP|COP"
_CUR = (
    r"(?:(?<![A-Za-z])(?:USD|CAD|AUD|NZD|SGD|MXN|CLP|ARS|COP)\$"
    r"|(?<![A-Za-z])(?:US\$|R\$|A\$|C\$|NZ\$|S\$|\$)|€|£"
    rf"|(?<![A-Za-z])(?:{_CCY})(?![A-Za-z]))"
)
_SUF = rf"(?:€|£|zł|(?<![A-Za-z])(?:{_CCY}|euros?|reais|dollars?)(?![A-Za-z]))"
_NUM = r"(?:\d{1,3}(?:[.,]\d{3}){1,3}|\d{1,3}(?:[ '\u00a0\u202f]\d{3}){1,2}|\d{1,9})(?:[.,]\d{1,2})?(?![.,]?\d)(?![ \u00a0']\d)"
_K = r"[kK](?![A-Za-z0-9])"
_SEP = r"(?:\s{0,3}[-–—]\s{0,3}|\s(?:to|bis|a|at[eé])\s)"
_SALARY_RE = re.compile(
    rf"(?P<c1>{_CUR})\s?(?P<a1>{_NUM})(?P<k1>{_K})?(?:\s?/\s?[A-Za-z]{{1,5}}(?=\s{{0,3}}[-–—]))?"
    rf"(?:{_SEP}(?P<c2>{_CUR})?\s?(?P<a2>{_NUM})(?P<k2>{_K})?)?"
    r"|"
    rf"(?<![\d.,])(?P<b1>{_NUM})(?P<bk1>{_K})?(?:\s?(?P<c3a>{_SUF}))?"
    rf"(?:{_SEP}(?P<b2>{_NUM})(?P<bk2>{_K})?)?\s?(?P<c3>{_SUF})",
    re.I,
)
_CUR_WORDS = {"euro": "EUR", "euros": "EUR", "reais": "BRL", "dollar": "USD", "dollars": "USD", "zł": "PLN"}
_UNIT_AFTER = re.compile(r"\s?(?:billion|million|trillion|bn|mm|[bm])\b", re.I)
_EQUITY = r"equity|stocks?|bonus(?:es)?|rsus?|funding|raised|valuation|arr|revenue|options|investment"
_PERK = (
    r"stipend|budget|allowance|reimbursement|credit|gift|perk|relocation|sign-?on|signing|wellness|"
    r"learning|education|home office|conference|equipment|funds?|tuition|voucher|discount"
)
_NONPAY_BEFORE = re.compile(rf"\b(?:{_EQUITY}|{_PERK})s?\b", re.I)
# After the amount only an adjacent word counts, so "$120k + equity" or
# "$120k + $1,500 learning budget" do not kill the real salary.
_NONPAY_AFTER = re.compile(rf"^[ \t]{{0,3}}(?P<gap>(?:\w+[ \t]+){{0,3}}?)(?:{_EQUITY}|{_PERK})s?\b", re.I)
_CONJ = re.compile(r"\b(?:plus|with|and|incl|including|inkl|mit|und|mais|com)\b", re.I)
_UPTO = re.compile(r"(?:up\s+to|bis\s+zu|at[eé]|maximum|max\.?|no\s+more\s+than|capped\s+at)\s*$", re.I)
_CODE_BEFORE = re.compile(rf"(?<![A-Za-z])({_CCY})\s?$")
_CODE_AFTER = re.compile(rf"^\s?\(?({_CCY})(?![A-Za-z])")
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
_ABORT = object()
_MAX_AMOUNT = Decimal("1e12")  # Numeric(14,2) holds < 10**12


def _parse_amount(s: str, k: bool) -> Decimal | None:
    s = re.sub(r"['   ]", "", s)  # space/apostrophe thousands (60 000, 100'000)
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


def _cur_code(sym: str) -> str:
    u = sym.upper()
    if u in _SYMBOLS:
        return _SYMBOLS[u]
    if sym.lower() in _CUR_WORDS:
        return _CUR_WORDS[sym.lower()]
    return u[:3] if len(u) == 4 and u.endswith("$") else u


def _clause_before(text: str, pos: int, width: int) -> str:
    before = text[max(0, pos - width) : pos]
    cut = max(before.rfind("\n"), before.rfind(";"), before.rfind(". "), before.rfind(","), before.rfind(" and "))
    return before[cut + 1 :]


def _candidate(text: str, m: re.Match):
    """SalaryInfo for one regex match, None to skip it, _ABORT if it is invalid."""
    s, e = m.start(), m.end()
    if m.group("a1") is not None:
        sym, a1, k1, a2, k2, c2 = m.group("c1"), m.group("a1"), m.group("k1"), m.group("a2"), m.group("k2"), m.group("c2")
        code = _cur_code(sym)
        if c2 and _cur_code(c2) != code and not (c2 == "$" and sym.endswith("$")):
            return None
    else:
        a1, k1, a2, k2 = m.group("b1"), m.group("bk1"), m.group("b2"), m.group("bk2")
        c3, c3a = m.group("c3"), m.group("c3a")
        if c3a and _cur_code(c3a) != _cur_code(c3):
            return None
        code = _cur_code(c3)
    after = text[e : e + 60]
    before = text[max(0, s - 80) : s]
    if _UNIT_AFTER.match(after) or _UPTO.search(before[-20:]):
        return None
    if _NONPAY_BEFORE.search(_clause_before(text, s, 40)):
        return None
    na = _NONPAY_AFTER.match(after)
    explicit = _CODE_BEFORE.search(before[-5:]) or _CODE_AFTER.match(after)
    if explicit:
        code = explicit.group(1).upper()
    lo = _parse_amount(a1, bool(k1))
    hi = _parse_amount(a2, bool(k2)) if a2 else None
    if lo is None or (a2 and hi is None):
        return None
    if hi is not None:
        if k2 and not k1 and lo < 1000:
            lo *= 1000
        elif k1 and not k2 and hi < 1000:
            hi *= 1000
    if na and not _PAY_RE.search(na.group("gap")):
        # A bonus/equity/perk word right after the amount disqualifies it, unless
        # the word sits in a trailing "plus/with/and ..." clause after a RANGE
        # that is itself clearly pay (currency code, period word or pay word).
        conj = _CONJ.search(na.group("gap")) is not None
        clearly_pay = (
            explicit is not None
            or any(pat.match(after) for _, pat in _PERIODS)
            or _PAY_RE.search(before[-60:].rsplit(". ", 1)[-1]) is not None
        )
        if not (conj and hi is not None and clearly_pay):
            return None
    if hi is None:  # a single amount needs a strong salary word in its own sentence
        pre = before.rsplit(". ", 1)[-1]
        post = after[:40].split(". ", 1)[0]
        if not _PAY_RE.search(pre + " " + post):
            return None
    if lo <= 0 or (hi is not None and (hi <= 0 or lo > hi)):
        return _ABORT
    # Job.salary_min/max are Numeric(14,2): an amount that cannot be stored is
    # nonsense anyway, so the salary is unknown (saving it would fail the job)
    if lo >= _MAX_AMOUNT or (hi is not None and hi >= _MAX_AMOUNT):
        return _ABORT
    period = None
    for name, pat in _PERIODS:
        if pat.match(after):
            period = name
            break
    if period is None:
        for name, pat in _PERIOD_BEFORE:
            if pat.search(before[-30:]):
                period = name
                break
    return SalaryInfo(min=lo, max=hi, currency=code, period=period)


def extract_salary(text: str) -> SalaryInfo | None:
    """First explicit salary in the first 60 KB, or None when unsure.

    Needs an explicit currency and either a range or a strong salary word in
    the same sentence. Ignores equity/bonus/funding amounts, perks (stipend,
    budget...), "up to X" and unit suffixes (B/M). The first accepted range
    wins, unless a later range in a different currency exists (then None).
    """
    text = _cap(text, _MAX_PAY_TEXT)
    first = None
    for i, m in enumerate(_SALARY_RE.finditer(text)):
        if i >= _MAX_CANDIDATES:
            break
        cand = _candidate(text, m)
        if cand is _ABORT:
            return None
        if cand is None:
            continue
        if first is None:
            first = cand
        elif cand.max is not None and first.max is not None and cand.currency != first.currency:
            return None
    return first


def find_salary_spans(text: str) -> list[SalaryInfo]:
    """Every salary-shaped span (currency-adjacent amount or range) in the first
    60 KB that passes the same non-pay exclusions as extract_salary (bonus,
    equity, stipend, funding, "up to X", unit suffixes...). Unlike
    extract_salary it returns ALL of them and skips invalid ones instead of
    giving up. Used to check that an LLM-claimed amount is really a salary."""
    text = _cap(text, _MAX_PAY_TEXT)
    spans: list[SalaryInfo] = []
    for i, m in enumerate(_SALARY_RE.finditer(text)):
        if i >= _MAX_CANDIDATES:
            break
        cand = _candidate(text, m)
        if isinstance(cand, SalaryInfo):
            spans.append(cand)
    return spans


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
    # length-prefixed so field boundaries cannot collide
    return hashlib.sha256("".join(f"{len(p)}:{p}" for p in parts).encode("utf-8", "replace")).hexdigest()


def _fp_norm(text: str) -> str:
    """NFKC, casefold, punctuation -> space (keeping + and #), collapsed whitespace."""
    t = unicodedata.normalize("NFKC", text or "").casefold()
    return " ".join(re.sub(r"[^\w+#]+|_+", " ", t).split())


def normalize_job(raw: RawJob) -> NormalizedJob:
    title = _cap(raw.title, _MAX_TITLE)
    location = _cap(raw.location_text, _MAX_LOCATION)
    department = _cap(raw.department, _MAX_DEPARTMENT) if raw.department else None
    description = html_to_text(raw.description_html)
    country, city = parse_location(location)
    salary = extract_salary(description)
    return NormalizedJob(
        description=description,
        country=country,
        city=city,
        work_model=detect_work_model(location, title, description),
        seniority=detect_seniority(title),
        language=detect_language(f"{title}\n{description}"),
        department=department,
        salary_min=salary.min if salary else None,
        salary_max=salary.max if salary else None,
        salary_currency=salary.currency if salary else None,
        salary_period=salary.period if salary else None,
        salary_known=salary is not None,
        # sha256 of (title, location_text, description text, department), capped fields
        content_hash=_sha(title, location, description, department or ""),
        # sha256 of (normalised title, normalised location_text); id-independent
        fingerprint=_sha(_fp_norm(title), _fp_norm(location)),
    )
