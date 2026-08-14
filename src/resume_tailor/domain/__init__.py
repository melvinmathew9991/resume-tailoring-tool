"""Pure domain logic: no I/O, no framework imports, no global state.

Everything in this package is a function of its arguments, which is what makes
it exhaustively testable (and what the coverage gate in ``pyproject.toml``
holds to 100% for :mod:`~resume_tailor.domain.latex` and
:mod:`~resume_tailor.domain.matching`).
"""
