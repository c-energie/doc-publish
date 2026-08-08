"""Thin Notion REST client for the publisher.

Deliberately not the notion-client SDK: the publisher needs an exact count of write
calls (the idempotency acceptance test is "second run makes zero writes"), polite rate
limiting, and nothing else. Uses API version 2022-06-28 throughout, which takes
*database* IDs everywhere - so only the `pageParents` map in context.json is ever
needed and the data-source/database id confusion documented there cannot arise here.

Hard safety rule, enforced at this layer: `child_page` blocks are never deleted.
Replacing a page's content must not archive its subpages - deleting a child_page block
trashes the page behind it, which orphans the manifest and breaks every inbound link.
"""
from __future__ import annotations

import json
import os
import time

import httpx

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
MIN_INTERVAL = 0.36  # ~2.8 req/s, under Notion's ~3/s average limit


class NotionError(RuntimeError):
    pass


def _find_deferred(block: dict, path: list[int] | None = None) -> list[tuple]:
    """(offset-path, blocks) pairs for every `_deferred` list in an inline-children
    tree. Path None means the block itself; [1, 0] means children[1].children[0]."""
    found = []
    if block.get("_deferred"):
        found.append((path, block["_deferred"]))
    body = block.get(block.get("type"), {})
    if isinstance(body, dict):
        for i, child in enumerate(body.get("children", [])):
            found += _find_deferred(child, (path or []) + [i])
    return found


class Notion:
    def __init__(self, token: str | None = None, dry_run: bool = False):
        token = token or os.environ.get("NOTION_TOKEN", "")
        if not token and not dry_run:
            raise NotionError("NOTION_TOKEN is not set")
        self.dry_run = dry_run
        self.reads = 0
        self.writes = 0
        self._last = 0.0
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": VERSION,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    # -- transport -----------------------------------------------------------
    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        is_write = method in ("POST", "PATCH", "DELETE") and not path.endswith("/query")
        if is_write:
            self.writes += 1
            if self.dry_run:
                # mirror the real response shape: children appends return one result
                # per block, and append_tree recurses on them
                n = len(payload.get("children", [])) if payload else 0
                return {"dry_run": True, "id": "dry-run-id",
                        "url": "https://www.notion.so/dryrun",
                        "results": [{"id": "dry-run-id", "type": b.get("type")}
                                    for b in (payload or {}).get("children", [])][:n]}
        else:
            self.reads += 1

        for attempt in range(6):
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self._client.request(method, f"{API}{path}",
                                     content=json.dumps(payload) if payload is not None else None)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 2)) + 0.5)
                continue
            if r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise NotionError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
            return r.json()
        raise NotionError(f"{method} {path}: gave up after repeated 429/5xx")

    # -- pages ---------------------------------------------------------------
    def create_page(self, parent_page_id: str, title: str, icon: str | None = None) -> str:
        payload: dict = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title[:200]}}]}},
        }
        if icon:
            payload["icon"] = {"type": "emoji", "emoji": icon}
        return self._request("POST", "/pages", payload)["id"]

    def set_icon(self, page_id: str, icon: str) -> None:
        self._request("PATCH", f"/pages/{page_id}", {"icon": {"type": "emoji", "emoji": icon}})

    def update_page_properties(self, page_id: str, properties: dict) -> None:
        self._request("PATCH", f"/pages/{page_id}", {"properties": properties})

    def create_db_page(self, database_id: str, properties: dict) -> str:
        out = self._request("POST", "/pages", {
            "parent": {"type": "database_id", "database_id": database_id},
            "properties": properties,
        })
        return out["id"]

    def rename_page(self, page_id: str, title: str) -> None:
        self._request("PATCH", f"/pages/{page_id}", {
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title[:200]}}]}},
        })

    def get_page(self, page_id: str) -> dict:
        return self._request("GET", f"/pages/{page_id}")

    # -- blocks --------------------------------------------------------------
    def list_children(self, block_id: str) -> list[dict]:
        results, cursor = [], None
        while True:
            path = f"/blocks/{block_id}/children?page_size=100"
            if cursor:
                path += f"&start_cursor={cursor}"
            out = self._request("GET", path)
            results += out["results"]
            if not out.get("has_more"):
                return results
            cursor = out["next_cursor"]

    def append_children(self, block_id: str, blocks: list[dict]) -> list[dict]:
        created = []
        for i in range(0, len(blocks), 100):
            out = self._request("PATCH", f"/blocks/{block_id}/children",
                                {"children": blocks[i : i + 100]})
            created += out.get("results", [])
        return created

    def append_tree(self, block_id: str, blocks: list[dict]) -> None:
        """Append blocks whose deeper children arrive in follow-up requests.

        The API allows two levels of nesting per request and only returns IDs for
        top-level blocks - so nested toggle headings (and tables inside card callouts)
        cannot go in one call. A block may carry `_deferred`: a list of child blocks
        stripped here and appended to that block's ID once it exists. Inline
        `children` (tables' rows, callouts' bullets) pass through untouched.
        """
        def strip(b: dict) -> dict:
            out = {k: v for k, v in b.items() if k != "_deferred"}
            body = out.get(out.get("type"), None)
            if isinstance(body, dict) and "children" in body:
                body = dict(body)
                body["children"] = [strip(c) for c in body["children"]]
                out[out["type"]] = body
            return out

        for i in range(0, len(blocks), 100):
            chunk = blocks[i : i + 100]
            out = self._request("PATCH", f"/blocks/{block_id}/children",
                                {"children": [strip(b) for b in chunk]})
            for src, made in zip(chunk, out.get("results", [])):
                deferred = _find_deferred(src)
                for target_offset, sub in deferred:
                    target_id = made["id"] if target_offset is None else \
                        self._nested_child_id(made, target_offset)
                    self.append_tree(target_id, sub)

    def _nested_child_id(self, made: dict, offset: list[int]) -> str:
        """Resolve the ID of a nested child (e.g. a column inside a column_list) by
        listing the created block's children - the append response only carries
        top-level IDs."""
        block_id = made["id"]
        if self.dry_run:
            return block_id
        for idx in offset:
            kids = self.list_children(block_id)
            block_id = kids[idx]["id"] if idx < len(kids) else block_id
        return block_id

    def delete_block(self, block_id: str) -> None:
        self._request("DELETE", f"/blocks/{block_id}")

    KEEP_TYPES = ("child_page", "child_database")

    def replace_content(self, page_id: str, blocks: list[dict]) -> list[dict]:
        """Delete the page's content blocks and append the new ones.

        child_page and child_database blocks survive: subpages and inline databases
        keep their IDs, so inbound links, comments and database rows keep working.
        Content therefore always sits below them, on every run, which keeps
        first-publish and update layouts identical (appends land at the end).
        """
        for child in self.list_children(page_id):
            if child.get("type") not in self.KEEP_TYPES:
                self.delete_block(child["id"])
        return self.append_tree(page_id, blocks)

    # -- databases: creation + rows -------------------------------------------
    def get_database(self, database_id: str) -> dict:
        return self._request("GET", f"/databases/{database_id}")

    def create_database(self, parent_page_id: str, title: str, properties: dict,
                        icon: str | None = None, inline: bool = False,
                        description: str = "") -> dict:
        payload: dict = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties,
            "is_inline": inline,
        }
        if icon:
            payload["icon"] = {"type": "emoji", "emoji": icon}
        if description:
            payload["description"] = [{"type": "text", "text": {"content": description}}]
        return self._request("POST", "/databases", payload)

    # -- databases (2022-06-28: database ids, i.e. context.json pageParents) --
    def query_database(self, database_id: str, filter_: dict | None = None) -> list[dict]:
        results, cursor = [], None
        while True:
            payload: dict = {"page_size": 100}
            if filter_:
                payload["filter"] = filter_
            if cursor:
                payload["start_cursor"] = cursor
            out = self._request("POST", f"/databases/{database_id}/query", payload)
            results += out["results"]
            if not out.get("has_more"):
                return results
            cursor = out["next_cursor"]
