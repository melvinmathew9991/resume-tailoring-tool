# Architecture

Why the code is shaped the way it is. For *what* it does, see the README; for
the audit that motivated the shape, see [PLAN.md](PLAN.md).

## The one-sentence version

A validated `ResumeSpec` is the only thing that can reach the renderer, and the
renderer decides its own template variables — so caller-supplied data can only
ever be a *value*, never a name, a key, or a command.

## Layers

```
             ┌─────────────┐          ┌──────────────┐
             │ Streamlit   │──http───▶│  FastAPI     │
             │ ui/         │◀─────────│  api/v1      │
             └─────┬───────┘          └──────┬───────┘
                   │ embedded                │
                   └───────────┬─────────────┘
                               ▼
                     ┌───────────────────┐
                     │ services/         │  orchestration, concurrency limit
                     └─────────┬─────────┘
          ┌──────────────┬─────┴──────┬──────────────┐
          ▼              ▼            ▼              ▼
     data/          domain/       render/       render/engines/
   bank + profile   models        template      tectonic
   cached, mtime    latex         renderer      pdflatex
   invalidated      matching      pagefit       fake
```

Dependencies point inwards only. `domain/` imports nothing from the project
outside itself, which is why it can be tested exhaustively and held to 100%
coverage.

## Decisions worth explaining

### Non-brace Jinja delimiters

The template uses `\VAR{...}` and `\BLOCK{...}` instead of `{{ }}` and `{% %}`.
LaTeX uses braces constantly; mixing the two is a well-known source of silent
template bugs. Carried over unchanged from the original implementation, which
got this right.

One consequence bit during the rebuild and is now documented in the template
itself: a Jinja comment ends at the first closing brace, so a comment
*describing* the delimiters truncates itself.

### A fixed template context

The renderer builds the template context from typed models, with a fixed key
set. The previous implementation spread a caller-controlled dict into
`template.render(**info, summary=..., font_size=...)`, so a request body
containing `{"personal_info": {"font_size": 1}}` raised
`TypeError: got multiple values for keyword argument` and returned a 500.

The fix is not a blocklist of reserved names — a blocklist rots the moment
someone adds a template variable. The fix is that user data is never a keyword
argument at all.

### Escape first, then re-enable a markup subset

Free text is escaped, and only *afterwards* is `**bold**` / `*italic*`
translated into `\textbf{}` / `\textit{}`. By the time the markup pass runs,
any LaTeX the caller typed is already inert literal characters, and `*` means
nothing to LaTeX. The feature is safe by construction rather than by vigilance.

### Allowlist, not denylist, in the source audit

The rendered document is audited against an allowlist of permitted control
sequences before it reaches a compiler. A denylist silently permits every
dangerous primitive nobody thought of; an allowlist rejects the unanticipated
by default. Adding legitimate new markup to the bank means adding one entry to
`ALLOWED_COMMANDS`, and the error message names the exact command to add.

### The `PdfEngine` abstraction

Three implementations behind one protocol:

- `TectonicEngine` — default. One self-contained binary.
- `PdflatexEngine` — for existing TeX installs; used in the API container.
- `FakeEngine` — in-process, emits real multi-page PDFs.

`FakeEngine` is the load-bearing one. Its page count is a function of source
length *and declared font size*, so the font ladder genuinely steps down under
test. That is what lets the entire pipeline — ladder, warning path, download
endpoint, UI — be tested on a machine with no TeX toolchain, which is the
machine this project is developed on.

### Two health endpoints

`/health/live` says the process is running. `/health/ready` says it can do its
job: bank parses, profile parses, an engine is present. The previous single
endpoint returned `{"status": "ok"}` unconditionally, so a server with an
unreadable project bank and no LaTeX reported itself perfectly healthy.

### Generate and download are separate requests

`POST /resume/generate` returns metadata — page count, whether it fits, which
font size was needed, any warning. `GET /resume/{id}` streams the bytes. The
previous version base64-encoded the PDF into the JSON body, inflating it by a
third and wrapping the warning a user most needed to read around a megabyte of
encoded document.

### Dual-mode UI client

One interface, two implementations: HTTP for the real two-service topology,
in-process for a single-user local session. The embedded client reuses the v1
route handlers rather than reimplementing the mapping from domain objects to
response models — two implementations of that mapping would drift, and the
drift would surface as the UI disagreeing with the API about what a resume
looks like.

### Content is data, not code

Experience, skills, education and personal details live in `data/profile.yaml`.
They used to be Python constants inside the module the web server imports,
which meant a phone number change was a code change and a home address was on
the import path of an HTTP service.

## What is deliberately absent

- **No database.** One JSON file and one YAML file, both hand-edited, both
  reloaded on change. A database would add operational weight and remove the
  ability to edit content in a text editor.
- **No semantic matching or LLM.** See the design principle in the README.
- **No auth by default.** Single-user tool; an optional API key exists for when
  it is not.
- **No repository/unit-of-work ceremony.** Two data modules and one service
  module, each justified by a specific failure it prevents.
