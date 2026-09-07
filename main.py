import os
import re
import time
import math
import asyncio
import zipfile
import shutil
import piexif
from flask import Flask
from threading import Thread
from pyrogram import Client, filters
from pymongo import MongoClient
from PIL import Image, ImageDraw, ImageFont
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
cancel_requested = {}

# --- WATERMARK & EXIF METADATA HELPER FUNCTION (UPDATED SIZE) ---
def process_photo_metadata(image_path, text="Anubhav"):
    img = Image.open(image_path).convert("RGBA")
    txt_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt_layer)
    
    width, height = img.size
    
    # Font Size ko 4% se bada karke 7% kar diya gaya hai (minimum 25px)
    font_size = max(int(height * 0.07), 25)

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # TOP-LEFT POSITIONING (Adjusted margin and padding)
    margin = int(height * 0.04)
    x = margin
    y = margin
    padding = int(font_size * 0.25)

    # Black Background Box for visual contrast
    draw.rectangle(
        [x - padding, y - padding, x + text_width + padding, y + text_height + padding],
        fill=(0, 0, 0, 160)
    )
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)

    watermarked_img = Image.alpha_composite(img, txt_layer).convert("RGB")
    
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    output_path = f"wm_{base_name}.jpg"

    # Set EXIF Metadata Tags inside JPEG File
    zeroth_ifd = {
        piexif.ImageIFD.Artist: text.encode('utf-8'),
        piexif.ImageIFD.ImageDescription: text.encode('utf-8'),
        piexif.ImageIFD.Copyright: text.encode('utf-8')
    }
    exif_bytes = piexif.dump({"0th": zeroth_ifd})

    watermarked_img.save(output_path, "JPEG", quality=95, exif=exif_bytes)
    return output_path

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
            f"⏱️ **ETA:** `{eta}s` | **Done:** `{current / (1024 * 1024):.2f} MB` / `{total / (1024 * 1024):.2f} MB`\n\n"
            f"❌ Stop karne ke liye `/cancel` send karein."
        )
        try:
            await status_msg.edit_text(progress_text)
        except Exception:
            pass

# --- COMMAND HANDLERS ---
@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text(
        "👋 **Welcome to Advance Audio & Photo Bot!**\n\n"
        "**Features:**\n"
        "• Single File Rename (`Filename | Artist`)\n"
        "• Batch Processing (`/batch Filename Ep 1 | Artist`)\n"
        "• Range Batch Processing (`/batch Filename Ep 1 TO 10 | Artist`)\n"
        "• Zip Extraction (`/extract Filename Ep 1 | Artist`)\n"
        "• Photo Tag & EXIF Watermark (`/photo TagName` - Reply to Photo)\n"
        "• Cancel Process (`/cancel`)\n\n"
        "**Thumbnail Commands:**\n"
        "• `/savethumb` (Reply to photo) | `/showthumb` | `/delthumb`\n\n"
        "**Caption Commands:**\n"
        "• `/setcaption <text>` | `/delcaption`"
    )

# --- PHOTO WATERMARK & METADATA HANDLER ---
@app.on_message(filters.command("photo"))
async def photo_watermark(client, message):
    replied = message.reply_to_message
    if not (replied and (replied.photo or (replied.document and replied.document.mime_type and replied.document.mime_type.startswith("image/")))):
        await message.reply_text("⚠️ Kripya kisi Photo par reply karke `/photo TagName` bhejein.")
        return

    args = message.text.split(None, 1)
    tag_text = args[1].strip() if len(args) > 1 else "Anubhav"

    status_msg = await message.reply_text("🖼️ Photo mein tag & EXIF metadata add ho raha hai...")

    downloaded_path = await client.download_media(replied)

    try:
        wm_image_path = process_photo_metadata(downloaded_path, text=tag_text)

        await status_msg.edit_text("⬆️ Uploading photo...")
        await client.send_photo(
            chat_id=message.chat.id,
            photo=wm_image_path,
            caption=f"✅ **Tag & EXIF Metadata Added:** `{tag_text}`"
        )
        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: `{str(e)}`")

    if os.path.exists(downloaded_path):
        os.remove(downloaded_path)
    if 'wm_image_path' in locals() and os.path.exists(wm_image_path):
        os.remove(wm_image_path)

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

# Cancel Command
@app.on_message(filters.command("cancel"))
async def cancel_process(client, message):
    chat_id = message.chat.id
    if is_processing.get(chat_id, False):
        cancel_requested[chat_id] = True
        await message.reply_text("🛑 Process cancel ho raha hai...")
    else:
        await message.reply_text("⚠️ Koi active process nahi chal raha.")

# --- FILE RECEIVER ---
@app.on_message(filters.audio | filters.document)
async def handle_files(client, message):
    chat_id = message.chat.id
    
    if message.document and message.document.file_name and message.document.file_name.endswith(('.zip', '.rar')):
        user_files[chat_id] = message
        await message.reply_text("📦 **Zip File Detected!**\nExtract command:\n👉 `/extract LSOTMK EP 1 | Artist Name`")
        return

    is_audio = message.audio or (message.document and message.document.mime_type and message.document.mime_type.startswith("audio/"))
    
    if is_audio:
        user_files[chat_id] = message
        user_queues.setdefault(chat_id, []).append(message)
        
        await message.reply_text(
            f"📥 File Queue me add hui (`Total: {len(user_queues[chat_id])}`).\n\n"
            "• **Batch:** `/batch LSOTMK EP 1 | Artist Name`\n"
            "• **Range Batch:** `/batch LSOTMK EP 1 TO 10 | Artist Name`"
        )
    else:
        await message.reply_text("❌ Kripya Audio ya Zip file bhejein.")

# --- BATCH HANDLER WITH ARTIST SUPPORT ---
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
    if len(args) < 2:
        await message.reply_text("⚠️ **Format:** `/batch LSOTMK EP 1 | Artist Name`")
        return

    raw_input = args[1].strip()
    
    if "|" in raw_input:
        text_arg, artist_name = [p.strip() for p in raw_input.split("|", 1)]
    else:
        text_arg = raw_input
        artist_name = "Unknown Artist"

    range_match = re.search(r'^(.*?)\s*(\d+)\s*(?:TO|-)\s*(\d+)$', text_arg, re.IGNORECASE)
    single_match = re.search(r'^(.*?)\s*(\d+)$', text_arg) if not range_match else None

    if range_match:
        mode = "RANGE"
        base_name = range_match.group(1).strip()
        start_num = int(range_match.group(2))
        end_num = int(range_match.group(3))
        step = (end_num - start_num) + 1
    elif single_match:
        mode = "SINGLE"
        base_name = single_match.group(1).strip()
        start_num = int(single_match.group(2))
    else:
        mode = "SINGLE"
        base_name = text_arg
        start_num = 1

    files_to_process = user_queues[chat_id].copy()
    user_queues[chat_id] = []
    is_processing[chat_id] = True
    cancel_requested[chat_id] = False

    status_msg = await message.reply_text(f"🔄 Batch process shuru ho raha hai (`Total: {len(files_to_process)}` files)...")

    user_data = users_db.find_one({"user_id": user_id})
    thumb_path = await client.download_media(user_data["thumb"], file_name=f"temp_thumb_{user_id}.jpg") if user_data and user_data.get("thumb") else None

    total_files = len(files_to_process)
    
    for index, msg in enumerate(files_to_process):
        if cancel_requested.get(chat_id, False):
            await status_msg.edit_text("🚫 **Batch Process Cancel Kar Diya Gaya!**")
            break

        if mode == "RANGE":
            curr_start = start_num + (index * step)
            curr_end = curr_start + step - 1
            new_filename = f"{base_name} {curr_start} TO {curr_end}.mp3"
        else:
            current_ep = start_num + index
            new_filename = f"{base_name} {current_ep}.mp3"

        clean_title = os.path.splitext(new_filename)[0]
        caption_text = user_data.get("caption", f"🎵 **Title:** `{clean_title}`\n👤 **Artist:** `{artist_name}`") if user_data else f"🎵 **Title:** `{clean_title}`\n👤 **Artist:** `{artist_name}`"

        start_time = time.time()
        file_path = await msg.download(
            file_name=new_filename,
            progress=progress_bar,
            progress_args=(status_msg, start_time, f"⬇️ Down ({index+1}/{total_files})")
        )

        if cancel_requested.get(chat_id, False):
            if os.path.exists(file_path):
                os.remove(file_path)
            await status_msg.edit_text("🚫 **Batch Process Cancel Kar Diya Gaya!**")
            break

        start_time = time.time()
        await client.send_audio(
            chat_id=chat_id,
            audio=file_path,
            thumb=thumb_path,
            file_name=new_filename,
            title=clean_title,
            performer=artist_name,
            caption=caption_text,
            progress=progress_bar,
            progress_args=(status_msg, start_time, f"⬆️ Up ({index+1}/{total_files})")
        )

        if os.path.exists(file_path):
            os.remove(file_path)
            
        await asyncio.sleep(1)

    if thumb_path and os.path.exists(thumb_path):
        os.remove(thumb_path)

    is_processing[chat_id] = False
    if not cancel_requested.get(chat_id, False):
        await status_msg.edit_text(f"🎉 Sabhi `{total_files}` files process ho gayi!")
    cancel_requested[chat_id] = False

# --- ZIP EXTRACT HANDLER WITH ARTIST SUPPORT ---
@app.on_message(filters.command("extract"))
async def process_zip(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if chat_id not in user_files or not user_files[chat_id].document:
        await message.reply_text("⚠️ Pehle `.zip` file bhejein!")
        return

    raw_input = message.text.split(None, 1)[1].strip() if len(message.command) > 1 else "Episode 1"
    
    if "|" in raw_input:
        text_arg, artist_name = [p.strip() for p in raw_input.split("|", 1)]
    else:
        text_arg = raw_input
        artist_name = "Unknown Artist"

    match = re.search(r'^(.*?)\s*(\d+)$', text_arg)
    custom_prefix = match.group(1).strip() if match else text_arg
    start_num = int(match.group(2)) if match else 1

    zip_msg = user_files[chat_id]
    status_msg = await message.reply_text("⬇️ Zip Download ho rahi hai...")

    is_processing[chat_id] = True
    cancel_requested[chat_id] = False
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
            if cancel_requested.get(chat_id, False):
                await status_msg.edit_text("🚫 **Zip Extraction Cancel Kar Diya Gaya!**")
                break

            new_filename = f"{custom_prefix} {count}.mp3"
            clean_title = os.path.splitext(new_filename)[0]
            caption_text = user_data.get("caption", f"🎵 **Title:** `{clean_title}`\n👤 **Artist:** `{artist_name}`") if user_data else f"🎵 **Title:** `{clean_title}`\n👤 **Artist:** `{artist_name}`"

            await status_msg.edit_text(f"⏳ Uploading Extracted: `{new_filename}`\n❌ Stop karne ke liye `/cancel` send karein.")
            await client.send_audio(
                chat_id=chat_id,
                audio=file_full_path,
                thumb=thumb_path,
                file_name=new_filename,
                title=clean_title,
                performer=artist_name,
                caption=caption_text
            )
            count += 1
            await asyncio.sleep(1)

        if not cancel_requested.get(chat_id, False):
            await status_msg.edit_text(f"🎉 Total `{len(audio_files)}` files extract hokar rename ho gayi!")

    except Exception as e:
        await status_msg.edit_text(f"❌ Zip Error: `{str(e)}`")

    if os.path.exists(zip_path):
        os.remove(zip_path)
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    if thumb_path and os.path.exists(thumb_path):
        os.remove(thumb_path)

    is_processing[chat_id] = False
    cancel_requested[chat_id] = False
    del user_files[chat_id]

# --- SINGLE AUDIO RENAME HANDLER WITH ARTIST SUPPORT ---
@app.on_message(filters.text & ~filters.command(["start", "savethumb", "showthumb", "delthumb", "setcaption", "delcaption", "clear", "batch", "extract", "photo", "cancel"]))
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
        raw_name = parts[0].strip()
        artist_name = parts[1].strip()
    else:
        raw_name = text_input
        artist_name = "Unknown Artist"

    range_match = re.search(r'^(.*?)\s*(\d+)\s*(?:TO|-)\s*(\d+)$', raw_name, re.IGNORECASE)
    
    if range_match:
        base_name = range_match.group(1).strip()
        start_num = range_match.group(2)
        end_num = range_match.group(3)
        new_filename = f"{base_name} {start_num} TO {end_num}.mp3"
    else:
        new_filename = raw_name if (raw_name.endswith(".mp3") or raw_name.endswith(".m4a")) else f"{raw_name}.mp3"

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
    keep_alive()
    app.run()
