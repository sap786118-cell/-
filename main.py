import subprocess
import sys
import os
import logging
import json
import threading
import time
import random
import string
import re
import requests
import zipfile
import hashlib
import base64
from datetime import datetime, timedelta
from html import escape
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes
from http.server import HTTPServer, SimpleHTTPRequestHandler

# ================== خادم ويب مدمج لفتح البورت لـ Render ==================
def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ================== إعداد التسجيل ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ================== استخراج التوكن والأيدي من متغيرات البيئة ==================
TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID_ENV = os.environ.get("ADMIN_ID")

if not TOKEN:
    logger.critical("❌ لم يتم العثور على BOT_TOKEN في متغيرات البيئة! يرجى إضافته في Render.")
    sys.exit(1)

try:
    ADMIN_ID = int(ADMIN_ID_ENV) if ADMIN_ID_ENV else 0
except ValueError:
    logger.critical("❌ متغير ADMIN_ID يجب أن يكون رقماً صحيحاً!")
    sys.exit(1)

# ================== تثبيت المكتبات المطلوبة ==================
required_modules = {
    'telebot': 'pyTelegramBotAPI',
    'requests': 'requests',
    'Crypto': 'pycryptodome'
}
missing_packages = []
for module, package in required_modules.items():
    try:
        __import__(module)
    except ImportError:
        missing_packages.append(package)

if missing_packages:
    logger.info(f"📦 جاري تثبيت الحزم المفقودة: {missing_packages}")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing_packages)
        logger.info("✅ تم التثبيت بنجاح.")
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ فشل التثبيت: {e}")
        sys.exit(1)

import telebot
from telebot import types

# ================== إنشاء البوت والمجلدات ==================
bot = telebot.TeleBot(TOKEN, threaded=True, parse_mode="HTML")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNNING_DIR = os.path.join(BASE_DIR, 'active_bots')
LOGS_DIR = os.path.join(BASE_DIR, 'bot_logs')
DB_DIR = os.path.join(BASE_DIR, 'database')
ASSETS_DIR = os.path.join(BASE_DIR, 'assets')
THUMBS_DIR = os.path.join(ASSETS_DIR, 'thumbs')
ENV_DIR = os.path.join(BASE_DIR, 'bot_environments')
ENCRYPTED_DIR = os.path.join(BASE_DIR, 'encrypted_files')

for d in [RUNNING_DIR, LOGS_DIR, DB_DIR, ASSETS_DIR, THUMBS_DIR, ENV_DIR, ENCRYPTED_DIR]:
    os.makedirs(d, exist_ok=True)

# ================== مسارات قواعد البيانات ==================
USERS_DB = os.path.join(DB_DIR, 'users.json')
FILES_DB = os.path.join(DB_DIR, 'files.json')
SETTINGS_DB = os.path.join(DB_DIR, 'settings.json')
ADMINS_DB = os.path.join(DB_DIR, 'admins.json')
SECURITY_DB = os.path.join(DB_DIR, 'security.json')

# ================== المتغيرات العامة ==================
db_lock = threading.Lock()
cancel_states = {}
last_bot_messages = {}
active_processes = {}
process_hours = {}
user_notifications = {}

def read_json(path):
    with db_lock:
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"خطأ في قراءة {path}: {e}")
            return {}

def write_json(path, data):
    with db_lock:
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"خطأ في كتابة {path}: {e}")

def save_settings(data):
    write_json(SETTINGS_DB, data)

def deco(title, content):
    settings = read_json(SETTINGS_DB)
    name = settings.get('bot_name', 'Div: @scofr')
    return f"<b>{title}</b>\n\n{content}\n\n<b>Div: @scofr</b>"

def get_master_key():
    security = read_json(SECURITY_DB)
    master_key = security.get('master_key')
    if not master_key:
        master_key = base64.b64encode(get_random_bytes(32)).decode('utf-8')
        security['master_key'] = master_key
        write_json(SECURITY_DB, security)
    return base64.b64decode(master_key)

def generate_file_key(fid, user_id):
    security = read_json(SECURITY_DB)
    file_keys = security.get('file_keys', {})
    if fid not in file_keys:
        combined = f"{fid}:{user_id}:{ADMIN_ID}:{TOKEN}"
        salt = hashlib.sha256(combined.encode()).digest()[:16]
        master_key = get_master_key()
        kdf = hashlib.pbkdf2_hmac('sha256', master_key, salt, 100000, dklen=32)
        file_keys[fid] = {
            'key': base64.b64encode(kdf).decode('utf-8'),
            'salt': base64.b64encode(salt).decode('utf-8'),
            'user_id': user_id
        }
        security['file_keys'] = file_keys
        write_json(SECURITY_DB, security)
    return file_keys[fid]

def get_file_key(fid):
    security = read_json(SECURITY_DB)
    return security.get('file_keys', {}).get(fid)

def encrypt_file_content(content, fid, user_id):
    try:
        file_key_info = generate_file_key(fid, user_id)
        key = base64.b64decode(file_key_info['key'])
        salt = base64.b64decode(file_key_info['salt'])
        cipher = AES.new(key, AES.MODE_CBC)
        ct_bytes = cipher.encrypt(pad(content.encode('utf-8'), AES.block_size))
        encrypted_data = {
            'iv': base64.b64encode(cipher.iv).decode('utf-8'),
            'ciphertext': base64.b64encode(ct_bytes).decode('utf-8'),
            'salt': base64.b64encode(salt).decode('utf-8'),
            'fid': fid,
            'user_id': user_id,
            'timestamp': datetime.now().isoformat()
        }
        return json.dumps(encrypted_data)
    except Exception as e:
        logger.error(f"Encryption error: {e}")
        return None

def decrypt_file_content(encrypted_json, fid):
    try:
        data = json.loads(encrypted_json)
        file_key_info = get_file_key(fid)
        if not file_key_info:
            return None
        key = base64.b64decode(file_key_info['key'])
        iv = base64.b64decode(data['iv'])
        ct = base64.b64decode(data['ciphertext'])
        cipher = AES.new(key, AES.MODE_CBC, iv)
        pt = unpad(cipher.decrypt(ct), AES.block_size)
        return pt.decode('utf-8')
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return None

def load_encrypted_file(fid):
    encrypted_path = os.path.join(ENCRYPTED_DIR, f"{fid}.enc")
    if os.path.exists(encrypted_path):
        with open(encrypted_path, 'r', encoding='utf-8') as f:
            encrypted_content = f.read()
        return decrypt_file_content(encrypted_content, fid)
    return None

def verify_file_access(fid, user_id):
    files = read_json(FILES_DB)
    if fid not in files:
        return False
    file_info = files[fid]
    file_user_id = file_info.get('user_id')
    if user_id == ADMIN_ID or is_admin(user_id):
        return True
    return file_user_id == user_id

def is_bot_locked():
    return read_json(SETTINGS_DB).get('bot_locked', False)

def toggle_bot_lock():
    settings = read_json(SETTINGS_DB)
    settings['bot_locked'] = not settings.get('bot_locked', False)
    write_json(SETTINGS_DB, settings)
    return settings['bot_locked']

def toggle_auto_approve():
    settings = read_json(SETTINGS_DB)
    settings['auto_approve'] = not settings.get('auto_approve', True)
    write_json(SETTINGS_DB, settings)
    return settings['auto_approve']

def is_admin(user_id):
    if user_id == ADMIN_ID:
        return True
    admins_data = read_json(ADMINS_DB)
    return user_id in admins_data.get("admins", [])

def is_main_admin(user_id):
    return user_id == ADMIN_ID

def is_user_pro(uid):
    if uid == ADMIN_ID or is_admin(uid):
        return True
    users = read_json(USERS_DB)
    u = users.get(str(uid), {})
    expiry = u.get('expiry')
    if not expiry or expiry == 'null':
        return False
    if expiry == 'LIFETIME' or expiry == 0:
        return True
    try:
        exp_date = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
        if datetime.now() < exp_date:
            return True
        u['expiry'] = None
        users[str(uid)] = u
        write_json(USERS_DB, users)
        return False
    except:
        return False

def check_sub(user_id):
    if user_id == ADMIN_ID or is_admin(user_id):
        return True
    settings = read_json(SETTINGS_DB)
    channels = settings.get('channels', [])
    if not channels:
        return True
    try:
        for ch in channels:
            member = bot.get_chat_member(ch["username"], user_id)
            if member.status in ['left', 'kicked']:
                return False
        return True
    except:
        return True

def set_cancel(uid, state=True):
    cancel_states[uid] = state

def is_cancelled(uid):
    return cancel_states.get(uid, False)

def clear_cancel(uid):
    if uid in cancel_states:
        del cancel_states[uid]

def gen_id(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def create_zip(files_list, zip_name):
    zip_path = os.path.join(BASE_DIR, zip_name)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for file_path in files_list:
            if os.path.exists(file_path):
                zipf.write(file_path, os.path.basename(file_path))
    return zip_path

# ================== تشغيل وإيقاف السكريبتات بأمان ==================
def start_script(fid):
    files = read_json(FILES_DB)
    if fid not in files:
        return False
    file_info = files[fid]
    user_id = file_info.get('user_id')
    if not verify_file_access(fid, user_id):
        return False
    encrypted_content = load_encrypted_file(fid)
    if not encrypted_content:
        return False
    env_dir = os.path.join(ENV_DIR, fid)
    os.makedirs(env_dir, exist_ok=True)
    env_file_path = os.path.join(env_dir, f"{fid}.py")
    if fid in active_processes and active_processes[fid].poll() is None:
        return True
    try:
        with open(env_file_path, 'w', encoding='utf-8') as f:
            f.write(encrypted_content)
    except Exception as e:
        logger.error(f"فشل كتابة السكريبت {fid}: {e}")
        return False
    
    log_path = os.path.join(LOGS_DIR, f"{fid}.log")
    try:
        log_file = open(log_path, "a", encoding="utf-8")
        
        # ثغرة أمنية تم إصلاحها: عزل التوكن ومتغيرات بيئة النظام عن السكريبتات المرفوعة
        safe_env = {k: v for k, v in os.environ.items() if k not in ['BOT_TOKEN', 'ADMIN_ID', 'MASTER_KEY']}
        safe_env["PYTHONPATH"] = env_dir

        proc = subprocess.Popen(
            [sys.executable, "-u", env_file_path],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.PIPE,
            cwd=env_dir,
            start_new_session=True,
            env=safe_env
        )
        active_processes[fid] = proc
        return True
    except Exception as e:
        logger.error(f"فشل تشغيل السكريبت {fid}: {e}")
        return False

def stop_script(fid):
    if fid in active_processes:
        proc = active_processes[fid]
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except:
            try:
                proc.terminate()
            except:
                pass
        del active_processes[fid]
        if fid in process_hours:
            del process_hours[fid]
        return True
    return False

def stop_all_scripts():
    for fid in list(active_processes.keys()):
        stop_script(fid)
    return True

# ================== دوال إرسال الرسائل ==================
def delete_last_message(chat_id):
    if chat_id in last_bot_messages:
        try:
            bot.delete_message(chat_id, last_bot_messages[chat_id])
        except:
            pass

def save_message(chat_id, msg_id):
    last_bot_messages[chat_id] = msg_id

def send_msg(chat_id, text, markup=None):
    delete_last_message(chat_id)
    settings = read_json(SETTINGS_DB)
    try:
        if settings.get('bot_image'):
            msg = bot.send_photo(chat_id, settings['bot_image'], caption=text, parse_mode="HTML", reply_markup=markup)
        else:
            msg = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
        save_message(chat_id, msg.message_id)
        return msg
    except Exception as e:
        logger.error(f"Send message error: {e}")
        msg = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
        save_message(chat_id, msg.message_id)
        return msg

def edit_msg(call, text, markup):
    try:
        if call.message.content_type == 'photo':
            bot.edit_message_caption(text[:4096], call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=markup)
        else:
            bot.edit_message_text(text[:4096], call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=markup)
        save_message(call.message.chat.id, call.message.message_id)
    except:
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        settings = read_json(SETTINGS_DB)
        try:
            if settings.get('bot_image'):
                msg = bot.send_photo(call.message.chat.id, settings['bot_image'], caption=text[:4096], parse_mode="HTML", reply_markup=markup)
            else:
                msg = bot.send_message(call.message.chat.id, text[:4096], parse_mode="HTML", reply_markup=markup)
            save_message(call.message.chat.id, msg.message_id)
        except:
            msg = bot.send_message(call.message.chat.id, text[:4096], parse_mode="HTML", reply_markup=markup)
            save_message(call.message.chat.id, msg.message_id)

def locked_msg(chat_id):
    text = "🔒 <b>البوت مغلق حالياً</b>\n\nتم إيقاف الخدمة مؤقتاً\n\nيمكنك التواصل عبر الزر أدناه."
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("👨‍💻 تواصل مع المطور", url=f"tg://user?id={ADMIN_ID}"))
    send_msg(chat_id, deco("🔒 البوت مغلق", text), markup)

# ================== لوحات الأزرار ==================
def main_kb(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📤 رفع ملف جديد", callback_data="nav_upload"))
    kb.row(types.InlineKeyboardButton("📁 ملفاتي", callback_data="nav_files"))
    kb.row(
        types.InlineKeyboardButton("💼 محفظتي", callback_data="nav_wallet"),
        types.InlineKeyboardButton("📊 حسابي", callback_data="nav_stats")
    )
    kb.row(
        types.InlineKeyboardButton("🛠 تثبيت مكتبة", callback_data="nav_lib"),
        types.InlineKeyboardButton("📖 التعليمات", callback_data="nav_help")
    )
    if is_user_pro(uid):
        kb.row(types.InlineKeyboardButton("🔧 لوحة Pro", callback_data="nav_pro"))
    kb.add(types.InlineKeyboardButton("👨‍💻 تواصل مع المطور", url=f"tg://user?id={ADMIN_ID}"))
    if is_admin(uid):
        kb.add(types.InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="nav_admin"))
    return kb

def cancel_kb(data="cancel"):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("❌ إلغاء", callback_data=data))
    return kb

# ================== الأوامر والمعالجات ==================
@bot.message_handler(commands=['myid'])
def myid_cmd(msg):
    uid = msg.from_user.id
    bot.reply_to(msg, f"🧑‍💻 معرفك هو: <code>{uid}</code>", parse_mode="HTML")

@bot.message_handler(commands=['start'])
def start_cmd(msg):
    try:
        uid = msg.from_user.id
        if is_bot_locked() and not is_admin(uid):
            locked_msg(msg.chat.id)
            return
        users = read_json(USERS_DB)
        clear_cancel(uid)
        if str(uid) not in users:
            users[str(uid)] = {
                'username': msg.from_user.username,
                'first_name': msg.from_user.first_name,
                'points': 10,
                'join_date': str(datetime.now().date()),
                'is_banned': 0,
                'expiry': None,
                'last_daily': None
            }
            write_json(USERS_DB, users)

        if not check_sub(uid):
            return
        
        u = users.get(str(uid), {})
        vip = is_user_pro(uid)
        welcome_text = (
            f"✨ <b>مرحباً بك {escape(msg.from_user.first_name or '')}</b> ✨\n\n"
            f"🔹 <b>رتبتك:</b> {'VIP 👑' if vip else 'مجاني 🆓'}\n"
            f"🔹 <b>نقاطك:</b> <code>{u.get('points', 0)}</code>\n"
            f"⚡ يمكنك رفع ملف .py واستضافته بسهولة!"
        )
        send_msg(msg.chat.id, deco("🏠 القائمة الرئيسية", welcome_text), main_kb(uid))
    except Exception as e:
        logger.error(f"Start error: {e}")

# ================== معالجة تثبيت المكتبات بآمان ==================
def lib_step(msg, prompt_id):
    uid = msg.from_user.id
    if is_cancelled(uid):
        clear_cancel(uid)
        return
    try:
        bot.delete_message(msg.chat.id, prompt_id)
        bot.delete_message(msg.chat.id, msg.message_id)
    except:
        pass

    lib_name = msg.text.strip() if msg.text else ""
    
    # ثغرة أمنية تم إصلاحها: فحص اسم المكتبة لمنع إدخال أوامر أجهزة تحكم أمنية
    if not re.match(r'^[a-zA-Z0-9_\-]+$', lib_name):
        send_msg(msg.chat.id, deco("❌ خطأ", "اسم المكتبة غير صالح! يرجى استخدام أحرف وأرقام فقط."), main_kb(uid))
        return

    m = send_msg(msg.chat.id, deco("🛠 تثبيت مكتبة", f"جاري تثبيت المكتبة <code>{escape(lib_name)}</code>..."))
    try:
        res = subprocess.run([sys.executable, "-m", "pip", "install", lib_name], capture_output=True, text=True, timeout=60)
        if res.returncode == 0:
            send_msg(msg.chat.id, deco("✅ تم التثبيت", f"تم تثبيت المكتبة <b>{escape(lib_name)}</b> بنجاح!"), main_kb(uid))
        else:
            send_msg(msg.chat.id, deco("❌ فشل التثبيت", f"خطأ أثناء التثبيت:\n<pre>{escape(res.stderr[:500])}</pre>"), main_kb(uid))
    except Exception as e:
        send_msg(msg.chat.id, deco("❌ خطأ", f"حدث خطأ أثناء التنفيذ: {escape(str(e))}"), main_kb(uid))

# ================== الاستجابة للأزرار callback_query ==================
@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    try:
        uid = call.from_user.id
        cid = call.message.chat.id
        data = call.data
        users = read_json(USERS_DB)

        if is_bot_locked() and not is_admin(uid):
            bot.answer_callback_query(call.id, "🔒 البوت مغلق!", show_alert=True)
            locked_msg(cid)
            return

        if data == "cancel":
            set_cancel(uid, True)
            bot.answer_callback_query(call.id, "✅ تم الإلغاء")
            u = users.get(str(uid), {})
            vip = is_user_pro(uid)
            text = f"💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n💰 نقاطك: <code>{u.get('points', 0)}</code>"
            edit_msg(call, deco("🏠 القائمة الرئيسية", text), main_kb(uid))
            return

        if data == "nav_main":
            u = users.get(str(uid), {})
            vip = is_user_pro(uid)
            text = f"💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n💰 نقاطك: <code>{u.get('points', 0)}</code>"
            edit_msg(call, deco("🏠 القائمة الرئيسية", text), main_kb(uid))

        elif data == "nav_lib":
            m = send_msg(cid, deco("🛠 تثبيت مكتبة", "أرسل اسم المكتبة المطلوبة لتثبيتها:"), reply_markup=cancel_kb())
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, lib_step, m.message_id)

    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "❌ حدث خطأ أثناء المعالجة", show_alert=True)

# ================== متابعة واستهلاك الساعات ==================
def check_hours_loop():
    while True:
        time.sleep(3600)
        with db_lock:
            for fid in list(process_hours.keys()):
                process_hours[fid] -= 1
                if process_hours[fid] <= 0:
                    stop_script(fid)
                    del process_hours[fid]
                    files = read_json(FILES_DB)
                    if fid in files:
                        files[fid]['status'] = 'expired'
                        write_json(FILES_DB, files)

threading.Thread(target=check_hours_loop, daemon=True).start()

# ================== تشغيل البوت ==================
if __name__ == '__main__':
    logger.info("🚀 جاري تشغيل البوت...")
    try:
        bot.infinity_polling(skip_pending=True)
    except Exception as e:
        logger.critical(f"❌ خطأ غير متوقع أثناء التشغيل: {e}")
