import os
import re
import json
import uuid
import html
import time
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
SITES_FILE = "supported_sites_clean.txt"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

user_links = {}
admin_states = {}

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

try:
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/logOut", timeout=10)
except Exception:
    pass


def get_base_ydl_opts():
    """إعدادات لتجاوز تحقق يوتيوب بدون كوكيز عبر عملاء TV / Android VR / Embedded"""
    return {
        "quiet": True,
        "noplaylist": True,
        "geo_bypass": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["tv", "android_vr", "web_embedded", "ios", "web"],
                "player_skip": ["webpage", "configs"]
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
    }


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
    except Exception:
        return None


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def is_nsfw_url(url):
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
    if not info:
        return False

    age_limit = info.get("age_limit") or 0
    if age_limit >= 18:
        return True

    extractor = (info.get("extractor") or info.get("extractor_key") or "").lower()
    for blocked in BLOCKED_DOMAINS:
        site_name = blocked.split(".")[0]
        if site_name in extractor:
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


def format_size(bytes_num):
    if not bytes_num or bytes_num <= 0:
        return ""
    mb = bytes_num / (1024 * 1024)
    if mb >= 1024:
        return f" (~{mb / 1024:.2f} GB)"
    return f" (~{mb:.1f} MB)"


def extract_available_qualities(info):
    formats = info.get("formats") or []
    best_audio_size = 0

    for f in formats:
        if f.get("vcodec") == "none" and f.get("acodec") != "none":
            a_size = f.get("filesize") or f.get("filesize_approx") or 0
            if a_size > best_audio_size:
                best_audio_size = a_size

    heights_map = {}
    for f in formats:
        vcodec = f.get("vcodec")
        height = f.get("height")
        if not vcodec or vcodec == "none" or not height:
            continue

        v_size = f.get("filesize") or f.get("filesize_approx") or 0
        has_audio = f.get("acodec") not in (None, "none")
        total_size = v_size if (has_audio or v_size == 0) else (v_size + best_audio_size)
        fps = f.get("fps") or 0

        if height not in heights_map:
            heights_map[height] = {"size": total_size, "fps": fps}
        else:
            if total_size > heights_map[height]["size"]:
                heights_map[height]["size"] = total_size
            if fps > heights_map[height]["fps"]:
                heights_map[height]["fps"] = fps

    sorted_heights = sorted(heights_map.keys(), reverse=True)
    qualities = []
    for h in sorted_heights:
        fps_tag = "60fps" if heights_map[h]["fps"] and heights_map[h]["fps"] >= 50 else ""
        size_tag = format_size(heights_map[h]["size"])
        label = f"🎬 {h}p {fps_tag}{size_tag}".replace("  ", " ").strip()
        qualities.append((h, label))

    return qualities, format_size(best_audio_size)


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


# --- القوائم والأزرار ---

def get_start_markup(user_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_sites = types.InlineKeyboardButton("🌐 المواقع المدعومة (القائمة الكاملة)", callback_data="show_sites")
    markup.add(btn_sites)

    if is_admin(user_id):
        btn_admin = types.InlineKeyboardButton("🔐 لوحة تحكم الأدمن", callback_data="admin_panel")
        markup.add(btn_admin)
    return markup


def get_admin_panel_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_stats = types.InlineKeyboardButton("📊 الإحصائيات العامة", callback_data="admin_stats")
    btn_users = types.InlineKeyboardButton("👥 قائمة المستخدمين", callback_data="admin_users")
    btn_blocked = types.InlineKeyboardButton("🚫 سجل المحظورين", callback_data="admin_blocked")
    btn_broadcast = types.InlineKeyboardButton("📢 إذاعة رسالة", callback_data="admin_broadcast")
    btn_backup = types.InlineKeyboardButton("📥 نسخة الإحصائيات", callback_data="admin_backup")
    btn_back = types.InlineKeyboardButton("🔙 رجوع للبداية", callback_data="back_start")

    markup.add(btn_stats, btn_users)
    markup.add(btn_blocked, btn_broadcast)
    markup.add(btn_backup)
    markup.add(btn_back)
    return markup


def get_welcome_text():
    return (
        "أهلاً بك في بوت التحميل الشامل! 👋\n\n"
        "📥 أرسل لي أي رابط فيديو أو صوت وسأعرض لك جميع الجودات المتاحة لتختار منها (حتى <b>4K</b> وبحجم يصل إلى <b>2GB</b>).\n"
        "⚠️ يُمنع استخدام البوت لتحميل أي محتوى إباحي أو مخالف.\n\n"
        "اضغط على الزر بالأسفل لمعرفة جميع المواقع التي يمكنك التحميل منها 👇"
    )


def get_supported_sites_text():
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
        "• SoundCloud • Bandcamp • Mixcloud • Audiomack • Apple Podcasts • Deezer (معاينات)\n\n"
        "📰 <b>مواقع الأخبار والرياضة والتعليم:</b>\n"
        "• Al Jazeera • BBC • CNN • Sky News • ESPN • FIFA • TED • Coursera (المجاني) • Udemy (المعاينات)\n\n"
        "🔗 <b>روابط أخرى مدعومة:</b>\n"
        "• روابط التخزين السحابي العامة (Google Drive, Dropbox, Archive.org, MediaFire)\n"
        "• أي صفحة ويب تحتوي على بث فيديو مباشر بصيغة (<code>.m3u8</code> أو <code>.mpd</code>)\n\n"
        "📄 <i>للحصول على القائمة الكاملة لجميع المواقع (أكثر من 1,800 موقع)، اضغط على زر تحميل القائمة بالأسفل.</i>"
    )


# --- معالجات الأوامر ---

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    admin_states.pop(message.from_user.id, None)
    track_user(message.from_user)
    bot.reply_to(
        message,
        get_welcome_text(),
        parse_mode="HTML",
        reply_markup=get_start_markup(message.from_user.id)
    )


@bot.message_handler(commands=["sites"])
def sites_command(message):
    track_user(message.from_user)
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📄 تحميل القائمة الكاملة (+1800 موقع)", callback_data="download_all_sites"),
        types.InlineKeyboardButton("🔙 رجوع للبداية", callback_data="back_start")
    )
    bot.reply_to(message, get_supported_sites_text(), parse_mode="HTML", reply_markup=markup)


@bot.message_handler(commands=["admin", "stats"])
def open_admin_command(message):
    if not is_admin(message.from_user.id):
        return
    admin_states.pop(message.from_user.id, None)
    bot.reply_to(
        message,
        "🔐 <b>لوحة تحكم الأدمن</b>\nاختر القسم الذي تريد إدارته:",
        parse_mode="HTML",
        reply_markup=get_admin_panel_markup()
    )


# --- معالجة أزرار القوائم ولوحة تحكم الأدمن ---

@bot.callback_query_handler(func=lambda call: call.data in [
    "show_sites", "download_all_sites", "back_start", "admin_panel", "admin_stats",
    "admin_users", "admin_blocked", "admin_broadcast", "admin_backup", "cancel_broadcast"
])
def handle_menu_callbacks(call):
    uid = call.from_user.id
    data = call.data

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

        text = (
            f"📊 <b>الإحصائيات العامة للبوت:</b>\n\n"
            f"👥 إجمالي المستخدمين المسجلين: <b>{total_users}</b>\n"
            f"🔥 مستخدمون فعليون (قاموا بالتحميل): <b>{active_users}</b>\n"
            f"📅 نشطون اليوم: <b>{active_today}</b>\n"
            f"📥 إجمالي التحميلات الناجحة: <b>{total_downloads}</b>\n"
            f"🚫 محاولات تحميل محظورة (NSFW): <b>{blocked_attempts}</b>"
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


# --- استقبال رسالة الإذاعة من الأدمن ---

@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and admin_states.get(m.from_user.id) == "waiting_broadcast")
def handle_admin_broadcast_text(message):
    admin_states.pop(message.from_user.id, None)
    stats = load_stats()
    users = list(stats.get("users", {}).keys())

    if not users:
        bot.reply_to(message, "لا يوجد مستخدمون لإرسال الرسالة إليهم.")
        return

    status = bot.reply_to(message, f"⏳ جاري إرسال الإذاعة إلى {len(users)} مستخدم...")
    sent = 0
    failed = 0

    for target_id in users:
        try:
            bot.send_message(int(target_id), f"📢 <b>إشعار من إدارة البوت:</b>\n\n{html.escape(message.text)}", parse_mode="HTML")
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


# --- استقبال الروابط وعرض الجودات المتاحة ---

@bot.message_handler(func=lambda m: m.text and m.text.startswith(("http://", "https://")))
def handle_url(message):
    url = message.text.strip()

    if is_nsfw_url(url):
        track_user(message.from_user, increment_blocked=True)
        bot.reply_to(message, "🚫 عذراً، هذا الرابط محظور لاحتوائه على محتوى إباحي أو غير لائق.")
        return

    track_user(message.from_user)
    wait_msg = bot.reply_to(message, "🔍 جاري فحص الرابط واستخراج الجودات المتاحة...")

    try:
        with yt_dlp.YoutubeDL(get_base_ydl_opts()) as ydl:
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
        qualities, audio_size_tag = extract_available_qualities(info)

        req_id = str(uuid.uuid4())[:8]
        user_links[req_id] = url

        markup = types.InlineKeyboardMarkup(row_width=2)

        if qualities:
            quality_buttons = [
                types.InlineKeyboardButton(label, callback_data=f"dl|{req_id}|{h}")
                for h, label in qualities
            ]
            markup.add(*quality_buttons)
        else:
            markup.add(
                types.InlineKeyboardButton("🎬 فيديو (أفضل جودة متاحة)", callback_data=f"dl|{req_id}|best")
            )

        markup.add(
            types.InlineKeyboardButton(f"🎵 صوت فقط (MP3 320kbps){audio_size_tag}", callback_data=f"dl|{req_id}|audio")
        )

        bot.edit_message_text(
            f"🎬 <b>{title}</b>\n\nاختر الجودة أو الصيغة التي تريد تحميلها:",
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
            parse_mode="HTML",
            reply_markup=markup
        )

    except Exception as e:
        safe_err = html.escape(str(e)[:250])
        bot.edit_message_text(
            f"❌ تعذر جلب بيانات الرابط:\n<code>{safe_err}</code>",
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
            parse_mode="HTML"
        )


# --- تنفيذ التحميل بالجودة المختارة ---

@bot.callback_query_handler(func=lambda call: call.data.startswith("dl|"))
def process_download(call):
    _, req_id, choice = call.data.split("|")
    url = user_links.pop(req_id, None)

    if not url:
        bot.answer_callback_query(call.id, "انتهت صلاحية هذا الطلب، أرسل الرابط مجدداً.", show_alert=True)
        return

    bot.answer_callback_query(call.id)
    selected_label = "صوت MP3 (320kbps)" if choice == "audio" else ("أفضل جودة" if choice == "best" else f"دقة {choice}p")
    status_msg = bot.edit_message_text(
        f"⏳ جاري التحميل ({selected_label}) بأعلى جودة صوت وصورة... يرجى الانتظار.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id
    )

    file_id = str(uuid.uuid4())[:8]
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")
    ydl_opts = get_base_ydl_opts()

    if choice == "audio":
        ydl_opts.update({
            "format": "bestaudio/best",
            "format_sort": ["abr", "asr"],
            "outtmpl": output_template,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }],
        })
    elif choice == "best":
        ydl_opts.update({
            "format": "bv*+ba/b",
            "format_sort": ["res", "fps", "vbr", "abr"],
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "postprocessor_args": {
                "merger": ["-c:v", "copy", "-c:a", "aac", "-b:a", "320k"]
            },
        })
    else:
        ydl_opts.update({
            "format": f"bv*[height={choice}]+ba/b[height={choice}]/bv*[height<={choice}]+ba/b[height<={choice}]",
            "format_sort": ["fps", "vbr", "abr"],
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "postprocessor_args": {
                "merger": ["-c:v", "copy", "-c:a", "aac", "-b:a", "320k"]
            },
        })

    downloaded_file = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Media")
            actual_height = info.get("height")
            res_text = f"{actual_height}p" if actual_height else (f"{choice}p" if choice.isdigit() else "")

        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                downloaded_file = os.path.join(DOWNLOAD_DIR, f)
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            raise FileNotFoundError("لم يتم العثور على الملف بعد التحميل.")

        file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        if file_size_mb > 1950:
            bot.edit_message_text(
                f"❌ حجم الملف ({file_size_mb:.1f} MB) يتجاوز الحد الأقصى لتيليجرام (2 GB). جرب اختيار دقة أقل.",
                chat_id=call.message.chat.id,
                message_id=status_msg.message_id
            )
            return

        quality_tag = f" [{res_text}]" if res_text and choice != "audio" else ""
        bot.edit_message_text(
            f"📤 جاري الرفع إلى تيليجرام{quality_tag} ({file_size_mb:.1f} MB)...",
            chat_id=call.message.chat.id,
            message_id=status_msg.message_id
        )

        with open(downloaded_file, "rb") as media:
            if choice != "audio":
                caption_text = f"🎬 {title}" + (f"\n✨ الجودة: {res_text}" if res_text else "")
                bot.send_video(
                    call.message.chat.id,
                    media,
                    caption=caption_text,
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


if __name__ == "__main__":
    print("Bot is running without cookies (Multi-client bypass enabled)...")
    bot.infinity_polling(skip_pending=True)
