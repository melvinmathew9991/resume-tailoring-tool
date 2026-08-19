"""LaTeX escaping, unescaping and auditing.

The module with the highest ratio of "silently wrong" to "loudly broken" in the
project: a bad escape does not crash, it produces a resume with missing or
mangled text that nobody notices until it has already been sent somewhere.
"""

from __future__ import annotations

import pytest

from resume_tailor.domain.latex import (
    apply_light_markup,
    count_unbalanced_braces,
    escape_latex,
    escape_user_text,
    find_dangerous_commands,
    find_href_unsafe_chars,
    find_unescaped_specials,
    find_unknown_commands,
    latex_to_display_text,
    require_href_safe,
    strip_control_chars,
    strip_markup,
    unescape_latex,
)

pytestmark = pytest.mark.unit


class TestEscapeLatex:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("R&D", r"R\&D"),
            ("50% improvement", r"50\% improvement"),
            ("Medical_RAG_Chatbot", r"Medical\_RAG\_Chatbot"),
            ("$100 budget", r"\$100 budget"),
            ("#1 priority", r"\#1 priority"),
            ("{curly}", r"\{curly\}"),
            ("~approx", r"\textasciitilde{}approx"),
            ("x^2", r"x\textasciicircum{}2"),
            ("Melvin Mathew", "Melvin Mathew"),
            ("", ""),
        ],
    )
    def test_specials(self, raw: str, expected: str) -> None:
        assert escape_latex(raw) == expected

    def test_backslash_does_not_re_escape_its_own_braces(self) -> None:
        # The original escaped by looping over replacement pairs, so the braces
        # inserted by \textbackslash{} were re-escaped by a later iteration.
        result = escape_latex("path\\to\\file")
        assert result == r"path\textbackslash{}to\textbackslash{}file"
        assert result.count("textbackslash") == 2

    def test_backslash_then_special_is_not_double_hit(self) -> None:
        assert escape_latex("100\\%") == r"100\textbackslash{}\%"

    def test_none_and_non_strings_are_coerced(self) -> None:
        assert escape_latex(None) == ""
        assert escape_latex(12345) == "12345"
        assert escape_latex(3.5) == "3.5"

    def test_control_characters_are_stripped(self) -> None:
        assert escape_latex("a\x00b\x07c") == "abc"

    def test_placeholder_bytes_cannot_forge_a_backslash(self) -> None:
        """The old implementation used "\\x00\\x01\\x02" as an internal
        sentinel, so user text containing those bytes was turned into a real
        backslash on the way out. Single-pass escaping has no sentinel."""
        assert "textbackslash" not in escape_latex("\x00\x01\x02")

    def test_crlf_is_normalised(self) -> None:
        assert escape_latex("a\r\nb") == "a\nb"
        assert escape_latex("a\rb") == "a\nb"

    def test_newlines_and_tabs_survive(self) -> None:
        assert escape_latex("a\nb\tc") == "a\nb\tc"

    @pytest.mark.parametrize("text", ["日本語のテキスト", "مرحبا", "emoji 🎯 here"])
    def test_unicode_passes_through(self, text: str) -> None:
        assert escape_latex(text) == text

    def test_output_contains_no_unescaped_specials(self) -> None:
        assert find_unescaped_specials(escape_latex("100% & $5 #3")) == []


class TestUnescapeLatex:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "plain text",
            r"\textbackslash{}",
            "R&D 50% $100 #1 {x} ~ ^ \\",
            r"\textbf{a \textit{b}}",
            "100\\%",
            "\\\\\\\\",
        ],
    )
    def test_round_trip(self, raw: str) -> None:
        assert unescape_latex(escape_latex(raw)) == raw

    def test_single_pass_does_not_reprocess_its_own_output(self) -> None:
        # Sequential str.replace calls turned this into "\\\\" -- silently wrong
        # and invisible in a rendered PDF.
        assert unescape_latex(escape_latex(r"\textbackslash{}")) == r"\textbackslash{}"

    def test_none_and_empty(self) -> None:
        assert unescape_latex(None) == ""
        assert unescape_latex("") == ""


class TestStripMarkup:
    def test_removes_textbf_wrapper(self) -> None:
        assert strip_markup(r"\textbf{Hello}") == "Hello"

    def test_removes_nested_markup_inside_out(self) -> None:
        # The original single-pass regex left the outer wrapper behind.
        assert strip_markup(r"\textbf{a \textit{b}}") == "a b"

    def test_leaves_unknown_commands_alone(self) -> None:
        assert strip_markup(r"\section{Hi}") == r"\section{Hi}"

    def test_terminates_on_pathological_nesting(self) -> None:
        deep = r"\textbf{" * 50 + "x" + "}" * 50
        assert strip_markup(deep).endswith("x" + "}" * 40)


class TestDisplayText:
    def test_strips_markup_and_unescapes(self) -> None:
        assert latex_to_display_text(r"\textbf{Fair-Lending \& Remediation}") == (
            "Fair-Lending & Remediation"
        )

    def test_regression_no_raw_latex_leaks(self) -> None:
        raw = r"Credit Default Risk Model -- Independent Validation \& Fair-Lending Remediation"
        result = latex_to_display_text(raw)
        assert "\\&" not in result
        assert "&" in result

    def test_underscore(self) -> None:
        assert latex_to_display_text(r"Medical\_RAG\_Chatbot") == "Medical_RAG_Chatbot"

    def test_none_and_empty(self) -> None:
        assert latex_to_display_text(None) == ""
        assert latex_to_display_text("") == ""


class TestLightMarkup:
    def test_bold_and_italic(self) -> None:
        assert apply_light_markup("a **b** c") == r"a \textbf{b} c"
        assert apply_light_markup("a *b* c") == r"a \textit{b} c"

    def test_bold_wins_over_italic(self) -> None:
        assert apply_light_markup("**x**") == r"\textbf{x}"

    def test_unpaired_markers_are_left_alone(self) -> None:
        assert apply_light_markup("2 * 3 = 6") == "2 * 3 = 6"

    def test_escape_then_markup_neutralises_injected_latex(self) -> None:
        """The whole point: LaTeX in the input is inert by the time markup runs."""
        result = escape_user_text(r"\textbf{x} and **real bold**")
        assert result == r"\textbackslash{}textbf\{x\} and \textbf{real bold}"

    def test_percent_survives_as_escaped(self) -> None:
        assert escape_user_text("50% done") == r"50\% done"


class TestDangerousCommands:
    @pytest.mark.parametrize(
        "payload",
        [
            r"\input{/etc/passwd}",
            r"\include{secrets}",
            r"\write18{whoami}",
            r"\openout1=x",
            r"\catcode`\%=12",
            r"\def\x{\x\x}",
            r"\csname relax\endcsname",
            r"\newcommand{\evil}{}",
            r"\lowercase{X}",
            r"\directlua{os.execute('id')}",
        ],
    )
    def test_flags_dangerous(self, payload: str) -> None:
        assert find_dangerous_commands(payload)

    @pytest.mark.parametrize(
        "payload", ["plain text", r"\textbf{bold}", "50% and R&D", r"\item one"]
    )
    def test_allows_ordinary_text(self, payload: str) -> None:
        assert find_dangerous_commands(payload) == []

    def test_deduplicates_and_preserves_order(self) -> None:
        found = find_dangerous_commands(r"\write \input \write \def")
        assert found == ["write", "input", "def"]


class TestUnknownCommands:
    def test_allowlisted_commands_pass(self) -> None:
        source = r"\textbf{x} \item y \section*{Z} \href{https://a}{b} \%"
        assert find_unknown_commands(source) == []

    def test_unknown_command_is_reported(self) -> None:
        assert "evilmacro" in find_unknown_commands(r"\evilmacro{x}")

    def test_escaped_specials_are_not_unknown(self) -> None:
        assert find_unknown_commands(escape_latex("& % $ # _ { } ~ ^ \\")) == []


class TestUnescapedSpecials:
    def test_clean_source_has_no_warnings(self) -> None:
        assert find_unescaped_specials(r"\textbf{Hello} 50\% done \& more") == []

    def test_raw_percent_is_flagged_with_a_line_number(self) -> None:
        warnings = find_unescaped_specials("line one\nline two has a % raw percent\nthree")
        assert len(warnings) == 1
        assert "line 2" in warnings[0]

    def test_raw_ampersand_is_flagged(self) -> None:
        assert find_unescaped_specials("a & b")


class TestBraceBalance:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (r"\textbf{a}", 0),
            (r"\textbf{a", 1),
            (r"a}", -1),
            (r"\{ \}", 0),  # escaped braces are not structural
            ("", 0),
            (r"{{{}}}", 0),
        ],
    )
    def test_counts(self, source: str, expected: int) -> None:
        assert count_unbalanced_braces(source) == expected


class TestStripControlChars:
    def test_removes_control_but_keeps_whitespace(self) -> None:
        assert strip_control_chars("a\x00\x1fb\nc\td") == "ab\nc\td"

    def test_removes_delete_character(self) -> None:
        assert strip_control_chars("a\x7fb") == "ab"


class TestHrefSafety:
    r"""The shape check for values that cannot be escaped.

    A link target reaches the document literally, because escaping a URL breaks
    it. That makes ``require_href_safe`` the only thing standing between a
    caller-supplied value and an ``\href{}`` argument -- and the audit behind it
    cannot help, since ``\href`` is an allowed command and a balanced payload
    passes the brace count.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "mailto:me@example.com",
            "https://github.com/owner/repo",
            "https://linkedin.com/in/first-last",
            "mailto:first_last+tag@example.co.uk",
            "https://example.com/path?a=b&c=d",
        ],
    )
    def test_legitimate_targets_pass(self, value: str) -> None:
        assert require_href_safe(value, "target") == value

    @pytest.mark.parametrize(
        ("value", "offender"),
        [
            ("a}{b", "}"),
            (r"x} \href{https://evil.invalid}{hi", "\\"),
            ("a#b", "#"),
            ("a%b", "%"),
            ("has space", " "),
            ('quote"', '"'),
            ("tab\there", "\t"),
            ("line\nbreak", "\n"),
            ("carriage\rreturn", "\r"),
            ("null\x00byte", "\x00"),
        ],
    )
    def test_unsafe_targets_are_named(self, value: str, offender: str) -> None:
        assert offender in find_href_unsafe_chars(value)
        with pytest.raises(ValueError, match="unsafe inside"):
            require_href_safe(value, "target")

    def test_the_error_says_which_field_and_which_character(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            require_href_safe("me}@example.com", "email")
        message = str(excinfo.value)
        assert "email" in message
        assert repr("}") in message

    def test_findings_are_sorted_and_deduplicated(self) -> None:
        found = find_href_unsafe_chars("}}}{{{ ###")
        assert found == sorted(set(found))
