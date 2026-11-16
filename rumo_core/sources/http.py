"""Safe outbound HTTP for job sources.

Every new third-party request goes through `SafeClient`:
- only http/https on ports 80/443, no userinfo, canonical host (`canonical_url`);
- the host is resolved once per hop and EVERY address must be public; the
  request then connects to the checked IP (no second resolution, so a DNS
  rebinding answer cannot redirect it), with the original name in `Host` and
  as the TLS SNI name;
- redirects are followed by hand: same registrable domain only, at most
  `MAX_REDIRECTS` hops, each hop checked again;
- body caps (declared and streamed), content-type whitelist, total timeout.
"""

import asyncio
import ipaddress
import re
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx

from rumo_core.sources.base import SourceBadResponse, SourceUnavailable

ALLOWED_PORTS = frozenset({80, 443})
MAX_REDIRECTS = 3
API_MAX_BYTES = 20_000_000
CONTENT_TYPES = frozenset({
    "text/html",
    "application/xhtml+xml",
    "application/json",
    "application/ld+json",
    "application/xml",
    "text/xml",
    "application/rss+xml",
    "application/atom+xml",
    "text/plain",
})
_OK_STATUSES = frozenset({404, 410})
_DEFAULT_PORT = {"http": 80, "https": 443}
_MULTI_SUFFIXES = frozenset({
    "com.br", "net.br", "org.br", "gov.br", "edu.br", "co.uk", "org.uk", "ac.uk",
    "com.au", "net.au", "co.jp", "com.mx", "com.ar", "co.in", "co.nz", "com.co",
    "com.pt", "co.za", "com.sg",
})
_PRIVATE_SUFFIXES = frozenset({"github.io", "herokuapp.com", "vercel.app", "netlify.app", "pages.dev"})
_NUMERIC_LABEL = re.compile(r"(0x[0-9a-f]+|[0-9]+)")


def _user_agent() -> str:
    try:
        from importlib.metadata import version

        v = version("rumo")
    except Exception:  # package metadata missing (source checkout)
        v = "0.1.0"
    return f"RumoJobSearch/{v} (uso pessoal)"


USER_AGENT = _user_agent()


class UnsafeURL(SourceBadResponse):
    """The address is not one we may fetch (scheme, port, private IP, redirect...)."""


def canonical_url(text: str) -> str:
    """Trim, require http(s), reject userinfo/odd ports/numeric hosts; drop the fragment.

    Does not resolve DNS: the address check happens in `SafeClient` before connecting.
    """
    value = (text or "").strip()
    if not value:
        raise UnsafeURL("endereço vazio")
    try:
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        netloc = parts.netloc
        port = parts.port
    except ValueError as exc:
        raise UnsafeURL("endereço inválido") from exc
    if scheme not in _DEFAULT_PORT:
        raise UnsafeURL("esquema não permitido")
    if "@" in netloc:
        raise UnsafeURL("credenciais na URL não são permitidas")
    if port is not None and port not in ALLOWED_PORTS:
        raise UnsafeURL("porta não permitida")
    host = (parts.hostname or "").strip().rstrip(".")
    if not host:
        raise UnsafeURL("host vazio")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise UnsafeURL("host inválido") from exc
    if not _is_ip_literal(host):
        last = host.rsplit(".", 1)[-1]
        if _NUMERIC_LABEL.fullmatch(last):
            # 2130706433, 0x7f.1 and friends: resolvers read them as IPv4
            raise UnsafeURL("host numérico não permitido")
    if port is not None and port == _DEFAULT_PORT[scheme]:
        port = None
    hostpart = f"[{host}]" if ":" in host else host
    if port is not None:
        hostpart = f"{hostpart}:{port}"
    query = f"?{parts.query}" if parts.query else ""
    return f"{scheme}://{hostpart}{parts.path}{query}"


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.strip("[]"))
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return bool(addr.is_global and not addr.is_multicast)


def registrable_domain(host: str) -> str:
    labels = host.lower().strip(".").split(".")
    for suffix in _PRIVATE_SUFFIXES:
        n = suffix.count(".") + 1
        if len(labels) > n and ".".join(labels[-n:]) == suffix:
            return ".".join(labels[-(n + 1):])
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


async def default_resolve(host: str, port: int) -> list[str]:
    def _lookup() -> list[str]:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        seen: list[str] = []
        for info in infos:
            ip = info[4][0]
            if ip not in seen:
                seen.append(ip)
        return seen

    return await asyncio.to_thread(_lookup)


@dataclass(frozen=True)
class SafeResponse:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes
    content_type: str

    def json(self):
        import json

        try:
            return json.loads(self.body)
        except ValueError as exc:
            raise SourceBadResponse("resposta não é JSON válido") from exc

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class SafeClient:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        resolve=default_resolve,
        clock=time.monotonic,
        sleep=asyncio.sleep,
        today=date.today,
        page_max_bytes: int = 5_000_000,
        timeout: float = 20.0,
        robots=None,
        limits=None,
    ) -> None:
        self._client = httpx.AsyncClient(
            transport=transport, follow_redirects=False, timeout=timeout
        )
        self._resolve = resolve
        self._clock = clock
        self._sleep = sleep
        self._today = today
        self._page_max_bytes = page_max_bytes
        self._timeout = timeout
        self._robots = robots
        self._limits = limits

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        kind: Literal["api", "page"] = "page",
        max_bytes: int | None = None,
        headers: Mapping[str, str] | None = None,
        accept: str | None = None,
    ) -> SafeResponse:
        limit = max_bytes or (self._page_max_bytes if kind == "page" else API_MAX_BYTES)
        try:
            return await asyncio.wait_for(
                self._get_checked(url, limit, headers, accept), self._timeout
            )
        except TimeoutError as exc:
            raise SourceUnavailable("tempo esgotado ao buscar a página") from exc

    async def _pin(self, host: str, port: int) -> str:
        """Resolve once and return the checked IP to connect to."""
        if _is_ip_literal(host):
            ips = [host.strip("[]")]
        else:
            try:
                ips = await self._resolve(host, port)
            except OSError as exc:
                raise SourceUnavailable("não foi possível resolver o host") from exc
        if not ips or not all(is_public_ip(ip) for ip in ips):
            raise UnsafeURL("endereço não público não é permitido")
        return ips[0]

    async def _get_checked(
        self, url: str, limit: int, headers: Mapping[str, str] | None, accept: str | None
    ) -> SafeResponse:
        current = canonical_url(url)
        origin_domain = registrable_domain(urlsplit(current).hostname or "")
        hops = 0
        while True:
            parts = urlsplit(current)
            scheme = parts.scheme
            host = parts.hostname or ""
            port = parts.port or _DEFAULT_PORT[scheme]
            ip = await self._pin(host, port)
            ip_part = f"[{ip}]" if ":" in ip else ip
            query = f"?{parts.query}" if parts.query else ""
            target = f"{scheme}://{ip_part}:{port}{parts.path or '/'}{query}"
            req_headers = {"User-Agent": USER_AGENT, "Host": host}
            if accept:
                req_headers["Accept"] = accept
            if headers:
                req_headers.update(headers)
            req_headers["Host"] = host  # the pinned IP must never override the name
            try:
                async with self._client.stream(
                    "GET", target, headers=req_headers,
                    extensions={"sni_hostname": host},
                ) as resp:
                    status = resp.status_code
                    if status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise SourceBadResponse("redirecionamento sem destino")
                        if hops >= MAX_REDIRECTS:
                            raise UnsafeURL("redirecionamentos demais")
                        hops += 1
                        nxt = canonical_url(urljoin(current, location))
                        if registrable_domain(urlsplit(nxt).hostname or "") != origin_domain:
                            raise UnsafeURL("redirecionamento para outro domínio")
                        current = nxt
                        continue
                    if status >= 500:
                        raise SourceUnavailable(f"o site respondeu HTTP {status}")
                    if status not in _OK_STATUSES and not (200 <= status < 300):
                        raise SourceBadResponse(f"o site respondeu HTTP {status}")
                    body = await self._read_capped(resp, limit)
                    ctype = resp.headers.get("content-type", "")
                    mime = ctype.split(";", 1)[0].strip().lower()
                    if 200 <= status < 300 and mime not in CONTENT_TYPES:
                        raise SourceBadResponse("tipo de conteúdo não aceito")
                    return SafeResponse(
                        url=current, status=status, headers=dict(resp.headers),
                        body=body, content_type=mime,
                    )
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"falha de rede: {type(exc).__name__}") from exc

    @staticmethod
    async def _read_capped(resp: httpx.Response, limit: int) -> bytes:
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise SourceBadResponse("resposta maior que o limite")
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise SourceBadResponse("resposta maior que o limite")
            chunks.append(chunk)
        return b"".join(chunks)
