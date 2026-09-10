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

import paths

BASE_DIR = paths.BASE_DIR
DB_PATH = paths.data_path("reminders.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_reminders (
        reminder_key  TEXT NOT NULL,
        days_before   INTEGER NOT NULL,
        sent_at       INTEGER NOT NULL,
        name          TEXT NOT NULL DEFAULT '',
        policy_number TEXT NOT NULL DEFAULT '',
        number        TEXT NOT NULL DEFAULT '',
        expiry_text   TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (reminder_key, days_before)
);
"""

# Columns added after the first version shipped; existing rows keep their
# defaults rather than forcing the log to be thrown away.
LATER_COLUMNS = ("name", "policy_number", "number", "expiry_text")


def connect():
        paths.ensure_data_dir()
        connection = sqlite3.connect(DB_PATH, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA)
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(sent_reminders)")}
        for column in LATER_COLUMNS:
                if column not in existing:
                        connection.execute(
                                f"ALTER TABLE sent_reminders ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                        )
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


def mark_sent(key, days_before, details=None):
        details = details or {}
        with connect() as connection:
                connection.execute(
                        "INSERT OR IGNORE INTO sent_reminders"
                        " (reminder_key, days_before, sent_at, name, policy_number,"
                        "  number, expiry_text) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                                key,
                                int(days_before),
                                int(time.time()),
                                str(details.get("name", "")),
                                str(details.get("policyNumber", "")),
                                str(details.get("mobileNumber", "")),
                                str(details.get("expiryText", "")),
                        ),
                )


def recent(limit=50):
        """Most recently sent reminders, newest first."""
        with connect() as connection:
                rows = connection.execute(
                        "SELECT * FROM sent_reminders ORDER BY sent_at DESC, rowid DESC"
                        " LIMIT ?",
                        (int(limit),),
                ).fetchall()
        return [dict(row) for row in rows]


def forget(key):
        """Clear a policy's history, so its next renewal cycle reminds again."""
        with connect() as connection:
                connection.execute(
                        "DELETE FROM sent_reminders WHERE reminder_key = ?", (key,)
                )
