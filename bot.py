import os
import threading
import requests
import xml.etree.ElementTree as ET
import html
import re

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

# UK EPG
EPG_URL = "https://iptv-epg.org/files/epg-gb.xml"

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
# PARSE XMLTV DATE
# ============================================================

def parse_xmltv_time(value):

    if not value:
        return None

    try:
        value = value.strip()

        # First 14 characters are:
        # YYYYMMDDHHMMSS
        base = datetime.strptime(
            value[:14],
            "%Y%m%d%H%M%S"
        )

        # Look for timezone such as +0100 or -0500
        match = re.search(r"([+-])(\d{2})(\d{2})", value[14:])

        if match:

            sign = 1 if match.group(1) == "+" else -1

            hours = int(match.group(2))
            minutes = int(match.group(3))

            offset_seconds = sign * (
                hours * 3600 +
                minutes * 60
            )

            tz = timezone(
                __import__("datetime").timedelta(
                    seconds=offset_seconds
                )
            )

            return base.replace(tzinfo=tz).astimezone(TZ)

        # No timezone supplied - assume UTC
        return base.replace(
            tzinfo=timezone.utc
        ).astimezone(TZ)

    except Exception as e:

        print(
            f"Could not parse EPG time '{value}': {e}"
        )

        return None


# ============================================================
# LOAD EPG - MEMORY EFFICIENT
# ============================================================

def load_epg():

    global CHANNELS, PROGRAMMES

    print("Loading TV guide...")

    now = datetime.now(TZ)

    channels = {}
    programmes = {}

    response = requests.get(
        EPG_URL,
        timeout=90,
        stream=True
    )

    response.raise_for_status()

    print("EPG download started...")

    # --------------------------------------------------------
    # STREAM XML INSTEAD OF LOADING EVERYTHING INTO MEMORY
    # --------------------------------------------------------

    for event, element in ET.iterparse(
        response.raw,
        events=("end",)
    ):

        # ----------------------------------------------------
        # CHANNEL
        # ----------------------------------------------------

        if element.tag == "channel":

            channel_id = element.get("id")

            name_element = element.find("display-name")

            if name_element is not None and name_element.text:
                name = name_element.text.strip()
            else:
                name = channel_id

            if channel_id and name:

                channels[channel_id] = name

            element.clear()

        # ----------------------------------------------------
        # PROGRAMME
        # ----------------------------------------------------

        elif element.tag == "programme":

            channel_id = element.get("channel")
            start_value = element.get("start")
            stop_value = element.get("stop")

            if not channel_id or not start_value or not stop_value:
                element.clear()
                continue

            # Ignore programmes for channels we don't know
            if channel_id not in channels:
                element.clear()
                continue

            start_dt = parse_xmltv_time(start_value)
            stop_dt = parse_xmltv_time(stop_value)

            if start_dt is None or stop_dt is None:
                element.clear()
                continue

            # Ignore programmes that have already finished
            if stop_dt < now:
                element.clear()
                continue

            title_element = element.find("title")

            if title_element is not None and title_element.text:
                title = title_element.text.strip()
            else:
                title = "Programme"

            # Keep only the next 12 programmes per channel
            channel_list = programmes.setdefault(
                channel_id,
                []
            )

            if len(channel_list) < 12:

                channel_list.append(
                    {
                        "title": title,
                        "start": start_dt,
                        "stop": stop_dt,
                    }
                )

            element.clear()

    response.close()

    # Sort each channel's programmes
    for channel_id in programmes:

        programmes[channel_id].sort(
            key=lambda x: x["start"]
        )

    CHANNELS = channels
    PROGRAMMES = programmes

    total = sum(
        len(x)
        for x in PROGRAMMES.values()
    )

    print(
        f"EPG loaded: {len(CHANNELS)} channels, "
        f"{total} upcoming programmes"
    )


# ============================================================
# CHANNEL KEYBOARD
# ============================================================

def channel_keyboard():

    buttons = []

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
# GET CHANNEL PROGRAMMES
# ============================================================

def get_channel_programmes(channel_id):

    now = datetime.now(TZ)

    programmes = PROGRAMMES.get(
        channel_id,
        []
    )

    current = []

    for programme in programmes:

        if programme["stop"] >= now:

            current.append(programme)

        if len(current) >= 12:
            break

    return current


# ============================================================
# PROGRAMME TEXT
# ============================================================

def programme_text(channel_id):

    name = CHANNELS.get(
        channel_id,
        "Unknown channel"
    )

    programmes = get_channel_programmes(
        channel_id
    )

    safe_name = html.escape(name)

    text = (
        f"📺 <b>{safe_name}</b>\n\n"
    )

    if not programmes:

        text += "No programme information available."

        return text

    now = datetime.now(TZ)

    for programme in programmes:

        start = programme["start"]
        stop = programme["stop"]

        start_text = start.strftime("%H:%M")
        stop_text = stop.strftime("%H:%M")

        title = html.escape(
            programme["title"]
        )

        if start <= now < stop:
            marker = "▶️ "
        else:
            marker = ""

        text += (
            f"{marker}"
            f"<b>{start_text} - {stop_text}</b> "
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

    try:

        # Load EPG if it hasn't been loaded yet
        if not CHANNELS:

            load_epg()

    except Exception as e:

        print(
            f"EPG error: {e}"
        )

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

            print(
                f"Refresh error: {e}"
            )

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

        text = programme_text(
            channel_id
        )

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
# POST INIT
# ============================================================

async def post_init(application):

    print(
        "Checking Telegram connection..."
    )

    me = await application.bot.get_me()

    print(
        f"Connected to Telegram as @{me.username}"
    )

    await application.bot.delete_webhook(
        drop_pending_updates=True
    )

    print(
        "Telegram webhook cleared"
    )

    print(
        "Starting polling..."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "========================================"
    )

    print(
        "Starting TV Guide Bot..."
    )

    print(
        "========================================"
    )

    # Render web server
    start_web_server()

    # Telegram application
    app = (
        Application
        .builder()
        .token(TOKEN)
        .post_init(post_init)
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
        "TV Guide Bot is ready"
    )

    # Telegram polling
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
