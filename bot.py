
import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TOKEN = os.environ["TOKEN"]

EPG_URL = "https://iptv-org.github.io/epg/guides/en/sky.com.xml"

TZ = ZoneInfo("Europe/London")

CHANNELS = {}
PROGRAMMES = {}


def load_epg():
    global CHANNELS, PROGRAMMES

    response = requests.get(EPG_URL, timeout=60)
    response.raise_for_status()

    root = ET.fromstring(response.content)

    channels = {}
    programmes = {}

    for channel in root.findall("channel"):
        channel_id = channel.get("id")
        name = channel.findtext("display-name")

        if channel_id and name:
            channels[channel_id] = name.strip()

    for programme in root.findall("programme"):
        channel_id = programme.get("channel")
        start = programme.get("start")
        stop = programme.get("stop")

        if not channel_id or not start or not stop:
            continue

        title = programme.findtext("title") or "Programme"

        try:
            start_dt = datetime.strptime(
                start[:19], "%Y%m%d%H%M%S"
            ).replace(tzinfo=TZ)

            stop_dt = datetime.strptime(
                stop[:19], "%Y%m%d%H%M%S"
            ).replace(tzinfo=TZ)

        except ValueError:
            continue

        programmes.setdefault(channel_id, []).append(
            (start_dt, stop_dt, title.strip())
        )

    for channel_id in programmes:
        programmes[channel_id].sort(key=lambda x: x[0])

    CHANNELS = channels
    PROGRAMMES = programmes


def channel_keyboard(page=0):
    names = sorted(
        CHANNELS.items(),
        key=lambda x: x[1].lower()
    )

    per_page = 10
    start = page * per_page
    page_items = names[start:start + per_page]

    keyboard = []

    for channel_id, name in page_items:
        keyboard.append([
            InlineKeyboardButton(
                f"📺 {name}",
                callback_data=f"channel:{channel_id}"
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f"page:{page - 1}"
            )
        )

    if start + per_page < len(names):
        navigation.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"page:{page + 1}"
            )
        )

    if navigation:
        keyboard.append(navigation)

    keyboard.append([
        InlineKeyboardButton(
            "🔄 Refresh guide",
            callback_data="refresh"
        )
    ])

    return InlineKeyboardMarkup(keyboard)


def format_channel(channel_id):
    name = CHANNELS.get(channel_id, "Channel")

    now = datetime.now(TZ)

    programmes = PROGRAMMES.get(channel_id, [])

    today = now.date()

    lines = [
        f"📺 <b>{name}</b>",
        f"📅 <b>{now.strftime('%A %d %B')}</b>",
        ""
    ]

    found = False

    for start, stop, title in programmes:
        if start.date() != today:
            continue

        if stop < now:
            continue

        lines.append(
            f"<b>{start.strftime('%H:%M')}</b>  {title}"
        )

        found = True

        if len(lines) >= 17:
            break

    if not found:
        lines.append("No programme information available.")

    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        load_epg()

        await update.message.reply_text(
            "📺 <b>TV GUIDE</b>\n\n"
            "☰ Choose a channel:",
            parse_mode="HTML",
            reply_markup=channel_keyboard(0),
        )

    except Exception:
        await update.message.reply_text(
            "⚠️ Unable to load the TV guide.\n\n"
            "Please try again later."
        )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    data = query.data

    try:
        if data.startswith("page:"):
            page = int(data.split(":")[1])

            await query.edit_message_text(
                "📺 <b>Choose a channel:</b>",
                parse_mode="HTML",
                reply_markup=channel_keyboard(page),
            )

        elif data.startswith("channel:"):
            channel_id = data.split(":", 1)[1]

            text = format_channel(channel_id)

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Back to channels",
                        callback_data="home"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔄 Refresh",
                        callback_data=f"channel:{channel_id}"
                    )
                ]
            ])

            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

        elif data == "home":
            await query.edit_message_text(
                "📺 <b>TV GUIDE</b>\n\n"
                "☰ Choose a channel:",
                parse_mode="HTML",
                reply_markup=channel_keyboard(0),
            )

        elif data == "refresh":
            load_epg()

            await query.edit_message_text(
                "📺 <b>TV GUIDE</b>\n\n"
                "☰ Choose a channel:",
                parse_mode="HTML",
                reply_markup=channel_keyboard(0),
            )

    except Exception:
        await query.edit_message_text(
            "⚠️ Something went wrong.\n\n"
            "Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 Try again",
                        callback_data="refresh"
                    )
                ]
            ])
        )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(button_handler)
    )

    print("TV Guide Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
