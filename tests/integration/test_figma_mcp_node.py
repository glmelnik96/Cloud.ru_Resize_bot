"""M3.2 unit tests — manifest loader, MCP client (fake), node behaviour."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def test_manifest_load_valid(tmp_path: Path) -> None:
    from infra.figma_manifest import load_manifest

    cfg = {
        "file_key": "AAA",
        "templates": {
            "vk_post_1080x1080": {
                "frame_id": "3302:516",
                "width": 1080,
                "height": 1080,
                "slots": {
                    "slogan_text_id": "3302:520",
                    "hero_image_id": "3302:522",
                    "cta_text_id": None,
                },
            }
        },
    }
    p = tmp_path / "m.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    m = load_manifest(p)
    assert m.file_key == "AAA"
    assert "vk_post_1080x1080" in m.templates
    t = m.templates["vk_post_1080x1080"]
    assert t.frame_id == "3302:516"
    assert t.width == 1080
    assert t.slots.cta_text_id is None
    assert t.slots.slogan_text_id == "3302:520"


def test_manifest_load_missing_file(tmp_path: Path) -> None:
    from infra.figma_manifest import load_manifest

    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "nope.json")


def test_manifest_load_invalid_json(tmp_path: Path) -> None:
    from infra.figma_manifest import load_manifest

    p = tmp_path / "m.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_manifest(p)


def test_manifest_missing_required_slot(tmp_path: Path) -> None:
    from pydantic import ValidationError

    from infra.figma_manifest import load_manifest

    cfg = {
        "file_key": "AAA",
        "templates": {
            "vk_post_1080x1080": {
                "frame_id": "3302:516",
                "width": 1080,
                "height": 1080,
                "slots": {
                    # missing slogan_text_id and hero_image_id
                    "cta_text_id": None
                },
            }
        },
    }
    p = tmp_path / "m.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_manifest(p)


@pytest.mark.asyncio
async def test_figma_client_disabled_when_env_empty(monkeypatch) -> None:
    from infra import figma_mcp

    monkeypatch.setenv("FIGMA_MCP_URL", "")
    # Reset singleton state between tests
    figma_mcp._client_handle = None  # type: ignore[attr-defined]
    handle = await figma_mcp.start_figma_mcp_client()
    assert handle is None
    assert figma_mcp.get_client() is None


@pytest.mark.asyncio
async def test_figma_client_stop_is_idempotent(monkeypatch) -> None:
    from infra import figma_mcp

    figma_mcp._client_handle = None  # type: ignore[attr-defined]
    await figma_mcp.stop_figma_mcp_client()  # no-op when never started
    assert figma_mcp.get_client() is None


class _FakeSession:
    """Minimal stand-in for mcp.ClientSession used by FigmaMCPClient.

    Records every call_tool invocation as (name, dict_args) tuples so tests
    can assert ordering and arguments.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.next_result: Any = None

    async def call_tool(self, name: str, args: dict) -> Any:
        self.calls.append((name, dict(args)))
        return self.next_result


def _client_with_fake_session() -> tuple[Any, _FakeSession]:
    from infra.figma_mcp import FigmaMCPClient

    c = FigmaMCPClient(url="http://fake/mcp")
    fake = _FakeSession()
    c._session = fake  # type: ignore[attr-defined]
    return c, fake


@pytest.mark.asyncio
async def test_upload_hero_calls_upload_assets() -> None:
    c, fake = _client_with_fake_session()
    await c.upload_hero(file_key="FK", node_id="3302:522", png_bytes=b"\x89PNG-fake")
    assert len(fake.calls) == 1
    name, args = fake.calls[0]
    assert name == "upload_assets"
    assert args["fileKey"] == "FK"
    # Asset goes inside assets[0] — confirm both nodeId and raw bytes survived.
    assert args["assets"][0]["nodeId"] == "3302:522"
    assert args["assets"][0]["bytes"] == b"\x89PNG-fake"


@pytest.mark.asyncio
async def test_set_texts_single_use_figma_call() -> None:
    c, fake = _client_with_fake_session()
    await c.set_texts(
        file_key="FK",
        replacements=[("3302:520", "Сэкономьте до 40%"), ("3302:530", "Купить")],
    )
    # One round-trip — both replacements packed into one use_figma JS call.
    assert len(fake.calls) == 1
    name, args = fake.calls[0]
    assert name == "use_figma"
    assert args["fileKey"] == "FK"
    code = args["code"]
    assert "3302:520" in code
    assert "Сэкономьте до 40%" in code
    assert "3302:530" in code
    assert "Купить" in code


@pytest.mark.asyncio
async def test_set_texts_escapes_quotes() -> None:
    c, fake = _client_with_fake_session()
    await c.set_texts(file_key="FK", replacements=[("3302:520", 'He said "hi"')])
    code = fake.calls[0][1]["code"]
    # Backslash-escaped so the JS string literal parses; raw form must NOT leak.
    assert 'He said \\"hi\\"' in code
    assert 'characters = "He said "hi""' not in code


@pytest.mark.asyncio
async def test_set_texts_empty_replacements_is_noop() -> None:
    c, fake = _client_with_fake_session()
    await c.set_texts(file_key="FK", replacements=[])
    assert fake.calls == []


@pytest.mark.asyncio
async def test_export_frame_calls_get_screenshot_and_fetches_url(monkeypatch) -> None:
    c, fake = _client_with_fake_session()
    fake.next_result = {"url": "http://figma-cdn/short-lived.png"}

    captured: dict = {}

    class _FakeResponse:
        status_code = 200
        content = b"PNG-bytes-from-cdn"

        def raise_for_status(self) -> None:
            return None

    class _FakeAsyncClient:
        def __init__(self, *_, **__): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
        async def get(self, url):
            captured["url"] = url
            return _FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    out = await c.export_frame(file_key="FK", node_id="3302:516", max_dim=1080)
    assert out == b"PNG-bytes-from-cdn"
    assert fake.calls[0][0] == "get_screenshot"
    assert fake.calls[0][1]["fileKey"] == "FK"
    assert fake.calls[0][1]["nodeId"] == "3302:516"
    assert fake.calls[0][1]["maxDimension"] == 1080
    assert captured["url"] == "http://figma-cdn/short-lived.png"


@pytest.mark.asyncio
async def test_export_frame_handles_url_inside_content_field(monkeypatch) -> None:
    """get_screenshot has been observed to return both {url: ...} and
    {content: [{url: ...}]} shapes across MCP versions. We accept both."""
    c, fake = _client_with_fake_session()
    fake.next_result = {"content": [{"url": "http://figma-cdn/x.png"}]}

    class _R:
        status_code = 200
        content = b"OK"
        def raise_for_status(self) -> None: return None

    class _AC:
        def __init__(self, *_, **__): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def get(self, url): return _R()

    # monkeypatch (not direct assignment) so the patch is reverted between tests.
    monkeypatch.setattr("httpx.AsyncClient", _AC)
    out = await c.export_frame(file_key="FK", node_id="3302:516", max_dim=1080)
    assert out == b"OK"


@pytest.mark.asyncio
async def test_export_frame_raises_when_no_url_in_result() -> None:
    c, fake = _client_with_fake_session()
    fake.next_result = {}  # neither "url" nor "content"
    with pytest.raises(RuntimeError, match="figma_export_no_url"):
        await c.export_frame(file_key="FK", node_id="3302:516", max_dim=1080)
