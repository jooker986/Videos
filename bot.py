import os
import uuid
import telebot
from telebot import types
import yt_dlp

# قراءة التوكن من متغيرات البيئة (أو ضعه هنا محلياً فقط ولا ترفعه لـ GitHub)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
bot = telebot.TeleBot(BOT_TOKEN)

# مجلد مؤقت لحفظ التحميلات
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# تخزين مؤقت للروابط لكل مستخدم
user_links = {}

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.reply_to(
        message,
        "أهلاً بك! 👋\n"
        "أرسل لي أي رابط فيديو (YouTube, TikTok, Instagram, Facebook...) وسأقوم بتحميله لك.\n\n"
        "⚠️ ملاحظة: الحد الأقصى لحجم الملف في تيليجرام للبوتات العادية هو 50MB."
    )

@bot.message_handler(func=lambda m: m.text and m.text.startswith(("http://", "https://")))
def handle_url(message):
    url = message.text.strip()
    req_id = str(uuid.uuid4())[:8]
    user_links[req_id] = url

    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_video = types.InlineKeyboardButton("🎬 فيديو (MP4)", callback_data=f"vid|{req_id}")
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
        "⏳ جاري التحميل والمعالجة... يرجى الانتظار.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id
    )

    file_id = str(uuid.uuid4())[:8]
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    if mode == "vid":
        ydl_opts = {
            "format": "bv*[filesize<48M][ext=mp4]+ba[ext=m4a]/b[filesize<48M][ext=mp4]/bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
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

        # البحث عن الملف المحمّل
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                downloaded_file = os.path.join(DOWNLOAD_DIR, f)
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            raise FileNotFoundError("لم يتم العثور على الملف بعد التحميل.")

        # التحقق من حجم الملف (حد تيليجرام 50 ميجابايت)
        file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        if file_size_mb > 49.5:
            bot.edit_message_text(
                f"❌ حجم الملف ({file_size_mb:.1f} MB) يتجاوز حد تيليجرام المسموح به للبوتات (50 MB).",
                chat_id=call.message.chat.id,
                message_id=status_msg.message_id
            )
            return

        bot.edit_message_text(
            "📤 جاري الرفع إلى تيليجرام...",
            chat_id=call.message.chat.id,
            message_id=status_msg.message_id
        )

        with open(downloaded_file, "rb") as media:
            if mode == "vid":
                bot.send_video(
                    call.message.chat.id,
                    media,
                    caption=f"🎬 {title}",
                    supports_streaming=True
                )
            else:
                bot.send_audio(
                    call.message.chat.id,
                    media,
                    title=title
                )

        bot.delete_message(call.message.chat.id, status_msg.message_id)

    except Exception as e:
        bot.edit_message_text(
            f"❌ حدث خطأ أثناء التحميل:\n`{str(e)[:200]}`",
            chat_id=call.message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="Markdown"
        )
    finally:
        # تنظيف الملف المؤقت فوراً
        if downloaded_file and os.path.exists(downloaded_file):
            os.remove(downloaded_file)

if __name__ == "__main__":
    print("Bot is running...")
    bot.infinity_polling(skip_pending=True)
