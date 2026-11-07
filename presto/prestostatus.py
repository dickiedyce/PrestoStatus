# ICON info
# NAME PrestoStatus
# DESC Recent macOS notifications

"""
PrestoStatus -- Presto firmware.

Connects to WiFi, polls the PrestoStatus macOS server for recent
notifications, and displays them in a scrollable list on the
480x480 touchscreen.

Launched from the Presto launcher: any .py in the root of the filesystem
appears as an icon. The three header lines above give it a name, a
description, and an icon. "info" is one of the icons the stock launcher
already knows about, so no edit to main.py is needed.

Requires:
    - secrets.py with WIFI_SSID, WIFI_PASSWORD and SERVER_HOST
    - Roboto-Medium.af on the device (ships with the firmware)

Optional in secrets.py:
    SERVER_PORT  -- server port, defaults to 8090
    UTC_OFFSET   -- hours from UTC for the header clock, defaults to 0
"""

import gc
import time

import machine
import ntptime
import urequests
from picovector import ANTIALIAS_BEST, PicoVector
from presto import Presto

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SERVER_PORT = 8090      # override via secrets.py SERVER_PORT
POLL_INTERVAL = 30      # seconds between server polls
TOUCH_TIMEOUT = 6       # seconds to wait for a touch before comms error

HEADER_HEIGHT = 52
CARD_PADDING_X = 12
CARD_PADDING_Y = 8
CARD_SPACING = 4
LINE_HEIGHT_APP = 22
LINE_HEIGHT_BODY = 20
LINE_HEIGHT_META = 18

FONT = "Roboto-Medium.af"

# ---------------------------------------------------------------------------
# Hardware setup
# ---------------------------------------------------------------------------

presto = Presto(full_res=True, ambient_light=False)
display = presto.display
WIDTH, HEIGHT = display.get_bounds()   # 480, 480

vector = PicoVector(display)
vector.set_antialiasing(ANTIALIAS_BEST)

# Colours
BLACK       = display.create_pen(0, 0, 0)
WHITE       = display.create_pen(255, 255, 255)
DARK_BG     = display.create_pen(18, 18, 22)
HEADER_BG   = display.create_pen(28, 28, 35)
CARD_BG     = display.create_pen(38, 38, 48)
ACCENT      = display.create_pen(88, 166, 255)
MUTED       = display.create_pen(140, 140, 155)
SEPARATOR   = display.create_pen(55, 55, 65)

CX = WIDTH // 2
CY = HEIGHT // 2

# App accent dot colours  (bundle-id -> RGB)
APP_DOT_COLOURS = {
    "slackmacgap":  (79, 24, 190),
    "MobileSMS":    (52, 199, 89),
    "mail":         (0, 122, 255),
    "MobilePhone":  (52, 199, 89),
    "reminders":    (255, 149, 0),
    "iCal":         (255, 59, 48),
    "spotify":      (30, 215, 96),
    "facetime":     (52, 199, 89),
}


def app_dot_pen(bundle_id):
    """Return a pen for the app accent dot, or a default."""
    rgb = APP_DOT_COLOURS.get(bundle_id)
    if rgb:
        return display.create_pen(*rgb)
    return ACCENT


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

notifs = []           # current notification list
scroll_y = 0          # current scroll offset
max_scroll = 0        # calculated from content
server_ok = False     # server reachable last poll
last_poll = 0         # timestamp of last successful poll
last_touch_y = None   # previous touch Y for drag tracking
server_url = None     # resolved server IP
DARK_MODE = False     # light/dark card style

# Mode toggle button (top-left of header)
MODE_BTN_Y = 6
MODE_BTN_X = 6
MODE_BTN_W = 42
MODE_BTN_H = 36
_mode_touched = False  # debounce: only toggle on first touch


# ---------------------------------------------------------------------------
# Touch helpers
# ---------------------------------------------------------------------------

touch = presto.touch


def wait_for_touch():
    """Idle for a few seconds. Returns True if the screen was touched.

    A tap exits the app back to the launcher; if nobody touches it we drop
    through and keep polling the server, so the display stays live.
    """
    deadline = time.time() + TOUCH_TIMEOUT
    while time.time() < deadline:
        touch.poll()
        if touch.state:
            # Wait for release so the launcher does not see a held touch
            while touch.state:
                time.sleep_ms(20)
                touch.poll()
            return True
        time.sleep_ms(50)
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def relative_time(ts):
    """Convert a Unix timestamp to a relative string (e.g. '3m ago')."""
    try:
        diff = int(time.time()) - ts
    except Exception:
        return "--"
    if diff < 0:
        diff = 0
    if diff < 60:
        return "now"
    if diff < 3600:
        return "{}m".format(diff // 60)
    if diff < 86400:
        return "{}h".format(diff // 3600)
    return "{}d".format(diff // 86400)


def calc_card_height(n):
    """Estimate pixel height of a single notification card."""
    h = CARD_PADDING_Y * 2   # top + bottom padding

    # App label line (title / sender)
    h += LINE_HEIGHT_APP
    h += 2                   # small gap after title

    # Body text -- may wrap; ~42 chars per line at font 14
    body = n.get("body", "")
    if body:
        max_chars = 42
        n_lines = max(1, -(-len(body) // max_chars))  # ceil division
        n_lines = min(n_lines, 3)
        h += n_lines * LINE_HEIGHT_BODY

    # Meta line (app name + time)
    h += LINE_HEIGHT_META + 2

    return h


def content_height():
    """Total pixel height of the notification list."""
    if not notifs:
        return 0
    return (sum(calc_card_height(n) for n in notifs)
            + CARD_SPACING * (len(notifs) - 1))


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def fetch_notifications():
    """Fetch notifications from the server. Returns list or None on error."""
    global server_ok, last_poll
    if not server_url:
        return None
    try:
        gc.collect()
        url = "http://{}:{}/notifications".format(server_url, SERVER_PORT)
        resp = urequests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            resp.close()
            del resp
            gc.collect()
            server_ok = True
            last_poll = time.time()
            return data
        resp.close()
        del resp
        server_ok = False
        return None
    except Exception as e:
        print("Fetch error:", e)
        server_ok = False
        return None


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def draw_header():
    """Draw the fixed header bar with title, clock, and mode toggle."""
    display.set_pen(HEADER_BG)
    display.rectangle(0, 0, WIDTH, HEADER_HEIGHT)

    # Mode toggle button (top-left)
    btn_label = "L" if DARK_MODE else "D"
    display.set_pen(SEPARATOR)
    display.rectangle(MODE_BTN_X, MODE_BTN_Y, MODE_BTN_W, MODE_BTN_H)
    vector.set_font(FONT, 18)
    vector.set_font_letter_spacing(100)
    vector.set_font_word_spacing(100)
    _, _, bw, bh = vector.measure_text(btn_label, x=0, y=0)
    display.set_pen(WHITE)
    vector.text(btn_label, MODE_BTN_X + (MODE_BTN_W - int(bw)) // 2,
                MODE_BTN_Y + (MODE_BTN_H + int(bh)) // 2)

    # Title -- to the right of toggle
    vector.set_font(FONT, 20)
    vector.set_font_letter_spacing(100)
    vector.set_font_word_spacing(100)
    display.set_pen(WHITE)
    vector.text("PrestoStatus", MODE_BTN_X + MODE_BTN_W + 8, 34)

    # Clock -- top right
    try:
        t = time.localtime(time.time() + UTC_OFFSET * 3600)
        time_str = "{:02d}:{:02d}".format(t[3], t[4])
    except Exception:
        time_str = "--:--"
    vector.set_font(FONT, 28)
    _, _, tw, _ = vector.measure_text(time_str, x=0, y=0)
    display.set_pen(MUTED)
    vector.text(time_str, WIDTH - int(tw) - 12, 38)

    # Bottom border
    display.set_pen(SEPARATOR)
    display.rectangle(0, HEADER_HEIGHT - 2, WIDTH, 2)


def draw_empty_state(msg):
    """Draw a centered message when there are no notifications."""
    display.set_pen(DARK_BG)
    display.rectangle(0, HEADER_HEIGHT, WIDTH, HEIGHT - HEADER_HEIGHT)
    vector.set_font(FONT, 16)
    vector.set_font_letter_spacing(100)
    vector.set_font_word_spacing(100)
    display.set_pen(MUTED)
    _, _, tw, _ = vector.measure_text(msg, x=0, y=0)
    vector.text(msg, (WIDTH - int(tw)) // 2, CY)


def draw_card(n, y):
    """Draw a single notification card. Returns the card height."""
    h = calc_card_height(n)
    card_x = 8
    card_w = WIDTH - 16

    # Card background -- light or dark depending on mode
    if DARK_MODE:
        display.set_pen(CARD_BG)
        card_text = WHITE
    else:
        display.set_pen(WHITE)
        card_text = BLACK
    display.rectangle(card_x, int(y), card_w, h)

    # Accent dot (5px radius)
    display.set_pen(app_dot_pen(n.get("app", "")))
    display.circle(card_x + CARD_PADDING_X + 4, int(y) + CARD_PADDING_Y + 10, 5)

    # Title row at top of card
    title_y = int(y) + CARD_PADDING_Y + 16

    # Notification time -- same size as title, right aligned
    rel = relative_time(n.get("timestamp", 0))
    vector.set_font(FONT, 28)
    vector.set_font_letter_spacing(100)
    vector.set_font_word_spacing(100)
    _, _, tw, _ = vector.measure_text(rel, x=0, y=0)
    display.set_pen(card_text if DARK_MODE else display.create_pen(80, 80, 80))
    vector.text(rel, card_x + card_w - CARD_PADDING_X - int(tw), title_y)

    # Title (sender/app name) -- left of time, right of dot
    title = n.get("title", "Unknown")
    if len(title) > 26:
        title = title[:24] + "..."
    display.set_pen(card_text)
    vector.text(title, card_x + CARD_PADDING_X + 16, title_y)

    # Body text -- below title, manually wrapped
    text_y = title_y + LINE_HEIGHT_APP + 2
    body = n.get("body", "")
    if body:
        vector.set_font(FONT, 18)
        vector.set_font_letter_spacing(100)
        vector.set_font_word_spacing(100)
        display.set_pen(card_text)
        text_x = card_x + CARD_PADDING_X + 16
        max_chars = 34  # ~34 chars at size 18 on 480px wide
        line_count = 0
        while body and line_count < 3:
            if len(body) <= max_chars:
                vector.text(body, text_x, text_y)
                break
            split_at = body.rfind(" ", 0, max_chars)
            if split_at <= 0:
                split_at = max_chars
            vector.text(body[:split_at], text_x, text_y)
            body = body[split_at:].lstrip()
            text_y += LINE_HEIGHT_BODY
            line_count += 1

    return h


def draw_notifs():
    """Draw all notification cards."""
    display.set_pen(CARD_BG if DARK_MODE else DARK_BG)
    display.rectangle(0, HEADER_HEIGHT, WIDTH, HEIGHT - HEADER_HEIGHT)

    y = HEADER_HEIGHT + 6 - scroll_y
    vis_bottom = HEIGHT - 6

    for i, n in enumerate(notifs):
        h = calc_card_height(n)

        # Only draw if visible (partially or fully)
        if y + h > HEADER_HEIGHT and y < vis_bottom:
            if i > 0:
                display.set_pen(SEPARATOR)
                sep_y = int(y) - CARD_SPACING // 2
                display.rectangle(24, sep_y, WIDTH - 48, 1)
            draw_card(n, y)

        y += h + CARD_SPACING

    # Scroll indicators
    if scroll_y > 5:
        display.set_pen(MUTED)
        vector.set_font(FONT, 14)
        vector.text("^", CX - 4, HEIGHT - 6)

    if content_height() > (HEIGHT - HEADER_HEIGHT) and y > vis_bottom:
        display.set_pen(MUTED)
        vector.set_font(FONT, 14)
        vector.text("v", CX - 4, HEIGHT - 4)


def draw_status(msg):
    """Draw a status screen message (WiFi / connecting / etc)."""
    display.set_pen(DARK_BG)
    display.clear()
    vector.set_font(FONT, 20)
    vector.set_font_letter_spacing(100)
    vector.set_font_word_spacing(100)
    display.set_pen(WHITE)
    _, _, tw, _ = vector.measure_text(msg, x=0, y=0)
    vector.text(msg, (WIDTH - int(tw)) // 2, CY - 20)
    presto.update()


def draw_frame():
    """Draw the whole UI and push it to the display."""
    if not notifs and not server_ok:
        draw_empty_state("Server unreachable")
    elif not notifs:
        draw_empty_state("No notifications")
    else:
        draw_notifs()
    draw_header()

    # --- Debug overlay (uncomment to show scroll telemetry) ---
    # display.set_pen(BLACK)
    # display.rectangle(0, HEIGHT - 24, WIDTH, 24)
    # vector.set_font(FONT, 14)
    # display.set_pen(ACCENT)
    # vector.text("scr={} max={}".format(scroll_y, max_scroll), 8, HEIGHT - 6)

    presto.update()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run():
    global notifs, scroll_y, max_scroll, server_ok, last_touch_y
    global server_url, SERVER_PORT, UTC_OFFSET, DARK_MODE, _mode_touched

    # -- Optional settings from secrets.py --
    try:
        from secrets import SERVER_HOST
    except ImportError:
        draw_status("No secrets.py - see README")
        while not wait_for_touch():
            pass
        machine.reset()

    try:
        from secrets import SERVER_PORT as _port
        if _port is not None:
            SERVER_PORT = _port
    except (ImportError, AttributeError):
        pass

    try:
        from secrets import UTC_OFFSET as _utc
    except (ImportError, AttributeError):
        _utc = 0
    UTC_OFFSET = _utc

    # -- Connect to WiFi --
    draw_status("Connecting to WiFi...")
    try:
        presto.connect()
    except Exception as e:
        draw_status("WiFi failed - tap to exit")
        print("WiFi error:", e)
        while not wait_for_touch():
            pass
        machine.reset()

    # -- Resolve hostname to an IP so urequests needn't do mDNS --
    import socket
    draw_status("Resolving host...")
    try:
        addr_info = socket.getaddrinfo(SERVER_HOST, SERVER_PORT)
        server_url = addr_info[0][-1][0]
    except Exception as e:
        print("mDNS resolve failed, using hostname:", e)
        server_url = SERVER_HOST

    # -- Sync time via NTP so relative timestamps work --
    draw_status("Syncing time...")
    try:
        ntptime.settime()
    except Exception as e:
        print("NTP sync failed:", e)

    # -- First fetch --
    draw_status("Fetching notifications...")
    result = fetch_notifications()
    if result is not None:
        notifs = result

    max_scroll = max(0, content_height() - (HEIGHT - HEADER_HEIGHT))

    # -- Main loop --
    while True:
        now = time.time()

        # --- Touch input ---
        touch.poll()
        tx = touch.x
        ty = touch.y

        if touch.state:
            in_btn = (MODE_BTN_X <= tx <= MODE_BTN_X + MODE_BTN_W and
                      MODE_BTN_Y <= ty <= MODE_BTN_Y + MODE_BTN_H)
            if last_touch_y is None and in_btn and not _mode_touched:
                DARK_MODE = not DARK_MODE
                _mode_touched = True
            elif not in_btn and last_touch_y is not None:
                delta = last_touch_y - ty
                if delta != 0:
                    scroll_y = max(0, min(scroll_y + delta, max_scroll))
            last_touch_y = ty
        else:
            last_touch_y = None
            _mode_touched = False

        # --- Periodic server poll ---
        if now - last_poll >= POLL_INTERVAL:
            result = fetch_notifications()
            if result is not None:
                notifs = result
            max_scroll = max(0, content_height() - (HEIGHT - HEADER_HEIGHT))
            # Keep the scroll offset inside the newly measured range
            scroll_y = max(0, min(scroll_y, max_scroll))

        # --- Draw ---
        draw_frame()


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

run()
