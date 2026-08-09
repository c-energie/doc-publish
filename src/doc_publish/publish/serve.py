"""Serve the rendered site locally: doc-publish serve [--port 8123]

Needed because Quarto's search fetches search.json over XHR, which browsers block
under file:// - everything else in the site works from a bare folder.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import sys
from pathlib import Path

from .. import config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="doc-publish serve")
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--dir", default=None, help="default: the configured site build dir")
    args = ap.parse_args(argv)

    try:
        root = Path(args.dir) if args.dir else config.site_build_dir()
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    if not (root / "index.html").exists():
        print(f"{root}/index.html not found - run `doc-publish site` first", file=sys.stderr)
        return 2
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(root))
    print(f"serving {root} at http://localhost:{args.port}/  (Ctrl+C to stop)")
    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
