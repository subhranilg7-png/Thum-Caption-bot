#!/usr/bin/env python3
"""
Telegram Thumbnail Bot — Pyrogram MTProto Edition
==================================================
Thumbnails are applied WITHOUT re-downloading files (MTProto magic).

Commands:
  /addthumb    - Set thumbnail (send photo after this command)
  /viewthumb   - Preview your saved thumbnail
  /deletethumb - Delete your saved thumbnail
  /mode        - Switch between Document (1:1) and Video (16:9)
  /addcaption  - Set a custom bold caption for all files
  /add         - Start collecting files/videos
  /end         - Process all collected files
  /stop        - Cancel and clear queue
  /help        - Show all commands

Setup:
  pip install pyrogram==2.0.106 tgcrypto Pillow flask

Env vars:
  API_ID    — from my.telegram.org
  API_HASH  — from my.telegram.org
  BOT_TOKEN — from @BotFather
"""

import os
import io
import logging
import threading

from flask import Flask
from PIL import Image, ImageFilter
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

# ── Config ─────────────────────────────────────────────────────────────────────
API_ID    = int(os.getenv("API_ID",    "0"))
API_HASH  =     os.getenv("API_HASH",  "")
BOT_TOKEN =     os.getenv("BOT_TOKEN", "")

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Pyrogram client ────────────────────────────────────────────────────────────
bot = Client(
    "thumb_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

# ── Per-user state ─────────────────────────────────────────────────────────────
_state: dict = {}

def get(uid: int) -> dict:
    if uid not in _state:
        _state[uid] = {
            "thumb":           None,   # JPEG bytes of processed thumbnail
            "mode":            "doc",  # "doc" (1:1) | "vid" (16:9)
            "caption":         None,   # custom caption str or None
            "queue":           [],     # list of file info dicts
            "collecting":      False,  # True when /add is active
            "waiting_thumb":   False,  # True after /addthumb
            "waiting_caption": False,  # True after /addcaption
        }
    return _state[uid]

# ── Thumbnail image processing ─────────────────────────────────────────────────
THUMB_DOC = (320, 320)    # 1:1  square
THUMB_VID = (320, 180)    # 16:9 widescreen

def process_thumb(raw: bytes, mode: str) -> bytes:
    img = Image.open(io.BytesIO(raw))

    # Clean RGB conversion (handles RGBA / PNG transparency)
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size

    if mode == "doc":
        s   = min(w, h)
        img = img.crop(((w - s) // 2, (h - s) // 2,
                         (w + s) // 2, (h + s) // 2))
        tgt = THUMB_DOC
    else:
        tw, th = w, int(w * 9 / 16)
        if th > h:
            th, tw = h, int(h * 16 / 9)
        img = img.crop(((w - tw) // 2, (h - th) // 2,
                         (w + tw) // 2, (h + th) // 2))
        tgt = THUMB_VID

    img = img.resize(tgt, Image.LANCZOS)
    img = img.filter(ImageFilter.UnsharpMask(radius=0.8, percent=120, threshold=2))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95, subsampling=0, optimize=True)
    return buf.getvalue()

def bold(text: str) -> str:
    return f"**{text}**"

def mode_label(mode: str) -> str:
    return "📄 Document (1:1  320×320)" if mode == "doc" else "🎬 Video (16:9  320×180)"

def status_text(s: dict) -> str:
    thumb  = "✅ Set" if s["thumb"] else "❌ Not set"
    cap    = f"`{s['caption']}`" if s["caption"] else "_File's own caption (bolded)_"
    return (
        f"**Current settings**\n"
        f"Thumbnail : {thumb}\n"
        f"Mode      : {mode_label(s['mode'])}\n"
        f"Caption   : {cap}"
    )

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

# ── /start ─────────────────────────────────────────────────────────────────────
@bot.on_message(filters.command("start") & filters.private)
async def cmd_start(_: Client, msg: Message):
    await msg.reply_text(
        "👋 **Welcome to Thumbnail Bot!**\n\n"
        "I add custom thumbnails to your files & videos and make "
        "captions bold — using Pyrogram MTProto so files are "
        "**never re-downloaded**.\n\n"
        + status_text(get(msg.from_user.id)) +
        "\n\n📖 Use /help to see all commands."
    )

# ── /help ──────────────────────────────────────────────────────────────────────
@bot.on_message(filters.command("help") & filters.private)
async def cmd_help(_: Client, msg: Message):
    await msg.reply_text(
        "📖 **All Commands**\n\n"

        "🖼 **Thumbnail**\n"
        "/addthumb — Set thumbnail _(send photo after)_\n"
        "/viewthumb — Preview your saved thumbnail\n"
        "/deletethumb — Delete your saved thumbnail\n\n"

        "🎛 **Mode**\n"
        "/mode — Switch between:\n"
        "　📄 Document → crops thumbnail to **1:1** (320×320)\n"
        "　🎬 Video → crops thumbnail to **16:9** (320×180)\n\n"

        "✏️ **Caption**\n"
        "/addcaption — Set a custom caption _(will be bold)_\n"
        "　If not set → file's own caption is made bold\n"
        "　If file has no caption → sent without caption\n\n"

        "📁 **Processing**\n"
        "/add — Start collecting files/videos\n"
        "/end — Process all collected files\n"
        "/stop — Cancel and clear the queue\n\n"

        "**Typical flow:**\n"
        "① /addthumb → send photo\n"
        "② /mode → pick Document or Video\n"
        "③ /addcaption → set caption _(optional)_\n"
        "④ /add → send files one by one\n"
        "⑤ /end → done! ✅"
    )

# ── /addthumb ──────────────────────────────────────────────────────────────────
@bot.on_message(filters.command("addthumb") & filters.private)
async def cmd_addthumb(_: Client, msg: Message):
    s = get(msg.from_user.id)
    s["waiting_thumb"]   = True
    s["waiting_caption"] = False
    ratio = "1:1 (320×320)" if s["mode"] == "doc" else "16:9 (320×180)"
    await msg.reply_text(
        f"📸 **Send me a photo** to use as thumbnail.\n"
        f"It will be cropped to **{ratio}** for your current mode.\n\n"
        f"Use /mode to switch modes first if needed.\n"
        f"Send /stop to cancel."
    )

# ── /viewthumb ─────────────────────────────────────────────────────────────────
@bot.on_message(filters.command("viewthumb") & filters.private)
async def cmd_viewthumb(_: Client, msg: Message):
    s = get(msg.from_user.id)
    if not s["thumb"]:
        await msg.reply_text("❌ No thumbnail saved. Use /addthumb to set one.")
        return
    cap = f"`{s['caption']}`" if s["caption"] else "_File's own caption (bolded)_"
    buf      = io.BytesIO(s["thumb"])
    buf.name = "thumb.jpg"
    await msg.reply_photo(
        photo=buf,
        caption=(
            f"🖼 **Your saved thumbnail**\n\n"
            f"Mode    : {mode_label(s['mode'])}\n"
            f"Caption : {cap}"
        )
    )

# ── /deletethumb ───────────────────────────────────────────────────────────────
@bot.on_message(filters.command("deletethumb") & filters.private)
async def cmd_deletethumb(_: Client, msg: Message):
    s = get(msg.from_user.id)
    if not s["thumb"]:
        await msg.reply_text("❌ No thumbnail to delete.")
        return
    s["thumb"] = None
    await msg.reply_text("🗑 **Thumbnail deleted.**\nUse /addthumb to set a new one.")

# ── /mode ──────────────────────────────────────────────────────────────────────
@bot.on_message(filters.command("mode") & filters.private)
async def cmd_mode(_: Client, msg: Message):
    s = get(msg.from_user.id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📄 Document (1:1)" + (" ✅" if s["mode"] == "doc" else ""),
                callback_data="mode_doc"
            ),
            InlineKeyboardButton(
                "🎬 Video (16:9)" + (" ✅" if s["mode"] == "vid" else ""),
                callback_data="mode_vid"
            ),
        ]
    ])
    await msg.reply_text(
        "🎛 **Select mode:**\n\n"
        "📄 **Document** — thumbnail cropped to **1:1** (320×320)\n"
        "🎬 **Video** — thumbnail cropped to **16:9** (320×180)\n\n"
        f"Current: {mode_label(s['mode'])}",
        reply_markup=keyboard,
    )

@bot.on_callback_query(filters.regex("^mode_"))
async def cb_mode(_: Client, cb: CallbackQuery):
    s         = get(cb.from_user.id)
    new_mode  = "doc" if cb.data == "mode_doc" else "vid"
    s["mode"] = new_mode
    ratio     = "1:1 (320×320)" if new_mode == "doc" else "16:9 (320×180)"
    note      = (
        "\n\n⚠️ You have a saved thumbnail — use /addthumb to re-crop it for the new mode."
        if s["thumb"] else ""
    )
    await cb.message.edit_text(
        f"✅ Mode set to **{mode_label(new_mode)}**\n"
        f"Thumbnail will be cropped to **{ratio}**.{note}"
    )
    await cb.answer()

# ── /addcaption ────────────────────────────────────────────────────────────────
@bot.on_message(filters.command("addcaption") & filters.private)
async def cmd_addcaption(_: Client, msg: Message):
    s = get(msg.from_user.id)
    s["waiting_caption"] = True
    s["waiting_thumb"]   = False
    cur = f"\n\nCurrent: `{s['caption']}`" if s["caption"] else ""
    await msg.reply_text(
        f"✏️ **Send the caption text** you want applied to all files.{cur}\n\n"
        "It will be displayed in **bold**.\n"
        "Send /stop to cancel without changing."
    )

# ── /add ───────────────────────────────────────────────────────────────────────
@bot.on_message(filters.command("add") & filters.private)
async def cmd_add(_: Client, msg: Message):
    s = get(msg.from_user.id)
    if not s["thumb"]:
        await msg.reply_text(
            "❌ No thumbnail set!\n"
            "Use /addthumb first to set a thumbnail."
        )
        return
    s["collecting"]      = True
    s["waiting_thumb"]   = False
    s["waiting_caption"] = False
    s["queue"]           = []
    cap = f"`{s['caption']}`" if s["caption"] else "_File's own caption (bolded)_"
    await msg.reply_text(
        f"📁 **Ready to collect files!**\n\n"
        f"Mode    : {mode_label(s['mode'])}\n"
        f"Caption : {cap}\n\n"
        f"Send your **files or videos** one by one.\n\n"
        f"✅ /end — process all queued files\n"
        f"🛑 /stop — cancel and clear queue"
    )

# ── /end ───────────────────────────────────────────────────────────────────────
@bot.on_message(filters.command("end") & filters.private)
async def cmd_end(client: Client, msg: Message):
    s = get(msg.from_user.id)
    if not s["collecting"] and not s["queue"]:
        await msg.reply_text(
            "❌ Nothing to process.\n"
            "Use /add to start collecting files."
        )
        return
    if not s["queue"]:
        await msg.reply_text("❌ Queue is empty! Send some files after /add.")
        return

    s["collecting"] = False
    count      = len(s["queue"])
    status_msg = await msg.reply_text(f"⏳ Processing **{count}** file(s)...")
    success    = 0
    failed     = 0

    for idx, item in enumerate(s["queue"], 1):
        try:
            await status_msg.edit_text(
                f"⏳ Processing **{idx}/{count}** — please wait..."
            )

            # Caption priority:
            #   1. Custom caption (bold)
            #   2. File's own caption (bold)
            #   3. No caption
            if s["caption"]:
                final_cap = bold(s["caption"])
            elif item.get("caption"):
                final_cap = bold(item["caption"])
            else:
                final_cap = None

            thumb_buf      = io.BytesIO(s["thumb"])
            thumb_buf.name = "thumb.jpg"

            if item["type"] == "document":
                await client.send_document(
                    chat_id=msg.chat.id,
                    document=item["file_id"],
                    thumb=thumb_buf,
                    caption=final_cap,
                    file_name=item.get("filename", "document"),
                    force_document=True,
                )
            else:
                await client.send_video(
                    chat_id=msg.chat.id,
                    video=item["file_id"],
                    thumb=thumb_buf,
                    caption=final_cap,
                    supports_streaming=True,
                )
            success += 1

        except Exception as e:
            logger.error("Failed item %d: %s", idx, e)
            failed += 1
            await msg.reply_text(f"❌ Failed on file **{idx}**: `{e}`")

    s["queue"] = []
    fail_note  = f"\n⚠️ {failed} file(s) failed." if failed else ""
    await status_msg.edit_text(
        f"🎉 **Done!** {success}/{count} file(s) sent with thumbnail + bold caption.{fail_note}\n\n"
        "Your settings are saved. Use /add to process more files."
    )

# ── /stop ──────────────────────────────────────────────────────────────────────
@bot.on_message(filters.command("stop") & filters.private)
async def cmd_stop(_: Client, msg: Message):
    s       = get(msg.from_user.id)
    cleared = len(s["queue"])
    s["collecting"]      = False
    s["waiting_thumb"]   = False
    s["waiting_caption"] = False
    s["queue"]           = []
    await msg.reply_text(
        f"🛑 **Stopped.**\n"
        f"{cleared} file(s) cleared from queue.\n\n"
        "Your **thumbnail**, **mode** and **caption** settings are kept.\n"
        "Use /add to start a new session."
    )

# ══════════════════════════════════════════════════════════════════════════════
#  MEDIA HANDLER — photos, documents, videos
# ══════════════════════════════════════════════════════════════════════════════
@bot.on_message(filters.private & (filters.photo | filters.document | filters.video))
async def handle_media(client: Client, msg: Message):
    uid = msg.from_user.id
    s   = get(uid)

    # ── Receiving thumbnail photo ──────────────────────────────────────────────
    if s["waiting_thumb"] and msg.photo:
        s["waiting_thumb"] = False
        notice = await msg.reply_text("⏳ Processing thumbnail...")
        try:
            dl  = await client.download_media(msg.photo.file_id, in_memory=True)
            raw = bytes(dl.getbuffer())
            s["thumb"] = process_thumb(raw, s["mode"])
            ratio = "1:1 (320×320)" if s["mode"] == "doc" else "16:9 (320×180)"
            await notice.edit_text(
                f"✅ **Thumbnail saved!** Cropped to **{ratio}**\n\n"
                "Use /viewthumb to preview it.\n"
                "Use /add to start processing files."
            )
        except Exception as e:
            logger.error("Thumb processing error: %s", e)
            await notice.edit_text(f"❌ Failed to process thumbnail: `{e}`")
        return

    # ── Waiting for caption — reject media ────────────────────────────────────
    if s["waiting_caption"]:
        await msg.reply_text("⚠️ Please send the caption as **text**, not a file.")
        return

    # ── Collecting files ───────────────────────────────────────────────────────
    if s["collecting"]:
        if msg.document:
            s["queue"].append({
                "type":     "document",
                "file_id":  msg.document.file_id,
                "filename": msg.document.file_name or "document",
                "caption":  msg.caption or "",
            })
            await msg.reply_text(
                f"✅ **Document added** — {len(s['queue'])} in queue\n"
                "/end to process • /stop to cancel"
            )
        elif msg.video:
            s["queue"].append({
                "type":    "video",
                "file_id": msg.video.file_id,
                "caption": msg.caption or "",
            })
            await msg.reply_text(
                f"✅ **Video added** — {len(s['queue'])} in queue\n"
                "/end to process • /stop to cancel"
            )
        elif msg.photo:
            await msg.reply_text(
                "⚠️ Photos can't be processed as files.\n"
                "Please send a **document** or **video**."
            )
        else:
            await msg.reply_text("⚠️ Please send a **document** or **video**.")
        return

    # ── Idle — nothing active ──────────────────────────────────────────────────
    await msg.reply_text(
        "💡 Use /add to start collecting files.\n"
        "Use /help for all commands."
    )

# ══════════════════════════════════════════════════════════════════════════════
#  TEXT HANDLER — for caption input
# ══════════════════════════════════════════════════════════════════════════════
CMDS = ["start","help","addthumb","viewthumb","deletethumb",
        "mode","addcaption","add","end","stop"]

@bot.on_message(filters.private & filters.text & ~filters.command(CMDS))
async def handle_text(_: Client, msg: Message):
    s = get(msg.from_user.id)

    if s["waiting_caption"]:
        s["caption"]         = msg.text.strip()
        s["waiting_caption"] = False
        await msg.reply_text(
            f"✅ **Caption set:** `{s['caption']}`\n\n"
            "This will be applied in **bold** to all files.\n"
            "Use /addcaption to change it or /stop to clear it."
        )
        return

    if s["collecting"]:
        await msg.reply_text(
            "⚠️ You're in collecting mode — please send files.\n"
            "/end to process • /stop to cancel"
        )
        return

    if s["waiting_thumb"]:
        await msg.reply_text("⚠️ Please send a **photo** as the thumbnail.")
        return

    await msg.reply_text("Use /help to see all available commands.")

# ══════════════════════════════════════════════════════════════════════════════
#  FLASK KEEP-ALIVE (prevents Render free tier spin-down)
# ══════════════════════════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "Thumbnail Bot is running!", 200

def run_flask():
    port = int(os.getenv("PORT", "10000"))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    if not API_ID or not API_HASH or not BOT_TOKEN:
        raise ValueError(
            "Missing env vars. Set API_ID, API_HASH and BOT_TOKEN."
        )
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Flask keep-alive started on port %s", os.getenv("PORT", "10000"))
    logger.info("Starting Pyrogram bot...")
    bot.run()
