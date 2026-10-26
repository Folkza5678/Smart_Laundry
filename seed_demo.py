"""ใส่ข้อมูลตัวอย่าง (แทนข้อมูลที่เคยม็อคไว้ใน HTML) — รันเมื่ออยากได้หน้าที่ไม่ว่างเปล่า
    python seed_demo.py          # เพิ่มข้อมูลตัวอย่าง (รันซ้ำได้ ไม่ซ้ำซ้อน)
    python seed_demo.py --reset  # ลบการจอง/การจ่ายเงิน/รายงาน/สมาชิกตัวอย่างเดิมก่อนแล้วสร้างใหม่
บัญชีตัวอย่างทุกคนรหัสผ่าน: demo1234   (ล็อกอินด้วยอีเมลของแต่ละคน เช่น somchai.j@gmail.com)
"""
import random
import sqlite3
import sys
from datetime import timedelta

from werkzeug.security import generate_password_hash

import app as A

DEMO = [("somchai.j@gmail.com", "somchai", "สมชาย", "ใจดี", 24),
        ("kamon.k@gmail.com", "kamon", "กมลวรรณ", "สุขเสริฐ", 12),
        ("wichai.r@gmail.com", "wichai", "วิชัย", "รักชาติ", 45),
        ("stalker69@gmail.com", "stalker69", "รายงานพฤติกรรมไม่เหมาะสม", "", 2)]
PROGRAMS = [("quick", "ซักด่วน", 15, 40), ("standard", "ซักมาตรฐาน", 30, 50), ("special", "ซักสะอาดพิเศษ", 45, 60)]

db = sqlite3.connect(A.DB_PATH)
db.row_factory = sqlite3.Row
if "--reset" in sys.argv:
    emails = [d[0] for d in DEMO]
    ids = [r[0] for r in db.execute(f"SELECT id FROM users WHERE email IN ({','.join('?' * len(emails))})", emails)]
    for i in ids:
        db.execute("DELETE FROM payments WHERE booking_id IN (SELECT id FROM bookings WHERE user_id=?)", (i,))
        db.execute("DELETE FROM bookings WHERE user_id=?", (i,))
        db.execute("DELETE FROM users WHERE id=?", (i,))
    db.execute("DELETE FROM reports WHERE contact LIKE '%demo%'")
if db.execute("SELECT 1 FROM users WHERE email=?", (DEMO[0][0],)).fetchone():
    sys.exit("มีข้อมูลตัวอย่างอยู่แล้ว (ใช้ --reset เพื่อสร้างใหม่)")

random.seed(7)
uids = []
for email, un, fn, ln, _ in DEMO:
    cur = db.execute("INSERT INTO users(email,username,password_hash,firstname,lastname,status,created_at) VALUES(?,?,?,?,?,?,?)",
                     (email, un, generate_password_hash("demo1234"), fn, ln,
                      "warning" if un == "stalker69" else "normal", A.ts(A.now())))
    uids.append(cur.lastrowid)

now = A.now()
machines = [r[0] for r in db.execute("SELECT id FROM machines WHERE enabled=1")]
n = 0
for day in range(7, -1, -1):                      # ย้อนหลัง 8 วัน วันละ 2-6 รายการ
    for _ in range(random.randint(2, 6)):
        key, name, mins, price = random.choice(PROGRAMS)
        dry, soft = random.random() < .5, random.random() < .3
        mins += 20 if dry else 0
        price += (20 if dry else 0) + (10 if soft else 0)
        start = (now - timedelta(days=day)).replace(hour=random.randint(8, 20), minute=random.randint(0, 59), second=0)
        if start > now - timedelta(hours=2):
            continue
        n += 1
        paid = random.random() < .9                     # 10% จ่ายไม่สำเร็จ/QR หมดอายุ
        cur = db.execute("""INSERT INTO bookings(user_id,machine_id,program_key,program_name,dry,softener,minutes,price,
                            status,created_at,reserve_expires,start_time,end_time) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (random.choice(uids[:3]), random.choice(machines), key, name, int(dry), int(soft), mins, price,
                          "completed" if paid else "expired",
                          A.ts(start - timedelta(minutes=3)), A.ts(start + timedelta(minutes=7)),
                          A.ts(start) if paid else None, A.ts(start + timedelta(minutes=mins)) if paid else None))
        db.execute("INSERT INTO payments(booking_id,tx_id,amount,status,payload,created_at,expires_at,paid_at) VALUES(?,?,?,?,?,?,?,?)",
                   (cur.lastrowid, start.strftime("%y%m%d%H%M%S") + str(n).zfill(3), price,
                    "success" if paid else "failed", "", A.ts(start - timedelta(minutes=1)),
                    A.ts(start + timedelta(minutes=4)), A.ts(start) if paid else None))
# ตัวอย่าง: M-02 กำลังซักอยู่ (ผู้ใช้ somchai) และ M-04 ติดจอง (ผู้ใช้ kamon)
t = now
for mid, uid, st, kw in [(2, uids[0], "washing", dict(start=t - timedelta(minutes=12), end=t + timedelta(minutes=18))),
                          (4, uids[1], "reserved", None)]:
    db.execute("""INSERT INTO bookings(user_id,machine_id,program_key,program_name,dry,softener,minutes,price,status,
                  created_at,reserve_expires,start_time,end_time) VALUES(?,?,?,?,0,0,30,50,?,?,?,?,?)""",
               (uid, mid, "standard", "ซักมาตรฐาน", st, A.ts(t - timedelta(minutes=2)), A.ts(t + timedelta(minutes=8)),
                A.ts(kw["start"]) if kw else None, A.ts(kw["end"]) if kw else None))
    if kw:
        b = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO payments(booking_id,tx_id,amount,status,payload,created_at,expires_at,paid_at) VALUES(?,?,?,?,?,?,?,?)",
                   (b, t.strftime("%y%m%d%H%M%S") + "999", 50, "success", "", A.ts(kw["start"]), A.ts(kw["start"]), A.ts(kw["start"])))
db.executemany("INSERT INTO reports(user_id,topic,machine_number,details,contact,created_at) VALUES(?,?,?,?,?,?)",
               [(uids[0], "เครื่องไม่ปั่นผ้า", "M-03", "หยุดทำงานกลางรอบ", "somchai.demo@mail.com", A.ts(now)),
                (None, "QR ชำระเงินไม่ขึ้น", "", "สแกนแล้วไม่ขึ้นยอด", "0812345678 demo", A.ts(now)),
                (uids[2], "ผ้าไม่สะอาด", "M-01", "ซักแล้วยังมีกลิ่น", "wichai.demo@mail.com", A.ts(now))])
db.commit()
print(f"เพิ่มข้อมูลตัวอย่างแล้ว: สมาชิก {len(DEMO)} คน, การซัก {n}+2 รายการ, รายงานปัญหา 3 รายการ (รหัสผ่านสมาชิกตัวอย่าง: demo1234)")
