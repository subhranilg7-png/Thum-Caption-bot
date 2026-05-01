#!/usr/bin/env python3
"""
Telegram Thumbnail Bot
======================
Features:
  • /setmode  — choose Document mode (1:1 crop) or Video mode (16:9 crop)
  • /go       — start a batch: send thumbnail first, then multiple files/videos
  • /done     — finish sending files, move to caption step
  • /skip     — skip global caption; each file's own caption is made bold automatically
  • /cancel   — abort current operation
  • Auto-crops thumbnail to the correct ratio using Pillow
  • Re-sends every file/video with thumbnail + bold caption (MarkdownV2)
  • Caption priority: global caption set by user → file's own caption → no caption

Setup:
  pip install python-telegram-bot==20.7 Pillow

Usage:
  1. Set BOT_TOKEN below or: export BOT_TOKEN="your_token"
  2. python thumbnail_bot.py
"""

import os
import io
import logging
from PIL import Image
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Conversation states
SET_MODE, WAITING_THUMB, WAITING_MEDIA, WAITING_CAPTION = range(4)

MODE_DOCUMENT = "document"
MODE_VIDEO    = "video"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def escape_md(text: str) -> str:
    """Escape all MarkdownV2 special characters."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)


def bold_md(text: str) -> str:
    return f"*{escape_md(text)}*"


def crop_image(image_bytes: bytes, mode: str) -> bytes:
    """
    Centre-crop an image to the target aspect ratio:
      document → 1:1  (square)
      video    → 16:9 (landscape)
    Returns JPEG bytes.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size

    if mode == MODE_DOCUMENT:
        side = min(w, h)
        left = (w - side) // 2
        top  = (h - side) // 2
        img  = img.crop((left, top, left + side, top + side))
    else:  # 16:9
        target_w = w
        target_h = int(w * 9 / 16)
        if target_h > h:
            target_h = h
            target_w = int(h * 16 / 9)
        left = (w - target_w) // 2
        top  = (h - target_h) // 2
        img  = img.crop((left, top, left + target_w, top + target_h))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def mode_label(mode: str) -> str:
    return "📄 Document \\(1:1\\)" if mode == MODE_DOCUMENT else "🎬 Video \\(16:9\\)"


# ── /start ─────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = context.user_data.get("mode")
    mode_line = (
        f"Current mode: {mode_label(mode)}\n\n"
        if mode else
        "⚠️ No mode set yet\\. Use /setmode first\\.\n\n"
    )
    await update.message.reply_text(
        "👋 *Welcome to Thumbnail Bot\\!*\n\n"
        + mode_line +
        "*Commands:*\n"
        "/setmode — choose Document or Video mode\n"
        "/go      — start a new batch\n"
        "/done    — finish adding files \\(during batch\\)\n"
        "/skip    — skip caption\n"
        "/cancel  — abort current operation\n\n"
        "*Flow:*\n"
        "1\\. /setmode\n"
        "2\\. /go → send thumbnail\n"
        "3\\. Send files/videos → /done\n"
        "4\\. Send caption \\(or /skip\\)\n"
        "5\\. Bot sends everything with cropped thumbnail \\+ bold caption\\!",
        parse_mode="MarkdownV2",
    )


# ── /setmode ───────────────────────────────────────────────────────────────────

async def setmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[
        InlineKeyboardButton("📄 Document  (1:1)",  callback_data=MODE_DOCUMENT),
        InlineKeyboardButton("🎬 Video  (16:9)",    callback_data=MODE_VIDEO),
    ]]
    await update.message.reply_text(
        "🎛 *Select mode:*\n\n"
        "📄 *Document* — thumbnail cropped to *1:1* \\(square\\)\n"
        "🎬 *Video* — thumbnail cropped to *16:9* \\(widescreen\\)",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SET_MODE


async def mode_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    mode = query.data
    context.user_data["mode"] = mode
    await query.edit_message_text(
        f"✅ Mode set to {mode_label(mode)}\\!\n\n"
        "Send /go to start processing files\\.",
        parse_mode="MarkdownV2",
    )
    return ConversationHandler.END


# ── /go → ask for thumbnail ────────────────────────────────────────────────────

async def go_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    mode = context.user_data.get("mode")
    if not mode:
        await update.message.reply_text(
            "⚠️ Please set a mode first with /setmode\\.",
            parse_mode="MarkdownV2",
        )
        return ConversationHandler.END

    context.user_data["media_list"] = []

    ratio = "1:1" if mode == MODE_DOCUMENT else "16:9"
    await update.message.reply_text(
        f"✅ Mode: {mode_label(mode)}\n\n"
        f"📸 *Step 1 — Thumbnail*\n"
        f"Send me a photo\\. It will be cropped to *{ratio}*\\.",
        parse_mode="MarkdownV2",
    )
    return WAITING_THUMB


# ── Receive thumbnail ──────────────────────────────────────────────────────────

async def receive_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg  = update.message
    mode = context.user_data["mode"]

    if not msg.photo:
        await msg.reply_text(
            "⚠️ Please send a *photo* as the thumbnail\\.",
            parse_mode="MarkdownV2",
        )
        return WAITING_THUMB

    thumb_file = await context.bot.get_file(msg.photo[-1].file_id)
    raw        = await thumb_file.download_as_bytearray()
    cropped    = crop_image(bytes(raw), mode)
    context.user_data["thumb_bytes"] = cropped

    media_word = "files" if mode == MODE_DOCUMENT else "videos"
    ratio      = "1:1" if mode == MODE_DOCUMENT else "16:9"

    await msg.reply_text(
        f"✅ Thumbnail cropped to *{ratio}*\\!\n\n"
        f"📁 *Step 2 — Send {media_word}*\n"
        f"Send one or more {media_word}\\. When done, send /done\\.",
        parse_mode="MarkdownV2",
    )
    return WAITING_MEDIA


# ── Receive media (batch) ──────────────────────────────────────────────────────

async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg        = update.message
    mode       = context.user_data.get("mode")
    media_list = context.user_data.setdefault("media_list", [])

    accepted = False

    if mode == MODE_DOCUMENT and msg.document:
        media_list.append({"type": "document", "file_id": msg.document.file_id, "caption": msg.caption or ""})
        accepted = True
    elif mode == MODE_VIDEO and msg.video:
        media_list.append({"type": "video", "file_id": msg.video.file_id, "caption": msg.caption or ""})
        accepted = True
    elif mode == MODE_DOCUMENT and msg.video:
        await msg.reply_text(
            "⚠️ You're in *Document* mode — send files, not videos\\.\n"
            "Switch with /setmode if needed\\.",
            parse_mode="MarkdownV2",
        )
        return WAITING_MEDIA
    elif mode == MODE_VIDEO and msg.document:
        await msg.reply_text(
            "⚠️ You're in *Video* mode — send videos, not files\\.\n"
            "Switch with /setmode if needed\\.",
            parse_mode="MarkdownV2",
        )
        return WAITING_MEDIA
    else:
        await msg.reply_text(
            "⚠️ Please send a valid file or video\\.",
            parse_mode="MarkdownV2",
        )
        return WAITING_MEDIA

    if accepted:
        n = len(media_list)
        await msg.reply_text(
            f"✅ Added \\— *{n}* item\\(s\\) queued\\.\n"
            "Send more or /done when finished\\.",
            parse_mode="MarkdownV2",
        )
    return WAITING_MEDIA


async def done_receiving(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    media_list = context.user_data.get("media_list", [])
    if not media_list:
        await update.message.reply_text(
            "⚠️ No files added yet\\. Please send some files/videos first\\.",
            parse_mode="MarkdownV2",
        )
        return WAITING_MEDIA

    count = len(media_list)
    await update.message.reply_text(
        f"✅ *{count}* item\\(s\\) ready\\!\n\n"
        "✏️ *Step 3 — Caption*\n"
        "Send a caption to apply to *all* files \\(will be made bold\\)\\.\n"
        "Or send /skip — each file's own caption will be made bold automatically\\.",
        parse_mode="MarkdownV2",
    )
    return WAITING_CAPTION


# ── Caption ────────────────────────────────────────────────────────────────────

async def receive_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["caption"] = update.message.text.strip()
    return await send_all(update, context)


async def skip_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # None = no global caption set → fall back to each file's own caption
    context.user_data["caption"] = None
    return await send_all(update, context)


# ── Send everything ────────────────────────────────────────────────────────────

async def send_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    media_list      = context.user_data.get("media_list", [])
    global_caption  = context.user_data.get("caption")   # None = use per-file caption
    thumb_bytes     = context.user_data.get("thumb_bytes")
    chat_id         = update.effective_chat.id
    count           = len(media_list)

    # Pre-compute bold global caption once if it was set
    bold_global = bold_md(global_caption) if global_caption else None

    await update.effective_message.reply_text(
        f"⏳ Sending *{count}* item\\(s\\)…",
        parse_mode="MarkdownV2",
    )

    success = 0
    for idx, item in enumerate(media_list, 1):
        try:
            thumb_input = InputFile(io.BytesIO(thumb_bytes), filename="thumb.jpg")

            # Caption priority:
            #   1. Global caption set by user (bold)
            #   2. File's own caption, if any (made bold)
            #   3. No caption
            if bold_global is not None:
                final_caption = bold_global
            elif item.get("caption"):
                final_caption = bold_md(item["caption"])
            else:
                final_caption = None

            send_kwargs = dict(
                chat_id=chat_id,
                thumbnail=thumb_input,
                caption=final_caption,
                parse_mode="MarkdownV2" if final_caption else None,
            )

            if item["type"] == "document":
                await context.bot.send_document(document=item["file_id"], **send_kwargs)
            else:
                await context.bot.send_video(video=item["file_id"], **send_kwargs)

            success += 1
            logger.info("Sent %d/%d", idx, count)

        except Exception as e:
            logger.error("Failed item %d: %s", idx, e)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Failed on item {idx}: {escape_md(str(e))}",
                parse_mode="MarkdownV2",
            )

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🎉 *Done\\!* {success}/{count} item\\(s\\) sent with "
            f"thumbnail \\+ bold caption\\.\n\n"
            "Use /go to process more or /setmode to change mode\\."
        ),
        parse_mode="MarkdownV2",
    )

    context.user_data.pop("media_list",  None)
    context.user_data.pop("caption",     None)
    # Keep thumb_bytes and mode for convenience on next /go
    return ConversationHandler.END


# ── /cancel ────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in ("media_list", "caption", "thumb_bytes"):
        context.user_data.pop(key, None)
    await update.message.reply_text(
        "❌ Cancelled\\. Use /go to start again or /setmode to change mode\\.",
        parse_mode="MarkdownV2",
    )
    return ConversationHandler.END


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise ValueError(
            "Please set your BOT_TOKEN via the BOT_TOKEN environment variable."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    # setmode conversation
    setmode_conv = ConversationHandler(
        entry_points=[CommandHandler("setmode", setmode_command)],
        states={SET_MODE: [CallbackQueryHandler(mode_chosen)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # main /go conversation
    go_conv = ConversationHandler(
        entry_points=[CommandHandler("go", go_command)],
        states={
            WAITING_THUMB: [
                MessageHandler(filters.PHOTO, receive_thumbnail),
            ],
            WAITING_MEDIA: [
                CommandHandler("done", done_receiving),
                MessageHandler(filters.Document.ALL | filters.VIDEO, receive_media),
            ],
            WAITING_CAPTION: [
                CommandHandler("skip", skip_caption),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_caption),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(setmode_conv)
    app.add_handler(go_conv)

    # ── Render (webhook) vs local (polling) ───────────────────────────────────
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")   # e.g. https://your-app.onrender.com
    PORT        = int(os.getenv("PORT", "10000"))

    if WEBHOOK_URL:
        # Running on Render — use webhook
        logger.info("Starting webhook on port %d …", PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/webhook",
            url_path="webhook",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        # Running locally — use polling
        logger.info("Starting polling …")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
