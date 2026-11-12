import asyncio
import html
import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx

from rumo_core.sources.base import (
    RawJob,
    SourceBadResponse,
    SourceNotFound,
    SourceUnavailable,
)

API_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
USER_AGENT = "Rumo/0.1 job-search-tool"
MAX_RETRIES = 3
MAX_RETRY_AFTER_SECONDS = 60.0
MAX_TOKEN_LEN = 100
_TOKEN = re.compile(r"[a-z0-9_-]+")


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After")
    if raw is not None:
        try:
            value = float(raw.strip())
        except ValueError:
            value = None
        if value is not None and math.isfinite(value) and value >= 0:
            return min(value, MAX_RETRY_AFTER_SECONDS)
    return float(2**attempt)


def _parse_dt(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_raw_job(item) -> RawJob | None:
    if not isinstance(item, dict):
        return None
    job_id = item.get("id")
    title = item.get("title")
    if job_id is None or isinstance(job_id, bool) or not isinstance(title, str) or not title.strip():
        return None
    location = item.get("location")
    location_text = location.get("name") if isinstance(location, dict) else None
    departments = item.get("departments")
    department = None
    if isinstance(departments, list) and departments and isinstance(departments[0], dict):
        name = departments[0].get("name")
        department = name if isinstance(name, str) and name else None
    content = item.get("content")
    url = item.get("absolute_url")
    posted = _parse_dt(item.get("first_published")) or _parse_dt(item.get("updated_at"))
    return RawJob(
        external_id=str(job_id),
        title=title,
        url=url if isinstance(url, str) else "",
        location_text=location_text if isinstance(location_text, str) else "",
        description_html=html.unescape(content) if isinstance(content, str) else "",
        department=department,
        posted_at=posted,
        raw=item,
    )


class GreenhouseSource:
    name = "greenhouse"

    def __init__(self, client: httpx.AsyncClient, *, max_bytes: int, sleep=asyncio.sleep):
        self._client = client
        self._max_bytes = max_bytes
        self._sleep = sleep

    def normalize_identifier(self, text: str) -> str:
        value = (text or "").strip()
        if not value:
            raise ValueError("empty board identifier")
        if re.search(r"\s", value):
            raise ValueError("board identifier must not contain spaces")
        if "." in value or "/" in value or "?" in value:
            parsed = urlparse(value if "://" in value else f"//{value}")
            host = (parsed.hostname or "").lower()
            if not (host == "greenhouse.io" or host.endswith(".greenhouse.io")):
                raise ValueError("not a Greenhouse board URL")
            parts = [p for p in parsed.path.split("/") if p]
            if parts and parts[0].lower() == "embed":
                values = parse_qs(parsed.query).get("for")
                token = values[0] if values else ""
            else:
                token = parts[0] if parts else ""
        else:
            token = value
        token = token.strip().lower()
        if not token or len(token) > MAX_TOKEN_LEN or not _TOKEN.fullmatch(token):
            raise ValueError("invalid Greenhouse board token")
        return token

    async def _read_capped(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > self._max_bytes:
            raise SourceBadResponse("Greenhouse response exceeds size cap")
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self._max_bytes:
                raise SourceBadResponse("Greenhouse response exceeds size cap")
            chunks.append(chunk)
        return b"".join(chunks)

    async def fetch_jobs(self, identifier: str) -> list[RawJob]:
        token = self.normalize_identifier(identifier)
        url = API_URL.format(token=token)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with self._client.stream("GET", url, headers=headers) as resp:
                    status = resp.status_code
                    if status == 429 or status >= 500:
                        delay = _retry_delay(resp, attempt) if status == 429 else float(2**attempt)
                        body = None
                    elif status == 404:
                        raise SourceNotFound(f"Greenhouse board {token!r} not found")
                    elif status != 200:
                        raise SourceBadResponse(f"Greenhouse returned HTTP {status}")
                    else:
                        body = await self._read_capped(resp)
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"Greenhouse request failed: {type(exc).__name__}") from exc
            if body is None:
                if attempt < MAX_RETRIES:
                    await self._sleep(delay)
                    continue
                raise SourceUnavailable(f"Greenhouse unavailable (HTTP {status}) after retries")
            return self._parse(body)
        raise SourceUnavailable("Greenhouse unavailable after retries")  # pragma: no cover

    @staticmethod
    def _parse(body: bytes) -> list[RawJob]:
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise SourceBadResponse("Greenhouse returned invalid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceBadResponse("Unexpected Greenhouse response shape")
        jobs = (_to_raw_job(item) for item in data["jobs"])
        return [j for j in jobs if j is not None]
