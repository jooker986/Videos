import os
import re
import json
import uuid
import html
import time
import signal
import logging
import threading
from urllib.parse import urlparse
from datetime import datetime, timezone
import requests
import telebot
from telebot import apihelper, types
import yt_dlp

# ==============================================================================
# 1. إعدادات السجل (Logging) والثوابت العامة
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("DownloaderBot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
USE_LOCAL_API = os.environ.get("USE_LOCAL_API", "1") == "1"

# إعدادات الاشتراك الإجباري في القناة
FORCE_SUB_CHANNEL = "@Jookerrr98"
FORCE_SUB_URL = "https://t.me/Jookerrr98"

if USE_LOCAL_API:
    apihelper.API_URL = "http://localhost:8081/bot{0}/{1}"
    apihelper.FILE_URL = "http://localhost:8081"
    MAX_FILE_SIZE_MB = 1950  # الحد الأقصى للسيرفر المحلي (2GB)
else:
    MAX_FILE_SIZE_MB = 49    # الحد الأقصى لسيرفر تيليجرام الرسمي (50MB)

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=8)

DOWNLOAD_DIR = "downloads"
STATS_FILE = "users_stats.json"
SITES_FILE = "supported_sites_clean.txt"
COOKIES_FILE = "cookies.txt"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

stats_lock = threading.Lock()
links_lock = threading.Lock()

user_requests = {}
admin_states = {}
is_shutting_down = False

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


# ==============================================================================
# 2. تهيئة السيرفر والتنظيف التلقائي والإغلاق الآمن (Graceful Shutdown)
# ==============================================================================
def handle_shutdown_signal(signum, frame):
    global is_shutting_down
    logger.info(f"Received shutdown signal ({signum}). Stopping polling gracefully to save stats...")
    is_shutting_down = True
    bot.stop_polling()


signal.signal(signal.SIGTERM, handle_shutdown_signal)
signal.signal(signal.SIGINT, handle_shutdown_signal)


def init_bot_session():
    if USE_LOCAL_API:
        try:
            requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/logOut", timeout=10)
            logger.info("Logged out from cloud API to attach Local Bot API (2GB support).")
        except Exception as e:
            logger.warning(f"Cloud logOut skipped or already done: {e}")


def background_cleaner():
    """مهمة خلفية لتنظيف الروابط منتهية الصلاحية والملفات المؤقتة العالقة"""
    while not is_shutting_down:
        time.sleep(600)
        now = time.time()
        with links_lock:
            expired = [k for k, v in user_requests.items() if now - v["ts"] > 1800]
            for k in expired:
                user_requests.pop(k, None)

        try:
            for fname in os.listdir(DOWNLOAD_DIR):
                fpath = os.path.join(DOWNLOAD_DIR, fname)
                if os.path.isfile(fpath) and (now - os.path.getmtime(fpath) > 3600):
                    os.remove(fpath)
        except Exception as e:
            logger.error(f"Cleanup error: {e}")


# ==============================================================================
# 3. نظام الاشتراك الإجباري في القناة (@Jookerrr98)
# ==============================================================================
def is_user_subscribed(user_id: int) -> bool:
    """التحقق من اشتراك المستخدم في القناة الإجبارية"""
    if is_admin(user_id):
        return True
    try:
        member = bot.get_chat_member(FORCE_SUB_CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"Force-sub check failed for {user_id}: {e}")
        # في حال لم يكن البوت مشرفاً في القناة بعد أو تعذر الاتصال، نمرر المستخدم لتجنب تعطل البوت
        return True


def get_force_sub_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📢 اشترك في القناة أولاً", url=FORCE_SUB_URL),
        types.InlineKeyboardButton("✅ تم الاشتراك — تحقق الآن", callback_data="check_sub")
    )
    return markup


def send_force_sub_prompt(chat_id: int, reply_to_message_id: int = None):
    text = (
        "⚠️ <b>عذراً، لا يمكنك استخدام البوت قبل الاشتراك في قناة البوت الرسمية!</b>\n\n"
        f"📢 القناة: {FORCE_SUB_CHANNEL}\n\n"
        "1️⃣ اضغط على زر <b>«اشترك في القناة أولاً»</b> بالأسفل.\n"
        "2️⃣ بعد الاشتراك، اضغط على زر <b>«تم الاشتراك — تحقق الآن»</b>."
    )
    bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_to_message_id=reply_to_message_id,
        reply_markup=get_force_sub_markup()
    )


# ==============================================================================
# 4. معالجة الروابط والفلترة الأمنية (NSFW Filter)
# ==============================================================================
def extract_youtube_id(url: str):
    patterns = [
        r'youtu\.be/([0-9A-Za-z_-]{11})',
        r'[?&]v=([0-9A-Za-z_-]{11})',
        r'/(?:v|embed|shorts|live)/([0-9A-Za-z_-]{11})',
        r'%3Fv%3D([0-9A-Za-z_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def normalize_url(url: str) -> str:
    url = url.strip().replace("yewtu.be", "youtube.com")
    if any(d in url.lower() for d in ("youtube.com", "youtu.be")):
        vid_id = extract_youtube_id(url)
        if vid_id:
            return f"https://www.youtube.com/watch?v={vid_id}"
    return url


def is_nsfw_url(url: str) -> bool:
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


def is_nsfw_metadata(info: dict) -> bool:
    if not info:
        return False

    if (info.get("age_limit") or 0) >= 18:
        return True

    extractor = (info.get("extractor") or info.get("extractor_key") or "").lower()
    for blocked in BLOCKED_DOMAINS:
        if blocked.split(".")[0] in extractor:
            return True

    title = info.get("title") or ""
    description = info.get("description") or ""
    tags = " ".join(info.get("tags") or [])
    categories = " ".join(info.get("categories") or [])
    combined_text = f"{title} {description[:500]} {tags} {categories}".lower()

    for kw in BLOCKED_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", combined_text):
            return True

    return False


# ==============================================================================
# 5. إعدادات yt-dlp واستخراج الجودات بدقة عالية
# ==============================================================================
def get_base_ydl_opts() -> dict:
    has_cookies = os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 0
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        "js_runtimes": {"node": {}},
        "remote_components": ["ejs:github"],
        "extractor_args": {
            "youtubepot-bgutilhttp": {
                "base_url": ["http://127.0.0.1:4416"]
            }
        }
    }

    if has_cookies:
        opts["cookiefile"] = COOKIES_FILE
        opts["extractor_args"]["youtube"] = {
            "player_client": ["web_creator", "mweb", "web"],
        }
    else:
        opts["extractor_args"]["youtube"] = {
            "player_client": ["tv", "android_vr", "web_embedded"],
        }

    return opts


def format_size(bytes_num: int) -> str:
    if not bytes_num or bytes_num <= 0:
        return ""
    mb = bytes_num / (1024 * 1024)
    if mb >= 1024:
        return f" ({mb / 1024:.2f} GB)"
    return f" ({mb:.1f} MB)"


def format_duration(seconds) -> str:
    if not seconds:
        return ""
    seconds = int(seconds)
    hrs, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    return f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs else f"{mins:02d}:{secs:02d}"


def extract_available_qualities(info: dict):
    formats = info.get("formats") or []
    best_audio_size = 0

    for f in formats:
        if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none"):
            a_size = f.get("filesize") or f.get("filesize_approx") or 0
            if a_size > best_audio_size:
                best_audio_size = a_size

    heights_map = {}
    for f in formats:
        vcodec = f.get("vcodec")
        ext = (f.get("ext") or "").lower()
        height = f.get("height")
        fmt_id = f.get("format_id")

        if not fmt_id or not vcodec or vcodec == "none" or not height or height < 144:
            continue
        if ext in ("mhtml", "webp", "jpg", "png") or "storyboard" in (f.get("format_note") or "").lower():
            continue

        v_size = f.get("filesize") or f.get("filesize_approx") or 0
        has_audio = f.get("acodec") not in (None, "none")
        total_size = v_size if (has_audio or v_size == 0) else (v_size + best_audio_size)
        fps = f.get("fps") or 0
        tbr = f.get("tbr") or 0

        score = (fps, tbr, total_size)
        if height not in heights_map or score > heights_map[height]["score"]:
            heights_map[height] = {
                "format_id": fmt_id,
                "has_audio": has_audio,
                "size": total_size,
                "fps": fps,
                "score": score
            }

    sorted_heights = sorted(heights_map.keys(), reverse=True)
    qualities = []
    for h in sorted_heights:
        item = heights_map[h]
        fps_tag = "60fps" if item["fps"] and item["fps"] >= 50 else ""
        size_tag = format_size(item["size"])
        label = f"🎬 {h}p {fps_tag}{size_tag}".replace("  ", " ").strip()
        qualities.append({
            "height": h,
            "label": label,
            "format_id": item["format_id"],
            "has_audio": item["has_audio"]
        })

    return qualities, format_size(best_audio_size)


def generate_clean_sites_file():
    if os.path.exists(SITES_FILE):
        return SITES_FILE
    try:
        extractors = yt_dlp.extractor.list_extractors(age_limit=17)
        clean_names = set()
        for ie in extractors:
            name = getattr(ie, "IE_NAME", "").split(":")[0].strip()
            if not name or name.lower() == "generic":
                continue
            name_lower = name.lower()
            if any(b.split(".")[0] in name_lower for b in BLOCKED_DOMAINS):
                continue
            if any(kw in name_lower for kw in BLOCKED_KEYWORDS):
                continue
            clean_names.add(name)

        sorted_sites = sorted(clean_names, key=lambda s: s.lower())
        with open(SITES_FILE, "w", encoding="utf-8") as f:
            f.write(f"قائمة جميع المواقع والمنصات المدعومة في البوت (إجمالي: {len(sorted_sites)} موقع)\n")
            f.write("=" * 60 + "\n\n")
            for idx, site in enumerate(sorted_sites, 1):
                f.write(f"{idx}. {site}\n")
        return SITES_FILE
    except Exception as e:
        logger.error(f"Error generating sites file: {e}")
        return None


# ==============================================================================
# 6. إدارة الإحصائيات والمستخدمين (Thread-Safe)
# ==============================================================================
def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def load_stats() -> dict:
    with stats_lock:
        if os.path.exists(STATS_FILE):
            try:
                with open(STATS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to read stats file: {e}")
        return {"users": {}, "total_downloads": 0, "blocked_attempts": 0}


def save_stats(data: dict):
    with stats_lock:
        tmp_file = f"{STATS_FILE}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, STATS_FILE)


def track_user(user, increment_download=False, increment_blocked=False) -> dict:
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


# ==============================================================================
# 7. القوائم والواجهات التفاعلية (UI Keyboards & Templates)
# ==============================================================================
def get_start_markup(user_id: int):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🌐 المواقع المدعومة (القائمة الكاملة)", callback_data="show_sites"),
        types.InlineKeyboardButton("📢 قناة البوت الرسمية", url=FORCE_SUB_URL)
    )
    if is_admin(user_id):
        markup.add(types.InlineKeyboardButton("🔐 لوحة تحكم الأدمن", callback_data="admin_panel"))
    return markup


def get_admin_panel_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 الإحصائيات العامة", callback_data="admin_stats"),
        types.InlineKeyboardButton("👥 قائمة المستخدمين", callback_data="admin_users")
    )
    markup.add(
        types.InlineKeyboardButton("🚫 سجل المحظورين", callback_data="admin_blocked"),
        types.InlineKeyboardButton("📢 إذاعة رسالة", callback_data="admin_broadcast")
    )
    markup.add(types.InlineKeyboardButton("📥 نسخة الإحصائيات", callback_data="admin_backup"))
    markup.add(types.InlineKeyboardButton("🔙 رجوع للبداية", callback_data="back_start"))
    return markup


def get_welcome_text() -> str:
    limit_text = "2GB" if USE_LOCAL_API else "50MB"
    return (
        "أهلاً بك في <b>بوت التحميل الشامل</b>! 👋\n\n"
        f"📥 أرسل لي أي رابط فيديو أو صوت وسأستخرج لك جميع الجودات المتاحة لتختار منها (حتى <b>4K</b> وبحجم يصل إلى <b>{limit_text}</b>).\n"
        "⚠️ يُمنع استخدام البوت لتحميل أي محتوى إباحي أو مخالف.\n\n"
        "اضغط على الزر بالأسفل لمعرفة جميع المواقع المدعومة 👇"
    )


def get_supported_sites_text() -> str:
    return (
        "🌐 <b>المواقع والمنصات المدعومة للتحميل:</b>\n\n"
        "🎬 <b>منصات الفيديو والبث المباشر:</b>\n"
        "• YouTube (فيديوهات، Shorts، قوائم تشغيل، بث مباشر)\n"
        "• Vimeo • Dailymotion • Twitch • Rumble • Bilibili • Kick • Trovo\n\n"
        "📱 <b>مواقع التواصل الاجتماعي:</b>\n"
        "• TikTok (بدون علامة مائية)\n"
        "• Instagram (Reels، منشورات، Stories، IGTV)\n"
        "• Facebook (فيديوهات عامة، Reels، Watch)\n"
        "• X (Twitter) • Reddit • Pinterest • Snapchat • LinkedIn • Threads • Tumblr • VK • OK.ru\n\n"
        "🎵 <b>منصات الصوت والموسيقى:</b>\n"
        "• SoundCloud • Bandcamp • Mixcloud • Audiomack • Apple Podcasts\n\n"
        "📰 <b>مواقع الأخبار والرياضة والتعليم:</b>\n"
        "• Al Jazeera • BBC • CNN • Sky News • ESPN • FIFA • TED • Coursera\n\n"
        "🔗 <b>روابط أخرى:</b>\n"
        "• روابط التخزين السحابي العامة (Google Drive, Dropbox, Archive.org, MediaFire)\n"
        "• روابط البث المباشر (<code>.m3u8</code> / <code>.mpd</code>)\n\n"
        "📄 <i>للحصول على القائمة الكاملة لجميع المواقع (+1,800 موقع)، اضغط على زر تحميل القائمة بالأسفل.</i>"
    )


# ==============================================================================
# 8. معالجات الأوامر والقوائم
# ==============================================================================
@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    admin_states.pop(message.from_user.id, None)
    track_user(message.from_user)

    if not is_user_subscribed(message.from_user.id):
        send_force_sub_prompt(message.chat.id, message.message_id)
        return

    bot.reply_to(
        message,
        get_welcome_text(),
        parse_mode="HTML",
        reply_markup=get_start_markup(message.from_user.id)
    )


@bot.message_handler(commands=["sites"])
def cmd_sites(message):
    track_user(message.from_user)
    if not is_user_subscribed(message.from_user.id):
        send_force_sub_prompt(message.chat.id, message.message_id)
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📄 تحميل القائمة الكاملة (+1800 موقع)", callback_data="download_all_sites"),
        types.InlineKeyboardButton("🔙 رجوع للبداية", callback_data="back_start")
    )
    bot.reply_to(message, get_supported_sites_text(), parse_mode="HTML", reply_markup=markup)


@bot.message_handler(commands=["admin", "stats"])
def cmd_admin(message):
    if not is_admin(message.from_user.id):
        return
    admin_states.pop(message.from_user.id, None)
    bot.reply_to(
        message,
        "🔐 <b>لوحة تحكم الأدمن</b>\nاختر القسم الذي تريد إدارته:",
        parse_mode="HTML",
        reply_markup=get_admin_panel_markup()
    )


@bot.callback_query_handler(func=lambda call: call.data == "check_sub")
def handle_check_subscription(call):
    uid = call.from_user.id
    if is_user_subscribed(uid):
        bot.answer_callback_query(call.id, "✅ تم التحقق من اشتراكك بنجاح!")
        bot.edit_message_text(
            get_welcome_text(),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=get_start_markup(uid)
        )
    else:
        bot.answer_callback_query(
            call.id,
            "❌ لم تشترك في القناة بعد! اشترك أولاً ثم اضغط تحقق.",
            show_alert=True
        )


@bot.callback_query_handler(func=lambda call: call.data in {
    "show_sites", "download_all_sites", "back_start", "admin_panel", "admin_stats",
    "admin_users", "admin_blocked", "admin_broadcast", "admin_backup", "cancel_broadcast"
})
def handle_menu_callbacks(call):
    uid = call.from_user.id
    data = call.data

    if data in {"show_sites", "download_all_sites"} and not is_user_subscribed(uid):
        bot.answer_callback_query(call.id, "⚠️ اشترك في القناة أولاً!", show_alert=True)
        return

    if data == "show_sites":
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("📄 تحميل القائمة الكاملة (+1800 موقع)", callback_data="download_all_sites"),
            types.InlineKeyboardButton("🔙 رجوع", callback_data="back_start")
        )
        bot.edit_message_text(
            get_supported_sites_text(),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=markup
        )
        bot.answer_callback_query(call.id)
        return

    if data == "download_all_sites":
        bot.answer_callback_query(call.id, "⏳ جاري تجهيز ملف المواقع...")
        file_path = generate_clean_sites_file()
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as f:
                bot.send_document(
                    call.message.chat.id,
                    f,
                    caption="📄 القائمة الكاملة لجميع المواقع والمنصات المدعومة للتحميل."
                )
        else:
            bot.send_message(call.message.chat.id, "❌ حدث خطأ أثناء تجهيز الملف، حاول مجدداً.")
        return

    if data == "back_start":
        admin_states.pop(uid, None)
        bot.edit_message_text(
            get_welcome_text(),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=get_start_markup(uid)
        )
        bot.answer_callback_query(call.id)
        return

    if not is_admin(uid):
        bot.answer_callback_query(call.id, "⛔ غير مصرح لك بالدخول هنا.", show_alert=True)
        return

    stats = load_stats()

    if data == "admin_panel":
        admin_states.pop(uid, None)
        bot.edit_message_text(
            "🔐 <b>لوحة تحكم الأدمن</b>\nاختر القسم الذي تريد إدارته من الأزرار التالية:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=get_admin_panel_markup()
        )

    elif data == "admin_stats":
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        users = stats.get("users", {})
        total_users = len(users)
        active_today = sum(1 for u in users.values() if u.get("last_active") == today)
        active_users = sum(1 for u in users.values() if u.get("downloads", 0) > 0)
        total_downloads = stats.get("total_downloads", 0)
        blocked_attempts = stats.get("blocked_attempts", 0)
        cookies_status = "✅ مفعّلة" if (os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 0) else "⚠️ غير مضافة"

        text = (
            f"📊 <b>الإحصائيات العامة للبوت:</b>\n\n"
            f"👥 إجمالي المستخدمين المسجلين: <b>{total_users}</b>\n"
            f"🔥 مستخدمون فعليون (قاموا بالتحميل): <b>{active_users}</b>\n"
            f"📅 نشطون اليوم: <b>{active_today}</b>\n"
            f"📥 إجمالي التحميلات الناجحة: <b>{total_downloads}</b>\n"
            f"🚫 محاولات تحميل محظورة (NSFW): <b>{blocked_attempts}</b>\n"
            f"🍪 حالة كوكيز يوتيوب: <b>{cookies_status}</b>\n"
            f"📢 قناة الاشتراك الإجباري: {FORCE_SUB_CHANNEL}"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_panel"))
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=markup
        )

    elif data == "admin_users":
        users = stats.get("users", {})
        recent_users = list(users.items())[-15:]
        lines = []
        for user_id, info in reversed(recent_users):
            safe_name = html.escape(info.get("name", "User"))
            uname = f"@{html.escape(info['username'])}" if info.get("username") else "بدون يوزر"
            dl_count = info.get("downloads", 0)
            lines.append(f"• {safe_name} ({uname}) | ID: <code>{user_id}</code> | 📥 {dl_count}")

        text = (
            f"👥 <b>آخر 15 مستخدم في البوت (من إجمالي {len(users)}):</b>\n\n"
            + ("\n".join(lines) if lines else "لا يوجد مستخدمون بعد.")
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_panel"))
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=markup
        )

    elif data == "admin_blocked":
        users = stats.get("users", {})
        violators = [(u_id, info) for u_id, info in users.items() if info.get("blocked", 0) > 0]
        lines = []
        for user_id, info in violators[-15:]:
            safe_name = html.escape(info.get("name", "User"))
            uname = f"@{html.escape(info['username'])}" if info.get("username") else "بدون يوزر"
            blk_count = info.get("blocked", 0)
            lines.append(f"• {safe_name} ({uname}) | ID: <code>{user_id}</code> | 🚫 {blk_count} محاولة")

        text = (
            f"🚫 <b>سجل محاولات المحتوى المحظور (إجمالي {stats.get('blocked_attempts', 0)}):</b>\n\n"
            + ("\n".join(lines) if lines else "✅ لا توجد أي محاولات مخالفة مسجلة.")
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data="admin_panel"))
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=markup
        )

    elif data == "admin_broadcast":
        admin_states[uid] = "waiting_broadcast"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("❌ إلغاء الإذاعة", callback_data="cancel_broadcast"))
        bot.edit_message_text(
            "📢 <b>إرسال رسالة جماعية (Broadcast):</b>\n\n"
            "أرسل الآن النص الذي تريد إرساله لجميع مستخدمي البوت:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=markup
        )

    elif data == "cancel_broadcast":
        admin_states.pop(uid, None)
        bot.edit_message_text(
            "❌ تم إلغاء الإذاعة.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=get_admin_panel_markup()
        )

    elif data == "admin_backup":
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, "rb") as doc:
                bot.send_document(
                    call.message.chat.id,
                    doc,
                    caption="📥 نسخة احتياطية من ملف بيانات وإحصائيات المستخدمين."
                )
            bot.answer_callback_query(call.id, "تم إرسال الملف.")
        else:
            bot.answer_callback_query(call.id, "ملف الإحصائيات غير موجود بعد.", show_alert=True)
        return

    bot.answer_callback_query(call.id)


@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and admin_states.get(m.from_user.id) == "waiting_broadcast")
def handle_admin_broadcast_text(message):
    admin_states.pop(message.from_user.id, None)
    stats = load_stats()
    users = list(stats.get("users", {}).keys())

    if not users:
        bot.reply_to(message, "لا يوجد مستخدمون لإرسال الرسالة إليهم.")
        return

    status = bot.reply_to(message, f"⏳ جاري إرسال الإذاعة إلى {len(users)} مستخدم...")
    sent, failed = 0, 0

    for target_id in users:
        try:
            bot.send_message(
                int(target_id),
                f"📢 <b>إشعار من إدارة البوت:</b>\n\n{html.escape(message.text)}",
                parse_mode="HTML"
            )
            sent += 1
            time.sleep(0.05)
        except Exception:
            failed += 1

    bot.edit_message_text(
        f"✅ <b>تمت الإذاعة بنجاح:</b>\n"
        f"• وصلت إلى: <b>{sent}</b> مستخدم\n"
        f"• فشل الإرسال (حظر البوت): <b>{failed}</b>",
        chat_id=message.chat.id,
        message_id=status.message_id,
        parse_mode="HTML",
        reply_markup=get_admin_panel_markup()
    )


# ==============================================================================
# 9. تحليل الروابط وعرض الجودات المتاحة
# ==============================================================================
@bot.message_handler(func=lambda m: m.text and m.text.strip().startswith(("http://", "https://")))
def handle_url(message):
    track_user(message.from_user)

    if not is_user_subscribed(message.from_user.id):
        send_force_sub_prompt(message.chat.id, message.message_id)
        return

    url = normalize_url(message.text)

    if is_nsfw_url(url):
        track_user(message.from_user, increment_blocked=True)
        bot.reply_to(message, "🚫 عذراً، هذا الرابط محظور لاحتوائه على محتوى إباحي أو غير لائق.")
        return

    wait_msg = bot.reply_to(message, "🔍 جاري فحص الرابط واستخراج الجودات المتاحة...")

    try:
        check_opts = get_base_ydl_opts()
        check_opts["ignore_no_formats_error"] = True
        with yt_dlp.YoutubeDL(check_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if is_nsfw_metadata(info):
            track_user(message.from_user, increment_blocked=True)
            bot.edit_message_text(
                "🚫 تم حظر التحميل: المحتوى مصنف للبالغين (+18) أو غير لائق.",
                chat_id=message.chat.id,
                message_id=wait_msg.message_id
            )
            return

        title = html.escape(info.get("title", "بدون عنوان"))
        uploader = html.escape(info.get("uploader") or "")
        duration_str = format_duration(info.get("duration"))
        qualities, audio_size_tag = extract_available_qualities(info)

        req_id = str(uuid.uuid4())[:8]
        formats_lookup = {str(q["height"]): q for q in qualities}

        with links_lock:
            user_requests[req_id] = {
                "url": url,
                "title": info.get("title", "Media"),
                "formats": formats_lookup,
                "ts": time.time()
            }

        markup = types.InlineKeyboardMarkup(row_width=2)
        if qualities:
            quality_buttons = [
                types.InlineKeyboardButton(q["label"], callback_data=f"dl|{req_id}|{q['height']}")
                for q in qualities
            ]
            markup.add(*quality_buttons)
        else:
            markup.add(
                types.InlineKeyboardButton("🎬 فيديو (أفضل جودة متاحة)", callback_data=f"dl|{req_id}|best")
            )

        markup.add(
            types.InlineKeyboardButton(f"🎵 صوت فقط (MP3 320kbps){audio_size_tag}", callback_data=f"dl|{req_id}|audio")
        )

        meta_lines = [f"🎬 <b>{title}</b>"]
        if uploader:
            meta_lines.append(f"👤 القناة/الناشر: {uploader}")
        if duration_str:
            meta_lines.append(f"⏱ المدة: {duration_str}")
        meta_lines.append("\nاختر الجودة أو الصيغة التي تريد تحميلها:")

        bot.edit_message_text(
            "\n".join(meta_lines),
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
            parse_mode="HTML",
            reply_markup=markup
        )

    except Exception as e:
        logger.error(f"Extraction error for {url}: {e}")
        safe_err = html.escape(str(e)[:250])
        bot.edit_message_text(
            f"❌ تعذر جلب بيانات الرابط:\n<code>{safe_err}</code>",
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
            parse_mode="HTML"
        )


# ==============================================================================
# 10. تنفيذ التحميل مع شريط التقدم الحي والمسار الاحتياطي للصيغ
# ==============================================================================
def make_progress_hook(chat_id: int, message_id: int, label: str):
    last_update = [0.0]

    def hook(d):
        if d.get("status") != "downloading":
            return
        now = time.time()
        if now - last_update[0] < 3.5:
            return
        last_update[0] = now

        downloaded = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        speed = d.get("speed") or 0

        if total > 0:
            pct = min(100.0, (downloaded / total) * 100)
            filled = int(pct // 10)
            bar = "█" * filled + "░" * (10 - filled)
            speed_str = f" | ⚡ {speed / (1024 * 1024):.1f} MB/s" if speed else ""
            text = (
                f"⏳ <b>جاري التحميل ({label})...</b>\n\n"
                f"<code>[{bar}] {pct:.1f}%</code>\n"
                f"📦 {downloaded / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MB{speed_str}"
            )
        else:
            text = (
                f"⏳ <b>جاري التحميل ({label})...</b>\n\n"
                f"📦 تم تحميل: {downloaded / (1024 * 1024):.1f} MB"
            )

        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode="HTML")
        except Exception:
            pass

    return hook


def build_ydl_opts(choice: str, fmt_info: dict, output_template: str, progress_hook, fallback=False) -> dict:
    ydl_opts = get_base_ydl_opts()
    ydl_opts["outtmpl"] = output_template
    ydl_opts["progress_hooks"] = [progress_hook]

    if choice == "audio":
        ydl_opts.update({
            "format": "bestaudio/best/best*" if not fallback else "best*/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }],
        })
    elif choice == "best" or fallback:
        ydl_opts.update({
            "format": "bv*+ba/b/best*/best",
            "merge_output_format": "mp4",
        })
    else:
        if fmt_info and fmt_info.get("format_id"):
            fid = fmt_info["format_id"]
            if fmt_info.get("has_audio"):
                fmt_str = f"{fid}/bv*[height={choice}]+ba/b[height={choice}]/best*/best"
            else:
                fmt_str = f"{fid}+bestaudio/{fid}+ba/bv*[height={choice}]+ba/b[height={choice}]/best*/best"
        else:
            fmt_str = f"bv*[height={choice}]+ba/b[height={choice}]/bv*[height<={choice}]+ba/best*/best"

        ydl_opts.update({
            "format": fmt_str,
            "merge_output_format": "mp4",
        })

    return ydl_opts


@bot.callback_query_handler(func=lambda call: call.data.startswith("dl|"))
def process_download(call):
    if not is_user_subscribed(call.from_user.id):
        bot.answer_callback_query(call.id, "⚠️ اشترك في القناة أولاً!", show_alert=True)
        return

    _, req_id, choice = call.data.split("|")

    with links_lock:
        req_data = user_requests.pop(req_id, None)

    if not req_data:
        bot.answer_callback_query(call.id, "انتهت صلاحية هذا الطلب، أرسل الرابط مجدداً.", show_alert=True)
        return

    url = req_data["url"]
    fmt_info = req_data.get("formats", {}).get(choice)

    bot.answer_callback_query(call.id)
    selected_label = "صوت MP3 320kbps" if choice == "audio" else ("أفضل جودة" if choice == "best" else f"دقة {choice}p")
    status_msg = bot.edit_message_text(
        f"⏳ <b>جاري بدء التحميل ({selected_label})...</b>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML"
    )

    file_id = str(uuid.uuid4())[:8]
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")
    hook = make_progress_hook(call.message.chat.id, status_msg.message_id, selected_label)
    downloaded_file = None

    try:
        try:
            with yt_dlp.YoutubeDL(build_ydl_opts(choice, fmt_info, output_template, hook, fallback=False)) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as first_err:
            if "Requested format is not available" in str(first_err):
                logger.warning(f"Format {choice} unavailable, switching to fallback for {url}")
                with yt_dlp.YoutubeDL(build_ydl_opts(choice, fmt_info, output_template, hook, fallback=True)) as ydl:
                    info = ydl.extract_info(url, download=True)
            else:
                raise first_err

        title = info.get("title") or req_data.get("title") or "Media"
        actual_height = info.get("height")
        res_text = f"{actual_height}p" if actual_height else (f"{choice}p" if choice.isdigit() else "")

        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                downloaded_file = os.path.join(DOWNLOAD_DIR, f)
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            raise FileNotFoundError("لم يتم العثور على الملف بعد اكتمال التحميل.")

        file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        if file_size_mb > MAX_FILE_SIZE_MB:
            bot.edit_message_text(
                f"❌ حجم الملف ({file_size_mb:.1f} MB) يتجاوز الحد المسموح ({MAX_FILE_SIZE_MB} MB). جرب اختيار دقة أقل.",
                chat_id=call.message.chat.id,
                message_id=status_msg.message_id
            )
            return

        quality_tag = f" [{res_text}]" if res_text and choice != "audio" else ""
        bot.edit_message_text(
            f"📤 <b>جاري الرفع إلى تيليجرام{quality_tag} ({file_size_mb:.1f} MB)...</b>",
            chat_id=call.message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="HTML"
        )

        with open(downloaded_file, "rb") as media:
            if choice != "audio":
                caption_text = f"🎬 <b>{html.escape(title)}</b>" + (f"\n✨ الجودة: <code>{res_text}</code>" if res_text else "")
                bot.send_video(
                    call.message.chat.id,
                    media,
                    caption=caption_text,
                    parse_mode="HTML",
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
        logger.error(f"Download failed for {url}: {e}")
        safe_err = html.escape(str(e)[:250])
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


# ==============================================================================
# 11. حلقة التشغيل المستمر 24/7 مع التعافي التلقائي من الأخطاء
# ==============================================================================
def run_bot_forever():
    init_bot_session()
    cleaner_thread = threading.Thread(target=background_cleaner, daemon=True)
    cleaner_thread.start()

    retry_delay = 3
    while not is_shutting_down:
        try:
            logger.info("Bot polling started (24/7 Auto-Recovery Active)...")
            bot.infinity_polling(
                skip_pending=True,
                timeout=60,
                long_polling_timeout=60
            )
            if is_shutting_down:
                break
        except Exception as e:
            if is_shutting_down:
                break
            logger.error(f"Polling crashed: {e}. Restarting in {retry_delay}s...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)
        else:
            retry_delay = 3

    logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    run_bot_forever()
