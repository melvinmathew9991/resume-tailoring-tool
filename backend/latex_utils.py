"""
latex_utils.py -- LaTeX-safe text handling.

The project bank stores bullets with LaTeX markup already applied
(\\textbf{}, \\%, etc.) by design -- those are pre-approved, hand-verified
strings and must NOT be re-escaped, or the markup breaks.

This module's job is narrower and more dangerous to get wrong: escaping
USER-SUPPLIED free text (JD text, custom header fields, etc.) that gets
interpolated into the LaTeX template. If this is wrong, LaTeX compilation
either breaks outright or -- worse -- silently swallows content.

Characters LaTeX treats specially: # $ % & _ { } ~ ^ \\
"""
import re

# Backslash uses a placeholder sentinel during the main pass, then gets
# substituted in as the LAST step. Without this, escaping backslash
# first (as "\textbackslash{}") would have its own { and } chars
# re-escaped by the brace-handling entries later in this same loop --
# a real bug caught by test_backslash / test_no_double_escaping.
# Pure control characters only -- deliberately contains none of the
# characters in _LATEX_SPECIAL_CHARS below (a placeholder using printable
# text, e.g. containing an underscore, would get re-escaped by the very
# rules it's meant to survive -- a real bug caught by testing).
_BACKSLASH_PLACEHOLDER = "\x00\x01\x02"

_LATEX_SPECIAL_CHARS = [
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
]


def escape_latex(text: str) -> str:
    """Escape a plain-text string for safe interpolation into LaTeX source.

    Only call this on untrusted/free-form input (e.g. a user-typed name
    field). Never call this on the pre-written bullet strings in
    project_bank.json -- those already contain deliberate LaTeX markup
    that this function would mangle.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)

    # Step 1: swap literal backslashes for a placeholder that contains
    # no characters any later step will touch.
    result = text.replace("\\", _BACKSLASH_PLACEHOLDER)

    # Step 2: escape every other special character normally.
    for char, replacement in _LATEX_SPECIAL_CHARS:
        result = result.replace(char, replacement)

    # Step 3: swap the placeholder for the final LaTeX-safe backslash
    # representation -- done last so its own { and } are untouched.
    result = result.replace(_BACKSLASH_PLACEHOLDER, r"\textbackslash{}")

    return result


def escape_latex_preserve_markup(text: str) -> str:
    """Escape free text that may ALSO legitimately contain a small,
    known allow-list of LaTeX commands we intentionally emit ourselves
    (\\textbf{...}, \\textit{...}). Used for bullet text that's a mix of
    pre-approved markup and needs no further escaping -- this is the
    identity function, documented explicitly so it's clear the decision
    not to escape is deliberate, not an oversight.
    """
    return text


def latex_to_display_text(text: str) -> str:
    """Converts a string containing LaTeX markup/escapes (as stored in
    project_bank.json, meant for compilation) into plain, human-readable
    text suitable for API responses / frontend display -- NOT for
    re-compilation.

    This is intentionally a small, known set of substitutions covering
    what actually appears in this project's data (bold markup and the
    handful of escaped special characters used in titles), not a full
    LaTeX-to-text parser.
    """
    if not text:
        return ""
    result = text
    # Strip \textbf{...} / \textit{...} wrappers, keeping their contents.
    result = re.sub(r"\\text(bf|it)\{([^{}]*)\}", r"\2", result)
    # Un-escape the special characters escape_latex() (or manual markup
    # in project_bank.json) would have escaped.
    unescape_map = [
        (r"\&", "&"), (r"\%", "%"), (r"\$", "$"), (r"\#", "#"),
        (r"\_", "_"), (r"\{", "{"), (r"\}", "}"),
        (r"\textasciitilde{}", "~"), (r"\textasciicircum{}", "^"),
        (r"\textbackslash{}", "\\"),
    ]
    for esc, plain in unescape_map:
        result = result.replace(esc, plain)
    return result


def validate_no_unescaped_specials(rendered_tex: str) -> list:
    """Sanity check on fully-rendered LaTeX source: look for characters
    that are almost always a sign of an escaping bug slipping through
    (a raw unescaped % or & outside of known-safe contexts). Returns a
    list of warning strings; empty list means nothing suspicious found.

    This is intentionally a heuristic safety net, not a LaTeX parser --
    it exists to catch regressions, not to be exhaustive.
    """
    warnings = []
    # A raw % not preceded by a backslash starts a LaTeX comment and
    # silently truncates the rest of the line -- the single most common
    # way unescaped user text breaks a resume.
    for i, line in enumerate(rendered_tex.split("\n"), start=1):
        # strip already-escaped \% before scanning
        scan_line = line.replace(r"\%", "")
        if "%" in scan_line:
            warnings.append(f"Line {i}: unescaped '%' found -- LaTeX will treat the rest of this line as a comment.")
    return warnings
