import os
import threading
import requests
import xml.etree.ElementTree as ET
import html

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)

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

# Working Sky EPG mirror
EPG_URL = "https://xmltv.tvkaista.net/guides/sky.com.xml"

TZ = ZoneInfo("Europe/London")

# Number of channels displayed on each Telegram page
CHANNELS_PER_PAGE = 30

CHANNELS = {}
PROGRAMMES = {}

CHANNEL_LIST = []


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

        self.send_header(
            "Content-type",
            "text/plain"
        )

        self.end_headers()

        self.wfile.write(
            b"TV Guide Bot is running"
        )

    def log_message(self, format, *args):
        return


def start_web_server():

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    server = ThreadingHTTPServer(
        (
            "0.0.0.0",
            port
        ),
        HealthHandler
    )

    print(
        f"Web server listening on port {port}"
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True
    )

    thread.start()


# ============================================================
# PARSE XMLTV DATE
# ============================================================

def parse_xmltv_datetime(value):

    if not value:
        return None

    value = value.strip()

    try:

        # XMLTV format normally looks like:
        #
        # 20260827080000 +0100
        #
        # or:
        #
        # 20260827080000

        if len(value) >= 19:

            date_part = value[:14]

            remaining = value[14:].strip()

            dt = datetime.strptime(
                date_part,
                "%Y%m%d%H%M%S"
            )

            if remaining:

                # Handle +0100 / -0500
                if (
                    remaining.startswith("+")
                    or remaining.startswith("-")
                ):

                    sign = (
                        1
                        if remaining[0] == "+"
                        else -1
                    )

                    offset_text = remaining[1:5]

                    hours = int(
                        offset_text[:2]
                    )

                    minutes = int(
                        offset_text[2:4]
                    )

                    offset_seconds = (
                        sign
                        * (
                            hours * 3600
                            + minutes * 60
                        )
                    )

                    from datetime import timedelta

                    dt = dt.replace(
                        tzinfo=timezone(
                            timedelta(
                                seconds=offset_seconds
                            )
                        )
                    )

                    return dt.astimezone(TZ)

            # If no offset was supplied,
            # treat it as UTC.
            dt = dt.replace(
                tzinfo=timezone.utc
            )

            return dt.astimezone(TZ)

    except Exception as e:

        print(
            f"Date parse error: {value} - {e}"
        )

    return None


# ============================================================
# LOAD EPG
# ============================================================

def load_epg():

    global CHANNELS
    global PROGRAMMES
    global CHANNEL_LIST

    print("Loading TV guide...")

    response = requests.get(
        EPG_URL,
        timeout=120,
        headers={
            "User-Agent": "TV-Guide-Bot/1.0"
        }
    )

    response.raise_for_status()

    print(
        f"EPG downloaded: {len(response.content)} bytes"
    )

    root = ET.fromstring(
        response.content
    )

    channels = {}
    programmes = {}

    # ========================================================
    # CHANNELS
    # ========================================================

    for channel in root.findall("channel"):

        channel_id = channel.get("id")

        if not channel_id:
            continue

        display_names = channel.findall(
            "display-name"
        )

        name = None

        if display_names:

            for element in display_names:

                if element.text:

                    name = element.text.strip()

                    if name:
                        break

        if not name:
            name = channel_id

        channels[channel_id] = name

    # ========================================================
    # PROGRAMMES
    # ========================================================

    programme_count = 0

    for programme in root.findall(
        "programme"
    ):

        channel_id = programme.get(
            "channel"
        )

        start = programme.get(
            "start"
        )

        stop = programme.get(
            "stop"
        )

        if not channel_id:
            continue

        if not start or not stop:
            continue

        start_dt = parse_xmltv_datetime(
            start
        )

        stop_dt = parse_xmltv_datetime(
            stop
        )

        if not start_dt or not stop_dt:
            continue

        title_element = programme.find(
            "title"
        )

        if (
            title_element is not None
            and title_element.text
        ):

            title = title_element.text.strip()

        else:

            title = "Programme"

        programmes.setdefault(
            channel_id,
            []
        ).append(
            {
                "title": title,
                "start": start_dt,
                "stop": stop_dt,
            }
        )

        programme_count += 1

    # ========================================================
    # SORT PROGRAMMES
    # ========================================================

    for channel_id in programmes:

        programmes[channel_id].sort(
            key=lambda item: item["start"]
        )

    # ========================================================
    # SAVE DATA
    # ========================================================

    CHANNELS = channels
    PROGRAMMES = programmes

    # Only show channels which actually have
    # programme information.
    CHANNEL_LIST = sorted(
        [
            channel_id
            for channel_id in CHANNELS
            if channel_id in PROGRAMMES
            and PROGRAMMES[channel_id]
        ],
        key=lambda channel_id:
            CHANNELS[channel_id].lower()
    )

    print(
        f"EPG loaded: "
        f"{len(CHANNELS)} channels, "
        f"{programme_count} programmes"
    )

    print(
        f"Channels with programme data: "
        f"{len(CHANNEL_LIST)}"
    )


# ============================================================
# CHANNEL KEYBOARD
# ============================================================

def channel_keyboard(page=0):

    total_channels = len(
        CHANNEL_LIST
    )

    total_pages = max(
        1,
        (
            total_channels
            + CHANNELS_PER_PAGE
            - 1
        )
        // CHANNELS_PER_PAGE
    )

    # Keep page in range
    page = max(
        0,
        min(
            page,
            total_pages - 1
        )
    )

    start_index = (
        page
        * CHANNELS_PER_PAGE
    )

    end_index = (
        start_index
        + CHANNELS_PER_PAGE
    )

    page_channels = CHANNEL_LIST[
        start_index:end_index
    ]

    buttons = []

    for channel_id in page_channels:

        # Use the channel's position in
        # CHANNEL_LIST for a short callback.
        channel_index = CHANNEL_LIST.index(
            channel_id
        )

        name = CHANNELS.get(
            channel_id,
            channel_id
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    name,
                    callback_data=(
                        f"channel:{channel_index}"
                    )
                )
            ]
        )

    # ========================================================
    # PAGE NAVIGATION
    # ========================================================

    navigation = []

    if page > 0:

        navigation.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f"page:{page - 1}"
            )
        )

    if page < total_pages - 1:

        navigation.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"page:{page + 1}"
            )
        )

    if navigation:

        buttons.append(
            navigation
        )

    # ========================================================
    # REFRESH
    # ========================================================

    buttons.append(
        [
            InlineKeyboardButton(
                "🔄 Refresh",
                callback_data="refresh"
            )
        ]
    )

    return InlineKeyboardMarkup(
        buttons
    )


# ============================================================
# CHANNEL PAGE TEXT
# ============================================================

def channel_page_text(page=0):

    total_channels = len(
        CHANNEL_LIST
    )

    total_pages = max(
        1,
        (
            total_channels
            + CHANNELS_PER_PAGE
            - 1
        )
        // CHANNELS_PER_PAGE
    )

    return (
        "📺 <b>TV GUIDE</b>\n\n"
        "☰ Choose a channel:\n\n"
        f"Page {page + 1} of {total_pages}"
    )


# ============================================================
# GET CHANNEL PROGRAMMES
# ============================================================

def get_channel_programmes(
    channel_id
):

    now = datetime.now(TZ)

    programmes = PROGRAMMES.get(
        channel_id,
        []
    )

    current = []

    for programme in programmes:

        stop = programme["stop"]

        if stop >= now:

            current.append(
                programme
            )

        if len(current) >= 12:

            break

    return current


# ============================================================
# PROGRAMME TEXT
# ============================================================

def programme_text(
    channel_id
):

    name = CHANNELS.get(
        channel_id,
        "Unknown channel"
    )

    programmes = get_channel_programmes(
        channel_id
    )

    safe_name = html.escape(
        name
    )

    text = (
        f"📺 <b>{safe_name}</b>\n\n"
    )

    if not programmes:

        text += (
            "No programme information "
            "available."
        )

        return text

    now = datetime.now(TZ)

    for programme in programmes:

        start = programme["start"]
        stop = programme["stop"]

        start_text = start.strftime(
            "%H:%M"
        )

        stop_text = stop.strftime(
            "%H:%M"
        )

        title = html.escape(
            programme["title"]
        )

        if start <= now < stop:

            marker = "▶️ "

        else:

            marker = ""

        text += (
            f"{marker}"
            f"<b>{start_text} - "
            f"{stop_text}</b> "
            f"{title}\n"
        )

    return text


# ============================================================
# CHANNEL BUTTON KEYBOARD
# ============================================================

def channel_view_keyboard(
    channel_id
):

    # Find channel page so the user
    # can return to the same page.
    try:

        index = CHANNEL_LIST.index(
            channel_id
        )

        page = (
            index
            // CHANNELS_PER_PAGE
        )

    except ValueError:

        page = 0

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Channels",
                    callback_data=f"page:{page}"
                ),
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data="refresh_channel:"
                    + str(
                        CHANNEL_LIST.index(
                            channel_id
                        )
                    )
                )
            ]
        ]
    )


# ============================================================
# START COMMAND
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "Received /start"
    )

    try:

        # Load guide if it has not
        # already been loaded.
        if not CHANNELS:

            load_epg()

    except Exception as e:

        print(
            f"EPG error: {e}"
        )

        if update.message:

            await update.message.reply_text(
                "⚠️ <b>TV guide could not "
                "be loaded.</b>\n\n"
                "Please try again in a moment.",
                parse_mode="HTML"
            )

        return

    if not CHANNEL_LIST:

        await update.message.reply_text(
            "⚠️ The TV guide loaded, "
            "but no channels were found."
        )

        return

    await update.message.reply_text(
        channel_page_text(0),
        parse_mode="HTML",
        reply_markup=channel_keyboard(0)
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

    # ========================================================
    # PAGE
    # ========================================================

    if data.startswith("page:"):

        try:

            page = int(
                data.split(
                    ":",
                    1
                )[1]
            )

        except ValueError:

            page = 0

        await query.edit_message_text(
            channel_page_text(page),
            parse_mode="HTML",
            reply_markup=channel_keyboard(
                page
            )
        )

        return

    # ========================================================
    # REFRESH
    # ========================================================

    if data == "refresh":

        try:

            load_epg()

            await query.edit_message_text(
                channel_page_text(0),
                parse_mode="HTML",
                reply_markup=channel_keyboard(0)
            )

        except Exception as e:

            print(
                f"Refresh error: {e}"
            )

            await query.edit_message_text(
                "⚠️ <b>TV guide could not "
                "be refreshed.</b>\n\n"
                "Please try again.",
                parse_mode="HTML",
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

    # ========================================================
    # REFRESH CHANNEL
    # ========================================================

    if data.startswith(
        "refresh_channel:"
    ):

        try:

            load_epg()

            index = int(
                data.split(
                    ":",
                    1
                )[1]
            )

            channel_id = CHANNEL_LIST[
                index
            ]

            text = programme_text(
                channel_id
            )

            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=channel_view_keyboard(
                    channel_id
                )
            )

        except Exception as e:

            print(
                f"Channel refresh error: {e}"
            )

            await query.edit_message_text(
                "⚠️ Unable to refresh "
                "the channel.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "⬅️ Channels",
                                callback_data="page:0"
                            )
                        ]
                    ]
                )
            )

        return

    # ========================================================
    # CHANNEL
    # ========================================================

    if data.startswith(
        "channel:"
    ):

        try:

            channel_index = int(
                data.split(
                    ":",
                    1
                )[1]
            )

            channel_id = CHANNEL_LIST[
                channel_index
            ]

        except (
            ValueError,
            IndexError
        ):

            await query.edit_message_text(
                "⚠️ Channel not found.",
                reply_markup=channel_keyboard(0)
            )

            return

        text = programme_text(
            channel_id
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=channel_view_keyboard(
                channel_id
            )
        )

        return


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    error = context.error

    print(
        "Telegram error:",
        error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "================================"
    )

    print(
        "Starting TV Guide Bot..."
    )

    print(
        "================================"
    )

    # --------------------------------------------------------
    # Start Render web server
    # --------------------------------------------------------

    start_web_server()

    # --------------------------------------------------------
    # Create Telegram application
    # --------------------------------------------------------

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # /start
    # --------------------------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # --------------------------------------------------------
    # Buttons
    # --------------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # --------------------------------------------------------
    # Error handler
    # --------------------------------------------------------

    app.add_error_handler(
        error_handler
    )

    print(
        "TV Guide Bot is running..."
    )

    # --------------------------------------------------------
    # Telegram polling
    # --------------------------------------------------------

    app.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
