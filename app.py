"""
Smart Laundry backend (Python Flask + SQLite + ESP32 Integration + SlipOK Verification + LINE OA Notification)

รัน:  pip install flask requests
      python app.py        ->  http://127.0.0.1:5000/index.html
"""
import json
import os
import re
import secrets
import sqlite3
import threading
import urllib.request
from datetime import datetime, timedelta
from functools import wraps

import requests
from flask import (Flask, flash, flash as flask_flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session)
from werkzeug.security import check_password_hash, generate_password_hash

# ============================================================
#  ตั้งค่าทั่วไป & LINE OA Configuration
# ============================================================
BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "smartlaundry.db")
UPLOAD_DIR = os.path.join(BASE, "uploads")          # รูปโปรไฟล์ที่ผู้ใช้อัปโหลด
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 🔑 [Channel Access Token จาก LINE Developers]
LINE_CHANNEL_ACCESS_TOKEN = "qCNm9TRkOKh2BR9H8DxZC0x4UxFo+pAl47zSksC/Yq5Twc1aUF2fvbfdhsUAczT+Ly94W4m0xK4spIKl9oUgyHz9kyAjBwXB5wF6D2UZEmCO/L7sLmn8PG/k87ijCFVcAaktTxfGLAKKc8MCr0O9CwdB04t89/1O/w1cDnyilFU="

# ตัวเก็บรหัส OTP ชั่วคราวสำหรับผูกบัญชี { "123456": {"user_id": 1, "expires_at": datetime} }
LINE_OTPS = {}

# 🔑 [ระบุรหัสจาก SlipOK ตรงนี้ครับ]
SLIPOK_URL = os.getenv("SLIPOK_URL", "https://api.slipok.com/api/line/apikey/79673")
SLIPOK_API_KEY = os.getenv("SLIPOK_API_KEY", "SLIPOKWLNGVRW")

RESERVE_MINUTES = 3           # ถือจองเครื่องไว้ 3 นาที (โหมดทดสอบ)
PAY_MINUTES = 3               # QR ชำระเงินหมดอายุ 3 นาที (โหมดทดสอบ)
DEMO_MODE = os.getenv("DEMO_MODE", "1") == "1"      # เปิดปุ่ม "จำลองจ่ายเงินสำเร็จ"
PROMPTPAY_ID = os.getenv("PROMPTPAY_ID", "0838903307")  # เบอร์/เลขบัตร ปชช. ผู้รับเงิน
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@smartlaundry.local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin1234")  # เปลี่ยนก่อนใช้งานจริง!


def _secret_key():
    if os.getenv("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(BASE, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    return open(path).read().strip()


app = Flask(__name__, static_folder=None, template_folder=BASE)
app.config.update(
    SECRET_KEY=_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,     # อัปโหลดไฟล์รูปไม่เกิน 5MB
)

THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
               "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]

# ============================================================
#  DATABASE (SQLite)
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE, username TEXT UNIQUE,
  password_hash TEXT NOT NULL,
  firstname TEXT, lastname TEXT, birthdate TEXT,
  gender TEXT DEFAULT 'unspecified', avatar TEXT,
  line_user_id TEXT,                          -- เก็บ LINE ID ของผู้ใช้สำหรับยิงแจ้งเตือน
  is_admin INTEGER DEFAULT 0,
  status TEXT DEFAULT 'normal',               -- normal | warning | banned
  created_at TEXT);
CREATE TABLE IF NOT EXISTS machines(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE, name TEXT, kind TEXT DEFAULT 'wash',   -- wash | dry
  capacity_kg INTEGER DEFAULT 10, enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS services(
  key TEXT PRIMARY KEY, name TEXT, type TEXT,              -- program | addon
  minutes INTEGER, price INTEGER);
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id), machine_id INTEGER REFERENCES machines(id),
  program_key TEXT, program_name TEXT, dry INTEGER, softener INTEGER,
  minutes INTEGER, price INTEGER,
  status TEXT,        -- reserved | washing | completed | cancelled | expired
  created_at TEXT, reserve_expires TEXT, start_time TEXT, end_time TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_per_machine
  ON bookings(machine_id) WHERE status IN ('reserved','washing');
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  booking_id INTEGER REFERENCES bookings(id),
  tx_id TEXT UNIQUE, amount INTEGER, method TEXT DEFAULT 'PromptPay',
  status TEXT,        -- pending | success | failed
  payload TEXT, created_at TEXT, expires_at TEXT, paid_at TEXT);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
  topic TEXT, machine_number TEXT, details TEXT, contact TEXT,
  status TEXT DEFAULT 'open', created_at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


def now():
    return datetime.now().replace(microsecond=0)


def ts(d):
    return d.strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S") if s else None


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)

    # อัปเดตตาราง users เพิ่มคอลัมน์ line_user_id กรณีใช้ DB เดิม
    try:
        db.execute("ALTER TABLE users ADD COLUMN line_user_id TEXT")
    except sqlite3.OperationalError:
        pass
    
    if not db.execute("SELECT 1 FROM services").fetchone():
        db.executemany("INSERT INTO services VALUES(?,?,?,?,?)", [
            ("quick", "ซักด่วน", "program", 1, 40),
            ("standard", "ซักมาตรฐาน", "program", 2, 50),
            ("special", "ซักสะอาดพิเศษ", "program", 3, 60),
            ("dry", "อบแห้ง", "addon", 1, 20),
            ("softener", "ปรับผ้านุ่ม", "addon", 0, 10)])
    else:
        db.executemany("UPDATE services SET minutes=? WHERE key=?", [
            (1, "quick"),
            (2, "standard"),
            (3, "special"),
            (1, "dry")
        ])

    if not db.execute("SELECT 1 FROM machines").fetchone():
        db.executemany("INSERT INTO machines(code,name,kind,capacity_kg) VALUES(?,?,?,?)",
                       [(f"M-{i:02d}", f"เครื่อง {i:02d}", "wash", 10) for i in range(1, 7)])
    for k, v in {"open_time": "08:00", "close_time": "22:00",
                 "line_enabled": "0", "line_token": ""}.items():
        db.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
    if not db.execute("SELECT 1 FROM users WHERE is_admin=1").fetchone():
        db.execute("INSERT INTO users(email,username,password_hash,is_admin,created_at) VALUES(?,?,?,1,?)",
                   (ADMIN_EMAIL, "admin", generate_password_hash(ADMIN_PASSWORD), ts(now())))
    db.commit()
    db.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


def get_setting(key, default=""):
    r = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key, value):
    with get_db() as db:
        db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))


# ============================================================
#  ตัวช่วยกลาง: เวลา / สถานะเครื่อง / Auth
# ============================================================
@app.template_filter("thai_dt")
def thai_dt(s):
    d = parse_ts(s)
    return f"{d.day} {THAI_MONTHS[d.month - 1]} {d.year}, {d:%H.%M} น." if d else "-"


@app.template_filter("short_dt")
def short_dt(s):
    d = parse_ts(s)
    return d.strftime("%d/%m/%Y, %H:%M") if d else "-"


@app.template_filter("format_mmss")
def format_mmss(seconds):
    if not seconds or seconds <= 0:
        return "0:00"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"


def to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def err(msg, code=400):
    return jsonify(error=msg), code


@app.before_request
def refresh_states():
    if request.endpoint in (None, "assets"):
        return
    db, t = get_db(), ts(now())

    # 1. ดึงรายการจองที่หมดเวลา เพื่อยิง LINE แจ้งเตือนผู้ใช้
    expiring_bookings = db.execute("""
        SELECT b.id, u.line_user_id, m.name AS machine_name
        FROM bookings b
        JOIN users u ON u.id = b.user_id
        JOIN machines m ON m.id = b.machine_id
        WHERE b.status = 'reserved' AND b.reserve_expires < ?
    """, (t,)).fetchall()

    for eb in expiring_bookings:
        if eb["line_user_id"]:
            msg = (
                f"⏰ [Smart Laundry] หมดเวลาชำระเงิน!\n"
                f"รายการจองตู้ {eb['machine_name']} หมดเวลาแล้ว ระบบได้ทำการยกเลิกและคืนคิวเรียบร้อยครับ"
            )
            send_line_message(eb["line_user_id"], msg)

    # 2. ดึงรายการที่ถึงกำหนดซักเสร็จ เพื่อยิง LINE แจ้งเตือนผู้ใช้
    completed_bookings = db.execute("""
        SELECT b.id, b.user_id, u.username, u.line_user_id, m.name AS machine_name, m.code AS machine_code
        FROM bookings b
        JOIN users u ON u.id = b.user_id
        JOIN machines m ON m.id = b.machine_id
        WHERE b.status = 'washing' AND b.end_time <= ?
    """, (t,)).fetchall()

    for cb in completed_bookings:
        notify_admin(f"✅ ตู้ {cb['machine_name']} ({cb['machine_code']}) ซักผ้าเสร็จสิ้นแล้วสำหรับคุณ {cb['username']}")
        notify_user(cb["user_id"], f"🧺 ผ้าของคุณที่ตู้ {cb['machine_name']} ซักเสร็จเรียบร้อยแล้ว!")
        if cb["line_user_id"]:
            msg = (
                f"✅ [Smart Laundry] ผ้าของคุณซักเสร็จเรียบร้อยแล้ว!\n"
                f"ตู้: {cb['machine_name']} ({cb['machine_code']})\n"
                f"👕 กรุณามารับผ้าออกจากตู้ได้เลยครับ"
            )
            send_line_message(cb["line_user_id"], msg)

    # 3. อัปเดตสถานะในฐานข้อมูล SQLite
    with db:
        db.execute("UPDATE bookings SET status='expired' WHERE status='reserved' AND reserve_expires<?", (t,))
        db.execute("UPDATE payments SET status='failed' WHERE status='pending' AND expires_at<?", (t,))
        db.execute("UPDATE bookings SET status='completed' WHERE status='washing' AND end_time<=?", (t,))


# 🛡️ ป้องกัน Browser Cache หน้าแอดมิน เพื่อไม่ให้กดย้อนกลับ (Back) มาดูหน้าล็อกอินค้างได้
@app.after_request
def prevent_caching(response):
    if request.path.startswith('/admin') or "text/html" in response.headers.get("Content-Type", ""):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "-1"
    return response


def store_open():
    n = now().strftime("%H:%M")
    o, c = get_setting("open_time", "08:00"), get_setting("close_time", "22:00")
    if o == c:
        return True
    return (o <= n < c) if o < c else (n >= o or n < c)


def machine_states():
    rows = get_db().execute("""
        SELECT m.*, b.status AS bstatus, b.end_time, b.reserve_expires
        FROM machines m
        LEFT JOIN bookings b ON b.machine_id=m.id AND b.status IN ('reserved','washing')
        ORDER BY m.id""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["state"], d["left"], d["left_seconds"] = "free", 0, 0
        if not r["enabled"]:
            d["state"] = "offline"
        elif r["bstatus"] == "washing":
            d["state"] = "busy"
            sec = max(0, int((parse_ts(r["end_time"]) - now()).total_seconds())) if r["end_time"] else 0
            d["left_seconds"] = sec
            d["left"] = sec / 60.0
        elif r["bstatus"] == "reserved":
            d["state"] = "reserved"
            sec = max(0, int((parse_ts(r["reserve_expires"]) - now()).total_seconds())) if r["reserve_expires"] else 0
            d["left_seconds"] = sec
            d["left"] = sec / 60.0
        out.append(d)
    return out


def current_user():
    if "user" not in g:
        g.user = None
        uid = session.get("uid") or session.get("user_id")
        if uid:
            u = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if u and u["status"] != "banned":
                g.user = u
            else:
                session.clear()
    return g.user


@app.context_processor
def inject_user():
    return {"user": current_user()}


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not current_user():
            if request.path.startswith("/api/"):
                return err("login_required", 401)
            return redirect("/login.html")
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u or not u["is_admin"]:
            if request.path.startswith("/admin/api/"):
                return err("forbidden", 403)
            return redirect("/login.html")
        return f(*a, **kw)
    return wrapper


# ============================================================
#  ระบบยิงข้อความ LINE OA & WEBHOOK (OTP Account Linking)
# ============================================================
def send_line_message(line_user_id, text_message):
    """ส่งข้อความ Push Message เข้าแชท LINE OA หาผู้ใช้เป็นรายบุคคล"""
    if not line_user_id or not LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_ACCESS_TOKEN.startswith("วาง_"):
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": line_user_id,
        "messages": [{"type": "text", "text": text_message}]
    }

    def run():
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            app.logger.warning("LINE push failed: %s", e)

    threading.Thread(target=run, daemon=True).start()


@app.post("/api/user/generate-line-otp")
@login_required
def generate_line_otp():
    """สร้างรหัส OTP 6 หลัก สำหรับนำไปพิมพ์ส่งในแชท LINE OA"""
    u = current_user()
    otp_code = str(secrets.randbelow(900000) + 100000)
    LINE_OTPS[otp_code] = {
        "user_id": u["id"],
        "expires_at": now() + timedelta(minutes=5)
    }
    return jsonify(ok=True, otp_code=otp_code)


@app.post("/api/line/webhook")
def line_webhook():
    """Webhook URL รับข้อความจาก LINE OA เพื่อตรวจสอบ OTP และผูก line_user_id"""
    data = request.get_json(silent=True) or {}
    events = data.get("events", [])

    for event in events:
        if event.get("type") == "message" and event.get("message", {}).get("type") == "text":
            line_user_id = event.get("source", {}).get("userId")
            text = event.get("message", {}).get("text", "").strip()

            if text in LINE_OTPS:
                otp_data = LINE_OTPS[text]
                if now() <= otp_data["expires_at"]:
                    user_id = otp_data["user_id"]
                    db = get_db()
                    with db:
                        db.execute("UPDATE users SET line_user_id=? WHERE id=?", (line_user_id, user_id))
                        u = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
                    
                    del LINE_OTPS[text]
                    send_line_message(
                        line_user_id, 
                        f"🎉 ผูกบัญชี Smart Laundry กับคุณ '{u['username']}' เรียบร้อยแล้ว!\nระบบจะเริ่มแจ้งเตือนสถานะการซักผ้าของคุณผ่านช่องทางนี้ครับ 🧺"
                    )
                else:
                    del LINE_OTPS[text]
                    send_line_message(line_user_id, "❌ รหัสยืนยันนี้หมดอายุแล้ว กรุณากดขอรหัสใหม่บนหน้าเว็บครับ")
            else:
                send_line_message(
                    line_user_id, 
                    "👋 สวัสดีครับ! หากต้องการผูกบัญชีรับแจ้งเตือน กรุณานำรหัส OTP 6 หลักจากหน้าเว็บ Profile มาพิมพ์ส่งในแชทนี้ได้เลยครับ"
                )

    return "OK", 200


# ============================================================
#  ระบบตรวจสลิปผ่าน SlipOK API
# ============================================================
def verify_slip_with_slipok(file_storage, expected_amount=None):
    """ส่งรูปภาพสลิปไปให้ SlipOK ตรวจสอบสลิปจริง ยอดโอน และสลิปซ้ำ"""
    url = SLIPOK_URL
    headers = {
        "x-authorization": SLIPOK_API_KEY
    }
    
    files = {
        'files': (file_storage.filename, file_storage.read(), file_storage.mimetype)
    }
    
    try:
        response = requests.post(url, headers=headers, files=files, timeout=12)
        res_data = response.json()

        if response.status_code == 200 and res_data.get("success"):
            data = res_data.get("data", {})
            paid_amount = float(data.get("amount", 0))

            if expected_amount and paid_amount < float(expected_amount):
                return False, f"ยอดเงินในสลิป ({paid_amount} บาท) ไม่ตรงกับยอดที่ต้องชำระ ({expected_amount} บาท)", data

            return True, "สลิปถูกต้อง", data
        else:
            msg = res_data.get("message", "สลิปไม่ถูกต้อง หรือถูกใช้งานไปแล้ว")
            return False, msg, None

    except Exception as e:
        return False, f"ไม่สามารถติดต่อระบบตรวจสอบสลิปได้: {str(e)}", None


# ============================================================
#  ไฟล์ Static (CSS / Images / Uploads)
# ============================================================
@app.route("/<any(template,images,uploads):folder>/<path:filename>")
def assets(folder, filename):
    return send_from_directory(os.path.join(BASE, folder), filename)


# ============================================================
#  หน้าเว็บทั่วไป
# ============================================================
@app.route("/")
def home():
    return redirect("/index.html")


@app.route("/<any(index,about,howtouse,faq,privacy):page>.html")
def static_page(page):
    return render_template(page + ".html")


# ============================================================
#  สมัครสมาชิก / ล็อกอิน / ล็อกเอาต์ / โปรไฟล์
# ============================================================
@app.route("/register.html", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        ident = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        email = username = None
        errors = []
        if "@" in ident:
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", ident):
                errors.append("รูปแบบอีเมลไม่ถูกต้อง")
            email = ident.lower()
        elif re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", ident):
            username = ident
        else:
            errors.append("Username ต้องเป็นอักษรอังกฤษ/ตัวเลข 3-30 ตัว หรือใช้อีเมล")
        if len(pw) < 6:
            errors.append("รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร")
        if pw != confirm:
            errors.append("รหัสผ่านและยืนยันรหัสผ่านไม่ตรงกัน")
        if not errors:
            try:
                with get_db() as db:
                    cur = db.execute(
                        "INSERT INTO users(email,username,password_hash,created_at) VALUES(?,?,?,?)",
                        (email, username, generate_password_hash(pw), ts(now())))
                session.clear()
                session["uid"] = cur.lastrowid
                session["user_id"] = cur.lastrowid
                return redirect("/index.html")
            except sqlite3.IntegrityError:
                errors.append("Username / อีเมลนี้ถูกใช้งานแล้ว")
        for m in errors:
            flask_flash(m, "error")
        return render_template("register.html"), 400
    return render_template("register.html")


@app.route("/login.html", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ident = request.form.get("username", "").strip().lower()
        pw = request.form.get("password", "")
        u = get_db().execute("SELECT * FROM users WHERE lower(email)=? OR lower(username)=?",
                             (ident, ident)).fetchone()
        if not u or not check_password_hash(u["password_hash"], pw):
            flash("Username/อีเมล หรือรหัสผ่านไม่ถูกต้อง", "error")
            return render_template("login.html"), 401
        if u["status"] == "banned":
            flash("บัญชีนี้ถูกระงับการใช้งาน กรุณาติดต่อผู้ดูแลระบบ", "error")
            return render_template("login.html"), 403
        
        next_url = session.get("next_url")

        session.clear()
        session["uid"] = u["id"]
        session["user_id"] = u["id"]
        session.permanent = True
        
        if next_url:
            return redirect(next_url)

        return redirect("/admin/admin_dashboard.html" if u["is_admin"] else "/index.html")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    next_page = request.args.get("next", "/index.html")
    res = redirect(next_page)
    res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    return res


# 🚪 ล็อกเอาต์ฝั่งแอดมิน (เคลียร์ Session + สั่งล้าง Cache)
@app.route("/admin/logout")
def admin_logout():
    session.clear()
    res = redirect("/login.html")
    res.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    res.headers["Pragma"] = "no-cache"
    res.headers["Expires"] = "0"
    return res


@app.route("/gprofile.html")
def gprofile():
    return redirect("/profile.html") if current_user() else render_template("gprofile.html")


@app.route("/profile.html")
def profile():
    if not current_user():
        return redirect("/gprofile.html")
    return render_template("profile.html")


ALLOWED_IMG = {"png", "jpg", "jpeg", "gif", "webp"}


@app.route("/edit_profile.html", methods=["GET", "POST"])
@login_required
def edit_profile():
    u = current_user()
    if request.method == "POST":
        f = request.form
        errors = []
        username = f.get("username", "").strip() or None
        if username and not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", username):
            errors.append("ชื่อผู้ใช้งานต้องเป็นอักษรอังกฤษ/ตัวเลข 3-30 ตัว (ห้ามมี @)")
        birth = None
        if f.get("birthdate", "").strip():
            try:
                d, m, y = map(int, f["birthdate"].strip().split("/"))
                birth = datetime(y - 543 if y > 2400 else y, m, d).strftime("%Y-%m-%d")
            except ValueError:
                errors.append("วันเกิดไม่ถูกต้อง (รูปแบบ dd/mm/yyyy)")
        gender = f.get("gender") if f.get("gender") in ("male", "female", "unspecified") else "unspecified"
        avatar = u["avatar"]
        up = request.files.get("avatar")
        if up and up.filename:
            ext = up.filename.rsplit(".", 1)[-1].lower() if "." in up.filename else ""
            if ext not in ALLOWED_IMG:
                errors.append("รองรับเฉพาะไฟล์รูป png, jpg, gif, webp")
            else:
                avatar = f"{u['id']}_{secrets.token_hex(6)}.{ext}"
                up.save(os.path.join(UPLOAD_DIR, avatar))
                if u["avatar"] and os.path.exists(os.path.join(UPLOAD_DIR, u["avatar"])):
                    os.remove(os.path.join(UPLOAD_DIR, u["avatar"]))
        if not errors:
            try:
                with get_db() as db:
                    db.execute("""UPDATE users SET username=?, firstname=?, lastname=?,
                                  birthdate=?, gender=?, avatar=? WHERE id=?""",
                               (username, f.get("firstname", "").strip(), f.get("lastname", "").strip(),
                                birth, gender, avatar, u["id"]))
                flask_flash("บันทึกข้อมูลเรียบร้อย", "success")
            except sqlite3.IntegrityError:
                flask_flash("ชื่อผู้ใช้งานนี้ถูกใช้แล้ว", "error")
        for m in errors:
            flask_flash(m, "error")
        return redirect("/edit_profile.html")
    b = parse_ts((u["birthdate"] or "") + " 00:00:00") if u["birthdate"] else None
    return render_template("edit_profile.html", birthdate=b.strftime("%d/%m/%Y") if b else "")


@app.route("/password.html", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        u, f = current_user(), request.form
        cur, new, conf = f.get("current_password", ""), f.get("new_password", ""), f.get("confirm_password", "")
        if not check_password_hash(u["password_hash"], cur):
            flash("รหัสผ่านปัจจุบันไม่ถูกต้อง", "error")
        elif len(new) < 6:
            flash("รหัสผ่านใหม่ต้องมีอย่างน้อย 6 ตัวอักษร", "error")
        elif new != conf:
            flash("รหัสผ่านใหม่และยืนยันไม่ตรงกัน", "error")
        elif new == cur:
            flash("รหัสผ่านใหม่ต้องไม่เหมือนรหัสผ่านเดิม", "error")
        else:
            with get_db() as db:
                db.execute("UPDATE users SET password_hash=? WHERE id=?",
                           (generate_password_hash(new), u["id"]))
            flash("เปลี่ยนรหัสผ่านเรียบร้อยแล้ว", "success")
        return redirect("/password.html")
    return render_template("password.html")


@app.route("/report.html", methods=["GET", "POST"])
def report():
    if request.method == "POST":
        f = request.form
        topic, details, contact = (f.get(k, "").strip() for k in ("issue_topic", "issue_details", "contact_info"))
        if not (topic and details and contact):
            flash("กรุณากรอกข้อมูลที่มี * ให้ครบ", "error")
        else:
            u = current_user()
            with get_db() as db:
                db.execute("""INSERT INTO reports(user_id,topic,machine_number,details,contact,created_at)
                              VALUES(?,?,?,?,?,?)""",
                           (u["id"] if u else None, topic[:200], f.get("machine_number", "").strip()[:50],
                            details[:3000], contact[:200], ts(now())))
            notify_admin(f"📢 มีการแจ้งปัญหาใหม่: {topic[:80]}")
            flash("ส่งเรื่องแจ้งปัญหาเรียบร้อย ทีมงานจะติดต่อกลับโดยเร็ว", "success")
        return redirect("/report.html")
    return render_template("report.html")


# ============================================================
#  การจองเครื่อง (Booking Page & API)
# ============================================================
def services_dict():
    return {r["key"]: dict(r) for r in get_db().execute("SELECT * FROM services")}


@app.route("/booking.html")
def booking_page():
    sv = services_dict()
    return render_template("booking.html", machines=machine_states(), store_open=store_open(),
                           programs=[s for s in sv.values() if s["type"] == "program"],
                           dry=sv["dry"], softener=sv["softener"])


@app.post("/api/bookings")
@login_required
def api_create_booking():
    d, u, db = request.get_json(silent=True) or {}, current_user(), get_db()
    if not store_open():
        return err(f"ขณะนี้ร้านปิดให้บริการ (เปิด {get_setting('open_time')} - {get_setting('close_time')} น.)")
    m = db.execute("SELECT * FROM machines WHERE id=?", (to_int(d.get("machine_id")),)).fetchone()
    if not m or not m["enabled"]:
        return err("ไม่พบเครื่อง หรือเครื่องปิดใช้งาน", 404)
    sv = services_dict()
    prog = sv.get(d.get("program"))
    if not prog or prog["type"] != "program":
        return err("กรุณาเลือกโปรแกรมการซัก")
    dry, soft = bool(d.get("dry")), bool(d.get("softener"))
    minutes = prog["minutes"] + (sv["dry"]["minutes"] if dry else 0)
    price = prog["price"] + (sv["dry"]["price"] if dry else 0) + (sv["softener"]["price"] if soft else 0)
    if db.execute("SELECT 1 FROM bookings WHERE user_id=? AND status='reserved'", (u["id"],)).fetchone():
        return err("คุณมีรายการจองที่ยังไม่ชำระเงิน กรุณาชำระเงินหรือยกเลิกก่อน", 409)
    t = now()
    try:
        with db:
            cur = db.execute("""INSERT INTO bookings(user_id,machine_id,program_key,program_name,dry,softener,
                                minutes,price,status,created_at,reserve_expires)
                                VALUES(?,?,?,?,?,?,?,?,'reserved',?,?)""",
                             (u["id"], m["id"], prog["key"], prog["name"], int(dry), int(soft), minutes, price,
                              ts(t), ts(t + timedelta(minutes=RESERVE_MINUTES))))
    except sqlite3.IntegrityError:
        return err("เครื่องนี้ถูกใช้งาน/จองไปแล้ว กรุณาเลือกเครื่องอื่น", 409)
    notify_admin(f"🧺 มีการจอง {m['name']} ({price} บาท)")

    # 🔔 สเต็ปที่ 1: ยิงแจ้งเตือนจองตู้สำเร็จเข้า LINE OA
    if u["line_user_id"]:
        msg = (
            f"🧺 [Smart Laundry] ยืนยันการจองตู้ซักผ้า\n"
            f"ตู้: {m['name']}\n"
            f"ยอดชำระ: {price} บาท\n"
            f"⏳ กรุณาสแกนจ่ายเงินและเริ่มใช้งานภายใน {RESERVE_MINUTES} นาทีครับ"
        )
        send_line_message(u["line_user_id"], msg)

    return jsonify(booking_id=cur.lastrowid, price=price, minutes=minutes, hold_minutes=RESERVE_MINUTES)


# ============================================================
#  สแกน QR หน้าตู้แล้วยืนยัน (Confirm Check-in)
# ============================================================
def normalize_code(s):
    if not s:
        return ""
    raw = str(s).strip()
    s_upper = raw.upper()

    if "QRCO.DE/BH4HGQ" in s_upper:
        return "M-01"

    m_param = re.search(r"(?:code|machine_id|machine|id)=([^&]+)", raw, re.I)
    if m_param:
        val = m_param.group(1)
        m_num = re.search(r"\d+", val)
        if m_num and 1 <= int(m_num.group()) <= 99:
            return f"M-{int(m_num.group()):02d}"

    m_code = re.search(r"(?:M|MACHINE|ตู้|เครื่อง)[-_\s]?(\d{1,2})\b", raw, re.I)
    if m_code:
        num = int(m_code.group(1))
        if 1 <= num <= 99:
            return f"M-{num:02d}"

    s_clean = re.sub(r"https?://[^/]+", "", raw)
    nums = re.findall(r"\b\d{1,2}\b", s_clean)
    if nums:
        for n in reversed(nums):
            val = int(n)
            if 1 <= val <= 99:
                return f"M-{val:02d}"

    if raw.isdigit() and 1 <= int(raw) <= 99:
        return f"M-{int(raw):02d}"

    return raw.upper()


@app.route("/confirm-checkin.html")
@app.route("/confirm-checkin")
def confirm_checkin_page():
    u = current_user()
    if not u:
        session["next_url"] = request.full_path
        flash("กรุณาเข้าสู่ระบบก่อนยืนยันการใช้บริการ", "error")
        return redirect("/login.html")
    
    mid = request.args.get("machine_id")
    code = request.args.get("code")
    db = get_db()
    
    machine = None
    if mid:
        machine = db.execute("SELECT * FROM machines WHERE id=?", (to_int(mid),)).fetchone()
    elif code:
        machine = db.execute("SELECT * FROM machines WHERE code=?", (normalize_code(code),)).fetchone()

    if not machine:
        flash("ไม่พบข้อมูลตู้ซักผ้าที่สแกน", "error")
        return redirect("/history.html")

    booking = db.execute("""
        SELECT b.*, m.name AS machine_name, m.code AS machine_code, m.capacity_kg 
        FROM bookings b 
        JOIN machines m ON m.id = b.machine_id 
        WHERE b.user_id = ? AND b.machine_id = ? AND b.status = 'reserved' AND b.reserve_expires > ?
    """, (u["id"], machine["id"], ts(now()))).fetchone()

    if not booking:
        flash(f"คุณไม่ได้จองตู้ {machine['name']} ไว้ หรือการจองหมดอายุแล้ว", "error")
        return redirect("/history.html")

    return render_template("confirm-checkin.html", booking=booking, machine=machine, user=u)


# ============================================================
#  ชำระเงิน PromptPay & อัปโหลดสลิป
# ============================================================
def crc16(s):
    crc = 0xFFFF
    for ch in s.encode():
        crc ^= ch << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return "%04X" % crc


def tlv(tag, val):
    return f"{tag}{len(val):02d}{val}"


def promptpay_payload(target_id, amount):
    t = re.sub(r"\D", "", target_id)
    if len(t) == 10 and t.startswith("0"):
        tgt = tlv("01", "0066" + t[1:])
    elif len(t) == 13:
        tgt = tlv("02", t)
    else:
        tgt = tlv("03", t)
    body = (tlv("00", "01") + tlv("01", "12") + tlv("29", tlv("00", "A000000677010111") + tgt)
            + tlv("53", "764") + tlv("54", "%.2f" % amount) + tlv("58", "TH") + "6304")
    return body + crc16(body)


def pending_booking(uid):
    return get_db().execute("""SELECT b.*, m.code AS machine_code, m.name AS machine_name, m.capacity_kg
                               FROM bookings b JOIN machines m ON m.id=b.machine_id
                               WHERE b.user_id=? AND b.status='reserved'""", (uid,)).fetchone()


@app.route("/qrcode.html")
def qrcode_page():
    return render_template("qrcode.html")


@app.route("/payment.html")
@app.route("/payment")
def payment_page():
    return render_template("payment.html")


@app.get("/api/bookings/pending")
@login_required
def api_pending_booking():
    b = pending_booking(current_user()["id"])
    if not b:
        return jsonify(booking=None)
    extra = (" + อบแห้ง" if b["dry"] else "") + (" + น้ำยาปรับผ้านุ่ม" if b["softener"] else "")
    return jsonify(booking=dict(
        machine_code=b["machine_code"], machine_name=b["machine_name"], capacity_kg=b["capacity_kg"],
        program=b["program_name"] + extra, price=b["price"],
        expires_in=int((parse_ts(b["reserve_expires"]) - now()).total_seconds())))


def payment_json(p, b):
    return dict(payment_id=p["id"], tx_id=p["tx_id"], amount=p["amount"], qr_payload=p["payload"],
                expires_in=max(0, int((parse_ts(p["expires_at"]) - now()).total_seconds())),
                machine_code=b["machine_code"], program=b["program_name"], capacity_kg=b["capacity_kg"])


@app.post("/api/payment/start")
@login_required
def api_payment_start():
    u, db = current_user(), get_db()
    b = pending_booking(u["id"])
    if not b:
        return err("ไม่พบรายการจองที่รอชำระเงิน กรุณาไปจองเครื่องก่อน", 404)
    code = normalize_code((request.get_json(silent=True) or {}).get("machine_code"))
    if code != b["machine_code"]:
        return err(f"รหัสตู้ไม่ตรงกับเครื่องที่จองไว้ ({b['machine_code']})")
    
    p = db.execute("SELECT * FROM payments WHERE booking_id=? AND status='pending'", (b["id"],)).fetchone()
    t = now()

    if not p:
        exp = t + timedelta(minutes=PAY_MINUTES)
        with db:
            cur = db.execute("""INSERT INTO payments(booking_id,tx_id,amount,status,payload,created_at,expires_at)
                                VALUES(?,?,?,'pending',?,?,?)""",
                             (b["id"], t.strftime("%y%m%d%H%M%S") + str(secrets.randbelow(900) + 100),
                              b["price"], promptpay_payload(PROMPTPAY_ID, b["price"]), ts(t), ts(exp)))
            # 🔒 ต่อเวลาล็อคตู้ใน bookings ให้ครอบคลุมช่วงเวลาชำระเงิน PromptPay (+30 วินาทีเผื่อเวลาประมวลผล)
            db.execute("UPDATE bookings SET reserve_expires=? WHERE id=?",
                       (ts(exp + timedelta(seconds=30)), b["id"]))
        p = db.execute("SELECT * FROM payments WHERE id=?", (cur.lastrowid,)).fetchone()
    else:
        # หากมีรายการชำระเงินเดิมที่กำลัง pending อยู่ ให้ขยายเวลา reserve_expires ของ booking ให้ครอบคลุมเวลา QR Code เสมอ
        exp_dt = parse_ts(p["expires_at"])
        if exp_dt and exp_dt > t:
            with db:
                db.execute("UPDATE bookings SET reserve_expires=? WHERE id=?",
                           (ts(exp_dt + timedelta(seconds=30)), b["id"]))

    return jsonify(payment_json(p, b))


# 🛡️ API อัปโหลดรูปภาพสลิปเพื่อตรวจผ่าน SlipOK (พร้อมระบบป้องกันสลิปซ้ำ)
@app.post("/api/payment/upload-slip")
@login_required
def api_upload_slip():
    u = current_user()
    db = get_db()
    
    b = pending_booking(u["id"])
    if not b:
        return err("ไม่พบรายการจองที่รอชำระเงิน", 404)

    if 'slip_image' not in request.files:
        return err("กรุณาแนบไฟล์รูปสลิปโอนเงิน")

    file = request.files['slip_image']
    if file.filename == '':
        return err("ไม่ได้เลือกไฟล์รูปสลิป")

    # 1. ตรวจสอบสลิปกับ SlipOK
    is_valid, message, slip_data = verify_slip_with_slipok(file, expected_amount=b["price"])
    if not is_valid:
        return err(message, 400)

    # 2. ดึงรหัสอ้างอิงธุรกรรม (transRef) จากสลิป
    trans_ref = (slip_data or {}).get("transRef")

    # 3. 🛡️ เช็กสลิปซ้ำ: หากรหัส transRef นี้เคยถูกใช้ชำระเงินสำเร็จไปแล้ว ให้ปฏิเสธทันที
    if trans_ref:
        dup = db.execute("SELECT 1 FROM payments WHERE tx_id=? AND status='success'", (trans_ref,)).fetchone()
        if dup:
            return err("สลิปนี้เคยถูกใช้ชำระเงินในระบบไปแล้ว ไม่สามารถใช้สลิปซ้ำได้!", 400)

    # 4. บันทึกและอัปเดตรายการชำระเงิน
    p = db.execute("SELECT * FROM payments WHERE booking_id=? AND status='pending'", (b["id"],)).fetchone()
    if not p:
        t = now()
        with db:
            cur = db.execute("""
                INSERT INTO payments(booking_id, tx_id, amount, status, payload, created_at, expires_at)
                VALUES(?, ?, ?, 'pending', ?, ?, ?)
            """, (
                b["id"], 
                trans_ref or (t.strftime("%y%m%d%H%M%S") + str(secrets.randbelow(900) + 100)),
                b["price"], 
                "PromptPay", 
                ts(t), 
                ts(t + timedelta(minutes=PAY_MINUTES))
            ))
            pid = cur.lastrowid
    else:
        pid = p["id"]
        if trans_ref:
            with db:
                db.execute("UPDATE payments SET tx_id=? WHERE id=?", (trans_ref, pid))

    # 5. ปรับสถานะชำระเงินสำเร็จและสั่งตู้ทำงาน
    if mark_payment_success(pid):
        return jsonify(ok=True, message="สลิปถูกต้อง! ตู้ซักผ้าเริ่มทำงานแล้ว")
    else:
        return err("เกิดข้อผิดพลาดในการอัปเดตสถานะเครื่อง", 500)


def own_payment(pid):
    return get_db().execute("""SELECT p.* FROM payments p JOIN bookings b ON b.id=p.booking_id
                               WHERE p.id=? AND b.user_id=?""", (pid, current_user()["id"])).fetchone()


@app.get("/api/payment/<int:pid>/status")
@login_required
def api_payment_status(pid):
    p = own_payment(pid)
    return jsonify(status=p["status"]) if p else err("not found", 404)


@app.post("/api/payment/cancel")
@login_required
def api_payment_cancel():
    u, db = current_user(), get_db()
    with db:
        db.execute("""UPDATE payments SET status='failed' WHERE status='pending' AND booking_id IN
                      (SELECT id FROM bookings WHERE user_id=? AND status='reserved')""", (u["id"],))
        db.execute("UPDATE bookings SET status='cancelled' WHERE user_id=? AND status='reserved'", (u["id"],))
    return jsonify(ok=True)


# ============================================================
#  อัปเดตเมื่อจ่ายเงินสำเร็จ + แจ้งเตือนผู้ใช้งาน
# ============================================================
def mark_payment_success(pid):
    db = get_db()
    p = db.execute("""SELECT p.*, b.minutes, b.machine_id, m.code AS machine_code, m.name AS machine_name, b.user_id
                      FROM payments p
                      JOIN bookings b ON b.id=p.booking_id 
                      JOIN machines m ON m.id=b.machine_id
                      WHERE p.id=?""", (pid,)).fetchone()
    if not p or p["status"] != "pending":
        return False
    t = now()
    with db:
        n = db.execute("UPDATE bookings SET status='washing', start_time=?, end_time=? WHERE id=? AND status='reserved'",
                       (ts(t), ts(t + timedelta(minutes=p["minutes"])), p["booking_id"])).rowcount
        if not n:
            return False
        db.execute("UPDATE payments SET status='success', paid_at=? WHERE id=?", (ts(t), pid))
    
    notify_admin(f"💰 ได้รับชำระเงิน {p['amount']} บาท (รหัส {p['tx_id']}) - ตู้ {p['machine_name']} เริ่มทำงาน")
    notify_user(p["user_id"], f"💳 การชำระเงินสำเร็จ! ตู้ {p['machine_name']} เริ่มทำงานแล้ว ({p['minutes']} นาที)")

    # 🔔 สเต็ปที่ 2: ยิงแจ้งเตือนชำระเงินสำเร็จ / เริ่มซักเข้า LINE OA
    u = db.execute("SELECT line_user_id FROM users WHERE id=?", (p["user_id"],)).fetchone()
    if u and u["line_user_id"]:
        end_dt = parse_ts(ts(t + timedelta(minutes=p["minutes"])))
        finish_str = end_dt.strftime("%H:%M น.")
        msg = (
            f"💳 [Smart Laundry] ชำระเงินสำเร็จ!\n"
            f"ตู้: {p['machine_name']} เริ่มทำงานเรียบร้อย\n"
            f"⏱️ ระยะเวลา: {p['minutes']} นาที\n"
            f"🕒 คาดว่าจะซักเสร็จเวลา: {finish_str}"
        )
        send_line_message(u["line_user_id"], msg)

    return True


@app.post("/api/payment/<int:pid>/simulate-success")
@login_required
def api_payment_simulate(pid):
    if not DEMO_MODE:
        return err("not found", 404)
    if not own_payment(pid) or not mark_payment_success(pid):
        return err("ทำรายการไม่ได้ (QR อาจหมดอายุแล้ว)", 409)
    return jsonify(ok=True)


# ============================================================
#  API ตรวจสถานะการซักสำหรับ Global Notification
# ============================================================
@app.get("/api/user/active-status")
@login_required
def api_user_active_status():
    u = current_user()
    db = get_db()
    
    b_washing = db.execute("""
        SELECT b.*, m.name AS machine_name, m.code AS machine_code 
        FROM bookings b
        JOIN machines m ON m.id = b.machine_id
        WHERE b.user_id = ? AND b.status = 'washing'
    """, (u["id"],)).fetchone()

    if b_washing:
        sec_left = max(0, int((parse_ts(b_washing["end_time"]) - now()).total_seconds()))
        return jsonify(
            has_active=True,
            status="washing",
            booking_id=b_washing["id"],
            machine_name=b_washing["machine_name"],
            machine_code=b_washing["machine_code"],
            seconds_left=sec_left
        )

    b_completed = db.execute("""
        SELECT b.*, m.name AS machine_name, m.code AS machine_code 
        FROM bookings b
        JOIN machines m ON m.id = b.machine_id
        WHERE b.user_id = ? AND b.status = 'completed'
        ORDER BY b.id DESC LIMIT 1
    """, (u["id"],)).fetchone()

    if b_completed and b_completed["end_time"]:
        sec_since_done = int((now() - parse_ts(b_completed["end_time"])).total_seconds())
        if 0 <= sec_since_done <= 300:
            return jsonify(
                has_active=True,
                status="just_completed",
                booking_id=b_completed["id"],
                machine_name=b_completed["machine_name"],
                machine_code=b_completed["machine_code"],
                seconds_left=0
            )

    return jsonify(has_active=False)


# ============================================================
#  ESP32 IoT Integration APIs
# ============================================================
@app.get("/api/esp32/status/<machine_code>")
def esp32_get_status(machine_code):
    code = normalize_code(machine_code)
    db = get_db()
    m = db.execute("SELECT * FROM machines WHERE code=?", (code,)).fetchone()
    if not m:
        return jsonify(error="Machine not found"), 404
        
    b = db.execute("""
        SELECT * FROM bookings 
        WHERE machine_id = ? AND status = 'washing' AND end_time > ?
    """, (m["id"], ts(now()))).fetchone()
    
    if b:
        sec_left = max(0, int((parse_ts(b["end_time"]) - now()).total_seconds()))
        return jsonify({
            "machine_code": code,
            "status": "washing",
            "relay": "ON",
            "seconds_left": sec_left,
            "booking_id": b["id"]
        })
    else:
        return jsonify({
            "machine_code": code,
            "status": "free",
            "relay": "OFF",
            "seconds_left": 0,
            "booking_id": None
        })


@app.post("/api/esp32/complete")
def esp32_complete():
    d = request.get_json(silent=True) or {}
    code = normalize_code(d.get("machine_code"))
    db = get_db()
    m = db.execute("SELECT * FROM machines WHERE code=?", (code,)).fetchone()
    if not m:
        return err("Machine not found", 404)
        
    b = db.execute("""
        SELECT b.*, u.username, u.email 
        FROM bookings b 
        JOIN users u ON u.id = b.user_id
        WHERE b.machine_id = ? AND b.status = 'washing'
    """, (m["id"],)).fetchone()
    
    if b:
        with db:
            db.execute("UPDATE bookings SET status='completed' WHERE id=?", (b["id"],))
        
        notify_admin(f"✅ ตู้ {m['name']} ({code}) ซักผ้าเสร็จสิ้นแล้วสำหรับคุณ {b['username']}")
        notify_user(b["user_id"], f"🧺 ผ้าของคุณที่ตู้ {m['name']} ({code}) ซักเสร็จเรียบร้อยแล้ว! กรุณามารับผ้าด้วยครับ")

        # 🔔 สเต็ปที่ 3: ยิงแจ้งเตือนผ้าซักเสร็จเรียบร้อยเข้า LINE OA
        u = db.execute("SELECT line_user_id FROM users WHERE id=?", (b["user_id"],)).fetchone()
        if u and u["line_user_id"]:
            msg = (
                f"✅ [Smart Laundry] ผ้าของคุณซักเสร็จเรียบร้อยแล้ว!\n"
                f"ตู้: {m['name']} ({code})\n"
                f"👕 กรุณามารับผ้าออกจากตู้ได้เลยครับ"
            )
            send_line_message(u["line_user_id"], msg)

        return jsonify(ok=True, message="Status updated to completed")
    
    return jsonify(ok=True, message="No active washing booking found")


# ============================================================
#  ประวัติการใช้งาน
# ============================================================
@app.route("/history1.html")
@app.route("/history.html")
@login_required
def history():
    rows = get_db().execute("""SELECT b.*, m.name AS machine_name, m.capacity_kg FROM bookings b
                               JOIN machines m ON m.id=b.machine_id WHERE b.user_id=?
                               ORDER BY b.id DESC""", (current_user()["id"],)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        end = r["reserve_expires"] if r["status"] == "reserved" else r["end_time"]
        sec = max(0, int((parse_ts(end) - now()).total_seconds())) if r["status"] in ("reserved", "washing") and end else 0
        d["left_seconds"] = sec
        d["left"] = sec / 60.0
        items.append(d)
    return render_template("history1.html",
                           washes=[i for i in items if i["status"] in ("washing", "completed")],
                           bookings=items)


# ============================================================
#  ระบบแจ้งเตือน (LINE Notify & User Notification)
# ============================================================
def line_push(token, to, text):
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps({"to": to, "messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    urllib.request.urlopen(req, timeout=5).read()


def notify_admin(text):
    to, token = os.getenv("LINE_ADMIN_USER_ID"), get_setting("line_token")
    if get_setting("line_enabled") != "1" or not (to and token):
        return

    def run():
        try:
            line_push(token, to, text)
        except Exception as e:
            app.logger.warning("LINE push failed: %s", e)
    threading.Thread(target=run, daemon=True).start()


def notify_user(user_id, text):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if u:
        app.logger.info(f"[NOTIFY USER #{user_id} ({u['username']})]: {text}")


# ============================================================
#  ADMIN DASHBOARD & MACHINE MANAGEMENT
# ============================================================
@app.route("/admin/admin_dashboard.html")
@admin_required
def admin_dashboard():
    db, today = get_db(), now().date()
    one = lambda sql, *a: db.execute(sql, a).fetchone()[0]
    daily = one("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='success' AND date(paid_at)=?", today.isoformat())
    monthly = one("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='success' AND strftime('%Y-%m',paid_at)=?",
                  today.strftime("%Y-%m"))
    monday = today - timedelta(days=today.weekday())
    counts = {r[0]: r[1] for r in db.execute(
        "SELECT date(paid_at), COUNT(*) FROM payments WHERE status='success' AND date(paid_at)>=? GROUP BY 1",
        (monday.isoformat(),))}
    peak = max(counts.values(), default=0)
    bars = []
    for i, label in enumerate(["จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา."]):
        d = monday + timedelta(days=i)
        c = counts.get(d.isoformat(), 0)
        bars.append(dict(
            label=label, 
            count=c, 
            active=(d == today), 
            height=max(4, round(100 * c / peak)) if peak else 4,
            date=d.isoformat()  # 👈 เพิ่มส่งค่า Date สำหรับคลิกดูรายละเอียด
        ))
    return render_template("admin/admin_dashboard.html", daily=daily, monthly=monthly,
                           bars=bars, machines=machine_states())


# 📊 API ดึงรายละเอียดการใช้งานประจำวันสำหรับแสดงบน Pop-up แดชบอร์ด
@app.get("/admin/api/daily-details/<date_str>")
@admin_required
def admin_daily_details(date_str):
    db = get_db()
    rows = db.execute("""
        SELECT p.tx_id, p.amount, p.paid_at, u.username, u.email, m.name AS machine_name, b.program_name
        FROM payments p
        JOIN bookings b ON b.id = p.booking_id
        JOIN machines m ON m.id = b.machine_id
        JOIN users u ON u.id = b.user_id
        WHERE p.status = 'success' AND date(p.paid_at) = ?
        ORDER BY p.paid_at DESC
    """, (date_str,)).fetchall()
    
    total_amount = sum(r["amount"] for r in rows)
    total_count = len(rows)
    
    details = [dict(r) for r in rows]
    return jsonify(ok=True, date=date_str, total_amount=total_amount, total_count=total_count, details=details)


@app.route("/admin/manage.html")
@admin_required
def admin_manage():
    ms = machine_states()
    return render_template("admin/manage.html", machines=ms, total=len(ms),
                           available=sum(m["state"] == "free" for m in ms),
                           broken=sum(m["state"] == "offline" for m in ms),
                           services=list(services_dict().values()))


# 1. API เพิ่มเครื่องซักผ้าใหม่
@app.post("/admin/api/machines")
@admin_required
def admin_add_machine():
    d = request.get_json(silent=True) or {}
    name = d.get("name", "").strip()
    capacity_kg = to_int(d.get("capacity_kg"), 10)
    kind = d.get("kind", "wash")
    enabled = 1 if d.get("enabled", True) else 0

    if not name:
        return err("กรุณาระบุชื่อเครื่อง")

    db = get_db()
    last_id = db.execute("SELECT MAX(id) FROM machines").fetchone()[0] or 0
    code = d.get("code", "").strip() or f"M-{(last_id + 1):02d}"

    try:
        with db:
            db.execute(
                "INSERT INTO machines(code, name, kind, capacity_kg, enabled) VALUES(?,?,?,?,?)",
                (code, name, kind, capacity_kg, enabled)
            )
        return jsonify(ok=True, message="เพิ่มเครื่องใหม่เรียบร้อยแล้ว")
    except sqlite3.IntegrityError:
        return err("รหัสเครื่อง (Code) นี้มีในระบบแล้ว", 409)


# 2. API แก้ไขข้อมูลเครื่อง
@app.post("/admin/api/machines/<int:mid>")
@admin_required
def admin_update_machine(mid):
    d = request.get_json(silent=True) or {}
    fields, args = [], []
    if "name" in d and d["name"]:
        fields.append("name=?"); args.append(str(d["name"])[:50])
    if "code" in d and d["code"]:
        fields.append("code=?"); args.append(str(d["code"])[:20])
    if "capacity_kg" in d:
        fields.append("capacity_kg=?"); args.append(max(1, to_int(d["capacity_kg"], 10)))
    if "enabled" in d:
        fields.append("enabled=?"); args.append(int(bool(d["enabled"])))
    if not fields:
        return err("ไม่มีข้อมูลให้แก้ไข")
    
    try:
        with get_db() as db:
            db.execute(f"UPDATE machines SET {','.join(fields)} WHERE id=?", (*args, mid))
        return jsonify(ok=True, message="บันทึกข้อมูลเรียบร้อยแล้ว")
    except sqlite3.IntegrityError:
        return err("รหัสเครื่อง (Code) ซ้ำกับเครื่องอื่นในระบบ", 409)


# 3. API ลบเครื่องซักผ้า (แก้ปัญหา Foreign Key Constraint)
@app.delete("/admin/api/machines/<int:mid>")
@admin_required
def admin_delete_machine(mid):
    db = get_db()
    active = db.execute(
        "SELECT 1 FROM bookings WHERE machine_id=? AND status IN ('reserved','washing')",
        (mid,)
    ).fetchone()
    if active:
        return err("ไม่สามารถลบได้ เนื่องจากเครื่องกำลังทำงานหรือถูกจองอยู่", 409)

    try:
        with db:
            db.execute("DELETE FROM payments WHERE booking_id IN (SELECT id FROM bookings WHERE machine_id=?)", (mid,))
            db.execute("DELETE FROM bookings WHERE machine_id=?", (mid,))
            db.execute("DELETE FROM machines WHERE id=?", (mid,))
        return jsonify(ok=True, message="ลบเครื่องเรียบร้อยแล้ว")
    except Exception as e:
        return err(f"เกิดข้อผิดพลาดในการลบ: {str(e)}", 500)


@app.post("/admin/api/services/<key>")
@admin_required
def admin_update_service(key):
    d = request.get_json(silent=True) or {}
    price, minutes = to_int(d.get("price"), -1), to_int(d.get("minutes"), -1)
    if price < 0 or minutes < 0:
        return err("ราคา/เวลา ไม่ถูกต้อง")
    with get_db() as db:
        n = db.execute("UPDATE services SET price=?, minutes=? WHERE key=?", (price, minutes, key)).rowcount
    return jsonify(ok=True) if n else err("not found", 404)


@app.route("/admin/admin_transactions.html")
@admin_required
def admin_transactions():
    db, a = get_db(), request.args
    q, rng, grp, page = a.get("q", "").strip(), a.get("range", ""), a.get("group", ""), max(1, to_int(a.get("page"), 1))
    where, args = ["1=1"], []
    if q:
        where.append("(p.tx_id LIKE ? OR m.code LIKE ? OR u.email LIKE ?)"); args += [f"%{q}%"] * 3
    if rng in ("today", "7", "30"):
        where.append("date(p.created_at)>=?")
        args.append((now().date() - timedelta(days={"today": 0, "7": 6, "30": 29}[rng])).isoformat())
    if grp in ("wash", "dry"):
        where.append("m.kind=?"); args.append(grp)
    frm = """FROM payments p JOIN bookings b ON b.id=p.booking_id JOIN machines m ON m.id=b.machine_id
             JOIN users u ON u.id=b.user_id WHERE """ + " AND ".join(where)
    total = db.execute("SELECT COUNT(*) " + frm, args).fetchone()[0]
    per = 10
    rows = db.execute("SELECT p.*, m.code AS machine_code, u.email AS user_email " + frm +
                      " ORDER BY p.id DESC LIMIT ? OFFSET ?", (*args, per, (page - 1) * per)).fetchall()
    one = lambda sql: db.execute(sql).fetchone()[0]
    n_all, n_ok = one("SELECT COUNT(*) FROM payments"), one("SELECT COUNT(*) FROM payments WHERE status='success'")
    stats = dict(revenue=one("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='success'"),
                 scans=n_all, rate=round(100 * n_ok / n_all, 1) if n_all else 0,
                 issues=one("SELECT COUNT(*) FROM reports WHERE status='open'"))
    return render_template("admin/admin_transactions.html", rows=rows, stats=stats, total=total, page=page,
                           pages=max(1, -(-total // per)), q=q, rng=rng, grp=grp,
                           first=(page - 1) * per + 1 if total else 0, last=min(page * per, total))


# 🔍 API ดึงรายละเอียดรายการชำระเงินและข้อมูลการซักสำหรับ Admin Details Pop-up
@app.get("/admin/api/transaction-details/<int:pid>")
@admin_required
def admin_transaction_details(pid):
    db = get_db()
    r = db.execute("""
        SELECT p.*, 
               b.program_name, b.dry, b.softener, b.minutes, b.price AS booking_price, 
               b.status AS booking_status, b.start_time, b.end_time, b.created_at AS booking_created,
               m.name AS machine_name, m.code AS machine_code, m.kind AS machine_kind, m.capacity_kg,
               u.username, u.email, u.firstname, u.lastname, u.line_user_id
        FROM payments p
        JOIN bookings b ON b.id = p.booking_id
        JOIN machines m ON m.id = b.machine_id
        JOIN users u ON u.id = b.user_id
        WHERE p.id = ?
    """, (pid,)).fetchone()
    
    if not r:
        return err("ไม่พบข้อมูลรายการนี้", 404)
    
    d = dict(r)
    return jsonify(ok=True, data=d)


@app.route("/admin/admin_settings.html")
@admin_required
def admin_settings():
    db, q, page = get_db(), request.args.get("q", "").strip(), max(1, to_int(request.args.get("page"), 1))
    where, args = "WHERE is_admin=0", []
    if q:
        where += " AND (email LIKE ? OR username LIKE ? OR firstname LIKE ? OR lastname LIKE ?)"
        args = [f"%{q}%"] * 4
    per = 10
    total = db.execute(f"SELECT COUNT(*) FROM users {where}", args).fetchone()[0]
    users = db.execute(f"""SELECT u.*, (SELECT COUNT(*) FROM bookings b WHERE b.user_id=u.id
                           AND b.status IN ('washing','completed')) AS uses
                           FROM users u {where} ORDER BY id LIMIT ? OFFSET ?""",
                       (*args, per, (page - 1) * per)).fetchall()
    return render_template("admin/admin_settings.html", users=users, total=total, page=page, q=q,
                           pages=max(1, -(-total // per)), offset=(page - 1) * per,
                           first=(page - 1) * per + 1 if total else 0, last=min(page * per, total),
                           open_time=get_setting("open_time"), close_time=get_setting("close_time"),
                           line_enabled=get_setting("line_enabled") == "1",
                           has_token=bool(get_setting("line_token")), is_open=store_open())


@app.post("/admin/api/settings")
@admin_required
def admin_save_settings():
    d = request.get_json(silent=True) or {}
    for k in ("open_time", "close_time"):
        if k in d:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(d[k])):
                return err("รูปแบบเวลาไม่ถูกต้อง (HH:MM)")
            set_setting(k, d[k])
    if "line_enabled" in d:
        set_setting("line_enabled", "1" if d["line_enabled"] else "0")
    if d.get("line_token"):
        set_setting("line_token", str(d["line_token"]).strip())
    return jsonify(ok=True)


@app.post("/admin/api/line-test")
@admin_required
def admin_line_test():
    to, token = os.getenv("LINE_ADMIN_USER_ID"), get_setting("line_token")
    if not (to and token):
        return err("ยังไม่ได้ตั้งค่า token และ LINE_ADMIN_USER_ID")
    try:
        line_push(token, to, "✅ ทดสอบแจ้งเตือนจาก Smart Laundry")
        return jsonify(ok=True)
    except Exception as e:
        return err(f"ส่งไม่สำเร็จ: {e}", 502)


@app.post("/admin/api/users/<int:uid>/status")
@admin_required
def admin_user_status(uid):
    st = (request.get_json(silent=True) or {}).get("status")
    if st not in ("normal", "warning", "banned"):
        return err("สถานะไม่ถูกต้อง")
    with get_db() as db:
        n = db.execute("UPDATE users SET status=? WHERE id=? AND is_admin=0", (st, uid)).rowcount
    return jsonify(ok=True) if n else err("not found", 404)


# ============================================================
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)