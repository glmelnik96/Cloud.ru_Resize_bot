"""SSRF-guarded page fetcher — turns a marketer-supplied URL into plain text.

The marketer may paste a link to a product page or article so the pipeline can
read what is actually being sold. That URL is untrusted user input pointed at
an outbound HTTP client running on our VM, i.e. a textbook SSRF sink, so every
hop is validated before a connection is made:

  - scheme must be http/https (no file:, gopher:, data:, ...)
  - the hostname is resolved and EVERY resolved address must be public —
    loopback, private, link-local (incl. the 169.254.169.254 metadata
    endpoint), reserved and multicast ranges are refused
  - redirects are followed manually, at most ``_MAX_REDIRECTS`` hops, and each
    hop is re-validated (an allowed public host must not be able to bounce us
    into the private network)
  - the response must be text/html or text/plain, and the body is read with a
    hard byte cap so a huge or endless stream can't exhaust memory

Residual risk: a DNS rebind between our resolution and httpx's own could still
slip through. Closing that needs connect-to-pinned-IP with SNI override; the
caller is an authenticated internal tool, so the resolve-time check is the
accepted trade-off.

Failures raise :class:`UrlFetchError`. Callers treat the URL as optional
enrichment and degrade gracefully rather than failing the run.
"""

from __future__ import annotations

import asyncio
import html as html_mod
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

import structlog

log = structlog.get_logger(__name__)

_MAX_REDIRECTS = 3
_DEFAULT_TIMEOUT_S = 10.0
_MAX_BYTES = 512 * 1024
_MAX_CHARS = 12_000
_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
_USER_AGENT = "Mozilla/5.0 (compatible; ResizeBot/1.0; +cloud.ru)"

_DROP_TAGS_RE = re.compile(
    r"<(script|style|noscript|template|svg|head)\b.*?</\1\s*>", re.S | re.I
)
_BLOCK_TAGS_RE = re.compile(
    r"</?(p|div|section|article|br|li|tr|h[1-6]|ul|ol|table|header|footer)\b[^>]*>",
    re.I,
)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKS_RE = re.compile(r"\n{3,}")


class UrlFetchError(RuntimeError):
    """The URL could not be fetched or was rejected by the SSRF guard."""


async def fetch_page_text(
    url: str,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_chars: int = _MAX_CHARS,
) -> str:
    """Fetch ``url`` and return its readable text (title + body, truncated)."""
    import httpx

    current = url
    async with httpx.AsyncClient(
        timeout=timeout_s,
        follow_redirects=False,
        headers={"User-Agent": _USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            await _assert_public(current)
            try:
                request = client.build_request("GET", current)
                response = await client.send(request, stream=True)
            except httpx.HTTPError as exc:
                raise UrlFetchError(f"request failed: {exc}") from exc

            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise UrlFetchError("redirect without Location header")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise UrlFetchError(f"HTTP {response.status_code}")
                _assert_text(response.headers.get("content-type", ""))
                body = await _read_capped(response)
            finally:
                await response.aclose()

            text = _to_text(body, max_chars=max_chars)
            if not text:
                raise UrlFetchError("page has no readable text")
            log.info("url_fetch_ok", url=url, final_url=current, chars=len(text))
            return text

    raise UrlFetchError("too many redirects")


async def _read_capped(response) -> str:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total >= _MAX_BYTES:
            break
    raw = b"".join(chunks)[:_MAX_BYTES]
    encoding = response.encoding or "utf-8"
    return raw.decode(encoding, errors="replace")


def _assert_text(content_type: str) -> None:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime and not mime.startswith(_ALLOWED_TYPES):
        raise UrlFetchError(f"unsupported content-type: {mime}")


async def _assert_public(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UrlFetchError(f"unsupported scheme: {parts.scheme or '(none)'}")
    host = parts.hostname
    if not host:
        raise UrlFetchError("URL has no host")

    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise UrlFetchError(f"cannot resolve host {host!r}") from exc

    if not infos:
        raise UrlFetchError(f"cannot resolve host {host!r}")
    for info in infos:
        addr = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not _is_public(addr):
            raise UrlFetchError(f"host {host!r} resolves to non-public {addr}")


def _is_public(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return False
    if addr.is_reserved or addr.is_multicast or addr.is_unspecified:
        return False
    # IPv4-mapped/6to4 addresses smuggle a v4 target inside a v6 literal.
    mapped = getattr(addr, "ipv4_mapped", None) or getattr(addr, "sixtofour", None)
    if mapped is not None:
        return _is_public(mapped)
    return True


def _to_text(document: str, *, max_chars: int) -> str:
    title_match = _TITLE_RE.search(document)
    title = _clean(_TAG_RE.sub(" ", title_match.group(1))) if title_match else ""

    body = _DROP_TAGS_RE.sub(" ", document)
    body = _BLOCK_TAGS_RE.sub("\n", body)
    body = _TAG_RE.sub(" ", body)
    body = _clean(body)

    text = f"{title}\n\n{body}" if title else body
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + " …"
    return text.strip()


def _clean(text: str) -> str:
    text = html_mod.unescape(text).replace("\xa0", " ")
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS_RE.sub("\n\n", text).strip()
