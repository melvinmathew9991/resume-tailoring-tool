# Resume Tailoring Tool

Paste a job description, review a keyword match against a bank of pre-verified
project content, choose what goes on the page, and generate a
**page-limit-verified** PDF.

Python end to end: **FastAPI** backend, **Streamlit** frontend, LaTeX rendering
with a pluggable PDF engine.

---

## The design principle (read this first)

**This tool never generates resume text.** Every bullet in
`data/project_bank.json` and `data/profile.yaml` is pre-written and
hand-verified against a source-of-truth record. The tool's job is *selection,
ordering and formatting* of already-approved content.

That boundary is deliberate and it is not a limitation to be fixed later: a
program that writes new resume claims cannot verify those claims are true, and
an unverifiable claim on a resume is worse than a smaller pool of verified ones.

**What it does not replace.** A human still has to write and verify new bullets,
apply judgment on borderline relevance (the matcher is a keyword scanner, not a
reader), and look at the final PDF before sending it anywhere.

## The page-fit guarantee

The one safety property the whole tool is built around:

> For every generated resume, either `page_count <= max_pages`, or `warning`
> is non-empty. **Never neither.**

Generation compiles at successively smaller font sizes (9.6pt down to a 8.8pt
readability floor) until the document fits. If it never fits, you still get the
PDF — but `fits` is `false` and `warning` explains that content needs trimming,
not shrinking. This exists because an earlier hand-built resume silently
compiled to three pages and shipped before anyone noticed.

It is enforced by a Hypothesis property test over the whole input space, not
just by the code that implements it.

---

## Quick start

Requires Python 3.10+. **No LaTeX toolchain is needed to install, develop or
test** — only to produce a real PDF.

```bash
python tasks.py setup     # install the package and dev/ui extras
python tasks.py doctor    # report what is installed and what is missing
python tasks.py test      # the fast suite: no LaTeX required
python tasks.py dev       # run the API and UI together
```

Then open <http://localhost:8501> for the UI, or
<http://127.0.0.1:8000/docs> for the API.

`tasks.py` is a plain-stdlib script and works identically on Windows, macOS and
Linux. Run `python tasks.py` with no argument to see every task.

### Getting real PDFs

Without a PDF engine the tool still runs end to end, but generated documents
are **blank placeholders with an accurate page count** — useful for checking
length, useless for sending. The UI says so, permanently and prominently.

Install **Tectonic** — one self-contained binary, no TeX distribution:

```bash
winget install TectonicProject.Tectonic   # Windows
brew install tectonic                      # macOS
cargo install tectonic                     # anywhere with Rust
```

Or run the containers, which bake in TeX Live:

```bash
docker compose -f docker/docker-compose.yml up --build
```

---

## Architecture

```
src/resume_tailor/
  core/        config (env-driven, validated at startup), structured logging
               with PII redaction, the error hierarchy
  domain/      pure logic: models, LaTeX escaping/auditing, JD matching
  data/        project-bank and profile loading, cached and mtime-invalidated
  render/      Jinja LaTeX template, PDF engines, the page-fit ladder
  services/    orchestration -- the one thing the API and the UI both call
  api/         FastAPI app factory, middleware, versioned v1 routes
ui/            Streamlit frontend and its dual-mode backend client
data/          project_bank.json, profile.yaml, and a JSON Schema for the bank
tests/         unit · property · security · api · ui · integration
```

Every layer boundary is a validated pydantic model. Nothing crosses one as a
raw `dict` — which is what makes a whole class of "unexpected key crashes the
renderer" failures unrepresentable rather than merely guarded against.

### PDF engines

| Engine | When |
|---|---|
| `tectonic` | Default. One binary, no TeX distribution. |
| `pdflatex` | If you already have TeX Live or MiKTeX. Used in the API container. |
| `fake` | In-process. Emits real, valid, multi-page PDFs whose page count responds to font size, so the entire pipeline is testable with no external binary. |

Selected with `RT_PDF_ENGINE` (`auto` probes in order). `auto` refuses to fall
back to `fake` when `RT_ENVIRONMENT=prod`, so a blank placeholder can never be
mistaken for a real resume in a deployment. Page counts are read in-process
with `pypdf` — no `pdfinfo`, no `poppler-utils`.

---

## API

Interactive docs at `/docs`. Errors are RFC 7807 `application/problem+json`
with a stable machine-readable `code`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health/live` | Process is up. |
| GET | `/health/ready` | Bank parses, profile parses, an engine is present. 503 if not. |
| GET | `/api/v1/meta` | Defaults, limits, font ladder, engine status. |
| GET | `/api/v1/projects` | List projects. `?include_hidden=true` to inspect hidden ones. |
| GET | `/api/v1/projects/{key}` | One project, with bullets as display text. |
| POST | `/api/v1/match` | Rank projects against a JD; report gap terms. |
| POST | `/api/v1/resume/preview` | Render LaTeX **without compiling**. Works with no engine installed. |
| POST | `/api/v1/resume/generate` | Compile, with the page-fit guarantee. |
| GET | `/api/v1/resume/{id}` | Stream the PDF. |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/resume/generate \
  -H 'Content-Type: application/json' \
  -d '{"selected_project_keys":["credit_default","aml_fraud"],"max_pages":2}'
```

**Known limitation, by design.** Matching is literal keyword overlap, not
semantic — most ATS scanning is literal too, so this mirrors the system it is
meant to help pass. A project can score zero and still be your strongest one;
the response says so in its `note` field, and the UI deprioritises zero-score
projects rather than hiding them.

---

## Editing your content

**Projects** live in `data/project_bank.json`. Point your editor at
`data/project_bank.schema.json` for completion and inline validation.

```json
{
  "my_project": {
    "title": "Project Title -- LaTeX formatted, used verbatim",
    "github": "owner/repo",
    "domain": ["fintech"],
    "keywords": ["python", "xgboost"],
    "bullets": ["Pre-written, fact-checked, used verbatim."],
    "hidden": false
  }
}
```

**Everything else** — header, summary, experience, skills, education — lives in
`data/profile.yaml`. It used to be hardcoded in Python; changing a phone number
should not be a code edit.

Both files are re-read automatically when they change on disk. Both are
validated on load: a malformed entry fails loudly and specifically, and a
duplicated JSON key is an error rather than a silently-dropped project.

Set `"hidden": true` on anything unverified. Hidden projects are excluded from
listings, from matching, **and from generation** — they cannot reach a resume
by any path.

---

## Testing

```bash
python tasks.py test       # fast suite, no LaTeX toolchain needed
python tasks.py test-all   # everything, including real compiles
python tasks.py cov        # with the 90% coverage gate
python tasks.py check      # lint + types + coverage (what CI runs)
```

| Layer | What it covers |
|---|---|
| `unit` | Pure functions: escaping, matching, models, the page-fit ladder. |
| `property` | Hypothesis invariants — escaping round-trips exactly and always produces safe output; the page-fit guarantee holds for every input. |
| `security` | A corpus of LaTeX injection, macro-redefinition, expansion-bomb and encoding payloads, driven through the real service and HTTP entry points. |
| `api` | Full app through `TestClient`: validation, limits, rate limiting, CORS, auth, and an OpenAPI contract snapshot. |
| `ui` | The real Streamlit script through `AppTest`, including the backend-unreachable path. |
| `integration` | Real LaTeX compilation. Marked `latex`; skipped automatically when no engine is installed. |

The fast suite is the default because the machine this was built on has no TeX
installed, and a suite that cannot run protects nothing.

---

## Configuration

Every setting is an environment variable with an `RT_` prefix; see
`.env.example` for the full list with defaults. Configuration is validated at
startup, so a bad value fails immediately with a clear message.

The ones worth knowing:

| Variable | Default | Notes |
|---|---|---|
| `RT_PDF_ENGINE` | `auto` | `auto` · `tectonic` · `pdflatex` · `fake` |
| `RT_UI_MODE` | `http` | `http` (two processes) or `embedded` (single process) |
| `RT_CORS_ORIGINS` | `http://localhost:8501` | An allowlist. Never `*`. |
| `RT_API_KEY` | unset | When set, `/api` requires an `X-API-Key` header. |
| `RT_MAX_CONCURRENT_COMPILES` | `2` | Caps simultaneous TeX processes. |
| `RT_ENVIRONMENT` | `local` | `prod` enforces a stricter posture. |

## Further reading

- [`docs/PLAN.md`](docs/PLAN.md) — the audit of the previous implementation and
  the rebuild plan, including the full defect list and the edge-case catalogue.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — why the layers are shaped
  this way.
- [`docs/SECURITY.md`](docs/SECURITY.md) — threat model and controls.
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — operating and troubleshooting.
