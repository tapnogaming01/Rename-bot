import os
import re
import time
import math
import asyncio
import zipfile
import shutil
from flask import Flask
from threading import Thread
from pyrogram import Client, filters
from pymongo import MongoClient
from config import Config

# --- FLASK WEB SERVER FOR RENDER ---
web_app = Flask('')

@web_app.route('/')
def home():
    return "Bot is Running Live!"

def run_flask():
    web_app.run(host='0.0.0.0', port=Config.PORT)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# --- MONGODB & BOT SETUP ---
mongo_client = MongoClient(Config.MONGO_URL)
db = mongo_client["AdvanceAudioBot"]
users_db = db["users"]

app = Client("advance_audio_bot", api_id=Config.API_ID, api_hash=Config.API_HASH, bot_token=Config.BOT_TOKEN)

user_files = {}
user_queues = {}
is_processing = {}

# --- PROGRESS BAR FUNCTION ---
async def progress_bar(current, total, status_msg, start_time, action_type):
    now = time.time()
    diff = now - start_time
    if round(diff % 5) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff if diff > 0 else 0
        eta = round((total - current) / speed) if speed > 0 else 0
        
        filled_blocks = math.floor(percentage / 10)
        bar = "█" * filled_blocks + "░" * (10 - filled_blocks)
        
        progress_text = (
            f"**{action_type}...**\n"
            f"[{bar}] `{percentage:.2f}%`\n"
            f"⚡ **Speed:** `{speed / (1024 * 1024):.2f} MB/s`\n"
            f"⏱️ **ETA:** `{eta}s` | **Done:** `{current / (1024 * 1024):.2f} MB` / `{total / (1024 * 1024):.2f} MB`"
        )
        try:
            await status_msg.edit_text(progress_text)
        except Exception:
            pass

# --- COMMAND HANDLERS ---
@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text(
        "👋 **Welcome to Advance Audio Rename & Batch Bot!**\n\n"
        "**Features:**\n"
        "• Single File Rename with Custom Artist (`Filename.mp3 | Artist Name`)\n"
        "• Batch Audio Processing (`/batch My Process Ep 1`)\n"
        "• Zip File Extraction (`/extract My Process Ep 1`)\n\n"
        "**Thumbnail Commands:**\n"
        "• `/savethumb` (Reply to photo) | `/showthumb` | `/delthumb`\n\n"
        "**Caption Commands:**\n"
        "• `/setcaption <text>` | `/delcaption`"
    )

# Thumbnail Commands
@app.on_message(filters.command("savethumb") & filters.reply)
async def save_thumbnail(client, message):
    replied = message.reply_to_message
    if replied.photo:
        user_id = message.from_user.id
        msg = await message.reply_text("⏳ Saving Thumbnail...")
        users_db.update_one({"user_id": user_id}, {"$set": {"thumb": replied.photo.file_id}}, upsert=True)
        await msg.edit_text("✅ **Custom Thumbnail Saved!**")
    else:
        await message.reply_text("❌ Photo par reply karke `/savethumb` bhejein.")

@app.on_message(filters.command("showthumb"))
async def show_thumbnail(client, message):
    user_id = message.from_user.id
    user_data = users_db.find_one({"user_id": user_id})
    if user_data and user_data.get("thumb"):
        await message.reply_photo(photo=user_data["thumb"], caption="🖼️ Saved Thumbnail")
    else:
        await message.reply_text("❌ Koi Thumbnail saved nahi hai.")

@app.on_message(filters.command("delthumb"))
async def delete_thumbnail(client, message):
    user_id = message.from_user.id
    users_db.update_one({"user_id": user_id}, {"$unset": {"thumb": ""}})
    await message.reply_text("🗑️ **Thumbnail Deleted!**")

# Caption Commands
@app.on_message(filters.command("setcaption"))
async def set_caption(client, message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        await message.reply_text("⚠️ **Usage:** `/setcaption Your Caption Here`")
        return
    caption_text = message.text.split(None, 1)[1]
    users_db.update_one({"user_id": user_id}, {"$set": {"caption": caption_text}}, upsert=True)
    await message.reply_text(f"✅ **Caption Saved:**\n`{caption_text}`")

@app.on_message(filters.command("delcaption"))
async def delete_caption(client, message):
    user_id = message.from_user.id
    users_db.update_one({"user_id": user_id}, {"$unset": {"caption": ""}})
    await message.reply_text("🗑️ **Caption Deleted!**")

@app.on_message(filters.command("clear"))
async def clear_queue(client, message):
    chat_id = message.chat.id
    user_queues[chat_id] = []
    await message.reply_text("🗑️ Queue clear kar di gayi hai.")

# --- FILE RECEIVER ---
@app.on_message(filters.audio | filters.document)
async def handle_files(client, message):
    chat_id = message.chat.id
    
    if message.document and message.document.file_name and message.document.file_name.endswith(('.zip', '.rar')):
        user_files[chat_id] = message
        await message.reply_text("📦 **Zip File Detected!**\nExtract ke liye send karein:\n👉 `/extract My Process Ep 1`")
        return

    is_audio = message.audio or (message.document and message.document.mime_type and message.document.mime_type.startswith("audio/"))
    
    if is_audio:
        user_files[chat_id] = message
        user_queues.setdefault(chat_id, []).append(message)
        
        await message.reply_text(
            f"📥 File Queue me add hui (`Total: {len(user_queues[chat_id])}`).\n\n"
            "• **Single Rename:** `LSOTMK EP 1 TO 20.mp3 | अनुभव चौधरी` text send karein.\n"
            "• **Batch Process:** `/batch My Process Ep 1` send karein."
        )
    else:
        await message.reply_text("❌ Kripya Audio ya Zip file bhejein.")

# --- BATCH RENAME HANDLER ---
@app.on_message(filters.command("batch"))
async def process_batch(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if chat_id not in user_queues or not user_queues[chat_id]:
        await message.reply_text("⚠️ Pehle audio files bhejein!")
        return

    if is_processing.get(chat_id, False):
        await message.reply_text("⏳ Thoda wait karein, purana process chal raha hai...")
        return

    args = message.text.split(None, 1)
    custom_prefix = None
    start_num = 1

    if len(args) > 1:
        text_arg = args[1].strip()
        match = re.search(r'^(.*?)\s*(\d+)$', text_arg)
        if match:
            custom_prefix = match.group(1).strip()
            start_num = int(match.group(2))
        else:
            custom_prefix = text_arg

    files_to_process = user_queues[chat_id].copy()
    user_queues[chat_id] = []
    is_processing[chat_id] = True

    status_msg = await message.reply_text(f"🔄 Batch process shuru ho raha hai (`{len(files_to_process)}` files)...")

    user_data = users_db.find_one({"user_id": user_id})
    thumb_path = await client.download_media(user_data["thumb"], file_name=f"temp_thumb_{user_id}.jpg") if user_data and user_data.get("thumb") else None

    count = start_num
    for msg in files_to_process:
        new_filename = f"{custom_prefix} {count}.mp3" if custom_prefix else f"Audio_{count}.mp3"
        clean_title = os.path.splitext(new_filename)[0]

        start_time = time.time()
        file_path = await msg.download(
            file_name=new_filename,
            progress=progress_bar,
            progress_args=(status_msg, start_time, f"⬇️ Down ({count}/{len(files_to_process)})")
        )

        start_time = time.time()
        await client.send_audio(
            chat_id=chat_id,
            audio=file_path,
            thumb=thumb_path,
            file_name=new_filename,
            title=clean_title,
            caption=f"✅ **Batch File:** `{new_filename}`",
            progress=progress_bar,
            progress_args=(status_msg, start_time, f"⬆️ Up ({count}/{len(files_to_process)})")
        )

        if os.path.exists(file_path):
            os.remove(file_path)
        count += 1
        await asyncio.sleep(1)

    if thumb_path and os.path.exists(thumb_path):
        os.remove(thumb_path)

    is_processing[chat_id] = False
    await status_msg.edit_text("🎉 Sabhi files batch me upload ho gayi!")

# --- ZIP EXTRACT HANDLER ---
@app.on_message(filters.command("extract"))
async def process_zip(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if chat_id not in user_files or not user_files[chat_id].document:
        await message.reply_text("⚠️ Pehle `.zip` file bhejein!")
        return

    text_arg = message.text.split(None, 1)[1].strip() if len(message.command) > 1 else "Episode 1"
    match = re.search(r'^(.*?)\s*(\d+)$', text_arg)
    custom_prefix = match.group(1).strip() if match else text_arg
    start_num = int(match.group(2)) if match else 1

    zip_msg = user_files[chat_id]
    status_msg = await message.reply_text("⬇️ Zip Download ho rahi hai...")

    extract_dir = f"extract_{chat_id}"
    zip_path = await zip_msg.download()

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        audio_files = []
        for root, _, files in os.walk(extract_dir):
            for f in files:
                if f.lower().endswith(('.mp3', '.m4a', '.flac', '.wav')):
                    audio_files.append(os.path.join(root, f))

        audio_files.sort(key=lambda x: [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', x)])

        user_data = users_db.find_one({"user_id": user_id})
        thumb_path = await client.download_media(user_data["thumb"], file_name=f"temp_thumb_{user_id}.jpg") if user_data and user_data.get("thumb") else None

        count = start_num
        for file_full_path in audio_files:
            new_filename = f"{custom_prefix} {count}.mp3"
            clean_title = os.path.splitext(new_filename)[0]

            await status_msg.edit_text(f"⏳ Uploading Extracted: `{new_filename}`")
            await client.send_audio(
                chat_id=chat_id,
                audio=file_full_path,
                thumb=thumb_path,
                file_name=new_filename,
                title=clean_title,
                caption=f"✅ **Extracted Audio:** `{new_filename}`"
            )
            count += 1
            await asyncio.sleep(1)

        await status_msg.edit_text(f"🎉 Total `{len(audio_files)}` files extract hokar rename ho gayi!")

    except Exception as e:
        await status_msg.edit_text(f"❌ Zip Error: `{str(e)}`")

    if os.path.exists(zip_path):
        os.remove(zip_path)
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    del user_files[chat_id]

# --- SINGLE AUDIO RENAME WITH ARTIST HANDLER ---
@app.on_message(filters.text & ~filters.command(["start", "savethumb", "showthumb", "delthumb", "setcaption", "delcaption", "clear", "batch", "extract"]))
async def single_rename(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if chat_id not in user_files:
        await message.reply_text("⚠️ Pehle Audio File bhejein!")
        return

    original_msg = user_files[chat_id]
    text_input = message.text.strip()

    if "|" in text_input:
        parts = text_input.split("|", 1)
        new_filename = parts[0].strip()
        artist_name = parts[1].strip()
    else:
        new_filename = text_input
        artist_name = "Unknown Artist"

    if not (new_filename.endswith(".mp3") or new_filename.endswith(".m4a")):
        new_filename += ".mp3"

    clean_title = os.path.splitext(new_filename)[0]
    status_msg = await message.reply_text("⬇️ Processing Audio...")

    user_data = users_db.find_one({"user_id": user_id})
    thumb_path = await client.download_media(user_data["thumb"], file_name=f"temp_thumb_{user_id}.jpg") if user_data and user_data.get("thumb") else None

    caption_text = user_data.get("caption", f"🎵 **Title:** `{clean_title}`\n👤 **Artist:** `{artist_name}`") if user_data else f"🎵 **Title:** `{clean_title}`\n👤 **Artist:** `{artist_name}`"

    start_time = time.time()
    file_path = await original_msg.download(
        file_name=new_filename,
        progress=progress_bar,
        progress_args=(status_msg, start_time, "⬇️ Downloading")
    )

    start_time = time.time()
    try:
        await client.send_audio(
            chat_id=chat_id,
            audio=file_path,
            thumb=thumb_path,
            file_name=new_filename,
            title=clean_title,
            performer=artist_name,
            caption=caption_text,
            progress=progress_bar,
            progress_args=(status_msg, start_time, "⬆️ Uploading")
        )
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: `{str(e)}`")

    if os.path.exists(file_path):
        os.remove(file_path)
    if thumb_path and os.path.exists(thumb_path):
        os.remove(thumb_path)

    del user_files[chat_id]

if __name__ == "__main__":
    keep_alive()  # Flask server thread start karega
    app.run()     # Telegram Pyrogram Bot run karega
