# PrestoStatus

Display recent macOS notifications on a Pimoroni Presto touchscreen over WiFi.

## Overview

PrestoStatus is a two-component system:

1. **Server** — a macOS daemon that reads the Notification Centre database and serves
   the latest notifications over HTTP.
2. **Presto firmware** — a MicroPython application that connects to WiFi, polls the
   server, and renders a scrollable notification list on the 480x480 touchscreen.

## Quick start

### Server (Mac)

Requires **Full Disk Access** granted to your terminal app (or the launch agent).

```bash
# Test it
cd server
python3 server.py

# Install as launch agent (auto-starts at login)
cd server && ./install.sh
```

### Presto (device)

Flash the [Pimoroni Presto firmware](https://github.com/pimoroni/presto/releases/latest),
then copy the files from `presto/` to the device.

Create a `secrets.py` on the Presto:

```python
WIFI_SSID = "Your Network"
WIFI_PASSWORD = "Your Password"
SERVER_HOST = "mac-mini.local"  # your Mac's .local hostname
```

## API

```
GET http://<SERVER_HOST>:8080/notifications
```

Returns JSON array, most recent first:

```json
[
  {
    "app": "com.tinyspeck.slackmacgap",
    "title": "Alice",
    "body": "Are you free at 3?",
    "timestamp": 1748736000
  }
]
```

## Configuration

| Setting             | Where               | Description                               |
| ------------------- | ------------------- | ----------------------------------------- |
| `WIFI_SSID`         | `presto/secrets.py` | WiFi network name                         |
| `WIFI_PASSWORD`     | `presto/secrets.py` | WiFi password                             |
| `SERVER_HOST`       | `presto/secrets.py` | Mac hostname (`.local`)                   |
| `SERVER_PORT`       | `server/server.py`  | Server port (default `8080`)              |
| `POLL_INTERVAL`     | `presto/main.py`    | Seconds between polls (default `5`)       |
| `MAX_NOTIFICATIONS` | `server/server.py`  | Max notifications to serve (default `10`) |
