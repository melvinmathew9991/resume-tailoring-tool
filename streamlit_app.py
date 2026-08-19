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
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent

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
# The engine comes from packages.txt, which is apt on a host whose base image
# this project does not control. If it is ever missing, `auto` falls back to
# the fake engine and the UI shows a permanent, unmissable warning that the
# PDFs are blank placeholders -- see components.render_engine_status. Naming
# the engine would instead refuse to boot, replacing a degraded public demo
# with no demo at all.
#
# Note this is the opposite of the choice in docker-compose.prod.yml, and for
# a reason: there, a missing toolchain means a broken image and should stop the
# deploy. Here it means a transient apt problem on somebody else's builder.
os.environ.setdefault("RT_PDF_ENGINE", "auto")

os.environ.setdefault("RT_ENVIRONMENT", "local")
os.environ.setdefault("RT_LOG_JSON", "true")

# Importing runs the app: ui/app.py calls main() at module scope, which is the
# Streamlit convention.
import ui.app  # noqa: E402,F401  -- import *is* the invocation
