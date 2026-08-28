import os
import threading
import requests
import xml.etree.ElementTree as ET
import tempfile
import html

from datetime import datetime, timezone, timedelta
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

EPG_URL = "https://iptv-epg.org/files/epg-gb.xml"

TZ = ZoneInfo("Europe/London")

CHANNELS = {}
PROGRAMMES = {}


# ============================================================
# CHECK BOT TOKEN
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
# PARSE EPG TIME
# ============================================================

def parse_epg_time(value):

    if not value:
        return None

    try:

        value = value.strip()

        # XMLTV normally looks like:
        # 20260827230000 +0100

        base = value[:14]

        dt = datetime.strptime(
            base,
            "%Y%m%d%H%M%S"
        )

        rest = value[14:].strip()

        # Timezone supplied
        if (
            len(rest) >= 5
            and rest[0] in "+-"
            and rest[1:5].isdigit()
        ):

            sign = 1 if rest[0] == "+" else -1

            hours = int(rest[1:3])
            minutes = int(rest[3:5])

            offset_seconds = sign * (
                hours * 3600
                + minutes * 60
            )

            tz = timezone(
                timedelta(
                    seconds=offset_seconds
                )
            )

            return dt.replace(
                tzinfo=tz
            ).astimezone(TZ)

        # No timezone supplied
        return dt.replace(
            tzinfo=timezone.utc
        ).astimezone(TZ)

    except Exception as e:

        print(
            f"Could not parse EPG time '{value}': {e}"
        )

        return None


# ============================================================
# LOAD EPG
# ============================================================

def load_epg():

    global CHANNELS
    global PROGRAMMES

    print("========================================")
    print("Loading TV guide...")
    print("EPG URL:", EPG_URL)
    print("========================================")

    channels = {}
    programmes = {}

    now = datetime.now(TZ)

    # Only keep 24 hours of programmes
    future_limit = now + timedelta(hours=24)

    temp_file = None

    try:

        # ----------------------------------------------------
        # DOWNLOAD EPG TO DISK
        # ----------------------------------------------------

        print("Downloading EPG...")

        response = requests.get(
            EPG_URL,
            timeout=120,
            stream=True,
            headers={
                "User-Agent": "TV-Guide-Bot/1.0"
            }
        )

        response.raise_for_status()

        print(
            f"EPG HTTP status: {response.status_code}"
        )

        # ----------------------------------------------------
        # SAVE TO TEMPORARY FILE
        # ----------------------------------------------------

        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            suffix=".xml"
        ) as file:

            temp_file = file.name

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if chunk:
                    file.write(chunk)

        print("EPG downloaded successfully")

        # ----------------------------------------------------
        # READ XML WITHOUT LOADING EVERYTHING INTO RAM
        # ----------------------------------------------------

        print("Reading EPG...")

        context = ET.iterparse(
            temp_file,
            events=("end",)
        )

        for event, element in context:

            # ------------------------------------------------
            # CHANNEL
            # ------------------------------------------------

            if element.tag == "channel":

                channel_id = element.get("id")

                name_element = element.find(
                    "display-name"
                )

                if (
                    name_element is not None
                    and name_element.text
                ):

                    name = name_element.text.strip()

                else:

                    name = channel_id

                if channel_id and name:

                    channels[channel_id] = name

                element.clear()

            # ------------------------------------------------
            # PROGRAMME
            # ------------------------------------------------

            elif element.tag == "programme":

                channel_id = element.get("channel")
                start = element.get("start")
                stop = element.get("stop")

                if (
                    not channel_id
                    or not start
                    or not stop
                ):

                    element.clear()
                    continue

                start_dt = parse_epg_time(start)
                stop_dt = parse_epg_time(stop)

                if (
                    start_dt is None
                    or stop_dt is None
                ):

                    element.clear()
                    continue

                # Ignore programmes already finished
                if stop_dt < now:

                    element.clear()
                    continue

                # Ignore programmes more than 24 hours ahead
                if start_dt > future_limit:

                    element.clear()
                    continue

                title_element = element.find(
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
                        "stop": stop_dt
                    }
                )

                element.clear()

        # ----------------------------------------------------
        # SORT PROGRAMMES
        # ----------------------------------------------------

        for channel_id in programmes:

            programmes[channel_id].sort(
                key=lambda item: item["start"]
            )

        CHANNELS = channels
        PROGRAMMES = programmes

        total = sum(
            len(items)
            for items in PROGRAMMES.values()
        )

        print("========================================")
        print("EPG LOADED SUCCESSFULLY")
        print(
            f"Channels: {len(CHANNELS)}"
        )
        print(
            f"Programmes: {total}"
        )
        print("========================================")

        return True

    except Exception as e:

        print("========================================")
        print("EPG LOAD ERROR")
        print(
            "ERROR TYPE:",
            type(e).__name__
        )
        print(
            "ERROR:",
            str(e)
        )
        print("========================================")

        raise

    finally:

        if temp_file:

            try:
                os.remove(temp_file)
                print("Temporary EPG file removed")

            except Exception:
                pass

print("===== CHANNELS FOUND =====")
for channel_id, channel_name in sorted(CHANNELS.items(), key=lambda x: x[1].lower()):
    print(channel_name)
print("===== END CHANNELS =====")
# ============================================================
# CHANNEL KEYBOARD
# ============================================================

def channel_keyboard():

    buttons = []

    sorted_channels = sorted(
        CHANNELS.items(),
        key=lambda item: item[1].lower()
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

    result = []

    for programme in programmes:

        if programme["stop"] >= now:

            result.append(programme)

        if len(result) >= 12:

            break

    return result


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

    # Escape text for Telegram HTML
    safe_name = html.escape(name)

    text = (
        f"📺 <b>{safe_name}</b>\n\n"
    )

    if not programmes:

        text += (
            "No programme information available."
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

    print("========================================")
    print("Received /start")
    print("========================================")

    try:

        if not CHANNELS:

            load_epg()

    except Exception as e:

        print("========================================")
        print("START COMMAND EPG ERROR")
        print(
            "ERROR TYPE:",
            type(e).__name__
        )
        print(
            "ERROR:",
            str(e)
        )
        print("========================================")

        await update.message.reply_text(
            "⚠️ <b>TV GUIDE ERROR</b>\n\n"
            f"{html.escape(type(e).__name__)}: "
            f"{html.escape(str(e))}",
            parse_mode="HTML"
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

            print("========================================")
            print("REFRESH EPG ERROR")
            print(
                "ERROR TYPE:",
                type(e).__name__
            )
            print(
                "ERROR:",
                str(e)
            )
            print("========================================")

            await query.edit_message_text(
                "⚠️ <b>TV GUIDE ERROR</b>\n\n"
                f"{html.escape(type(e).__name__)}: "
                f"{html.escape(str(e))}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔄 Try again",
                                callback_data="refresh"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "⬅️ Home",
                                callback_data="home"
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

    print("========================================")
    print("TELEGRAM ERROR")
    print(
        "ERROR TYPE:",
        type(context.error).__name__
    )
    print(
        "ERROR:",
        str(context.error)
    )
    print("========================================")


# ============================================================
# TELEGRAM CONNECTION
# ============================================================

async def post_init(application):

    print("========================================")
    print("Checking Telegram connection...")
    print("========================================")

    me = await application.bot.get_me()

    print(
        f"Connected to Telegram as @{me.username}"
    )

    # Remove any old webhook
    await application.bot.delete_webhook(
        drop_pending_updates=True
    )

    print("Telegram webhook cleared")
    print("Starting polling...")


# ============================================================
# MAIN
# ============================================================

def main():
    import os
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    PORT = int(os.environ.get("PORT", 10000))

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"TV Guide Bot is running")

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)

    threading.Thread(
        target=server.serve_forever,
        daemon=True
    ).start()

    print(f"Web server listening on port {PORT}")

    print("========================================")
    print("Starting TV Guide Bot...")
    print("========================================")

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(button_handler)
    )

    app.add_error_handler(error_handler)

    print("TV Guide Bot is ready")
    print("Starting Telegram polling...")

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
    
    
