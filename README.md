# Smart Laundry

เว็บไซต์จัดการร้านซักผ้าหยอดเหรียญ/บริการซักผ้า ให้ลูกค้าตรวจสอบสถานะเครื่อง เลือกโปรแกรมและจองเครื่อง ชำระเงินผ่าน PromptPay และติดตามรายการซักได้ ส่วนผู้ดูแลสามารถจัดการเครื่อง บริการ ผู้ใช้ และตรวจสอบรายการชำระเงินได้

## ฟีเจอร์หลัก

### สำหรับผู้ใช้

- สมัครสมาชิก เข้าสู่ระบบ แก้ไขโปรไฟล์ และเปลี่ยนรหัสผ่าน
- ดูเครื่องซักผ้า และสถานะว่าง/กำลังใช้งาน/ปรับปรุง
- จองเครื่อง เลือกโปรแกรมซัก และเพิ่มบริการอบแห้งหรือน้ำยาปรับผ้านุ่ม
- ชำระเงินด้วย QR PromptPay พร้อมส่งสลิปเพื่อตรวจสอบผ่าน SlipOK
- ดูประวัติการใช้งานและสถานะการซัก
- เชื่อมบัญชี LINE เพื่อรับการแจ้งเตือน และรีเซ็ตรหัสผ่านด้วย OTP ทางอีเมล
- ส่งรายงานปัญหาเกี่ยวกับเครื่องหรือบริการ

### สำหรับผู้ดูแล

- ดูแดชบอร์ด ยอดขาย และสถิติการใช้งาน
- เพิ่ม แก้ไข ปิดใช้งาน หรือลบเครื่อง และปรับราคา/เวลาให้บริการ
- ตรวจสอบประวัติธุรกรรมและจัดการสถานะสมาชิก
- ตั้งค่าการแจ้งเตือน LINE และดูรายงานปัญหา

## เทคโนโลยี

- **Backend:** Python, Flask
- **Frontend:** HTML, CSS, JavaScript
- **ฐานข้อมูล:** SQLite
- **บริการภายนอก (ตั้งค่าเพิ่มเติม):** SlipOK, PromptPay, LINE Messaging API และ Gmail SMTP

## เริ่มต้นใช้งาน

ต้องติดตั้ง Python 3.10 ขึ้นไปก่อน

```bash
# 1. สร้างและเปิดใช้งาน virtual environment
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows PowerShell

# 2. ติดตั้ง dependencies
python -m pip install -r requirements.txt

# 3. เริ่มเว็บเซิร์ฟเวอร์สำหรับพัฒนา
python app.py
```

เปิดเว็บที่ <http://127.0.0.1:5000/> ฐานข้อมูล `smartlaundry.db` และตารางที่จำเป็นจะถูกสร้างขึ้นโดยอัตโนมัติเมื่อเริ่มแอปครั้งแรก

## บัญชีเริ่มต้น

เมื่อฐานข้อมูลยังไม่มีบัญชี ระบบจะสร้างบัญชีเริ่มต้นดังนี้:

| ประเภท | อีเมล | รหัสผ่าน |
|---|---|---|
| ผู้ดูแล | `admin@smartlaundry.local` | `admin1234` |
| ผู้ใช้ทดสอบ | `user@test.com` | `user1234` |

เปลี่ยนรหัสผ่านและตั้งค่าความปลอดภัยก่อนนำระบบขึ้นใช้งานจริง บัญชีผู้ดูแลจะสร้างจาก `ADMIN_EMAIL` และ `ADMIN_PASSWORD` เฉพาะตอนที่ยังไม่มีบัญชีผู้ดูแลในฐานข้อมูล

## ตั้งค่าการเชื่อมต่อเสริม

สร้างไฟล์ `.env` ที่ root ของโปรเจกต์ แล้วกำหนดค่าที่ต้องการใช้งาน ตัวอย่าง:

```dotenv
SECRET_KEY=replace-with-a-long-random-secret
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=replace-with-a-strong-password
DEMO_MODE=0
PROMPTPAY_ID=your-promptpay-number-or-id

# ตรวจสอบสลิป
SLIPOK_API_KEY=
# SLIPOK_URL=https://api.slipok.com/api/line/apikey

# ส่งอีเมล OTP สำหรับรีเซ็ตรหัสผ่าน
GMAIL_USER=
GMAIL_APP_PASS=

# แจ้งเตือนผ่าน LINE
LINE_CHANNEL_ACCESS_TOKEN=
LINE_ADMIN_USER_ID=
```

- `DEMO_MODE=1` เปิดปุ่มจำลองการชำระเงินสำเร็จสำหรับทดสอบ; ควรตั้งเป็น `0` ในระบบจริง
- การตรวจสอบสลิปต้องมี `SLIPOK_API_KEY` และการสร้าง QR ใช้ `PROMPTPAY_ID`
- การส่งอีเมลต้องตั้งค่า Gmail SMTP ให้พร้อมใช้งาน
- การแจ้งเตือน LINE ต้องมี Channel Access Token; การแจ้งเตือนผู้ดูแลต้องตั้ง `LINE_ADMIN_USER_ID` และตั้งค่า token/เปิดใช้งานจากหน้าผู้ดูแลด้วย
- เก็บ `.env` และ secret ทั้งหมดไว้นอก Git ห้ามใช้ค่าทดสอบหรือรหัสผ่านเริ่มต้นใน production

## ข้อมูลตัวอย่าง

หากต้องการข้อมูลสมาชิก ประวัติการซัก และรายการธุรกรรมตัวอย่าง:

```bash
python seed_demo.py
```

บัญชีตัวอย่างใช้รหัสผ่าน `demo1234` ส่วนการล้างข้อมูลตัวอย่างเดิมแล้วสร้างใหม่ ใช้:

```bash
python seed_demo.py --reset
```

## โครงสร้างโปรเจกต์

```text
.
├── app.py                 # Flask routes, API และการเชื่อมต่อฐานข้อมูล
├── smartlaundry.db        # SQLite database (สร้างและใช้งานในเครื่อง)
├── seed_demo.py           # สร้างข้อมูลตัวอย่าง
├── *.html                 # หน้าเว็บผู้ใช้
├── admin/                 # หน้าจัดการสำหรับผู้ดูแล
├── template/              # CSS ของหน้าเว็บ
├── images/                # รูปภาพและโลโก้
└── requirements.txt       # Python dependencies
```

## การนำขึ้นใช้งาน

สำหรับ production ให้ใช้ WSGI server แทน development server ของ Flask ตัวอย่าง:

```bash
gunicorn --bind 0.0.0.0:8000 app:app
```

กำหนด environment variables ให้ครบ ใช้ HTTPS และตรวจสอบให้แน่ใจว่าที่เก็บ `smartlaundry.db` มี persistence เพราะข้อมูลสมาชิกและธุรกรรมเก็บอยู่ใน SQLite ในเครื่อง นอกจากนี้ workflow ใน GitHub Actions จะทำงานเมื่อมีการ push ไปยัง branch `main`
