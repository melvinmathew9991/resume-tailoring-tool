# Resume-Tailoring Automation — Process & Requirements Spec

*Extracted strictly from an actual, long-running resume-tailoring conversation. Every rule below was used, tested, or corrected in real application cycles — nothing here is generic best-practice filler.*

---

## SECTION 1 — RESUME TAILORING WORKFLOW

### Step-by-step process (as actually refined)

1. **Input validation** — Confirm the JD text is complete (not truncated). If a JD is cut off mid-sentence or missing a Requirements/Qualifications section, say so explicitly and ask for the full text rather than guessing at what's missing. Confirm the Project Points document (source of truth) is available before proceeding; if not cached, say so and either re-extract or ask the user to state changes directly.

2. **ATS Score (BEFORE)** — Score the *current* resume against the JD, before any edits. This baseline is required for every net-improvement claim later.

3. **Score Breakdown** — List matched requirements (with which resume element backs each) and gaps, each gap tagged by severity/closability (see Section 2).

4. **Improvement Recommendations** — For each gap: state whether it is (a) closeable using real, existing project evidence, (b) closeable via honest reframing of existing content, or (c) not closeable at all. Never propose closing a gap with unverified content.

5. **Final Edited Resume** — Full rewritten resume (or the specific section requested), applying only recommendations from Step 4.

6. **Applied-Changes Verification** — A table cross-checking each recommendation against what was actually applied, explicitly marking any recommendation that was *not* applied and why (usually: the fact doesn't exist).

7. **ATS Score (AFTER)** — Re-score the edited resume using the *same* method as Step 2. Report net improvement as `Before → After (+delta)`.

### Input types handled

| Input | How processed |
|---|---|
| JD as pasted text | Read directly; if visual-only elements (screenshot) show additional context, use both |
| JD as screenshot/image | Extract responsibilities/qualifications visually |
| Resume as LaTeX source | Edit directly; used as canonical format for all resume construction |
| Resume as PDF | Compile-check via `pdflatex`, render to PNG, visually inspect for overflow/formatting bugs |
| Resume as screenshot(s) | Read text directly from image(s); used for score-checks without needing the underlying source file |
| Project Points document (.docx) | Extract to plain text once per session; **cache the extraction** and diff against it for later updates rather than re-reading in full each time (see Section 3 for the token-optimized update protocol) |

### The anti-fabrication rule (core constraint, non-negotiable)

- **Every claim in every resume must trace to a specific, verifiable fact in the Project Points document.** No metric, tool, or outcome may be invented, estimated, or "reasonably assumed."
- **Reframing is allowed; invention is not.** E.g., relabeling AML's transaction classification as "fraud typology detection" is permitted (same underlying fact, different words matching JD vocabulary); claiming "identity fraud experience" when only transaction-type fraud evidence exists is not.
- **Bare skill-list mentions are a distinct, lower-stakes tier** from project-level claims. A skill studied but never used in a shipped project (e.g., PCA) may appear in a Skills list but must never be implied as project-backed.
- **Never fabricate to close a "required" gap**, even under user pressure. If explicitly instructed to add an unverified claim anyway (e.g., PyTorch before it was genuinely executed), the assistant must: (a) state the risk once clearly, (b) if the user confirms/overrides, comply only in the lowest-risk location (bare Skills mention, never a project bullet), and (c) never claim it as a completed project.
- **When new evidence closes an old gap** (e.g., a project moves from "scripted, unexecuted" to "genuinely deployed"), all standing resume templates using the old, hedged phrasing must be identified and offered for update — stale caveats should not persist once the underlying fact has changed.
- **Corrections must be applied globally, not just where first caught.** If a numeric error or fabricated credential is found once (e.g., a wrong dataset row count, a fake degree on LinkedIn), check whether the same error propagated to other resume versions or platforms and flag all instances.

### Output format rules

- **LaTeX is the canonical resume format.** A fixed, locked template (margins, font size, section spacing) is defined once and reused for every tailored version — never rebuilt from scratch per JD.
- **Hard 2-page constraint**, enforced by actually compiling (tectonic or `pdflatex`, whichever is installed) and reading the page count from the PDF itself (`pypdf`) — never assumed from visual estimation alone.
- **Fixed font size; overflow is reported, not shrunk** *(changed from the original font ladder)*: the format is locked at 9.2pt type on 11.0pt line spacing. If content exceeds 2 pages, the tool still returns the PDF but warns that content must be trimmed; it never ships an over-length document silently. The original rule stepped down five sizes (9.6 → 8.8) until the resume fitted, which guaranteed a fit but meant two resumes generated a week apart could be set at different sizes. Adding sizes back to `font_ladder` in `core/config.py` restores that behaviour.
- **Visual verification step**: after compiling, render to PNG and visually inspect both pages for cramped spacing, orphaned headers, or broken hyperlinks before delivering.
- **Plain-text/part-by-part fallback**: when the user requests low-token-cost iteration, skip compilation entirely and deliver LaTeX source section-by-section (Header/Summary → Experience → Projects → Skills → Certifications/Education) for the user's own review/approval before moving to the next section.
- **Standing content structure** (fixed unless the JD demands deviation):
  - Experience: 5 bullets per role, both roles *(not enforced by the tool: it prints whatever `profile.yaml` holds)*
  - Projects: 5/5/3/3/1 bullet distribution across the 5 selected projects, ordered most-relevant-first *(was 5/5/3/3/3; changed so the resume fits two pages at 9.2pt)*
  - Section order: Summary → Experience → Projects → Skills → Certifications → Education *(the template has no Certifications section yet)*

---

## SECTION 2 — ATS SCORING METHODOLOGY

### Scoring criteria

1. **Keyword/phrase overlap** — Does the resume use the JD's *own* vocabulary (not just a synonym)? Near-verbatim phrase matches score higher than conceptually-similar-but-differently-worded content, since this mirrors how real ATS keyword scanners work.
2. **Required vs. preferred distinction** — Gaps in "required"/"must-have" sections are weighted far more heavily than gaps in "preferred"/"good to have" sections. A JD with many unclosable "preferred" gaps can still score high; one required gap in a short list can cap the score hard.
3. **Structural/qualification gates** — Degree level, years of experience, and location/work-mode requirements are checked as pass/fail gates, separate from skill-keyword scoring, because these are often applied as hard ATS filters independent of content quality.
4. **Formatting compatibility** — Single-column layout, no tables/graphics/text-boxes, standard section headers, clean hyperlinks, confirmed via actual text-extraction test (`pdftotext`) to verify the PDF parses cleanly.
5. **Domain/evidence depth** — A literal domain match (e.g., a healthcare project for a healthcare JD) scores higher than a conceptually-adjacent bridge (e.g., industrial IoT for a healthcare JD), and the assistant states explicitly when a match is a "bridge" versus a literal fit.

### Scoring philosophy

- **Conservative and evidence-based**: score reflects only what can be verified against the Project Points document. When uncertain whether a fact supports a claim, the assistant checks directly (grep/read) rather than assuming.
- **Consistent methodology across before/after**: the same weighting logic is applied both times so the delta is meaningful, not an artifact of inconsistent scoring.
- **Honest ceilings stated explicitly**: when a JD has multiple hard, unclosable gaps (e.g., 6+ named tools with zero evidence), the assistant states a realistic ceiling *before* building the resume, rather than promising a score the content can't honestly reach.
- **No score inflation under pressure**: if a user asks to "push the score higher," the assistant either finds genuine, previously-unused evidence to surface (real ROI) or states plainly that the remaining gap is structural and not resume-fixable.

### Score breakdown structure

```
ATS Score (BEFORE): NN/100

Score Breakdown:
- Matches: [requirement → resume evidence, per item]
- Gaps: [requirement | status (confirmed absent / partial / bridgeable) ]

Improvement Recommendations: [ranked, each tagged closeable/not]

Final Edited Resume: [full LaTeX or requested section]

Applied-Changes Verification: [table: recommendation | applied? | note]

ATS Score (AFTER): NN/100 (+delta)
```

### Before/after and net-improvement reporting

- Always reported as `Before: NN → After: NN (+delta)`.
- The delta must be attributable to specific, listed changes (never a bare number with no explanation).
- If a requested "further optimization" pass yields no genuine new evidence, the assistant states the score is at its honest ceiling rather than inventing marginal justification for a higher number.

---

## SECTION 3 — PROJECT POINTS DOCUMENT USAGE

### What it is and its role

The Project Points document is the **single source of truth** for all factual claims — a running, detailed log of every project (problem, approach, tools, metrics, self-audit findings, honest limitations). It is authoritative over the resume: the resume is a *derived, tailored view* of facts that live in this document, never the other way around.

### Cross-referencing process

- Before adding any claim to a resume, the specific fact is checked against the Project Points document (via targeted search/grep for the claim in question, not a full re-read unless investigating an unknown change).
- When closing a JD gap, the assistant searches the Project Points document specifically for evidence bridging that gap before declaring it closeable — e.g., checking for "GAN," "VAE," "PySpark," or "Databricks" as literal terms before claiming or denying experience.
- New/updated projects are located efficiently: rather than diffing the entire document every time, the assistant asks the user to state what changed directly (fastest), or — if only the file is provided — compares total line count against the last known baseline and targets the changed region (tail/head of the diff) rather than performing a full-document scan.

### Conflict-resolution rule (resume vs. project doc)

**The Project Points document always wins.** If a previously-built resume contains a claim that the Project Points document no longer supports (e.g., an old resume says "cloud deployment scripted, not executed" but the project doc now confirms live deployment), the resume is treated as stale and flagged for update — never the reverse. The assistant does not preserve an old resume's wording out of inertia once the underlying source-of-truth fact has changed.

---

## SECTION 4 — KNOWLEDGE/SKILL UPDATION PROCESS

*Manual. The tool stores candidate knowledge (Profile knowledge) but does no market research or gap-trend analysis; see Section 7.*

### Gap-analysis approach

1. **Evidence-based profile reconstruction** — Consolidate all confirmed skills/tools/projects from the Project Points document and resume history into one grounded summary before any market comparison, so the gap analysis isn't built on assumption.
2. **Current market requirements research** — Live web search for current-year hiring trends in the target role/domain (not relying on internal knowledge alone, since tooling/market demand shifts).
3. **Gap categorization** — Each finding sorted into: (a) skills held and still relevant (keep leveraging), (b) skills held but declining in relevance, (c) critical gaps (in-demand, currently absent), (d) partial gaps (some exposure, needs depth), each further tagged by urgency (needed now vs. needed within ~1–2 years).
4. **Portfolio audit** — Check whether existing projects actually substantiate claimed skills, and separate gaps closeable via a *new project* from those needing direct study/certification.
5. **Prioritized action plan** — Ranked by ROI (hiring impact vs. effort), split into quick wins (days, often just *finishing* something already started) vs. longer-term investments.

### How new projects/skills get incorporated back

- Once a new or updated project exists in the Project Points document, it becomes eligible for inclusion in *future* tailored resumes immediately — it does not require a separate "sync" step, but existing/standing resume templates are **not** automatically retrofitted; the assistant flags the update and asks whether to propagate it into standing templates.
- A new project's suitability for a *specific* JD is judged the same way as any existing project — real keyword/domain overlap, not recency bias (a newer project isn't preferred over an older one unless it's genuinely more relevant).

### Cadence/triggers for re-running analysis

Established triggers in this workflow (not fixed calendar cadence):
- After a new project is completed and logged in the Project Points document.
- After a recurring gap (the same missing skill) appears across 3+ unrelated JD scans — signals a real, worth-closing gap rather than a one-off mismatch.
- On direct user request for a full profile audit (has been run once as a comprehensive exercise combining Sections 1–4 of this spec).
- When a "last hope" / time-pressured job-search phase begins, triggering a shift from project-building time allocation toward application-volume allocation (see Section 5).

---

## SECTION 5 — SUPPORTING WORKFLOWS USED ALONGSIDE THIS

*Manual. None of these workflows are in the tool, and `docs/features/PRD.md` rules them out of scope.*

### Interview prep workflow

- **Grounded in the same three sources**: JD, tailored resume, Project Points document — never generic interview advice.
- **STAR-adjacent answers built from real project specifics**: every mock answer traces to an actual metric, bug, or decision already documented (e.g., "tell me about a time you found a bug after deployment" answered using the real AML post-deployment defect-count finding, not a hypothetical).
- **Explicit gap-disclosure scripting**: for every JD with a known gap, a short, direct, pre-written disclosure sentence is prepared *in advance* so the candidate doesn't improvise under pressure and doesn't either hide or over-apologize for the gap.
- **Multiple-choice + short-essay technical assessment answers** are also grounded this way — e.g., time-series/ML conceptual questions answered by pulling the relevant real project's methodology as the worked example, not textbook generic answers.

### Cross-domain / base-resume optimization approach (LinkedIn Easy Apply, Naukri, etc.)

- A **single non-tailorable resume** is optimized differently from a per-JD tailored one: instead of matching one JD, it's built for **maximum domain diversity** across the industries actually driving current hiring demand (validated via live market research, not assumption).
- Platform-specific formatting differences are respected: e.g., a dense "Key Skills" keyword bar at the top is justified for Naukri (its Resdex algorithm rewards this) but explicitly *not* carried over to a pure-LinkedIn version, since the justification doesn't apply there.
- Explicit tradeoff disclosure is given every time a non-tailored resume is delivered: what's gained (broader pass rate) vs. what's sacrificed (lower score than a JD-specific version) — with a recommendation on when to use which.

### Other recurring processes relevant to the automation tool

- **Token/cost-optimization protocol** (established directly in this conversation): a standing "part by part, no compile, no gap narration unless asked" mode that skips LaTeX compilation and visual rendering entirely for draft iteration, reserving the expensive compile-and-verify step for the final, submission-ready version only.
- **Standing-rule accumulation**: formatting/content decisions made once (font size, margins, bullet-count structure, section order) are locked in as defaults and referenced by name in future requests rather than re-specified — the automation tool should persist these as a versioned config, not re-derive them per session.
- **Referral/cold-outreach drafting**, grounded in the same tailored-resume facts — cover letters, cold emails, and LinkedIn comments all pull from the same verified fact set, kept consistent with whichever resume version accompanies them, and updated together when the underlying project facts change (see Section 3's conflict rule).
- **Explicit "don't apply" / low-ceiling flagging**: for JDs representing a genuinely different job family or specialization (not just a tool gap) — e.g., a computer-vision role for a candidate with zero CV evidence, or a role with a hard degree-eligibility filter — the assistant states a realistic score ceiling *before* building anything, and recommends against investing further effort rather than optimizing a resume that can't honestly close the gap.

---

## SECTION 6 — IMPLEMENTATION-READY SUMMARY

### Spec format for the CLI build

```yaml
inputs:
  jd:
    type: text | image
    validation: must_be_complete  # reject/flag truncated JD text
  resume:
    type: latex_source | pdf | image
    canonical_format: latex
  project_points_doc:
    type: docx | cached_text
    role: single_source_of_truth
    update_protocol: diff_against_last_known_linecount  # not full re-read
  user_directives:
    - token_mode: full | part_by_part_no_compile
    - standing_template: {margins, font_size, line_spacing, section_order}
    - standing_content_structure: {experience_bullets: 5/5, project_bullets: "5/5/3/3/1"}

processing_pipeline:
  1_validate_input: confirm JD completeness; confirm project_points_doc availability
  2_score_before:
      method: keyword_overlap + required_vs_preferred_weighting + structural_gates + formatting_check
      output: score_int, matched_list, gap_list[severity, closability]
  3_recommend:
      rule: NO_FABRICATION — every recommendation must cite a specific project_points_doc fact
      classify_each_gap: closeable_with_existing_evidence | closeable_via_reframe | not_closeable
  4_edit_resume:
      apply: only_step_3_recommendations
      format_rules:
        - two_page_hard_limit
        - compile_and_check_pagecount  # tectonic/pdflatex + pypdf page count, never assume
        - font_ladder: [(9.2,11.0)]  # fixed size; was 9.6 -> 8.8 in five steps
        - fail_loud_on_overflow  # never silently ship overflow; the warning says trim content
        - visual_render_check: compile_to_png + inspect  # skip if token_mode = part_by_part
  5_verify_applied_changes:
      output_table: [recommendation, applied_bool, note_if_not_applied]
  6_score_after:
      method: identical_to_step_2  # for valid delta
      output: score_int, delta

conflict_resolution:
  rule: project_points_doc_always_wins
  trigger: resume_claim_contradicts_current_project_points_doc
  action: flag_stale_claim, offer_update, do_not_silently_preserve_old_wording

gap_analysis_subsystem:
  trigger_conditions:
    - new_project_logged
    - same_missing_skill_seen_across_N>=3_JDs
    - explicit_user_request
    - job_search_phase_change  # e.g. time-pressure shift
  pipeline:
    1_reconstruct_profile: from project_points_doc + resume_history
    2_research_market: live_search, current_year_only
    3_categorize_gaps: [keep, deprioritize, critical_gap, partial_gap] x [now, 1-2yr]
    4_audit_portfolio: does_evidence_exist_for_each_claim
    5_rank_by_roi: quick_wins_first  # esp. "finish what's already started"

output_variants:
  tailored: single_JD_optimized, max_relevant_score
  base_broad: max_domain_diversity, platform_specific_formatting_rules
  supporting_docs: [cover_letter, cold_email, linkedin_comment]
      rule: must_stay_fact-consistent_with_accompanying_resume_version

flags_for_manual_review_not_yet_codified:
  - exact_keyword_matching_algorithm: currently_qualitative_LLM_judgment,
      needs: explicit_method (e.g. fuzzy string match threshold? phrase n-gram overlap?
      weighted by JD section: required vs preferred?)
  - scoring_formula: currently_holistic_LLM_estimate,
      needs: explicit_point_allocation_per_criterion (Section 2) to be reproducible/auditable
  - "domain bridge" vs "literal match" classification: currently_judgment_call,
      needs: explicit rule for what counts as adjacent-enough to claim as evidence
  - font_ladder_thresholds: currently_fixed_list_tuned_by_trial,
      needs: could be dynamic (measure actual overflow amount, compute needed reduction)
      instead of fixed-step trial-and-error
  - "3+ JD gap recurrence" trigger: currently_informal_tracking,
      needs: explicit gap-frequency log across scanned JDs to actually trigger this reliably
  - conflict detection between resume and project_points_doc:
      needs: explicit reconciliation pass (diff claims vs facts) rather than
      relying on the assistant noticing by chance during an unrelated task
```

### What's currently manual/conversational and needs explicit rules to automate

1. **The scoring formula itself** is holistic LLM judgment in this conversation, not a point-weighted rubric. To automate reliably, Section 2's criteria need actual numeric weights (e.g., required-keyword match = X points each, preferred = Y, structural gate failure = hard cap at Z) defined once and applied identically every time.
2. **Keyword-matching method** is currently semantic/contextual (an LLM recognizing "fraud typology" as related to "transaction classification"). A CLI tool needs an explicit method — likely a hybrid of exact-phrase matching (for ATS-realism) plus a controlled synonym/reframe list (to allow legitimate rewording without allowing fabrication).
3. **The "closeable via reframe" vs. "not closeable" judgment** is currently case-by-case reasoning. This needs a concrete rule, e.g.: a claim is reframable only if every content word in the new phrasing maps to a word/fact already present in the project_points_doc entry for that project; anything requiring a *new* fact is fabrication.
4. **Gap-recurrence tracking across JDs** (the "3+ JDs show the same gap" trigger for the learning-plan subsystem) has been done informally by the assistant recalling conversation history. A real tool needs a persistent log of every scored JD's gap list to compute this automatically.
5. **The Project-Points-diff update protocol** (comparing line counts, targeting the tail of the document) is a manual heuristic developed ad hoc in this conversation. A production version should use real diffing (e.g., `git diff` if the doc is version-controlled, or a structured per-project changelog) rather than line-count heuristics.

---

## SECTION 7 — IMPLEMENTATION STATUS (as of 2026-09-11)

How the tool maps to this spec. Sections 1–6 describe the process; this section says which parts the code does.

### Section 6's open questions

| Question | Status |
|---|---|
| Scoring formula | **Answered.** Fixed category weights (skills 0.30, tools 0.20, experience 0.15, responsibilities 0.15, education 0.10, keywords 0.10), credit per requirement (exact 1.0, related 0.5, missing 0), bands at 80/60/40, and a cap at 39 when a required degree or years demand fails. All published in every response (`domain/ats.py`). |
| Keyword-matching method | **Answered.** Literal, whole-word matching plus a spelling-alias table (`ALIASES` in `domain/matching.py`) and a hand-written related-terms table (`domain/vocabulary.py`). No semantic model. |
| Domain bridge vs literal match | **Answered.** `exact` vs `related` (half credit); a related match is scored, never claimed. A multi-word requirement earns partial credit from one of its words only if that word is distinctive: words shared by 3+ vocabulary phrases ("data", "model", "management") don't count on their own. |
| Font-ladder thresholds | **Settled** by removing the ladder: fixed 9.2pt, overflow reported. |
| 3+ JD gap recurrence | **Open.** ATS reports aren't stored, so there is nothing to count across JDs. |
| Resume vs Project Points conflict detection | **Open.** Uploaded documents never write resume text (`profile.yaml` and `project_bank.json` stay hand-edited), but nothing checks that their claims are still backed by the knowledge store. |
| Project Points update protocol | **Partly answered.** Uploading a new version in **Update** mode (`mode=supersede`) removes everything the earlier version with the same filename contributed and adds the new version; other documents are kept. No line-level diff. |

### Section 2 scoring criteria

| Criterion | Status |
|---|---|
| 1. Keyword/phrase overlap | Implemented (literal + aliases). |
| 2. Required vs preferred | Implemented. Headings ("Nice to have", "Preferred qualifications") and in-sentence cues ("is a plus", "preferred") mark a requirement preferred; it counts 0.25 of a required one, and a category listed only as preferred shrinks by the same factor. Anything ambiguous is treated as required. |
| 3. Structural gates | Implemented, for what the posting states as *required*. **Degree level** (bachelor's < master's < doctorate, read from education lines only) and **years of experience** (the largest required figure) are pass/fail; a clear shortfall caps the score at 39, the top of "Weak match". A fact the record doesn't hold is "unverified" and never caps. **Location** is compared city-to-city (with renamed-city aliases such as Bengaluru/Bangalore); a mismatch is flagged for review, never failed, because nothing records whether you'd relocate. A remote role passes. "Or equivalent experience" waives a degree gate. |
| 4. Formatting compatibility | **Implemented.** Every generated PDF is re-opened, its text extracted with `pypdf`, and compared word-for-word against what the spec put on the page; link annotations are checked separately, because a printed link and a clickable one are different objects. Losses are reported by cause -- `ligatures` (a word typeset with an `fi` ligature extracts as one glyph and will not match a keyword scan), `split_words` (a wide kerning pair reads as a word boundary), or genuinely absent text -- since the three have different remedies. A document a parser reads as empty is a failure; ligature and kerning losses are warnings, because the resume is still worth sending. The ligature case was then fixed at source: T1 `fontenc` forces the legacy 8-bit fonts, whose character map reports an `fi` ligature as one glyph, so it is now loaded for pdfTeX only and XeTeX uses its native Unicode Latin Modern. That took the real resume from 96.8% to 99.4% extraction coverage. The single-column, no-table, standard-header part of this criterion is a property of the template rather than something checked per run. |
| 5. Domain/evidence depth | Partly: domain terms are a scored category, and adjacent ("bridge") matches get half credit. |

### Section 1 workflow

| Step | Status |
|---|---|
| 1. Input validation | **Implemented.** Empty and over-length JDs are refused. Completeness is reported, never enforced: a posting is flagged when it ends on a "show more"/ellipsis marker, stops mid-sentence (on a dangling comma or a word that cannot close one), is shorter than a full posting, or states no requirements/qualifications section anywhere. Advisory rather than a rejection, because a truncated posting still ranks and scores honestly against the text it was given -- the point of the warning is that it scores *higher* than the real posting would, since the part that never arrived is the part nobody was measured against. Surfaced on `/match` and `/ats/check` as `posting`, and in both UI views above the results. |
| 2 & 7. Before/after score | No rewrite step, so no delta. The resume score (`/resume/ats`) scores the assembled resume against the JD. |
| 3. Score breakdown | Implemented: each requirement is exact / related / missing, with the candidate term and file that matched, and whether the posting marked it preferred. |
| 4. Recommendations | Partial: the resume score lists `covered_elsewhere` — requirements the resume misses but the candidate's record covers (the "closeable with existing evidence" group). No reframing suggestions. |
| 5. Edited resume | The tool selects and orders projects from pre-verified bullets and never rewrites text, so the anti-fabrication rule holds by construction. |
| 6. Applied-changes table | Not applicable (no rewriting). |
