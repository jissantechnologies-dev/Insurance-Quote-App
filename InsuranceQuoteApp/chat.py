"""WhatsApp conversation store.

Inbound customer messages and delivery receipts arrive on the webhook in
app.py; outbound messages are recorded when the app sends them. Everything
lands in a small SQLite database - a plain JSON file loses writes when the
webhook fires while a page is saving.

Numbers are stored the way the Cloud API reports them: digits only, with
country code, no '+' (e.g. 919941456453).
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import paths

BASE_DIR = paths.BASE_DIR
DB_PATH = paths.data_path("messages.db")

# WhatsApp only allows free-form replies within 24 hours of the customer's
# last message. Outside it, only an approved template may be sent.
CUSTOMER_WINDOW_SECONDS = 24 * 60 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
        wamid       TEXT PRIMARY KEY,
        number      TEXT NOT NULL,
        direction   TEXT NOT NULL,
        type        TEXT NOT NULL DEFAULT 'text',
        body        TEXT NOT NULL DEFAULT '',
        media_id    TEXT,
        local_path  TEXT NOT NULL DEFAULT '',
        filed_as    TEXT NOT NULL DEFAULT '',
        status      TEXT NOT NULL DEFAULT '',
        error       TEXT NOT NULL DEFAULT '',
        timestamp   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_number ON messages(number, timestamp);
"""


def normalize_number(text):
        digits = "".join(ch for ch in str(text or "") if ch.isdigit())
        if len(digits) == 10:
                return "91" + digits
        return digits


def connect():
        paths.ensure_data_dir()
        connection = sqlite3.connect(DB_PATH, timeout=10)
        connection.row_factory = sqlite3.Row
        # The webhook and the browser can write at the same time; WAL keeps
        # readers from blocking on the writer.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        # Databases created before inbound media was stored lack these columns.
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
        for column in ("local_path", "filed_as"):
                if column not in existing:
                        connection.execute(
                                f"ALTER TABLE messages ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                        )
        return connection


def save_message(wamid, number, direction, body, msg_type="text",
                 media_id=None, status="", timestamp=None):
        """Insert a message, ignoring duplicates.

        Meta retries webhook deliveries, so the same wamid can arrive more
        than once - the primary key makes that a no-op."""
        if not wamid:
                wamid = f"local.{direction}.{time.time()}"
        with connect() as connection:
                connection.execute(
                        "INSERT OR IGNORE INTO messages"
                        " (wamid, number, direction, type, body, media_id, status, timestamp)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                                wamid,
                                normalize_number(number),
                                direction,
                                msg_type,
                                body or "",
                                media_id,
                                status,
                                int(timestamp or time.time()),
                        ),
                )


def update_status(wamid, status, error=""):
        with connect() as connection:
                connection.execute(
                        "UPDATE messages SET status = ?, error = ? WHERE wamid = ?",
                        (status, error, wamid),
                )


def describe_message(message):
        """Flatten one inbound Cloud API message into (type, body, media_id).

        Non-text messages have no body of their own, so they get a readable
        placeholder for the chat list."""
        msg_type = message.get("type", "text")
        if msg_type == "text":
                return "text", message.get("text", {}).get("body", ""), None

        media = message.get(msg_type)
        if isinstance(media, dict):
                caption = media.get("caption", "")
                media_id = media.get("id")
                if msg_type == "button":
                        return "button", media.get("text", ""), None
                if msg_type == "interactive":
                        reply = media.get("button_reply") or media.get("list_reply") or {}
                        return "interactive", reply.get("title", ""), None
                return msg_type, caption or f"[{msg_type}]", media_id

        return msg_type, f"[{msg_type}]", None


def record_webhook(payload):
        """Store inbound messages and delivery receipts from one webhook POST.

        Returns the inbound media that still needs fetching, as a list of
        {wamid, number, media_id, type}. Meta deletes inbound media after 30
        days, so the caller must download it promptly - but downloading here
        would make the webhook slow and drag main.py into this module, so the
        work is handed back to app.py instead."""
        pending = []
        for entry in payload.get("entry", []) or []:
                for change in entry.get("changes", []) or []:
                        value = change.get("value", {}) or {}

                        for message in value.get("messages", []) or []:
                                msg_type, body, media_id = describe_message(message)
                                save_message(
                                        message.get("id"),
                                        message.get("from"),
                                        "in",
                                        body,
                                        msg_type=msg_type,
                                        media_id=media_id,
                                        status="received",
                                        timestamp=message.get("timestamp"),
                                )
                                if media_id:
                                        pending.append({
                                                "wamid": message.get("id"),
                                                "number": message.get("from"),
                                                "media_id": media_id,
                                                "type": msg_type,
                                        })

                        for status in value.get("statuses", []) or []:
                                errors = status.get("errors") or []
                                update_status(
                                        status.get("id"),
                                        status.get("status", ""),
                                        errors[0].get("title", "") if errors else "",
                                )
        return pending


def set_media_file(wamid, local_path):
        """Record where an inbound attachment was saved on disk."""
        with connect() as connection:
                connection.execute(
                        "UPDATE messages SET local_path = ? WHERE wamid = ?",
                        (str(local_path or ""), wamid),
                )


def set_filed_as(wamid, doc_label):
        """Note that an attachment was filed into a customer's documents."""
        with connect() as connection:
                connection.execute(
                        "UPDATE messages SET filed_as = ? WHERE wamid = ?",
                        (str(doc_label or ""), wamid),
                )


def get_message(wamid):
        with connect() as connection:
                row = connection.execute(
                        "SELECT * FROM messages WHERE wamid = ?", (wamid,)
                ).fetchone()
        return dict(row) if row else None


def last_inbound_timestamp(number):
        with connect() as connection:
                row = connection.execute(
                        "SELECT MAX(timestamp) AS ts FROM messages"
                        " WHERE number = ? AND direction = 'in'",
                        (normalize_number(number),),
                ).fetchone()
        return row["ts"] if row and row["ts"] else 0


def window_state(number):
        """Whether a free-form reply is allowed, and how long is left."""
        last = last_inbound_timestamp(number)
        remaining = int(last + CUSTOMER_WINDOW_SECONDS - time.time()) if last else 0
        return {
                "open": remaining > 0,
                "secondsLeft": max(remaining, 0),
                "lastInboundAt": last,
        }


def format_time(timestamp):
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone().strftime(
                "%d-%m-%Y %I:%M %p"
        )


def get_thread(number, limit=200):
        with connect() as connection:
                rows = connection.execute(
                        "SELECT * FROM messages WHERE number = ?"
                        " ORDER BY timestamp ASC, rowid ASC LIMIT ?",
                        (normalize_number(number), limit),
                ).fetchall()

        return [
                {
                        "id": row["wamid"],
                        "direction": row["direction"],
                        "type": row["type"],
                        "body": row["body"],
                        "status": row["status"],
                        "error": row["error"],
                        "at": format_time(row["timestamp"]),
                        "mediaUrl": f"/chat-media/{row['wamid']}" if row["local_path"] else "",
                        "filedAs": row["filed_as"],
                }
                for row in rows
        ]
