"""
PrestoStatus — macOS notification server.

Reads notifications from the macOS Notification Centre database and serves
them as JSON over HTTP for the Presto device to consume.

Requires Full Disk Access granted to the terminal or process running this.
"""

import json
import os
import plistlib
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.path.expanduser(
    "~/Library/Group Containers/group.com.apple.usernoted/db2/db2"
)
SERVER_PORT = 8080
MAX_NOTIFICATIONS = 10
POLL_INTERVAL = 2  # seconds

# ---------------------------------------------------------------------------
# Notification reader
# ---------------------------------------------------------------------------

# CFAbsoluteTime epoch is 2001-01-01 00:00:00 UTC
CFA_INTERVAL = 978_307_200


def read_notifications(limit: int = MAX_NOTIFICATIONS) -> list[dict]:
    """Read latest notifications from the macOS Notification Centre DB.

    Returns a list of dicts sorted newest-first, each containing:
        app       — bundle identifier (e.g. 'com.tinyspeck.slackmacgap')
        title     — notification title (app name or sender)
        body      — notification body text
        timestamp — Unix epoch seconds
    """
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        print(f"[reader] DB open failed: {exc}")
        return []

    try:
        cursor = conn.execute(
            """
            SELECT app, uuid, data, presented, delivered_date
            FROM record
            WHERE presented = 1
            ORDER BY delivered_date DESC
            LIMIT ?
            """,
            (limit,),
        )

        notifications: list[dict] = []
        for row in cursor:
            app_id = row["app"]
            raw_data = row["data"]
            delivered = row["delivered_date"] if row["delivered_date"] else 0

            title = ""
            body = ""
            if raw_data:
                try:
                    plist = plistlib.loads(raw_data)
                    title = plist.get("titl", "") or plist.get("title", "")
                    body = plist.get("body", "")
                    # Fallback: some apps use 'titl' differently
                    if not title:
                        title = app_id.rsplit(".", 1)[-1]
                except Exception:
                    pass

            # Convert CFAbsoluteTime → Unix epoch
            ts = int(delivered + CFA_INTERVAL) if delivered else int(time.time())

            notifications.append(
                {
                    "app": app_id,
                    "title": title,
                    "body": body,
                    "timestamp": ts,
                }
            )

        return notifications

    except sqlite3.OperationalError as exc:
        print(f"[reader] query failed: {exc}")
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class NotificationHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler — serves GET /notifications."""

    def do_GET(self):
        if self.path == "/notifications":
            data = read_notifications()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        # Quieter logging — only log errors
        if args and "404" in str(args[1]):
            super().log_message(fmt, *args)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    # Sanity-check DB access
    if not os.path.exists(DB_PATH):
        print(f"[!] Notification DB not found at {DB_PATH}")
        print("    Grant Full Disk Access to this app in System Settings.")
        return

    server = ThreadingHTTPServer(("0.0.0.0", SERVER_PORT), NotificationHandler)
    print(f"[PrestoStatus] Listening on http://0.0.0.0:{SERVER_PORT}")
    print(f"[PrestoStatus] Polling DB every {POLL_INTERVAL}s")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[PrestoStatus] Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
