"""thesis-agent: turn a LaTeX document into a queryable corpus, a Notion wiki and a site.

Three stages, each usable on its own:

    ingest   LaTeX -> corpus_public.md / corpus_draft.md + label and figure maps
    publish  corpus -> Notion wiki (sync) or Quarto site (build_site), one page plan
    app      corpus -> a cached-context chat server that answers with section citations

Two corpora, because LaTeX source comments are not noise: the public build strips them,
the draft build keeps them as annotations. Anything that leaves the machine is built from
the public corpus; the draft build never is.

Every path comes from `thesis_agent.config`, so the entry points run from any directory.
"""

__version__ = "0.1.0"
