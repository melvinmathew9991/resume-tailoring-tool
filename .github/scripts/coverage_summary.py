#!/usr/bin/env python
"""Render coverage.xml as a GitHub step summary.

A separate file rather than a heredoc inside the workflow. Escape sequences in
an inline `python - <<'PY'` block are processed by the shell before Python ever
sees them, so a script that prints a newline escape arrives with real line
breaks in the middle of its string literals -- broken in a way that only shows
up when the job runs. This project has already lost one CI round-trip to a
heredoc mangling its own payload; once is enough.

Stdlib only, so it needs nothing installed beyond the interpreter.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <coverage.xml>", file=sys.stderr)
        return 2

    report = Path(argv[1])
    if not report.is_file():
        print(f"no coverage report at {report}", file=sys.stderr)
        return 1

    root = ElementTree.parse(report).getroot()
    line_rate = float(root.get("line-rate", "0")) * 100
    branch_rate = float(root.get("branch-rate", "0")) * 100

    out: list[str] = ["## Coverage", ""]
    out.append("| line | branch |")
    out.append("| ---: | ---: |")
    out.append(f"| {line_rate:.1f}% | {branch_rate:.1f}% |")
    out.append("")

    partial = sorted(
        (float(node.get("line-rate", "0")) * 100, node.get("filename", "?"))
        for node in root.iter("class")
        if float(node.get("line-rate", "0")) < 1.0
    )
    if partial:
        out.append("<details><summary>Files below 100%</summary>")
        out.append("")
        out.append("| file | line |")
        out.append("| --- | ---: |")
        out.extend(f"| `{name}` | {rate:.1f}% |" for rate, name in partial)
        out.append("")
        out.append("</details>")
    else:
        out.append("Every measured file is at 100%.")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
