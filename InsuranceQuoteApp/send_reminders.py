#!/usr/bin/env python
"""Daily renewal-reminder run, invoked by cron.

cPanel -> Cron Jobs, once a day (09:00 IST shown here; the server clock may be
on another timezone, so check what 09:00 IST is locally before setting it):

    0 9 * * * /home/USER/virtualenv/APP_PATH/3.11/bin/python \\
        /home/USER/APP_PATH/send_reminders.py >> /home/USER/APP_PATH/reminders.log 2>&1

Cron does not load the cPanel "Setup Python App" environment variables, so the
WhatsApp credentials and GI_DATA_DIR have to be provided here. Point
GI_ENV_FILE at a file of KEY=value lines, or export them in the crontab
itself. Without GI_DATA_DIR the run reads the app directory rather than the
environment's data, and finds no customers.

Only production sends. Dev and production share one WhatsApp number, so a
second cron would message every customer twice; on a non-production
environment this refuses to send unless --force is passed, and --dry-run
always works. Do not install the cron on dev.

Run with --dry-run to see who would be messaged without sending anything.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def load_env_file():
        """Read KEY=value lines from GI_ENV_FILE, if one is configured."""
        env_path = os.environ.get("GI_ENV_FILE", "")
        if not env_path:
                return
        try:
                text = Path(env_path).read_text(encoding="utf-8")
        except OSError as exc:
                print(f"Could not read GI_ENV_FILE: {exc}", file=sys.stderr)
                return

        for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                        continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
        load_env_file()
        sys.path.insert(0, str(BASE_DIR))
        import main as core

        dry_run = "--dry-run" in sys.argv
        force = "--force" in sys.argv

        import paths
        if not paths.IS_PRODUCTION and not dry_run and not force:
                print(f"Refusing to send from the '{paths.ENV_NAME}' environment -"
                      " it shares the production WhatsApp number, so this would"
                      " message real customers a second time. Use --dry-run to"
                      " test, or --force if you really mean it.", file=sys.stderr)
                return 1

        if not core.cloud_api_configured():
                print("WhatsApp Cloud API is not configured - set GI_WA_TOKEN and "
                      "GI_WA_PHONE_NUMBER_ID.", file=sys.stderr)
                return 1

        stamp = datetime.now(core.IST).strftime("%Y-%m-%d %H:%M:%S")
        summary = core.send_expiry_reminders(dry_run=dry_run)
        print(f"[{stamp} IST] sent={summary['sent']} skipped={summary['skipped']} "
              f"failed={summary['failed']}{' (dry run)' if dry_run else ''}")
        for detail in summary["details"]:
                print(f"  {detail}")

        return 1 if summary["failed"] else 0


if __name__ == "__main__":
        sys.exit(main())
