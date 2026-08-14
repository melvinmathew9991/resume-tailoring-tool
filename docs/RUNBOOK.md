# Runbook

Operating, diagnosing and changing the tool.

## Starting it

| Goal | Command |
|---|---|
| Everything, locally | `python tasks.py dev` |
| API only | `python tasks.py api` → <http://127.0.0.1:8000/docs> |
| UI only (needs the API) | `python tasks.py ui` |
| UI alone, one process | `RT_UI_MODE=embedded streamlit run ui/app.py` |
| Containers | `docker compose -f docker/docker-compose.yml up --build` |

## First thing to run when something is wrong

```bash
python tasks.py doctor          # what is installed, what is missing
curl http://127.0.0.1:8000/health/ready | python -m json.tool
```

`/health/ready` answers most questions directly: whether the bank parsed,
whether the profile parsed, which PDF engine is active, and any content
warnings from the bank linter.

## Symptoms

### "Generated PDFs are blank"

The `fake` engine is active. Confirm with `/health/ready` →
`checks.pdf_engine.name`. Install Tectonic (`winget install
TectonicProject.Tectonic`, `brew install tectonic`, or `cargo install
tectonic`) or use the containers. This is reported, never silent: the sidebar
carries a permanent warning and every generate response includes `"engine"`.

### "Could not reach the API"

The UI is in `http` mode and nothing is listening. Either start the API
(`python tasks.py api`) or switch the UI to `RT_UI_MODE=embedded`. The UI says
which of the two it is doing in the sidebar.

### 503 from `/health/ready`

One of the three checks failed. The response body names which and includes the
error. Usual causes: a JSON syntax error in `project_bank.json` after a manual
edit, or a YAML indentation error in `profile.yaml`.

### 422 `unsafe_content`

Text in the summary or a personal field contained a LaTeX command. Use
`**bold**` and `*italic*` instead — the response names the offending command.

If the code is `unsafe_content` but it came from *rendering* rather than input,
the rendered document contained a control sequence that is not on the
allowlist. That means new markup was added to the bank; add the command to
`ALLOWED_COMMANDS` in `src/resume_tailor/domain/latex.py`. The error names it.

### 504 `compile_timeout`

A compile exceeded `RT_COMPILE_TIMEOUT_S`. Usually pathological input; try
fewer projects. Raise the limit only after ruling that out.

### 429 `rate_limited`

Expected under a retry loop. `Retry-After` says how long. Tune with
`RT_RATE_LIMIT_PER_MINUTE` / `RT_GENERATE_RATE_LIMIT_PER_MINUTE`.

### "It says my resume doesn't fit"

Working as designed. `fits: false` plus a `warning` means the content did not
fit even at the 8.8pt floor. The PDF returned is the smallest-font attempt, so
you can look at it while deciding what to cut. Remove a project or shorten
bullets — do not raise the page limit reflexively.

### A generated PDF 404s on download

Documents live in a bounded in-memory store with a TTL
(`RT_DOCUMENT_TTL_S`, default 15 minutes) and a cap of 32. Generate again.

## Changing content

Edit `data/project_bank.json` or `data/profile.yaml`. **No restart is
required** — both are re-read when their mtime or size changes.

After editing:

```bash
python -m pytest tests/unit/test_repositories.py -q     # validity
curl -s localhost:8000/health/ready | python -m json.tool  # linter warnings
```

The linter flags content that is valid but probably unintended: a `github`
value that points at a profile rather than a repository, a project with no
keywords (which can therefore never match anything), or one with a single
bullet.

To take a project out of circulation without deleting it, set
`"hidden": true`. Hidden projects are excluded from listings, matching **and**
generation.

## Deployment posture

Before exposing beyond localhost, work through the checklist at the end of
[SECURITY.md](SECURITY.md). The short version: `RT_ENVIRONMENT=prod`, set
`RT_API_KEY`, set `RT_CORS_ORIGINS` exactly, put TLS in front.

## Observability

Logs are structured (`RT_LOG_JSON=true` in containers). Every line carries a
`request_id`, echoed to the client in `X-Request-ID`, so a failure report can
be traced to the exact request.

Events worth alerting on:

| Event | Meaning |
|---|---|
| `engine.fallback_to_fake` | No real engine found; output is placeholder |
| `app.not_ready` | Started with a failing dependency |
| `api.unhandled` | A bug — carries a traceback |
| `http.rate_limited` | Sustained occurrences mean a client is looping |
| `render.audit_warnings` | Rendered source has a suspicious construct |

Emails and phone numbers are redacted by a logging processor before emit.

## Making changes safely

```bash
python tasks.py check      # lint + types + coverage gate (what CI runs)
python tasks.py test-all   # includes real compiles, if an engine is installed
```

The coverage gate is 90% overall. `domain/latex.py` and `domain/matching.py`
are the two modules where a bug is silent and lands on a real resume — keep
them at 100%.

If you touch the LaTeX escaping, run the property tests specifically:

```bash
python -m pytest tests/property tests/security -q
```
