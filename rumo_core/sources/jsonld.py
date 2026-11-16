"""schema.org `JobPosting` read from `<script type="application/ld+json">` blocks.

Only data is read: the JSON is parsed, never executed, and every text field
stays text (the normalizer sanitizes HTML descriptions). Blocks that are
invalid, too big or too many are skipped; one bad posting never blocks the rest.
"""

import hashlib
import json
import math
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urljoin

from rumo_core.sources.base import RawJob, SalaryHint
from rumo_core.sources.http import UnsafeURL, canonical_url

MAX_BLOCKS = 50
MAX_BLOCK_CHARS = 1_000_000
_MAX_ID = 255
_PERIODS = {"YEAR": "year", "MONTH": "month", "HOUR": "hour"}


class _LdCollector(HTMLParser):
    """Collects only the text of ld+json script tags."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self._buf: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        kind = {(k or "").lower(): (v or "") for k, v in attrs}.get("type", "")
        if kind.split(";", 1)[0].strip().lower() == "application/ld+json":
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "script" and self._buf is not None:
            self.blocks.append("".join(self._buf))
            self._buf = None

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)

    def close(self):
        super().close()
        if self._buf is not None:
            self.blocks.append("".join(self._buf))
            self._buf = None


def _objects(data):
    """Every dict inside lists and @graph wrappers."""
    if isinstance(data, list):
        for item in data:
            yield from _objects(item)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _objects(data["@graph"])


def _is_posting(obj: dict) -> bool:
    kind = obj.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and k.rsplit("/", 1)[-1] == "JobPosting" for k in kinds)


def _text(value, limit: int) -> str | None:
    if isinstance(value, dict):
        value = value.get("name")
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] or None


def _decimal(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return number


def _url(posting: dict, base_url: str | None) -> str | None:
    raw = posting.get("url")
    if isinstance(raw, str) and raw.strip():
        candidate = urljoin(base_url, raw.strip()) if base_url else raw.strip()
        try:
            return canonical_url(candidate)
        except UnsafeURL:
            pass
    if base_url:
        try:
            return canonical_url(base_url)
        except UnsafeURL:
            return None
    return None


def _external_id(posting: dict, url: str) -> str:
    ident = posting.get("identifier")
    if isinstance(ident, dict):
        ident = ident.get("value")
    value = None
    if isinstance(ident, (str, int)) and not isinstance(ident, bool):
        value = str(ident).strip() or None
    value = value or url
    if len(value) > _MAX_ID:
        value = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return value


def _places(posting: dict) -> tuple[str, set[str]]:
    locations = posting.get("jobLocation")
    if not isinstance(locations, list):
        locations = [locations] if locations is not None else []
    texts: list[str] = []
    codes: set[str] = set()
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        address = loc.get("address")
        if isinstance(address, str):
            text = address.strip()
            if text:
                texts.append(text[:255])
            continue
        if not isinstance(address, dict):
            continue
        parts = [
            _text(address.get("addressLocality"), 120),
            _text(address.get("addressRegion"), 120),
            _text(address.get("addressCountry"), 120),
        ]
        country = address.get("addressCountry")
        if isinstance(country, str) and len(country.strip()) == 2 and country.strip().isalpha():
            codes.add(country.strip().upper())
        joined = ", ".join(p for p in parts if p)
        if joined:
            texts.append(joined[:255])
    return "; ".join(texts), codes


def _remote_text(posting: dict) -> str:
    required = posting.get("applicantLocationRequirements")
    if not isinstance(required, list):
        required = [required] if required is not None else []
    names = [n for n in (_text(r, 120) for r in required) if n]
    return f"Remote - {', '.join(names)}" if names else "Remote"


def _salary(base) -> SalaryHint | None:
    if not isinstance(base, dict):
        return None
    value = base.get("value")
    unit = base.get("unitText")
    currency = base.get("currency")
    if isinstance(value, dict):
        unit = unit or value.get("unitText")
        currency = currency or value.get("currency")
        low, high, single = value.get("minValue"), value.get("maxValue"), value.get("value")
    else:
        low = high = single = value
    period = _PERIODS.get(unit.strip().upper()) if isinstance(unit, str) else None
    if period is None or not isinstance(currency, str) or len(currency.strip()) != 3:
        return None
    lo = _decimal(low) if low is not None else _decimal(single)
    hi = _decimal(high) if high is not None else _decimal(single)
    if lo is None and hi is None:
        return None
    return SalaryHint(min=lo, max=hi, currency=currency.strip().upper(), period=period)


def _posted_at(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _to_raw(posting: dict, base_url: str | None) -> RawJob | None:
    title = _text(posting.get("title"), 500)
    url = _url(posting, base_url)
    if title is None or url is None:
        return None
    text, codes = _places(posting)
    work_model = None
    if posting.get("jobLocationType") == "TELECOMMUTE":
        work_model = "remote"
        if not text:
            text = _remote_text(posting)
    description = posting.get("description")
    return RawJob(
        external_id=_external_id(posting, url),
        title=title,
        url=url,
        location_text=text,
        description_html=description if isinstance(description, str) else "",
        department=None,
        posted_at=_posted_at(posting.get("datePosted")),
        raw=posting,
        work_model_hint=work_model,
        country_hint=next(iter(codes)) if len(codes) == 1 else None,
        salary_hint=_salary(posting.get("baseSalary")),
    )


def extract_job_postings(html: str, *, base_url: str | None = None) -> list[RawJob]:
    """Every valid JobPosting in the page's ld+json blocks, in document order."""
    collector = _LdCollector()
    collector.feed(html or "")
    collector.close()
    out: list[RawJob] = []
    for block in collector.blocks[:MAX_BLOCKS]:
        if len(block) > MAX_BLOCK_CHARS:
            continue
        try:
            data = json.loads(block, strict=False)
        except ValueError:
            continue
        for obj in _objects(data):
            if not _is_posting(obj):
                continue
            raw = _to_raw(obj, base_url)
            if raw is not None:
                out.append(raw)
    return out


def employer_name(raw: RawJob) -> str | None:
    """Hiring organization name of a JobPosting read by `extract_job_postings`."""
    if not isinstance(raw.raw, dict):
        return None
    return _text(raw.raw.get("hiringOrganization"), 255)


__all__ = ["extract_job_postings", "employer_name", "MAX_BLOCKS", "MAX_BLOCK_CHARS", "math"]
