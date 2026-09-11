# PRD — Candidate Knowledge & ATS Match Checker

Scope: two independent features added to the existing resume-tailoring tool.
Nothing in the existing tailoring workflow changes behaviour.

## Users

One user: the candidate (this is a single-user local/Community-Cloud tool).
Two jobs to be done that the tool cannot do today:

1. "The system does not know enough about me." Today candidate knowledge lives
   in two hand-edited files (`data/profile.yaml`, `data/project_bank.json`).
   Adding a new skill, certificate or project means editing YAML/JSON by hand.
2. "I want to know if this JD is worth applying to." Today the only answer is
   `/match`, which ranks *projects* for a resume. It cannot answer
   "how well do I fit this role, and what am I missing?" without generating a
   resume.

## Feature 1 — Profile/Project Knowledge Update

Update the candidate's project/profile knowledge from a document or pasted text.

| # | Requirement | Acceptance |
|---|---|---|
| F1.1 | Accept PDF, DOCX and plain text/Markdown upload | Text extracted; page/paragraph order preserved |
| F1.2 | Accept legacy `.doc` gracefully | Detected and refused with an actionable message (convert to DOCX/PDF/paste) — never a 500 |
| F1.3 | Accept copy/pasted text | Same pipeline as an upload |
| F1.4 | Extract and normalise candidate facts | Skills, tools, domains, education, certifications, experience entries, responsibilities |
| F1.5 | Update, not replace, by default | `mode=merge` keeps every existing entry; `mode=supersede` (swap in a new version of one document, matched by name) and `mode=replace` are explicit |
| F1.6 | No duplicates | Entries deduped on a normalised key; a re-upload of the same document adds zero new entries |
| F1.7 | Explicit removal | `DELETE` of a named entry, `mode=supersede` and `mode=replace` are the only ways to lose data |
| F1.8 | Separation of concerns | Knowledge is stored in `data/knowledge.json`, distinct from the JD (request-scoped) and the generated resume (`profile.yaml` + `project_bank.json`) |
| F1.9 | No fabrication | Extraction is rule-based only. Every stored entry carries the source id and the verbatim evidence line it came from |
| F1.10 | Available downstream | Knowledge is readable by the tailoring workflow and is the candidate side of Feature 2 |
| F1.11 | Validation | Empty file, empty text, unsupported type, oversized input, corrupt archive → 400-class error with a reason |

Out of scope: rewriting `profile.yaml` or `project_bank.json` automatically.
Those hold *pre-verified, LaTeX-formatted* resume content; auto-writing them
would put unverified text on a resume, which is the one thing this tool refuses
to do.

## Feature 2 — ATS Match Checker

Score a pasted JD against stored candidate knowledge. No resume involved.

| # | Requirement | Acceptance |
|---|---|---|
| F2.1 | Input is a JD, nothing else | One text field; no project selection, no options |
| F2.2 | Produces a percentage score | 0–100 integer plus a named band |
| F2.3 | Breakdown by factor | skills, tools, experience, education/certifications, responsibilities/domain, keywords |
| F2.4 | Three-way status per requirement | `exact` / `related` / `missing`, never a bare boolean |
| F2.5 | Gap list | Missing requirements surfaced separately and prominently |
| F2.6 | Deterministic | Same JD + same knowledge → byte-identical report. No model calls, no randomness, no time dependence |
| F2.7 | Explainable | Every requirement reports why it matched: which candidate term, from which source (knowledge / profile / project bank) |
| F2.8 | Not frequency-inflatable | A requirement contributes to the score at most once regardless of how many times it appears. Occurrence counts are reported, never scored |
| F2.9 | Generates nothing | The endpoint has no access to the renderer or the PDF engine |
| F2.10 | Honest about method | Response carries a methodology note stating this is literal/alias matching, not comprehension |
| F2.11 | Required vs preferred | A requirement the posting marks as preferred counts `priority_weights["preferred"]` (0.25) of a required one; ambiguity resolves to required |
| F2.12 | No credit for generic words | A multi-word requirement earns partial credit from one of its words only if that word is not shared by 3+ vocabulary phrases |
| F2.13 | Hard filters | Required degree level and years of experience are pass/fail; a failed one caps the score at 39 and the uncapped score is reported. Missing data is `unverified` and never caps. Location is pass or flagged for review, never failed |

## Non-goals

- No LLM, embeddings or semantic model. The tool's existing matching is
  deliberately literal because ATS keyword scanning is literal; both features
  keep that property, and it is what makes F2.6 achievable.
- No multi-user accounts, no auth changes.
- No new runtime dependency.
