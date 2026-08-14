# Resume Tailoring Tool

A tested, full-stack tool for tailoring a resume against a job description:
paste a JD, get a ranked project shortlist, select projects, and generate a
LaTeX-compiled, page-limit-verified PDF.

## Design principle (read this first)

**This tool never generates new resume text.** Every bullet in
`data/project_bank.json` is pre-written, hand-verified content -- checked
against a source-of-truth project record, not produced by a script or an
LLM at request time. The tool's job is *selection, ordering, and
formatting* of already-approved content, not writing. This is a
deliberate boundary, not a limitation to be "fixed" later: a script that
generates new resume claims on the fly cannot verify those claims are
true, and this tool is built around the idea that an unverifiable claim
on a resume is worse than a smaller pool of verified ones.

**What this tool does NOT replace:** a human (or an LLM with access to
the real source document) still needs to (1) write and verify new bullets
when you build a new project, (2) apply judgment on borderline project
relevance -- the matcher is a literal keyword scanner, and a project with
zero keyword overlap is not necessarily irrelevant, just possibly phrased
differently than the JD -- and (3) sanity-check the final PDF before
sending it anywhere. This tool removes the mechanical, repetitive parts
of that process; it does not remove the need for judgment.

## Architecture

```
backend/
  app.py              Flask REST API (4 endpoints)
  matcher.py           JD-to-project keyword scoring
  resume_builder.py    Assembles resume content from the project bank
  pdf_compiler.py       Compiles LaTeX -> PDF with automatic page-fit
  latex_utils.py         LaTeX escaping / display-text conversion
  templates/
    resume.tex.j2        Jinja2 LaTeX template (LaTeX-safe delimiters)
data/
  project_bank.json    Pre-verified project content (the source of truth)
frontend/
  index.html / app.js / style.css   Single-page UI, no build step needed
tests/
  59 backend unit tests + 20 Flask API tests = 79 total, all passing
```

## Setup

Requires Python 3.9+ and a LaTeX distribution (`pdflatex`, `pdfinfo`).

```bash
# Install LaTeX if you don't have it:
#   Ubuntu/Debian: sudo apt install texlive-latex-base texlive-latex-extra poppler-utils
#   macOS: brew install --cask mactex-no-gui ; brew install poppler
#   Windows: install MiKTeX, and poppler via conda or a prebuilt binary

pip install -r requirements.txt

# Run tests (recommended before first use, to confirm your environment works):
python3 -m pytest tests/ -v

# Start the backend:
cd backend
python3 app.py
# Serves on http://localhost:5001

# In a separate terminal, serve the frontend:
cd frontend
python3 -m http.server 8080
# Open http://localhost:8080 in a browser
```

## API Reference

### `GET /api/health`
Liveness check. Returns `{"status": "ok"}`.

### `GET /api/projects`
Lists all projects in the bank (key, display title, domain tags).

### `POST /api/match`
Body: `{"jd_text": "..."}`
Returns ranked projects by keyword overlap, plus a list of JD terms with
no match anywhere in the bank (the real gap list).

**Known limitation, by design:** this is literal substring matching, not
semantic matching. A project can score zero and still be worth including
-- e.g. a strong general-purpose project whose JD simply doesn't use its
specific vocabulary. The `note` field in the response says this
explicitly; the UI does not auto-exclude zero-score projects, only
deprioritizes them in the initial checkbox state.

### `POST /api/generate`
Body: `{"selected_project_keys": [...], "max_pages": 2, "summary": "...", "personal_info": {...}}`
(`max_pages`, `summary`, `personal_info` are optional.)

Returns a base64-encoded PDF, the actual page count, which font size in
the size-ladder was needed to fit, and a `warning` field that is **always
checked** -- if the content couldn't fit within `max_pages` even at the
smallest allowed font, `warning` explains why and the PDF returned is
still the smallest-font attempt (so you have something to look at while
deciding what to trim).

## Adding a new project to the bank

Edit `data/project_bank.json`. Each entry needs:
- `title` (LaTeX-formatted, as it should appear on the resume)
- `github` (owner/repo, no `https://` prefix -- the template adds that)
- `domain` (list of lowercase strings, used for matching)
- `keywords` (list of lowercase strings, used for matching)
- `bullets` (list of pre-written, LaTeX-formatted strings -- these are
  used verbatim, so they must already be fact-checked before they go in)

Run `python3 -m pytest tests/test_resume_builder.py -v` after editing to
confirm the new entry doesn't break anything (malformed entries fail
loudly at load time, not silently).

## Why some design choices were made the way they were

**Why LaTeX with `\VAR{}` / `\BLOCK{}` instead of Jinja2's default `{{ }}`
delimiters?** LaTeX itself uses `{` and `}` constantly (`\textbf{...}`).
Mixing that with Jinja2's default brace-based syntax is a well-known
source of silent bugs. Non-brace delimiters avoid the collision entirely.

**Why does `pdf_compiler.py` try multiple font sizes instead of just
picking one?** Earlier iterations of this resume-editing process (see
project history) shipped at least one resume that silently compiled to
3 pages before a human caught it visually. Automating the "try
progressively smaller, verify, stop when it fits" loop -- and *guaranteeing*
a warning if nothing fits -- was the direct fix for that failure mode.

**Why is there a `latex_to_display_text()` function?** The project bank
stores LaTeX-formatted strings (for compilation). Early testing showed
those raw LaTeX escapes (e.g. `\&`) leaking directly into JSON API
responses meant for human/frontend display. This function converts
LaTeX-formatted text back to plain text for anywhere it's *read* rather
than *compiled*.

## Test suite

```bash
python3 -m pytest tests/ -v          # all 79 tests
python3 -m pytest tests/ --cov=backend --cov-report=term-missing   # with coverage
```

Notable regression tests (bugs actually caught during development, not
hypothetical edge cases):
- `test_underscore_in_github_field_does_not_break_compilation` -- a raw
  `_` in a GitHub repo name fatally broke LaTeX compilation (LaTeX reads
  `_` as a math-mode subscript operator outside math mode).
- `test_titles_are_clean_display_text_not_raw_latex` -- raw `\&` was
  leaking into JSON API responses before `latex_to_display_text()` existed.
- `test_backslash` / `test_no_double_escaping_backslash_then_special` --
  the LaTeX-escaping function's own backslash-escaping step was getting
  re-escaped by its own brace-handling step, a classic escaping-order bug.
- `test_all_projects_selected_either_fits_or_warns_clearly` -- the core
  safety guarantee: the tool must never silently return a resume that
  exceeds the page limit.

## Known limitations (stated plainly, not hidden)

1. Keyword matching is literal, not semantic -- documented above.
2. The project bank's bullet content must be manually updated when you
   complete a new project; there is no automatic ingestion from a source
   document (that step still requires the same careful fact-verification
   this whole project exists to protect).
3. `flask_cors` is configured wide-open (`CORS(app)`, all origins) --
   fine for local development, not appropriate if this is ever deployed
   somewhere reachable over a network. Tighten before doing that.
4. The Flask dev server (`app.run()`) is explicitly not production-grade
   (Flask's own startup warning says so). This is a local tool, not
   something to deploy as-is behind a public URL.
