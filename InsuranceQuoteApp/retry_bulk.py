#!/usr/bin/env python
"""Hourly resend of bulk messages WhatsApp held back, invoked by cron.

Meta sometimes accepts a marketing message and then drops it so one person
does not receive too many (error 131049, "...to maintain healthy ecosystem
engagement"). This resends those - and only those - once 24 hours have
passed, at most twice per message. See retry_bulk_failures() in main.py.

cPanel -> Cron Jobs, every hour:

    0 * * * * GI_ENV_FILE=/home/USER/gi.env GI_DATA_DIR=/home/USER/gi-data/production \\
        /home/USER/virtualenv/APP_PATH/3.11/bin/python \\
        /home/USER/APP_PATH/retry_bulk.py >> /home/USER/gi-data/production/retry_bulk.log 2>&1

Like send_reminders.py, cron does not see the cPanel app's environment, so
the credentials come from GI_ENV_FILE and GI_DATA_DIR must be set.

Run with --dry-run to see who would be resent without sending anything.
"""

import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def main():
        sys.path.insert(0, str(BASE_DIR))
        from send_reminders import load_env_file

        load_env_file()
        import main as core

        dry_run = "--dry-run" in sys.argv
        if not core.cloud_api_configured():
                print("WhatsApp Cloud API is not configured - set GI_WA_TOKEN and "
                      "GI_WA_PHONE_NUMBER_ID.", file=sys.stderr)
                return 1

        summary = core.retry_bulk_failures(dry_run=dry_run)
        if not summary["details"]:
                # Hourly and usually idle: stay quiet so the log stays readable.
                return 0

        stamp = datetime.now(core.IST).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{stamp} IST] retried={summary['retried']} failed={summary['failed']} "
              f"gave_up={summary['gaveUp']}{' (dry run)' if dry_run else ''}")
        for detail in summary["details"]:
                print(f"  {detail}")
        return 1 if summary["failed"] else 0


if __name__ == "__main__":
        sys.exit(main())
