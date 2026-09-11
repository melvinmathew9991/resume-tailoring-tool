# Design — UI for the two new views

Streamlit's default theme, unchanged. This tool is a utility; a custom palette
would be decoration with a maintenance cost. What follows is the small set of
consistent choices the two new views make.

## Navigation

A sidebar `st.radio` labelled **Feature** with three entries:
Tailor resume (default, existing), Profile knowledge, ATS match check.

Radio rather than tabs because each view is a distinct task with its own state
and its own `st.stop()` paths; tabs would run all three bodies on every rerun.
The shared sidebar (backend status, engine, bank version) stays visible in all
three views.

## Semantic colour, reused from the existing app

| Meaning | Widget |
|---|---|
| Success, exact match, ready | `st.success` (green) |
| Caution, related match, does not fit | `st.warning` (amber) |
| Gap, missing requirement, failure | `st.error` (red) |
| Method note, caveat, provenance | `st.caption` (muted) |

The same three-colour vocabulary the tailoring view already uses for the
page-fit result, so a colour means one thing across the whole app.

## Profile knowledge view

1. **Current knowledge** — a metric row (entries, sources, store version), then
   one `st.expander` per category listing entries with their evidence line in a
   caption. The empty state says so plainly and points at step 2.
2. **Add knowledge** — `st.radio` for Upload a document or Paste text, then
   `st.file_uploader` (pdf, docx, txt, md) or `st.text_area`. The update mode is
   a radio defaulting to Merge; Replace everything is second, captioned in red,
   and re-states exactly what it deletes.
3. **Result** — a diff summary: N added, M already known, listed by category, so
   a no-op re-upload visibly reports "nothing new" rather than looking broken.

## ATS match check view

- One `st.text_area` for the JD plus a primary Check match button. No other
  inputs; the feature is one field wide on purpose.
- **Score**: an `st.metric` with the percentage, an `st.progress` bar, and a
  band label (Strong at 80 and above, Moderate at 60, Partial at 40, Weak
  below).
- **Breakdown**: one row per category showing name, weight, category score and
  the exact / related / missing counts, with terms rendered as inline code the
  way `render_gap_terms` already shows terms in the tailoring view.
- **Gaps** get their own section above the fold-out detail, because the missing
  list is the actionable output.
- A permanent `st.caption` carries the methodology note: literal plus alias
  matching, each requirement counted once, not a comprehension score.

## Typography and layout

Streamlit defaults (system sans, and the `st.title` / `st.header` /
`st.caption` hierarchy), with `layout="wide"` as already set. Numbers are
`st.metric`, term lists are inline code, explanations are captions. No custom
CSS and no HTML injection.
