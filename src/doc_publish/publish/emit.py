"""Corpus markdown -> Notion block fragments.

Parses the format ingest/flatten.py emits (verified against build/corpus_public.md):
  `## §N.N Title`      headings, 1-4 #'s, top-level number from main.tex subfile order
  `[FIGURE <label>] c` figure stub, caption on the same line; labels contain spaces/colons
  `[TABLE <label>] c`  table stub, followed immediately by `| pipe | rows |` - a row's
                       cells can contain newlines (LaTeX source line breaks), so a line
                       not starting with `|` continues the previous row
  `$$ ... $$`          display equations, raw LaTeX
  `- item`             bullets
  `% ...`              leaked LaTeX comments: comments inside \\ExecuteMetaData table
                       libraries are pulled in *after* comment-stripping, so a handful
                       survive even in the public corpus. They are drafting state and
                       must not reach the wiki - excluded here and listed in the sync
                       report, never dropped silently.

Cross-references and citations are left as inline markers in fragment text; linker.py
resolves them once page IDs exist. This module is deliberately offline - no Notion.

KaTeX pass: Notion equation blocks render KaTeX, a LaTeX subset. Trivially-fixable
constructs are rewritten (align->aligned, gather->gathered, \\label/\\nonumber stripped,
bare % escaped - % starts a comment in KaTeX and silently eats the rest of the line).
Anything likely to fail is emitted as a code block instead and listed with its section
in katex_report.md in the state dir, mirroring ingest's [UNRESOLVED] philosophy: a broken
grey equation with no explanation is a silent lie about what the thesis says.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADING_RE = re.compile(r"^(#{1,4}) \u00a7([\d.]+) (.*)$")
FIGURE_RE = re.compile(r"^\[FIGURE ([^\]]+)\]\s*(.*)$")
TABLE_RE = re.compile(r"^\[TABLE ([^\]]+)\]\s*(.*)$")
MULTICOL_RE = re.compile(r"\\multicolumn\{(\d+)\}\{[^{}]*(?:\{[^{}]*\})?[^{}]*\}\{")

# Commands seen in the corpus plus the common KaTeX-supported set. An equation using
# only these is assumed to render; anything else is flagged for the report.
KATEX_OK = {
    "frac", "sqrt", "sum", "int", "prod", "lim", "ln", "log", "exp", "sin", "cos", "tan",
    "max", "min", "operatorname", "mathrm", "mathbf", "mathit", "mathcal", "mathbb",
    "text", "textrm", "left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr",
    "Bigl", "Bigr", "quad", "qquad", "hat", "bar", "tilde", "vec", "dot", "ddot",
    "overline", "underline", "prime", "star", "dagger", "circ", "cdot", "cdots", "dots",
    "ldots", "times", "div", "pm", "mp", "le", "ge", "leq", "geq", "ne", "neq", "equiv",
    "approx", "sim", "simeq", "propto", "mid", "parallel", "perp", "in", "notin",
    "subset", "supset", "cup", "cap", "infty", "partial", "nabla", "forall", "exists",
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta",
    "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau",
    "upsilon", "phi", "varphi", "chi", "psi", "omega", "Gamma", "Delta", "Theta",
    "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega", "begin", "end",
    "rightarrow", "leftarrow", "Rightarrow", "Leftarrow", "to", "mapsto", "implies",
    "langle", "rangle", "lvert", "rvert", "lVert", "rVert", "vert", "Vert", "%", ",",
    ";", "!", " ", "\\", "{", "}", "_", "&", "#", "|",
    "lfloor", "rfloor", "lceil", "rceil", "ell", "hbar", "arg", "deg", "det", "dim",
    "gcd", "inf", "sup", "ker", "Pr", "liminf", "limsup", "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "coth", "sec", "csc", "cot", "binom", "dbinom", "tfrac",
    "dfrac", "cfrac", "overset", "underset", "substack", "mathsf", "mathtt",
    "boldsymbol", "overbrace", "underbrace", "widehat", "widetilde", "overrightarrow",
    "xrightarrow", "xleftarrow", "vdots", "ddots", "therefore", "because", "setminus",
    "oplus", "otimes", "odot", "bigcup", "bigcap", "oint", "coprod", "neg", "land",
    "lor", "iff", "leftrightarrow", "Leftrightarrow", "longrightarrow", "uparrow",
    "downarrow", "hookrightarrow", "succ", "prec", "succeq", "preceq", "ll", "gg",
    "asymp", "doteq", "cong", "models", "vdash", "top", "bot", "wedge", "vee",
    "bullet", "diamond", "ast", "colon", "displaystyle", "textstyle", "limits",
    "nolimits", "mathop", "phantom", "hphantom", "vphantom", "boxed", "tag",
}
KATEX_ENVS = {
    "aligned", "alignedat", "cases", "rcases", "dcases", "matrix", "pmatrix", "bmatrix",
    "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix", "array", "gathered", "split",
    "subarray", "darray",
}
ENV_REWRITE = {"align": "aligned", "align*": "aligned", "gather": "gathered",
               "gather*": "gathered", "equation": None, "equation*": None,
               "multline": "gathered", "multline*": "gathered"}
EQUATION_CHAR_LIMIT = 1000  # Notion caps equation expressions at 1000 chars


@dataclass
class Fragment:
    kind: str                 # paragraph | bullet | heading | equation | figure | table | comment_leak
    text: str = ""            # paragraph/bullet/heading text, or equation expression
    level: int = 0            # heading depth (1-3, relative to page)
    label: str = ""           # figure/table label
    caption: str = ""
    rows: list = field(default_factory=list)   # table rows, list[list[str]]
    issues: list = field(default_factory=list) # katex issues for this fragment
    as_code: bool = False     # equation demoted to a code block
    raw: str = ""             # equation expression exactly as parsed (math="mathjax")


@dataclass
class Section:
    number: str               # "2.2.6" (no §); "vocabulary" for the front block
    level: int                # 1 = top-level section
    title: str
    fragments: list = field(default_factory=list)
    children: list = field(default_factory=list)

    @property
    def display(self) -> str:
        return f"\u00a7{self.number} {self.title}" if self.number[0].isdigit() else self.title

    def own_words(self) -> int:
        return sum(len(f.text.split()) + len(f.caption.split()) for f in self.fragments)

    def walk(self):
        yield self
        for ch in self.children:
            yield from ch.walk()


# --- KaTeX compatibility ----------------------------------------------------
def katex_fix(expr: str) -> tuple[str, list[str], list[str]]:
    """Returns (rewritten expression, fixes applied, remaining issues)."""
    fixes, issues = [], []

    def env_sub(m: re.Match) -> str:
        env = m.group(2)
        if env in ENV_REWRITE:
            new = ENV_REWRITE[env]
            fixes.append(f"{env} -> {new or 'unwrapped'}")
            return f"\\{m.group(1)}{{{new}}}" if new else ""
        return m.group(0)

    expr = re.sub(r"\\(begin|end)\{([a-zA-Z*]+)\}", env_sub, expr)

    for pat, name in ((r"\\label\{[^}]*\}", "\\label"), (r"\\nonumber", "\\nonumber"),
                      (r"\\notag", "\\notag")):
        if re.search(pat, expr):
            fixes.append(f"stripped {name}")
            expr = re.sub(pat, "", expr)

    if re.search(r"(?<!\\)%", expr):
        fixes.append("escaped bare % (KaTeX comment char)")
        expr = re.sub(r"(?<!\\)%", r"\\%", expr)

    for m in re.finditer(r"\\begin\{([a-zA-Z*]+)\}", expr):
        if m.group(1) not in KATEX_ENVS:
            issues.append(f"unsupported environment {{{m.group(1)}}}")
    for m in re.finditer(r"\\([a-zA-Z]+)", expr):
        if m.group(1) not in KATEX_OK and m.group(1) not in KATEX_ENVS:
            issues.append(f"unknown command \\{m.group(1)}")
    if "[UNRESOLVED" in expr:
        issues.append("contains an [UNRESOLVED] marker from ingest")
    if len(expr) > EQUATION_CHAR_LIMIT:
        issues.append(f"{len(expr)} chars exceeds Notion's {EQUATION_CHAR_LIMIT}-char equation limit")
    return expr.strip(), fixes, sorted(set(issues))


# --- text cleanup -----------------------------------------------------------
def clean_text(s: str) -> str:
    """Residual LaTeX-isms that survive flatten: empty groups ('model{}s'), brace-wrapped
    words ('({BEIS} n.d.)'), TeX dashes and quotes. Inline `$...$` spans are left
    untouched - braces inside math are structure, not residue."""
    out = []
    for part in re.split(r"(\$[^$\n]+\$)", s):
        if part.startswith("$") and part.endswith("$") and len(part) > 2:
            out.append(part)
            continue
        # \texorpdfstring{tex}{pdf}: keep the TeX form; must run before generic
        # brace-stripping or it degrades to literal '\texorpdfstring(...)'
        part = re.sub(r"\\texorpdfstring\{([^{}]*)\}\{[^{}]*\}", r"\1", part)
        part = part.replace("{}", "")
        part = re.sub(r"\{([^{}]*)\}", r"\1", part)
        part = part.replace("---", "\u2014").replace("--", "\u2013")
        part = part.replace("``", "\u201c").replace("''", "\u201d")
        out.append(part)
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()


_STYLE_CMD_RE = re.compile(r"\\(?:emph|textbf|textit|texttt|textsc)\{")


def _unwrap_style(s: str) -> str:
    """Remove \\emph{...}-style wrappers with proper brace matching - the argument can
    contain nested braces ($HTC_{\\text{test}}$), which a flat regex cannot cross."""
    while True:
        m = _STYLE_CMD_RE.search(s)
        if not m:
            return s
        depth, j = 1, m.end()
        while j < len(s) and depth:
            depth += {"{": 1, "}": -1}.get(s[j], 0)
            j += 1
        s = s[: m.start()] + s[m.end() : j - 1] + s[j:]


def _clean_cell(s: str) -> str:
    s = _unwrap_style(s)
    s = re.sub(r"\\(?:addlinespace|midrule|toprule|bottomrule)(?:\[[^\]]*\])?", "", s)
    return clean_text(re.sub(r"\s+", " ", s))


# --- parsing ----------------------------------------------------------------
def parse_corpus(text: str, *, math: str = "katex") -> tuple[Section, list[Fragment]]:
    """Returns (root section tree, leaked comment fragments).

    The corpus header (build metadata up to `## Vocabulary`) is dropped: it documents
    the corpus for an LLM, not the thesis for a reader. The Vocabulary block becomes
    its own section so the wiki keeps the one-stop definition of every term.

    `math` selects the equation treatment for the *display* equations parsed here:
      "katex"   (default) - byte-identical to the historical behaviour: expressions are
                rewritten for Notion's KaTeX subset and anything outside the whitelist
                is demoted to a code block (`as_code`).
      "mathjax" - for emitters with a permissive renderer (Quarto/MathJax): the
                expression passes through untouched (also kept in `raw`), nothing is
                demoted, and only genuinely universal problems are recorded as issues
                (an [UNRESOLVED] marker left by ingest).
    Inline `$...$` maths is untouched by either mode - it stays inside fragment text,
    and only the Notion-specific rich_text() applies KaTeX rules to it.
    """
    assert math in ("katex", "mathjax"), math
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("## Vocabulary")), 0)

    root = Section(number="root", level=0, title="Document")
    stack = [root]
    leaks: list[Fragment] = []
    vocab = Section(number="vocabulary", level=1, title="Vocabulary")

    i = start
    current: Section | None = None
    body: list[str] = []

    def close() -> None:
        if current is not None:
            current.fragments = _parse_body(body, leaks, math)
        body.clear()

    while i < len(lines):
        line = lines[i]
        m = HEADING_RE.match(line)
        if line.startswith("## Vocabulary"):
            close()
            current = vocab
            root.children.append(vocab)
            stack = [root, vocab]
        elif line.startswith("### ") and current is vocab:
            body.append("@@H2@@" + line[4:])
        elif m:
            close()
            level = len(m.group(1))
            sec = Section(number=m.group(2).rstrip("."), level=level,
                          title=clean_text(m.group(3)))
            while stack and stack[-1].level >= level:
                stack.pop()
            stack[-1].children.append(sec)
            stack.append(sec)
            current = sec
        else:
            body.append(line)
        i += 1
    close()
    return root, leaks


def _parse_body(lines: list[str], leaks: list[Fragment], math: str = "katex") -> list[Fragment]:
    frags: list[Fragment] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            txt = clean_text(" ".join(para))
            if txt:
                frags.append(Fragment("paragraph", text=txt))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped or set(stripped) == {"-"}:
            flush()
        elif stripped.startswith("%"):
            flush()
            leaks.append(Fragment("comment_leak", text=stripped.lstrip("% ").strip()))
        elif stripped == "$$":
            flush()
            expr_lines = []
            i += 1
            while i < len(lines) and lines[i].strip() != "$$":
                expr_lines.append(lines[i])
                i += 1
            raw = "\n".join(expr_lines).strip()
            if math == "mathjax":
                issues = (["contains an [UNRESOLVED] marker from ingest"]
                          if "[UNRESOLVED" in raw else [])
                frags.append(Fragment("equation", text=raw, raw=raw, issues=issues))
            else:
                expr, fixes, issues = katex_fix(raw)
                frags.append(Fragment("equation", text=expr, issues=fixes + issues,
                                      as_code=bool(issues)))
        elif stripped.startswith("@@H2@@"):
            flush()
            frags.append(Fragment("heading", text=stripped[6:], level=2))
        elif FIGURE_RE.match(stripped):
            flush()
            m = FIGURE_RE.match(stripped)
            frags.append(Fragment("figure", label=m.group(1).strip(),
                                  caption=clean_text(m.group(2))))
        elif TABLE_RE.match(stripped):
            flush()
            m = TABLE_RE.match(stripped)
            rows, i = _parse_table(lines, i + 1)
            frags.append(Fragment("table", label=m.group(1).strip(),
                                  caption=clean_text(m.group(2)), rows=rows))
            continue
        elif stripped.startswith("- "):
            flush()
            frags.append(Fragment("bullet", text=clean_text(stripped[2:])))
        elif stripped.startswith("> "):
            # draft notes never reach the public corpus; treat any quote as a leak
            flush()
            leaks.append(Fragment("comment_leak", text=stripped[2:]))
        else:
            para.append(stripped)
        i += 1
    flush()
    return frags


def _parse_table(lines: list[str], i: int) -> tuple[list[list[str]], int]:
    """Rows run until the first blank line. A line not starting with `|` is a cell
    continuation (flatten splits rows on \\\\, and cells keep their internal newlines)."""
    raw_rows: list[str] = []
    while i < len(lines) and lines[i].strip():
        line = lines[i].strip()
        if line.startswith("|"):
            raw_rows.append(line)
        elif raw_rows:
            raw_rows[-1] += " " + line
        else:
            break
        i += 1

    rows: list[list[str]] = []
    for raw in raw_rows:
        raw = raw.strip().strip("|")
        mc = MULTICOL_RE.search(raw)
        if mc:
            content = raw[mc.end():]
            depth, j = 1, 0
            while j < len(content) and depth:
                depth += {"{": 1, "}": -1}.get(content[j], 0)
                j += 1
            rows.append(["@span@" + _clean_cell(content[: j - 1])])
            continue
        cells = [_clean_cell(c) for c in raw.split("|")]
        if any(cells):
            rows.append(cells)

    width = max((len(r) for r in rows if r and not r[0].startswith("@span@")), default=1)
    fixed = []
    for r in rows:
        if r and r[0].startswith("@span@"):
            r = [r[0][6:]] + [""] * (width - 1)
        else:
            r = (r + [""] * width)[:width]
        if any(c for c in r):
            fixed.append(r)
    return fixed, i


# --- rich text --------------------------------------------------------------
RICH_LIMIT = 2000

# a bracket span is a cross-reference only if the linker recognises the label;
# `[TODO]`, `[W K^-1]` and friends stay literal text. The kind word owns its
# trailing space - a bare `\s?` before the bracket would swallow the space in
# "described in [label]" and render "in§4.3.2".
XREF_RE = re.compile(r"(?:(Figure|Table|Equation|Chapter|Section)\s)?\[([^\[\]\n]+)\]")
CITE_RE = re.compile(r"\(((?:[^()]+? (?:n\.d\.|\d{4})(?:; )?)+)\)")
INLINE_MATH_RE = re.compile(r"\$([^$\n]+)\$")


def rich_text(text: str, resolve=None, katex_log=None) -> list[dict]:
    """Text -> Notion rich_text array. `resolve(kind, label)` (from linker) returns a
    link descriptor or None; without it, markers render as plain text.

    Order matters: inline math is carved out first so a `$...$` span can never be
    mistaken for a citation or reference.
    """
    runs: list[dict] = []

    def emit_text(s: str, link: str | None = None, italic: bool = False) -> None:
        for j in range(0, len(s), RICH_LIMIT):
            chunk = s[j : j + RICH_LIMIT]
            if not chunk:
                continue
            run: dict = {"type": "text", "text": {"content": chunk}}
            if link:
                run["text"]["link"] = {"url": link}
            if italic:
                run["annotations"] = {"italic": True}
            runs.append(run)

    def emit_plainish(s: str) -> None:
        # citations, then cross-references, inside a math-free span. Each author-year
        # component links separately, so "(A 2020; B 2021)" is two links.
        pos = 0
        for m in CITE_RE.finditer(s):
            emit_refs(s[pos : m.start()])
            emit_text("(")
            for j, comp in enumerate(m.group(1).split("; ")):
                if j:
                    emit_text("; ")
                target = resolve("citation", comp.strip()) if resolve else None
                emit_text(comp, link=target)
            emit_text(")")
            pos = m.end()
        emit_refs(s[pos:])

    def emit_refs(s: str) -> None:
        pos = 0
        for m in XREF_RE.finditer(s):
            kind, label = (m.group(1) or "").strip(), m.group(2).strip()
            target = resolve("ref", label) if resolve else None
            if target is None:
                continue  # not a known label - leave the whole span as literal text
            emit_text(s[pos : m.start()])
            display, url = target
            if kind:
                # "Figure [Fig: x]" must not render "Figure Fig: x"
                display = re.sub(r"^(?:Fig|Tab)\s*:\s*", "", display, flags=re.I)
                emit_text(f"{kind} ")
            emit_text(display, link=url)
            pos = m.end()
        emit_text(s[pos:])

    pos = 0
    for m in INLINE_MATH_RE.finditer(text):
        emit_plainish(text[pos : m.start()])
        expr, fixes, issues = katex_fix(m.group(1))
        if issues:
            if katex_log is not None:
                katex_log.append((expr, fixes + issues))
            runs.append({"type": "text", "text": {"content": f"${m.group(1)}$"},
                         "annotations": {"code": True}})
        else:
            if fixes and katex_log is not None:
                katex_log.append((expr, fixes))
            runs.append({"type": "equation", "equation": {"expression": expr[:RICH_LIMIT]}})
        pos = m.end()
    emit_plainish(text[pos:])
    return [r for r in runs if r.get("type") != "text" or r["text"]["content"]]
