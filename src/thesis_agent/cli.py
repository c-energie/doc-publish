"""The `thesis-agent` command. One entry point, subcommands per stage.

Each subcommand is a thin shim over a module's `main()`, which stays independently
runnable as `python -m thesis_agent.<module>`. Imports are deferred into the handlers
so the ingest and publish paths need none of the serving dependencies - `thesis-agent
build` works in an environment with neither anthropic nor fastapi installed.
"""
from __future__ import annotations

import argparse
import sys

USAGE = """thesis-agent <command> [options]

  config     show how every setting resolved, and from where
  build      LaTeX -> corpus_public.md, corpus_draft.md, labels, figure manifest
  figures    map interactive .html exports onto figure labels
  sync       publish the public corpus to Notion (idempotent)
  site       build the Quarto site from the public corpus
  serve      serve the rendered site locally
  bundle     zip the rendered site for hand-carried distribution
  publish    copy the site and corpus into $THESIS_PUBLISH_REPO (no commit)
  app        run the chat server (needs the 'app' extra)
  agent      run the Agent SDK chat server (needs the 'agent' extra)
"""


def _config(argv: list[str]) -> int:
    from . import config
    print(config.describe())
    return 0


def _app(argv: list[str]) -> int:
    import uvicorn
    ap = argparse.ArgumentParser(prog="thesis-agent app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args(argv)
    uvicorn.run("thesis_agent.app.server:app", host=args.host, port=args.port,
                reload=args.reload)
    return 0


def _agent(argv: list[str]) -> int:
    """The Agent SDK backend, which cannot run under the uvicorn CLI on Windows.

    Uvicorn installs WindowsSelectorEventLoopPolicy on startup, and a SelectorEventLoop
    cannot spawn subprocesses - asyncio raises a bare NotImplementedError. The SDK works
    by launching the Claude Code CLI as a subprocess, so the two are mutually exclusive.
    Running the server on a loop we create ourselves keeps the Proactor policy set here.
    """
    import asyncio
    import uvicorn

    ap = argparse.ArgumentParser(prog="thesis-agent agent")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    # reload is deliberately unavailable: the reloader spawns a child that re-creates
    # the loop under uvicorn's own policy, reintroducing the bug. Restart by hand.
    server = uvicorn.Server(uvicorn.Config(
        "thesis_agent.app.agent_server:app", host=args.host, port=args.port,
        loop="asyncio", log_level="info"))
    asyncio.run(server.serve())
    return 0


def _lazy(module: str):
    def run(argv: list[str]) -> int:
        import importlib
        main = importlib.import_module(module).main
        # ingest.build takes no argv; everything else parses its own.
        return main() if module.endswith(".build") else main(argv)
    return run


COMMANDS = {
    "config": _config,
    "build": _lazy("thesis_agent.ingest.build"),
    "figures": _lazy("thesis_agent.ingest.manifest"),
    "sync": _lazy("thesis_agent.publish.sync"),
    "site": _lazy("thesis_agent.publish.build_site"),
    "serve": _lazy("thesis_agent.publish.serve"),
    "bundle": _lazy("thesis_agent.publish.bundle"),
    "publish": _lazy("thesis_agent.publish.to_repo"),
    "app": _app,
    "agent": _agent,
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    command, rest = args[0], args[1:]
    if command not in COMMANDS:
        print(f"unknown command {command!r}\n\n{USAGE}", file=sys.stderr)
        return 2
    try:
        return COMMANDS[command](rest)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
