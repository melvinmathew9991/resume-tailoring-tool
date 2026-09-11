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

Generation compiles at a fixed 9.2pt. If the content does not fit, you still get
the PDF — but `fits` is `false` and `warning` explains that content needs
trimming, not shrinking. This exists because an earlier hand-built resume
silently compiled to three pages and shipped before anyone noticed.

Shrinking to fit was the original behaviour and was deliberately removed: two
resumes generated a week apart came out at different sizes, and hiding a
three-page resume at 8.8pt solved the symptom rather than the cause. Adding
rungs back to `font_ladder` restores it with no code change.

It is enforced by a Hypothesis property test over the whole input space, not
just by the code that implements it.

## The text-extraction check

An ATS does not read your PDF. It reads whatever its parser can pull out of
your PDF, and those are not the same thing.

So every generated resume is opened again, its text extracted with `pypdf`, and
compared against what the spec says is on the page. The result rides along on
the generate response as `parse_check`:

| Field | Meaning |
|---|---|
| `status` | `pass`, `warn` or `fail`. |
| `term_coverage` | Share of the page's words that survived extraction. |
| `missing_terms` | The words that did not. |
| `links` | Link targets that are actually *clickable*, not merely printed. |
| `findings` | One entry per cause, each naming its own remedy. |

The causes are reported separately, because they have different fixes:

- **`ligatures`** — `classification` is typeset with an `fi` ligature and
  extracts as `classi<fi>cation`. On the page it is perfect; to a keyword scan
  it is a different word. Fixable in the template.
- **`split_words`** — a wide kerning pair (`F r`, `T o`) reads as a word
  boundary, so `Frameworks` extracts as `F rameworks`. A property of the
  extractor more than the document.
- **`split_words` and `ligatures` are warnings, never failures.** The resume is
  still worth sending; it simply matches a posting less well than its content
  deserves.
- **`no_text` / `little_text`** — the document is valid, the page count is
  right, and a parser reads nothing. This one is a failure.
- **`links_not_clickable`** — `hyperref` did not produce an annotation.

Running this against the real resume with Tectonic is what found the ligature
problem in the first place: 14 words including `classification`, `verification`
and `MLflow` were on the page and invisible to a literal keyword match.

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
carry the right words and an accurate page count but are **not typeset** —
useful for checking length and machine-readability, useless for sending. The UI
says so, permanently and prominently.

Install **Tectonic** — one self-contained binary, no TeX distribution:

```bash
brew install tectonic     # macOS
cargo install tectonic    # anywhere with Rust
```

On Windows, Tectonic is **not** published to winget or Chocolatey — download
the `x86_64-pc-windows-msvc` zip from
[the releases page](https://github.com/tectonic-typesetting/tectonic/releases),
extract `tectonic.exe`, and put its directory on `PATH`:

```powershell
$dir = "$env:LOCALAPPDATA\Programs\tectonic"
[Environment]::SetEnvironmentVariable(
  "Path", [Environment]::GetEnvironmentVariable("Path","User") + ";$dir", "User")
```

Open a new terminal afterwards, then confirm with `python tasks.py doctor`.

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

📐 **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) is the full reference** —
layer diagram and dependency rule, a module-by-module map, sequence diagrams
for the match and generate flows, the page-fit ladder, the text-safety
pipeline, the error-code table, and the developer workflow.

### PDF engines

| Engine | When |
|---|---|
| `tectonic` | Default. One binary, no TeX distribution. |
| `pdflatex` | If you already have TeX Live or MiKTeX. Used in the API container. |
| `fake` | In-process. Emits real, valid, multi-page PDFs that carry the document's text and links and whose page count responds to font size, so the entire pipeline — page fit *and* text extraction — is testable with no external binary. It does no typesetting, so only a real engine can prove the layout. |

Selected with `RT_PDF_ENGINE` (`auto` probes in order). `auto` refuses to fall
back to `fake` when `RT_ENVIRONMENT=prod`, so an untypeset placeholder can never
be mistaken for a real resume in a deployment. Page counts are read in-process
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
| POST | `/api/v1/resume/ats` | Score the assembled resume against a JD. Compiles nothing. |
| POST | `/api/v1/resume/generate` | Compile, with the page-fit guarantee and the text-extraction check. |
| GET | `/api/v1/resume/{id}` | Stream the PDF. |
| GET | `/api/v1/knowledge` | Everything the system knows about the candidate. |
| POST | `/api/v1/knowledge` | Add knowledge from pasted text or an uploaded document. |
| DELETE | `/api/v1/knowledge/entries` | Remove one entry by `category` and `value`. |
| DELETE | `/api/v1/knowledge` | Empty the knowledge store. |
| POST | `/api/v1/ats/check` | Score a JD against stored knowledge. Generates nothing. |

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

## Profile knowledge

Two things the tool does that have nothing to do with generating a resume. Pick
them from the **Feature** switch in the sidebar.

### Updating what the system knows about you

`data/knowledge.json` holds the candidate's own skills, tools, domains,
education, certifications and dated experience. Add to it from the **Profile
knowledge** view, or over the API, from:

- a **PDF**, **DOCX**, **TXT** or **MD** upload, or
- **pasted text**.

Legacy `.doc` is refused with an explanation rather than half-read — it is an
OLE binary and reading it would mean a new dependency. Save it as `.docx` or
`.pdf`, or paste the text.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/knowledge \
  -H 'Content-Type: application/json' \
  -d '{"text":"Technical Skills\nPython, SQL, Airflow","mode":"merge"}'
```

**Checking it worked.** Entry counts alone will not tell you -- a document
whose prose gets read as a skills list produces thousands of entries and looks
thorough. So the store is linted, the same way `project_bank.json` already is:

```bash
python tasks.py knowledge     # counts, sources, and content warnings
```

The same warnings appear at the top of the Profile knowledge view, and in the
`warnings` field of `GET /api/v1/knowledge`. A clean store says so explicitly.
The `version` field is a content hash: if it did not change, nothing was
written.

Three rules make this safe to run repeatedly:

- **`mode=merge` is the default and never removes anything.** Re-uploading a
  document you have already added changes nothing and says so. `mode=replace`
  is the only lossy option and has to be asked for by name.
- **Nothing is inferred.** Extraction is rule-based; every entry stores the
  verbatim line it came from, and the UI shows it. There is no model here that
  could decide you are "experienced in" something you once listed.
- **It cannot reach your resume.** This store is separate from
  `data/profile.yaml` and `data/project_bank.json`, which stay hand-edited.
  An uploaded document can never put unverified prose onto a generated PDF.

Documents are sent as base64 in the JSON body rather than as a multipart
upload, which is what keeps `python-multipart` out of the dependency list. The
Streamlit UI does the encoding for you (and skips it entirely in embedded
mode).

### Checking a job description — the ATS match score

The **ATS match check** view scores a pasted JD against that knowledge and
tells you what is missing. It does not generate, preview or modify a resume.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ats/check \
  -H 'Content-Type: application/json' \
  -d '{"jd_text":"Senior Data Scientist. 4+ years with Python, SQL, Snowflake."}'
```

You get a percentage, a band, and a breakdown across six factors — skills,
tools, experience, responsibilities/domain, education/certifications and other
keywords — with every requirement marked **exact**, **related** or **missing**,
and every match reporting which source it came from.

The scoring is arithmetic and published with the result, so you can recompute
it by hand:

- fixed category weights (skills 0.30, tools 0.20, experience 0.15,
  responsibilities 0.15, education 0.10, keywords 0.10), returned in every
  response;
- exact = 1.0, related = 0.5, missing = 0.0;
- categories the posting never mentions are dropped and their weight is shared
  out, so a JD that says nothing about education does not score you a zero for
  it;
- **each requirement counts once.** Repeating a word twenty times in a job
  description cannot raise your score. Occurrence counts are reported and are
  not an input.

The candidate side is your knowledge store *plus* `profile.yaml` and
`project_bank.json`, so the check is useful before you have uploaded anything.

### Two scores, two questions

The **Tailor resume** view shows its own ATS score, and it is deliberately a
different number:

| | Scores | Answers | Changes when |
|---|---|---|---|
| **ATS match check** view | everything known about you | "should I apply?" | you add profile knowledge |
| **Tailor resume**, step 4 | the resume you are assembling | "will this PDF pass the screen?" | you change the project selection |

The resume score counts only what is *printed*: your profile plus the bullets
that survive the bullet budget. A project's `keywords` and `domain` tags in the
bank are excluded — they drive matching, they never reach the page, and
counting them would flatter a resume for words no screen can see. So a project
tagged `nlp` that never says "NLP" in a bullet will not move this score, which
is the honest answer.

It is computed without compiling, so it updates as you tick projects on and
off. The gap it reports is split in two: requirements missing from the page
that you *do* cover elsewhere (fix by choosing a different project) and
requirements missing everywhere (a real gap).
Same caveat as `/match`: this is literal and alias-aware term matching, not
comprehension. A missing requirement means the term is absent from your stored
knowledge, which is not the same as being absent from your experience.

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

The fast suite is the default because it must stay runnable on a machine with
no TeX installed — a suite that cannot run protects nothing.

**Run `test-all` at least once on any machine you develop on.** The first time
the `latex` suite was executed against a real engine, seven of its nine tests
failed on a defect that had made the default PDF engine unusable on Windows.
The fast suite could not have caught it: it runs on the fake engine, which
never starts a subprocess. Skipped tests are not passing tests.

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
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — architecture and workflow:
  how the system is built, how a request moves through it, and how to work on
  it.
- [`docs/SECURITY.md`](docs/SECURITY.md) — threat model and controls.
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — operating and troubleshooting.
