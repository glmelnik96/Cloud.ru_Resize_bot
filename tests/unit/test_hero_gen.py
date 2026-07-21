"""App1HeroGenerator SSRF allowlist for the JSON `{"url": ...}` download branch."""
from __future__ import annotations

from app.services.hero_gen import App1HeroGenerator


def _gen() -> App1HeroGenerator:
    return App1HeroGenerator("http://127.0.0.1:8011/internal/hero")


def test_download_allowed_same_origin():
    g = _gen()
    # Same host:port as the configured App1 endpoint → allowed.
    assert g._download_allowed("http://127.0.0.1:8011/files/hero.png")


def test_download_denied_other_host():
    g = _gen()
    # Cloud metadata / internal service / different port must all be refused,
    # so a compromised App1 response can't drive App3 into SSRF.
    assert not g._download_allowed("http://169.254.169.254/latest/meta-data/")
    assert not g._download_allowed("http://127.0.0.1:9999/x.png")
    assert not g._download_allowed("http://evil.example/x.png")


def test_download_denied_non_http_scheme():
    g = _gen()
    assert not g._download_allowed("file:///etc/passwd")
    assert not g._download_allowed("gopher://127.0.0.1:8011/x")
