import io
import logging
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import requests
import telebot
from telebot import types
import yt_dlp
from yt_dlp.utils import DownloadError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Secret is not configured")

bot = telebot.TeleBot(BOT_TOKEN)
search_cache = {}
youtube_choice_sessions = {}
PAGE_SIZE = 10
RESULT_LIMIT = 50
MAX_VIDEO_BYTES = 48_000_000
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mp4", ".mkv", ".webm"}

@bot.message_handler(commands=["start", "help"])
def start(message):
    bot.reply_to(
        message,
        "👋 Assalomu alaykum! Sifatli musiqalar va videolar yuklovchi botga xush kelibsiz!\n\n"
        "🎧 Musiqa eshitish uchun shunchaki qo‘shiq nomini yoki ijrochini yozib yuboring.\n"
        "📥 TikTok, Instagram yoki YouTube'dan video yuklash uchun uning havolasini (linkini) tashlang!"
    )

@bot.message_handler(func=lambda message: bool(message.text) and not message.text.startswith("/"))
def handle_message(message):
    text = message.text.strip()
    
    if text.startswith("http://") or text.startswith("https://"):
        if "youtube.com" in text or "youtu.be" in text:
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("360p", callback_data=f"yt:360:{text}"),
                types.InlineKeyboardButton("480p", callback_data=f"yt:480:{text}"),
                types.InlineKeyboardButton("720p", callback_data=f"yt:720:{text}"),
                types.InlineKeyboardButton("1080p", callback_data=f"yt:1080:{text}"),
                types.InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"yt:audio:{text}")
            )
            bot.send_message(message.chat.id, "🎬 YouTube videosi uchun sifatni tanlang:", reply_markup=markup)
        else:
            bot.send_chat_action(message.chat.id, 'upload_video')
            msg = bot.reply_to(message, "⏳ Video yuklanmoqda, iltimos kuting...")
            try:
                res = requests.post("https://cobalt.tools", json={"url": text}, headers={"Accept": "application/json"})
                video_url = res.json().get("url")
                if video_url:
                    bot.send_video(message.chat.id, video_url, caption="Musiqa qidirish va eshitish uchun shunchaki qo‘shiq nomini yozib yuboring 🎧")
                    bot.delete_message(message.chat.id, msg.message_id)
                else:
                    bot.edit_message_text("❌ Videoni yuklab bo'lmadi. Serverda yuklama yuqori bo'lishi mumkin.", message.chat.id, msg.message_id)
            except Exception:
                bot.edit_message_text("⚠️ Yuklashda xatolik yuz berdi.", message.chat.id, msg.message_id)
        return

    query = text
    try:
        response = requests.get("https://apple.com", params={"term": query, "entity": "song", "limit": RESULT_LIMIT}, timeout=15)
        results = response.json().get("results", [])
    except Exception:
        bot.reply_to(message, "⚠️ Qidiruv vaqtida xatolik yuz berdi.")
        return

    songs = [{"title": item.get("trackName"), "artist": item.get("artistName"), "preview_url": item.get("previewUrl")} for item in results if item.get("trackName") and item.get("artistName")]
    
    if not songs:
        bot.reply_to(message, "❌ Hech narsa topilmadi. Boshqa nom bilan qidirib ko‘ring.")
        return

    cache_key = (message.chat.id, message.message_id)
    search_cache[cache_key] = {"songs": songs, "page": 0}
    send_page(message.chat.id, message.message_id, cache_key)

def send_page(chat_id, reply_to_id, cache_key, message_id=None):
    data = search_cache.get(cache_key)
    songs = data["songs"]
    page = data["page"]
    
    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_songs = songs[start_idx:end_idx]

    lines = ["🎵 Topilgan qo‘shiqlar:", ""]
    buttons = []
    for i, song in enumerate(page_songs, start=1):
        actual_idx = start_idx + i
        lines.append(f"{actual_idx}. {song['title']} — {song['artist']}")
        buttons.append(types.InlineKeyboardButton(text=str(actual_idx), callback_data=f"pick:{reply_to_id}:{actual_idx-1}"))

    markup = types.InlineKeyboardMarkup()
    markup.row(*buttons[:5])
    markup.row(*buttons[5:])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ Orqaga", callback_data=f"nav:{reply_to_id}:{page-1}"))
    if end_idx < len(songs):
        nav_buttons.append(types.InlineKeyboardButton("Keyingi ➡️", callback_data=f"nav:{reply_to_id}:{page+1}"))
    if nav_buttons:
        markup.row(*nav_buttons)

    text = "\n".join(lines) + "\n\n👇 Yuklab olish uchun raqamni tanlang:"
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_to_message_id=reply_to_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data.startswith("pick:"):
        _, msg_id, idx = call.data.split(":")
        cache_key = (call.message.chat.id, int(msg_id))
        data = search_cache.get(cache_key)
        if not data:
            bot.answer_callback_query(call.id, "Natija eskirgan. Qayta qidiring.")
            return
        song = data["songs"][int(idx)]
        if not song["preview_url"]:
            bot.answer_callback_query(call.id, "Preview mavjud emas.")
            return
        
        bot.answer_callback_query(call.id, "🎵 Musiqa yuklanmoqda...")
        res = requests.get(song["preview_url"])
        audio_file = io.BytesIO(res.content)
        audio_file.name = f"{song['title']}.m4a"
        bot.send_audio(call.message.chat.id, audio_file, title=song["title"], performer=song["artist"], caption="Musiqa qidirish uchun shunchaki qo‘shiq nomini yozing 🎧")

    elif call.data.startswith("nav:"):
        _, msg_id, new_page = call.data.split(":")
        cache_key = (call.message.chat.id, int(msg_id))
        if cache_key in search_cache:
            search_cache[cache_key]["page"] = int(new_page)
            send_page(call.message.chat.id, int(msg_id), cache_key, call.message.message_id)

    elif call.data.startswith("yt:"):
        _, quality, url = call.data.split(":")
        bot.answer_callback_query(call.id, "⏳ YouTube fayli tayyorlanmoqda...")
        msg = bot.send_message(call.message.chat.id, f"📥 YouTube yuklanmoqda ({quality})...\nIltimos kuting, bu biroz vaqt olishi mumkin.")
        
        def download_yt():
            try:
                ydl_opts = {
                    'format': 'bestaudio/best' if quality == 'audio' else f'bestvideo[height<={quality[:-1]}]+bestaudio/best',
                    'outtmpl': tempfile.gettempdir() + '/%(id)s.%(ext)s',
                    'max_filesize': MAX_VIDEO_BYTES,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    
                    if quality == 'audio':
                        with open(filename, 'rb') as f:
                            bot.send_audio(call.message.chat.id, f, title=info.get('title'), caption="Musiqa qidirish uchun shunchaki qo‘shiq nomini yozing 🎧")
                    else:
                        with open(filename, 'rb') as f:
                            bot.send_video(call.message.chat.id, f, caption="Musiqa qidirish uchun shunchaki qo‘shiq nomini yozing 🎧")
                bot.delete_message(call.message.chat.id, msg.message_id)
            except Exception as e:
                bot.edit_message_text("❌ Yuklashda xatolik yuz berdi yoki fayl o'lchami juda katta (max 50MB).", call.message.chat.id, msg.message_id)
                
        threading.Thread(target=download_yt).start()

if __name__ == "__main__":
    bot.infinity_polling()
