"""Where this instance keeps its live data.

The app used to write every file next to its own code, which meant one
checkout could only ever be one environment. Pointing GI_DATA_DIR at a
directory outside the app root lets the same code serve dev and production
from separate data sets:

    GI_DATA_DIR=/home/USER/gi-data/prod
    GI_DATA_DIR=/home/USER/gi-data/dev

Unset, it falls back to the app directory, so a local checkout keeps
behaving exactly as before and nothing has to be configured to run
`py main.py`.

GI_ENV_NAME is cosmetic - it labels the environment in the preflight report
and in the page header, so a dev tab is never mistaken for production.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def resolve_data_dir():
        """The directory live data is read from and written to."""
        configured = os.environ.get("GI_DATA_DIR", "").strip()
        if not configured:
                return BASE_DIR
        return Path(configured).expanduser().resolve()


DATA_DIR = resolve_data_dir()

# "production" is the default because an unlabelled server is the live one:
# forgetting to set this on dev is a visible mislabel, whereas forgetting it
# on production would silently mark the real site as safe to experiment on.
ENV_NAME = os.environ.get("GI_ENV_NAME", "").strip() or "production"
IS_PRODUCTION = ENV_NAME.lower() in {"production", "prod", "live"}


def data_path(name):
        """Absolute path to a live-data file or directory."""
        return DATA_DIR / name


def ensure_data_dir():
        """Create the data directory on demand.

        Called before the first write rather than at import time so that
        merely importing the app (tests, preflight, tooling) never creates
        stray directories.
        """
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return DATA_DIR
