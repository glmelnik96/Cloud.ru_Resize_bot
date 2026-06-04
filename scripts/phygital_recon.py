"""Phygital+ headed login recon (run on HOST, not inside Docker).

Why:
- Phygital+ login goes through Google SSO which refuses headless Linux Chromium
  inside the bot container. We do the OAuth handshake once on the host with a
  visible browser, persist the entire browser profile to disk, and later the
  container launches its own persistent context against that profile read-only.

How:
- Launches Chromium in non-headless mode with persistent user_data_dir
  at ``var/playwright_user_data/``.
- Opens ``PHYGITAL_URL`` (default ``https://phygital.plus``).
- Waits for the user to close the browser window after finishing login.
- Persistent context auto-saves cookies, localStorage, IndexedDB on close.

Usage:
    .venv/Scripts/python.exe scripts/phygital_recon.py
or:
    PHYGITAL_URL=https://app.phygital.plus .venv/Scripts/python.exe scripts/phygital_recon.py

Re-run any time the session expires (Phygital will return 401 → bot pings owner
in TG and you re-run this script).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

_REPO_ROOT = Path(__file__).resolve().parent.parent
_USER_DATA = _REPO_ROOT / "var" / "playwright_user_data"
_DEFAULT_URL = "https://phygital.plus"


async def main() -> None:
    url = os.environ.get("PHYGITAL_URL", _DEFAULT_URL)
    _USER_DATA.mkdir(parents=True, exist_ok=True)
    print(f"[recon] user_data_dir = {_USER_DATA}")
    print(f"[recon] target URL    = {url}")
    print("[recon] launching headed Chromium...")

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(_USER_DATA),
            headless=False,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:  # noqa: BLE001
            print(f"[recon] WARNING: initial goto failed ({exc}); browse manually.")

        print()
        print("=" * 60)
        print(" Залогинься в окне браузера (Google SSO, email, что угодно).")
        print(" Убедись, что попал внутрь рабочего пространства Phygital.")
        print(" Когда готов — ПРОСТО ЗАКРОЙ ОКНО БРАУЗЕРА.")
        print(" Persistent context сохранится автоматически.")
        print("=" * 60)

        close_event = asyncio.Event()
        ctx.on("close", lambda: close_event.set())
        try:
            await close_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n[recon] aborted by user")
            try:
                await ctx.close()
            except Exception:
                pass
            sys.exit(1)

    print(f"[recon] saved profile to {_USER_DATA}")
    print("[recon] done. Container can now mount this dir read-only.")


if __name__ == "__main__":
    asyncio.run(main())
