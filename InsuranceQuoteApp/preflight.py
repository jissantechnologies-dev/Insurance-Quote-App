"""Check that this machine can actually run the app before Passenger reloads.

Run on the server after deploying, and standalone any time something looks
wrong:

        python preflight.py            # human-readable report
        python preflight.py --strict   # also fail on warnings

Exit codes: 0 all good, 1 something is broken, 2 broken only under --strict.

Checks are grouped so a failure names the thing to fix rather than the symptom.
A FAIL means the app will not work; a WARN means a feature is degraded but the
site stays up - the Pillow check is a WARN for exactly that reason, since the
quote image is guarded and everything else keeps working without it.
"""

import argparse
import importlib
import os
import sys
from pathlib import Path

import paths

BASE_DIR = paths.BASE_DIR
DATA_DIR = paths.DATA_DIR

OK, WARN, FAIL = "OK", "WARN", "FAIL"

# Import name -> what stops working without it.
REQUIRED_MODULES = {
        "flask": "the hosted app cannot start at all",
        "openpyxl": "Excel import on Send Quote / Send Payment Link",
}
OPTIONAL_MODULES = {
        "PIL": "the rendered quote PNG (send_quote_image reports it and skips)",
}

# Written by the running server. Deploy must never clobber these, and the app
# needs to be able to write them.
LIVE_DATA = (
        "users.json", "auth_secret.key", "messages.db", "reminders.db",
        "sent_quote.json", "sent_payment.json",
        "customers.json", "newcustomer.txt",
)
LIVE_DIRS = ("documents", "chat_media")

# The July 2026 outage: the parent public_html/.htaccess rewrites non-file
# paths to /index.html for the main React site, and the subdomain docroot sits
# inside it, so every route but / returned 500. These two lines above the
# Passenger block stop the inherited rule. cPanel regenerates .htaccess when
# the Python app is recreated, which silently reintroduces the outage.
HTACCESS_MARKERS = ("RewriteEngine On", "RewriteRule ^ - [L]")


class Report:
        def __init__(self):
                self.rows = []

        def add(self, status, check, detail=""):
                self.rows.append((status, check, detail))

        def counts(self, status):
                return sum(1 for row in self.rows if row[0] == status)

        def render(self):
                width = max(len(check) for _, check, _ in self.rows)
                lines = []
                for status, check, detail in self.rows:
                        line = f"[{status:<4}] {check:<{width}}"
                        if detail:
                                line = f"{line}  {detail}"
                        lines.append(line.rstrip())
                lines.append("")
                lines.append(
                        f"{self.counts(OK)} ok, {self.counts(WARN)} warning(s), "
                        f"{self.counts(FAIL)} failure(s)"
                )
                return "\n".join(lines)


def check_python(report):
        version = ".".join(str(part) for part in sys.version_info[:3])
        # zoneinfo (main.py) landed in 3.9; the host runs a 3.11 virtualenv.
        status = OK if sys.version_info >= (3, 9) else FAIL
        report.add(status, "python", f"{version} at {sys.executable}")


def check_modules(report):
        for name, why in REQUIRED_MODULES.items():
                try:
                        importlib.import_module(name)
                        report.add(OK, f"import {name}")
                except ImportError:
                        report.add(FAIL, f"import {name}",
                                   f"missing - {why}. pip install {name}")

        for name, why in OPTIONAL_MODULES.items():
                try:
                        module = importlib.import_module(name)
                        version = getattr(module, "__version__", "")
                        report.add(OK, f"import {name}", version)
                except ImportError:
                        report.add(WARN, f"import {name}",
                                   f"missing - degrades {why}. pip install -r requirements.txt")


def check_app_modules(report):
        """Import the app itself. Catches a syntax error or a bad import before
        Passenger does, where it would only show as a 500."""
        for name in ("main", "app"):
                try:
                        importlib.import_module(name)
                        report.add(OK, f"import {name}")
                except Exception as exc:
                        report.add(FAIL, f"import {name}", f"{type(exc).__name__}: {exc}")


def check_fonts(report):
        try:
                import quote_image
        except ImportError as exc:
                report.add(WARN, "quote fonts", f"cannot check: {exc}")
                return

        if quote_image.fonts_available():
                report.add(OK, "quote fonts", quote_image._font_path(False))
        else:
                report.add(WARN, "quote fonts",
                           "no TrueType font; put DejaVuSans.ttf and "
                           "DejaVuSans-Bold.ttf in fonts/")


def check_whatsapp(report):
        """Report configuration only - never the token itself."""
        token = os.environ.get("GI_WA_TOKEN", "")
        phone_id = os.environ.get("GI_WA_PHONE_NUMBER_ID", "")

        if token and phone_id:
                report.add(OK, "whatsapp cloud api", f"phone number id {phone_id}")
        else:
                missing = [
                        name for name, value in
                        (("GI_WA_TOKEN", token), ("GI_WA_PHONE_NUMBER_ID", phone_id))
                        if not value
                ]
                report.add(WARN, "whatsapp cloud api",
                           f"{', '.join(missing)} unset - sends fall back to wa.me links")

        for env_name, default in (
                ("GI_WA_TEMPLATE_QUOTE", "quote_share"),
                ("GI_WA_TEMPLATE_PAYMENT", "payment_link_share"),
                ("GI_WA_TEMPLATE_REMINDER", "policy_expiry_reminder"),
                ("GI_WA_TEMPLATE_QUOTE_IMAGE", "quote_image_share"),
        ):
                value = os.environ.get(env_name, default)
                report.add(OK, f"template {env_name.split('TEMPLATE_')[-1].lower()}", value)

        if not os.environ.get("GI_QUOTE_CONTACT", ""):
                report.add(WARN, "quote contact line",
                           "GI_QUOTE_CONTACT unset - the quote PNG footer will be blank")
        else:
                report.add(OK, "quote contact line")


def check_environment(report):
        """Which environment this checkout is serving, and from what data.

        A dev instance pointed at the production data directory is the
        mistake worth catching here - it looks completely normal until a
        test run edits real customers.
        """
        report.add(OK, "environment", paths.ENV_NAME)
        if DATA_DIR == BASE_DIR:
                report.add(
                        WARN if paths.IS_PRODUCTION else OK,
                        "data directory",
                        f"{DATA_DIR} (GI_DATA_DIR unset - data sits in the app dir)",
                )
        else:
                report.add(OK, "data directory", str(DATA_DIR))


def check_writable(report):
        """Live data is written to the data directory, so that directory and
        the files in it must be writable by the Passenger user."""
        if os.access(DATA_DIR, os.W_OK):
                report.add(OK, "data dir writable", str(DATA_DIR))
        elif not DATA_DIR.exists():
                report.add(FAIL, "data dir writable",
                           f"{DATA_DIR} does not exist - create it and chown it")
        else:
                report.add(FAIL, "data dir writable", f"{DATA_DIR} is read-only")

        for name in LIVE_DATA:
                path = DATA_DIR / name
                if not path.exists():
                        continue
                status = OK if os.access(path, os.W_OK) else FAIL
                report.add(status, f"writable {name}")

        for name in LIVE_DIRS:
                path = DATA_DIR / name
                if not path.exists():
                        continue
                status = OK if os.access(path, os.W_OK) else FAIL
                report.add(status, f"writable {name}/")


def check_htaccess(report):
        """Only meaningful on the server; skipped where there is no .htaccess."""
        path = BASE_DIR / ".htaccess"
        if not path.exists():
                path = BASE_DIR.parent / ".htaccess"
        if not path.exists():
                report.add(OK, "htaccess", "none found (local machine)")
                return

        try:
                text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
                report.add(WARN, "htaccess", f"unreadable: {exc}")
                return

        missing = [marker for marker in HTACCESS_MARKERS if marker not in text]
        if missing:
                report.add(FAIL, "htaccess", (
                        f"{path} is missing {missing} - without these the parent "
                        "public_html SPA rewrite makes every route but / return 500"
                ))
        else:
                report.add(OK, "htaccess", "rewrite override present")


def run():
        report = Report()
        check_python(report)
        check_modules(report)
        check_app_modules(report)
        check_fonts(report)
        check_whatsapp(report)
        check_environment(report)
        check_writable(report)
        check_htaccess(report)
        return report


def main(argv=None):
        parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
        parser.add_argument("--strict", action="store_true",
                            help="treat warnings as failures")
        args = parser.parse_args(argv)

        # Imported modules resolve against the app directory wherever this runs.
        sys.path.insert(0, str(BASE_DIR))
        report = run()
        print(report.render())

        if report.counts(FAIL):
                return 1
        if args.strict and report.counts(WARN):
                return 2
        return 0


if __name__ == "__main__":
        raise SystemExit(main())
