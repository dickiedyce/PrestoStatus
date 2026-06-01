"""
PrestoStatus -- Presto firmware.

Connects to WiFi, polls the PrestoStatus macOS server for recent
notifications, and displays them in a scrollable list on the
480x480 touchscreen.

Requires:
    - secrets.py with WIFI_SSID, WIFI_PASSWORD, and SERVER_HOST
    - Roboto-Medium.af font file on the device (ships with firmware)
"""

import gc
import json
import time

import machine
import ntptime
import urequests
from picovector import ANTIALIAS_BEST, PicoVector, Polygon, Transform
from presto import Presto

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SERVER_PORT = 8080
POLL_INTERVAL = 5       # seconds between server polls
MAX_DISPLAY = 10        # max notifications to fetch

HEADER_HEIGHT = 52
CARD_PADDING_X = 12
CARD_PADDING_Y = 8
CARD_SPACING = 4
LINE_HEIGHT_APP = 22
LINE_HEIGHT_BODY = 20
LINE_HEIGHT_META = 18

# ---------------------------------------------------------------------------
# Hardware setup
# ---------------------------------------------------------------------------

presto = Presto(full_res=True, ambient_light=False)
display = presto.display
WIDTH, HEIGHT = display.get_bounds()   # 480, 480

vector = PicoVector(display)
vector.set_antialiasing(ANTIALIAS_BEST)

# PicoVector transform
t = Transform()
vector.set_transform(t)

# Colours
BLACK       = display.create_pen(0, 0, 0)
WHITE       = display.create_pen(255, 255, 255)
DARK_BG     = display.create_pen(18, 18, 22)
HEADER_BG   = display.create_pen(28, 28, 35)
CARD_BG     = display.create_pen(38, 38, 48)
ACCENT      = display.create_pen(88, 166, 255)

CX = WIDTH // 2
CY = HEIGHT // 2
MUTED       = display.create_pen(140, 140, 155)
SEPARATOR   = display.create_pen(55, 55, 65)

PURPLE_LED  = display.create_pen(120, 40, 200)
YELLOW_LED  = display.create_pen(200, 180, 40)

# App accent dot colours  (bundle-id -> RGB)
APP_DOT_COLOURS = {
    "com.tinyspeck.slackmacgap": (79, 24, 190),
    "com.apple.MobileSMS":       (52, 199, 89),
    "com.apple.mail":            (0, 122, 255),
    "com.apple.MobilePhone":     (52, 199, 89),
    "com.apple.reminders":       (255, 149, 0),
    "com.apple.iCal":            (255, 59, 48),
    "com.spotify.client":        (30, 215, 96),
    "com.apple.facetime":        (52, 199, 89),
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
scroll_y = 0.0        # current scroll offset
scroll_target = 0.0   # target scroll offset (lerped)
max_scroll = 0.0      # calculated from content
server_ok = False     # server reachable last poll
wifi_ok = False       # WiFi connected
last_poll = 0         # timestamp of last successful poll
last_touch_y = None   # previous touch Y for drag tracking
touch_prev_state = False
server_url = None     # resolved server IP
server_host = None    # hostname from secrets.py

# Light/dark mode
DARK_MODE = False
MODE_BTN_Y = 10
MODE_BTN_X = WIDTH - 50
MODE_BTN_W = 38
MODE_BTN_H = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def relative_time(ts):
    """Convert a Unix timestamp to a relative string (e.g. '3m ago')."""
    now = int(time.time())
    diff = now - ts
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


def set_leds_purple():
    for i in range(7):
        presto.set_led_rgb(i, 80, 0, 120)


def set_leds_yellow():
    for i in range(7):
        presto.set_led_rgb(i, 120, 100, 0)


def set_leds_ambient(n):
    """Set LED colour based on the most recent notification's dot colour."""
    if n:
        rgb = APP_DOT_COLOURS.get(n[0].get("app", ""), (88, 166, 255))
    else:
        rgb = (40, 40, 50)
    for i in range(7):
        presto.set_led_rgb(i, *rgb)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def wifi_handler(wifi):
    """Called on WiFi connect/fail for status display."""
    pass


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
        else:
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
    """Draw the fixed header bar with title and clock."""
    display.set_pen(HEADER_BG)
    display.rectangle(0, 0, WIDTH, HEADER_HEIGHT)

    vector.set_font("Roboto-Medium.af", 20)
    vector.set_font_letter_spacing(100)
    vector.set_font_word_spacing(100)
    display.set_pen(WHITE)
    vector.text("PrestoStatus", 12, 34)

    # Clock -- top right (BST = UTC+1)
    try:
        t = time.localtime(time.time() + 3600)
        time_str = "{:02d}:{:02d}".format(t[3], t[4])
    except Exception:
        time_str = "--:--"
    vector.set_font("Roboto-Medium.af", 28)
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
    vector.set_font("Roboto-Medium.af", 16)
    vector.set_font_letter_spacing(100)
    vector.set_font_word_spacing(100)
    display.set_pen(MUTED)
    _, _, tw, th = vector.measure_text(msg, x=0, y=0)
    vector.text(msg, (WIDTH - int(tw)) // 2, CY)


def draw_card(n, y, accent_colour):
    """Draw a single notification card. Returns the card height."""
    h = calc_card_height(n)
    card_x = 8
    card_w = WIDTH - 16

    # Card background -- inverted (white)
    display.set_pen(WHITE)
    display.rectangle(card_x, int(y), card_w, h)

    # Accent dot (6px)
    dot_colour = app_dot_pen(n.get("app", ""))
    display.set_pen(dot_colour)
    display.circle(card_x + CARD_PADDING_X + 4, int(y) + CARD_PADDING_Y + 10, 5)

    # Title row at top of card
    title_y = int(y) + CARD_PADDING_Y + 16

    # Relative time -- drawn first at top-right
    rel = relative_time(n.get("timestamp", 0))
    vector.set_font("Roboto-Medium.af", 18)
    vector.set_font_letter_spacing(100)
    vector.set_font_word_spacing(100)
    _, _, tw, _ = vector.measure_text(rel, x=0, y=0)
    display.set_pen(MUTED)
    vector.text(rel, card_x + card_w - CARD_PADDING_X - int(tw), title_y)

    # Title (sender/app name) -- left of time, right of dot
    title = n.get("title", "Unknown")
    if len(title) > 26:
        title = title[:24] + "..."
    vector.set_font("Roboto-Medium.af", 28)
    vector.set_font_letter_spacing(100)
    vector.set_font_word_spacing(100)
    display.set_pen(BLACK)
    vector.text(title, card_x + CARD_PADDING_X + 16, title_y)

    # Body text -- below title
    text_y = title_y + LINE_HEIGHT_APP + 2
    body = n.get("body", "")
    if body:
        vector.set_font("Roboto-Medium.af", 18)
        vector.set_font_letter_spacing(100)
        vector.set_font_word_spacing(100)
        display.set_pen(BLACK)
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
    display.set_pen(DARK_BG)
    display.rectangle(0, HEADER_HEIGHT, WIDTH, HEIGHT - HEADER_HEIGHT)

    y = HEADER_HEIGHT + 6 - scroll_y
    vis_bottom = HEIGHT - 6

    for n in notifs:
        h = calc_card_height(n)

        # Only draw if visible (partially or fully)
        if y + h > HEADER_HEIGHT and y < vis_bottom:
            if notifs.index(n) > 0:
                # Separator line
                display.set_pen(SEPARATOR)
                sep_y = int(y) - CARD_SPACING // 2
                display.rectangle(24, sep_y, WIDTH - 48, 1)

            draw_card(n, y, ACCENT)

        y += h + CARD_SPACING

    # Scroll indicators
    if scroll_y > 5:
        display.set_pen(MUTED)
        vector.set_font("Roboto-Medium.af", 14)
        vector.text("^", CX - 4, HEIGHT - 6)

    # Check if there's content below
    total_height = sum(calc_card_height(n) for n in notifs) + CARD_SPACING * max(0, len(notifs) - 1)
    if total_height > (HEIGHT - HEADER_HEIGHT) and y > vis_bottom:
        display.set_pen(MUTED)
        vector.set_font("Roboto-Medium.af", 14)
        vector.text("v", CX - 4, HEIGHT - 4)


def draw_status(scr):
    """Draw a status-screen message (WiFi/connecting/etc)."""
    display.set_pen(DARK_BG)
    display.clear()

    draw_header()

    # Status message, centered below header
    vector.set_font("Roboto-Medium.af", 20)
    display.set_pen(WHITE)
    _, _, tw, th = vector.measure_text(scr, x=0, y=0)
    vector.text(scr, (WIDTH - int(tw)) // 2, HEADER_HEIGHT + 80)

    presto.update()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    global notifs, scroll_y, scroll_target, max_scroll
    global server_ok, wifi_ok, last_touch_y, touch_prev_state, server_url

    # -- LED startup indicator --
    set_leds_purple()
    presto.auto_ambient_leds(False)

    # -- Show connecting screen --
    draw_status("Connecting to WiFi...")

    # -- Resolve server hostname --
    # Read SERVER_HOST from secrets.py
    server_host = None
    try:
        from secrets import SERVER_HOST
        server_host = SERVER_HOST
    except ImportError:
        draw_status("No secrets.py!\nSee README.")
        while True:
            time.sleep(60)

    if not server_host:
        draw_status("SERVER_HOST is empty\nin secrets.py")
        while True:
            time.sleep(60)

    # -- Connect to WiFi --
    try:
        wifi = presto.connect()
    except ValueError as e:
        draw_status("WiFi error: {}".format(e))
        while True:
            time.sleep(60)
    except ImportError as e:
        draw_status("WiFi config missing:\n{}".format(e))
        while True:
            time.sleep(60)

    wifi_ok = True
    set_leds_yellow()

    # -- Resolve .local hostname to IP --
    import socket
    draw_status("Resolving host...")
    try:
        addr_info = socket.getaddrinfo(server_host, SERVER_PORT)
        server_ip = addr_info[0][-1][0]
        server_url = "{}".format(server_ip)
    except Exception as e:
        server_url = server_host
        print("mDNS resolve failed, using hostname:", e)
        server_url = server_host

    # -- Sync time via NTP --
    draw_status("Syncing time...")
    try:
        ntptime.settime()
    except Exception:
        print("NTP sync failed")

    # -- Initial fetch --
    draw_status("Fetching notifications...")
    result = fetch_notifications()
    if result is not None:
        notifs = result
        set_leds_ambient(notifs)
    else:
        set_leds_yellow()

    # -- Calculate initial max scroll --
    total_height = sum(calc_card_height(n) for n in notifs) + CARD_SPACING * max(0, len(notifs) - 1)
    visible_height = HEIGHT - HEADER_HEIGHT
    max_scroll = max(0, total_height - visible_height)

    # -- Main event loop --
    while True:
        now = time.time()

        # --- Touch input ---
        touch = presto.touch
        touch.poll()
        touched = touch.state
        ty = touch.y

        if touched:
            if last_touch_y is not None:
                delta = last_touch_y - ty
                if abs(delta) > 2:
                    scroll_y = max(0, min(scroll_y + delta, max_scroll))
            last_touch_y = ty
        else:
            last_touch_y = None

        # --- Periodic poll ---
        if now - last_poll >= POLL_INTERVAL:
            result = fetch_notifications()
            if result is not None:
                notifs = result
                set_leds_ambient(notifs)
        elif not server_ok:
            set_leds_yellow()
            if now - last_poll >= POLL_INTERVAL:
                fetch_notifications()

        # --- Recalculate max scroll ---
        total_height = sum(calc_card_height(n) for n in notifs) + CARD_SPACING * max(0, len(notifs) - 1)
        visible_height = HEIGHT - HEADER_HEIGHT
        max_scroll = max(0, total_height - visible_height)
        scroll_target = max(0, min(scroll_target, max_scroll))

        # --- Draw ---
        if not notifs and not server_ok:
            draw_empty_state("Server unreachable")
        elif not notifs:
            draw_empty_state("No notifications")
        else:
            draw_notifs()

        draw_header()
        presto.update()

        # --- Yield ---
        time.sleep_ms(50)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


try:
    main()
except KeyboardInterrupt:
    pass
