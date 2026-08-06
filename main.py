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
from telebot import types
from datetime import datetime, timedelta
from html import escape
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes
from http.server import HTTPServer, SimpleHTTPRequestHandler


# تشغيل خادم ويب مدمج لفتح البورت لـ Render
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

# ================== التوكن والمتغيرات الأساسية ==================
TOKEN = "8955451883:AAFmFOjx4bNCpv03SL9dC59U4pblMQzkBR8"
if not TOKEN:
    logger.critical("❌ لم يتم تعيين BOT_TOKEN في متغيرات البيئة")
    sys.exit(1)

ADMIN_ID = 8105998916  # معرف الأدمن الخاص بك

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
        logger.info("✅ تم التثبيت بنجاح، يرجى إعادة تشغيل السكريبت.")
        sys.exit(0)
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ فشل التثبيت: {e}")
        sys.exit(1)

import telebot

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

MAX_FILES_PER_USER = 10

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

def save_encrypted_file(fid, content, user_id):
    encrypted_content = encrypt_file_content(content, fid, user_id)
    if encrypted_content:
        encrypted_path = os.path.join(ENCRYPTED_DIR, f"{fid}.enc")
        with open(encrypted_path, 'w', encoding='utf-8') as f:
            f.write(encrypted_content)
        return True
    return False

def load_encrypted_file(fid):
    encrypted_path = os.path.join(ENCRYPTED_DIR, f"{fid}.enc")
    if os.path.exists(encrypted_path):
        with open(encrypted_path, 'r', encoding='utf-8') as f:
            encrypted_content = f.read()
        return decrypt_file_content(encrypted_content, fid)
    return None

def save_running_file(fid, content):
    running_path = os.path.join(RUNNING_DIR, f"{fid}.py")
    with open(running_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return running_path

# ================== دوال المساعدة الأساسية ==================
def verify_file_access(fid, user_id):
    files = read_json(FILES_DB)
    if fid not in files:
        return False
    file_info = files[fid]
    file_user_id = file_info.get('user_id')
    if user_id == ADMIN_ID or is_admin(user_id):
        return True
    if file_user_id == user_id:
        return True
    return False

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
    """التحقق من صلاحية الأدمن (المالك أو في قائمة الأدمن)"""
    if user_id == ADMIN_ID:
        return True
    admins_data = read_json(ADMINS_DB)
    admins_list = admins_data.get("admins", [])
    return user_id in admins_list

def is_main_admin(user_id):
    return user_id == ADMIN_ID

def get_admins():
    admins_data = read_json(ADMINS_DB)
    return admins_data.get("admins", [ADMIN_ID])

def add_admin(user_id):
    admins_data = read_json(ADMINS_DB)
    if user_id not in admins_data.get("admins", []):
        admins_data["admins"] = admins_data.get("admins", []) + [user_id]
        write_json(ADMINS_DB, admins_data)
        return True
    return False

def remove_admin(user_id):
    if user_id == ADMIN_ID:
        return False
    admins_data = read_json(ADMINS_DB)
    if user_id in admins_data.get("admins", []):
        admins_data["admins"].remove(user_id)
        write_json(ADMINS_DB, admins_data)
        return True
    return False

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
        else:
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

def get_preview(path, lines=40):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                content = f.readlines()
                preview = "".join(content[:lines])
                safe = escape(preview)
                if len(safe) > 3000:
                    safe = safe[:3000] + "\n..."
                return f"<pre><code class='language-python'>{safe}</code></pre>"
        return "❌ تعذر قراءة الملف"
    except Exception as e:
        logger.error(f"Preview error: {e}")
        return "❌ خطأ في القراءة"

def get_logs(fid, lines=40):
    log_path = os.path.join(LOGS_DIR, f"{fid}.log")
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                all_lines = f.readlines()
                last = all_lines[-lines:] if len(all_lines) > lines else all_lines
                output = "".join(last)
                safe = escape(output)
                if len(safe) > 3000:
                    safe = safe[:3000] + "\n..."
                return f"<pre><code>{safe}</code></pre>"
        return "📝 لا توجد مخرجات"
    except Exception as e:
        logger.error(f"Logs error: {e}")
        return "❌ خطأ في القراءة"

def update_token(path, new_token):
    keywords = ["TOKEN", "bot_token", "api_key", "tok", "TKN", "BOT_TKN", "API_TOKEN"]
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        pattern = r"(['\"])\d{8,12}:[a-zA-Z0-9_-]{35,}(['\"])"
        new_content = re.sub(pattern, f"\\1{new_token}\\2", content)
        for kw in keywords:
            kw_pattern = rf"{kw}\s*=\s*(['\"])[^'\"]+(['\"])"
            new_content = re.sub(kw_pattern, f"{kw} = \\1{new_token}\\2", new_content)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    except Exception as e:
        logger.error(f"Update token error: {e}")
        return False

def check_token(token):
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        res = requests.get(url, timeout=15).json()
        if res.get("ok"):
            return True, res["result"]
        return False, res.get("description")
    except Exception as e:
        return False, str(e)

def gen_id(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def set_cancel(uid, state=True):
    cancel_states[uid] = state

def is_cancelled(uid):
    return cancel_states.get(uid, False)

def clear_cancel(uid):
    if uid in cancel_states:
        del cancel_states[uid]

def get_thumb():
    settings = read_json(SETTINGS_DB)
    thumb = settings.get('file_thumb')
    if thumb and os.path.exists(thumb):
        return thumb
    return None

def locked_msg(chat_id):
    text = "🔒 <b>البوت مغلق حالياً</b>\n\nتم إيقاف الخدمة مؤقتاً\n\nيمكنك التواصل عبر الزر أدناه."
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("👨‍💻 تواصل مع المطور", url=f"tg://user?id={ADMIN_ID}"))
    send_msg(chat_id, deco("🔒 البوت مغلق", text), markup)

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
        logger.error(f"Failed to write script {fid}: {e}")
        return False
    log_path = os.path.join(LOGS_DIR, f"{fid}.log")
    try:
        log_file = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-u", env_file_path],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.PIPE,
            cwd=env_dir,
            start_new_session=True,
            env={**os.environ, "PYTHONPATH": env_dir}
        )
        active_processes[fid] = proc
        return True
    except Exception as e:
        logger.error(f"Failed to start script {fid}: {e}")
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

def write_proc(fid, cmd):
    if fid in active_processes and active_processes[fid].poll() is None:
        try:
            proc = active_processes[fid]
            if proc.stdin:
                proc.stdin.write(cmd.encode('utf-8') + b'\n')
                proc.stdin.flush()
                return True
        except:
            pass
    return False

def create_zip(files_list, zip_name):
    zip_path = os.path.join(BASE_DIR, zip_name)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for file_path in files_list:
            if os.path.exists(file_path):
                zipf.write(file_path, os.path.basename(file_path))
    return zip_path

def auto_fix_errors(code):
    fixes = [
        (r'print\s+(\S+)', r'print(\1)'),
        (r'raw_input', 'input'),
        (r'xrange', 'range'),
        (r'\.iteritems\(\)', '.items()'),
        (r'\.itervalues\(\)', '.values()'),
        (r'\.iterkeys\(\)', '.keys()'),
    ]
    for pattern, replacement in fixes:
        code = re.sub(pattern, replacement, code)
    return code

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

def del_msg(chat_id, *msg_ids):
    for msg_id in msg_ids:
        if msg_id:
            try:
                bot.delete_message(chat_id, msg_id)
            except:
                pass


def main_kb(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📤 رفع ملف جديد", callback_data="nav_upload"))
    kb.row(
        types.InlineKeyboardButton("📁 ملفاتي", callback_data="nav_files"),
    )
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

def pro_panel_kb(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📥 تحميل جميع الملفات", callback_data="pro_download_all"))
    kb.add(types.InlineKeyboardButton("🔍 فحص تلقائي", callback_data="pro_auto_fix"))
    kb.add(types.InlineKeyboardButton("▶️ تشغيل تجريبي", callback_data="pro_test_run"))

    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
    return kb

def cancel_kb(data="cancel"):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("❌ إلغاء", callback_data=data))
    return kb

def back_kb(data="nav_main"):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data=data))
    return kb


@bot.message_handler(commands=['myid'])
def myid_cmd(msg):
    """أمر لعرض معرف المستخدم (للمالك فقط)"""
    uid = msg.from_user.id
    bot.reply_to(msg, f"🧑‍💻 معرفك هو: <code>{uid}</code>", parse_mode="HTML")
    if uid == ADMIN_ID:
        bot.send_message(msg.chat.id, f"✅ هذا هو نفس المعرف المسجل في الكود (ADMIN_ID = {ADMIN_ID}).")
    else:
        bot.send_message(msg.chat.id, f"⚠️ هذا المعرف ({uid}) يختلف عن ADMIN_ID المسجل في الكود ({ADMIN_ID}).")

# ================== بداية معالجات البوت ==================

@bot.message_handler(commands=['start'])
def start_cmd(msg):
    try:
        uid = msg.from_user.id
        if is_bot_locked() and not is_admin(uid):
            try:
                bot.delete_message(msg.chat.id, msg.message_id)
            except:
                pass
            locked_msg(msg.chat.id)
            return
        users = read_json(USERS_DB)
        clear_cancel(uid)
        if str(uid) not in users:

            if len(msg.text.split()) > 1:
                ref = msg.text.split()[1]
                if ref.isdigit() and int(ref) != uid:
                    udb = read_json(USERS_DB)
                    if str(ref) in udb:
                        udb[str(ref)]['points'] = udb[str(ref)].get('points', 0) + 10
                        write_json(USERS_DB, udb)
                        try:
                            bot.send_message(int(ref), deco("🎁 مكافأة", "حصلت على 10 نقاط لإحالة شخص!"))
                        except:
                            pass

            users[str(uid)] = {
                'username': msg.from_user.username,
                'first_name': msg.from_user.first_name,
                'last_name': msg.from_user.last_name,
                'points': 10,
                'join_date': str(datetime.now().date()),
                'is_banned': 0,
                'expiry': None,
                'last_daily': None,
                'notifications': True
            }
            write_json(USERS_DB, users)
            user_notifications[uid] = True
        
        users = read_json(USERS_DB)
        if users.get(str(uid), {}).get('is_banned', 0) == 1:
            return bot.send_message(msg.chat.id, deco("🚫 محظور", "تم حظرك من البوت."))
        if not check_sub(uid):
            return sub_msg(msg.chat.id)
        try:
            bot.delete_message(msg.chat.id, msg.message_id)
        except:
            pass
        
        u = users.get(str(uid), {})
        vip = is_user_pro(uid)
        
        welcome_text = (
            f"✨ <b>مرحباً بك {escape(msg.from_user.first_name)}</b> ✨\n\n"
            f"🔹 <b>رتبتك:</b> {'VIP 👑' if vip else 'مجاني 🆓'}\n"
            f"🔹 <b>نقاطك:</b> <code>{u.get('points', 0)}</code>\n"
            f"🔹 <b>عضو منذ:</b> {u.get('join_date', 'اليوم')}\n\n"
            f"⚡ يمكنك رفع ملف .py واستضافته بسهولة!\n"
            f"📖 استخدم الأزرار أدناه للبدء."
        )
        send_msg(msg.chat.id, deco("🏠 القائمة الرئيسية", welcome_text), main_kb(uid))
    except Exception as e:
        logger.error(f"Start error: {e}")

def sub_msg(chat_id):
    settings = read_json(SETTINGS_DB)
    channels = settings.get('channels', [])
    if not channels:
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        kb.add(types.InlineKeyboardButton(f"📢 {ch['name']}", url=f"https://t.me/{ch['username'].replace('@', '')}"))
    kb.add(types.InlineKeyboardButton("✅ تحقق", callback_data="check_sub"))
    text = "🔔 <b>اشتراك إجباري</b>\n\nيجب الاشتراك في القنوات التالية:"
    send_msg(chat_id, deco("🔔 اشتراك مطلوب", text), kb)


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
        if str(uid) in users and users[str(uid)].get('is_banned', 0) == 1:
            return bot.answer_callback_query(call.id, "🚫 أنت محظور!", show_alert=True)
        if data == "cancel":
            set_cancel(uid, True)
            bot.answer_callback_query(call.id, "✅ تم الإلغاء")
            u = users.get(str(uid), {})
            vip = is_user_pro(uid)
            text = f"💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n💰 نقاطك: <code>{u.get('points', 0)}</code>"
            edit_msg(call, deco("🏠 القائمة الرئيسية", text), main_kb(uid))
            return
        if data == "cancel_admin":
            set_cancel(uid, True)
            bot.answer_callback_query(call.id, "✅ تم الإلغاء")
            admin_panel(call)
            return
        if data == "check_sub":
            if check_sub(uid):
                bot.answer_callback_query(call.id, "✅ تم التحقق!")
                u = users.get(str(uid), {})
                vip = is_user_pro(uid)
                text = f"💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n💰 نقاطك: <code>{u.get('points', 0)}</code>"
                edit_msg(call, deco("🏠 القائمة الرئيسية", text), main_kb(uid))
            else:
                bot.answer_callback_query(call.id, "❌ لم تشترك!", show_alert=True)
            return
        if not check_sub(uid) and not is_admin(uid):
            bot.answer_callback_query(call.id, "❌ اشترك أولاً!", show_alert=True)
            return
        clear_cancel(uid)
        if data == "nav_main":
            u = users.get(str(uid), {})
            vip = is_user_pro(uid)
            text = f"💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n💰 نقاطك: <code>{u.get('points', 0)}</code>"
            edit_msg(call, deco("🏠 القائمة الرئيسية", text), main_kb(uid))
        elif data == "nav_pro":
            if not is_user_pro(uid):
                bot.answer_callback_query(call.id, "❌ لمشتركي VIP فقط!", show_alert=True)
                return
            text = "🔧 <b>لوحة VIP المميزة</b>\n\nاستمتع بمزايا حصرية لمشتركي VIP"
            edit_msg(call, deco("🔧 لوحة Pro", text), pro_panel_kb(uid))
        elif data == "pro_download_all":
            if not is_user_pro(uid):
                bot.answer_callback_query(call.id, "❌ لمشتركي VIP فقط!", show_alert=True)
                return
            files = read_json(FILES_DB)
            u_files = {fid: f for fid, f in files.items() if f.get('user_id') == uid and f.get('status') == 'active'}
            if not u_files:
                bot.answer_callback_query(call.id, "📂 لا ملفات!", show_alert=True)
                return
            decrypted_files = []
            for fid in u_files.keys():
                if verify_file_access(fid, uid):
                    content = load_encrypted_file(fid)
                    if content:
                        temp_path = os.path.join(BASE_DIR, f"temp_{fid}_{gen_id(4)}.py")
                        with open(temp_path, 'w', encoding='utf-8') as f:
                            f.write(content)
                        decrypted_files.append(temp_path)
            if decrypted_files:
                zip_name = f"files_{uid}_{gen_id(4)}.zip"
                zip_path = create_zip(decrypted_files, zip_name)
                try:
                    with open(zip_path, 'rb') as f:
                        bot.send_document(cid, f, caption="📦 جميع ملفاتك في أرشيف واحد")
                    for temp_file in decrypted_files:
                        try:
                            os.remove(temp_file)
                        except:
                            pass
                    os.remove(zip_path)
                except:
                    bot.answer_callback_query(call.id, "❌ فشل في التحميل!", show_alert=True)
            else:
                bot.answer_callback_query(call.id, "❌ لا ملفات للتحميل!", show_alert=True)
        elif data == "pro_auto_fix":
            if not is_user_pro(uid):
                bot.answer_callback_query(call.id, "❌ لمشتركي VIP فقط!", show_alert=True)
                return
            m = bot.send_message(cid, deco("🔍 فحص تلقائي", "أرسل ملف .py لفحصه وتصحيح الأخطاء:"), reply_markup=cancel_kb())
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, auto_fix_step, m.message_id)
        elif data == "pro_test_run":
            if not is_user_pro(uid):
                bot.answer_callback_query(call.id, "❌ لمشتركي VIP فقط!", show_alert=True)
                return
            files = read_json(FILES_DB)
            u_files = {fid: f for fid, f in files.items() if f.get('user_id') == uid and f.get('status') == 'active'}
            if not u_files:
                bot.answer_callback_query(call.id, "📂 لا ملفات!", show_alert=True)
                return
            kb = types.InlineKeyboardMarkup(row_width=1)
            for fid, f in u_files.items():
                kb.add(types.InlineKeyboardButton(f"📄 {f.get('file_name', '?')[:25]}", callback_data=f"testrun_{fid}"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_pro"))
            edit_msg(call, deco("▶️ تشغيل تجريبي", "اختر ملف للتشغيل التجريبي:"), kb)
        elif data.startswith("testrun_"):
            fid = data.split("_")[1]
            if not verify_file_access(fid, uid):
                bot.answer_callback_query(call.id, "❌ لا تملك صلاحية الوصول!", show_alert=True)
                return
            content = load_encrypted_file(fid)
            if not content:
                bot.answer_callback_query(call.id, "❌ الملف غير موجود!", show_alert=True)
                return
            try:
                temp_path = os.path.join(BASE_DIR, f"temp_run_{gen_id(4)}.py")
                with open(temp_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                proc = subprocess.Popen([sys.executable, temp_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                stdout, stderr = proc.communicate()
                os.remove(temp_path)
                if proc.returncode == 0:
                    bot.answer_callback_query(call.id, "✅ تم التشغيل التجريبي بنجاح!")
                else:
                    bot.answer_callback_query(call.id, f"❌ خطأ: {stderr.decode()[:100]}", show_alert=True)
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ خطأ: {str(e)[:100]}", show_alert=True)

        elif data == "nav_wallet":
            u = users.get(str(uid), {})
            vip = is_user_pro(uid)
            exp = "لا يوجد"
            if vip:
                e = u.get('expiry')
                if e == 'LIFETIME' or e == 0:
                    exp = "دائم ♾"
                elif e:
                    exp = e
            today = str(datetime.now().date())
            can = u.get('last_daily') != today
            text = f"💰 رصيدك: <code>{u.get('points', 0)}</code>\n💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n⏰ صلاحية VIP: {exp}\n\n💡 كل نقطة = ساعة"
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton(f"🎁 الهدية {'✅' if can else '❌'}", callback_data="daily"),
                types.InlineKeyboardButton("🔗 رابط الإحالة", callback_data="ref")
            )
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            edit_msg(call, deco("💼 محفظتي", text), kb)
        elif data == "daily":
            u = users.get(str(uid))
            today = str(datetime.now().date())
            if u.get('last_daily') == today:
                return bot.answer_callback_query(call.id, "❌ حصلت عليها اليوم!", show_alert=True)
            gift = random.randint(5, 15)
            u['points'] = u.get('points', 0) + gift
            u['last_daily'] = today
            users[str(uid)] = u
            write_json(USERS_DB, users)
            bot.answer_callback_query(call.id, f"🎁 حصلت على {gift} نقاط!", show_alert=True)
            vip = is_user_pro(uid)
            text = f"💰 رصيدك: <code>{u.get('points', 0)}</code>\n💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n\n✅ تم إضافة {gift} نقاط!"
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🎁 الهدية ❌", callback_data="daily"),
                types.InlineKeyboardButton("🔗 رابط الإحالة", callback_data="ref")
            )
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            edit_msg(call, deco("💼 محفظتي", text), kb)
        elif data == "ref":
            info = bot.get_me()
            link = f"https://t.me/{info.username}?start={uid}"
            text = f"🔗 رابطك:\n<code>{link}</code>\n\n💰 كل شخص = 10 نقاط!"
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_wallet"))
            edit_msg(call, deco("🔗 رابط الإحالة", text), kb)
        elif data == "nav_help":
            help_text = (
                "📖 <b>دليل الاستخدام</b>\n\n"
                "🚀 <b>الاستضافة:</b>\n"
                "• ارفع ملف .py\n"
                "• اختر المدة (ساعات للنقاط أو VIP غير محدود)\n"
                "• ينتظر الموافقة (أو يتم قبوله تلقائياً)\n\n"
                "💰 <b>النقاط:</b>\n"
                "• كل نقطة = ساعة تشغيل\n"
                "• هدية يومية عشوائية (5-15 نقطة)\n"
                "• إحالة صديق = 10 نقاط\n\n"
                "💎 <b>VIP:</b>\n"
                "• استضافة غير محدودة المدة\n"
                "• لا يتم خصم نقاط\n"
                "• مزايا إضافية (تحميل الكل، فحص تلقائي، وغيرها)\n\n"
                "👨‍💻 للتواصل: @scofr"
            )
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("👨‍💻 المطور", url=f"tg://user?id={ADMIN_ID}"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            edit_msg(call, deco("📖 التعليمات", help_text), kb)
        elif data == "nav_upload":
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🆓 مجانية", callback_data="up_free"),
                types.InlineKeyboardButton("💎 VIP", callback_data="up_pro")
            )
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            text = "📤 اختر نوع الاستضافة:\n\n🆓 مجانية: بالنقاط\n💎 VIP: غير محدودة"
            edit_msg(call, deco("📤 رفع ملف", text), kb)
        elif data.startswith("up_"):
            h_type = data.split("_")[1]
            if h_type == "pro" and not is_user_pro(uid):
                return bot.answer_callback_query(call.id, "❌ لمشتركي VIP فقط!", show_alert=True)
            if h_type == "free":
                u = users.get(str(uid), {})
                if u.get('points', 0) < 1:
                    return bot.answer_callback_query(call.id, "❌ لا نقاط كافية!", show_alert=True)
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("📤 إرسال الملف", "📥 أرسل ملف .py:"), reply_markup=cancel_kb())
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, upload_step, h_type, m.message_id)
        elif data == "nav_files":
            files = read_json(FILES_DB)
            u_files = {fid: f for fid, f in files.items() if f.get('user_id') == uid and f.get('status') == 'active'}
            if not u_files:
                return bot.answer_callback_query(call.id, "📂 لا ملفات!", show_alert=True)
            kb = types.InlineKeyboardMarkup(row_width=1)
            for fid, f in u_files.items():
                running = fid in active_processes and active_processes[fid].poll() is None
                icon = "🟢" if running else "🔴"
                ft = "💎" if f.get('type') == 'pro' else "🆓"
                kb.add(types.InlineKeyboardButton(f"{icon} {ft} {f.get('file_name', '?')[:25]}", callback_data=f"manage_{fid}"))
            if is_user_pro(uid):
                kb.add(types.InlineKeyboardButton("📦 تحميل الكل (ZIP)", callback_data="pro_download_all"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            running_count = sum(1 for fid in u_files if fid in active_processes and active_processes[fid].poll() is None)
            text = f"📊 الملفات: {len(u_files)}\n🟢 تعمل: {running_count}\n🔴 متوقفة: {len(u_files) - running_count}"
            edit_msg(call, deco("📁 ملفاتي", text), kb)
        elif data.startswith("manage_"):
            file_panel(call, data.split("_")[1])
        elif data.startswith("toggle_"):
            toggle_file(call, data.split("_")[1])
        elif data.startswith("delc_"):
            fid = data.split("_")[1]
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("✅ نعم", callback_data=f"del_{fid}"),
                types.InlineKeyboardButton("❌ لا", callback_data=f"manage_{fid}")
            )
            edit_msg(call, deco("🗑️ تأكيد", "هل تريد حذف الملف؟"), kb)
        elif data.startswith("del_"):
            delete_file(call, data.split("_")[1])
        elif data.startswith("dl_"):
            download_file(call, data.split("_")[1])
        elif data.startswith("term_"):
            terminal(call, data.split("_")[1])
        elif data.startswith("rterm_"):
            terminal(call, data.split("_")[1])
        elif data.startswith("inp_"):
            fid = data.split("_")[1]
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("⌨️ إدخال", "اكتب الأمر:"), reply_markup=cancel_kb())
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, input_step, fid, m.message_id)
        elif data.startswith("chtoken_"):
            fid = data.split("_")[1]
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("🔑 تغيير التوكن", "أرسل التوكن:"), reply_markup=cancel_kb())
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, token_step, fid, m.message_id)
        elif data.startswith("tokinfo_"):
            token_info(call, data.split("_")[1])

        elif data == "nav_lib":
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("🛠 تثبيت مكتبة", "أرسل اسم المكتبة:"), reply_markup=cancel_kb())
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, lib_step, m.message_id)
        elif data == "nav_stats":
            files = read_json(FILES_DB)
            u = users.get(str(uid), {})
            u_files = [f for f in files.values() if f.get('user_id') == uid and f.get('status') == 'active']
            running = sum(1 for fid, f in files.items() if f.get('user_id') == uid and fid in active_processes and active_processes[fid].poll() is None)
            vip = is_user_pro(uid)
            exp = "لا يوجد"
            if vip:
                e = u.get('expiry')
                if e == 'LIFETIME' or e == 0:
                    exp = "دائم ♾"
                elif e:
                    try:
                        ed = datetime.strptime(e, "%Y-%m-%d %H:%M:%S")
                        rem = ed - datetime.now()
                        exp = f"{rem.days} يوم"
                    except:
                        exp = e
            text = f"🆔 الآيدي: <code>{uid}</code>\n🔗 المعرف: @{u.get('username', 'لا يوجد')}\n📅 الانضمام: {u.get('join_date', '?')}\n\n💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n⏰ صلاحية VIP: {exp}\n💰 النقاط: <code>{u.get('points', 0)}</code>\n\n📁 الملفات: {len(u_files)}\n🟢 تعمل: {running}"
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("💼 محفظتي", callback_data="nav_wallet"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            edit_msg(call, deco("📊 حسابي", text), kb)
        elif data == "nav_admin" and is_admin(uid):
            admin_panel(call)
        elif data == "lock_bot" and is_admin(uid):
            new = toggle_bot_lock()
            st = "مغلق 🔒" if new else "مفتوح 🔓"
            bot.answer_callback_query(call.id, f"✅ البوت {st}")
            admin_panel(call)
        elif data == "adm_users" and is_admin(uid):
            users_panel(call)
        elif data.startswith("userpage_"):
            page = int(data.split("_")[1])
            users_panel(call, page)
        elif data.startswith("uctrl_") and is_admin(uid):
            user_panel(call, data.split("_")[1])
        elif data.startswith("ban_") and is_admin(uid):
            ban_toggle(call, data.split("_")[1])
        elif data.startswith("pro_") and is_admin(uid):
            tuid = data.split("_")[1]
            if is_user_pro(int(tuid)):
                pro_remove(call, tuid)
            else:
                try:
                    bot.delete_message(cid, call.message.message_id)
                except:
                    pass
                m = bot.send_message(cid, deco("💎 منح VIP", "أرسل عدد الأيام (0 = دائم):"), reply_markup=cancel_kb("cancel_admin"))
                save_message(cid, m.message_id)
                bot.register_next_step_handler(m, pro_grant_step, tuid, m.message_id)
        elif data.startswith("charge_") and is_admin(uid):
            tuid = data.split("_")[1]
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("💰 شحن", f"أرسل عدد النقاط لـ <code>{tuid}</code>:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, charge_step, tuid, m.message_id)
        elif data.startswith("msguser_") and is_admin(uid):
            tuid = data.split("_")[1]
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("💬 رسالة", f"اكتب رسالتك لـ <code>{tuid}</code>:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, msg_user_step, tuid, m.message_id)
        elif data == "adm_admins" and is_admin(uid):
            admins_panel(call)
        elif data == "add_admin" and is_main_admin(uid):
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("➕ إضافة أدمن", "أرسل آيدي المستخدم:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, add_admin_step, m.message_id)
        elif data == "add_admin" and not is_main_admin(uid):
            bot.answer_callback_query(call.id, "❌ فقط المالك الرئيسي!", show_alert=True)
        elif data.startswith("rmadmin_") and is_admin(uid):
            aid = int(data.split("_")[1])
            if aid == ADMIN_ID:
                bot.answer_callback_query(call.id, "❌ لا يمكن إزالة المالك!", show_alert=True)
            elif not is_main_admin(uid) and aid != uid:
                bot.answer_callback_query(call.id, "❌ فقط المالك يمكنه!", show_alert=True)
            elif remove_admin(aid):
                bot.answer_callback_query(call.id, "✅ تم إزالة الأدمن")
                admins_panel(call)
            else:
                bot.answer_callback_query(call.id, "❌ فشل!", show_alert=True)

        elif data == "adm_pending" and is_admin(uid):
            pending_list(call)
        elif data.startswith("vpend_") and is_admin(uid):
            pending_view(call, data.split("_")[1])
        elif data.startswith("approve_") and is_admin(uid):
            approve_file(call, data.split("_")[1])
        elif data.startswith("reject_") and is_admin(uid):
            reject_file(call, data.split("_")[1])
        elif data == "adm_broadcast" and is_admin(uid):
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("📢 إذاعة", "أرسل رسالتك:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, broadcast_step, m.message_id)
        elif data == "adm_settings" and is_admin(uid):
            settings_panel(call)
        elif data == "adm_channels" and is_admin(uid):
            channels_panel(call)
        elif data == "add_channel" and is_admin(uid):
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("📢 إضافة قناة", "أرسل معرف القناة (@...):"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, add_channel_step, m.message_id)
        elif data.startswith("delch_") and is_admin(uid):
            del_channel(call, int(data.split("_")[1]))
        elif data == "set_img" and is_admin(uid):
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("🖼 صورة البوت", "أرسل الصورة:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, img_step, m.message_id)
        elif data == "rm_img" and is_admin(uid):
            settings = read_json(SETTINGS_DB)
            settings['bot_image'] = None
            save_settings(settings)
            bot.answer_callback_query(call.id, "✅ تم إزالة الصورة")
            settings_panel(call)
        elif data == "set_thumb" and is_admin(uid):
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("🎨 أيقونة الملفات", "أرسل الصورة:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, thumb_step, m.message_id)
        elif data == "rm_thumb" and is_admin(uid):
            settings = read_json(SETTINGS_DB)
            if settings.get('file_thumb') and os.path.exists(settings.get('file_thumb', '')):
                try:
                    os.remove(settings['file_thumb'])
                except:
                    pass
            settings['file_thumb'] = None
            save_settings(settings)
            bot.answer_callback_query(call.id, "✅ تم إزالة الأيقونة")
            settings_panel(call)
        elif data == "set_name" and is_admin(uid):
            try:
                bot.delete_message(cid, call.message.message_id)
            except:
                pass
            m = bot.send_message(cid, deco("✏️ اسم البوت", "أرسل الاسم:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, name_step, m.message_id)
        elif data == "stop_all" and is_admin(uid):
            stop_all_scripts()
            bot.answer_callback_query(call.id, "✅ تم إيقاف جميع البوتات")
            admin_panel(call)
        elif data == "toggle_auto":
            if not is_admin(uid):
                logger.warning(f"محاولة تغيير الموافقة التلقائية من مستخدم غير مخوّل: {uid}")
                bot.answer_callback_query(
                    call.id,
                    f"❌ أنت لست أدمن (معرفك: {uid})\nالمعرف المسجل في الكود: {ADMIN_ID}",
                    show_alert=True
                )
                return
            new = toggle_auto_approve()
            st = "مفعّل ✅" if new else "معطّل ❌"
            bot.answer_callback_query(call.id, f"✅ الموافقة التلقائية {st}")
            settings_panel(call)
        elif data == "adm_files" and is_admin(uid):
            all_files_panel(call)
        elif data.startswith("afpage_"):
            page = int(data.split("_")[1])
            all_files_panel(call, page)
        elif data.startswith("afile_"):
            fid = data.split("_")[1]
            file_panel_admin(call, fid)
        elif data == "download_all_files" and is_admin(uid):
            all_files = read_json(FILES_DB)
            decrypted_files = []
            for fid in all_files.keys():
                if verify_file_access(fid, ADMIN_ID):
                    content = load_encrypted_file(fid)
                    if content:
                        temp_path = os.path.join(BASE_DIR, f"temp_{fid}_{gen_id(4)}.py")
                        with open(temp_path, 'w', encoding='utf-8') as f:
                            f.write(content)
                        decrypted_files.append(temp_path)
            if decrypted_files:
                zip_name = f"all_files_{gen_id(4)}.zip"
                zip_path = create_zip(decrypted_files, zip_name)
                try:
                    with open(zip_path, 'rb') as f:
                        bot.send_document(cid, f, caption="📦 جميع ملفات البوت")
                    for temp_file in decrypted_files:
                        try:
                            os.remove(temp_file)
                        except:
                            pass
                    os.remove(zip_path)
                except:
                    bot.answer_callback_q
