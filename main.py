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

# ================== تشغيل خادم ويب مدمج لفتح البورت ==================
def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ================== إعداد التسجيل ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ================== المتغيرات الأساسية ==================
TOKEN = os.environ.get("BOT_TOKEN", "8955451883:AAFglAXAZ4o80wt2ZvK1l8lrZLi7nw16cVs")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 8105998916))

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
process_start_times = {}

MAX_FILES_PER_USER = 10

# ================== دوال قراءة وكتابة البيانات الآمنة ==================
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
            tmp_path = path + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            os.replace(tmp_path, path)
        except Exception as e:
            logger.error(f"خطأ في كتابة {path}: {e}")

def save_settings(data):
    write_json(SETTINGS_DB, data)

def deco(title, content):
    settings = read_json(SETTINGS_DB)
    name = settings.get('bot_name', 'Div: @scofr')
    return f"<b>{title}</b>\n\n{content}\n\n<b>{name}</b>"

# ================== دوال التشفير والحماية ==================
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

# ================== دوال المساعدة للتحقق والأذونات ==================
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

def get_admins():
    admins_data = read_json(ADMINS_DB)
    return admins_data.get("admins", [ADMIN_ID])

def add_admin(user_id):
    admins_data = read_json(ADMINS_DB)
    admins = admins_data.get("admins", [])
    if user_id not in admins:
        admins.append(user_id)
        admins_data["admins"] = admins
        write_json(ADMINS_DB, admins_data)
        return True
    return False

def remove_admin(user_id):
    if user_id == ADMIN_ID:
        return False
    admins_data = read_json(ADMINS_DB)
    admins = admins_data.get("admins", [])
    if user_id in admins:
        admins.remove(user_id)
        admins_data["admins"] = admins
        write_json(ADMINS_DB, admins_data)
        return True
    return False

def is_user_pro(uid):
    if uid == ADMIN_ID or is_admin(uid):
        return True
    users = read_json(USERS_DB)
    u = users.get(str(uid), {})
    expiry = u.get('expiry')
    if not expiry:
        return False
    if expiry in ['LIFETIME', 0]:
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
    for ch in channels:
        try:
            member = bot.get_chat_member(ch["username"], user_id)
            if member.status in ['left', 'kicked']:
                return False
        except:
            continue
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
        res = requests.get(url, timeout=10).json()
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

def locked_msg(chat_id):
    text = "🔒 <b>البوت مغلق حالياً</b>\n\nتم إيقاف الخدمة مؤقتاً\n\nيمكنك التواصل عبر الزر أدناه."
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("👨‍💻 تواصل مع المطور", url=f"tg://user?id={ADMIN_ID}"))
    send_msg(chat_id, deco("🔒 البوت مغلق", text), markup)

# ================== تشغيل وإيقاف السكربتات الفرعية بآمان ==================
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
        
        # حماية بيئة التشغيل من استخراج التوكن الخاص بالبوت الرئيسي
        clean_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": env_dir,
            "HOME": env_dir,
            "TMPDIR": env_dir
        }
        
        proc = subprocess.Popen(
            [sys.executable, "-u", env_file_path],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.PIPE,
            cwd=env_dir,
            start_new_session=True if hasattr(os, 'setsid') else False,
            env=clean_env
        )
        active_processes[fid] = proc
        process_start_times[fid] = time.time()
        return True
    except Exception as e:
        logger.error(f"Failed to start script {fid}: {e}")
        return False

def stop_script(fid):
    if fid in active_processes:
        proc = active_processes[fid]
        try:
            if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                os.killpg(os.getpgid(proc.pid), 9)
            else:
                proc.kill()
        except:
            try:
                proc.kill()
            except:
                pass
        del active_processes[fid]
        if fid in process_start_times:
            del process_start_times[fid]
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
                proc.stdin.write((cmd + '\n').encode('utf-8'))
                proc.stdin.flush()
                return True
        except Exception as e:
            logger.error(f"Proc write error: {e}")
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

# ================== خيط الخلفية لخصم النقاط ومراقبة التشغيل ==================
def billing_and_monitor_worker():
    while True:
        try:
            time.sleep(60)
            now = time.time()
            files = read_json(FILES_DB)
            users = read_json(USERS_DB)
            
            for fid in list(active_processes.keys()):
                proc = active_processes[fid]
                if proc.poll() is not None:
                    # السكربت توقف تلقائياً
                    stop_script(fid)
                    continue

                if fid not in files:
                    stop_script(fid)
                    continue

                finfo = files[fid]
                uid = finfo.get('user_id')

                # تخطي الخصم للمشتركين ذوي العضوية الممتازة VIP أو الأدمن
                if is_user_pro(uid):
                    continue

                start_t = process_start_times.get(fid, now)
                # إذا مرت ساعة من التشغيل
                if now - start_t >= 3600:
                    process_start_times[fid] = now
                    u = users.get(str(uid), {})
                    pts = u.get('points', 0)
                    if pts >= 1:
                        u['points'] = pts - 1
                        users[str(uid)] = u
                        write_json(USERS_DB, users)
                    else:
                        # النقاط انتهت، يتم إيقاف السكربت
                        stop_script(fid)
                        try:
                            bot.send_message(uid, deco("⚠️ توقف السكربت", f"تم إيقاف تشغيل الملف <code>{finfo.get('file_name')}</code> لنفاذ نقاطك."))
                        except:
                            pass
        except Exception as e:
            logger.error(f"Billing worker error: {e}")

threading.Thread(target=billing_and_monitor_worker, daemon=True).start()

# ================== دوال إرسال الرسائل واختصار الواجهات ==================
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
        send_msg(call.message.chat.id, text, markup)

# ================== لوحات الأزرار القوائم ==================
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
        kb.row(types.InlineKeyboardButton("🔧 لوحة VIP", callback_data="nav_pro"))
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

# ================== معالجات الأوامر ==================
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
            if len(msg.text.split()) > 1:
                ref = msg.text.split()[1]
                if ref.isdigit() and int(ref) != uid:
                    udb = read_json(USERS_DB)
                    if str(ref) in udb:
                        udb[str(ref)]['points'] = udb[str(ref)].get('points', 0) + 10
                        write_json(USERS_DB, udb)
                        try:
                            bot.send_message(int(ref), deco("🎁 مكافأة", "حصلت على 10 نقاط لإحالة شخص جديد!"))
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
        
        if users.get(str(uid), {}).get('is_banned', 0) == 1:
            return bot.send_message(msg.chat.id, deco("🚫 محظور", "تم حظرك من استخدام البوت."))
        
        if not check_sub(uid):
            return sub_msg(msg.chat.id)
        
        u = users.get(str(uid), {})
        vip = is_user_pro(uid)
        
        welcome_text = (
            f"✨ <b>مرحباً بك {escape(msg.from_user.first_name or 'عزيزي')}</b> ✨\n\n"
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
    kb.add(types.InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_sub"))
    text = "🔔 <b>اشتراك إجباري</b>\n\nيرجى الاشتراك في القنوات التالية لتتمكن من استخدام البوت:"
    send_msg(chat_id, deco("🔔 اشتراك مطلوب", text), kb)

# ================== معالج الاستدعاءات Callback Queries ==================
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
                bot.answer_callback_query(call.id, "✅ تم التحقق بنجاح!")
                u = users.get(str(uid), {})
                vip = is_user_pro(uid)
                text = f"💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n💰 نقاطك: <code>{u.get('points', 0)}</code>"
                edit_msg(call, deco("🏠 القائمة الرئيسية", text), main_kb(uid))
            else:
                bot.answer_callback_query(call.id, "❌ لم تشترك في جميع القنوات بعد!", show_alert=True)
            return

        if not check_sub(uid) and not is_admin(uid):
            bot.answer_callback_query(call.id, "❌ اشترك أولاً بالقنوات!", show_alert=True)
            return

        clear_cancel(uid)

        if data == "nav_main":
            u = users.get(str(uid), {})
            vip = is_user_pro(uid)
            text = f"💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n💰 نقاطك: <code>{u.get('points', 0)}</code>"
            edit_msg(call, deco("🏠 القائمة الرئيسية", text), main_kb(uid))

        elif data == "nav_pro":
            if not is_user_pro(uid):
                bot.answer_callback_query(call.id, "❌ هذه الميزة للمشتركين VIP فقط!", show_alert=True)
                return
            text = "🔧 <b>لوحة VIP المميزة</b>\n\nاستمتع بالمزايا الحصرية المخصصة لك."
            edit_msg(call, deco("🔧 لوحة Pro", text), pro_panel_kb(uid))

        elif data == "pro_download_all":
            if not is_user_pro(uid):
                return bot.answer_callback_query(call.id, "❌ للمشتركين VIP فقط!", show_alert=True)
            files = read_json(FILES_DB)
            u_files = {fid: f for fid, f in files.items() if f.get('user_id') == uid and f.get('status') == 'active'}
            if not u_files:
                return bot.answer_callback_query(call.id, "📂 لا توجد ملفات لديك!", show_alert=True)
            
            decrypted_files = []
            for fid in u_files.keys():
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
                        os.remove(temp_file)
                    os.remove(zip_path)
                    bot.answer_callback_query(call.id, "✅ تم التصدير بنجاح")
                except Exception as e:
                    logger.error(f"ZIP error: {e}")
                    bot.answer_callback_query(call.id, "❌ فشل التصدير", show_alert=True)

        elif data == "pro_auto_fix":
            if not is_user_pro(uid):
                return bot.answer_callback_query(call.id, "❌ للمشتركين VIP فقط!", show_alert=True)
            m = bot.send_message(cid, deco("🔍 فحص تلقائي", "أرسل ملف .py لفحصه وتصحيح أخطاء Python 2 إلى Python 3:"), reply_markup=cancel_kb())
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, auto_fix_step, m.message_id)

        elif data == "nav_wallet":
            u = users.get(str(uid), {})
            vip = is_user_pro(uid)
            exp = "لا يوجد"
            if vip:
                e = u.get('expiry')
                exp = "دائم ♾" if e in ['LIFETIME', 0] else (e or "مفعل")
            today = str(datetime.now().date())
            can = u.get('last_daily') != today
            text = f"💰 رصيدك الحالي: <code>{u.get('points', 0)}</code> نقطة\n💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n⏰ صلاحية VIP: {exp}\n\n💡 ملاحظة: كل 1 نقطة تتكفل بتشغيل السكربت لمدة 1 ساعة."
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton(f"🎁 الهدية اليومية {'✅' if can else '❌'}", callback_data="daily"),
                types.InlineKeyboardButton("🔗 رابط الإحالة", callback_data="ref")
            )
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            edit_msg(call, deco("💼 محفظتي", text), kb)

        elif data == "daily":
            u = users.get(str(uid))
            today = str(datetime.now().date())
            if u.get('last_daily') == today:
                return bot.answer_callback_query(call.id, "❌ لقد حصلت على الهدية اليومية بالفعل!", show_alert=True)
            gift = random.randint(5, 15)
            u['points'] = u.get('points', 0) + gift
            u['last_daily'] = today
            users[str(uid)] = u
            write_json(USERS_DB, users)
            bot.answer_callback_query(call.id, f"🎁 حصلت على {gift} نقاط هادفة!", show_alert=True)
            vip = is_user_pro(uid)
            text = f"💰 رصيدك: <code>{u.get('points', 0)}</code>\n💎 الرتبة: {'VIP 👑' if vip else 'مجاني 🆓'}\n\n✅ تم إضافة {gift} نقاط لرفيدك!"
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🎁 الهدية اليومية ❌", callback_data="daily"),
                types.InlineKeyboardButton("🔗 رابط الإحالة", callback_data="ref")
            )
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            edit_msg(call, deco("💼 محفظتي", text), kb)

        elif data == "ref":
            info = bot.get_me()
            link = f"https://t.me/{info.username}?start={uid}"
            text = f"🔗 رابط الإحالة الخاص بك:\n<code>{link}</code>\n\n💰 ستحصل على 10 نقاط عند انضمام أي مستخدم جديد عبر هذا الرابط!"
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_wallet"))
            edit_msg(call, deco("🔗 رابط الإحالة", text), kb)

        elif data == "nav_help":
            help_text = (
                "📖 <b>دليل الاستخدام والتعليمات</b>\n\n"
                "🚀 <b>الاستضافة:</b>\n"
                "• يمكنك رفع أي ملف بترميز .py وتلقائياً سيتم تشغيله السريع.\n"
                "• يتم خصم 1 نقطة لكل ساعة تشغيل السكربت مجاناً.\n\n"
                "💰 <b>جمع النقاط:</b>\n"
                "• مكافأة يومية عشوائية.\n"
                "• رابط الإحالة يمنحك 10 نقاط لكل صديق.\n\n"
                "💎 <b>مزيا VIP:</b>\n"
                "• تشغيل دائم وبدون نقاط.\n"
                "• إمكانية تحميل وتصدير كافة أرقام الملفات بملف ZIP مضغوط."
            )
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("👨‍💻 التواصل مع الدعم", url=f"tg://user?id={ADMIN_ID}"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            edit_msg(call, deco("📖 التعليمات", help_text), kb)

        elif data == "nav_upload":
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🆓 مجانية (نقاط)", callback_data="up_free"),
                types.InlineKeyboardButton("💎 VIP (غير محدودة)", callback_data="up_pro")
            )
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            text = "📤 اختر نوع الاستضافة المطلوبة:"
            edit_msg(call, deco("📤 رفع ملف", text), kb)

        elif data.startswith("up_"):
            h_type = data.split("_")[1]
            if h_type == "pro" and not is_user_pro(uid):
                return bot.answer_callback_query(call.id, "❌ للمشتركين VIP فقط!", show_alert=True)
            if h_type == "free":
                u = users.get(str(uid), {})
                if u.get('points', 0) < 1:
                    return bot.answer_callback_query(call.id, "❌ لا تملك نقاطاً كافية!", show_alert=True)
            
            m = bot.send_message(cid, deco("📤 إرسال الملف", "📥 قم بإرسال ملف Python الخص بك الآن (.py):"), reply_markup=cancel_kb())
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, upload_step, h_type, m.message_id)

        elif data == "nav_files":
            files = read_json(FILES_DB)
            u_files = {fid: f for fid, f in files.items() if f.get('user_id') == uid and f.get('status') == 'active'}
            if not u_files:
                return bot.answer_callback_query(call.id, "📂 لا توجد ملفات مرفوعة!", show_alert=True)
            kb = types.InlineKeyboardMarkup(row_width=1)
            for fid, f in u_files.items():
                running = fid in active_processes and active_processes[fid].poll() is None
                icon = "🟢" if running else "🔴"
                ft = "💎" if f.get('type') == 'pro' else "🆓"
                kb.add(types.InlineKeyboardButton(f"{icon} {ft} {f.get('file_name', '?')[:25]}", callback_data=f"manage_{fid}"))
            
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            running_count = sum(1 for fid in u_files if fid in active_processes and active_processes[fid].poll() is None)
            text = f"📊 إجمالي الملفات: {len(u_files)}\n🟢 قيد التشغيل: {running_count}\n🔴 متوقفة: {len(u_files) - running_count}"
            edit_msg(call, deco("📁 ملفاتي", text), kb)

        elif data.startswith("manage_"):
            fid = data.split("_")[1]
            file_panel(call, fid)

        elif data.startswith("toggle_"):
            fid = data.split("_")[1]
            toggle_file(call, fid)

        elif data.startswith("delc_"):
            fid = data.split("_")[1]
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("✅ نعم، أحذف", callback_data=f"del_{fid}"),
                types.InlineKeyboardButton("❌ إلغاء", callback_data=f"manage_{fid}")
            )
            edit_msg(call, deco("🗑️ تأكيد الحذف", "هل أنت متأكد من حذف هذا الملف نهائياً؟"), kb)

        elif data.startswith("del_"):
            fid = data.split("_")[1]
            delete_file(call, fid)

        elif data.startswith("dl_"):
            fid = data.split("_")[1]
            download_file(call, fid)

        elif data.startswith("term_") or data.startswith("rterm_"):
            fid = data.split("_")[1]
            terminal(call, fid)

        elif data.startswith("inp_"):
            fid = data.split("_")[1]
            m = bot.send_message(cid, deco("⌨️ إدخال امر", "اكتب النص أو الأمر المُراد توجيهه إلى السكربت:"), reply_markup=cancel_kb())
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, input_step, fid, m.message_id)

        elif data.startswith("chtoken_"):
            fid = data.split("_")[1]
            m = bot.send_message(cid, deco("🔑 تغيير التوكن", "أرسل التوكن الجديد للتوكن المطلوب تبديله بالملف:"), reply_markup=cancel_kb())
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, token_step, fid, m.message_id)

        elif data == "nav_lib":
            m = bot.send_message(cid, deco("🛠 تثبيت مكتبة", "أرسل اسم المكتبة المراد تثبيتها من pip (مثال: requests):"), reply_markup=cancel_kb())
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, lib_step, m.message_id)

        elif data == "nav_stats":
            files = read_json(FILES_DB)
            u = users.get(str(uid), {})
            u_files = [f for f in files.values() if f.get('user_id') == uid and f.get('status') == 'active']
            running = sum(1 for fid, f in files.items() if f.get('user_id') == uid and fid in active_processes and active_processes[fid].poll() is None)
            vip = is_user_pro(uid)
            text = f"🆔 المعرف: <code>{uid}</code>\n🔗 اليوزر: @{u.get('username', 'لا يوجد')}\n📅 تاريخ الانضمام: {u.get('join_date', '?')}\n\n💎 حالة الحساب: {'VIP 👑' if vip else 'مجاني 🆓'}\n💰 رصيد النقاط: <code>{u.get('points', 0)}</code>\n\n📁 عدد الملفات: {len(u_files)}\n🟢 شغال حالياً: {running}"
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("💼 محفظتي", callback_data="nav_wallet"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_main"))
            edit_msg(call, deco("📊 حسابي", text), kb)

        # ================== لوحة الإدارة Administrative Callback Routes ==================
        elif data == "nav_admin" and is_admin(uid):
            admin_panel(call)

        elif data == "lock_bot" and is_admin(uid):
            st = "مغلق 🔒" if toggle_bot_lock() else "مفتوح 🔓"
            bot.answer_callback_query(call.id, f"✅ تم وضع حالة البوت: {st}")
            admin_panel(call)

        elif data == "toggle_auto" and is_admin(uid):
            st = "مفعّل ✅" if toggle_auto_approve() else "معطّل ❌"
            bot.answer_callback_query(call.id, f"✅ الموافقة التلقائية: {st}")
            settings_panel(call)

        elif data == "adm_users" and is_admin(uid):
            users_panel(call)

        elif data.startswith("userpage_") and is_admin(uid):
            users_panel(call, int(data.split("_")[1]))

        elif data.startswith("uctrl_") and is_admin(uid):
            user_panel(call, data.split("_")[1])

        elif data.startswith("ban_") and is_admin(uid):
            ban_toggle(call, data.split("_")[1])

        elif data.startswith("pro_") and is_admin(uid):
            tuid = data.split("_")[1]
            if is_user_pro(int(tuid)):
                pro_remove(call, tuid)
            else:
                m = bot.send_message(cid, deco("💎 منح VIP", "أرسل عدد الأيام لترقية الحساب (0 للترقية الدائمة):"), reply_markup=cancel_kb("cancel_admin"))
                save_message(cid, m.message_id)
                bot.register_next_step_handler(m, pro_grant_step, tuid, m.message_id)

        elif data.startswith("charge_") and is_admin(uid):
            tuid = data.split("_")[1]
            m = bot.send_message(cid, deco("💰 شحن رصيد", f"أرسل عدد النقاط لإضافتها للمستخدم <code>{tuid}</code>:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, charge_step, tuid, m.message_id)

        elif data.startswith("msguser_") and is_admin(uid):
            tuid = data.split("_")[1]
            m = bot.send_message(cid, deco("💬 رسالة خاصة", f"اكتب نص الرسالة للارسال إلى <code>{tuid}</code>:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, msg_user_step, tuid, m.message_id)

        elif data == "adm_admins" and is_admin(uid):
            admins_panel(call)

        elif data == "add_admin" and is_main_admin(uid):
            m = bot.send_message(cid, deco("➕ إضافة أدمن", "أرسل ID المستخدم لرفعه أدمن:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, add_admin_step, m.message_id)

        elif data.startswith("rmadmin_") and is_admin(uid):
            aid = int(data.split("_")[1])
            if remove_admin(aid):
                bot.answer_callback_query(call.id, "✅ تم الحذف من الأدمن")
                admins_panel(call)

        elif data == "adm_pending" and is_admin(uid):
            pending_list(call)

        elif data.startswith("vpend_") and is_admin(uid):
            pending_view(call, data.split("_")[1])

        elif data.startswith("approve_") and is_admin(uid):
            approve_file(call, data.split("_")[1])

        elif data.startswith("reject_") and is_admin(uid):
            reject_file(call, data.split("_")[1])

        elif data == "adm_broadcast" and is_admin(uid):
            m = bot.send_message(cid, deco("📢 إذاعة عامة", "أرسل نص الرسالة التي تريد إرسالها لجميع المشتركين:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, broadcast_step, m.message_id)

        elif data == "adm_settings" and is_admin(uid):
            settings_panel(call)

        elif data == "adm_channels" and is_admin(uid):
            channels_panel(call)

        elif data == "add_channel" and is_admin(uid):
            m = bot.send_message(cid, deco("📢 إضافة قناة", "أرسل معرف القناة الآن (مثل @MyChannel):"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, add_channel_step, m.message_id)

        elif data.startswith("delch_") and is_admin(uid):
            del_channel(call, int(data.split("_")[1]))

        elif data == "set_img" and is_admin(uid):
            m = bot.send_message(cid, deco("🖼 صورة البوت", "أرسل الصورة الجديدة لواجهة البوت:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, img_step, m.message_id)

        elif data == "set_name" and is_admin(uid):
            m = bot.send_message(cid, deco("✏️ اسم البوت", "أرسل التوقيع / الاسم الجديد للبوت:"), reply_markup=cancel_kb("cancel_admin"))
            save_message(cid, m.message_id)
            bot.register_next_step_handler(m, name_step, m.message_id)

        elif data == "stop_all" and is_admin(uid):
            stop_all_scripts()
            bot.answer_callback_query(call.id, "✅ تم إيقاف جميع السكربتات المشتغلة فوراً!")
            admin_panel(call)

        elif data == "adm_files" and is_admin(uid):
            all_files_panel(call)

        elif data.startswith("afpage_") and is_admin(uid):
            all_files_panel(call, int(data.split("_")[1]))

        elif data.startswith("afile_") and is_admin(uid):
            file_panel_admin(call, data.split("_")[1])

        elif data == "download_all_files" and is_admin(uid):
            all_files = read_json(FILES_DB)
            decrypted_files = []
            for fid in all_files.keys():
                content = load_encrypted_file(fid)
                if content:
                    temp_path = os.path.join(BASE_DIR, f"temp_{fid}_{gen_id(4)}.py")
                    with open(temp_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    decrypted_files.append(temp_path)
            if decrypted_files:
                zip_name = f"all_files_{gen_id(4)}.zip"
                zip_path = create_zip(decrypted_files, zip_name)
                with open(zip_path, 'rb') as f:
                    bot.send_document(cid, f, caption="📦 جميع السكربتات المحفوظة بالنظام")
                for temp_file in decrypted_files:
                    os.remove(temp_file)
                os.remove(zip_path)
                bot.answer_callback_query(call.id, "✅ تم التحميل")
            else:
                bot.answer_callback_query(call.id, "❌ لا يوجد ملفات", show_alert=True)

    except Exception as e:
        logger.error(f"Callback Error: {e}")

# ================== دوال معالجة الخطوات التفصيلية (Step Handlers) ==================
def upload_step(msg, h_type, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    uid = msg.from_user.id
    if is_cancelled(uid):
        return

    if not msg.document or not msg.document.file_name.endswith('.py'):
        return send_msg(msg.chat.id, deco("❌ خطأ", "يرجى إرسال ملف بصيغة .py فقط!"), main_kb(uid))

    try:
        file_info = bot.get_file(msg.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        content = downloaded.decode('utf-8', errors='ignore')

        fid = gen_id(8)
        save_encrypted_file(fid, content, uid)

        settings = read_json(SETTINGS_DB)
        auto = settings.get('auto_approve', True)
        status = 'active' if auto else 'pending'

        files = read_json(FILES_DB)
        files[fid] = {
            'fid': fid,
            'user_id': uid,
            'file_name': msg.document.file_name,
            'upload_date': str(datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            'type': h_type,
            'status': status
        }
        write_json(FILES_DB, files)

        if auto:
            start_script(fid)
            send_msg(msg.chat.id, deco("✅ تم الرفع والتشغيل", f"تم قبول وتشغيل السكربت تلقائياً!\n\n🆔 معرف الملف: <code>{fid}</code>"), main_kb(uid))
        else:
            send_msg(msg.chat.id, deco("⏳ بانتظار الموافقة", f"تم رفع السكربت وهو الآن بانتظار مراجعة الأدمن.\n\n🆔 معرف الملف: <code>{fid}</code>"), main_kb(uid))
            # إشعار الأدمن
            for aid in get_admins():
                try:
                    kb = types.InlineKeyboardMarkup()
                    kb.add(types.InlineKeyboardButton("🔍 معاينة الطلب", callback_data=f"vpend_{fid}"))
                    bot.send_message(aid, deco("📥 طلب رفع ملف جديد", f"قام المستخدم <code>{uid}</code> برفع ملف: {msg.document.file_name}"), reply_markup=kb)
                except:
                    pass
    except Exception as e:
        logger.error(f"Upload Step Error: {e}")
        send_msg(msg.chat.id, deco("❌ خطأ", "حدث خطأ أثناء معالجة الملف."), main_kb(uid))

def auto_fix_step(msg, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    uid = msg.from_user.id
    if is_cancelled(uid):
        return
    if not msg.document or not msg.document.file_name.endswith('.py'):
        return send_msg(msg.chat.id, deco("❌ خطأ", "يرجى إرسال ملف Python فقط!"), main_kb(uid))

    try:
        file_info = bot.get_file(msg.document.file_id)
        downloaded = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')
        fixed_code = auto_fix_errors(downloaded)

        temp_fixed = os.path.join(BASE_DIR, f"fixed_{msg.document.file_name}")
        with open(temp_fixed, 'w', encoding='utf-8') as f:
            f.write(fixed_code)

        with open(temp_fixed, 'rb') as f:
            bot.send_document(msg.chat.id, f, caption="✅ تم فحص الملف وتصحيحه بنجاح!")
        os.remove(temp_fixed)
    except Exception as e:
        logger.error(f"Auto fix error: {e}")
        send_msg(msg.chat.id, deco("❌ خطأ", "فشل معالجة وتصحيح الملف."), main_kb(uid))

def input_step(msg, fid, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    uid = msg.from_user.id
    if is_cancelled(uid):
        return
    cmd = msg.text
    if cmd and write_proc(fid, cmd):
        bot.send_message(msg.chat.id, deco("✅ تم الإرسال", "تم إرسال الإدخال إلى العملية المشتغلة بنجاح."))
    else:
        bot.send_message(msg.chat.id, deco("❌ خطأ", "العملية غير مشتغلة أو تعذر الإرسال."))

def token_step(msg, fid, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    uid = msg.from_user.id
    if is_cancelled(uid):
        return
    new_token = msg.text.strip()
    valid, res = check_token(new_token)
    if not valid:
        return send_msg(msg.chat.id, deco("❌ توكن غير صالح", f"التوكن المدخل غير صحيح:\n{res}"), main_kb(uid))

    content = load_encrypted_file(fid)
    if not content:
        return send_msg(msg.chat.id, deco("❌ خطأ", "الملف غير موجود."), main_kb(uid))

    temp_path = os.path.join(BASE_DIR, f"temp_tok_{fid}.py")
    with open(temp_path, 'w', encoding='utf-8') as f:
        f.write(content)

    if update_token(temp_path, new_token):
        with open(temp_path, 'r', encoding='utf-8') as f:
            new_content = f.read()
        save_encrypted_file(fid, new_content, uid)
        os.remove(temp_path)
        
        # إعادة التشغيل
        stop_script(fid)
        start_script(fid)
        send_msg(msg.chat.id, deco("✅ تم التحديث", "تم تغيير التوكن بالملف وإعادة تشغيله بنجاح!"), main_kb(uid))
    else:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        send_msg(msg.chat.id, deco("❌ خطأ", "لم يتم العثور على صيغة توكن معتمدة للتعديل بالملف."), main_kb(uid))

def lib_step(msg, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    uid = msg.from_user.id
    if is_cancelled(uid):
        return
    pkg = msg.text.strip()
    
    # حماية من Injection عبر أداة PIP
    if not re.match(r'^[a-zA-Z0-9_\-\.>=<]+$', pkg):
        return send_msg(msg.chat.id, deco("❌ اسم غير آمن", "اسم المكتبة يحتوي على رموز غير مسموح بها!"), main_kb(uid))

    msg_wait = bot.send_message(msg.chat.id, deco("⏳ جاري التثبيت", f"جاري تثبيت المكتبة <code>{pkg}</code>..."))
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        bot.edit_message_text(deco("✅ تم التثبيت", f"تم تثبيت المكتبة <code>{pkg}</code> بنجاح!"), msg.chat.id, msg_wait.message_id)
    except Exception as e:
        bot.edit_message_text(deco("❌ فشل التثبيت", f"تعذر تثبيت المكتبة:\n<code>{e}</code>"), msg.chat.id, msg_wait.message_id)

def charge_step(msg, tuid, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    if is_cancelled(msg.from_user.id):
        return
    if not msg.text.isdigit():
        return bot.send_message(msg.chat.id, "❌ ادخل رقماً صحيحاً!")
    pts = int(msg.text)
    users = read_json(USERS_DB)
    if tuid in users:
        users[tuid]['points'] = users[tuid].get('points', 0) + pts
        write_json(USERS_DB, users)
        bot.send_message(msg.chat.id, deco("✅ تم الشحن", f"تم إضافة {pts} نقطة للمستخدم <code>{tuid}</code>"))
        try:
            bot.send_message(int(tuid), deco("💰 شحن رصيد", f"تم إضافة {pts} نقاط لحسابك من قبل الإدارة!"))
        except:
            pass

def pro_grant_step(msg, tuid, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    if is_cancelled(msg.from_user.id):
        return
    if not msg.text.isdigit():
        return bot.send_message(msg.chat.id, "❌ أدخل رقماً صحيحاً!")
    days = int(msg.text)
    users = read_json(USERS_DB)
    if tuid in users:
        if days == 0:
            exp = "LIFETIME"
        else:
            exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        users[tuid]['expiry'] = exp
        write_json(USERS_DB, users)
        bot.send_message(msg.chat.id, deco("✅ تم المنح", f"تم تفعيل VIP للمستخدم <code>{tuid}</code> (المدة: {days} يوم)"))

def msg_user_step(msg, tuid, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    if is_cancelled(msg.from_user.id):
        return
    try:
        bot.send_message(int(tuid), deco("💬 رسالة من الإدارة", msg.text))
        bot.send_message(msg.chat.id, deco("✅ تم الإرسال", "تم توجيه الرسالة بنجاح."))
    except Exception as e:
        bot.send_message(msg.chat.id, deco("❌ فشل الإرسال", str(e)))

def broadcast_step(msg, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    if is_cancelled(msg.from_user.id):
        return
    users = read_json(USERS_DB)
    sent, failed = 0, 0
    m = bot.send_message(msg.chat.id, "⏳ جاري إرسال الإذاعة...")
    for u in users.keys():
        try:
            bot.send_message(int(u), deco("📢 إذاعة العامة", msg.text))
            sent += 1
            time.sleep(0.05)
        except:
            failed += 1
    bot.edit_message_text(deco("✅ اكتملت الإذاعة", f"تم الإرسال لـ {sent} مستخدم.\nفشل الإرسال لـ {failed}"), msg.chat.id, m.message_id)

def add_admin_step(msg, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    if is_cancelled(msg.from_user.id):
        return
    if msg.text.isdigit() and add_admin(int(msg.text)):
        bot.send_message(msg.chat.id, deco("✅ تم", f"تمت إضافة <code>{msg.text}</code> قائمة الأدمن."))
    else:
        bot.send_message(msg.chat.id, deco("❌ خطأ", "الآيدي غير صحيح أو مضاف سابقاً."))

def add_channel_step(msg, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    if is_cancelled(msg.from_user.id):
        return
    ch = msg.text.strip()
    if not ch.startswith("@"):
        ch = "@" + ch
    try:
        chat = bot.get_chat(ch)
        settings = read_json(SETTINGS_DB)
        channels = settings.get('channels', [])
        channels.append({'name': chat.title, 'username': ch})
        settings['channels'] = channels
        save_settings(settings)
        bot.send_message(msg.chat.id, deco("✅ تم الإضافة", f"تم إضافة القناة {chat.title} بنجاح."))
    except Exception as e:
        bot.send_message(msg.chat.id, deco("❌ خطأ", f"لم يتم العثور على القناة أو البوت ليس مشرفاً بها.\n{e}"))

def img_step(msg, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    if is_cancelled(msg.from_user.id):
        return
    if msg.photo:
        file_id = msg.photo[-1].file_id
        settings = read_json(SETTINGS_DB)
        settings['bot_image'] = file_id
        save_settings(settings)
        bot.send_message(msg.chat.id, deco("✅ تم الحفظ", "تم تعيين الصورة الرئيسية بنجاح."))
    else:
        bot.send_message(msg.chat.id, deco("❌ خطأ", "لم تقم ببدء صورة válida!"))

def name_step(msg, prompt_id):
    del_msg(msg.chat.id, prompt_id)
    if is_cancelled(msg.from_user.id):
        return
    settings = read_json(SETTINGS_DB)
    settings['bot_name'] = msg.text.strip()
    save_settings(settings)
    bot.send_message(msg.chat.id, deco("✅ تم الحفظ", f"تم تعديل التوقيع لـ: {msg.text}"))

# ================== لوحات إدارة وحكم الملفات File Management Views ==================
def file_panel(call, fid):
    files = read_json(FILES_DB)
    if fid not in files:
        return bot.answer_callback_query(call.id, "❌ الملف غير موجود!", show_alert=True)
    f = files[fid]
    running = fid in active_processes and active_processes[fid].poll() is None
    status_str = "🟢 يعمل" if running else "🔴 متوقف"
    
    text = (
        f"📄 <b>اسم الملف:</b> <code>{escape(f.get('file_name'))}</code>\n"
        f"🆔 <b>معرف السكربت:</b> <code>{fid}</code>\n"
        f"📊 <b>الحالة:</b> {status_str}\n"
        f"📅 <b>تاريخ الرفع:</b> {f.get('upload_date')}\n"
        f"💎 <b>النوع:</b> {f.get('type')}\n\n"
        f"📋 <b>أخر المخرجات (Logs):</b>\n{get_logs(fid, 10)}"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔴 إيقاف" if running else "🟢 تشغيل", callback_data=f"toggle_{fid}"),
        types.InlineKeyboardButton("📥 تحميل", callback_data=f"dl_{fid}")
    )
    kb.add(
        types.InlineKeyboardButton("🖥 المخرجات", callback_data=f"term_{fid}"),
        types.InlineKeyboardButton("⌨️ إرسال أمر", callback_data=f"inp_{fid}")
    )
    kb.add(types.InlineKeyboardButton("🔑 تغيير التوكن", callback_data=f"chtoken_{fid}"))
    kb.add(
        types.InlineKeyboardButton("🗑️ حذف الملف", callback_data=f"delc_{fid}"),
        types.InlineKeyboardButton("🔙 رجوع", callback_data="nav_files")
    )
    edit_msg(call, deco("📁 إدارة الملف", text), kb)

def toggle_file(call, fid):
    if fid in active_processes and active_processes[fid].poll() is None:
        stop_script(fid)
        bot.answer_callback_query(call.id, "🔴 تم إيقاف السكربت")
    else:
        if start_script(fid):
            bot.answer_callback_query(call.id, "🟢 تم تشغيل السكربت")
        else:
            bot.answer_callback_query(call.id, "❌ فشل التشغيل!", show_alert=True)
    file_panel(call, fid)

def delete_file(call, fid):
    stop_script(fid)
    files = read_json(FILES_DB)
    if fid in files:
        del files[fid]
        write_json(FILES_DB, files)
    
    enc_path = os.path.join(ENCRYPTED_DIR, f"{fid}.enc")
    log_path = os.path.join(LOGS_DIR, f"{fid}.log")
    for p in [enc_path, log_path]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except:
                pass
    bot.answer_callback_query(call.id, "🗑️ تم حذف الملف بنجاح")
    
    # العودة لملفاتي
    u = read_json(USERS_DB).get(str(call.from_user.id), {})
    text = f"💎 الرتبة: {'VIP 👑' if is_user_pro(call.from_user.id) else 'مجاني 🆓'}\n💰 نقاطك: <code>{u.get('points', 0)}</code>"
    edit_msg(call, deco("🏠 القائمة الرئيسية", text), main_kb(call.from_user.id))

def download_file(call, fid):
    content = load_encrypted_file(fid)
    if not content:
        return bot.answer_callback_query(call.id, "❌ تعذر فك تشفير الملف", show_alert=True)
    files = read_json(FILES_DB)
    fname = files.get(fid, {}).get('file_name', f"{fid}.py")
    temp_path = os.path.join(BASE_DIR, f"dl_{fid}_{fname}")
    with open(temp_path, 'w', encoding='utf-8') as f:
        f.write(content)
    with open(temp_path, 'rb') as f:
        bot.send_document(call.message.chat.id, f)
    os.remove(temp_path)
    bot.answer_callback_query(call.id, "✅ تم التحميل")

def terminal(call, fid):
    text = f"🖥 <b>سجل الشاشة والمخرجات (Terminal):</b>\n\n{get_logs(fid, 30)}"
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("🔄 تحديث", callback_data=f"rterm_{fid}"),
        types.InlineKeyboardButton("🔙 رجوع", callback_data=f"manage_{fid}")
    )
    edit_msg(call, deco("🖥 المخرجات الحية", text), kb)

# ================== لوحات الإدارة الأدمن (Admin Panels) ==================
def admin_panel(call):
    users = read_json(USERS_DB)
    files = read_json(FILES_DB)
    pending = sum(1 for f in files.values() if f.get('status') == 'pending')
    locked = is_bot_locked()
    
    text = (
        f"⚙️ <b>لوحة التحكم والإدارة الرئيسية</b>\n\n"
        f"👥 <b>المستخدمين:</b> {len(users)}\n"
        f"📁 <b>الملفات:</b> {len(files)}\n"
        f"⏳ <b>طلبات الانتظار:</b> {pending}\n"
        f"🟢 <b>العمليات النشطة:</b> {len(active_processes)}\n"
        f"🔒 <b>حالة البوت:</b> {'مغلق 🔒' if locked else 'مفتوح 🔓'}"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="adm_users"),
        types.InlineKeyboardButton("📁 جميع الملفات", callback_data="adm_files")
    )
    kb.add(
        types.InlineKeyboardButton(f"⏳ الطلبات المعلقة ({pending})", callback_data="adm_pending"),
        types.InlineKeyboardButton("📢 إذاعة عامة", callback_data="adm_broadcast")
    )
    kb.add(
        types.InlineKeyboardButton("👮‍♂️ فريق الأدمن", callback_data="adm_admins"),
        types.InlineKeyboardButton("⚙️ إعدادات البوت", callback_data="adm_settings")
    )
    kb.add(
        types.InlineKeyboardButton("🛑 إيقاف كافة البوتات", callback_data="stop_all"),
        types.InlineKeyboardButton("🔒 قفل / فتح البوت", callback_data="lock_bot")
    )
    kb.add(types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="nav_main"))
    edit_msg(call, deco("⚙️ الإدارة", text), kb)

def users_panel(call, page=1):
    users = read_json(USERS_DB)
    u_list = list(users.items())
    per_page = 8
    total_pages = (len(u_list) + per_page - 1) // per_page or 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    for uid, u in u_list[start:end]:
        name = u.get('first_name', 'مستخدم')
        vip = "👑" if is_user_pro(int(uid)) else "🆓"
        kb.add(types.InlineKeyboardButton(f"{vip} {name} ({uid})", callback_data=f"uctrl_{uid}"))
        
    nav_btns = []
    if page > 1:
        nav_btns.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"userpage_{page-1}"))
    if page < total_pages:
        nav_btns.append(types.InlineKeyboardButton("التالي ➡️", callback_data=f"userpage_{page+1}"))
    if nav_btns:
        kb.row(*nav_btns)
    kb.add(types.InlineKeyboardButton("🔙 رجوع للإدارة", callback_data="nav_admin"))
    
    text = f"👥 <b>إدارة المستخدمين</b> (صفحة {page}/{total_pages})\nإجمالي المسجلين: {len(users)}"
    edit_msg(call, deco("👥 المستخدمين", text), kb)

def user_panel(call, tuid):
    users = read_json(USERS_DB)
    u = users.get(str(tuid))
    if not u:
        return bot.answer_callback_query(call.id, "❌ المستخدم غير موجود!", show_alert=True)
    vip = is_user_pro(int(tuid))
    banned = u.get('is_banned', 0) == 1
    
    text = (
        f"🧑‍💻 <b>تفاصيل المستخدم:</b> <code>{tuid}</code>\n"
        f"👤 <b>الاسم:</b> {escape(u.get('first_name', ''))}\n"
        f"🔗 <b>المعرف:</b> @{u.get('username', 'لا يوجد')}\n"
        f"💰 <b>النقاط:</b> {u.get('points', 0)}\n"
        f"💎 <b>حالة VIP:</b> {'نعم 👑' if vip else 'لا 🆓'}\n"
        f"🚫 <b>الحظر:</b> {'محظور 🔴' if banned else 'سليم 🟢'}"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💰 شحن نقاط", callback_data=f"charge_{tuid}"),
        types.InlineKeyboardButton("💎 سحب/منح VIP", callback_data=f"pro_{tuid}")
    )
    kb.add(
        types.InlineKeyboardButton("💬 مراسلة", callback_data=f"msguser_{tuid}"),
        types.InlineKeyboardButton("🚫 حظر/إلغاء", callback_data=f"ban_{tuid}")
    )
    kb.add(types.InlineKeyboardButton("🔙 رجوع للمستخدمين", callback_data="adm_users"))
    edit_msg(call, deco("👤 التحكم بالمستخدم", text), kb)

def ban_toggle(call, tuid):
    users = read_json(USERS_DB)
    if tuid in users:
        curr = users[tuid].get('is_banned', 0)
        users[tuid]['is_banned'] = 0 if curr == 1 else 1
        write_json(USERS_DB, users)
        bot.answer_callback_query(call.id, "✅ تم تغيير حالة الحظر")
        user_panel(call, tuid)

def pro_remove(call, tuid):
    users = read_json(USERS_DB)
    if tuid in users:
        users[tuid]['expiry'] = None
        write_json(USERS_DB, users)
        bot.answer_callback_query(call.id, "✅ تم سحب VIP")
        user_panel(call, tuid)

def admins_panel(call):
    admins = get_admins()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for aid in admins:
        txt = f"👑 المالك ({aid})" if aid == ADMIN_ID else f"👮‍♂️ أدمن ({aid})"
        if is_main_admin(call.from_user.id) and aid != ADMIN_ID:
            kb.add(types.InlineKeyboardButton(f"❌ حذف {aid}", callback_data=f"rmadmin_{aid}"))
        else:
            kb.add(types.InlineKeyboardButton(txt, callback_data="none"))
    if is_main_admin(call.from_user.id):
        kb.add(types.InlineKeyboardButton("➕ إضافة أدمن جديد", callback_data="add_admin"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع للإدارة", callback_data="nav_admin"))
    edit_msg(call, deco("👮‍♂️ فريق الإدارة", "فريق المشرفين والمدراء المعتمدين بالبوت:"), kb)

def pending_list(call):
    files = read_json(FILES_DB)
    p_files = {fid: f for fid, f in files.items() if f.get('status') == 'pending'}
    if not p_files:
        return bot.answer_callback_query(call.id, "✨ لا توجد طلبات معلقة!", show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for fid, f in p_files.items():
        kb.add(types.InlineKeyboardButton(f"🔍 {f.get('file_name')} ({f.get('user_id')})", callback_data=f"vpend_{fid}"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع للإدارة", callback_data="nav_admin"))
    edit_msg(call, deco("⏳ الطلبات المعلقة", "اختر ملفاً لمعاينته والموافقة عليه:"), kb)

def pending_view(call, fid):
    files = read_json(FILES_DB)
    if fid not in files:
        return bot.answer_callback_query(call.id, "❌ الملف غير موجود", show_alert=True)
    f = files[fid]
    text = f"📄 <b>الملف:</b> {f.get('file_name')}\n👤 <b>المستخدم:</b> <code>{f.get('user_id')}</code>\n📅 <b>التاريخ:</b> {f.get('upload_date')}"
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ قبول وتشغيل", callback_data=f"approve_{fid}"),
        types.InlineKeyboardButton("❌ رفض وحذف", callback_data=f"reject_{fid}")
    )
    kb.add(types.InlineKeyboardButton("🔙 رجوع للطلبات", callback_data="adm_pending"))
    edit_msg(call, deco("🔍 معاينة الطلب", text), kb)

def approve_file(call, fid):
    files = read_json(FILES_DB)
    if fid in files:
        files[fid]['status'] = 'active'
        write_json(FILES_DB, files)
        start_script(fid)
        bot.answer_callback_query(call.id, "✅ تم القبول والتشغيل")
        try:
            bot.send_message(files[fid]['user_id'], deco("✅ تم القبول", f"تمت الموافقة على ملفك <code>{files[fid]['file_name']}</code> وتشغيله بنجاح!"))
        except:
            pass
    pending_list(call)

def reject_file(call, fid):
    files = read_json(FILES_DB)
    if fid in files:
        uid = files[fid]['user_id']
        fname = files[fid]['file_name']
        del files[fid]
        write_json(FILES_DB, files)
        bot.answer_callback_query(call.id, "❌ تم الرفض")
        try:
            bot.send_message(uid, deco("❌ تم الرفض", f"نأسف، تم رفض ملفك <code>{fname}</code> من قبل الإدارة."))
        except:
            pass
    pending_list(call)

def settings_panel(call):
    settings = read_json(SETTINGS_DB)
    auto = settings.get('auto_approve', True)
    has_img = "مفعلة ✅" if settings.get('bot_image') else "غير مفعلة ❌"
    
    text = (
        f"⚙️ <b>إعدادات البوت والخدمة</b>\n\n"
        f"⚡ <b>الموافقة التلقائية:</b> {'مفعلة ✅' if auto else 'معطلة ❌'}\n"
        f"🖼 <b>صورة البوت:</b> {has_img}\n"
        f"✏️ <b>التوقيع:</b> {settings.get('bot_name', 'Div: @scofr')}"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⚡ التوافقة التلقائية", callback_data="toggle_auto"),
        types.InlineKeyboardButton("📢 القنوات الإجبارية", callback_data="adm_channels")
    )
    kb.add(
        types.InlineKeyboardButton("🖼 تغيير الصورة", callback_data="set_img"),
        types.InlineKeyboardButton("🗑️ حذف الصورة", callback_data="rm_img")
    )
    kb.add(
        types.InlineKeyboardButton("✏️ تغيير التوقيع", callback_data="set_name"),
        types.InlineKeyboardButton("🔙 رجوع للإدارة", callback_data="nav_admin")
    )
    edit_msg(call, deco("⚙️ الإعدادات", text), kb)

def channels_panel(call):
    settings = read_json(SETTINGS_DB)
    channels = settings.get('channels', [])
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, ch in enumerate(channels):
        kb.add(types.InlineKeyboardButton(f"❌ حذف {ch['name']}", callback_data=f"delch_{idx}"))
    kb.add(types.InlineKeyboardButton("➕ إضافة قناة جديدة", callback_data="add_channel"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع للإعدادات", callback_data="adm_settings"))
    edit_msg(call, deco("📢 القنوات الإجبارية", "قنوات الاشتراك الإجباري المطلوبة من المستخدمين:"), kb)

def del_channel(call, idx):
    settings = read_json(SETTINGS_DB)
    channels = settings.get('channels', [])
    if 0 <= idx < len(channels):
        channels.pop(idx)
        settings['channels'] = channels
        save_settings(settings)
        bot.answer_callback_query(call.id, "✅ تم حذف القناة")
    channels_panel(call)

def all_files_panel(call, page=1):
    files = read_json(FILES_DB)
    f_list = list(files.items())
    per_page = 8
    total_pages = (len(f_list) + per_page - 1) // per_page or 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    for fid, f in f_list[start:end]:
        running = fid in active_processes and active_processes[fid].poll() is None
        icon = "🟢" if running else "🔴"
        kb.add(types.InlineKeyboardButton(f"{icon} {f.get('file_name')} | {f.get('user_id')}", callback_data=f"afile_{fid}"))
        
    nav_btns = []
    if page > 1:
        nav_btns.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"afpage_{page-1}"))
    if page < total_pages:
        nav_btns.append(types.InlineKeyboardButton("التالي ➡️", callback_data=f"afpage_{page+1}"))
    if nav_btns:
        kb.row(*nav_btns)
    kb.add(types.InlineKeyboardButton("📦 تحميل كل الملفات (ZIP)", callback_data="download_all_files"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع للإدارة", callback_data="nav_admin"))
    
    text = f"📁 <b>جميع السكربتات بجميع الحسابات</b> (صفحة {page}/{total_pages})\nالإجمالي: {len(files)}"
    edit_msg(call, deco("📁 كافة السكربتات", text), kb)

def file_panel_admin(call, fid):
    files = read_json(FILES_DB)
    if fid not in files:
        return bot.answer_callback_query(call.id, "❌ الملف غير موجود!", show_alert=True)
    f = files[fid]
    running = fid in active_processes and active_processes[fid].poll() is None
    
    text = (
        f"📄 <b>اسم الملف:</b> <code>{escape(f.get('file_name'))}</code>\n"
        f"🆔 <b>معرف السكربت:</b> <code>{fid}</code>\n"
        f"👤 <b>صاحب الملف:</b> <code>{f.get('user_id')}</code>\n"
        f"📊 <b>الحالة:</b> {'🟢 يعمل' if running else '🔴 متوقف'}\n\n"
        f"📋 <b>المخرجات:</b>\n{get_logs(fid, 10)}"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔴 إيقاف" if running else "🟢 تشغيل", callback_data=f"toggle_{fid}"),
        types.InlineKeyboardButton("📥 تحميل", callback_data=f"dl_{fid}")
    )
    kb.add(
        types.InlineKeyboardButton("🗑️ حذف الملف", callback_data=f"delc_{fid}"),
        types.InlineKeyboardButton("🔙 رجوع لكافة الملفات", callback_data="adm_files")
    )
    edit_msg(call, deco("📁 التحكم بالملف (أدمن)", text), kb)

# ================== بدء تشغيل البوت ==================
if __name__ == '__main__':
    logger.info("🚀 جاري بدء تشغيل البوت بنجاح...")
    bot.infinity_polling(skip_pending=True)
