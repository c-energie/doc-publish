"""LaTeX -> markdown flattener, written against the conventions real documents use.

Verified against a working thesis; all of it is ordinary subfiles/glossaries/biblatex
usage rather than anything peculiar to that document:
  main.tex           subfiles: Preamble, Introduction, background_chapter, Use-cases,
                     Method, Results, Discussion, Conclusion
  custom_settings.sty  \\graphicspath (7 dirs), \\newcommand literals, biblatex resources
  glossary_terms.tex   \\newacronym (abbreviations) + \\newglossaryentry type=symbols

Pipeline order matters and is not negotiable:
  1. inline \\subfile/\\input        (paths are relative to the INCLUDING file's dir)
  2. split out % comments            (they carry provenance, TODOs, supervisor questions)
  3. resolve \\ExecuteMetaData       (AFTER 2: a commented-out table reference is one the
                                     author withheld, and must not be pulled in)
  4. expand macros                   (acronyms, symbols, citations, refs, and the
                                     document's own via ingest/adapter.py)
  5. number sections                 (chapter numbers come from main.tex's subfile order)

Two corpora come out of this: `public` (comments dropped) and `draft` (comments kept as
annotations). See build.py - the difference is not cosmetic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import adapter

ROOT = "main.tex"
SETTINGS = "custom_settings.sty"
GLOSSARY = "glossary_terms.tex"
BIB_PATHS = ["Bibliographies/bib.bib", "Bibliographies/references.bib"]

INCLUDE_RE = re.compile(r"\\(?:subfile|input|include)\{([^}]+)\}")
DOCCLASS_RE = re.compile(r"\\documentclass(?:\[[^\]]*\])?\{subfiles\}")
EXECMETA_RE = re.compile(r"\\ExecuteMetaData(?:\[([^\]]*)\])?\{([^}]+)\}")
COMMENT_RE = re.compile(r"(?<!\\)%(.*)$", re.M)
ACR_RE = re.compile(r"\\(acrshort|acrlong|acrfull|Acrlong|glsentryshort)\{([^}]+)\}")
GLS_RE = re.compile(r"\\(gls|Gls|glspl)\{([^}]+)\}")
CITE_RE = re.compile(
    r"\\(?:parencite|textcite|autocite|citeauthor|citeyear|posscite|cite[tp]?)\*?"
    r"(?:\[[^\]]*\])*\{([^}]+)\}"
)
REF_RE = re.compile(r"\\(?:cref|Cref|autoref|eqref|ref)\{([^}]+)\}")
LABEL_RE = re.compile(r"\\label\{([^}]+)\}")
SECTION_RE = re.compile(r"\\(chapter|section|subsection|subsubsection)\*?\s*\{")
FIGURE_RE = re.compile(r"\\begin\{figure\}\[?\w*\]?(.*?)\\end\{figure\}", re.S)
TABLE_RE = re.compile(r"\\begin\{table\}\[?\w*\]?(.*?)\\end\{table\}", re.S)
EQ_ENVS = ("equation", "align", "gather", "multline")
LEVELS = {"chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3}

REF_KIND = {"fig": "Figure", "tab": "Table", "eq": "Equation", "ch": "Chapter"}

# Macros the document defines for itself. Anything here that survives expansion is a
# macro nothing knows how to render - see check_document_macros.
MACRODEF_RE = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|newrobustcmd|DeclareRobustCommand|def)"
    r"\s*\*?\s*\{?\\([A-Za-z@]+)\}?"
)
# Maths is passed through as LaTeX on purpose, for MathJax/KaTeX to render. Unknown
# commands *inside* it are a rendering concern, reported separately by the site build.
MATH_SPAN_RE = re.compile(r"\$\$.*?\$\$|\$[^$\n]*\$", re.S)


@dataclass
class Vocab:
    acronyms: dict[str, tuple[str, str]] = field(default_factory=dict)   # key -> (short, long)
    symbols: dict[str, tuple[str, str, str]] = field(default_factory=dict)  # key -> (name, desc, unit)
    bib: dict[str, str] = field(default_factory=dict)
    literals: dict[str, str] = field(default_factory=dict)   # \newcommand{\n}{40} -> "40"
    # The document's own macro adapter, if it ships one. Carried here because Vocab
    # already reaches both expand() call sites and vocabulary_block(); see adapter.py.
    macros: Any = None


@dataclass
class Corpus:
    text: str
    labels: dict[str, str] = field(default_factory=dict)     # label -> "S3.2"
    annotations: list[dict] = field(default_factory=list)    # draft comments w/ section
    unresolved: list[str] = field(default_factory=list)
    vocab: Vocab = field(default_factory=Vocab)


# --- helpers ----------------------------------------------------------------
def _brace(s: str, open_idx: int) -> tuple[str, int]:
    depth, i = 0, open_idx
    while i < len(s):
        if s[i] == "{" and (i == 0 or s[i - 1] != "\\"):
            depth += 1
        elif s[i] == "}" and s[i - 1] != "\\":
            depth -= 1
            if depth == 0:
                return s[open_idx + 1 : i], i + 1
        i += 1
    return s[open_idx + 1 :], len(s)


def _detex(s: str) -> str:
    """Strip maths wrappers from a glossary `name`/`unit` field to plain text.

    Innermost-first and repeated: \\ensuremath{HTC_{\\text{in-use}}} has nested braces, so a
    single non-nested pass leaves the outer command behind as literal text.
    """
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"\\[A-Za-z]+\{([^{}]*)\}", r"\1", s)
    s = s.replace("\\,", " ").replace("^\\circ", "\u00b0")
    s = re.sub(r"\\[A-Za-z]+", "", s)
    return re.sub(r"[{}$]", "", s).strip()


# --- vocabulary -------------------------------------------------------------
def load_vocab(repo: Path) -> Vocab:
    v = Vocab()
    # Raises if the document ships a broken macros.py. Better here than three stages
    # later with raw \macro{...} already written into the corpus.
    v.macros = adapter.load_for(repo)
    v.literals = literal_macros(repo)
    raw = (repo / GLOSSARY).read_text(encoding="utf-8", errors="replace")

    for m in re.finditer(r"\\newacronym\{([^}]+)\}\{(.+?)\}\{(.+?)\}\s*$", raw, re.M):
        v.acronyms[m.group(1)] = (m.group(2), m.group(3))
    # nested \acrshort inside long forms (e.g. test -> "... of \acrshort{smeter} ...")
    for _ in range(3):
        for k, (short, long) in list(v.acronyms.items()):
            short = ACR_RE.sub(lambda mm: v.acronyms.get(mm.group(2), ("?", "?"))[0], short)
            long = ACR_RE.sub(lambda mm: v.acronyms.get(mm.group(2), ("?", "?"))[0], long)
            v.acronyms[k] = (short, long)

    for m in re.finditer(r"\\newglossaryentry\{([^}]+)\}\s*\{", raw):
        body, _ = _brace(raw, m.end() - 1)
        name = re.search(r"name=\{(.*?)\},\s*\n", body, re.S)
        desc = re.search(r"description=\{(.*?)\},\s*\n", body, re.S)
        unit = re.search(r"unit=\{(.*?)\},\s*\n", body, re.S)
        v.symbols[m.group(1)] = (
            _detex(name.group(1)) if name else m.group(1),
            desc.group(1).strip() if desc else "",
            _detex(unit.group(1)) if unit else "",
        )

    for rel in BIB_PATHS:
        p = repo / rel
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"@\w+\s*\{\s*([^,]+),(.*?)(?=\n@|\Z)", src, re.S):
            body = m.group(2)
            au = re.search(r"\n\s*author\s*=\s*[{\"](.+?)[}\"]\s*,", body, re.S)
            # biblatex entries carry `date = {2020-03}` rather than `year`; without the
            # fallback nearly every citation in the corpus renders as "n.d."
            yr = (re.search(r"\n\s*year\s*=\s*[{\"]?(\d{4})", body)
                  or re.search(r"\n\s*date\s*=\s*[{\"]?(\d{4})", body))
            name = "?"
            if au:
                names = au.group(1).split(" and ")
                first = names[0].strip()
                if first.startswith("{"):
                    # corporate author ({Zero Carbon Hub}): the braces are grouping,
                    # not part of the name - last-word extraction yields "Hub}"
                    name = re.sub(r"[{}]", "", first)
                else:
                    name = first.split(",")[0].strip() if "," in first else first.split()[-1]
                if len(names) > 1:
                    name += " et al."
            v.bib[m.group(1).strip()] = f"{name} {yr.group(1) if yr else 'n.d.'}"
    return v


# --- stage 1: inline subfiles (relative to the including file's directory) ---
def inline(repo: Path, rel: str, base: Path | None = None, seen: set[str] | None = None) -> str:
    seen = seen if seen is not None else set()
    base = base or repo
    cand = [base / rel, repo / rel]
    path = next((p if p.suffix else p.with_suffix(".tex")
                 for p in cand if (p if p.suffix else p.with_suffix(".tex")).exists()), None)
    if path is None:
        return f"\n[UNRESOLVED: subfile {rel} (from {base.relative_to(repo)})]\n"
    key = str(path.resolve())
    if key in seen:
        return ""
    seen.add(key)

    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = DOCCLASS_RE.sub("", raw)
    if "\\begin{document}" in raw:
        raw = raw.split("\\begin{document}", 1)[1].rsplit("\\end{document}", 1)[0]
    return INCLUDE_RE.sub(lambda m: inline(repo, m.group(1), path.parent, seen), raw)


# --- stage 3: \ExecuteMetaData[<file>]{<tag>} -------------------------------
def pull_tagged(repo: Path, text: str, unresolved: list[str]) -> str:
    def sub(m: re.Match) -> str:
        src, tag = m.group(1), m.group(2)
        if not src:
            unresolved.append(f"ExecuteMetaData without file for tag '{tag}'")
            return f"[UNRESOLVED: table {tag}]"
        p = repo / src
        if not p.exists():
            unresolved.append(f"table library {src}")
            return f"[UNRESOLVED: table library {src}]"
        body = p.read_text(encoding="utf-8", errors="replace")
        block = re.search(rf"%<\*{re.escape(tag)}>(.*?)%</{re.escape(tag)}>", body, re.S)
        if not block:
            unresolved.append(f"table tag '{tag}' not in {src}")
            return f"[UNRESOLVED: table tag {tag}]"
        return block.group(1)

    return EXECMETA_RE.sub(sub, text)


# --- stage 4: macro expansion ----------------------------------------------
def expand(s: str, v: Vocab, unresolved: list[str]) -> str:
    # The document's own macros go first: they expand into prose that may itself contain
    # acronyms and references, which the passes below then resolve.
    s = adapter.expand(v.macros, s, v, unresolved)

    # Zero-argument literals. `\nmodels{}` and `\nmodels` are the same macro; the empty
    # braces are LaTeX's way of protecting the following space, and dropping them here
    # is what stops "of \nmodels{} variants" collapsing to "of variants".
    for name, value in v.literals.items():
        s = re.sub(rf"\\{re.escape(name)}(?![A-Za-z])(?:\{{\}})?", value.replace("\\", r"\\"), s)

    def acr(m: re.Match) -> str:
        kind, key = m.group(1), m.group(2)
        if key not in v.acronyms:
            unresolved.append(f"acronym '{key}'")
            return f"[UNRESOLVED: acronym {key}]"
        short, long = v.acronyms[key]
        if kind in ("acrlong", "Acrlong"):
            return long[0].upper() + long[1:] if kind == "Acrlong" else long
        if kind == "acrfull":
            return f"{long} ({short})"
        return short

    s = ACR_RE.sub(acr, s)

    def gls(m: re.Match) -> str:
        key = m.group(2)
        if key in v.symbols:
            return v.symbols[key][0]
        if key in v.acronyms:
            return v.acronyms[key][0]
        unresolved.append(f"glossary key '{key}'")
        return f"[UNRESOLVED: gls {key}]"

    s = GLS_RE.sub(gls, s)

    s = CITE_RE.sub(
        lambda m: "(" + "; ".join(
            v.bib.get(k.strip(), f"[UNRESOLVED: cite {k.strip()}]") for k in m.group(1).split(",")
        ) + ")",
        s,
    )

    def ref(m: re.Match) -> str:
        lab = m.group(1)
        kind = REF_KIND.get(lab.split(":")[0].strip().lower(), "")
        return f"{kind} [{lab}]" if kind else f"[{lab}]"

    s = REF_RE.sub(ref, s)
    s = LABEL_RE.sub("", s)

    for env in EQ_ENVS:
        s = re.sub(rf"\\begin\{{{env}\*?\}}(.*?)\\end\{{{env}\*?\}}",
                   lambda m: f"\n\n$$\n{m.group(1).strip()}\n$$\n\n", s, flags=re.S)

    s = s.replace("\\physicalmodel", "deterministic model").replace("\\Physicalmodel", "Deterministic model")
    s = s.replace("\\ap{}", "'").replace("\\ap", "'")
    s = re.sub(r"\\(?:textbf|textit|emph|texttt|textsc)\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:begin|end)\{(?:itemize|enumerate|customenum|center|figure|table|tabular|tblr)\*?\}(?:\[[^\]]*\])?(?:\{[^}]*\})?", "", s)
    s = re.sub(r"\\item\s*", "\n- ", s)
    s = re.sub(r"\\(?:centering|toprule|midrule|bottomrule|small|normalsize|clearpage|newpage|noindent|hfill|width|printbibliography|makeglossaries|maketitle)\b", "", s)
    s = re.sub(r"\\(?:vspace|hspace)\{[^}]*\}", "", s)
    for a, b in [("\\%", "%"), ("\\&", "&"), ("\\#", "#"), ("\\_", "_"), ("\\$", "$"), ("~", " ")]:
        s = s.replace(a, b)
    return s


# --- stages 2+5: comments, floats, section numbering ------------------------
def flatten(repo: Path, mode: str = "public") -> Corpus:
    assert mode in ("public", "draft")
    v = load_vocab(repo)
    c = Corpus(text="", vocab=v)

    raw = inline(repo, ROOT)

    # stage 2 - comments carry provenance, TODOs and open supervisor questions.
    # A multi-line comment is one thought, not N. Consecutive comment lines are grouped so
    # a commented-out figure block or a provenance note arrives as a single annotation.
    notes: list[str] = []
    kept: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        body = " ".join(x.strip() for x in buf if x.strip()).strip()
        buf.clear()
        if body and not body.startswith(("<*", "</")) and set(body) != {"="}:
            notes.append(body)
            if mode == "draft":
                kept.append(f"\x00{len(notes) - 1}\x00")

    for line in raw.splitlines():
        m = COMMENT_RE.search(line)
        if line.lstrip().startswith("%"):
            buf.append(m.group(1) if m else "")
            continue
        flush()
        kept.append(COMMENT_RE.sub("", line))
    flush()
    raw = "\n".join(kept)

    # stage 3 - only NOW pull the tagged tables. \ExecuteMetaData must be resolved after
    # comment-splitting, not before: a commented-out reference is a table the author has
    # deliberately withheld (awaiting a notebook run, or still a [TODO] skeleton), and
    # resolving it first injects that placeholder into the corpus as a live table. The tag
    # delimiters live in the library file, which pull_tagged reads raw, so nothing is lost
    # by deferring.
    raw = pull_tagged(repo, raw, c.unresolved)

    raw = FIGURE_RE.sub(lambda m: _float_stub(m.group(1), "FIGURE"), raw)
    raw = TABLE_RE.sub(lambda m: _float_stub(m.group(1), "TABLE"), raw)

    # stage 5 - chapter numbers follow main.tex's subfile order
    counters = [0, 0, 0, 0]
    out: list[str] = []
    here = "front matter"
    i = 0
    while True:
        m = SECTION_RE.search(raw, i)
        chunk = raw[i : m.start()] if m else raw[i:]
        out.append(_emit(chunk, v, c, notes, here, mode))
        if not m:
            break
        title, after = _brace(raw, m.end() - 1)
        lvl = LEVELS[m.group(1)]
        counters[lvl] += 1
        for j in range(lvl + 1, 4):
            counters[j] = 0
        here = "\u00a7" + ".".join(str(x) for x in counters[: lvl + 1])
        for lm in LABEL_RE.finditer(raw[after : after + 300]):
            c.labels[lm.group(1)] = here
        out.append(f"\n\n{'#' * (lvl + 1)} {here} {expand(title, v, c.unresolved).strip()}\n\n")
        i = after

    c.text = re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()
    check_document_macros(c.text, repo, c.unresolved)
    c.unresolved += re.findall(r"\[UNRESOLVED: [^\]]+\]", c.text)
    return c


def _float_stub(block: str, kind: str) -> str:
    lab = LABEL_RE.search(block)
    cap = re.search(r"\\caption\{", block)
    caption = re.sub(r"\s+", " ", _brace(block, cap.end() - 1)[0]).strip() if cap else ""
    head = f"\n\n[{kind} {lab.group(1) if lab else '?'}] {caption}\n"
    if kind == "FIGURE":
        return head + "\n"
    return head + _rows(block) + "\n"


def _rows(block: str) -> str:
    """Tabular body -> pipe rows, so the numbers in a table survive into the corpus."""
    m = re.search(r"\\begin\{(?:tabular|tblr|tabularx)\}(?:\[[^\]]*\])?\s*\{", block)
    if not m:
        return ""
    # column spec may itself contain braces (@{}lccccc@{}), so match them properly
    _, start = _brace(block, m.end() - 1)
    end = block.find("\\end{tabular}", start)
    body = block[start : end if end != -1 else len(block)]
    out = []
    for line in body.split(r"\\"):
        line = re.sub(r"\\(?:toprule|midrule|bottomrule|hline|cmidrule(?:\[[^\]]*\])?(?:\{[^}]*\})?)", "", line)
        cells = [c.strip() for c in line.split("&")]
        if any(cells):
            out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _emit(chunk: str, v: Vocab, c: Corpus, notes: list[str], here: str, mode: str) -> str:
    def note(m: re.Match) -> str:
        body = notes[int(m.group(1))]
        c.annotations.append({"section": here, "note": body})
        return f"\n\n> [draft note] {body}\n\n"

    # Every \label in the chunk is recorded before expand() strips it - equation
    # labels in particular live mid-section, far from the 300-char post-heading
    # window flatten() records, and without this both publishers leave every
    # "Equation [eq:x]" reference dangling. setdefault: the heading pass has
    # already recorded section labels against the correct (new) section.
    for lm in LABEL_RE.finditer(chunk):
        c.labels.setdefault(lm.group(1), here)

    chunk = re.sub(r"\x00(\d+)\x00", note if mode == "draft" else "", chunk)
    return expand(chunk, v, c.unresolved)


#: `\newcommand{\name}{...}` or `\newcommand\name{...}`, zero arguments only - a `[n]`
#: between the name and the body means it takes parameters, which is adapter territory.
LITERAL_DEF_RE = re.compile(
    r"\\(?:newcommand|providecommand)\s*\*?\s*\{?\\([A-Za-z]+)\}?\s*(?!\[)\{")


def literal_macros(repo: Path) -> dict[str, str]:
    """Zero-argument macros whose body is plain text: `\\newcommand{\\nmodels}{40}`.

    These are a package convention, not a document quirk - any LaTeX document can define
    a count or a phrase once and use it throughout - so the engine expands them itself
    rather than making every document ship an adapter for them.

    Bodies containing commands (`\\width` -> `0.95\\textwidth`, `\\reddot` -> a tikz
    picture) are skipped: those are layout, they never carry meaning in prose, and
    substituting them would put raw LaTeX into the corpus in place of a clean marker.
    """
    path = repo / SETTINGS
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith("%"):
            continue
        body_line = re.split(r"(?<!\\)%", line)[0]
        m = LITERAL_DEF_RE.search(body_line)
        if not m:
            continue
        body, _ = _brace(body_line, m.end() - 1)
        if "\\" in body or "{" in body:
            continue
        out[m.group(1)] = body.strip()
    return out


def document_macros(repo: Path) -> set[str]:
    """Names the document defines with \\newcommand and friends, from its .sty preamble.

    Commented-out definitions are skipped, and internal `@` names are ignored: those are
    implementation details of other macros and never appear in prose.
    """
    path = repo / SETTINGS
    if not path.exists():
        return set()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith("%"):
            continue
        body = re.split(r"(?<!\\)%", line)[0]
        names |= {n for n in MACRODEF_RE.findall(body) if "@" not in n}
    return names


def check_document_macros(text: str, repo: Path, unresolved: list[str]) -> None:
    """Report document macros that reached the corpus unexpanded.

    Without this the failure is silent and the corpus reads plausibly: a count macro
    resolves to nothing, so "a family of \\nmodels{} variants" becomes "a family of
    variants" and the number is simply gone. A reader cannot tell that anything is
    missing, which is precisely the class of error the [UNRESOLVED] markers exist for.

    The engine handles package conventions itself; a document's own macros are the
    business of its `macros.py` adapter. Anything found here is either a macro that
    adapter should expand, or one it does not know about yet.
    """
    defined = document_macros(repo)
    if not defined:
        return
    prose = MATH_SPAN_RE.sub(" ", text)
    for name in sorted(defined):
        n = len(re.findall(rf"\\{re.escape(name)}(?![A-Za-z])", prose))
        if n:
            unresolved.append(f"document macro '\\{name}' ({n}x) - no adapter expands it")


def vocabulary_block(v: Vocab) -> str:
    """Prepended to the corpus so the model has every term defined exactly once."""
    lines = ["## Vocabulary\n", "### Abbreviations\n"]
    for k, (short, long) in sorted(v.acronyms.items(), key=lambda x: x[1][0]):
        lines.append(f"- **{short}** - {long}")
    lines.append("\n### Symbols\n")
    for k, (name, desc, unit) in sorted(v.symbols.items(), key=lambda x: x[1][0]):
        lines.append(f"- **{name}** - {desc}" + (f" [{unit}]" if unit else ""))
    lines += adapter.vocabulary(v.macros)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import os, sys
    repo = Path(os.environ.get("DOC_REPO", sys.argv[1] if len(sys.argv) > 1 else "."))
    c = flatten(repo, mode="draft")
    print(c.text[:4000])
    print(f"\n--- {len(c.text.split()):,} words | {len(c.labels)} labels | "
          f"{len(c.annotations)} draft notes | {len(set(c.unresolved))} unresolved")
