"""References and Vocabulary as Notion databases, upserted idempotently.

Both follow the same contract as pages: every row's properties are hashed into the
manifest, an unchanged row makes zero API writes, and rows whose source entry has
vanished are flagged in the sync report, never deleted. The database itself is created
once and its ID lives in the manifest; child_database blocks survive content
replacement (see notion.replace_content), so an inline database keeps its rows.

References rows are keyed by BibTeX key. The row title is the author-year label -
exactly the string ingest/flatten.py puts in the corpus text - so the linker can send
each in-text citation to its own row's page. Two bib entries can collide on the same
label (same surname, same year); the collision is reported and the citation links the
first, which is at worst one click from the right one.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .emit import Fragment, clean_text

BIB_PATHS = ["Bibliographies/bib.bib", "Bibliographies/references.bib"]

TYPE_NAMES = {"article": "Article", "book": "Book", "incollection": "Chapter",
              "inproceedings": "Conference paper", "techreport": "Report",
              "report": "Report", "online": "Web", "misc": "Misc", "thesis": "Thesis",
              "phdthesis": "Thesis", "mastersthesis": "Thesis", "manual": "Manual",
              "standard": "Standard", "legislation": "Legislation"}

REFS_SCHEMA = {
    "Reference": {"title": {}},
    "Title": {"rich_text": {}},
    "Authors": {"rich_text": {}},
    "Year": {"number": {"format": "number"}},
    "Type": {"select": {}},
    "Venue": {"rich_text": {}},
    "DOI": {"url": {}},
    "URL": {"url": {}},
    "Cited in": {"rich_text": {}},
    "Bib key": {"rich_text": {}},
}

VOCAB_SCHEMA = {
    "Term": {"title": {}},
    "Kind": {"select": {}},
    "Definition": {"rich_text": {}},
    "Unit": {"rich_text": {}},
}


def _rt(s: str) -> list[dict]:
    return [{"type": "text", "text": {"content": s[:1900]}}] if s else []


def _detex(s: str) -> str:
    s = re.sub(r"\\[A-Za-z]+", "", s)
    return clean_text(s.replace("{", "").replace("}", "")).strip()


# --- bib parsing -------------------------------------------------------------
def bib_entries(thesis_repo: Path) -> list[dict]:
    entries = []
    for rel in BIB_PATHS:
        p = thesis_repo / rel
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"@(\w+)\s*\{\s*([^,]+),(.*?)(?=\n@|\Z)", src, re.S):
            kind, key, body = m.group(1).lower(), m.group(2).strip(), m.group(3)

            def fld(*names: str) -> str:
                for name in names:
                    fm = re.search(rf"\n\s*{name}\s*=\s*[{{\"](.+?)[}}\"]\s*,?\s*\n",
                                   body, re.S)
                    if fm:
                        return re.sub(r"\s+", " ", fm.group(1)).strip("{} ")
                return ""

            # author is read raw, NOT via fld(): fld strips outer braces, which is
            # exactly the marker distinguishing a corporate author ({Zero Carbon
            # Hub}) from a person - and the label must match ingest/flatten.py's
            # construction character for character or citations won't resolve
            am = re.search(r"\n\s*author\s*=\s*[{\"](.+?)[}\"]\s*,?\s*\n", body, re.S)
            au = re.sub(r"\s+", " ", am.group(1)).strip() if am else ""
            names = au.split(" and ") if au else []
            first = names[0].strip() if names else ""
            if first.startswith("{"):
                surname = first                # corporate author; _detex drops braces
            else:
                surname = first.split(",")[0].strip() if "," in first else (
                    first.split()[-1] if first else "?")
            # same label construction as ingest/flatten.py - the corpus citation text
            # and the row title must agree or citation links cannot resolve
            yr = (re.search(r"\n\s*year\s*=\s*[{\"]?(\d{4})", body)
                  or re.search(r"\n\s*date\s*=\s*[{\"]?(\d{4})", body))
            label = (_detex(surname) + (" et al." if len(names) > 1 else "")
                     + f" {yr.group(1) if yr else 'n.d.'}")
            doi = fld("doi")
            entries.append({
                "key": key,
                "label": label,
                "title": _detex(fld("title")),
                "authors": _detex("; ".join(n.strip() for n in names)),
                "year": int(yr.group(1)) if yr else None,
                "type": TYPE_NAMES.get(kind, kind.capitalize()),
                "venue": _detex(fld("journaltitle", "journal", "booktitle",
                                    "publisher", "institution", "organization")),
                "doi": (doi if doi.startswith("http") else f"https://doi.org/{doi}")
                       if doi else None,
                "url": fld("url") or None,
            })
    return entries


def refs_properties(e: dict, cited_in: list[str]) -> dict:
    return {
        "Reference": {"title": _rt(e["label"])},
        "Title": {"rich_text": _rt(e["title"])},
        "Authors": {"rich_text": _rt(e["authors"])},
        "Year": {"number": e["year"]},
        "Type": {"select": {"name": e["type"]} if e["type"] else None},
        "Venue": {"rich_text": _rt(e["venue"])},
        "DOI": {"url": e["doi"]},
        "URL": {"url": e["url"]},
        "Cited in": {"rich_text": _rt(", ".join(cited_in))},
        "Bib key": {"rich_text": _rt(e["key"])},
    }


# --- vocabulary terms from the corpus's vocab section ------------------------
TERM_RE = re.compile(r"\*\*(.+?)\*\*\s*[-–]\s*(.*)$")
UNIT_RE = re.compile(r"\s*\[([^\]]+)\]$")


def vocab_terms(fragments: list[Fragment]) -> list[dict]:
    terms, kind = [], ""
    kind_of = {"Abbreviations": "Abbreviation", "Symbols": "Symbol",
               "PTG model notation": "Notation"}
    for f in fragments:
        if f.kind == "heading":
            kind = kind_of.get(f.text.strip(), f.text.strip() or "Term")
        elif f.kind == "bullet":
            m = TERM_RE.match(f.text)
            if not m:
                continue
            definition = m.group(2).strip()
            unit = ""
            um = UNIT_RE.search(definition)
            if um and kind == "Symbol":
                unit, definition = um.group(1), definition[: um.start()].strip()
            terms.append({"term": m.group(1).strip(), "kind": kind,
                          "definition": definition, "unit": unit})
        elif f.kind == "paragraph" and kind == "Notation" and "PTG" in f.text:
            # only the notation gloss itself: the corpus keeps front-matter LaTeX
            # (\tableofcontents etc.) between the vocab block and §1, which also
            # arrives here as paragraphs
            terms.append({"term": "PTG^form_vars", "kind": "Notation",
                          "definition": f.text, "unit": ""})
            kind = ""
    return terms


def vocab_properties(t: dict) -> dict:
    return {
        "Term": {"title": _rt(t["term"])},
        "Kind": {"select": {"name": t["kind"]}},
        "Definition": {"rich_text": _rt(t["definition"])},
        "Unit": {"rich_text": _rt(t["unit"])},
    }


# --- recorded settings (schema-level; views are invisible to the API) --------
def snapshot_schema(notion, database_id: str) -> dict:
    """Everything the API exposes about how a database is set up: title, icon,
    inline-ness, description, and properties including select-option colours.

    Views (sorts, filters, per-view property visibility) are NOT in the public API
    and cannot be captured or recreated - carrying hand-tuned views to another page
    means moving the database itself in the Notion UI, which sync then adopts.
    """
    db = notion.get_database(database_id)
    props: dict = {}
    for name, p in db["properties"].items():
        t = p["type"]
        if t == "select":
            props[name] = {"select": {"options": [
                {"name": o["name"], "color": o["color"]}
                for o in p["select"].get("options", [])]}}
        elif t == "number":
            props[name] = {"number": {"format": p["number"].get("format", "number")}}
        else:
            props[name] = {t: {}}
    icon = db.get("icon") or {}
    return {
        "title": "".join(t.get("plain_text", "") for t in db.get("title", [])),
        "icon": icon.get("emoji"),
        "is_inline": db.get("is_inline", False),
        "description": "".join(t.get("plain_text", "") for t in db.get("description", [])),
        "properties": props,
    }


def creation_schema(recorded: dict | None, default: dict, title: str,
                    report: list[str]) -> dict:
    """Schema for a fresh database: the recorded snapshot when there is one (so
    select colours and user-added properties carry over), with every property the
    rows actually write guaranteed present."""
    if not recorded:
        return default
    props = dict(recorded.get("properties") or default)
    for name, spec in default.items():
        if name not in props:
            report.append(f"'{title}': recorded settings were missing required "
                          f"property '{name}' - restored")
            props[name] = spec
    return props


# --- shared upsert -----------------------------------------------------------
def row_hash(properties: dict) -> str:
    return hashlib.sha256(json.dumps(properties, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


def _plain(prop: dict) -> str:
    parts = prop.get("title") or prop.get("rich_text") or []
    return "".join(t.get("plain_text", "") for t in parts)


def _adopt(notion, manifest_db: dict, parent_page_id: str, title: str,
           key_property: str, rows: list[tuple[str, dict]], report: list[str]) -> bool:
    """Adopt a same-titled database already under the parent instead of creating a
    duplicate. This is how hand-tuned views reach the real wiki: the API cannot
    create views, but a database MOVED under the parent in the Notion UI keeps
    them - sync then takes it over and reconciles its rows by natural key."""
    match = next((c for c in notion.list_children(parent_page_id)
                  if c.get("type") == "child_database"
                  and c["child_database"].get("title") == title), None)
    if match is None:
        return False
    manifest_db.update({"database_id": match["id"],
                        "url": "https://www.notion.so/" + match["id"].replace("-", ""),
                        "rows": {}})
    want = dict(rows)
    adopted = 0
    for r in notion.query_database(match["id"]):
        key = _plain(r["properties"].get(key_property, {}))
        if key in want and key not in manifest_db["rows"]:
            manifest_db["rows"][key] = {"page_id": r["id"],
                                        "hash": row_hash(want[key])}
            adopted += 1
    report.append(f"'{title}': adopted existing database under the parent "
                  f"({adopted} rows matched by {key_property}; views preserved)")
    return True


def sync_database(notion, manifest_db: dict, parent_page_id: str, title: str,
                  icon: str, schema: dict, rows: list[tuple[str, dict]],
                  report: list[str], inline: bool = False, key_property: str = "",
                  recorded: dict | None = None) -> dict:
    """Ensure the database exists (adopting a same-titled one on the parent first,
    then creating from recorded settings) and upsert `rows` [(key, properties)].
    Mutates and returns manifest_db: {database_id, url, rows: {key: {page_id, hash}}}.
    """
    if not manifest_db.get("database_id"):
        if not (key_property
                and _adopt(notion, manifest_db, parent_page_id, title, key_property,
                           rows, report)):
            out = notion.create_database(
                parent_page_id, (recorded or {}).get("title") or title,
                creation_schema(recorded, schema, title, report),
                (recorded or {}).get("icon") or icon,
                (recorded or {}).get("is_inline", inline),
                (recorded or {}).get("description", ""))
            manifest_db.update({"database_id": out["id"], "url": out.get("url", ""),
                                "rows": {}})
            report.append(f"created database '{title}'")
    db_id = manifest_db["database_id"]
    known = manifest_db.setdefault("rows", {})

    created = updated = unchanged = 0
    seen = set()
    for key, properties in rows:
        if key in seen:
            continue
        seen.add(key)
        h = row_hash(properties)
        entry = known.get(key)
        if entry is None:
            page_id = notion.create_db_page(db_id, properties)
            known[key] = {"page_id": page_id, "hash": h}
            created += 1
        elif entry["hash"] != h:
            notion.update_page_properties(entry["page_id"], properties)
            entry["hash"] = h
            updated += 1
        else:
            unchanged += 1

    gone = sorted(set(known) - seen)
    if gone:
        report.append(f"'{title}': {len(gone)} rows no longer in the source, kept in "
                      f"Notion: {', '.join(gone[:8])}{'…' if len(gone) > 8 else ''}")
    report.append(f"'{title}': {created} rows created, {updated} updated, "
                  f"{unchanged} unchanged")
    return manifest_db


def citation_urls(manifest_db: dict, entries: list[dict], report: list[str]) -> dict[str, str]:
    """author-year label -> row page URL. First entry wins on label collisions."""
    urls: dict[str, str] = {}
    collisions = []
    for e in entries:
        row = manifest_db.get("rows", {}).get(e["key"])
        if not row:
            continue
        if e["label"] in urls:
            collisions.append(e["label"])
            continue
        urls[e["label"]] = "https://www.notion.so/" + row["page_id"].replace("-", "")
    if collisions:
        report.append(f"references: {len(collisions)} author-year labels are ambiguous "
                      f"(citation links go to the first match): "
                      f"{', '.join(sorted(set(collisions))[:6])}")
    return urls
