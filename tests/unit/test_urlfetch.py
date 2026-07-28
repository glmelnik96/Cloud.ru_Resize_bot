"""Unit tests for the SSRF guard and HTML→text reduction in infra/urlfetch.

The URL comes from the marketer's brief and is fetched by the VM, so the guard
is the security boundary of the whole "read the product page" feature. These
tests are offline: DNS is stubbed so a hostname can be made to resolve wherever
the case needs it.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

from infra.urlfetch import (
    UrlFetchError,
    _assert_public,
    _assert_text,
    _is_public,
    _to_text,
)


def _stub_dns(monkeypatch, ip: str) -> None:
    """Make every hostname resolve to `ip`."""

    async def fake_getaddrinfo(host, port, **kw):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

    class _Loop:
        getaddrinfo = staticmethod(fake_getaddrinfo)

    monkeypatch.setattr(
        "infra.urlfetch.asyncio.get_running_loop", lambda: _Loop()
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "data:text/html,<h1>x</h1>",
        "https://",
    ],
)
async def test_rejects_non_http_schemes_and_missing_host(url, monkeypatch):
    _stub_dns(monkeypatch, "93.184.216.34")
    with pytest.raises(UrlFetchError):
        await _assert_public(url)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",        # loopback
        "10.1.2.3",         # private
        "192.168.0.7",      # private
        "172.16.0.9",       # private
        "169.254.169.254",  # cloud metadata endpoint
        "0.0.0.0",          # unspecified
        "224.0.0.1",        # multicast
        "::1",              # v6 loopback
        "fd00::1",          # v6 unique-local
        "::ffff:127.0.0.1",  # v4-mapped loopback smuggled in a v6 literal
    ],
)
async def test_rejects_non_public_targets(ip, monkeypatch):
    _stub_dns(monkeypatch, ip)
    with pytest.raises(UrlFetchError, match="non-public"):
        await _assert_public("https://totally-legit.example.com/page")


async def test_allows_public_target(monkeypatch):
    _stub_dns(monkeypatch, "93.184.216.34")
    await _assert_public("https://example.com/products/rag")  # no raise


async def test_unresolvable_host_is_an_error(monkeypatch):
    async def boom(host, port, **kw):
        raise socket.gaierror("nope")

    class _Loop:
        getaddrinfo = staticmethod(boom)

    monkeypatch.setattr("infra.urlfetch.asyncio.get_running_loop", lambda: _Loop())
    with pytest.raises(UrlFetchError, match="cannot resolve"):
        await _assert_public("https://does-not-exist.invalid/")


def test_is_public_unwraps_sixtofour():
    assert not _is_public(ipaddress.ip_address("2002:0a00:0001::"))  # 6to4 of 10.0.0.1
    assert _is_public(ipaddress.ip_address("2606:2800:220:1::"))


@pytest.mark.parametrize("mime", ["application/pdf", "image/png", "application/zip"])
def test_rejects_non_text_content(mime):
    with pytest.raises(UrlFetchError, match="unsupported content-type"):
        _assert_text(f"{mime}; charset=utf-8")


@pytest.mark.parametrize(
    "mime", ["text/html; charset=utf-8", "text/plain", "application/xhtml+xml", ""]
)
def test_accepts_text_content(mime):
    _assert_text(mime)  # no raise


def test_to_text_drops_scripts_and_keeps_title():
    html = """
    <html><head><title>Managed RAG — Cloud.ru</title>
    <style>.a{color:red}</style></head>
    <body><script>alert('x')</script>
    <h1>Managed&nbsp;RAG</h1><p>Поиск по документам.</p><p>Второй абзац.</p>
    </body></html>
    """
    text = _to_text(html, max_chars=12_000)
    assert text.startswith("Managed RAG — Cloud.ru")
    assert "Поиск по документам." in text
    assert "Второй абзац." in text
    assert "alert" not in text
    assert "color:red" not in text
    assert "<" not in text


def test_to_text_truncates_at_word_boundary():
    html = "<html><body><p>" + ("слово " * 500) + "</p></body></html>"
    text = _to_text(html, max_chars=100)
    assert len(text) <= 102  # cap + the ellipsis marker
    assert text.endswith("…")
