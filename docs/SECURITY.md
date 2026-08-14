# Security

## Threat model

This service accepts free text over HTTP and interpolates it into a document
that it hands to a **TeX engine** — a Turing-complete macro processor with
historical file-read and shell-execution primitives. That is the whole threat
model in one sentence, and it is why this document exists for what is nominally
a personal tool.

Assumed attacker positions:

1. **A web page in the user's browser.** The API listens on localhost; any open
   tab can issue cross-origin requests to it.
2. **Anyone on the network**, if the service is ever bound beyond localhost.
3. **The user themselves**, pasting text from a job posting they did not write.

## Controls

| Threat | Control | Where |
|---|---|---|
| LaTeX injection (`\input`, `\write18`, `\catcode`, macro redefinition) | All free text escaped in a single pass; dangerous commands rejected before escaping so the caller gets a clear error rather than silent literal text | `domain/latex.py`, `services/resume_service.py` |
| Unanticipated LaTeX constructs | Rendered source audited against an **allowlist** of control sequences; anything else is refused | `render/renderer.py::audit_source` |
| Shell escape | `-no-shell-escape` (pdflatex) and `--untrusted` (tectonic), unconditionally and not configurable | `render/engines/` |
| Compile reading or writing outside its scratch dir | `HOME`, `TEXMF*` redirected into a temp dir; `openin_any`/`openout_any=p` | `render/engines/base.py` |
| Expansion bombs / hangs | Hard per-compile timeout mapped to a typed 504, plus the dangerous-command rejection that catches `\def`/`\loop` first | `render/engines/base.py` |
| Resource exhaustion by concurrency | `asyncio.Semaphore` capping simultaneous compiles; compilation runs off the event loop | `services/resume_service.py` |
| Oversized requests | Body-size middleware checking both `Content-Length` and the streamed body; per-field length limits in the schemas and the service | `api/middleware.py`, `api/schemas.py` |
| Request flooding | Fixed-window rate limiter, with a separate tighter budget for generation; health checks exempt | `api/middleware.py` |
| Drive-by requests from any browser tab | CORS **allowlist**, defaulting to `http://localhost:8501`; `*` rejected outright in `prod` | `api/main.py`, `core/config.py` |
| Unauthorised access when exposed | Optional `X-API-Key` (`RT_API_KEY`); health endpoints exempt so probes keep working | `api/deps.py` |
| Malformed URLs reaching `\href{}` | `github` and URL fields are shape-validated, not escaped — escaping would break the URL | `domain/models.py` |
| PII in logs | structlog processor redacts emails and phone numbers, and blanks sensitive keys | `core/logging.py` |
| Information disclosure in errors | Unexpected exceptions log a traceback server-side and return a generic problem document | `api/main.py` |
| Blank placeholder mistaken for a real PDF | `auto` engine selection refuses to fall back to `fake` in `prod`; the UI shows a permanent warning; `/health/ready` reports the engine by name | `render/engines/registry.py`, `ui/components.py` |
| Running the compiler as root | Both container images create and switch to an unprivileged user | `docker/` |

## Defence in depth on the injection path

Four independent layers, any one of which would stop the original defect:

1. **Rejection** — dangerous commands in caller text are refused with a 422.
2. **Escaping** — everything that survives is escaped, so LaTeX becomes literal
   characters.
3. **Audit** — the fully rendered document is checked against a command
   allowlist and a brace-balance check before it reaches an engine.
4. **Sandboxing** — the engine itself runs with shell escape off, file access
   restricted to a temporary directory, and a hard timeout.

Layers 1–3 are asserted against a checked-in payload corpus
(`tests/security/payloads.py`) at both the service and HTTP entry points, plus
a property test asserting that *no* input produces unsafe rendered output.

## Deliberate non-goals

- **Multi-tenancy.** There is one user. Documents are held in a bounded
  in-memory TTL store keyed by an unguessable id, not per-account storage.
- **Persistence.** Nothing generated is written to disk, so nothing needs
  securing at rest.
- **Authenticated UI sessions.** The optional API key protects the API; the UI
  has no login because there is no second user to distinguish from.

## If you deploy this

The defaults are for localhost. Before binding to anything wider:

1. `RT_ENVIRONMENT=prod` — enforces a non-wildcard CORS list, forbids debug,
   and refuses the fake engine.
2. Set `RT_API_KEY`.
3. Set `RT_CORS_ORIGINS` to the exact UI origin.
4. Terminate TLS in front of it. Neither container does TLS.
5. Lower `RT_GENERATE_RATE_LIMIT_PER_MINUTE` and
   `RT_MAX_CONCURRENT_COMPILES` to match the host.

## Reporting

This is a personal project with no security contact. If you find something,
open an issue describing the impact.
