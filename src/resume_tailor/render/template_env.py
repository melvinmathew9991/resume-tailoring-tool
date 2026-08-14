"""LaTeX-safe Jinja2 environment.

``{{ }}`` and ``{% %}`` collide with LaTeX's own pervasive use of braces, which
is a well-documented source of silent template bugs. Non-brace delimiters avoid
the collision entirely -- kept from the original design because it was right.

``autoescape`` stays off: HTML escaping is meaningless for LaTeX, and escaping
is handled explicitly and visibly in :mod:`resume_tailor.render.renderer`.
``undefined=StrictUndefined`` is new -- a typo'd variable name now fails the
render instead of quietly producing a resume with a blank section.
"""

from __future__ import annotations

import functools
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


@functools.lru_cache(maxsize=4)
def get_environment(template_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string=r"\#{",
        comment_end_string="}",
        line_statement_prefix="%%",
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
        autoescape=False,  # noqa: S701 -- LaTeX, not HTML; see module docstring
    )
