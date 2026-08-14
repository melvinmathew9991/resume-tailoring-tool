from latex_utils import escape_latex, validate_no_unescaped_specials, latex_to_display_text


class TestEscapeLatex:
    def test_ampersand(self):
        assert escape_latex("R&D") == r"R\&D"

    def test_percent(self):
        assert escape_latex("50% improvement") == r"50\% improvement"

    def test_underscore(self):
        assert escape_latex("Medical_RAG_Chatbot") == r"Medical\_RAG\_Chatbot"

    def test_dollar(self):
        assert escape_latex("$100 budget") == r"\$100 budget"

    def test_hash(self):
        assert escape_latex("#1 priority") == r"\#1 priority"

    def test_braces(self):
        assert escape_latex("{curly}") == r"\{curly\}"

    def test_tilde(self):
        assert escape_latex("~approx") == r"\textasciitilde{}approx"

    def test_caret(self):
        assert escape_latex("x^2") == r"x\textasciicircum{}2"

    def test_backslash(self):
        # Backslash must not have its own inserted { } re-escaped by the
        # brace-handling pass -- this is the exact bug the placeholder
        # approach in escape_latex() fixes.
        result = escape_latex("path\\to\\file")
        assert result == r"path\textbackslash{}to\textbackslash{}file"
        assert result.count("textbackslash") == 2

    def test_multiple_specials_combined(self):
        result = escape_latex("50% & $100 #1 {test}")
        assert "\\%" in result
        assert "\\&" in result
        assert "\\$" in result
        assert "\\#" in result
        assert "\\{" in result and "\\}" in result

    def test_empty_string(self):
        assert escape_latex("") == ""

    def test_none_input(self):
        assert escape_latex(None) == ""

    def test_non_string_input_coerced(self):
        assert escape_latex(12345) == "12345"

    def test_plain_text_unchanged(self):
        assert escape_latex("Melvin Mathew") == "Melvin Mathew"

    def test_no_double_escaping_backslash_then_special(self):
        # A backslash followed by a character that's ALSO special (e.g. \%)
        # must not become \textbackslash{}\% -- i.e. escaping backslash
        # first should not create new false-positive matches for the
        # other special-char passes to double-hit.
        result = escape_latex("100\\%")
        # backslash becomes \textbackslash{}, then % (now standalone, no
        # longer preceded by the original backslash) becomes \%
        assert result == r"100\textbackslash{}\%"


class TestLatexToDisplayText:
    def test_strips_textbf_wrapper(self):
        assert latex_to_display_text(r"\textbf{Hello}") == "Hello"

    def test_strips_textit_wrapper(self):
        assert latex_to_display_text(r"\textit{Hello}") == "Hello"

    def test_unescapes_ampersand(self):
        assert latex_to_display_text(r"Fair-Lending \& Remediation") == "Fair-Lending & Remediation"

    def test_regression_credit_default_title(self):
        # The exact bug caught via manual API testing: raw LaTeX escaping
        # leaking into a JSON response meant for human/frontend display.
        raw = r"Credit Default Risk Model -- Independent Validation \& Fair-Lending Remediation"
        result = latex_to_display_text(raw)
        assert "\\&" not in result
        assert "&" in result

    def test_unescapes_underscore(self):
        assert latex_to_display_text(r"Medical\_RAG\_Chatbot") == "Medical_RAG_Chatbot"

    def test_empty_string(self):
        assert latex_to_display_text("") == ""

    def test_none_input(self):
        assert latex_to_display_text(None) == ""

    def test_plain_text_unchanged(self):
        assert latex_to_display_text("Plain text, no markup") == "Plain text, no markup"


class TestValidateNoUnescapedSpecials:
    def test_clean_latex_no_warnings(self):
        tex = r"\textbf{Hello} 50\% done \& more"
        assert validate_no_unescaped_specials(tex) == []

    def test_raw_percent_flagged(self):
        tex = "This has a raw 50% sign"
        warnings = validate_no_unescaped_specials(tex)
        assert len(warnings) == 1
        assert "unescaped" in warnings[0].lower()

    def test_escaped_percent_not_flagged(self):
        tex = r"This has an escaped 50\% sign"
        assert validate_no_unescaped_specials(tex) == []

    def test_multiline_reports_correct_line_number(self):
        tex = "line one\nline two has a % raw percent\nline three"
        warnings = validate_no_unescaped_specials(tex)
        assert len(warnings) == 1
        assert "Line 2" in warnings[0]
