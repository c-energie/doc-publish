"""Alternative backend: Claude Agent SDK instead of the Anthropic API.

Runs on your Claude plan's Agent SDK credit rather than API credits, so no
ANTHROPIC_API_KEY is needed. Serves the same SSE shape as server.py, so web/index.html
works unchanged.

    thesis-agent agent

Use that subcommand, not the uvicorn CLI: uvicorn forces a Windows event-loop policy
that cannot spawn the subprocess this backend depends on. See thesis_agent/cli.py.

Prerequisites:
  1. Node 18+ and the Claude Code CLI (`npm i -g @anthropic-ai/claude-code`)
  2. `claude login` with your Claude plan
  3. `pip install claude-agent-sdk`
  4. ANTHROPIC_API_KEY must be UNSET. If it is set, the SDK bills the API account
     instead of your plan - the opposite of the point of this file.

PERSONAL, LOCAL USE ONLY. Anthropic does not permit third-party products to offer
claude.ai login or plan rate limits to their users. The moment other people are hitting
this endpoint, switch to the `app` subcommand with a Console API key.

Single conversation per process: one client, one thread of history. That matches the
personal-use constraint above. Serving concurrent users needs a client per session, and
if you are doing that you should be on server.py anyway.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import config
from .prompt import build_system
from .tools import dispatch

MODEL = "claude-sonnet-5"
BUILD = config.build_dir()
MODE = config.corpus_mode()
CORPUS = (BUILD / f"corpus_{MODE}.md").read_text(encoding="utf-8")
SYSTEM = build_system()
INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"

if os.environ.get("ANTHROPIC_API_KEY"):
    raise SystemExit(
        "ANTHROPIC_API_KEY is set, so the Agent SDK would bill your API account rather "
        "than your Claude plan. Unset it, or run app/server.py instead."
    )

# Images returned by tools go to Claude inside the tool result, which the browser never
# sees. This queue carries a second copy out to the SSE stream so figures actually render.
_images: asyncio.Queue[dict] = asyncio.Queue()


def _to_mcp(blocks: list[dict]) -> dict[str, Any]:
    """Anthropic content blocks -> MCP tool result. MCP uses mimeType, not media_type."""
    out = []
    for b in blocks:
        if b["type"] == "image":
            src = b["source"]
            _images.put_nowait({"data": src["data"], "media": src["media_type"]})
            out.append({"type": "image", "data": src["data"], "mimeType": src["media_type"]})
        else:
            out.append(b)
    return {"content": out}


@tool("get_figure", "Retrieve a figure by its LaTeX label, e.g. 'Fig: model fits'. "
                    "Figures marked pending have no image - say so rather than describing them.",
      {"label": str})
async def get_figure(args: dict[str, Any]) -> dict[str, Any]:
    return _to_mcp(dispatch("get_figure", args))


@tool("list_figures", "List figures whose caption, label or section matches a term.",
      {"query": str})
async def list_figures(args: dict[str, Any]) -> dict[str, Any]:
    return _to_mcp(dispatch("list_figures", args))


@tool("query_results", "Query the per-record estimates behind the results chapters. "
                       "Args: filter (pandas .query expression), columns, groupby.",
      {"filter": str, "columns": list, "groupby": list})
async def query_results(args: dict[str, Any]) -> dict[str, Any]:
    return _to_mcp(dispatch("query_results", args))


@tool("plot_results", "Plot the per-record results. kind: scatter | hist | ecdf. "
                      "Generated now - not a figure from the document. Say so when showing one.",
      {"kind": str, "x": str, "y": str, "filter": str, "title": str})
async def plot_results(args: dict[str, Any]) -> dict[str, Any]:
    return _to_mcp(dispatch("plot_results", args))


DOCUMENT_TOOLS = create_sdk_mcp_server(
    name="document",
    version="1.0.0",
    tools=[get_figure, list_figures, query_results, plot_results],
)

# The rules + the whole corpus, written to disk and passed BY PATH.
#
# A string system_prompt is passed to the CLI as `--system-prompt <text>` on the command
# line. Windows caps a command line at 32,767 characters and the corpus is ~280,000, so
# CreateProcess fails with WinError 206 ("filename or extension is too long"). That
# surfaces as FileNotFoundError, which the SDK's handler interprets as a missing CLI and
# re-raises as CLINotFoundError - so the visible error names the wrong cause entirely.
#
# The file form sends a path instead, which is a few dozen characters. It also avoids
# passing the unpublished draft corpus through a process command line, where it would be
# visible to anything that can list processes.
SYSTEM_FILE = BUILD / f"system_{MODE}.txt"
SYSTEM_FILE.write_text(SYSTEM + "\n\n---\n\n" + CORPUS, encoding="utf-8")

OPTIONS = ClaudeAgentOptions(
    model=MODEL,
    system_prompt={"type": "file", "path": str(SYSTEM_FILE.resolve())},
    mcp_servers={"document": DOCUMENT_TOOLS},
    allowed_tools=[f"mcp__document__{n}" for n in
                   ("get_figure", "list_figures", "query_results", "plot_results")],
    # The agent answers from the corpus in its context, not by wandering the filesystem.
    # Without this it will try to Read the document repo and answer from raw LaTeX.
    disallowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch"],
    # Load no filesystem settings: CLAUDE.md carries grep-the-corpus instructions meant for
    # interactive Claude Code sessions, which would contradict the setup above.
    setting_sources=[],
    permission_mode="bypassPermissions",
    max_turns=8,
)

_client: ClaudeSDKClient | None = None
_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    _client = ClaudeSDKClient(options=OPTIONS)
    await _client.connect()
    yield
    await _client.disconnect()


app = FastAPI(title="thesis-agent (Agent SDK)", lifespan=lifespan)


class Ask(BaseModel):
    messages: list[dict]


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@app.post("/ask")
async def ask(req: Ask):
    # The SDK keeps its own history, so only the newest user turn is sent.
    prompt = next((m["content"] for m in reversed(req.messages) if m["role"] == "user"), "")

    async def run():
        # One conversation per process: serialise requests rather than interleave them.
        async with _lock:
            await _client.query(prompt)
            async for msg in _client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            yield _sse({"type": "text", "text": block.text})
                        elif isinstance(block, ToolUseBlock):
                            yield _sse({"type": "tool",
                                        "name": block.name.replace("mcp__document__", "")})
                    while not _images.empty():
                        img = _images.get_nowait()
                        yield _sse({"type": "image", **img})
                elif isinstance(msg, ResultMessage):
                    if msg.is_error:
                        yield _sse({"type": "text", "text": f"\n\n[error: {msg.stop_reason}]"})
                    yield _sse({"type": "done"})

    return StreamingResponse(run(), media_type="text/event-stream")


@app.get("/")
def index():
    return FileResponse(INDEX)


@app.get("/health")
async def health():
    return {"backend": "claude-agent-sdk", "model": MODEL, "mode": MODE,
            "corpus_chars": len(CORPUS),
            "figures": len(json.loads((BUILD / "figures.json").read_text()))}
