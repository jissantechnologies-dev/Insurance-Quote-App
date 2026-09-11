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


def load_env_file():
        """Read KEY=value lines from GI_ENV_FILE into the environment.

        The WhatsApp token and phone number id are set in cPanel for the hosted
        app, which leaves nothing for a local run or a cron job to read - hence
        this file, the same one send_reminders.py has always taken its
        credentials from. Real environment variables win over the file, so an
        export on the command line still overrides it.
        """
        env_path = os.environ.get("GI_ENV_FILE", "").strip()
        if not env_path:
                return
        try:
                text = Path(env_path).expanduser().read_text(encoding="utf-8")
        except OSError:
                # A missing or unreadable env file must not stop the app booting;
                # the features that need those values report their own absence.
                return

        for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                        continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# Before anything below reads os.environ.
load_env_file()


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
