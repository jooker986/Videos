
import os
import re
import json
import uuid
import html
import time
import logging
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timezone

import telebot
from telebot import apihelper, types
import yt_dlp


# =====================================================
# CONFIGURATION
# =====================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

try:
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
except (ValueError, TypeError):
    ADMIN_ID = 0

LOCAL_API = os.environ.get("LOCAL_API", "http://localhost:8081")

# Telegram Local Bot API
apihelper.API_URL = LOCAL_API + "/bot{0}/{1}"
apihelper.FILE_URL = LOCAL_API + "/file/bot{0}/{1}"

DOWNLOAD_DIR = Path("downloads")
STATS_FILE = Path("users_stats.json")
SITES_FILE = Path("supported_sites_clean.txt")
COOKIES_FILE = Path("cookies.txt")

DOWNLOAD_DIR.mkdir(exist_ok=True)

if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    raise RuntimeError("يرجى ضبط BOT_TOKEN أولاً.")

if ADMIN_ID == 0:
    logging.warning("ADMIN_ID غير مضبوط. لوحة الأدمن غير متاحة.")

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("MediaBot")

user_links = {}
admin_states = {}


# =====================================================
# CONTENT FILTER
# =====================================================

BLOCKED_DOMAINS = {
    "pornhub.com", "xvideos.com", "xnxx.com",
    "xhamster.com", "redtube.com", "youporn.com",
    "tube8.com", "spankbang.com", "eporner.com",
    "hqporner.com", "beeg.com", "brazzers.com",
    "bangbros.com", "realitykings.com",
    "naughtyamerica.com", "chaturbate.com",
    "stripchat.com", "cam4.com", "myfreecams.com",
    "livejasmin.com", "onlyfans.com", "fansly.com",
    "manyvids.com", "clips4sale.com", "rule34.xxx",
    "hanime.tv", "hentaihaven.xxx", "nhentai.net",
    "e-hentai.org", "motherless.com", "tnaflix.com",
    "drtuber.com", "empflix.com", "slutload.com",
    "xtube.com", "fapello.com", "erome.com",
    "bunkr.si", "coomer.su", "kemono.su", "javhd.com"
}

BLOCKED_KEYWORDS = [
    "porn", "xxx", "xnxx", "xvideos", "xhamster",
    "hentai", "nsfw", "nude", "nudes", "erotic",
    "onlyfans", "chaturbate", "camgirl", "milf",
    "blowjob", "handjob", "creampie", "gangbang",
    "incest", "jav", "rule34",
    "سكس", "إباحي", "اباحي", "نيك", "بورن",
    "شرموط", "قحبة", "عاري", "عارية", "للكبار فقط"
]


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def is_nsfw_url(url):
    try:
        parsed = urlparse(url)
        domain = (parsed.hostname or "").lower()

        if domain.startswith("www."):
            domain = domain[4:]

        for blocked in BLOCKED_DOMAINS:
            if domain == blocked or domain.endswith("." + blocked):
                return True

        full_url = url.lower()

        for keyword in BLOCKED_KEYWORDS:
            if re.search(
                rf"(?<!\w){re.escape(keyword)}(?!\w)",
                full_url
            ):
                return True

    except Exception:
        logger.exception("URL filter error")

    return False


def is_nsfw_metadata(info):
    if not info:
        return False

    try:
        if int(info.get("age_limit") or 0) >= 18:
            return True
    except (ValueError, TypeError):
        pass

    extractor = (
        info.get("extractor")
        or info.get("extractor_key")
        or ""
    ).lower()

    for domain in BLOCKED_DOMAINS:
        if domain.split(".")[0] in extractor:
            return True

    title = info.get("title") or ""
    description = info.get("description") or ""
    tags = " ".join(info.get("tags") or [])
    categories = " ".join(info.get("categories") or [])

    combined = (
        f"{title} {description[:500]} {tags} {categories}"
    ).lower()

    for keyword in BLOCKED_KEYWORDS:
        if re.search(
            rf"(?<!\w){re.escape(keyword)}(?!\w)",
            combined
        ):
            return True

    return False


# =====================================================
# URL NORMALIZATION
# =====================================================

def extract_youtube_id(url):
    patterns = [
        r"(?:youtu\.be|yewtu\.be)/([A-Za-z0-9_-]{11})",
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"/(?:v|embed|shorts|live)/([A-Za-z0-9_-]{11})",
        r"%3Fv%3D([A-Za-z0-9_-]{11})"
    ]

    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def normalize_url(url):
    url = url.strip()

    video_id = extract_youtube_id(url)

    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"

    return url


def validate_url(url):
    try:
        parsed = urlparse(url)

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
        )

    except Exception:
        return False


# =====================================================
# YT-DLP CONFIGURATION
# =====================================================

def get_base_ydl_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }

    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        opts["cookiefile"] = str(COOKIES_FILE)

    else:
        opts["extractor_args"] = {
            "youtube": {
                "player_client": [
                    "tv",
                    "android_vr",
                    "web_embedded"
                ]
            }
        }

    # PO Token provider, when its local service is installed
    if os.environ.get("ENABLE_PO_TOKEN", "0") == "1":
        opts["extractor_args"] = opts.get("extractor_args", {})
        opts["extractor_args"]["youtubepot-bgutilhttp"] = {
            "base_url": [
                os.environ.get(
                    "PO_TOKEN_URL",
                    "http://127.0.0.1:4416"
                )
            ]
        }

    return opts


# =====================================================
# SUPPORTED SITES
# =====================================================

def generate_clean_sites_file():
    if SITES_FILE.exists():
        return str(SITES_FILE)

    try:
        extractors = yt_dlp.extractor.list_extractors(
            age_limit=17
        )

        clean_names = set()

        for extractor in extractors:
            name = getattr(extractor, "IE_NAME", "")
            name = name.split(":")[0].strip()

            if not name or name.lower() == "generic":
                continue

            lower_name = name.lower()

            if any(
                domain.split(".")[0] in lower_name
                for domain in BLOCKED_DOMAINS
            ):
                continue

            if any(
                keyword in lower_name
                for keyword in BLOCKED_KEYWORDS
            ):
                continue

            clean_names.add(name)

        sites = sorted(clean_names, key=str.lower)

        with open(SITES_FILE, "w", encoding="utf-8") as file:
            file.write(
                f"قائمة المواقع المدعومة ({len(sites)} موقع)\n"
            )
            file.write("=" * 50 + "\n\n")

            for index, site in enumerate(sites, 1):
                file.write(f"{index}. {site}\n")

        return str(SITES_FILE)

    except Exception:
        logger.exception("Could not generate sites file")
        return None


# =====================================================
# STATISTICS
# =====================================================

def load_stats():
    default = {
        "users": {},
        "total_downloads": 0,
        "blocked_attempts": 0
    }

    if not STATS_FILE.exists():
        return default

    try:
        with open(STATS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        data.setdefault("users", {})
        data.setdefault("total_downloads", 0)
        data.setdefault("blocked_attempts", 0)

        return data

    except (OSError, ValueError):
        logger.exception("Statistics file error")
        return default


def save_stats(data):
    temp_file = STATS_FILE.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_file, STATS_FILE)


def track_user(
    user,
    increment_download=False,
    increment_blocked=False
):
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

    info = stats["users"][uid]

    info["last_active"] = today
    info["name"] = user.first_name or info.get("name", "User")
    info["username"] = user.username or info.get("username", "")

    if increment_download:
        info["downloads"] = info.get("downloads", 0) + 1
        stats["total_downloads"] += 1

    if increment_blocked:
        info["blocked"] = info.get("blocked", 0) + 1
        stats["blocked_attempts"] += 1

    save_stats(stats)

    return stats


# =====================================================
# QUALITY HELPERS
# =====================================================

def format_size(size):
    if not size or size <= 0:
        return ""

    mb = size / (1024 * 1024)

    if mb >= 1024:
        return f" (~{mb / 1024:.2f} GB)"

    return f" (~{mb:.1f} MB)"


def extract_available_qualities(info):
    formats = info.get("formats") or []

    best_audio_size = 0
    heights = {}

    for fmt in formats:
        if fmt.get("vcodec") == "none":
            if fmt.get("acodec") not in (None, "none"):
                size = (
                    fmt.get("filesize")
                    or fmt.get("filesize_approx")
                    or 0
                )
                best_audio_size = max(best_audio_size, size)

    for fmt in formats:
        height = fmt.get("height")
        vcodec = fmt.get("vcodec")

        if not height or not vcodec or vcodec == "none":
            continue

        video_size = (
            fmt.get("filesize")
            or fmt.get("filesize_approx")
            or 0
        )

        has_audio = fmt.get("acodec") not in (None, "none")

        total_size = (
            video_size
            if has_audio or video_size == 0
            else video_size + best_audio_size
        )

        fps = fmt.get("fps") or 0

        if height not in heights:
            heights[height] = {
                "size": total_size,
                "fps": fps
            }
        else:
            heights[height]["size"] = max(
                heights[height]["size"],
                total_size
            )

            heights[height]["fps"] = max(
                heights[height]["fps"],
                fps
            )

    qualities = []

    for height in sorted(heights, reverse=True):
        fps = heights[height]["fps"]
        size = heights[height]["size"]

        fps_text = "60fps" if fps >= 50 else ""

        label = (
            f"🎬 {height}p {fps_text}"
            f"{format_size(size)}"
        ).strip()

        qualities.append((height, label))

    return qualities, format_size(best_audio_size)


# =====================================================
# KEYBOARDS
# =====================================================

def get_start_markup(user_id):
    markup = types.InlineKeyboardMarkup(row_width=1)

    markup.add(
        types.InlineKeyboardButton(
            "🌐 المواقع المدعومة",
            callback_data="show_sites"
        )
    )

    if is_admin(user_id):
        markup.add(
            types.InlineKeyboardButton(
                "🔐 لوحة تحكم الأدمن",
                callback_data="admin_panel"
            )
        )

    return markup


def get_admin_panel_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)

    markup.add(
        types.InlineKeyboardButton(
            "📊 الإحصائيات",
            callback_data="admin_stats"
        ),
        types.InlineKeyboardButton(
            "👥 المستخدمون",
            callback_data="admin_users"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🚫 المحظورون",
            callback_data="admin_blocked"
        ),
        types.InlineKeyboardButton(
            "📢 إذاعة",
            callback_data="admin_broadcast"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "📥 نسخة احتياطية",
            callback_data="admin_backup"
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "🔙 رجوع",
            callback_data="back_start"
        )
    )

    return markup


# =====================================================
# TEXTS
# =====================================================

def get_welcome_text():
    return (
        "أهلاً بك في بوت التحميل الشامل! 👋\n\n"
        "📥 أرسل رابط فيديو أو صوت لاستخراج الجودات المتاحة.\n\n"
        "🎬 اختر الجودة المناسبة قبل التحميل.\n"
        "🎵 يمكنك تحميل الصوت بصيغة MP3.\n\n"
        "⚠️ يُمنع تحميل المحتوى الإباحي أو المخالف.\n\n"
        "اضغط على الزر لمعرفة المواقع المدعومة 👇"
    )


def get_supported_sites_text():
    return (
        "🌐 <b>المواقع والمنصات المدعومة:</b>\n\n"
        "🎬 <b>الفيديو والبث:</b>\n"
        "YouTube • Vimeo • Dailymotion • Twitch\n"
        "Rumble • Bilibili • Kick • Trovo\n\n"
        "📱 <b>التواصل الاجتماعي:</b>\n"
        "TikTok • Instagram • Facebook\n"
        "X • Reddit • Pinterest • Snapchat\n"
        "LinkedIn • Threads • Tumblr\n\n"
        "🎵 <b>الصوت:</b>\n"
        "SoundCloud • Bandcamp • Mixcloud\n"
        "Audiomack • Apple Podcasts\n\n"
        "📄 القائمة الكاملة متاحة من الزر بالأسفل."
    )


# =====================================================
# START / HELP / SITES
# =====================================================

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    admin_states.pop(message.from_user.id, None)

    track_user(message.from_user)

    bot.reply_to(
        message,
        get_welcome_text(),
        reply_markup=get_start_markup(message.from_user.id)
    )


@bot.message_handler(commands=["sites"])
def sites_command(message):
    track_user(message.from_user)

    markup = types.InlineKeyboardMarkup(row_width=1)

    markup.add(
        types.InlineKeyboardButton(
            "📄 تحميل القائمة الكاملة",
            callback_data="download_all_sites"
        ),
        types.InlineKeyboardButton(
            "🔙 رجوع",
            callback_data="back_start"
        )
    )

    bot.reply_to(
        message,
        get_supported_sites_text(),
        parse_mode="HTML",
        reply_markup=markup
    )


@bot.message_handler(commands=["admin", "stats"])
def open_admin_command(message):
    if not is_admin(message.from_user.id):
        return

    admin_states.pop(message.from_user.id, None)

    bot.reply_to(
        message,
        "🔐 <b>لوحة تحكم الأدمن</b>\nاختر القسم:",
        parse_mode="HTML",
        reply_markup=get_admin_panel_markup()
    )


# =====================================================
# ADMIN CALLBACKS
# =====================================================

@bot.callback_query_handler(
    func=lambda call: call.data in {
        "show_sites",
        "download_all_sites",
        "back_start",
        "admin_panel",
        "admin_stats",
        "admin_users",
        "admin_blocked",
        "admin_broadcast",
        "admin_backup",
        "cancel_broadcast"
    }
)
def handle_menu_callbacks(call):
    uid = call.from_user.id
    data = call.data

    try:
        if data == "show_sites":
            markup = types.InlineKeyboardMarkup()

            markup.add(
                types.InlineKeyboardButton(
                    "📄 تحميل القائمة الكاملة",
                    callback_data="download_all_sites"
                )
            )

            markup.add(
                types.InlineKeyboardButton(
                    "🔙 رجوع",
                    callback_data="back_start"
                )
            )

            bot.edit_message_text(
                get_supported_sites_text(),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=markup
            )

        elif data == "download_all_sites":
            bot.answer_callback_query(
                call.id,
                "⏳ جاري تجهيز الملف..."
            )

            path = generate_clean_sites_file()

            if path and os.path.exists(path):
                with open(path, "rb") as file:
                    bot.send_document(
                        call.message.chat.id,
                        file,
                        caption="📄 قائمة المواقع المدعومة"
                    )
            else:
                bot.send_message(
                    call.message.chat.id,
                    "❌ تعذر تجهيز الملف."
                )

            return

        elif data == "back_start":
            admin_states.pop(uid, None)

            bot.edit_message_text(
                get_welcome_text(),
                call.message.chat.id,
                call.message.message_id,
                reply_markup=get_start_markup(uid)
            )

        else:
            if not is_admin(uid):
                bot.answer_callback_query(
                    call.id,
                    "⛔ غير مصرح لك.",
                    show_alert=True
                )
                return

            stats = load_stats()
            users = stats["users"]

            if data == "admin_panel":
                admin_states.pop(uid, None)

                bot.edit_message_text(
                    "🔐 <b>لوحة تحكم الأدمن</b>",
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=get_admin_panel_markup()
                )

            elif data == "admin_stats":
                today = datetime.now(
                    timezone.utc
                ).strftime("%Y-%m-%d")

                total = len(users)

                active_today = sum(
                    1 for user in users.values()
                    if user.get("last_active") == today
                )

                active = sum(
                    1 for user in users.values()
                    if user.get("downloads", 0) > 0
                )

                cookies = (
                    "✅ مفعّلة"
                    if COOKIES_FILE.exists()
                    and COOKIES_FILE.stat().st_size > 0
                    else "⚠️ غير مضافة"
                )

                text = (
                    "📊 <b>إحصائيات البوت</b>\n\n"
                    f"👥 المستخدمون: <b>{total}</b>\n"
                    f"🔥 مستخدمون حمّلوا: <b>{active}</b>\n"
                    f"📅 نشطون اليوم: <b>{active_today}</b>\n"
                    f"📥 التحميلات: <b>{stats['total_downloads']}</b>\n"
                    f"🚫 المحاولات المحظورة: "
                    f"<b>{stats['blocked_attempts']}</b>\n"
                    f"🍪 Cookies: <b>{cookies}</b>"
                )

                bot.edit_message_text(
                    text,
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=back_admin_markup()
                )

            elif data == "admin_users":
                lines = []

                for user_id, info in list(users.items())[-15:][::-1]:
                    name = html.escape(info.get("name", "User"))
                    username = info.get("username", "")

                    uname = (
                        "@" + html.escape(username)
                        if username else "بدون يوزر"
                    )

                    lines.append(
                        f"• {name} ({uname}) | "
                        f"<code>{user_id}</code> | "
                        f"📥 {info.get('downloads', 0)}"
                    )

                text = (
                    f"👥 <b>المستخدمون: {len(users)}</b>\n\n"
                    + ("\n".join(lines) if lines else "لا يوجد مستخدمون.")
                )

                bot.edit_message_text(
                    text,
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=back_admin_markup()
                )

            elif data == "admin_blocked":
                violators = [
                    (uid2, info)
                    for uid2, info in users.items()
                    if info.get("blocked", 0) > 0
                ]

                lines = []

                for user_id, info in violators[-15:][::-1]:
                    name = html.escape(info.get("name", "User"))

                    lines.append(
                        f"• {name} | <code>{user_id}</code> | "
                        f"🚫 {info.get('blocked', 0)}"
                    )

                text = (
                    "🚫 <b>سجل المحاولات المحظورة</b>\n\n"
                    + (
                        "\n".join(lines)
                        if lines else "لا توجد محاولات محظورة."
                    )
                )

                bot.edit_message_text(
                    text,
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=back_admin_markup()
                )

            elif data == "admin_broadcast":
                admin_states[uid] = "waiting_broadcast"

                markup = types.InlineKeyboardMarkup()

                markup.add(
                    types.InlineKeyboardButton(
                        "❌ إلغاء",
                        callback_data="cancel_broadcast"
                    )
                )

                bot.edit_message_text(
                    "📢 <b>إذاعة رسالة</b>\n\n"
                    "أرسل الرسالة التي تريد إرسالها للمستخدمين.",
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=markup
                )

            elif data == "cancel_broadcast":
                admin_states.pop(uid, None)

                bot.edit_message_text(
                    "❌ تم إلغاء الإذاعة.",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=get_admin_panel_markup()
                )

            elif data == "admin_backup":
                if STATS_FILE.exists():
                    with open(STATS_FILE, "rb") as file:
                        bot.send_document(
                            call.message.chat.id,
                            file,
                            caption="📥 نسخة احتياطية للإحصائيات"
                        )
                else:
                    bot.answer_callback_query(
                        call.id,
                        "ملف الإحصائيات غير موجود.",
                        show_alert=True
                    )
                    return

        bot.answer_callback_query(call.id)

    except Exception:
        logger.exception("Menu callback error")

        try:
            bot.answer_callback_query(
                call.id,
                "حدث خطأ أثناء تنفيذ العملية."
            )
        except Exception:
            pass


def back_admin_markup():
    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "🔙 لوحة التحكم",
            callback_data="admin_panel"
        )
    )

    return markup


# =====================================================
# BROADCAST
# =====================================================

@bot.message_handler(
    func=lambda m: (
        m.from_user is not None
        and is_admin(m.from_user.id)
        and admin_states.get(m.from_user.id) == "waiting_broadcast"
    ),
    content_types=["text"]
)
def handle_admin_broadcast_text(message):
    admin_states.pop(message.from_user.id, None)

    stats = load_stats()
    users = list(stats["users"].keys())

    if not users:
        bot.reply_to(message, "لا يوجد مستخدمون.")
        return

    status = bot.reply_to(
        message,
        f"⏳ جاري إرسال الرسالة إلى {len(users)} مستخدم..."
    )

    sent = 0
    failed = 0

    for target_id in users:
        try:
            bot.send_message(
                int(target_id),
                "📢 <b>إشعار من إدارة البوت:</b>\n\n"
                + html.escape(message.text),
                parse_mode="HTML"
            )

            sent += 1
            time.sleep(0.05)

        except Exception:
            failed += 1

    bot.edit_message_text(
        "✅ <b>انتهت الإذاعة</b>\n\n"
        f"وصلت: <b>{sent}</b>\n"
        f"فشل: <b>{failed}</b>",
        message.chat.id,
        status.message_id,
        parse_mode="HTML",
        reply_markup=get_admin_panel_markup()
    )


# =====================================================
# URL HANDLER
# =====================================================

@bot.message_handler(
    func=lambda m: (
        m.text is not None
        and m.text.strip().lower().startswith(("http://", "https://"))
    ),
    content_types=["text"]
)
def handle_url(message):
    raw_url = message.text.strip()
    url = normalize_url(raw_url)

    if not validate_url(url):
        bot.reply_to(message, "❌ الرابط غير صالح.")
        return

    if is_nsfw_url(url):
        track_user(
            message.from_user,
            increment_blocked=True
        )

        bot.reply_to(
            message,
            "🚫 هذا الرابط محظور."
        )
        return

    track_user(message.from_user)

    wait_msg = bot.reply_to(
        message,
        "🔍 جاري فحص الرابط واستخراج الجودات..."
    )

    try:
        with yt_dlp.YoutubeDL(get_base_ydl_opts()) as ydl:
            info = ydl.extract_info(
                url,
                download=False
            )

        if not info:
            raise ValueError("تعذر استخراج بيانات الرابط.")

        if is_nsfw_metadata(info):
            track_user(
                message.from_user,
                increment_blocked=True
            )

            bot.edit_message_text(
                "🚫 تم حظر المحتوى المصنف للبالغين.",
                message.chat.id,
                wait_msg.message_id
            )
            return

        title = html.escape(
            info.get("title") or "بدون عنوان"
        )

        qualities, audio_size = extract_available_qualities(info)

        req_id = uuid.uuid4().hex[:12]

        user_links[req_id] = {
            "url": url,
            "user_id": message.from_user.id,
            "created": time.time()
        }

        # Clean expired requests
        now = time.time()

        for key in list(user_links.keys()):
            if now - user_links[key]["created"] > 1800:
                user_links.pop(key, None)

        markup = types.InlineKeyboardMarkup(row_width=2)

        if qualities:
            for height, label in qualities:
                markup.add(
                    types.InlineKeyboardButton(
                        label[:60],
                        callback_data=f"dl|{req_id}|{height}"
                    )
                )
        else:
            markup.add(
                types.InlineKeyboardButton(
                    "🎬 أفضل جودة متاحة",
                    callback_data=f"dl|{req_id}|best"
                )
            )

        markup.add(
            types.InlineKeyboardButton(
                f"🎵 صوت MP3{audio_size}",
                callback_data=f"dl|{req_id}|audio"
            )
        )

        bot.edit_message_text(
            f"🎬 <b>{title}</b>\n\n"
            "اختر الجودة التي تريد تحميلها:",
            message.chat.id,
            wait_msg.message_id,
            parse_mode="HTML",
            reply_markup=markup
        )

    except Exception as error:
        logger.exception("Extraction error")

        safe_error = html.escape(str(error)[:250])

        bot.edit_message_text(
            "❌ تعذر استخراج بيانات الرابط:\n"
            f"<code>{safe_error}</code>",
            message.chat.id,
            wait_msg.message_id,
            parse_mode="HTML"
        )


# =====================================================
# DOWNLOAD CALLBACK
# =====================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("dl|")
)
def process_download(call):
    downloaded_file = None
    status_msg = None

    try:
        parts = call.data.split("|")

        if len(parts) != 3:
            bot.answer_callback_query(
                call.id,
                "طلب غير صالح.",
                show_alert=True
            )
            return

        _, req_id, choice = parts

        request = user_links.get(req_id)

        if not request:
            bot.answer_callback_query(
                call.id,
                "انتهت صلاحية الطلب. أرسل الرابط مجدداً.",
                show_alert=True
            )
            return

        if request["user_id"] != call.from_user.id:
            bot.answer_callback_query(
                call.id,
                "هذا الطلب ليس خاصاً بك.",
                show_alert=True
            )
            return

        url = request["url"]

        user_links.pop(req_id, None)

        bot.answer_callback_query(call.id)

        selected_label = (
            "MP3"
            if choice == "audio"
            else (
                "أفضل جودة"
                if choice == "best"
                else f"{choice}p"
            )
        )

        status_msg = bot.edit_message_text(
            f"⏳ جاري تحميل {selected_label}...\n"
            "يرجى الانتظار.",
            call.message.chat.id,
            call.message.message_id
        )

        file_id = uuid.uuid4().hex[:12]

        output_template = str(
            DOWNLOAD_DIR / f"{file_id}.%(ext)s"
        )

        ydl_opts = get_base_ydl_opts()

        ydl_opts.update({
            "outtmpl": output_template,
            "overwrites": True
        })

        if choice == "audio":
            ydl_opts.update({
                "format": "bestaudio/best",
                "format_sort": ["abr", "asr"],
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320"
                }]
            })

        elif choice == "best":
            ydl_opts.update({
                "format": "bestvideo+bestaudio/best",
                "format_sort": [
                    "res",
                    "fps",
                    "vcodec:av01",
                    "vcodec:vp9",
                    "vbr",
                    "abr"
                ],
                "merge_output_format": "mp4"
            })

        else:
            try:
                height = int(choice)

                if height < 1 or height > 10000:
                    raise ValueError()

            except ValueError:
                raise ValueError("دقة الفيديو غير صالحة.")

            ydl_opts.update({
                "format": (
                    f"bestvideo[height={height}]+bestaudio/"
                    f"best[height={height}]/"
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}]"
                ),
                "format_sort": [
                    "fps",
                    "vcodec:av01",
                    "vcodec:vp9",
                    "vbr",
                    "abr"
                ],
                "merge_output_format": "mp4"
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                url,
                download=True
            )

            title = info.get("title") or "Media"
            actual_height = info.get("height")

        # Find only the file created for this request
        candidates = [
            DOWNLOAD_DIR / filename
            for filename in os.listdir(DOWNLOAD_DIR)
            if filename.startswith(file_id + ".")
        ]

        candidates = [
            path for path in candidates
            if path.is_file()
        ]

        if not candidates:
            raise FileNotFoundError(
                "لم يتم العثور على الملف بعد التحميل."
            )

        downloaded_file = max(
            candidates,
            key=lambda path: path.stat().st_size
        )

        file_size = downloaded_file.stat().st_size
        file_size_mb = file_size / (1024 * 1024)

        if file_size_mb > 1950:
            raise ValueError(
                f"حجم الملف {file_size_mb:.1f} MB "
                "يتجاوز الحد المحدد في البوت."
            )

        res_text = (
            f"{actual_height}p"
            if actual_height
            else (
                f"{choice}p"
                if choice.isdigit()
                else ""
            )
        )

        bot.edit_message_text(
            f"📤 جاري رفع الملف إلى تيليجرام "
            f"({file_size_mb:.1f} MB)...",
            call.message.chat.id,
            status_msg.message_id
        )

        with open(downloaded_file, "rb") as media:

            if choice == "audio":
                bot.send_audio(
                    call.message.chat.id,
                    media,
                    title=title,
                    timeout=600
                )

            else:
                caption = f"🎬 {title}"

                if res_text:
                    caption += f"\n✨ الجودة: {res_text}"

                bot.send_video(
                    call.message.chat.id,
                    media,
                    caption=caption[:1024],
                    supports_streaming=True,
                    timeout=600
                )

        track_user(
            call.from_user,
            increment_download=True
        )

        bot.delete_message(
            call.message.chat.id,
            status_msg.message_id
        )

    except Exception as error:
        logger.exception("Download error")

        safe_error = html.escape(str(error)[:250])

        if status_msg:
            try:
                bot.edit_message_text(
                    "❌ حدث خطأ أثناء التحميل:\n"
                    f"<code>{safe_error}</code>",
                    call.message.chat.id,
                    status_msg.message_id,
                    parse_mode="HTML"
                )
            except Exception:
                bot.send_message(
                    call.message.chat.id,
                    f"❌ خطأ: {safe_error}",
                    parse_mode="HTML"
                )

    finally:
        if downloaded_file and downloaded_file.exists():
            try:
                downloaded_file.unlink()
            except OSError:
                logger.exception("Could not delete downloaded file")


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":
    print("=" * 45)
    print("       MEDIA DOWNLOADER BOT")
    print("=" * 45)
    print("Telegram Local API:", LOCAL_API)
    print("Admin ID:", ADMIN_ID)
    print("Downloads directory:", DOWNLOAD_DIR.resolve())
    print("Bot is starting...")

    try:
        bot.infinity_polling(
            skip_pending=True,
            timeout=30,
            long_polling_timeout=30
        )

    except KeyboardInterrupt:
        print("Bot stopped.")

    except Exception:
        logger.exception("Fatal bot error")
