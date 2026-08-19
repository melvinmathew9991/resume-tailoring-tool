# Resume Tailoring Tool — Production Rebuild Plan

**Target stack:** Python end-to-end. FastAPI backend, Streamlit frontend, pytest test estate.
**Status:** planning document. Written after a full read of the existing codebase (20 files, ~1,700 LOC).
**Plan revision:** v2 (v1 drafted, critiqued, and revised — see §7 for what changed and why).

---

## 1. Audit of the current codebase

### 1.1 What exists today

| Layer | Files | Verdict |
|---|---|---|
| API | `backend/app.py` — Flask, 4 endpoints, CORS wide open | Replace with FastAPI |
| Matching | `backend/matcher.py` — literal substring scoring | Keep the idea, fix the algorithm |
| Assembly | `backend/resume_builder.py` — Jinja2 → LaTeX, static data hardcoded in Python | Split: data out of code, add validation |
| Compilation | `backend/pdf_compiler.py` — `pdflatex` subprocess + font-size ladder | Keep the *design*, harden the *execution* |
| Escaping | `backend/latex_utils.py` — LaTeX escaping / display-text conversion | Genuinely good; keep, extend, prove with property tests |
| Frontend | `frontend/index.html` + `app.js` + `style.css` | Delete, replace with Streamlit |
| Data | `data/project_bank.json` — 14 projects, 48 bullets | Keep format, add JSON Schema + strict loader |
| Tests | 4 files, ~79 tests | Keep the good regression tests, restructure and multiply |

**What the existing code gets right** (this must survive the rewrite — it is the actual value of the project):

1. **The no-generation boundary.** The tool selects and formats pre-verified bullets; it never writes new resume claims. That is a correctness guarantee, not a limitation.
2. **The page-fit guarantee.** Try progressively smaller fonts, verify the real page count, and *always* warn if nothing fits. This was a fix for a real failure (a 3-page resume that shipped silently).
3. **The escaping-order fix.** `_BACKSLASH_PLACEHOLDER` using non-printable control chars is a subtle, correct solution to a real double-escaping bug.
4. **Regression tests tied to real bugs**, not hypothetical ones.

### 1.2 Defects found — verified, not speculative

Each of these was confirmed by reading the code and, where marked ✅, by executing it.

**Critical — security**

| # | Defect | Evidence |
|---|---|---|
| C1 | **LaTeX injection.** `summary` and `personal_info` arrive from the request body and are interpolated into the template with `autoescape=False` and no escaping (`resume_builder.py:141-171`). A caller can submit `\input{/etc/passwd}` to read server files into the PDF, or `\write18{...}` (blocked by default, but `-shell-escape` is one flag away), or an expansion bomb (`\def\x{\x\x}`) to hang the compiler. | `app.py:122-123` → `build_resume(summary=…)` → `template.render(summary=summary)` |
| C2 | **No request size limits.** `jd_text` and `summary` are unbounded. A 100 MB paste is accepted and processed. | `app.py:79`, `app.py:122` |
| C3 | **CORS `*` on an endpoint that shells out to a subprocess.** Any web page in the user's browser can drive `pdflatex` on their machine. | `app.py:33` |
| C4 | **No concurrency limit on compilation.** N parallel requests spawn N `pdflatex` processes. Trivially exhausts CPU/RAM. | `pdf_compiler.py:135-142` |

**Critical — correctness**

| # | Defect | Evidence |
|---|---|---|
| B1 | **Reserved-key crash.** `template.render(**info, summary=…, font_size=…)` raises `TypeError: got multiple values for keyword argument` if `personal_info` contains any of `summary`, `experience`, `projects`, `skills`, `education`, `font_size`, `line_spacing`. Returns 500. | ✅ reproduced |
| B2 | **Unhashable selection element → 500.** `selected_project_keys: [["a"]]` passes the `isinstance(list)` check, then `k not in bank` raises `TypeError: unhashable type`. | ✅ reproduced |
| B3 | **`transformer_scratch` is hidden from `/api/projects` and `/api/match`, but `/api/generate` will happily put it on the resume.** The exclusion is a hardcoded string in the API layer, not a property of the data. | `app.py:68` and `app.py:84` vs. `resume_builder.py:144` |
| B4 | **Substring matching produces false positives.** The bank contains the keyword `r`. `normalize()` strips punctuation, so `r` is a substring of virtually every JD. ✅ Confirmed: a JD about *"R&D, Rust, Ruby"* scores `nifty_arima` above every genuinely relevant project. Same class of bug for `ai`, `cte`, `psi`, `mlp`. | ✅ reproduced |
| B5 | **`bool` passes the `max_pages` integer check.** `isinstance(True, int)` is `True`, so `max_pages: true` silently becomes 1 page. No upper bound either — `max_pages: 999999999` is accepted. | `app.py:119` |
| B6 | **`personal_info` is never type-checked.** A string value produces `{**PERSONAL_INFO, **"str"}` → `TypeError` → 500. | `app.py:123`, `resume_builder.py:142` |
| B7 | **Duplicate keys are not deduplicated.** `["medbot", "medbot"]` renders the project twice. |`resume_builder.py:149` |
| B8 | **`validate_no_unescaped_specials()` is dead code.** A well-written safety net that is never called from anywhere in the pipeline. | grep: zero call sites outside tests |
| B9 | **`_get_page_count` ignores the exit code.** If `pdfinfo` fails, the parse loop falls through to a `RuntimeError` with a confusing message, surfacing as an opaque 500. | `pdf_compiler.py:98-108` |
| B10 | **`subprocess.TimeoutExpired` is uncaught.** A 60-second hang becomes an unhandled 500 rather than a clean 504/422. | `pdf_compiler.py:76-82` |
| B11 | **`_BANK_CACHE` is a mutable global with no lock and no invalidation.** Editing `project_bank.json` requires a server restart; the README's "just edit the JSON" workflow silently serves stale data. | `app.py:35-42` |
| B12 | **Unescaped `github` field inside `\href{}`.** A `%`, `#`, or `}` in a repo path breaks compilation or escapes the argument. Only the *display* copy is escaped. | `resume_builder.py:153` |

**Structural / operational**

| # | Defect |
|---|---|
| S1 | `sys.path.insert(0, ...)` in two places instead of an installable package. Imports work only from specific working directories. |
| S2 | Resume content (experience, skills, education, personal info) is **hardcoded in `resume_builder.py`**. Changing a phone number requires editing Python. |
| S3 | No dependency pinning (`>=` only), no lockfile, no `pyproject.toml`. |
| S4 | No Dockerfile, no CI, no linting, no type checking, no config management, no structured logging. |
| S5 | `run.sh` is bash-only. **The developer's machine is Windows.** |
| S6 | **Nothing is installed and nothing runs on this machine right now.** ✅ Verified: no `jinja2`, no `flask`, no `pytest`, and critically **no `pdflatex` and no `pdfinfo`**. The current project is 100% non-functional on the primary dev box. This is the single most important constraint on the plan. |
| S7 | Base64-encoding the PDF into a JSON body inflates it ~33% and forces full buffering on both ends. |
| S8 | The Flask dev server is explicitly not production-grade (acknowledged in the README, never fixed). |

### 1.3 The constraint that shapes everything

> **There is no LaTeX toolchain on the development machine, and the tests that matter most (the page-fit guarantee) currently require one.**

A plan that ignores this produces a project that cannot be developed or tested locally. §3.3 addresses it directly with a pluggable PDF engine, and §5 splits the test suite so the majority runs with no external binaries at all.

---

## 2. Goals and non-goals

**Goals**
- G1 — FastAPI backend, Streamlit frontend, Python only, no JavaScript.
- G2 — Every defect in §1.2 fixed and locked behind a regression test.
- G3 — Runs on Windows, macOS, Linux, and in Docker with one command each.
- G4 — Test suite that runs fully green on a machine with **no LaTeX installed**.
- G5 — Production posture: config, structured logs, health/readiness, error contract, rate limits, CI.
- G6 — Resume *content* fully data-driven; no personal data hardcoded in Python.

**Non-goals** (stated so scope does not drift)
- Multi-user accounts, auth, or a database. Single-user tool; optional API key only.
- Semantic/embedding matching or an LLM writing bullets. The no-generation boundary is preserved deliberately.
- A hosted public deployment. The artifact is deployable, but shipping it is out of scope.

---

## 3. Target architecture

### 3.1 Layout

```
resume-tailoring-tool/
├── pyproject.toml                  # deps, ruff, mypy, pytest, coverage — single source of truth
├── requirements.lock               # pip-compile output, hash-pinned
├── tasks.py                        # cross-platform task runner (replaces run.sh)
├── docker/{Dockerfile.api,Dockerfile.ui,docker-compose.yml}
├── .github/workflows/ci.yml
├── src/resume_tailor/
│   ├── core/
│   │   ├── config.py               # pydantic-settings; env-driven; validated at startup
│   │   ├── logging.py              # structlog JSON, request_id, PII redaction
│   │   └── errors.py               # AppError hierarchy → RFC 7807 problem+json
│   ├── domain/
│   │   ├── models.py               # Project, ProjectBank, PersonalInfo, ResumeSpec (pydantic v2)
│   │   ├── matching.py             # token-boundary scoring + alias map + explainability
│   │   └── latex.py                # escape / unescape / injection guard / rendered-source audit
│   ├── data/
│   │   ├── bank_repo.py            # load, validate, cache with mtime invalidation, version hash
│   │   └── profile_repo.py         # experience/skills/education/personal info — from YAML, not code
│   ├── render/
│   │   ├── template_env.py         # LaTeX-safe Jinja env (\VAR{} / \BLOCK{})
│   │   ├── renderer.py             # ResumeSpec → .tex, with pre-flight source audit
│   │   ├── engines/
│   │   │   ├── base.py             # PdfEngine protocol: compile(tex) -> (pdf_bytes, log)
│   │   │   ├── pdflatex.py         # subprocess, hardened (see §3.3)
│   │   │   ├── tectonic.py         # self-contained TeX, zero system install
│   │   │   └── fake.py             # deterministic in-memory engine for tests
│   │   └── pagefit.py              # font ladder + page count via pypdf (no pdfinfo dependency)
│   ├── services/resume_service.py  # orchestration; the only thing the API and UI both call
│   └── api/
│       ├── main.py                 # app factory, lifespan, middleware stack
│       ├── deps.py
│       └── v1/{health,projects,match,resume,meta}.py
├── ui/
│   ├── app.py                      # Streamlit entrypoint
│   ├── client.py                   # HTTP client (httpx) OR in-process adapter — one interface
│   ├── state.py                    # session_state schema, typed
│   └── components/                 # jd_input, project_picker, gap_report, result_panel
├── data/{project_bank.json,project_bank.schema.json,profile.yaml}
└── tests/{unit,integration,security,property,api,ui,e2e,fixtures}/
```

**Why `src/` layout:** it makes the package installable (`pip install -e .`), which deletes every `sys.path.insert` hack (S1) and makes imports identical in tests, the API, the UI, and CI.

### 3.2 Request flow

```
Streamlit UI ──httpx──► FastAPI ──► resume_service ──► bank_repo   (validated, cached, versioned)
     │                     │                      ├──► matching    (pure, no I/O)
     │                     │                      ├──► renderer    (ResumeSpec → .tex + audit)
     │                     │                      └──► pagefit ──► PdfEngine (pdflatex|tectonic|fake)
     └──────────────── PDF bytes streamed back ◄───────────────────────────┘
```

Every layer boundary is a pydantic model. Nothing crosses a boundary as a raw `dict`, which is what made B1/B2/B6 possible.

### 3.3 The PDF engine abstraction — the load-bearing decision

```python
class PdfEngine(Protocol):
    name: str

    def available(self) -> EngineStatus: ...  # feeds /health/ready
    def compile(self, tex: str, *, timeout_s: float) -> CompiledPdf: ...
```

Three implementations:

- **`PdflatexEngine`** — production path. Hardened: `-no-shell-escape` forced, isolated temp `TEXMFHOME`, hard `timeout`, output size cap, `TimeoutExpired`/`CalledProcessError` mapped to typed errors (fixes B10), non-zero exit inspected before parsing (fixes B9).
- **`TectonicEngine`** — a single self-contained binary that downloads its own packages. **This is what makes the project installable on the Windows dev box without a 4 GB MiKTeX install.**
- **`FakeEngine`** — returns a real, valid, minimal PDF whose page count is a deterministic function of the input length. This lets the *entire* page-fit ladder, warning logic, and API contract be tested with **zero external binaries** — directly solving §1.3.

Page counting moves from `subprocess pdfinfo` to **`pypdf`** (a pure-Python dependency), removing the `poppler-utils` requirement entirely and fixing B9's whole failure class.

Engine selection is config-driven (`RT_PDF_ENGINE=auto|pdflatex|tectonic|fake`); `auto` probes in order and reports the choice on `/health/ready`.

### 3.4 API surface (v1)

| Method | Path | Notes |
|---|---|---|
| GET | `/health/live` | Process is up. Never touches disk. |
| GET | `/health/ready` | Bank loaded + engine available + template compiles. Returns which engine won. |
| GET | `/api/v1/meta` | Defaults: summary, personal info, font ladder, bank version, engine name. |
| GET | `/api/v1/projects` | `?include_hidden=false`. `hidden` is now **a field on the project**, not a hardcoded string (fixes B3). |
| GET | `/api/v1/projects/{key}` | 404 with problem+json on unknown key. |
| POST | `/api/v1/match` | Ranked projects + gap terms + per-project score explanation. |
| POST | `/api/v1/resume/preview` | Renders `.tex` and runs the source audit **without compiling**. Fast feedback, and it works with no engine installed. |
| POST | `/api/v1/resume/generate` | Compiles. Returns metadata + `document_id`. |
| GET | `/api/v1/resume/{document_id}` | Streams `application/pdf` (fixes S7 — no base64). TTL-bounded in-memory store. |

Errors are **RFC 7807 `application/problem+json`** with a stable machine-readable `type`, so the Streamlit UI branches on a code rather than on English prose.

### 3.5 Security model

| Threat | Control |
|---|---|
| LaTeX injection (C1) | All free text escaped through `domain/latex.py`. `ResumeSpec` is a pydantic model with `extra="forbid"` and **a fixed template namespace** — user fields are rendered from a single `ctx` object, structurally eliminating B1. Rendered source is audited for `\input`, `\write`, `\openout`, `\catcode`, `\def` before it reaches the engine, and rejected. |
| Shell escape | `-no-shell-escape` always; never configurable. |
| Resource exhaustion (C2/C4) | Body size cap (middleware), field-level `max_length` in pydantic, `asyncio.Semaphore` around compiles, per-compile timeout, global rate limit. |
| CSRF-ish drive-by (C3) | CORS allowlist from config, defaults to `http://localhost:8501` only. Optional `X-API-Key`. |
| Blocking the event loop | Compilation runs in `anyio.to_thread` — a sync `def` FastAPI endpoint doing a 20 s subprocess call would stall every other request. |
| PII in logs | `structlog` processor redacts email/phone before emit. |

---

## 4. Edge-case catalogue

This is the "no loopholes" deliverable. Every row becomes at least one test. **Bold** rows are confirmed live defects from §1.2.

**A. JD text input**
empty · whitespace-only · single character · 1 MB paste (must 413, not hang) · null bytes · lone UTF-16 surrogates · CJK / Arabic (RTL) / emoji · HTML and `<script>` · a 50,000-character single token with no spaces · only stopwords · the resume itself pasted as the JD · Windows `\r\n` vs `\n` · `Node.js` (the sentence-splitter splits on `.`) · **a JD containing the letter `r` (B4)**

**B. Project selection**
`[]` (must still compile a valid static-only resume) · unknown key · **duplicate keys (B7)** · **non-string element (B2)** · **nested list element (B2)** · `null` element · 500 keys · **hidden project requested explicitly (B3)** · wrong-case key · order preservation · every key in the bank at once

**C. `max_pages`**
`0` · `-1` · `1` · `2` · `1000000` · **`true` (B5)** · `2.0` · `"2"` · `null` · omitted

**D. Summary and personal info**
**`\input{/etc/passwd}` (C1)** · **`\write18{...}` (C1)** · `\def\x{\x\x}` expansion bomb · `\catcode` reassignment · unbalanced `{` · a bare `%` (comments out the rest of the line) · 100 KB summary · non-string type · **`personal_info={"summary": ...}` and `{"font_size": ...}` (B1)** · **`personal_info` as a string (B6)** · unknown keys · empty string vs. `None` (different meanings: "blank" vs. "use default") · unicode name · email containing `%`, `_`, `#`

**E. Project bank data**
file missing · malformed JSON · UTF-8 BOM · duplicate JSON keys · empty object `{}` · empty `bullets` · `bullets` not a list · non-string bullet · title with unbalanced braces · **`github` containing `%`, `#`, `}`, or a full `https://` prefix (B12)** · 200 bullets · unicode content · **file edited while the server is running (B11)**

**F. Compilation**
engine binary missing · engine times out · exits 0 but produces no PDF · produces a 0-page PDF · LaTeX error on ladder rung 3 of 5 · every rung fails · empty font ladder (currently would `AttributeError` on `None.warning`) · read-only temp dir · disk full · 20 concurrent compiles · client disconnects mid-compile

**G. Page-fit guarantee** *(the core invariant)*
fits on rung 1 · fits only on the last rung · never fits → **`warning` must be non-empty and a PDF must still be returned** · `max_pages` far exceeds actual · property test: *for every input, `page_count <= max_pages` OR `warning != ""` — never neither*

**H. HTTP layer**
wrong `Content-Type` · malformed JSON body · body over the size cap · missing required field · unexpected extra field · wrong HTTP method · unknown route · CORS preflight from a disallowed origin · gzip bomb · concurrent identical requests · rate limit tripped

**I. Streamlit UI**
backend down at startup · backend dies mid-session · backend returns 500 / 422 / 413 · zero match results · double-click on Generate (must not fire twice) · rerun preserving session state · browser refresh · 500-project list rendering · download button before generation · very long gap-term list

**J. Operations**
invalid env var at startup (must fail loudly, not boot degraded) · bank missing at startup · `/health/ready` red while `/health/live` green · SIGTERM during a compile · log output is valid JSON · no email/phone in logs

---

## 5. Test strategy

### 5.1 Layout and markers

```
tests/
├── unit/         pure functions — latex, matching, models, pagefit logic   (no I/O, milliseconds)
├── property/     hypothesis invariants on escaping and page-fit
├── security/     the injection corpus from §4-D
├── api/          FastAPI TestClient against the full app with FakeEngine
├── integration/  real PDF engine  [marker: latex]
├── ui/           streamlit.testing.v1.AppTest with a mocked client
└── e2e/          UI → live API → real PDF  [markers: latex, slow]
```

Markers: `unit`, `property`, `security`, `api`, `ui`, `latex`, `slow`.

**`pytest -m "not latex and not slow"` is the default local command and must be fully green on the current Windows box with nothing but `pip install -e .[dev]`.** This is the direct answer to §1.3 and G4.

### 5.2 Techniques

- **`FakeEngine`** — deterministic page counts make the ladder, the warning path, and every API contract testable without TeX.
- **Hypothesis property tests** — the highest-value tool for the escaping module:
  - `latex_to_display_text(escape_latex(s)) == s` for all `s` (round-trip).
  - `escape_latex(s)` never contains an unescaped special (audit invariant).
  - `escape_latex` is idempotent under the audit.
  - The page-fit invariant from §4-G, over generated project selections.
- **Golden-file tests** — the rendered `.tex` for a fixed input is snapshotted. Any template change that alters output has to be reviewed, not discovered in a PDF.
- **Injection corpus** — a checked-in list of ~30 hostile payloads, each asserted to be either escaped or rejected. Adding a payload is a one-line change.
- **Contract test** — the OpenAPI schema is snapshotted; an unintended breaking change fails CI.
- **Coverage gates** — ≥ 90% on `src/`, **100% on `domain/latex.py` and `domain/matching.py`** (the two modules where a bug is silent and lands on a real resume).
- **Mutation testing** (`mutmut`, on `domain/` only, nightly) — coverage proves lines ran; mutation proves the assertions actually constrain behaviour.

### 5.3 CI matrix

| Job | Python | Engine | Runs |
|---|---|---|---|
| fast | 3.10 / 3.11 / 3.12 | fake | every push |
| lint | 3.12 | — | ruff + mypy strict on `src/` |
| latex | 3.12 | tectonic | every push (tectonic installs in seconds) |
| full | 3.12 | texlive | nightly + release tags |
| mutation | 3.12 | fake | nightly, `domain/` only |

---

## 6. Execution phases

Each phase ends green: tests pass, lint passes, and the thing is runnable.

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **P0 Foundation** | `pyproject.toml`, `src/` package, config, logging, errors, `tasks.py`, CI skeleton | `pip install -e .[dev]` works; `pytest` collects; ruff+mypy clean |
| **P1 Domain** | `models.py`, `latex.py`, `matching.py`, `bank_repo.py`, `profile_repo.py`, `profile.yaml`, JSON Schema | Fixes B4, B7, B11, B12, S2. 100% coverage + property tests on latex & matching |
| **P2 Render** | `template_env`, `renderer`, engines (pdflatex/tectonic/fake), `pagefit` | Fixes B8, B9, B10, C1's audit half, F-class edge cases. Full ladder tested via FakeEngine |
| **P3 API** | FastAPI v1, schemas, middleware, problem+json, OpenAPI snapshot | Fixes B1, B2, B3, B5, B6, C2, C3, C4, S7. All of §4-A/B/C/D/H under test |
| **P4 UI** | Streamlit app, client (HTTP + in-process), components, session state | All of §4-I under `AppTest`. Old `frontend/` deleted |
| **P5 Hardening** | Security corpus, edge-case sweep, coverage/mutation gates | Every §4 row has a test. Gates enforced in CI |
| **P6 Ops** | Dockerfiles, compose, README rewrite, `docs/` (ARCHITECTURE, SECURITY, RUNBOOK, ADRs) | `docker compose up` serves both; one-command start on Windows |

**Sequencing rule:** P1 and P2 are pure-Python and testable with no LaTeX, so they land first and de-risk the rest. The API is built on top of an already-proven core rather than the reverse.

---

## 7. Plan iteration — what v1 got wrong

The first draft of this plan was reviewed against the audit findings. Nine changes were made:

1. **v1 assumed LaTeX was available.** It planned a straight Flask→FastAPI port with `pdflatex` as the only engine. Given §1.3 (no TeX on the dev machine), that plan produces a codebase the author cannot run or test. → **v2 adds the `PdfEngine` abstraction with `tectonic` and `fake` implementations, and makes "green test suite with no LaTeX installed" an explicit goal (G4).** This is the most important change in the revision.

2. **v1 kept `pdfinfo`.** A second external binary for a job `pypdf` does in-process. → **v2 drops the `poppler-utils` dependency entirely.**

3. **v1 kept base64-in-JSON** because it was the existing behaviour. → **v2 splits generate/download and streams the PDF**, cutting payload size ~33% and unblocking large documents.

4. **v1 treated B1 (the kwarg collision) as an input-validation fix** — blocklist the reserved keys. A blocklist rots the moment a template variable is added. → **v2 changes the template contract so user data renders from a single namespaced `ctx` object, making the collision structurally impossible.** Fix the shape, not the symptom.

5. **v1 had a full repository/service/unit-of-work stack.** For a single-user tool with one JSON file, that is ceremony. → **v2 keeps exactly two data modules and one service module**, and each is justified by a specific defect it fixes (B11 caching, S2 hardcoded content, orchestration shared by API and UI).

6. **v1 had the Streamlit UI talk only over HTTP.** That forces two processes for what is often a single-user local session. → **v2 defines one `client` interface with an HTTP and an in-process implementation**, so `streamlit run ui/app.py` works standalone while `docker compose` runs the real two-service topology. Same UI code, one env var.

7. **v1's tests all required a real compile** (as today's do). CI would be slow and the dev box could not run them. → **v2 introduces `FakeEngine` and the marker split**, so the fast suite is seconds and the `latex` suite is opt-in.

8. **v1 had no bank versioning.** Without it, caching is unsafe and results are not reproducible. → **v2 adds a content hash (`bank_version`) surfaced in every response**, which also gives the UI a way to detect stale state.

9. **v1 said "add tests for edge cases" without enumerating them.** That is how loopholes survive. → **v2 replaces it with §4, a concrete 100+ row catalogue derived from the audit**, where each row maps to a test file. "Don't miss any loophole" is only achievable if the loopholes are written down.

**Deliberately *not* changed in v2:** the no-generation boundary, the font-size ladder design, the `\VAR{}`/`\BLOCK{}` delimiter choice, the control-character backslash placeholder, and the "warn loudly rather than silently overflow" guarantee. Those are the parts of the original design that were already right.

---

## 8. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| D1 | Default PDF engine on the dev box | **Tectonic** — one binary, no 4 GB install, works on Windows today |
| D2 | UI↔backend coupling | **Dual-mode client**, HTTP default in Docker, in-process for local single-user |
| D3 | Repo strategy | **Rewrite in place** into `src/`, delete `backend/` and `frontend/` at P4; `git init` first so every phase is a reviewable commit |

All three were confirmed and implemented as recommended.

---

## 9. Outcome

All seven phases are complete. This section records what was actually built and,
more importantly, where reality differed from the plan — a plan document that
only records the parts that went as expected is not worth keeping.

### Delivered

| Phase | State |
|---|---|
| P0 Foundation | `pyproject.toml`, `src/` package, config, structured logging with PII redaction, error hierarchy, `tasks.py` |
| P1 Domain | models, `latex.py`, `matching.py`, bank/profile repositories, `profile.yaml`, JSON Schema |
| P2 Render | template, renderer, four engines, page-fit ladder on `pypdf` |
| P3 API | FastAPI v1, nine routes, problem+json, middleware stack |
| P4 UI | Streamlit app, dual-mode client; `backend/` and `frontend/` deleted |
| P5 Tests | 692 tests, 95% coverage, gates enforced |
| P6 Ops | two Dockerfiles, compose, CI matrix, README, ARCHITECTURE, SECURITY, RUNBOOK |

Verified locally: `ruff check` clean, `ruff format --check` clean, `mypy --strict`
clean across 41 files.

As first written, that verification covered 692 tests with the nine
`latex`-marked ones skipped for want of a toolchain. Tectonic has since been
installed, and then a full end-to-end audit (19 Aug 2026, §10) found fifteen
defects sitting behind the green suite and fixed them. The current numbers are **824
tests and 95.0% coverage across both `resume_tailor` and `ui`**, nine of them
compiling actual PDFs with the real engine.

That audit is worth recording as a result in its own right, for what it says
about the suite that preceded it. 705 passing tests, a clean `mypy --strict`,
and a 97% coverage number did not surface a live LaTeX-injection hole in the
resume header, a body-size limiter that did the opposite of what its own
docstring claimed, or a mutation-testing configuration that four consecutive
nightly CI runs had already been failing on. Coverage measures which lines ran.
None of these three defects was an unrun line.


### Corrections to the audit in §1.2

**B3 was latent, not active.** The plan says `/api/generate` would put the
hidden `transformer_scratch` project on a resume. The code path was real, but
**no project by that key exists in `project_bank.json`** — the exclusion in the
old API layer referenced a key that had already been removed from the data, and
the test asserting its absence passed vacuously. The defect was a live bug
waiting for the next hidden project rather than one currently reachable. The
fix is unchanged and is now covered by a test with a project that actually
exists.

### Defects found *during* the rebuild, not in the original audit

Both were caught by tests written against the new code, which is the argument
for writing them:

1. **Alias double-counting.** The new matcher credited a keyword and its alias
   separately when both matched the same characters, so a JD saying
   "machine-learning" scored that project twice for one mention. Fixed by
   counting distinct match spans rather than summing pattern hits.
2. **Leading-slash `github` values silently rewritten.** The normaliser stripped
   slashes from both ends, so the malformed value `/repo` became a valid profile
   link for a user named "repo" — a wrong URL on a resume rather than a reported
   error. Now only a trailing slash is absorbed.

### Defects found after the rebuild, on first contact with a real engine

Tectonic was installed on the development machine for the first time on
2026-08-15. Until that moment **no test in this repository had ever compiled a
real document on this machine**, and the nine `latex`-marked tests had only ever
been skipped. On their first real run, **seven of the nine failed.**

3. **The subprocess sandbox made Tectonic unusable.** `SubprocessEngine`
   redirected `HOME` and `USERPROFILE` into the per-compile scratch directory.
   Tectonic resolves its downloaded-package cache through exactly those
   platform directories, so it exited 1 with `Unable to find standard
   directories for platform` before typesetting a single page. Every real
   compile failed, on the engine the README names as the default. Bisecting the
   environment showed `USERPROFILE` alone was sufficient to cause it, and that
   setting `TECTONIC_CACHE_DIR` did not rescue it.

   The fix is a per-engine `sandbox_home` flag rather than dropping the
   redirect globally: `pdflatex` genuinely reads user TeX configuration and
   keeps the full sandbox, while Tectonic — which does not read TeX user
   configuration at all and is sandboxed by `--untrusted` — declines it. The
   containment that was actually doing the work (`TEXMF*` redirection,
   `openin_any`/`openout_any`, no shell escape) is unchanged in both cases, and
   there is now a test asserting exactly that.

   Worth noting *why* it survived: the fast suite uses `FakeEngine`, which
   never builds a subprocess environment, and `render/engines/tectonic.py` was
   in the coverage `omit` list. The defect sat in the seam between the two
   things the test estate deliberately did not look at.

   The `omit` list has since been emptied, and the interesting part is what
   that revealed: **both engine modules were already at 100% coverage** from
   the fast suite. The exclusion was not protecting an untestable file, it was
   hiding a fully-tested one — and in doing so it removed the signal that would
   have shown `_subprocess_env` had no assertions behind it. An `omit` entry
   added for a plausible reason ("you cannot run this without the binary")
   outlived that reason and became a blind spot. Total coverage was unchanged
   at 97.3% after removing it.

4. **The documented Windows install command did not exist.** The README,
   `tasks.py doctor`, the UI's no-engine warning and the engine's own
   `missing_binary_hint` all said
   `winget install TectonicProject.Tectonic`. There is no such winget package —
   Tectonic is not published to winget or Chocolatey. Anyone following the
   README on Windows would have hit `No package found matching input criteria`
   and had no path forward. All four sites now point at the release binary.

### The finding behind both of the above

**No CI workflow in this repository has ever run.** `.github/workflows/ci.yml`
defines six jobs, including one that installs Tectonic and compiles for real on
every push. `git remote -v` is empty: there is no remote, nothing has ever been
pushed, and therefore not one of those jobs has executed a single time.

Statements elsewhere in this document of the form "skipped locally and run in
CI" were describing intent, not observed behaviour. Anything a plan defers to
CI is unverified until CI has actually run once — the deferral is only as good
as the pipeline behind it, and an unrun pipeline provides nothing. Adding a
remote and pushing is the highest-value open item on the project.

### Deviations from the plan

- **`ui` is an installed package.** The plan did not anticipate that Streamlit
  executes its entrypoint with only the script's own directory on `sys.path`,
  so `from ui import components` failed. Rather than reintroduce the
  `sys.path` hack the rewrite exists to remove, `pyproject.toml` now declares
  two package roots.
- **The service is built in `create_app`, not in the lifespan handler.** A
  lifespan-only setup works under uvicorn but silently does not run when a
  `TestClient` is used without a context manager, turning every route into a
  confusing `AttributeError`. Construction is free (no I/O until first request),
  so the trap was removed rather than documented.
- **The module-level `app` is lazy.** Importing `api.main` used to build an
  application and probe the filesystem for a TeX binary, emitting a misleading
  "no PDF engine found" warning during tests that never use it.
- **`"2"` is accepted for `max_pages`; `true` is not.** A numeric string has
  exactly one sensible reading, so coercing it is helpful. A boolean does not,
  which is the whole substance of defect B5.

### Not done, and why

- **Mutation testing** (§5.2) is configured (`[tool.mutmut]`, scoped to
  `domain/`) and wired as a nightly Linux CI job. It has now executed — and
  failed on four consecutive nights, for a reason worth writing down. The block
  was written against mutmut **2**'s schema, and mutmut 3 ignores the key it
  depended on most: `runner = "..."` is not read at all, and is not warned about
  either. `PytestRunner` builds its own argv out of `pytest_add_cli_args` and
  `pytest_add_cli_args_test_selection`. So a carefully constructed command line
  — explicit test files, `--assert=plain`, and a `-k` deselection added
  specifically to fix this very failure — sat in `pyproject.toml` doing
  nothing, while the failure it was written to prevent kept happening with no
  hint as to why. Two further keys, `paths_to_mutate` and `tests_dir`, still
  work but are deprecated aliases and warn on every run.

  The lesson is about the failure mode rather than the tool. A config key that
  is silently ignored costs far more than one that errors, because every piece
  of evidence keeps pointing at the code under test. The block now uses the v3
  keys, and the selection has been verified by running mutmut's exact argv by
  hand: **210 selected, 164 deselected**, where before nothing was deselected at
  all and the app-building tests ran and failed.

  `mutmut` itself still cannot run on this machine — it refuses to start,
  printing `To run mutmut on Windows, please use the WSL` (boxed/mutmut#397),
  and there is no native support, no Docker and no WSL distribution here. The
  mutation run therefore remains CI-only, and it stays a separate `mutation`
  extra so a Windows `pip install -e .[dev]` does not pull in a tool that cannot
  run.
- **A golden-file snapshot of rendered `.tex`** (§5.2) was dropped in favour of
  structural assertions (`audit_source` returning no warnings, brace balance,
  ordering). A byte-exact snapshot of a document whose content is expected to
  be edited regularly would fail on every legitimate content change, which
  trains people to regenerate it without reading the diff.

---

## 10. End-to-end audit, 19 August 2026

A full sweep of the code, the gates, the containers and the documentation,
performed against `main @ d6df8a5` with every finding executed rather than read
off a doc. Fifteen defects, all fixed in one pass. They are recorded here
because the interesting part is not the individual bugs — it is that a suite of
705 passing tests, a clean `mypy --strict`, and a 97% coverage number reported
none of them.

### 10.1 What the green suite was not measuring

Three findings share one root cause, and it is worth naming: **every check in
this project verified that code ran, not that the right thing happened at the
boundary between two correct-looking pieces.**

- The **injection hole** (10.2, SEC-1) sat in `domain/latex.py` and
  `render/renderer.py`, both at 100% line coverage. Every line ran. No test
  asserted that a link target was validated, because nobody had noticed link
  targets were a category.
- The **body-size limiter** (10.2, SEC-2) had tests, and they passed. They
  passed against the header path only; the test that claimed to exercise the
  chunked path sent a request with a `Content-Length` on it, because httpx adds
  one to a bytes body. The docstring described a defence that the test did not
  reach and the code did not implement.
- The **mutmut configuration** (10.2, CI-1) is not covered by tests at all, by
  nature. It failed four consecutive nightly runs while the two commits before
  this audit both aimed at the wrong cause, because a silently-ignored config
  key makes every piece of available evidence point at the code under test.

### 10.2 Findings

Ranked as they were prioritised, not as they were found.

| # | Area | Finding |
|---|---|---|
| CI-1 | CI | `[tool.mutmut]` written against mutmut 2's schema. `runner` is not a key mutmut 3 reads, and is not warned about, so the explicit test files, `--assert=plain` and the deselection were all inert. `paths_to_mutate` and `tests_dir` are deprecated aliases. |
| SEC-1 | Security | `email` reached `\href{mailto:...}` raw with no shape validation, while `linkedin_url` and `github_url` beside it were validated for exactly that. Reproduced: a `personal_info.email` override placed an attacker-chosen clickable link in the resume header and returned 200 with no warnings. |
| PKG-1 | Packaging | `anyio` and `starlette` imported directly but declared nowhere; both arrived transitively through FastAPI. |
| CI-2 | CI | `[tool.coverage.run] source` named only `resume_tailor`, so the entire `ui` package — four modules with a dedicated `AppTest` suite running on every CI job — was excluded from the gate. |
| SEC-2 | Security | `BodySizeLimitMiddleware` buffered the whole body with `await request.body()` and measured afterwards, while its docstring and `SECURITY.md` both claimed streaming enforcement. |
| SEC-3 | Security | API key compared with `!=` rather than `secrets.compare_digest`. |
| SEC-4 | Security | Rate-limiter `_hits` dict never evicted quiet clients. |
| SEC-5 | Security | The 32 MB PDF ceiling was applied after `read_bytes()`, so a runaway document was resident before it was refused. |
| SEC-6 | Security | `redact_pii` blanked the key `name` globally, erasing `EngineStatus.name` from the readiness log — the one field that log line exists to report. |
| PKG-2 | Runtime | `_probe_version` spawned a `--version` subprocess on every `status()` call; `/health/ready` reaches it per request and both images poll it every 30 s. Measured ~52 ms each. |
| PKG-3 | Packaging | The default `data_dir` walked up from `__file__`, which resolves to the environment root under a real wheel. Both images set `RT_DATA_DIR` and so never noticed. |
| PKG-4 | Hygiene | `tests/api`, `tests/integration` and `tests/ui` had no `__init__.py` while the other three test packages did. |
| CI-3 | CI | No dependency ceilings anywhere; `pip install .` in a container resolved whatever was current. Actual drift found: pypdf 4 → 6, structlog 24 → 26. |
| CI-4 | CI | `coverage.xml` uploaded as an artifact that nothing consumed. |
| CI-5 | CI | `concurrency.group` omitted `github.event_name`, so a manual dispatch cancelled the push run for the same ref. The push runs for the two commits before this audit both died at 2–3 s. |

Five documentation claims were also corrected, including two module docstrings
still describing the lifespan-built service that §"The service is built in
`create_app`" had already recorded as changed.

### 10.3 What changed structurally, not just locally

Four of the fixes are design changes rather than patches, and those are the
ones worth keeping in mind when extending the project:

**The mutation deselection became structural.** The `-k "not Api and not
ErrorResponses"` filter that CI-1 restored to working order was still a naming
convention rather than a constraint, and the very first app-backed class added
during this audit — `TestLinkTargetsAreValidated` — would have sailed straight
through it and back into the same namespace-package import failure. Those
classes now carry `pytest.mark.api` and the job deselects `-m "not api"`.

**One shared link-target check, applied twice.** `require_href_safe` now lives
in `domain/latex.py` and is called by every model validator that owns a link
field, *and* again by `render/renderer.py::_href`, which is now the only place
in the codebase that constructs an `\href{}`. The template no longer assembles
its own project link. The point is not belt-and-braces: it is that the previous
design made "add a field that reaches a link and forget to validate it" a silent
one-line mistake, and made the result invisible to every downstream check.

**The body limiter left `BaseHTTPMiddleware`.** It is plain ASGI now, wrapping
`receive`. Starlette's own `_CachedRequest` documents why there was no fix
available inside `dispatch`: `body()` buffers everything and `stream()` empties
the body for downstream. The class of bug here is the general one — a docstring
describing an intent that the framework does not permit, with nothing to catch
the divergence.

**Redaction became path-aware.** `_SENSITIVE_KEYS` blanks a value wherever the
key appears, which is right for `email` and `api_key` and wrong for `name`. A
second table, `_SENSITIVE_PATHS`, blanks a field only inside the container it
actually travels in. Over-broad redaction is not a safe default; it destroys
exactly the diagnostic information an incident needs.

### 10.4 Numbers, before and after

| | Before | After |
|---|---|---|
| Tests | 705 | 824 |
| Coverage | 97.3% over `resume_tailor` only | 95.0% over `resume_tailor` **and** `ui` |
| Security corpus | 36 payloads | 46 payloads, including a new link-target class |
| Nightly CI | 9 of 10 jobs green, 4 nights running | expected green |

The coverage number went *down* and that is the honest direction: the
denominator grew by 365 statements of previously unmeasured frontend. A number
that only ever rises is measuring the wrong thing.
