"""Entrypoint for single-process hosting (Streamlit Community Cloud).

Community Cloud runs one process: `streamlit run <file>`. There is no place to
put the FastAPI service, so the UI has to call the domain layer in-process --
which `ui/client.py` already supports as `RT_UI_MODE=embedded`.

This file exists to *declare* that topology rather than leave it to a setting
somebody has to remember. `RT_UI_MODE` defaults to `http`, so deploying
`ui/app.py` directly would start an app that comes up looking fine and then
fails on its first click, trying to reach an API server that does not exist on
this host. An entrypoint that exists because of single-process hosting is the
right place to say so.

Everything here is `setdefault`, so a real environment variable or a Community
Cloud secret still wins. Nothing is hardcoded that a deployer might need to
override.

This file also installs the LaTeX engine, because on this host nothing else
can -- see `_ensure_tectonic`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent

_log = logging.getLogger("streamlit_app")

# One process, no HTTP hop. Nothing is exposed but Streamlit itself, which is
# why this deployment needs no API key and no CORS allowlist -- there is no
# second service to protect.
os.environ.setdefault("RT_UI_MODE", "embedded")

# Set explicitly rather than relying on the working directory. The package is
# installed into site-packages here, so `Settings._default_data_dir` cannot use
# the checkout layout and falls back to `cwd/data` -- which happens to be right
# on Community Cloud and would be silently wrong under any launcher that runs
# from elsewhere.
os.environ.setdefault("RT_DATA_DIR", str(_REPO_ROOT / "data"))

# `auto` deliberately, not `tectonic`.
#
# `_ensure_tectonic` below fetches the engine over the network, and networks
# fail. If it is missing, `auto` falls back to the fake engine and the UI shows
# a permanent, unmissable warning that the PDFs are blank placeholders -- see
# components.render_engine_status. Naming the engine would instead refuse to
# boot, replacing a degraded public demo with no demo at all.
#
# Note this is the opposite of the choice in docker-compose.prod.yml, and for
# a reason: there, a missing toolchain means a broken image and should stop the
# deploy. Here it means somebody else's host had a bad minute.
os.environ.setdefault("RT_PDF_ENGINE", "auto")

os.environ.setdefault("RT_ENVIRONMENT", "local")
os.environ.setdefault("RT_LOG_JSON", "true")

# ---------------------------------------------------------------------------
# The LaTeX engine
# ---------------------------------------------------------------------------
#
# This exists because `packages.txt` cannot do it. That file used to say
# `tectonic`, on the assumption that apt could install it. It cannot: there has
# never been a `tectonic` package in Debian, in any suite. The deploy failed on
# `E: Unable to locate package tectonic` -- correctly, and it would have failed
# that way on the first attempt whenever it was made.
#
# So the binary comes from the upstream release instead. It is a single static
# musl executable, ~10 MB, with no shared-library dependencies, which is the
# only reason this approach is reasonable rather than a hack. Tectonic still
# downloads the TeX packages the template needs on first compile; that part is
# unchanged and unavoidable.
#
# Everything here is best-effort. Any failure logs and returns, leaving
# `RT_PDF_ENGINE=auto` to fall back to the fake engine with a visible warning.
# A deploy that comes up degraded beats a deploy that does not come up.

_TECTONIC_VERSION = "0.17.0"

#: sha256 of each release tarball, recorded by downloading and hashing it.
#: Upstream publishes no checksums and no signatures, so this is the only
#: integrity check available -- but it is a real one: it pins these bytes, and
#: a tarball that does not match them is not unpacked.
_TECTONIC_TARBALLS = {
    "x86_64": (
        "x86_64-unknown-linux-musl",
        "8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7",
    ),
    "aarch64": (
        "aarch64-unknown-linux-musl",
        "b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8",
    ),
}


def _ensure_tectonic() -> None:
    """Put a `tectonic` binary on PATH, downloading it once if necessary.

    Streamlit re-runs this module on every interaction, so the common case has
    to be cheap: an already-installed binary is a `shutil.which` and nothing
    else.
    """
    if shutil.which("tectonic"):
        return  # already on PATH -- dev machines, containers, warm reruns

    target = _tectonic_target()
    if target is None:
        return

    arch, (triple, expected_sha) = target
    bindir = Path(os.environ.get("RT_TECTONIC_DIR") or Path.home() / ".cache" / "rt-tectonic")
    binary = bindir / "tectonic"

    if not binary.exists():
        try:
            _download_tectonic(triple, expected_sha, binary)
        except Exception as exc:  # deliberately total -- see the note above
            _log.warning(
                "tectonic bootstrap failed (%s); PDFs will be placeholders: %s",
                arch,
                exc,
            )
            return

    os.environ["PATH"] = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"


def _tectonic_target() -> tuple[str, tuple[str, str]] | None:
    """The release to fetch for this machine, or None if there isn't one."""
    if os.name != "posix":
        return None  # Windows dev boxes install it themselves; see the runbook
    arch = platform.machine().lower()
    if arch in ("amd64", "x86-64"):
        arch = "x86_64"
    elif arch == "arm64":
        arch = "aarch64"
    entry = _TECTONIC_TARBALLS.get(arch)
    if entry is None:
        _log.warning("no tectonic release for architecture %r", arch)
        return None
    return arch, entry


def _download_tectonic(triple: str, expected_sha: str, destination: Path) -> None:
    """Fetch, verify and unpack the release tarball to `destination`.

    Verification happens before extraction, not after: an unverified tarball is
    never unpacked. The final move is atomic, so two Streamlit sessions racing
    to install cannot leave a half-written binary on PATH for the other.
    """
    name = f"tectonic-{_TECTONIC_VERSION}-{triple}.tar.gz"
    url = (
        "https://github.com/tectonic-typesetting/tectonic/releases/download/"
        f"tectonic%40{_TECTONIC_VERSION}/{name}"
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as scratch:
        tarball = Path(scratch) / name
        _log.info("downloading %s", url)
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
            tarball.write_bytes(response.read())

        actual_sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"checksum mismatch for {name}: expected {expected_sha}, got {actual_sha}"
            )

        with tarfile.open(tarball) as archive:
            member = archive.getmember("tectonic")
            extracted = Path(scratch) / "tectonic"
            with archive.extractfile(member) as src:  # type: ignore[union-attr]
                extracted.write_bytes(src.read())

        extracted.chmod(0o755)
        os.replace(extracted, destination)

    _log.info("tectonic %s installed at %s", _TECTONIC_VERSION, destination)


_ensure_tectonic()

# Importing runs the app: ui/app.py calls main() at module scope, which is the
# Streamlit convention.
import ui.app  # noqa: E402,F401  -- import *is* the invocation
