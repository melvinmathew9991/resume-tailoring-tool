# Architecture — Candidate Knowledge & ATS Match Checker

Both features slot into the existing four-layer structure. No layer is added,
no layer is bypassed.

```
ui/                     Streamlit. Sidebar radio picks one of three views.
  app.py                  view: Tailor resume        (unchanged flow)
  knowledge_view.py       view: Profile knowledge    (new, Feature 1)
  ats_view.py             view: ATS match check      (new, Feature 2)
  client.py             BackendClient protocol -- http | embedded (extended)

api/v1/                 FastAPI routes. Thin: validate, call the service, map.
  knowledge.py            GET/POST/DELETE /api/v1/knowledge          (new)
  ats.py                  POST /api/v1/ats/check                     (new)

services/resume_service.py
  knowledge(), update_knowledge(), remove_knowledge_entry(), ats_check()

data/
  knowledge_repo.py     read/merge/atomic-write data/knowledge.json  (new)

domain/                 Pure functions and models. No I/O.
  extraction.py           bytes|text -> normalised plain text        (new)
  knowledge.py            text -> KnowledgeBase; merge/dedupe        (new)
  ats.py                  JD + candidate corpus -> AtsReport         (new)
  matching.py             REUSED: count_matches, ALIASES, STOPWORDS,
                          normalize, extract_meaningful_terms
  models.py               REUSED: StrictModel base, compute_version
```

## Data flow

A. Knowledge update

```
upload bytes / pasted text
  -> extraction.extract_text()        pdf (pypdf) | docx (zipfile+regex) | text
  -> extraction.split_sections()      heading-keyed sections
  -> knowledge.extract_knowledge()    vocabulary + pattern rules
  -> KnowledgeBase.merged_with()      dedupe on normalised key, keep existing
  -> KnowledgeRepository.save()       atomic write of data/knowledge.json
  -> available to ResumeService.candidate_corpus()
```

B. ATS check

```
JD text
  -> ats.extract_requirements()   vocabulary scan + years/degree regex + JD terms
  -> ats.build_report()           vs CandidateCorpus (knowledge + profile + bank)
  -> AtsReport                    score, band, 6 category breakdowns, gaps, note
```

## The three knowledge surfaces, kept separate

| Surface | File | Written by | Purpose |
|---|---|---|---|
| Candidate knowledge | `data/knowledge.json` | Feature 1 (app) | What the system knows about the person |
| Resume content | `data/profile.yaml`, `data/project_bank.json` | human, by hand | Pre-verified text that may be printed |
| Job description | none, request-scoped | caller, per request | Never persisted |

Feature 1 writes only the first. That boundary is why an uploaded document can
never put unverified prose onto a PDF.

## Candidate corpus

`ResumeService.candidate_corpus()` composes three named sources so every ATS
match can report where it came from:

- `knowledge` -- `data/knowledge.json` (Feature 1)
- `profile`   -- `data/profile.yaml` summary, skills, experience, education
- `bank`      -- `data/project_bank.json` keywords, domains, titles, bullets

Composing all three means the ATS checker is useful before the first upload,
and it reuses the two loaders that already exist rather than duplicating them.

## Scoring model (deterministic)

Weights, fixed in `domain/ats.py` and echoed in every response:

| Category | Weight |
|---|---|
| skills | 0.30 |
| tools | 0.20 |
| experience | 0.15 |
| responsibilities / domain | 0.15 |
| education / certifications | 0.10 |
| keywords | 0.10 |

- Per requirement credit: exact 1.0, related 0.5, missing 0.0.
- Category score = mean credit over that category's requirements.
- Overall = weighted mean over *present* categories only; weights are
  renormalised so a JD that never mentions education is not penalised for it.
- A requirement is credited once. Occurrence counts are reported for
  transparency and are not an input to the score (PRD F2.8).

## Transport decision: no python-multipart

FastAPI needs `python-multipart` for `UploadFile`. A document is instead sent as
a base64 `document_b64` plus `filename` in the existing JSON body, so:

- no new runtime dependency;
- the existing `BodySizeLimitMiddleware` and RFC 7807 error path apply unchanged;
- embedded mode (the deployed Streamlit Cloud topology) passes raw bytes
  straight to the service with no encoding at all, reusing the route module's
  `build_update_response` mapping so the two modes cannot drift.

## Storage format

`data/knowledge.json` -- one `KnowledgeBase` object, validated on load exactly
like the project bank. `version` is a content hash (the same `compute_version`
helper the bank uses) so a caller can detect drift.

```json
{
  "entries": [
    {"category": "skill", "value": "pytorch", "display": "PyTorch",
     "evidence": "Skills: Python, PyTorch, SQL", "source_id": "a1b2c3d4"}
  ],
  "experience": [{"title": "...", "organisation": "...", "dates": "...", "months": 16}],
  "sources": [{"source_id": "a1b2c3d4", "label": "resume.pdf", "kind": "pdf",
               "characters": 4210, "sha256": "...", "added_at": "2026-01-01T00:00:00Z"}]
}
```
