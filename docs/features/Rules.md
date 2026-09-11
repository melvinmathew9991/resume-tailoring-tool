# Rules — boundaries for this change

## Dependencies

- **Add none.** PDF text uses `pypdf` (already a dependency, used for page
  counting). DOCX uses stdlib `zipfile` plus a regex over `word/document.xml`.
  Legacy `.doc` is detected and refused rather than pulling in `olefile`.
- No `python-multipart`: documents travel as base64 in the JSON body.
- No LLM or embedding library. Rule-based extraction only, see "No fabrication".

## No fabrication

- Never infer a skill, a job title, a duration or a level that is not literally
  present in the submitted text.
- Every stored entry carries `evidence` (the verbatim line it came from) and
  `source_id`. An entry that cannot cite its evidence is a bug.
- The ATS checker reports what is missing. It never guesses that the candidate
  probably has something.

## Data safety

- `mode=merge` is the default and must never drop an existing entry.
- `mode=supersede`, `mode=replace` and `DELETE /api/v1/knowledge/entries` are
  the only destructive paths, and all are explicit caller actions.
  `supersede` removes only what the earlier version of the same-named document
  contributed, and is refused when no document by that name is stored.
- Writes to `data/knowledge.json` are atomic (temp file plus `os.replace`), so
  an interrupted write cannot truncate the store.
- Feature 1 never writes `profile.yaml` or `project_bank.json`.

## Error handling

- Every expected failure is an `AppError` subclass from `core/errors.py` and
  serialises as RFC 7807, like the rest of the API. New: `ExtractionError`
  (422) and `KnowledgeError` (500, invalid store on disk).
- Invalid or empty file, empty text, unsupported extension, oversized payload,
  corrupt zip, encrypted PDF, PDF with no extractable text (a scan) give a 4xx
  with a message naming the fix. Never a 500, never a silent empty update.
- Untrusted archives are size-checked before extraction (zip-bomb guard) and
  parsed without an XML parser (no entity expansion surface).

## Code conventions to follow

- Domain models subclass `StrictModel` (frozen, `extra="forbid"`).
- Repositories follow `BankRepository`: `threading.Lock`, mtime plus size
  stamp, `invalidate()`, and a `parse_*` free function raising a typed error.
- Routes stay thin; all logic lives in the service or the domain.
- Both client modes in `ui/client.py` must return the same response models.
  Embedded mode calls the route function, never a reimplementation.
- Comments explain why, matching the existing density in this repo.
- `ruff` (line length 100) and `mypy --strict` must pass.

## Do not

- Do not change the tailoring workflow, the renderer, the font ladder, the
  page-fit guarantee, or any existing response model.
- Do not make the ATS score depend on wall-clock time, dict ordering or
  keyword frequency.
- Do not let the ATS feature import the renderer or the PDF engine.
