"""FastAPI server: cached thesis corpus, tool loop, SSE streaming."""
from __future__ import annotations

import json
import os
from pathlib import Path

from anthropic import Anthropic
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import config
from .prompt import build_system
from .tools import SCHEMA, dispatch

MODEL = "claude-sonnet-4-6"
BUILD = config.build_dir()
# public = comments stripped (safe to serve); draft = source comments kept (author only)
MODE = config.corpus_mode()
CORPUS = (BUILD / f"corpus_{MODE}.md").read_text(encoding="utf-8")
SYSTEM = build_system()
INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
app = FastAPI(title="thesis-agent")

# Two blocks: instructions, then the corpus with a cache breakpoint. The corpus never
# changes between requests, so every turn after the first reads it at ~0.1x input rate.
SYSTEM_BLOCKS = [
    {"type": "text", "text": SYSTEM},
    {"type": "text", "text": CORPUS, "cache_control": {"type": "ephemeral"}},
]


class Ask(BaseModel):
    messages: list[dict]


@app.post("/ask")
def ask(req: Ask):
    def run():
        messages = list(req.messages)
        for _ in range(8):  # tool-loop bound
            with client.messages.stream(
                model=MODEL,
                max_tokens=2000,
                system=SYSTEM_BLOCKS,
                tools=SCHEMA,
                messages=messages,
            ) as stream:
                for event in stream.text_stream:
                    yield f"data: {json.dumps({'type': 'text', 'text': event})}\n\n"
                final = stream.get_final_message()

            messages.append({"role": "assistant", "content": final.content})
            calls = [b for b in final.content if b.type == "tool_use"]
            if not calls:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            results = []
            for c in calls:
                yield f"data: {json.dumps({'type': 'tool', 'name': c.name})}\n\n"
                blocks = dispatch(c.name, c.input)
                for b in blocks:
                    if b["type"] == "image":
                        yield f"data: {json.dumps({'type': 'image', 'data': b['source']['data'], 'media': b['source']['media_type']})}\n\n"
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": blocks})
            messages.append({"role": "user", "content": results})

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(run(), media_type="text/event-stream")


@app.get("/")
def index():
    return FileResponse(INDEX)


@app.get("/health")
def health():
    return {"mode": MODE, "corpus_chars": len(CORPUS),
            "figures": len(json.loads((BUILD / "figures.json").read_text()))}
