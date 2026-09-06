"""Record of which renewal reminders have already gone out.

A policy is reminded once per milestone (30/15/7/3/1 days before expiry).
Without a record the daily cron would message the same customer again on every
run, which is both expensive and the fastest way to get a number's quality
rating downgraded.

The log lives in its own SQLite file rather than messages.db: that database is
the conversation store and is written by the webhook, and keeping the two apart
means a reminder run can never block on a webhook write.
"""

import sqlite3
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "reminders.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_reminders (
        reminder_key TEXT NOT NULL,
        days_before  INTEGER NOT NULL,
        sent_at      INTEGER NOT NULL,
        PRIMARY KEY (reminder_key, days_before)
);
"""


def connect():
        connection = sqlite3.connect(DB_PATH, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA)
        return connection


def reminder_key(number, policy_number):
        """Identify a policy for deduplication.

        The policy number alone is not enough - it is blank on some rows - so
        the customer's number is included, which also keeps two policies held
        by the same person apart.
        """
        return f"{str(number or '').strip()}|{str(policy_number or '').strip()}"


def already_sent(key, days_before):
        with connect() as connection:
                row = connection.execute(
                        "SELECT 1 FROM sent_reminders"
                        " WHERE reminder_key = ? AND days_before = ?",
                        (key, int(days_before)),
                ).fetchone()
        return row is not None


def mark_sent(key, days_before):
        with connect() as connection:
                connection.execute(
                        "INSERT OR IGNORE INTO sent_reminders"
                        " (reminder_key, days_before, sent_at) VALUES (?, ?, ?)",
                        (key, int(days_before), int(time.time())),
                )


def forget(key):
        """Clear a policy's history, so its next renewal cycle reminds again."""
        with connect() as connection:
                connection.execute(
                        "DELETE FROM sent_reminders WHERE reminder_key = ?", (key,)
                )
