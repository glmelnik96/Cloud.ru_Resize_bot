"""Session-kind isolation: a /new (brief) handler must ignore a /banner
session and vice-versa, so a lingering ConversationHandler from one flow can't
steal text input meant for the other (live bug: cancelled /new wrote into an
active /banner session).
"""

from __future__ import annotations

from types import SimpleNamespace

from bot.banner_wizard import _banner_session
from bot.sessions import Session, put
from bot.wizard import _brief_session


def _ctx() -> SimpleNamespace:
    app = SimpleNamespace(bot_data={})
    return SimpleNamespace(application=app)


def test_default_kind_is_brief():
    assert Session(user_id=1, chat_id=1).kind == "brief"


def test_brief_handler_ignores_banner_session():
    ctx = _ctx()
    put(ctx.application.bot_data, Session(user_id=7, chat_id=7, kind="banner"))
    assert _brief_session(ctx, 7) is None  # brief handler must skip it
    assert _banner_session(ctx, 7) is not None  # banner handler owns it


def test_banner_handler_ignores_brief_session():
    ctx = _ctx()
    put(ctx.application.bot_data, Session(user_id=9, chat_id=9, kind="brief"))
    assert _banner_session(ctx, 9) is None
    assert _brief_session(ctx, 9) is not None


def test_no_session_returns_none():
    ctx = _ctx()
    assert _brief_session(ctx, 123) is None
    assert _banner_session(ctx, 123) is None
