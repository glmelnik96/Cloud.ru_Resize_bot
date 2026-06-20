"""App3 / creatives — FastAPI web sub-app behind the platform gateway.

This package is the web frontend layer for Resize_bot's /new pipeline. It
follows the gateway↔sub-app contract (App1 = reference): the process listens
ONLY on 127.0.0.1:<port>, trusts identity from X-User-Id / X-User-Email
headers injected by the gateway, has its own SQLite DB, and exposes the
pipeline as interactive tasks (HITL over SSE + REST).

The heavy lifting (LangGraph pipeline, PIL composer, Cloud.ru FM client) is
reused as-is from the graph/ infra/ llm/ packages — only the Telegram I/O
layer (bot/) is replaced by this web layer.
"""
