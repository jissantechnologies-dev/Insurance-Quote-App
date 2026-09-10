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

Dev and production send from one WhatsApp number and keep separate dedup
logs, so a customer listed in both environments would be reminded twice, once
by each cron. A non-production run therefore compares today's recipients
against the customer list at GI_PEER_DATA_DIR (production's data directory)
and refuses to send if any number appears in both. Lists that do not overlap
send normally, so dev can have its own cron. --force skips the check;
--dry-run reports what it finds and carries on.

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


def check_peer_overlap(core, dry_run):
        """Stop a send that would remind someone the other environment also
        reminds. Returns 0 to carry on, 1 to stop.

        Dev and production send from one WhatsApp number and keep separate
        dedup logs, so neither can see that the other has already messaged a
        customer. Two crons over lists that share a number means that customer
        hears from us twice. Distinct lists are safe, and that is what this
        checks - the environment's name proves nothing either way.

        Production is the authority on who its customers are and is not
        checked. Dev must be told where production's data lives, via
        GI_PEER_DATA_DIR; without it there is nothing to compare against and
        the answer is unknowable rather than "fine".
        """
        import paths

        if paths.IS_PRODUCTION:
                return 0

        # A dry run sends nothing, so it reports what it finds and carries on -
        # it is how you check for an overlap in the first place. The wording
        # follows suit: a dry run is warning, not refusing.
        def stop(problem, remedy):
                lead = "Overlap check (dry run)" if dry_run else "Refusing to send"
                print(f"{lead}: {problem}\n{remedy}", file=sys.stderr)
                return 0 if dry_run else 1

        peer_dir = os.environ.get("GI_PEER_DATA_DIR", "").strip()
        if not peer_dir:
                return stop(
                        f"GI_PEER_DATA_DIR is unset, so there is no way to check\n"
                        "whether this environment's reminders also go out from "
                        "production.",
                        "Point it at production's data directory.",
                )

        try:
                due = core.collect_expiring_customers(core.get_today())
                shared = core.find_shared_recipients(due, peer_dir)
        except core.PeerDataError as exc:
                return stop(
                        f"production's customer list could not be read\n({exc}).",
                        "Fix GI_PEER_DATA_DIR.",
                )

        if not shared:
                return 0

        listed = "\n".join(
                f"  {item['name']} ({item['mobileNumber']}), {item['daysLeft']}d"
                for item in shared
        )
        return stop(
                f"{len(shared)} of today's reminders would go to numbers that\n"
                "production also holds, and both environments send from the same "
                "WhatsApp\nnumber - these customers would be messaged twice:\n"
                f"{listed}",
                "Remove them from this environment's customer list, or use --force.",
        )


def main():
        load_env_file()
        sys.path.insert(0, str(BASE_DIR))
        import main as core

        dry_run = "--dry-run" in sys.argv
        force = "--force" in sys.argv

        if not force and check_peer_overlap(core, dry_run) != 0:
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
