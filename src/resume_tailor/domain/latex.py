"""LaTeX-safe text handling -- the highest-risk module in the project.

Three separate jobs, deliberately kept as three separate functions because
conflating them is exactly how the original code grew bugs:

1. :func:`escape_latex` -- makes *untrusted* free text safe to interpolate into
   LaTeX source. Never call it on the pre-written bullets in the project bank;
   those contain intentional markup that escaping would mangle.
2. :func:`unescape_latex` / :func:`latex_to_display_text` -- converts LaTeX back
   to plain text for JSON responses and UI display. ``unescape_latex`` is an
   exact inverse of ``escape_latex`` (proven by a Hypothesis round-trip
   property); ``latex_to_display_text`` additionally strips the ``\\textbf{}``
   markup that the bank stores.
3. :func:`find_unknown_commands` / :func:`find_unescaped_specials` /
   :func:`count_unbalanced_braces` -- the primitives behind
   :func:`resume_tailor.render.renderer.audit_source`, a defence-in-depth
   allowlist check over the *fully rendered* document, run immediately before it
   reaches the compiler.
4. :func:`require_href_safe` -- the shape check for the one kind of value that
   cannot be escaped at all: a link target.

Design notes on what changed from the original implementation
-------------------------------------------------------------
The original escaped by looping over a list of (char, replacement) pairs, which
meant a replacement inserted by one iteration could be re-escaped by a later
one. That was patched with a control-character placeholder for the backslash --
a clever fix to a real bug, but the class of bug remained possible for any
future addition to the table, and the placeholder itself was injectable: user
text containing the literal bytes ``\\x00\\x01\\x02`` would be turned into a
backslash.

Both problems disappear if escaping is a **single regex pass** where every
character is consumed exactly once and replacements are never re-examined. That
is what this module does. The placeholder is gone, and so is the bug class.
"""

from __future__ import annotations

import re

# --- escaping ---------------------------------------------------------------

#: Characters LaTeX treats specially, and their safe representations.
ESCAPE_MAP: dict[str, str] = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_ESCAPE_RE = re.compile("[" + re.escape("".join(ESCAPE_MAP)) + "]")

#: Control characters are stripped before escaping. A stray ``\x00`` breaks
#: pdflatex outright, and permitting arbitrary control bytes was the hole that
#: made the old placeholder scheme injectable. Newline and tab survive because
#: they are meaningful whitespace in a summary paragraph.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def strip_control_chars(text: str) -> str:
    """Remove control characters, normalising CRLF to LF first."""
    return _CONTROL_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def escape_latex(text: object) -> str:
    """Escape arbitrary text for safe interpolation into LaTeX source.

    Single-pass: each character is consumed once, so no replacement can be
    re-escaped by a later rule. ``None`` becomes ``""`` and non-strings are
    coerced, because a caller passing an ``int`` should get a sensible resume
    rather than a 500.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return _ESCAPE_RE.sub(lambda match: ESCAPE_MAP[match.group()], strip_control_chars(text))


# --- href targets -----------------------------------------------------------

#: Characters that cannot appear inside an ``\href{}`` argument without either
#: breaking out of it or silently truncating the line. Deliberately a shared
#: constant rather than a check copy-pasted per field.
#:
#: The reason it is shared: this check existed on ``linkedin_url`` and
#: ``github_url`` but not on ``email``, even though the renderer interpolates
#: the email straight into ``\href{mailto:...}``. Escaping is not an option for
#: a link target -- escaping breaks the URL, which is why these fields are
#: shape-validated instead -- so a field that reached ``\href{}`` without this
#: check had no protection at all. Nothing downstream caught it either:
#: ``\href`` is on :data:`ALLOWED_COMMANDS`, so an injected link is by
#: construction invisible to the allowlist audit, and a payload that closes the
#: brace it opens is invisible to the brace-balance check too.
HREF_UNSAFE_CHARS: frozenset[str] = frozenset('{}%#\\ \t\n\r"')


def find_href_unsafe_chars(value: str) -> list[str]:
    """Characters in ``value`` that must never reach an ``\\href{}`` argument."""
    unsafe = {
        char for char in value if char in HREF_UNSAFE_CHARS or ord(char) < 0x20 or ord(char) == 0x7F
    }
    return sorted(unsafe)


def require_href_safe(value: str, kind: str = "value") -> str:
    """Return ``value`` unchanged, or raise ``ValueError`` naming what is wrong."""
    unsafe = find_href_unsafe_chars(value)
    if unsafe:
        raise ValueError(
            f"{kind} contains characters that are unsafe inside \\href{{}}: "
            + ", ".join(repr(char) for char in unsafe)
        )
    return value


# --- unescaping -------------------------------------------------------------

#: Matches exactly one escaped unit. Order matters inside the alternation: the
#: multi-character forms must be tried before the single-character ones, or
#: ``\textbackslash{}`` would be partially consumed.
_UNESCAPE_RE = re.compile(
    r"\\textbackslash\{\}|\\textasciitilde\{\}|\\textasciicircum\{\}|\\([&%$#_{}])"
)

_UNESCAPE_LITERALS = {
    r"\textbackslash{}": "\\",
    r"\textasciitilde{}": "~",
    r"\textasciicircum{}": "^",
}


def unescape_latex(text: str | None) -> str:
    """Exact inverse of :func:`escape_latex`.

    Single-pass for the same reason escaping is: a sequence of independent
    ``str.replace`` calls (what the original did) can re-process its own output.
    ``escape_latex(r"\\textbackslash{}")`` then round-tripped through sequential
    replaces produced ``\\\\`` -- silently wrong, and invisible in a PDF.
    """
    if not text:
        return ""
    return _UNESCAPE_RE.sub(
        lambda m: m.group(1) if m.group(1) else _UNESCAPE_LITERALS[m.group()], text
    )


# --- light markup for user-supplied text ------------------------------------

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.DOTALL)


def apply_light_markup(escaped_text: str) -> str:
    """Turn ``**bold**`` / ``*italic*`` into LaTeX, *after* escaping.

    The original accepted raw LaTeX in the summary override, which is how the
    injection hole (defect C1) got in. Refusing all formatting would have been
    safe but a real feature loss, so instead the text is escaped first -- at
    which point any LaTeX the caller typed is inert literal characters -- and
    only then is a two-command markdown subset translated. ``*`` is not special
    to LaTeX, so this is safe by construction rather than by vigilance.
    """
    with_bold = _BOLD_RE.sub(lambda m: r"\textbf{" + m.group(1) + "}", escaped_text)
    return _ITALIC_RE.sub(lambda m: r"\textit{" + m.group(1) + "}", with_bold)


def escape_user_text(text: object) -> str:
    """Escape untrusted text and re-enable the light markup subset."""
    return apply_light_markup(escape_latex(text))


_MARKUP_RE = re.compile(r"\\text(?:bf|it|rm|sf|tt)\{([^{}]*)\}")
_MAX_MARKUP_PASSES = 10


def strip_markup(text: str) -> str:
    """Remove ``\\textbf{...}``-style wrappers, keeping their contents.

    Applied repeatedly so that nested markup unwraps from the inside out; the
    original single-pass regex silently left the outer wrapper behind on
    ``\\textbf{a \\textit{b}}``.
    """
    for _ in range(_MAX_MARKUP_PASSES):
        new_text = _MARKUP_RE.sub(r"\1", text)
        if new_text == text:
            return new_text
        text = new_text
    return text


def latex_to_display_text(text: str | None) -> str:
    """Convert stored LaTeX markup into plain text for APIs and the UI.

    For *trusted* bank content, which mixes deliberate markup with escaped
    specials. Regression target: raw ``\\&`` leaking into JSON responses.
    """
    if not text:
        return ""
    return unescape_latex(strip_markup(text))


# --- auditing ---------------------------------------------------------------

#: Every control sequence the template and the project bank are allowed to
#: emit. An allowlist rather than a denylist: a denylist silently permits any
#: dangerous primitive nobody thought of, which is the failure mode this audit
#: exists to prevent. Adding legitimate markup to the bank means adding the
#: command here -- deliberately a conscious step, and the error message names
#: the exact command to add.
ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        # document scaffolding
        "documentclass",
        "usepackage",
        "begin",
        "end",
        "pagenumbering",
        "hyphenpenalty",
        "exhyphenpenalty",
        "tolerance",
        "emergencystretch",
        "titleformat",
        "titlespacing",
        "titlerule",
        "setlist",
        "setlength",
        "parskip",
        "parindent",
        "raggedright",
        "raggedbottom",
        # sizing and weight
        "fontsize",
        "selectfont",
        "normalsize",
        "small",
        "footnotesize",
        "large",
        "Large",
        "LARGE",
        "bfseries",
        "itshape",
        "rmfamily",
        # inline markup used by the bank
        "textbf",
        "textit",
        "textrm",
        "textsf",
        "texttt",
        "underline",
        "emph",
        # layout
        "hfill",
        "vspace",
        "hspace",
        "quad",
        "qquad",
        "item",
        "section",
        "subsection",
        "newline",
        "linebreak",
        "noindent",
        "centering",
        # links
        "href",
        "url",
        "hidelinks",
        # escaped specials produced by escape_latex()
        "textbackslash",
        "textasciitilde",
        "textasciicircum",
        "&",
        "%",
        "$",
        "#",
        "_",
        "{",
        "}",
        # ligature/spacing primitives that appear in normal prose
        "ldots",
        "dots",
        "textendash",
        "textemdash",
        "\\",
    }
)

#: ``\command`` or an escaped single character. ``[A-Za-z]+`` matches control
#: words; the second branch matches control symbols such as ``\%`` and ``\\``.
_COMMAND_RE = re.compile(r"\\([A-Za-z]+|.)")

#: Commands that are never acceptable regardless of the allowlist, reported
#: with a sharper message because they indicate an attack rather than a typo.
DANGEROUS_COMMANDS: frozenset[str] = frozenset(
    {
        "input",
        "include",
        "includeonly",
        "write",
        "write18",
        "openout",
        "openin",
        "read",
        "closeout",
        "immediate",
        "special",
        "shipout",
        "catcode",
        "def",
        "gdef",
        "edef",
        "xdef",
        "let",
        "futurelet",
        "csname",
        "endcsname",
        "expandafter",
        "afterassignment",
        "aftergroup",
        "loop",
        "repeat",
        "batchmode",
        "scrollmode",
        "escapechar",
        "endlinechar",
        "newcommand",
        "renewcommand",
        "providecommand",
        "newenvironment",
        "renewenvironment",
        "lowercase",
        "uppercase",
        "InputIfFileExists",
        "IfFileExists",
        "directlua",
        "pdfshellescape",
    }
)


def find_dangerous_commands(text: str) -> list[str]:
    """Return the dangerous LaTeX commands present in ``text``, in order.

    Run against *raw user input*, before escaping. Escaping already renders
    such input inert, so this is not the only line of defence -- it exists so
    that a caller who pastes ``\\input{/etc/passwd}`` gets a clear rejection
    instead of that string silently appearing as body text on their resume.
    """
    found = [name for name in _COMMAND_RE.findall(text) if name in DANGEROUS_COMMANDS]
    return list(dict.fromkeys(found))  # de-duplicate, preserve order


def find_unknown_commands(rendered_tex: str) -> list[str]:
    """Return control sequences in rendered source that are not on the allowlist."""
    found = [name for name in _COMMAND_RE.findall(rendered_tex) if name not in ALLOWED_COMMANDS]
    return list(dict.fromkeys(found))


def find_unescaped_specials(rendered_tex: str) -> list[str]:
    """Heuristic scan of rendered source for escaping bugs that slipped through.

    A bare ``%`` starts a comment and silently swallows the rest of the line --
    the single most common way unescaped user text ruins a resume without
    raising any error at all. Kept from the original code, where it was
    correct but, critically, **never called from anywhere** (defect B8). It is
    now wired into :mod:`resume_tailor.render.renderer`.
    """
    warnings: list[str] = []
    for number, line in enumerate(rendered_tex.split("\n"), start=1):
        stripped = line.replace(r"\%", "").replace(r"\&", "").replace(r"\#", "")
        if "%" in stripped:
            warnings.append(
                f"line {number}: unescaped '%' -- LaTeX will treat the rest of this line "
                "as a comment and silently drop it"
            )
        if "&" in stripped:
            warnings.append(f"line {number}: unescaped '&' -- LaTeX reads this as an alignment tab")
    return warnings


def count_unbalanced_braces(rendered_tex: str) -> int:
    """Net brace depth, ignoring escaped braces. Non-zero means a broken document."""
    depth = 0
    index = 0
    length = len(rendered_tex)
    while index < length:
        char = rendered_tex[index]
        if char == "\\":
            index += 2  # skip the escaped character, whatever it is
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return depth
