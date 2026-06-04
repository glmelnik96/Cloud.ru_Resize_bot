"""One-shot helper to dump the structure of a Figma page -> JSON.

Operator runs this OUTSIDE the bot (e.g. `python scripts/figma_dump_structure.py
--file-key ... --page-id 3292:2`) against the locally running Figma Desktop MCP.
The output is a JSON tree of page -> frames -> leaf text/image nodes that the
operator (or Claude) edits into `config/figma_templates.json`.

Why a one-shot JS dump (not per-node get_metadata):
  get_metadata has been observed to time out on heavy pages/frames in this
  Figma file. Plugin-API traversal is fast and reliable for the structure we
  actually need: frame id+name+size, leaf node id+name+type. The operator picks
  the slogan/hero/cta nodes by name heuristics + visual confirmation in Figma.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack

_JS_DUMP = r"""
const page = figma.getNodeById("__PAGE_ID__");
if (!page) { throw new Error("page not found: __PAGE_ID__"); }
const out = { id: page.id, name: page.name, frames: [] };
for (const f of page.children) {
  if (f.type !== "FRAME") continue;
  const frame = { id: f.id, name: f.name, width: f.width, height: f.height, leaves: [] };
  const walk = (n) => {
    if (n.type === "TEXT" || (n.fills && n.fills.length)) {
      frame.leaves.push({ id: n.id, name: n.name, type: n.type });
    }
    if ("children" in n) { for (const c of n.children) walk(c); }
  };
  for (const c of f.children) walk(c);
  out.frames.push(frame);
}
JSON.stringify(out);
"""


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file-key", required=True)
    ap.add_argument("--page-id", required=True, help="e.g. 3292:2")
    ap.add_argument(
        "--mcp-url",
        default=os.environ.get("FIGMA_MCP_URL", "http://127.0.0.1:3845/mcp"),
        help="Defaults to FIGMA_MCP_URL env or local Desktop Figma",
    )
    args = ap.parse_args()

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with AsyncExitStack() as stack:
        read, write, _ = await stack.enter_async_context(streamablehttp_client(args.mcp_url))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        code = _JS_DUMP.replace("__PAGE_ID__", args.page_id)
        result = await session.call_tool(
            "use_figma", {"fileKey": args.file_key, "code": code}
        )
        # use_figma can return {result: "..."} or {content: [{text: "..."}]};
        # try both then fall back to repr.
        payload = None
        if isinstance(result, dict):
            payload = result.get("result")
            if payload is None and isinstance(result.get("content"), list):
                first = result["content"][0]
                if isinstance(first, dict):
                    payload = first.get("text")
        if not payload:
            print(json.dumps({"raw": repr(result)}, ensure_ascii=False), file=sys.stderr)
            return 2
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            print(payload)
            return 0
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
