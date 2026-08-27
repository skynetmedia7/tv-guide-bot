import os
import threading
import requests
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# ============================================================
# SETTINGS
# ============================================================

TOKEN = os.environ.get("BOT_TOKEN")

EPG_URL = "https://iptv-org.github.io/epg/guides/en/sky.com.xml"

TZ = ZoneInfo("Europe/London")

CHANNELS = {}
PROGRAMMES = {}


# ============================================================
# CHECK TELEGRAM TOKEN
# ============================================================

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing. Add BOT_TOKEN in Render Environment Variables."
    )


# ============================================================
# RENDER WEB SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"TV Guide Bot is running")

    def log_message(self, format, *args):
        return


def start_web_server():

    port = int(os.environ.get("PORT", "10000"))

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"Web server listening on port {port}")

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True
    )

    thread.start()


# ============================================================
# LOAD EPG
# ============================================================

def load_epg():

    global CHANNELS, PROGRAMMES

    print("Loading TV guide...")

    response = requests.get(
        EPG_URL,
        timeout=60
    )

    response.raise_for_status()

    root = ET.fromstring(response.content)

    channels = {}
    programmes = {}

    # --------------------------------------------------------
    # CHANNELS
    # --------------------------------------------------------

    for channel in root.findall("channel"):

        channel_id = channel.get("id")

        name_element = channel.find("display-name")

        if name_element is not None:
            name = name_element.text
        else:
            name = channel_id

        if channel_id and name:
            channels[channel_id] = name.strip()

    # --------------------------------------------------------
    # PROGRAMMES
    # --------------------------------------------------------

    for programme in root.findall("programme"):

        channel_id = programme.get("channel")
        start = programme.get("start")
        stop = programme.get("stop")

        if not channel_id or not start or not stop:
            continue

        title_element = programme.find("title")

        if title_element is not None and title_element.text:
            title = title_element.text.strip()
        else:
            title = "Programme"

        try:

            start_text = start[:19]
            stop_text = stop[:19]

            start_dt = datetime.strptime(
                start_text,
                "%Y%m%d%H%M%S"
            ).replace(tzinfo=timezone.utc)

            stop_dt = datetime.strptime(
                stop_text,
                "%Y%m%d%H%M%S"
            ).replace(tzinfo=timezone.utc)

            start_dt = start_dt.astimezone(TZ)
            stop_dt = stop_dt.astimezone(TZ)

        except Exception:
            continue

        programmes.setdefault(channel_id, []).append(
            {
                "title": title,
                "start": start_dt,
                "stop": stop_dt,
            }
        )

    CHANNELS = channels
    PROGRAMMES = programmes

    print(
        f"EPG loaded: {len(CHANNELS)} channels, "
        f"{sum(len(x) for x in PROGRAMMES.values())} programmes"
    )


# ============================================================
# CHANNEL KEYBOARD
# ============================================================

def channel_keyboard():

    buttons = []

    # Sort channels alphabetically
    sorted_channels = sorted(
        CHANNELS.items(),
        key=lambda x: x[1].lower()
    )

    for channel_id, name in sorted_channels:

        buttons.append(
            [
                InlineKeyboardButton(
                    name,
                    callback_data=f"channel:{channel_id}"
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "🔄 Refresh",
                callback_data="refresh"
            )
        ]
    )

    return InlineKeyboardMarkup(buttons)


# ============================================================
# SHOW CHANNEL PROGRAMMES
# ============================================================

def get_channel_programmes(channel_id):

    now = datetime.now(TZ)

    programmes = PROGRAMMES.get(channel_id, [])

    # Only show programmes around the current time
    current = []

    for programme in programmes:

        start = programme["start"]
        stop = programme["stop"]

        if stop >= now:

            current.append(programme)

        if len(current) >= 12:
            break

    return current


def programme_text(channel_id):

    name = CHANNELS.get(
        channel_id,
        "Unknown channel"
    )

    programmes = get_channel_programmes(channel_id)

    text = f"📺 <b>{name}</b>\n\n"

    if not programmes:

        text += "No programme information available."

        return text

    for programme in programmes:

        start = programme["start"]
        stop = programme["stop"]

        start_text = start.strftime("%H:%M")
        stop_text = stop.strftime("%H:%M")

        title = programme["title"]

        now = datetime.now(TZ)

        if start <= now < stop:
            marker = "▶️ "
        else:
            marker = ""

        text += (
            f"{marker}<b>{start_text} - {stop_text}</b> "
            f"{title}\n"
        )

    return text


# ============================================================
# START COMMAND
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # Load EPG when /start is used
    try:
        if not CHANNELS:
            load_epg()

    except Exception as e:

        print(f"EPG error: {e}")

        await update.message.reply_text(
            "⚠️ TV guide could not be loaded.\n\n"
            "Please try again in a moment."
        )

        return

    await update.message.reply_text(
        "📺 <b>TV GUIDE</b>\n\n"
        "☰ Choose a channel:",
        parse_mode="HTML",
        reply_markup=channel_keyboard()
    )


# ============================================================
# BUTTON HANDLER
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    # --------------------------------------------------------
    # HOME
    # --------------------------------------------------------

    if data == "home":

        await query.edit_message_text(
            "📺 <b>TV GUIDE</b>\n\n"
            "☰ Choose a channel:",
            parse_mode="HTML",
            reply_markup=channel_keyboard()
        )

        return

    # --------------------------------------------------------
    # REFRESH
    # --------------------------------------------------------

    if data == "refresh":

        try:

            load_epg()

            await query.edit_message_text(
                "📺 <b>TV GUIDE</b>\n\n"
                "☰ Choose a channel:",
                parse_mode="HTML",
                reply_markup=channel_keyboard()
            )

        except Exception as e:

            print(f"Refresh error: {e}")

            await query.edit_message_text(
                "⚠️ Something went wrong.\n\n"
                "Please try again.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔄 Try again",
                                callback_data="refresh"
                            )
                        ]
                    ]
                )
            )

        return

    # --------------------------------------------------------
    # CHANNEL
    # --------------------------------------------------------

    if data.startswith("channel:"):

        channel_id = data.split(
            ":",
            1
        )[1]

        if channel_id not in CHANNELS:

            await query.edit_message_text(
                "⚠️ Channel not found.",
                reply_markup=channel_keyboard()
            )

            return

        text = programme_text(channel_id)

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⬅️ Channels",
                        callback_data="home"
                    ),
                    InlineKeyboardButton(
                        "🔄 Refresh",
                        callback_data="refresh"
                    )
                ]
            ]
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard
        )

        return


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "Telegram error:",
        context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "Starting TV Guide Bot..."
    )

    # Start Render web server FIRST
    start_web_server()

    # Create Telegram application
    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    # /start
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # Buttons
    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # Errors
    app.add_error_handler(
        error_handler
    )

    print(
        "TV Guide Bot is running..."
    )

    # Telegram polling
    app.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
