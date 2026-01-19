import logging
import os
from pathlib import Path
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from datetime import datetime, timedelta
from typing import Dict, List

# Configure logging:
# - Console: warnings/errors only
# - File: everything (debug+)
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler("bot_debug.log", encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(logging.Formatter(LOG_FORMAT))

# Avoid duplicate handlers if the script is reloaded in some environments
root_logger.handlers.clear()
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)

# --- Env loading ---
def load_env(path: str = ".env"):
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # Do not overwrite existing environment variables
        os.environ.setdefault(key.strip(), value.strip())

# Load environment variables (including BOT_TOKEN)
load_env()

# Keep third-party libraries quieter on the console while preserving full detail in the file.
# (They still propagate to root_logger, but console_handler filters below WARNING.)
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("httpcore").setLevel(logging.DEBUG)
logging.getLogger("telegram").setLevel(logging.DEBUG)
logging.getLogger("telegram.ext").setLevel(logging.DEBUG)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Please define it in .env or environment.")

GROUP_ID = int(os.getenv("GROUP_ID", "-1003356712572"))

# Topic thread IDs
# NOTE: From your logs, the topic named "Main" is using thread_id=2.
# If "Hauptgruppe" is that topic, this must be 2 (not 1).
HAUPTGRUPPE_TOPIC_ID = int(os.getenv("HAUPTGRUPPE_TOPIC_ID", "2"))
BIETE_TOPIC_ID = int(os.getenv("BIETE_TOPIC_ID", "3"))
SUCHE_TOPIC_ID = int(os.getenv("SUCHE_TOPIC_ID", "4"))

# Time window (in minutes) for context-based forwarding
# Messages from the same user within this time will be forwarded to the same topics
CONTEXT_TIME_WINDOW_MINUTES = int(os.getenv("CONTEXT_TIME_WINDOW_MINUTES", "5"))

# User context tracking: {user_id: {"topics": [topic_ids], "timestamp": datetime}}
user_forwarding_context: Dict[int, Dict] = {}

def get_active_topics_for_user(user_id: int) -> List[int]:
    """
    Get the list of topics this user should forward to based on recent context.
    Returns empty list if context has expired.
    """
    if user_id not in user_forwarding_context:
        return []

    context_data = user_forwarding_context[user_id]
    timestamp = context_data["timestamp"]
    time_diff = datetime.now() - timestamp

    if time_diff > timedelta(minutes=CONTEXT_TIME_WINDOW_MINUTES):
        # Context expired, remove it
        logger.info(f"Context expired for user {user_id} (age: {time_diff})")
        del user_forwarding_context[user_id]
        return []

    logger.info(f"Active context for user {user_id}: topics {context_data['topics']} (age: {time_diff})")
    return context_data["topics"]

def update_user_context(user_id: int, topics: List[int]):
    """
    Update the forwarding context for a user with the given topics.
    """
    user_forwarding_context[user_id] = {
        "topics": topics,
        "timestamp": datetime.now()
    }
    logger.info(f"Updated context for user {user_id}: topics {topics}")

async def forward_media_to_topic(
    context: ContextTypes.DEFAULT_TYPE,
    message,
    topic_id: int,
    user_link: str
):
    """
    Forward a media message (photo, video, document, etc.) to a specific topic.
    """
    try:
        logger.info(f"Forwarding media to topic {topic_id}")

        caption_text = f"📨 Von {user_link}"
        if message.caption:
            caption_text += f":\n\n{message.caption}"

        # Handle different media types
        if message.photo:
            # Get the largest photo
            photo = message.photo[-1]
            result = await context.bot.send_photo(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                photo=photo.file_id,
                caption=caption_text,
                parse_mode="Markdown"
            )
            logger.info(f"✓ Successfully sent photo to topic {topic_id}. Message ID: {result.message_id}")

        elif message.video:
            result = await context.bot.send_video(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                video=message.video.file_id,
                caption=caption_text,
                parse_mode="Markdown"
            )
            logger.info(f"✓ Successfully sent video to topic {topic_id}. Message ID: {result.message_id}")

        elif message.document:
            result = await context.bot.send_document(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                document=message.document.file_id,
                caption=caption_text,
                parse_mode="Markdown"
            )
            logger.info(f"✓ Successfully sent document to topic {topic_id}. Message ID: {result.message_id}")

        elif message.audio:
            result = await context.bot.send_audio(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                audio=message.audio.file_id,
                caption=caption_text,
                parse_mode="Markdown"
            )
            logger.info(f"✓ Successfully sent audio to topic {topic_id}. Message ID: {result.message_id}")

        elif message.voice:
            result = await context.bot.send_voice(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                voice=message.voice.file_id,
                caption=caption_text,
                parse_mode="Markdown"
            )
            logger.info(f"✓ Successfully sent voice to topic {topic_id}. Message ID: {result.message_id}")

        elif message.video_note:
            # Video notes don't support captions, so we send a separate message
            result = await context.bot.send_video_note(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                video_note=message.video_note.file_id
            )
            await context.bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                text=caption_text,
                parse_mode="Markdown"
            )
            logger.info(f"✓ Successfully sent video note to topic {topic_id}. Message ID: {result.message_id}")

        elif message.sticker:
            result = await context.bot.send_sticker(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                sticker=message.sticker.file_id
            )
            if message.caption:
                await context.bot.send_message(
                    chat_id=GROUP_ID,
                    message_thread_id=topic_id,
                    text=caption_text,
                    parse_mode="Markdown"
                )
            logger.info(f"✓ Successfully sent sticker to topic {topic_id}. Message ID: {result.message_id}")

    except Exception as e:
        logger.error(f"✗ ERROR forwarding media to topic {topic_id}: {type(e).__name__}: {e}", exc_info=True)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("=" * 60)
    logger.info("NEW MESSAGE RECEIVED")
    logger.info(f"Update ID: {update.update_id}")
    
    message = update.message
    
    if not message:
        logger.warning("Message is None, skipping")
        return
    
    logger.info(f"Message ID: {message.message_id}")
    logger.info(f"Chat ID: {message.chat_id} (Expected: {GROUP_ID})")
    logger.info(f"Message Thread ID: {message.message_thread_id} (Expected: {HAUPTGRUPPE_TOPIC_ID})")
    logger.info(f"Chat Type: {message.chat.type}")
    
    if message.chat_id != GROUP_ID:
        logger.warning(f"Chat ID mismatch! Got {message.chat_id}, expected {GROUP_ID}. Skipping.")
        return
    
    logger.info("✓ Chat ID matches")
    
    # Only process messages from the Hauptgruppe topic
    if message.message_thread_id != HAUPTGRUPPE_TOPIC_ID:
        logger.info(
            f"Message thread ID {message.message_thread_id} != {HAUPTGRUPPE_TOPIC_ID}. "
            "Not from Hauptgruppe topic, skipping."
        )
        return
    
    logger.info("✓ Message is from Hauptgruppe topic")
    
    text = message.text or message.caption or ""
    logger.info(f"Message text: {repr(text)}")
    logger.info(f"Text length: {len(text)}")
    
    user = message.from_user
    if user:
        logger.info(f"User ID: {user.id}")
        logger.info(f"User name: {user.first_name} {user.last_name or ''}")
        logger.info(f"Username: @{user.username or 'N/A'}")
    else:
        logger.warning("User is None!")
    
    user_link = f"[{user.first_name}](tg://user?id={user.id})"

    text_lower = text.lower()
    logger.info(f"Text (lowercase): {repr(text_lower)}")

    # Determine if message has media
    has_media = any([
        message.photo,
        message.video,
        message.document,
        message.audio,
        message.voice,
        message.video_note,
        message.sticker
    ])
    logger.info(f"Message has media: {has_media}")

    # Check for hashtags and determine target topics
    target_topics = []

    if "#biete" in text_lower:
        logger.info("✓ Found #biete hashtag")
        target_topics.append(BIETE_TOPIC_ID)
    else:
        logger.debug("No #biete hashtag found")

    if "#suche" in text_lower:
        logger.info("✓ Found #suche hashtag")
        target_topics.append(SUCHE_TOPIC_ID)
    else:
        logger.debug("No #suche hashtag found")

    # If hashtags were found, forward the message and update user context
    if target_topics:
        logger.info(f"Forwarding to topics: {target_topics}")

        # Send text message if there's text content
        if text.strip():
            for topic_id in target_topics:
                try:
                    topic_name = "Biete" if topic_id == BIETE_TOPIC_ID else "Suche"
                    logger.info(f"Attempting to send text to {topic_name} topic (ID: {topic_id})")
                    result = await context.bot.send_message(
                        chat_id=GROUP_ID,
                        message_thread_id=topic_id,
                        text=f"📨 Von {user_link}:\n\n{text}",
                        parse_mode="Markdown"
                    )
                    logger.info(f"✓ Successfully sent text to {topic_name} topic. Message ID: {result.message_id}")
                except Exception as e:
                    logger.error(f"✗ ERROR sending text to topic {topic_id}: {type(e).__name__}: {e}", exc_info=True)

        # Send media if present
        if has_media:
            for topic_id in target_topics:
                await forward_media_to_topic(context, message, topic_id, user_link)

        # Update user context
        update_user_context(user.id, target_topics)

    # If no hashtags found but message has content (text or media), check user context
    elif has_media or text.strip():
        logger.info("No hashtags found, but message has content. Checking user context...")
        active_topics = get_active_topics_for_user(user.id)

        if active_topics:
            logger.info(f"User {user.id} has active context for topics: {active_topics}")

            # Forward text if present
            if text.strip():
                for topic_id in active_topics:
                    try:
                        topic_name = "Biete" if topic_id == BIETE_TOPIC_ID else "Suche"
                        logger.info(f"Attempting to send text to {topic_name} topic (ID: {topic_id}) based on context")
                        result = await context.bot.send_message(
                            chat_id=GROUP_ID,
                            message_thread_id=topic_id,
                            text=f"📨 Von {user_link}:\n\n{text}",
                            parse_mode="Markdown"
                        )
                        logger.info(f"✓ Successfully sent text to {topic_name} topic. Message ID: {result.message_id}")
                    except Exception as e:
                        logger.error(f"✗ ERROR sending text to topic {topic_id}: {type(e).__name__}: {e}", exc_info=True)

            # Forward media if present
            if has_media:
                for topic_id in active_topics:
                    await forward_media_to_topic(context, message, topic_id, user_link)
        else:
            logger.info(f"No active context for user {user.id}. Message will not be forwarded.")
    
    logger.info("Message processing complete")
    logger.info("=" * 60)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors and exceptions"""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    if update:
        logger.error(f"Update that caused error: {update}")

def main():
    logger.info("=" * 60)
    logger.info("STARTING BOT")
    logger.info(f"Bot Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:]}")
    logger.info(f"Group ID: {GROUP_ID}")
    logger.info(f"Hauptgruppe Topic ID: {HAUPTGRUPPE_TOPIC_ID}")
    logger.info(f"Biete Topic ID: {BIETE_TOPIC_ID}")
    logger.info(f"Suche Topic ID: {SUCHE_TOPIC_ID}")
    logger.info("=" * 60)
    
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        logger.info("Application builder created successfully")
        
        app.add_handler(MessageHandler(filters.ALL, handle_message))
        logger.info("Message handler added")
        
        app.add_error_handler(error_handler)
        logger.info("Error handler added")
        
        logger.info("Bot is running and polling...")
        print("Bot is running...")
        app.run_polling()
    except Exception as e:
        logger.critical(f"FATAL ERROR in main(): {type(e).__name__}: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()