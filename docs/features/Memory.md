# Memory — build log for the two standalone features

Written so a new session (or a different tool) can pick this up without
re-reading the whole codebase. Newest phase last.

Status: **all six phases complete, plus a review pass** (see "Review fixes" at
the end). 1049 tests pass, `ruff check` and `mypy --strict` are clean, coverage
93.6% against a 90% floor.

## What exists now

| Feature | Entry points |
|---|---|
| Profile knowledge | `ui/knowledge_view.py`, `GET/POST/DELETE /api/v1/knowledge`, `ResumeService.update_knowledge` |
| ATS match check | `ui/ats_view.py`, `POST /api/v1/ats/check`, `ResumeService.ats_check` |

## Files added

```
src/resume_tailor/domain/extraction.py     pdf/docx/text -> plain text, section split
src/resume_tailor/domain/vocabulary.py     shared curated term list + related-term table
src/resume_tailor/domain/knowledge.py      KnowledgeBase, extractor, merge rules
src/resume_tailor/domain/ats.py            requirements, classification, weighted score
src/resume_tailor/data/knowledge_repo.py   knowledge.json, atomic writes, mtime cache
src/resume_tailor/api/v1/knowledge.py      feature 1 routes + shared response mapping
src/resume_tailor/api/v1/ats.py            feature 2 route
ui/knowledge_view.py                       feature 1 view
ui/ats_view.py                             feature 2 view
tests/unit/test_extraction.py              41 tests
tests/unit/test_knowledge.py               38 tests
tests/unit/test_ats.py                     43 tests
tests/api/test_knowledge_ats.py            37 tests
docs/features/*.md                         PRD, Architecture, Rules, Phases, Design, this file
```

## Files modified (and why, briefly)

| File | Change |
|---|---|
| `core/errors.py` | `+ExtractionError` (422), `+KnowledgeError` (500) |
| `core/config.py` | `knowledge_filename`, `knowledge_path`, three input limits |
| `services/resume_service.py` | `knowledge()`, `update_knowledge()`, `remove_knowledge_entry()`, `clear_knowledge()`, `candidate_corpus()`, `ats_check()`, `_profile_corpus()`; optional fourth repository on the constructor |
| `api/schemas.py` | request/response models for both features |
| `api/v1/__init__.py` | two routers included |
| `api/main.py` | `build_service` passes `KnowledgeRepository` |
| `ui/client.py` | five client methods in both http and embedded modes |
| `ui/state.py` | view constants, knowledge and ATS state fields |
| `ui/app.py` | sidebar `Feature` radio and dispatch (≈15 lines) |
| `tests/api/test_http_layer.py` | route snapshot extended by three paths |
| `tests/unit/test_service.py` | two service-layer test classes |
| `tests/ui/test_streamlit_app.py` | `TestFeatureNavigation` |
| `README.md`, `docs/ARCHITECTURE.md`, `.gitignore` | documentation and ignoring `data/knowledge.json` |

Nothing in the tailoring workflow, the renderer, the templates, the page-fit
ladder or any existing response model was changed.

## Decisions a future session should not re-litigate

**No new dependency.** PDF text comes from `pypdf`, already present for page
counting. DOCX is stdlib `zipfile` plus two regexes over `word/document.xml` --
which also means there is no XML parser for a hostile upload to attack. Legacy
`.doc` is detected by its OLE magic bytes and refused with advice, because
reading it would mean `olefile`.

**Documents travel as base64 in the JSON body.** `UploadFile` needs
`python-multipart`. Base64 costs a third more bytes on a payload the existing
body-size middleware already caps, and keeps every failure on the same RFC 7807
path. Embedded mode skips the encoding and calls the service directly, reusing
the route module's `build_update_response` so the two modes cannot drift.

**The ATS candidate side is three sources, not one.** `knowledge.json`,
`profile.yaml` and `project_bank.json` are all things the system already knows
about this person; using only the first would report gaps the repository
plainly contradicts, and would make the feature useless until the user had
uploaded something. Each match reports which source it came from.

**Durations are resolved once, at extraction time.** An open-ended "Present"
range is closed against the clock when the document is ingested and the month
count is stored. Recomputing it per request would make the same job description
score differently next month, which breaks the determinism guarantee.

**Overlapping roles are counted once.** Two concurrent jobs are not two careers;
`KnowledgeBase.total_experience_months` merges intervals.

**Each requirement is credited once.** Occurrence counts are reported and are
never an input to the score. This is asserted by
`test_repetition_cannot_raise_the_score`.

## Known limitations, accepted

- The vocabulary is curated and data-science-leaning. Anything it misses still
  scores, as a `keywords` requirement -- it just is not categorised.
- Matching is literal plus aliases plus a hand-written related-term table. It
  is not semantic, deliberately: that is what makes the report reproducible.
- Section splitting is heuristic. A resume with unlabelled sections yields
  fewer experience facts; the terms are still extracted from the whole text.
- An image-only PDF is refused rather than OCR'd.
- Removing knowledge is one entry at a time, or all of it. There is no bulk
  edit and no undo beyond re-adding the document.

## Where to start if you are extending this

- New term to recognise: `domain/vocabulary.py`. Alternate spellings go in
  `ALIASES` in `domain/matching.py`, not here.
- Different weights or bands: `WEIGHTS` / `BANDS` in `domain/ats.py`. They are
  published in every response, so a change is visible to callers.
- A new extractable field: add a rule to `domain/knowledge.py` and a category to
  `CATEGORIES`; the API and UI iterate the categories rather than naming them.
- A new file format: `domain/extraction.py` is the only module that knows about
  formats, and `SUPPORTED_EXTENSIONS` plus `guess_kind` are the whole contract.

## Review fixes (post-implementation pass)

A code review of the working tree raised eleven issues; all were real and all
are fixed. Recorded here because several were *unreachable-guard* bugs -- the
code looked correct and the friendly error simply could not fire -- which is
the kind of thing a future reader will otherwise re-introduce.

| # | Was | Now |
|---|---|---|
| 1 | `KnowledgeBase.entries` hard cap equalled `max_knowledge_entries`, so the service's friendly limit error was unreachable and pydantic answered instead | Hard caps raised well above the configured ones (the two-layer scheme `api/schemas.py` already uses), and the settings bounded `le=` the hard caps |
| 2 | `experience` and `sources` had no service-side ceiling; 200 documents locked the user out permanently | `_check_knowledge_limits` checks all three, `max_knowledge_experience` / `max_knowledge_sources` added |
| 3 | An encrypted or deflate64 `.docx` raised `RuntimeError` / `NotImplementedError` past the handler as a 500 | Both caught as `ExtractionError`. `NotImplementedError` is caught **first** -- it subclasses `RuntimeError`, and the other order reported a compression problem as a password one |
| 4 | A line holding an unrelated year ("founded 1919 ... Jan 2024") produced a 1261-month span that failed validation and aborted the whole upload | Spans over 50 years are not date ranges and the line is skipped; `_extract_experience` also skips any line that fails validation, so one bad line never costs the document |
| 5 | The 25-keyword cap kept the alphabetically first terms, silently dropping Snowflake and Terraform | Ordered by first mention in the posting -- importance-shaped, and unlike frequency it cannot be gamed |
| 6 | `experience_months` came only from `knowledge.json`, so a fresh checkout scored every "N years" requirement missing despite a populated `profile.yaml` | Ranges are pooled from both and merged once (`merge_month_intervals`), with `experience_sources` reporting which contributed |
| 7 | `load()`/`save()` read-modify-write: two concurrent updates lost one set of entries | A service-level lock covers the whole cycle; extraction stays outside it |
| 8 | The save confirmation was never cleared, so it re-announced itself on every later rerun | Shown once, then cleared |
| 9 | `transformers`, `code review` and `statistics` each sat in two vocabularies and scored in two weighted categories | One home per term, a disjointness test enforces it, and `extract_requirements` claims terms category-by-category as a backstop |
| 10 | A new document with nothing extractable was reported as "already known" | Its own branch, naming the real problem |
| 11 | `HttpBackendClient` defaulted a missing filename to `"upload"`, guaranteeing "unsupported file type" instead of "filename is required" | Passed through empty so the API's own validator answers |

One further inconsistency was found while verifying #6 and fixed with them:
relatedness is one hop, so a candidate with `b.tech` scored `bachelor` as
related and `degree` as missing -- two answers about one qualification. The
degree abbreviations are now related to `degree` directly.

**Test count after the pass: 1035** (up from 1007), coverage 93.6%. After the resume-scoped score below: **1049**.

## Later addition: the resume-scoped ATS score

`POST /api/v1/resume/ats` and step 4 of the tailoring view. Asked for after the
review pass, because the tailoring flow showed no ATS score at all.

It is **not** feature 2 reused. `/ats/check` scores the candidate from every
knowledge surface; this scores one assembled document, and its candidate corpus
is `_spec_corpus(spec)` -- the profile plus the selected projects' titles and
budget-trimmed bullets. Project `keywords` and `domain` tags are excluded on
purpose: they are matching metadata that is never printed, and including them
would score a resume for words an ATS cannot see. `test_bank_keywords_are_not_counted`
pins that.

The response adds `covered_elsewhere`: requirements the page misses that the
wider candidate corpus covers. That is the actionable subset -- a different
project selection closes it -- and it is what separates "you did not put this
on the page" from "you do not have this".

Computed without compiling, so the UI re-scores on every selection change
rather than per multi-second PDF. `breakdown_out` was lifted out of
`api/v1/ats.py` and is shared by both endpoints so the two cannot describe the
same job description differently.

Steps in the tailoring view renumbered: ATS match is 4, Result is 5.

## Bug: a prose document became 2,057 "skills"

Reported as "ATS match check says 88%, the resume score says 52% for the same
JD". The gap was real but the cause was upstream of scoring.

`split_sections` ends a section only at the *next recognised heading*. A 581 kB
`.docx` of project notes had one "Skills" heading and no heading after it, so
the whole file landed in that section, and `_add_declared_skills` comma-split
all of it: 2,057 skill entries, of which 2,001 were sentence fragments, URLs
(`https://github.com/...`), stray numbers (`~2`, `000 breast cancer patients)`)
and headings (`1. what is this project?`).

That inflates the *candidate* score specifically, because `corpus_text()`
includes every entry's evidence line -- so the candidate corpus became the
entire document, and almost any JD term matched. The resume score was unaffected
(it reads the page), hence the divergence.

Fixed in `_add_declared_skills` with three bounds and a shape predicate:

- `_MAX_SKILL_SECTION_LINES = 25` -- a real skills block is a handful of lines;
- `_MAX_LIST_LINE_WORDS = 20` -- a longer line is prose, whatever heading it sits under;
- `_MAX_DECLARED_SKILLS = 150` per document;
- `looks_like_a_declared_term()` rejects URLs, digit-led fragments, unbalanced
  brackets, sentence punctuation, all-stopword fragments, embedded measurements
  and participle clauses.

The predicate can afford to be strict because `_add_vocabulary_terms` captures
vocabulary terms independently -- rejecting a bare "monitoring" costs nothing.
Same document shape now yields 16 entries instead of 2,057, with `python`,
`sql`, `pandas`, `scikit-learn` and `lifelines` all kept.

**A store written before this fix stays polluted** -- extraction runs at upload
time, not at read time. Re-upload the document with mode `replace`.

## Store health is now observable

Follow-up to the prose bug: the user could not tell whether a re-upload had
worked. `lint_knowledge()` is the counterpart of `lint_bank()` and flags the
shapes that indicate a bad ingest -- a high share of unrecognised skill/tool
terms, sentence-length values, link- or number-shaped entries, no dated roles,
and any single document contributing more than 500 entries.

Surfaced in three places: `KnowledgeResponse.warnings`, the top of the Profile
knowledge view (with an explicit "looks healthy" line when there are none), and
`python tasks.py knowledge` for a terminal check.

The `version` field was already a content hash; it is the check that a write
actually happened, and it is now stated as such in the README.

## Why "the upload did not update the app"

The store's mtime moved but its content hash did not, and the one source still
carried its original `added_at`. That is the signature of a **Merge** of an
already-ingested document: every entry was known, so nothing was added, nothing
was removed, and the file was rewritten byte-identical. Merge is additive by
definition and can never clean a polluted store -- only Replace can.

The app was unhelpful about that, so three things changed:

- `_summarise` now names Replace in the "added before" branch, instead of
  ending at "your knowledge base is unchanged".
- The knowledge view shows a hint beside the mode radio when the store has
  warnings and Merge is selected: merge cannot fix them.
- The view **re-reads the store on every render** rather than caching it in
  session state. A snapshot is how "the app did not pick up my change" happens
  when the file is written by `tasks.py`, another session, or by hand.
- A successful update clears `state.ats` and `state.resume_ats`, so an ATS
  report computed against the previous store cannot survive an upload and be
  compared against the new one.

The two scores were also relabelled -- "ATS match · your profile" and "ATS
match · this resume" -- with each view stating that the other number exists and
why they differ. Two similar-looking percentages in one app read as a
contradiction; naming them is cheaper than letting each user rediscover it.

## Checked against `docs/resume_automation_spec.md` (2026-09-11)

The spec records the manual process this tool automates. Comparing the two
found three places where the code fell short of the spec's own rules; all
three are fixed. Section 7 of the spec now records the full status.

**Generic words no longer earn partial credit.** A multi-word requirement used
to score `related` if *any* 4+ letter word of it appeared in the candidate
corpus, so "data governance", "model risk management" and "stakeholder
management" all got half credit from any data background. `GENERIC_TOKENS` in
`domain/ats.py` is derived from the vocabulary -- every word shared by 3+
multi-word terms ("data" 18, "analysis" 9, "model" 8, "management" 7, ...) -- and
those words cannot carry a partial match alone. The old test that pinned the
inflated behaviour was rewritten, not deleted: a distinctive word
("governance") still earns `related`.

**Required vs preferred.** `split_by_priority` tags each sentence from headings
("Nice to have", "Preferred qualifications") and in-sentence cues ("is a plus",
"preferred"). A requirement is `preferred` only if *every* mention is; required
cues win within a sentence; unrecognised short `...:` headings close a
preferred section. Everything ambiguous is required, because that direction
cannot inflate a score. A preferred requirement weighs 0.25
(`PRIORITY_WEIGHTS`), and a category's own weight scales by its average
priority weight -- without that, a category holding only wish-list items still
counted at full weight. `priority` is on every requirement and
`priority_weights` is on both ATS responses, so the score stays recomputable
from the breakdown.

**Update one document (`mode=supersede`).** Merge only adds, so a fact deleted
from an edited Project Points document stayed in the store and kept scoring --
against the spec's "the source document always wins". Supersede drops
everything earlier same-named versions contributed (name compared ignoring
case and spacing), merges the new one, and reports added/removed/unchanged
against the store as it was. It is **refused** when no document by that name is
stored, rather than falling back to merge, because a renamed file would
otherwise leave the stale version in place silently. Known limitation: an
entry records only its first source, so a fact another document also holds but
that was credited to the old version is removed with it.

**Test count after this pass: 1120** (up from 1096); `ruff format --check`,
`ruff check` and `mypy --strict src ui` clean.

## Hard filters (spec criterion 3)

`Gate` in `domain/ats.py`: degree, years and location, each `pass` / `fail` /
`unverified`, for what the posting states as *required* only.

- **Degree** reads the candidate's level from `CandidateCorpus.education_text`
  -- knowledge entries filed as education plus `profile.yaml` degree names --
  never from the matching corpus, where "scrum master" or "master data" would
  read as a master's. "master"/"bachelor" need degree context even in the JD
  ("master's", "master of"). Two required levels read as the lower; "or
  equivalent" waives the gate.
- **Years** binds on the largest required figure; within a year passes with a
  note, matching `classify_years`.
- **Location** is pass or `unverified`, never `fail`: nothing records whether
  the candidate would relocate. City compared without the trailing country
  part, with a renamed-city alias table.
- A `fail` caps the score at `GATE_CAP = 39`, the top of "Weak match".
  `uncapped_score` is kept, so the breakdown still adds up; `capped` is true
  only when the cap actually lowered the score. `unverified` never caps.

Found on the way: `ats_check_resume` built its page corpus with no dated
experience, so every "N years" demand on the resume score read as
unverifiable. It now uses the profile's printed dates, education and location.

Also found by running the gates against the real `data/` with a sample JD: the
catch-all keyword category double-counted two things. "Master's" came back as
a keyword after education had already scored "master" (possessives are now
matched against claimed terms), and the "Location:" line was read for keywords,
so "Bangalore" and "Hybrid" were scored as skills -- counting the location
twice and faulting the candidate for never writing "hybrid". That line now
belongs to the location gate only.

**Test count after the hard filters: 1144**; format, lint and
`mypy --strict src ui` clean.

## The PDF text-extraction check (spec criterion 4)

The last thing in the tool that reasoned about the resume without ever looking
at the resume. The page-fit ladder counts pages; the resume ATS score reads
`ResumeSpec`. Neither opened the compiled PDF and asked what a parser finds in
it -- and an ATS only ever sees what its parser finds.

`render/parsecheck.py` extracts the text back out with `pypdf` (already present
for page counting, so no new dependency and no second binary) and compares it
word-for-word against `expected_text(spec)` -- built from the typed spec, never
from the rendered LaTeX, because the markup is the thing under test and
deriving the expectation from it would compare the document against itself and
pass unconditionally. It runs on every `generate_sync`, unconditionally, on the
same bytes that get stored: a check a caller has to remember to ask for is one
that stops being run the week after it is written.

The check is **one-directional**. Only text that was expected and not found is
reported; extra text is never a defect, because the template prints labels
("GitHub:", section rules) that no model holds, and flagging those would make
the whole panel noise.

**It found a real defect on its first real compile.** Against Tectonic and the
production `data/`, coverage was 96.8%, and the losses were not random:

| Cause | Count | What it looks like |
|---|---|---|
| Ligatures | 14 words | `classification` is typeset with an `fi` ligature and extracts as one glyph, not two letters -- `verification`, `MLflow`, `significance`, `workflows`, `identified` all likewise. **Fixed, see below.** |
| Kerning splits | 3 words | `Frameworks` extracts as `F rameworks`; the wide `F r` pair reads as a word boundary. Also hit `FAISS` and `Tools` |

Both are invisible on the page and total to a literal keyword scan, which is
exactly the class of problem criterion 4 exists to catch. Neither is a content
problem, so neither fails the document -- they are warnings, each naming its own
remedy, because "3.4% of words are missing" is a number and "these are set with
ligatures, those are split by kerning" is a diagnosis.

**The ligature half is now fixed** (applied after the check landed, on the
author's decision). The cause is one line: `\usepackage[T1]{fontenc}` forces
the legacy 8-bit Type1 fonts, whose character map reports the `fi` ligature as
a single glyph. It is now loaded for pdfTeX only -- where it is still needed,
pdfTeX having no Unicode font path -- and XeTeX uses its native Unicode Latin
Modern, which sets no f-ligatures at all. Measured on the real resume: **27
ligature codepoints to 0, and coverage 96.8% to 99.4%** -- `classification`,
`verification`, `MLflow` and eleven others now read back.

**Two dead ends, recorded so nobody tries them again.** `microtype` changes
nothing under XeTeX. Neither does `fontspec`: the first version of this fix
loaded it and set `Ligatures=NoCommon`, which read convincingly, shipped, and
turned out to do **nothing at all** -- compiling with the whole `\else` branch
empty produces byte-identical output, and so does `Ligatures=Common`. The fix
had always come from *withholding* `fontenc`, never from anything added. Both
lines were removed, along with `else` and `defaultfontfeatures` from
`ALLOWED_COMMANDS`; an allowlist entry that buys nothing is worse than no entry,
because the next reader assumes it was load-bearing. `Kerning=Off` and
`LetterSpace=0` were measured against the kerning splits and change nothing
either.

The lesson is the one the check itself exists to teach: in this area, measure
the compiled artefact. Every one of these settings is documented as doing what
it says, and four of the five changed not a single byte of output.

The kerning splits survive every variant and are still reported. They are a
property of the extractor rather than of the font, so there is nothing in the
template that would fix them.

**What it cost.** Two entries on `ALLOWED_COMMANDS`, `\ifPDFTeX` and `\fi` --
the security boundary this project deliberately makes hard to widen. The
reasoning is recorded beside them in `domain/latex.py` and pinned by
`TestConditionalsAreNotAWayIn`: neither can read a file, write a file, define a
macro or reach a shell; `input`, `write`, `csname` and `def` stay in
`DANGEROUS_COMMANDS` and are rejected whatever conditional they appear inside,
because the audit is a flat scan of rendered source and evaluates nothing. User
text cannot become a command in the first place -- a summary goes through
`escape_user_text`, so a typed `\fi` lands on the page as characters.

The pdfTeX branch cannot be verified on this machine (no TeX Live), and the
`texlive` CI job ran only nightly and on tags. It now also accepts
`workflow_dispatch`, because it is the only job that exercises that branch and
waiting a day to test a change to it is how the branch rots.

**False alarm found and fixed on the way.** `8--13` was reported as lost text:
LaTeX turns `--` into an en dash, so the source and the page held the same
authored text written two ways. `tokenize` now cuts en dashes, em dashes and
`--` runs on both sides, while leaving a *single* hyphen alone -- splitting
`scikit-learn` would turn one real keyword match into two that no posting asks
for.

### The placeholder engine now emits real text

`FakeEngine` produced structurally valid blank pages, which would have made the
parse check fail identically on a perfect document and a broken one -- that is,
untestable anywhere without a TeX toolchain. It now lays the document's own
words into a base-14 font content stream and emits a link annotation per link.
What it still does not model is typesetting: no line breaking, no ligatures, no
kerning. So the fake engine proves the plumbing, and only the `latex`-marked
integration tests prove the layout.

Consequence worth knowing: a ligature codepoint cannot be expressed in WinAnsi,
so the ligature branch is tested by classifying synthetic extracted text
directly rather than through a generated PDF. Every claim about the placeholder
engine being "blank" was corrected -- README, the sidebar notice, and the test
that pinned the old wording.

### Also fixed while here

`GenerateResponse` was built in two places -- the route and the embedded-mode
client -- and `parse_check` would have been the second field to drift between
them. Both now call `build_generate_response`, the same pattern
`build_update_response` already established for knowledge.

The README still described the five-step font ladder that was removed in favour
of a fixed 9.2pt. Corrected, along with three places that called the
placeholder PDFs blank.

**Test count after this pass: 1230 fast** (up from 1144) **and 13
`latex`-marked** (up from 9); coverage 93.6% against the 90% floor;
`ruff format --check`, `ruff check` and `mypy --strict src ui` clean.

Still open (spec Section 7): JD truncation detection, a stored gap log across
JDs, and resume/Project-Points conflict detection.
