import os
import json
import uuid
from datetime import datetime, timezone
import requests
import telebot
from telebot import apihelper, types
import yt_dlp

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

apihelper.API_URL = "http://localhost:8081/bot{0}/{1}"
apihelper.FILE_URL = "http://localhost:8081"

bot = telebot.TeleBot(BOT_TOKEN)

DOWNLOAD_DIR = "downloads"
STATS_FILE = "users_stats.json"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
user_links = {}

try:
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/logOut", timeout=10)
except Exception:
    pass


def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"users": {}, "total_downloads": 0}


def save_stats(data):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def track_user(user, increment_download=False):
    stats = load_stats()
    uid = str(user.id)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if uid not in stats["users"]:
        stats["users"][uid] = {
            "name": user.first_name or "User",
            "username": user.username or "",
            "joined": today,
            "last_active": today,
            "downloads": 0
        }
    else:
        stats["users"][uid]["last_active"] = today
        if user.first_name:
            stats["users"][uid]["name"] = user.first_name

    if increment_download:
        stats["users"][uid]["downloads"] = stats["users"][uid].get("downloads", 0) + 1
        stats["total_downloads"] = stats.get("total_downloads", 0) + 1

    save_stats(stats)
    return stats


def get_stats_summary(stats):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    users = stats.get("users", {})
    total_users = len(users)
    active_today = sum(1 for u in users.values() if u.get("last_active") == today)
    active_users = sum(1 for u in users.values() if u.get("downloads", 0) > 0)
    total_downloads = stats.get("total_downloads", 0)

    return (
        f"📊 **إحصائيات البوت:**\n"
        f"👥 إجمالي المستخدمين: **{total_users}**\n"
        f"🔥 مستخدمون فعليون (قاموا بالتحميل): **{active_users}**\n"
        f"📅 نشطون اليوم: **{active_today}**\n"
        f"📥 إجمالي التحميلات: **{total_downloads}**"
    )


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    stats = track_user(message.from_user)
    summary = get_stats_summary(stats)
    bot.reply_to(
        message,
        "أهلاً بك! 👋\n"
        "أرسل لي أي رابط فيديو (YouTube, TikTok, Facebook, Instagram...) وسأقوم بتحميله لك بأعلى جودة (يدعم حتى 2GB).\n\n"
        f"{summary}",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["stats"])
def send_stats(message):
    stats = track_user(message.from_user)
    bot.reply_to(message, get_stats_summary(stats), parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text and m.text.startswith(("http://", "https://")))
def handle_url(message):
    track_user(message.from_user)
    url = message.text.strip()
    req_id = str(uuid.uuid4())[:8]
    user_links[req_id] = url

    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_video = types.InlineKeyboardButton("🎬 فيديو (أعلى جودة MP4)", callback_data=f"vid|{req_id}")
    btn_audio = types.InlineKeyboardButton("🎵 صوت (MP3)", callback_data=f"aud|{req_id}")
    markup.add(btn_video, btn_audio)

    bot.reply_to(message, "اختر صيغة التحميل المطلوبة:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith(("vid|", "aud|")))
def process_download(call):
    mode, req_id = call.data.split("|")
    url = user_links.pop(req_id, None)

    if not url:
        bot.answer_callback_query(call.id, "انتهت صلاحية هذا الطلب، أرسل الرابط مجدداً.", show_alert=True)
        return

    bot.answer_callback_query(call.id)
    status_msg = bot.edit_message_text(
        "⏳ جاري التحميل بأعلى جودة... يرجى الانتظار.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id
    )

    file_id = str(uuid.uuid4())[:8]
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    if mode == "vid":
        ydl_opts = {
            "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
        }
    else:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }

    downloaded_file = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Media")

        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                downloaded_file = os.path.join(DOWNLOAD_DIR, f)
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            raise FileNotFoundError("لم يتم العثور على الملف بعد التحميل.")

        file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        if file_size_mb > 1950:
            bot.edit_message_text(
                f"❌ حجم الملف ({file_size_mb:.1f} MB) يتجاوز الحد الأقصى لتيليجرام (2 GB).",
                chat_id=call.message.chat.id,
                message_id=status_msg.message_id
            )
            return

        bot.edit_message_text(
            f"📤 جاري الرفع إلى تيليجرام ({file_size_mb:.1f} MB)...",
            chat_id=call.message.chat.id,
            message_id=status_msg.message_id
        )

        with open(downloaded_file, "rb") as media:
            if mode == "vid":
                bot.send_video(
                    call.message.chat.id,
                    media,
                    caption=f"🎬 {title}",
                    supports_streaming=True,
                    timeout=600
                )
            else:
                bot.send_audio(
                    call.message.chat.id,
                    media,
                    title=title,
                    timeout=600
                )

        # تسجيل عملية التحميل الناجحة للمستخدم
        track_user(call.from_user, increment_download=True)
        bot.delete_message(call.message.chat.id, status_msg.message_id)

    except Exception as e:
        bot.edit_message_text(
            f"❌ حدث خطأ أثناء التحميل:\n`{str(e)[:200]}`",
            chat_id=call.message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="Markdown"
        )
    finally:
        if downloaded_file and os.path.exists(downloaded_file):
            try:
                os.remove(downloaded_file)
            except OSError:
                pass


if __name__ == "__main__":
    print("Bot is running with Local Bot API & User Stats...")
    bot.infinity_polling(skip_pending=True)
