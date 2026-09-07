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
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pymongo import MongoClient
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
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
queue_messages = {}
is_processing = {}
cancel_requested = {}

# --- WATERMARK & ENHANCE HELPER FUNCTION ---
def process_photo_metadata(image_path, text="Anubhav"):
    img = Image.open(image_path).convert("RGBA")
    
    # Image Quality Enhancement (Sharpness & Color)
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1.5)
    
    enhancer_color = ImageEnhance.Color(img)
    img = enhancer_color.enhance(1.1)

    txt_layer = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt_layer)
    
    height = img.size[1]
    font_size = int(height * 0.30)

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        font = ImageFont.load_default()

    margin = int(height * 0.05)
    x = margin
    y = margin

    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)

    watermarked_img = Image.alpha_composite(img, txt_layer).convert("RGB")
    
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    output_path = f"wm_{base_name}.jpg"

    zeroth_ifd = {
        piexif.ImageIFD.Artist: text.encode('utf-8'),
        piexif.ImageIFD.ImageDescription: text.encode('utf-8'),
        piexif.ImageIFD.Copyright: text.encode('utf-8')
    }
    exif_bytes = piexif.dump({"0th": zeroth_ifd})

    watermarked_img.save(output_path, "JPEG", quality=100, subsampling=0, exif=exif_bytes)
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
            f"⏱️ **ETA:** `{eta}s` | **Done:** `{current / (1024 * 1024):.2f} MB` / `{total / (1024 * 1024):.2f} MB`"
        )
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Stop Process", callback_data="btn_cancel")]])
        try:
            await status_msg.edit_text(progress_text, reply_markup=cancel_btn)
        except Exception:
            pass

# --- UI KEYBOARD BUILDERS ---
def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 Commands Menu", callback_data="menu_commands"), InlineKeyboardButton("👨‍💻 Developer", callback_data="menu_developer")],
        [InlineKeyboardButton("🗑️ Clear Queue", callback_data="btn_clear"), InlineKeyboardButton("🛑 Cancel Action", callback_data="btn_cancel")]
    ])

def get_commands_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Watermark Photo", callback_data="cmd_photo"), InlineKeyboardButton("⚡ Batch Run", callback_data="cmd_batch")],
        [InlineKeyboardButton("🖼️ Show Thumbnail", callback_data="cmd_showthumb"), InlineKeyboardButton("🗑️ Delete Thumbnail", callback_data="cmd_delthumb")],
        [InlineKeyboardButton("📝 Delete Caption", callback_data="cmd_delcaption"), InlineKeyboardButton("🗑️ Clear Queue", callback_data="btn_clear")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")]
    ])

def get_developer_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨‍💻 Contact Developer (Anubhav)", url="https://t.me/kcxry")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")]
    ])

# --- COMMAND HANDLERS & INLINE MENUS ---
@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text(
        "👋 **Welcome to Advance Audio & Photo Bot!**\n\n"
        "Neeche diye gaye buttons se menu explore karein ya commands run karein:",
        reply_markup=get_main_menu()
    )

# --- CALLBACK QUERY HANDLER FOR INLINE BUTTONS ---
@app.on_callback_query()
async def cb_handler(client, query: CallbackQuery):
    data = query.data
    chat_id = query.message.chat.id

    # NAVIGATION MENUS
    if data == "menu_main":
        await query.message.edit_text(
            "👋 **Welcome to Advance Audio & Photo Bot!**\n\n"
            "Neeche diye gaye buttons se menu explore karein ya commands run karein:",
            reply_markup=get_main_menu()
        )

    elif data == "menu_commands":
        await query.message.edit_text(
            "📜 **Commands List & Inline Runners:**\n\n"
            "• `/photo TagName` - Watermark Photo (Reply to Photo)\n"
            "• `/batch Title Ep 1 | Artist` - Process Queue\n"
            "• `/savethumb` - Save Custom Thumbnail (Reply to Photo)\n"
            "• `/showthumb` - View Saved Thumbnail\n"
            "• `/delthumb` - Delete Saved Thumbnail\n"
            "• `/setcaption Text` - Set Custom Caption\n"
            "• `/delcaption` - Delete Custom Caption\n"
            "• `/cancel` - Stop Running Task\n\n"
            "👉 **Neeche kisi bhi button par click karke direct feature trigger karein:**",
            reply_markup=get_commands_menu()
        )

    elif data == "menu_developer":
        dev_text = (
            "👨‍💻 **Developer Details & Bot Info:**\n\n"
            "👤 **Developer:** [Anubhav](https://t.me/kcxry)\n"
            "🤖 **Bot Version:** v2.5 Advance\n"
            "⚡ **Framework:** Pyrogram + Python 3.10\n"
            "🌐 **Database:** MongoDB Atlas\n\n"
            "💡 *Aap is bot se High-Quality audio renaming, zip extract, custom caption, aur 100% clean photo watermarking kar sakte hain.*"
        )
        await query.message.edit_text(dev_text, reply_markup=get_developer_menu(), disable_web_page_preview=True)

    # DIRECT COMMAND RUNNERS VIA INLINE BUTTONS
    elif data == "cmd_photo":
        await query.message.edit_text(
            "🖼️ **Photo Watermark Mode:**\n\n"
            "Kise bhi photo par reply karke likhein:\n"
            "`/photo Your Name`\n\n"
            "*(Bot bina kisi background box ke clean white bold font me watermark lagayega).*",
            reply_markup=get_commands_menu()
        )

    elif data == "cmd_batch":
        await query.message.edit_text(
            "⚡ **Batch Execution:**\n\n"
            "1. Pehle saari audio files bhej dein.\n"
            "2. Uske baad bhejien:\n`/batch LSOTMK EP 1 | Artist Name`\n\n"
            "Range Format:\n`/batch LSOTMK EP 1 TO 10 | Artist Name`",
            reply_markup=get_commands_menu()
        )

    elif data == "cmd_showthumb":
        user_id = query.from_user.id
        user_data = users_db.find_one({"user_id": user_id})
        if user_data and user_data.get("thumb"):
            await client.send_photo(chat_id=chat_id, photo=user_data["thumb"], caption="🖼️ Saved Thumbnail")
            await query.answer("Thumbnail bhej diya gaya hai!")
        else:
            await query.answer("❌ Koi Thumbnail saved nahi hai.", show_alert=True)

    elif data == "cmd_delthumb":
        user_id = query.from_user.id
        users_db.update_one({"user_id": user_id}, {"$unset": {"thumb": ""}})
        await query.answer("🗑️ Thumbnail successfully delete ho gaya!", show_alert=True)

    elif data == "cmd_delcaption":
        user_id = query.from_user.id
        users_db.update_one({"user_id": user_id}, {"$unset": {"caption": ""}})
        await query.answer("🗑️ Caption successfully delete ho gaya!", show_alert=True)

    # ACTION BUTTONS
    elif data == "btn_clear":
        user_queues[chat_id] = []
        if chat_id in queue_messages:
            try:
                await queue_messages[chat_id].delete()
            except Exception:
                pass
            del queue_messages[chat_id]
        await query.answer("🗑️ Queue clear kar di gayi hai!", show_alert=True)

    elif data == "btn_cancel":
        if is_processing.get(chat_id, False):
            cancel_requested[chat_id] = True
            await query.answer("🛑 Process cancel request send kar di gayi hai.")
        else:
            await query.answer("⚠️ Koi active process nahi chal raha.", show_alert=True)

# --- PHOTO WATERMARK & METADATA HANDLER ---
@app.on_message(filters.command("photo"))
async def photo_watermark(client, message):
    replied = message.reply_to_message
    if not (replied and (replied.photo or (replied.document and replied.document.mime_type and replied.document.mime_type.startswith("image/")))):
        await message.reply_text("⚠️ Kripya kisi Photo par reply karke `/photo TagName` bhejein.")
        return

    args = message.text.split(None, 1)
    tag_text = args[1].strip() if len(args) > 1 else "Anubhav"

    status_msg = await message.reply_text("🖼️ Quality enhance karke white watermark lagaya ja raha hai...")
    downloaded_path = await client.download_media(replied)

    try:
        wm_image_path = process_photo_metadata(downloaded_path, text=tag_text)

        await status_msg.edit_text("⬆️ Enhanced Photo Upload ho rahi hai...")
        await client.send_photo(
            chat_id=message.chat.id,
            photo=wm_image_path,
            caption=f"✅ **Enhanced Photo & Clean Watermark Added:** `{tag_text}`"
        )
        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: `{str(e)}`")

    if os.path.exists(downloaded_path):
        os.remove(downloaded_path)
    if 'wm_image_path' in locals() and os.path.exists(wm_image_path):
        os.remove(wm_image_path)

# --- CLEAN FILE RECEIVER (NO SPAM MESSAGES) ---
@app.on_message(filters.audio | filters.document)
async def handle_files(client, message):
    chat_id = message.chat.id

    if message.document and message.document.file_name and message.document.file_name.endswith(('.zip', '.rar')):
        user_files[chat_id] = message
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📜 Extract Help", callback_data="menu_commands")]])
        await message.reply_text("📦 **Zip File Detected!**\nUse: `/extract LSOTMK EP 1 | Artist Name`", reply_markup=btn)
        return

    is_audio = message.audio or (message.document and message.document.mime_type and message.document.mime_type.startswith("audio/"))

    if is_audio:
        user_files[chat_id] = message
        user_queues.setdefault(chat_id, []).append(message)
        total_count = len(user_queues[chat_id])

        inline_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📜 Commands Menu", callback_data="menu_commands"), InlineKeyboardButton("🗑️ Clear Queue", callback_data="btn_clear")]
        ])

        if chat_id in queue_messages:
            try:
                await queue_messages[chat_id].edit_text(
                    f"📥 **Total Files Received:** `{total_count}`\n\nCommand bhejein: `/batch Title Ep 1 | Artist`",
                    reply_markup=inline_btn
                )
            except Exception:
                pass
        else:
            msg = await message.reply_text(
                f"📥 **Total Files Received:** `{total_count}`\n\nCommand bhejein: `/batch Title Ep 1 | Artist`",
                reply_markup=inline_btn
            )
            queue_messages[chat_id] = msg

# --- BATCH PROCESS HANDLER ---
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
    artist_name = raw_input.split("|", 1)[1].strip() if "|" in raw_input else "Unknown Artist"
    text_arg = raw_input.split("|", 1)[0].strip() if "|" in raw_input else raw_input

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
    
    if chat_id in queue_messages:
        del queue_messages[chat_id]

    is_processing[chat_id] = True
    cancel_requested[chat_id] = False

    status_msg = await message.reply_text(
        f"🔄 Batch process shuru ho raha hai (`Total: {len(files_to_process)}` files)...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Process", callback_data="btn_cancel")]])
    )

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

# --- THUMBNAIL & CAPTION COMMANDS ---
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

@app.on_message(filters.command("cancel"))
async def cancel_process(client, message):
    chat_id = message.chat.id
    if is_processing.get(chat_id, False):
        cancel_requested[chat_id] = True
        await message.reply_text("🛑 Process cancel ho raha hai...")
    else:
        await message.reply_text("⚠️ Koi active process nahi chal raha.")

if __name__ == "__main__":
    keep_alive()
    app.run()
