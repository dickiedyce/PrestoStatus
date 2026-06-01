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
    "~/Library/Group Containers/group.com.apple.usernoted/db2/db"
)
SERVER_PORT = 8080
MAX_NOTIFICATIONS = 10
MAX_AGE_HOURS = 24   # only return notifications from the last N hours
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
        # CFAbsoluteTime window: from N hours ago to 2 hours from now.
        # The upper bound excludes future calendar invitations (which have
        # delivered_date set to the event date, not when they were shown).
        now_cfa = time.time() - CFA_INTERVAL
        cutoff_past = now_cfa - (MAX_AGE_HOURS * 3600)
        cutoff_future = now_cfa + (2 * 3600)
        cursor = conn.execute(
            """
            SELECT a.identifier, r.uuid, r.data, r.delivered_date
            FROM record r
            JOIN app a ON r.app_id = a.app_id
            WHERE r.delivered_date > ?
              AND r.delivered_date < ?
            ORDER BY r.delivered_date DESC
            LIMIT ?
            """,
            (cutoff_past, cutoff_future, limit),
        )

        notifications: list[dict] = []
        for row in cursor:
            app_identifier = row[0] or "unknown"
            raw_data = row[2]
            delivered = row[3] if row[3] else 0

            title = ""
            body = ""
            if raw_data:
                try:
                    plist = plistlib.loads(raw_data)
                    req = plist.get("req", {})
                    # title/body live inside the 'req' sub-dict
                    title = req.get("titl", "") or req.get("title", "")
                    body = req.get("body", "")
                    # body can be a list of strings — join them
                    if isinstance(body, list):
                        body = " ".join(str(b) for b in body)
                    title = str(title)
                    body = str(body)
                    # Fallback: derive a readable name from the bundle ID
                    if not title:
                        title = app_identifier.rsplit(".", 1)[-1]
                except Exception:
                    pass

            # Convert CFAbsoluteTime → Unix epoch
            ts = int(delivered + CFA_INTERVAL) if delivered else int(time.time())

            notifications.append(
                {
                    "app": app_identifier.rsplit(".", 1)[-1],
                    "title": title.strip()[:40],
                    "body": body.strip()[:120],
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
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(body)
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
