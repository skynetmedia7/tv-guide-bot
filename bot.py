import os
import threading
import requests
import xml.etree.ElementTree as ET
import tempfile

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

EPG_URL = "https://iptv-epg.org/files/epg-gb.xml"

TZ = ZoneInfo("Europe/London")

CHANNELS = {}
PROGRAMMES = {}


# ============================================================
# CHECK TOKEN
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

        # Standard XMLTV format:
        # 20260827230000 +0100

        base = value[:14]

        dt = datetime.strptime(
            base,
            "%Y%m%d%H%M%S"
        )

        # Look for timezone offset
        rest = value[14:].strip()

        if rest:

            # +0100 / -0500
            if len(rest) >= 5 and (
                rest[0] in "+-"
                and rest[1:5].isdigit()
            ):

                sign = 1 if rest[0] == "+" else -1

                hours = int(rest[1:3])
                minutes = int(rest[3:5])

                from datetime import timedelta, timezone as dt_timezone

                offset = sign * (
                    hours * 3600 +
                    minutes * 60
                )

                tz = dt_timezone(
                    timedelta(seconds=offset)
                )

                return dt.replace(
                    tzinfo=tz
                ).astimezone(TZ)

        # No timezone supplied - assume UTC
        return dt.replace(
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

    print("========================================")
    print("Loading TV guide...")
    print("EPG URL:", EPG_URL)
    print("========================================")

    channels = {}
    programmes = {}

    now = datetime.now(TZ)

    # Keep approximately 24 hours of guide data
    # instead of loading the entire EPG into memory.

    future_limit = now.timestamp() + (24 * 60 * 60)

    temp_file = None

    try:

        # ----------------------------------------------------
        # DOWNLOAD TO DISK
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
            "EPG download started. HTTP:",
            response.status_code
        )

        # ----------------------------------------------------
        # SAVE TEMPORARY FILE
        # ----------------------------------------------------

        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            suffix=".xml"
        ) as f:

            temp_file = f.name

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if chunk:
                    f.write(chunk)

        print("EPG downloaded successfully")

        # ----------------------------------------------------
        # STREAM XML FILE
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

                if name_element is not None:
                    name = name_element.text
                else:
                    name = channel_id

                if channel_id and name:

                    channels[channel_id] = (
                        name.strip()
                    )

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

                # Ignore programmes that have finished
                if stop_dt < now:

                    element.clear()
                    continue

                # Ignore programmes more than 24 hours ahead
                if start_dt.timestamp() > future_limit:

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
                        "stop": stop_dt,
                    }
                )

                element.clear()

        # ----------------------------------------------------
        # SORT PROGRAMMES
        # ----------------------------------------------------

        for channel_id in programmes:

            programmes[channel_id].sort(
                key=lambda x: x["start"]
            )

        CHANNELS = channels
        PROGRAMMES = programmes

        total_programmes = sum(
            len(x)
            for x in PROGRAMMES.values()
        )

        print("========================================")
        print("EPG LOADED SUCCESSFULLY")
        print("Channels:", len(CHANNELS))
        print("Programmes:", total_programmes)
        print("========================================")

        return True

    except Exception as e:

        print("========================================")
        print("EPG LOAD ERROR")
        print("ERROR TYPE:", type(e).__name__)
        print("ERROR:", str(e))
        print("========================================")

        raise

    finally:

        # Delete temporary XML file
        if temp_file:

            try:
                os.remove(temp_file)

            except Exception:
                pass


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

        stop = programme["stop"]

        if stop >= now:

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

    text = (
        f"📺 <b>{name}</b>\n\n"
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

        title = programme["title"]

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
        print("
