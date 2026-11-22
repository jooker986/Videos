import os
import re
import json
import uuid
import html
from urllib.parse import urlparse
from datetime import datetime, timezone
import requests
import telebot
from telebot import apihelper, types
import yt_dlp

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

# توجيه البوت لاستخدام سيرفر Telegram Local Bot API المحلي (يدعم حتى 2GB)
apihelper.API_URL = "http://localhost:8081/bot{0}/{1}"
apihelper.FILE_URL = "http://localhost:8081"

bot = telebot.TeleBot(BOT_TOKEN)

DOWNLOAD_DIR = "downloads"
STATS_FILE = "users_stats.json"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
user_links = {}

# قائمة النطاقات والكلمات المفتاحية المحظورة
BLOCKED_DOMAINS = {
    "pornhub.com", "xvideos.com", "xnxx.com", "xhamster.com", "redtube.com",
    "youporn.com", "tube8.com", "spankbang.com", "eporner.com", "hqporner.com",
    "beeg.com", "brazzers.com", "bangbros.com", "realitykings.com", "naughtyamerica.com",
    "chaturbate.com", "stripchat.com", "cam4.com", "myfreecams.com", "livejasmin.com",
    "onlyfans.com", "fansly.com", "manyvids.com", "clips4sale.com", "rule34.xxx",
    "hanime.tv", "hentaihaven.xxx", "nhentai.net", "e-hentai.org", "motherless.com",
    "tnaflix.com", "drtuber.com", "empflix.com", "slutload.com", "xtube.com",
    "fapello.com", "erome.com", "bunkr.si", "coomer.su", "kemono.su", "javhd.com"
}

BLOCKED_KEYWORDS = [
    "porn", "xxx", "xnxx", "xvideos", "xhamster", "hentai", "nsfw", "nude",
    "nudes", "sex", "erotic", "onlyfans", "chaturbate", "camgirl", "milf",
    "blowjob", "handjob", "creampie", "gangbang", "incest", "jav", "rule34",
    "سكس", "إباحي", "اباحي", "نيك", "بورن", "شرموط", "قحبة", "عاري", "عارية", "للكبار فقط"
]

# تسجيل الخروج من السيرفر الرسمي لتيليجرام للانتقال للسيرفر المحلي تلقائياً
try:
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/logOut", timeout=10)
except Exception:
    pass


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def is_nsfw_url(url):
    """فحص مبدئي للرابط والنطاق قبل بدء المعالجة"""
    try:
        parsed = urlparse(url.lower())
        domain = parsed.netloc.replace("www.", "")
        for blocked in BLOCKED_DOMAINS:
            if domain == blocked or domain.endswith("." + blocked):
                return True
        full_url_lower = url.lower()
        for kw in BLOCKED_KEYWORDS:
            if re.search(rf"\b{re.escape(kw)}\b", full_url_lower):
                return True
    except Exception:
        pass
    return False


def is_nsfw_metadata(info):
    """فحص بيانات الفيديو المستخرجة من yt-dlp (المستخرج، التصنيف العمري، العنوان، الوسوم)"""
    if not info:
        return False

    # 1. فحص التصنيف العمري (18+)
    age_limit = info.get("age_limit") or 0
    if age_limit >= 18:
        return True

    # 2. فحص اسم المستخرج (Extractor) في yt-dlp
    extractor = (info.get("extractor") or info.get("extractor_key") or "").lower()
    for blocked in BLOCKED_DOMAINS:
        site_name = blocked.split(".")[0]
        if site_name in extractor:
            return True

    # 3. فحص العنوان والوصف والوسوم (Tags)
    title = info.get("title") or ""
    description = info.get("description") or ""
    tags = " ".join(info.get("tags") or [])
    categories = " ".join(info.get("categories") or [])
    combined_text = f"{title} {description[:500]} {tags} {categories}".lower()

    for kw in BLOCKED_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", combined_text):
            return True

    return False


def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"users": {}, "total_downloads": 0, "blocked_attempts": 0}


def save_stats(data):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def track_user(user, increment_download=False, increment_blocked=False):
    stats = load_stats()
    uid = str(user.id)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if uid not in stats["users"]:
        stats["users"][uid] = {
            "name": user.first_name or "User",
            "username": user.username or "",
            "joined": today,
            "last_active": today,
            "downloads": 0,
            "blocked": 0
        }
    else:
        stats["users"][uid]["last_active"] = today
        if user.first_name:
            stats["users"][uid]["name"] = user.first_name
        if user.username:
            stats["users"][uid]["username"] = user.username

    if increment_download:
        stats["users"][uid]["downloads"] = stats["users"][uid].get("downloads", 0) + 1
        stats["total_downloads"] = stats.get("total_downloads", 0) + 1

    if increment_blocked:
        stats["users"][uid]["blocked"] = stats["users"][uid].get("blocked", 0) + 1
        stats["blocked_attempts"] = stats.get("blocked_attempts", 0) + 1

    save_stats(stats)
    return stats


def get_admin_stats_report(stats):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    users = stats.get("users", {})
    total_users = len(users)
    active_today = sum(1 for u in users.values() if u.get("last_active") == today)
    active_users = sum(1 for u in users.values() if u.get("downloads", 0) > 0)
    total_downloads = stats.get("total_downloads", 0)
    blocked_attempts = stats.get("blocked_attempts", 0)

    recent_users = list(users.items())[-10:]
    users_list_text = ""
    for uid, info in reversed(recent_users):
        safe_name = html.escape(info.get("name", "User"))
        if info.get("username"):
            safe_uname = f"@{html.escape(info['username'])}"
        else:
            safe_uname = f"<code>{uid}</code>"
        users_list_text += f"- {safe_name} ({safe_uname}) | تحميلات: {info.get('downloads', 0)}\n"

    return (
        f"🔐 <b>لوحة إحصائيات الأدمن:</b>\n"
        f"👥 إجمالي المستخدمين: <b>{total_users}</b>\n"
        f"🔥 مستخدمون فعليون (قاموا بالتحميل): <b>{active_users}</b>\n"
        f"📅 نشطون اليوم: <b>{active_today}</b>\n"
        f"📥 إجمالي التحميلات: <b>{total_downloads}</b>\n"
        f"🚫 محاولات محظورة (NSFW): <b>{blocked_attempts}</b>\n\n"
        f"🕒 <b>آخر المستخدمين:</b>\n{users_list_text or 'لا يوجد بعد'}"
    )


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    stats = track_user(message.from_user)
    welcome_text = (
        "أهلاً بك! 👋\n"
        "أرسل لي أي رابط فيديو (YouTube, TikTok, Facebook, Instagram...) وسأقوم بتحميله لك بأعلى جودة (يدعم حتى 2GB).\n"
        "⚠️ يُمنع استخدام البوت لتحميل أي محتوى إباحي أو مخالف."
    )

    if is_admin(message.from_user.id):
        welcome_text += f"\n\n{get_admin_stats_report(stats)}"
        bot.reply_to(message, welcome_text, parse_mode="HTML")
    else:
        bot.reply_to(message, welcome_text)


@bot.message_handler(commands=["stats"])
def send_stats(message):
    if not is_admin(message.from_user.id):
        return
    stats = track_user(message.from_user)
    bot.reply_to(message, get_admin_stats_report(stats), parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text and m.text.startswith(("http://", "https://")))
def handle_url(message):
    url = message.text.strip()

    # المستوى الأول من الفلترة: فحص الرابط والنطاق فوراً
    if is_nsfw_url(url):
        track_user(message.from_user, increment_blocked=True)
        bot.reply_to(message, "🚫 عذراً، هذا الرابط محظور لاحتوائه على محتوى إباحي أو غير لائق.")
        return

    track_user(message.from_user)
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

    if is_nsfw_url(url):
        track_user(call.from_user, increment_blocked=True)
        bot.edit_message_text(
            "🚫 عذراً، هذا الرابط محظور لاحتوائه على محتوى إباحي أو غير لائق.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
        return

    bot.answer_callback_query(call.id)
    status_msg = bot.edit_message_text(
        "🔍 جاري فحص الرابط والتحقق من المحتوى...",
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
        # المستوى الثاني من الفلترة: جلب البيانات الوصفية أولاً بدون تحميل الملف
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as ydl_check:
            info = ydl_check.extract_info(url, download=False)

        if is_nsfw_metadata(info):
            track_user(call.from_user, increment_blocked=True)
            bot.edit_message_text(
                "🚫 تم حظر التحميل: المحتوى مصنف للبالغين (+18) أو غير لائق.",
                chat_id=call.message.chat.id,
                message_id=status_msg.message_id
            )
            return

        bot.edit_message_text(
            "⏳ جاري التحميل بأعلى جودة... يرجى الانتظار.",
            chat_id=call.message.chat.id,
            message_id=status_msg.message_id
        )

        # بدء التحميل الفعلي بعد اجتياز الفلترة
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

        track_user(call.from_user, increment_download=True)
        bot.delete_message(call.message.chat.id, status_msg.message_id)

    except Exception as e:
        safe_err = html.escape(str(e)[:200])
        bot.edit_message_text(
            f"❌ حدث خطأ أثناء التحميل:\n<code>{safe_err}</code>",
            chat_id=call.message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="HTML"
        )
    finally:
        if downloaded_file and os.path.exists(downloaded_file):
            try:
                os.remove(downloaded_file)
            except OSError:
                pass


if __name__ == "__main__":
    print("Bot is running with NSFW Filter, Local Bot API & Admin Stats...")
    bot.infinity_polling(skip_pending=True)
