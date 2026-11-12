"""سرور دمو (چندپزشکی): بیمار، چهار پزشک، گفتگو و داروخانه‌ی شبیه‌سازی‌شده.
فقط با کتابخانه‌ی استاندارد پایتون.

اجرا:  python3 server.py   →   http://127.0.0.1:8000
"""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import agent
import calendar_fa
import notify
import tools

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE, "data"))  # برای دیسک پایدار می‌توان تغییر داد
STORE_PATH = os.path.join(DATA_DIR, "store.json")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
MAX_CASES = int(os.environ.get("MAX_CASES", "500"))          # سقف کل پرونده‌ها (جلوگیری از پر شدن دیسک)
RATE_INTAKE = int(os.environ.get("RATE_INTAKE", "60"))        # ثبت بیمار در ساعت برای هر IP
MAX_AUTH_FAILS = int(os.environ.get("MAX_AUTH_FAILS", "10"))  # ورود ناموفق پزشک در ۱۰ دقیقه برای هر IP
RATE_ASSIST = int(os.environ.get("RATE_ASSIST", "30"))        # سؤال از دستیار پزشکی در ساعت برای هر IP
ASSIST_PATH = os.path.join(DATA_DIR, "assistant_log.json")
ASSIST_LOG = []
IMG_DIR = os.path.join(BASE, "images")
MAX_BODY = 8_000_000
MAX_IMG = 2_500_000
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # بدون حروف شبیه هم (0/O و 1/I)
CHAT_OPEN = ("approved", "accepted", "dispensed")
OUTCOME_STATUS = {"emergency": "emergency", "see_doctor": "referred",
                  "draft": "pending_doctor", "plan": "pending_doctor", "summary": "pending_doctor"}
IMG_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml"}

LOCK = threading.Lock()
CASES = {}
DOCTORS = {d["id"]: d for d in tools.DOCTORS}

# رمز هر پزشک را می‌توان با متغیر محیطی عوض کرد: PIN_SOHRABI, PIN_MORADI, PIN_MOUSAVI, PIN_KARAMATI
DEMO_PINS = True
for _d in DOCTORS.values():
    _p = os.environ.get("PIN_" + _d["id"].upper())
    if _p:
        _d["pin"], DEMO_PINS = _p, False

# رمز مدیر (دیدن همه‌ی پرونده‌ها). روی سرور عمومی باید با متغیر ADMIN_PIN تنظیم شود؛ وگرنه غیرفعال است.
ADMIN_PIN = os.environ.get("ADMIN_PIN") or ("9999" if DEMO_PINS else None)

# ---- قفل دسترسی (اختیاری): اگر OWNER_KEY تنظیم شود، سایت فقط با لینک مالک یا لینک مهمانِ موقت باز می‌شود
OWNER_KEY = os.environ.get("OWNER_KEY", "").strip()
GATE = bool(OWNER_KEY)
GUESTS = {}  # توکن -> {"minutes", "created", "first", "sess"}
GUEST_UNUSED_TTL = 3600  # لینکی که هرگز باز نشود بعد از ۱ ساعت باطل می‌شود
MAX_LIVE_LINKS = 20


def _now():
    return time.time()


def _owner_cookie():
    return hashlib.sha256(("owner:" + OWNER_KEY).encode()).hexdigest()[:40]


def _guest_state(tok):
    """وضعیت لینک مهمان: unused / active / expired / unknown و ثانیه‌ی باقی‌مانده."""
    g = GUESTS.get(tok)
    if not g:
        return "unknown", 0
    t = _now()
    if g["first"] is None:
        left = g["created"] + GUEST_UNUSED_TTL - t
        return ("unused", int(left)) if left > 0 else ("expired", 0)
    left = g["first"] + g["minutes"] * 60 - t
    return ("active", int(left)) if left > 0 else ("expired", 0)


_HITS = {}  # (نام سطل, ip) -> زمان رخدادها


def _hit(bucket, ip, window):
    """ثبت یک رخداد و برگرداندن تعداد رخدادهای داخل بازه‌ی زمانی (ثانیه)."""
    now = time.time()
    lst = [t for t in _HITS.get((bucket, ip), []) if now - t < window]
    lst.append(now)
    _HITS[(bucket, ip)] = lst
    return len(lst)


def _count(bucket, ip, window):
    now = time.time()
    return len([t for t in _HITS.get((bucket, ip), []) if now - t < window])


# ---------------------------------------------------------------- ذخیره‌سازی
def _load():
    if os.path.exists(STORE_PATH):
        try:
            with open(STORE_PATH, encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    if "doctor_id" in v:  # پرونده‌های نسخه‌ی قدیمی (تک‌پزشکی) نادیده گرفته می‌شوند
                        CASES[k] = v
        except Exception:
            pass


def _save():
    try:
        with open(STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(CASES, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _load_assist():
    try:
        with open(ASSIST_PATH, encoding="utf-8") as f:
            ASSIST_LOG[:] = json.load(f)[-200:]
    except Exception:
        pass


def _save_assist():
    try:
        with open(ASSIST_PATH, "w", encoding="utf-8") as f:
            json.dump(ASSIST_LOG, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _new_code(kind):
    while True:
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        code = raw[:4] + "-" + raw[4:] if kind == "prescription" else "VIS-" + raw[:6]
        if not any(c.get("code") == code for c in CASES.values()):
            return code


def _norm_code(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _add_chat(c, who, text):
    c.setdefault("chat", []).append({"from": who, "text": text, "ts": calendar_fa.now().strftime("%H:%M")})


# ---------------------------------------------------------------- پاک‌سازی ورودی
def _num(v, cast=float):
    try:
        return cast(str(v).strip()) if str(v).strip() != "" else None
    except ValueError:
        return None


def _clean_patient(d):
    sex = d.get("sex") if d.get("sex") in ("male", "female") else ""
    return {
        "name": str(d.get("name", "")).strip()[:60],
        "age": _num(d.get("age"), int),
        "sex": sex,
        "weight": _num(d.get("weight")),
        "pregnant": sex == "female" and str(d.get("pregnant", "")).strip().lower() in ("1", "true", "yes", "بله"),
        "allergies": str(d.get("allergies", ""))[:300],
        "current_meds": str(d.get("current_meds", ""))[:300],
        "conditions": str(d.get("conditions", ""))[:300],
        "duration_days": _num(d.get("duration_days"), int),
        "severity": _num(d.get("severity"), int),
        "temperature": _num(d.get("temperature")),
    }


def _eqs(x):
    return x if isinstance(x, list) else [x]


def _visible(f, vals):
    si = f.get("show_if")
    return True if not si else vals.get(si["field"], "") in _eqs(si["equals"])


def _label_of(f, v):
    if f.get("type") == "scale":
        return f"{v} از ۱۰"
    for o in f.get("options", []):
        if isinstance(o, dict) and o["v"] == v:
            return o["l"]
    if f.get("bind") == "sex":
        return v
    return v


def _sniff(b):
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg", ".jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", ".png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def _decode_images(lst):
    """خروجی: (فهرست (bytes, mime, ext) یا None، پیام خطا)"""
    if not lst:
        return [], None
    if not isinstance(lst, list):
        return None, "تصویر نامعتبر است"
    if len(lst) > 3:
        return None, "حداکثر ۳ تصویر مجاز است"
    out = []
    for s in lst:
        if not isinstance(s, str) or not s.startswith("data:image/") or "," not in s:
            return None, "تصویر نامعتبر است"
        try:
            raw = base64.b64decode(s.split(",", 1)[1], validate=True)
        except Exception:
            return None, "تصویر نامعتبر است"
        if len(raw) > MAX_IMG:
            return None, "حجم یکی از تصویرها زیاد است"
        t = _sniff(raw)
        if not t:
            return None, "فقط تصویر JPG، PNG یا WEBP مجاز است"
        out.append((raw, t[0], t[1]))
    return out, None


# ---------------------------------------------------------------- نماها
def public_doctor(d):
    return {k: v for k, v in d.items() if k != "pin"}


def public_case(c, include_trace=True):
    """نمای قابل‌نمایش به بیمار (کد فقط بعد از تأیید یا پذیرش پزشک)."""
    out = {k: c.get(k) for k in ("id", "status", "outcome", "message", "mode", "created", "doctor_note", "routing", "code_type", "appointment")}
    d = DOCTORS.get(c.get("doctor_id"))
    out["doctor"] = ({"id": d["id"], "name": d["name"], "specialty": d["specialty"], "color": d["color"],
                      "soft": d["soft"], "style": d["style"]} if d else None)
    out["draft"] = c.get("draft")
    out["approved_drugs"] = c.get("approved_drugs")
    out["code"] = c.get("code") if c.get("status") in CHAT_OPEN else None
    out["chat"] = c.get("chat", [])
    out["image_count"] = len(c.get("images", []))
    if include_trace:
        out["trace"] = c.get("trace", [])
    return out


def _taken(doctor_id, exclude=None):
    """نوبت‌های رزروشده‌ی یک پزشک (باید داخل LOCK صدا زده شود)."""
    return {c["appointment"]["iso"] for c in CASES.values()
            if c.get("appointment") and c["doctor_id"] == doctor_id and c["id"] != exclude and c["status"] != "rejected"}


def _book(c, after=None):
    """اولین وقت آزاد پزشک را (بر اساس ساعت واقعی ایران) برای این پرونده رزرو می‌کند. داخل LOCK صدا زده شود."""
    d = DOCTORS[c["doctor_id"]]
    slot = calendar_fa.next_slot(d["schedule"], _taken(d["id"], c["id"]), after=after)
    c["appointment"] = calendar_fa.describe(slot, d["schedule"]) if slot else None
    return c["appointment"]


GUEST_PAGE = """<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ورود مهمان</title><body style="font-family:Tahoma,sans-serif;background:#f3f6f7;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0">
<div style="background:#fff;border-radius:14px;padding:28px;max-width:440px;text-align:center;box-shadow:0 2px 12px #0001">
<h2 style="margin-top:0">کلینیک هوشمند (نسخه‌ی آزمایشی)</h2>
<p>دسترسی شما <b>__MIN__ دقیقه</b> است و با زدن دکمه‌ی زیر شروع می‌شود. بعد از پایان، سایت برای شما بسته می‌شود.</p>
<button id="b" style="font:inherit;padding:10px 26px;border:0;border-radius:10px;background:#12343b;color:#fff;cursor:pointer;font-size:16px">شروع و ورود به سایت</button>
<p id="e" style="color:#b91c1c"></p></div>
<script>document.getElementById('b').onclick=async()=>{try{const r=await fetch('/api/guest/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:'__TOK__'})});if(r.ok){location.replace('/')}else{const j=await r.json().catch(()=>({}));document.getElementById('e').textContent=j.error||'این لینک معتبر نیست'}}catch(x){document.getElementById('e').textContent='خطا در ارتباط'}}</script></body></html>"""

GATE_PAGE = """<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>دسترسی ندارید</title><body style="font-family:Tahoma,sans-serif;background:#f3f6f7;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0">
<div style="background:#fff;border-radius:14px;padding:28px;max-width:420px;text-align:center;box-shadow:0 2px 12px #0001">
<h2 style="margin-top:0">دسترسی ندارید</h2><p>این لینک منقضی شده یا معتبر نیست. برای ورود، از صاحب سایت لینک تازه بگیرید.</p></div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    # ---------- کمکی
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _raw(self, code, ctype, body, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return {}

    def _ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")  # پشت پروکسی Render
        return fwd.split(",")[0].strip() if fwd else self.client_address[0]

    def _doctor(self):
        ip = self._ip()
        if _count("authfail", ip, 600) >= MAX_AUTH_FAILS:
            return None  # قفل موقت بعد از تلاش‌های ناموفق زیاد
        d = DOCTORS.get(self.headers.get("X-Doctor-Id", ""))
        raw = self.headers.get("X-Doctor-Pin", "")
        if d and secrets.compare_digest(raw.encode("utf-8", "ignore"), d["pin"].encode()):
            return d
        if raw:
            _hit("authfail", ip, 600)
        return None

    def _admin(self):
        ip = self._ip()
        if not ADMIN_PIN or _count("authfail", ip, 600) >= MAX_AUTH_FAILS:
            return False
        raw = self.headers.get("X-Admin-Pin", "")
        if secrets.compare_digest(raw.encode("utf-8", "ignore"), ADMIN_PIN.encode()):
            return True
        if raw:
            _hit("authfail", ip, 600)
        return False

    # ---------- قفل دسترسی
    def _access(self):
        """'owner' یا 'guest' یا None (طبق کوکی)."""
        if not GATE:
            return "owner"
        try:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            v = c["acc"].value if "acc" in c else ""
        except Exception:
            return None
        if v and secrets.compare_digest(v.encode(), _owner_cookie().encode()):
            return "owner"
        parts = v.split(".")
        if len(parts) == 3 and parts[0] == "g":
            g = GUESTS.get(parts[1])
            if g and g.get("sess") and secrets.compare_digest(parts[2].encode(), g["sess"].encode()) \
                    and _guest_state(parts[1])[0] == "active":
                return "guest"
        return None

    def _set_cookie_redirect(self, value, max_age):
        self.send_response(302)
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        self.send_header("Set-Cookie", f"acc={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}")
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _try_link(self, qs):
        """?k=کلید مالک یا ?g=لینک مهمان را می‌پذیرد. True یعنی پاسخ (ریدایرکت) فرستاده شد."""
        k = (qs.get("k") or [""])[0]
        if k:
            if secrets.compare_digest(k.encode("utf-8", "ignore"), OWNER_KEY.encode()):
                self._set_cookie_redirect(_owner_cookie(), 30 * 86400)
                return True
            _hit("authfail", self._ip(), 600)
            return False
        tok = (qs.get("g") or [""])[0]
        if tok:
            with LOCK:
                g = GUESTS.get(tok)
                st, left = _guest_state(tok)
                if g and st == "unused":
                    # فقط صفحه‌ی «شروع» نشان داده می‌شود؛ شمارش با زدن دکمه شروع می‌شود (پیش‌نمایش پیام‌رسان‌ها شمارش را نمی‌سوزاند)
                    page = GUEST_PAGE.replace("__TOK__", tok).replace("__MIN__", str(g["minutes"]))
                    self._raw(200, "text/html; charset=utf-8", page.encode("utf-8"))
                    return True
                mine = False
                if g and st == "active" and g.get("sess"):
                    try:
                        c = SimpleCookie(self.headers.get("Cookie", ""))
                        mine = ("acc" in c and c["acc"].value == f"g.{tok}.{g['sess']}")
                    except Exception:
                        mine = False
                if g and st == "active" and mine:
                    self._set_cookie_redirect(f"g.{tok}.{g['sess']}", max(left, 1))
                    return True
            _hit("authfail", self._ip(), 600)
        return False

    def _guest_start(self):
        data = self._body() or {}
        tok = str(data.get("token", ""))
        with LOCK:
            g = GUESTS.get(tok)
            if not g or _guest_state(tok)[0] != "unused":
                _hit("authfail", self._ip(), 600)
                return self._json(403, {"error": "این لینک معتبر نیست یا قبلاً استفاده شده است"})
            g["first"], g["sess"] = _now(), secrets.token_hex(8)
            left = _guest_state(tok)[1]
            val = f"g.{tok}.{g['sess']}"
        body = b'{"ok":true}'
        self.send_response(200)
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        self.send_header("Set-Cookie", f"acc={val}; Path=/; Max-Age={max(left, 1)}; HttpOnly; SameSite=Lax{secure}")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _deny(self, path, api_gate_ok=False):
        if path == "/api/doctors" and api_gate_ok:  # سلامت‌سنجی Render همچنان ۲۰۰ می‌گیرد
            return self._json(200, {"gated": True})
        if path.startswith("/api/"):
            return self._json(403, {"error": "این لینک منقضی شده یا دسترسی ندارید", "code": "access"})
        return self._raw(403, "text/html; charset=utf-8", GATE_PAGE.encode("utf-8"))

    # ---------- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        lite = "lite=1" in self.path
        if GATE:
            if path in ("/", "/index.html") and parsed.query and self._try_link(parse_qs(parsed.query)):
                return
            if not self._access():
                return self._deny(path, api_gate_ok=True)

        if path in ("/", "/index.html"):
            with open(os.path.join(BASE, "index.html"), "rb") as f:
                return self._raw(200, "text/html; charset=utf-8", f.read())

        if path.startswith("/img/"):
            name = os.path.basename(path[5:])
            ext = os.path.splitext(name)[1].lower()
            fp = os.path.join(IMG_DIR, name)
            if ext in IMG_TYPES and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    return self._raw(200, IMG_TYPES[ext], f.read(), cache="public, max-age=300")
            return self._json(404, {"error": "یافت نشد"})

        if path == "/api/doctors":
            with LOCK:
                docs = []
                for d in tools.DOCTORS:
                    pd = public_doctor(d)
                    s = calendar_fa.next_slot(d["schedule"], _taken(d["id"]))
                    pd["next_slot"] = calendar_fa.describe(s, d["schedule"])["text"] if s else None
                    docs.append(pd)
            return self._json(200, {"doctors": docs, "demo_pins": DEMO_PINS,
                                    "assistant": agent.llm_available(), "now": calendar_fa.stamp()})

        # ---------- پنل مدیر: همه‌ی پرونده‌ها
        if path == "/api/admin/links":
            if not self._admin():
                return self._json(401, {"error": "رمز مدیر نادرست است یا تنظیم نشده"})
            with LOCK:
                out = []
                for tok, g in sorted(GUESTS.items(), key=lambda kv: -kv[1]["created"]):
                    st, left = _guest_state(tok)
                    out.append({"state": st, "left": left, "minutes": g["minutes"],
                                "path": f"/?g={tok}" if st in ("unused", "active") else ""})
            return self._json(200, {"gate": GATE, "links": out[:30]})

        if path == "/api/admin/cases":
            if not self._admin():
                return self._json(401, {"error": "رمز مدیر نادرست است یا تنظیم نشده"})
            with LOCK:
                items = sorted(CASES.values(), key=lambda c: c["created_ts"], reverse=True)
                return self._json(200, {"total": len(items), "assistant": list(reversed(ASSIST_LOG))[:100], "cases": [
                    dict(public_case(c, include_trace=False), patient=c["patient"], symptoms=c["symptoms"],
                         answers_display=c.get("answers_display", []), code=c.get("code"),
                         risk_reasons=c.get("risk_reasons", []))
                    for c in items]})

        m = re.fullmatch(r"/api/admin/cases/([0-9a-f]{8})/images/(\d)", path)
        if m:
            if not self._admin():
                return self._json(401, {"error": "رمز مدیر نادرست است یا تنظیم نشده"})
            with LOCK:
                c = CASES.get(m.group(1))
                imgs = c.get("images", []) if c else []
                idx = int(m.group(2))
                info = imgs[idx] if idx < len(imgs) else None
            if not info:
                return self._json(404, {"error": "یافت نشد"})
            fp = os.path.join(UPLOAD_DIR, os.path.basename(info["file"]))
            if not os.path.isfile(fp):
                return self._json(404, {"error": "فایل یافت نشد"})
            with open(fp, "rb") as f:
                return self._raw(200, info["mime"], f.read(), cache="private, max-age=60")

        m = re.fullmatch(r"/api/cases/([0-9a-f]{8})", path)
        if m:
            with LOCK:
                c = CASES.get(m.group(1))
                return self._json(200, public_case(c, include_trace=not lite)) if c else self._json(404, {"error": "یافت نشد"})

        m = re.fullmatch(r"/api/cases/([0-9a-f]{8})/images/(\d)", path)
        if m:
            doc = self._doctor()
            if not doc:
                return self._json(401, {"error": "ورود پزشک لازم است"})
            with LOCK:
                c = CASES.get(m.group(1))
                if not c:
                    return self._json(404, {"error": "یافت نشد"})
                if c["doctor_id"] != doc["id"]:
                    return self._json(403, {"error": "این پرونده متعلق به پزشک دیگری است"})
                idx = int(m.group(2))
                imgs = c.get("images", [])
                if idx >= len(imgs):
                    return self._json(404, {"error": "یافت نشد"})
                info = imgs[idx]
            fp = os.path.join(UPLOAD_DIR, os.path.basename(info["file"]))
            if not os.path.isfile(fp):
                return self._json(404, {"error": "فایل یافت نشد"})
            with open(fp, "rb") as f:
                return self._raw(200, info["mime"], f.read(), cache="private, max-age=60")

        if path == "/api/cases":
            doc = self._doctor()
            if not doc:
                return self._json(401, {"error": "رمز یا پزشک نادرست است"})
            with LOCK:
                items = sorted((c for c in CASES.values() if c["doctor_id"] == doc["id"]),
                               key=lambda c: c["created_ts"], reverse=True)
                return self._json(200, {"doctor": public_doctor(doc), "cases": [
                    dict(public_case(c), patient=c["patient"], symptoms=c["symptoms"],
                         answers_display=c.get("answers_display", []),
                         risk_reasons=c.get("risk_reasons", []), safety=c.get("safety", []),
                         rejected_drugs=c.get("rejected_drugs", []), chat_alert=c.get("chat_alert", False))
                    for c in items]})

        m = re.fullmatch(r"/api/pharmacy/([A-Za-z0-9\- ]{4,20})", path)
        if m:
            want = _norm_code(m.group(1))
            with LOCK:
                for c in CASES.values():
                    if c.get("code") and _norm_code(c["code"]) == want:
                        if c.get("code_type") != "prescription":
                            return self._json(404, {"error": "این کد، «کد پذیرش ویزیت» است و نسخه‌ی دارویی نیست"})
                        p = c["patient"]
                        d = DOCTORS.get(c["doctor_id"], {})
                        return self._json(200, {
                            "code": c["code"], "status": c["status"],
                            "patient": {"name": p["name"], "age": p["age"]},
                            "doctor": {"name": d.get("name", ""), "specialty": d.get("specialty", "")},
                            "drugs": c["approved_drugs"], "doctor_note": c.get("doctor_note", ""),
                            "approved_at": c.get("approved_at"), "dispensed_at": c.get("dispensed_at"),
                        })
            return self._json(404, {"error": "نسخه‌ای با این کد پیدا نشد"})

        self._json(404, {"error": "یافت نشد"})

    # ---------- POST
    def do_POST(self):
        path = urlparse(self.path).path
        if GATE and path == "/api/guest/start":  # تنها مسیر POST که بدون دسترسی باز است
            return self._guest_start()
        if GATE and not self._access():
            return self._deny(path)
        data = self._body()
        if data is None:
            return self._json(413, {"error": "حجم درخواست زیاد است؛ تصویرها را کوچک‌تر کنید"})

        # ---------- ساخت لینک مهمانِ موقت (فقط مدیر؛ فقط وقتی OWNER_KEY تنظیم باشد)
        if path == "/api/admin/links":
            if not self._admin():
                return self._json(401, {"error": "رمز مدیر نادرست است یا تنظیم نشده"})
            if not GATE:
                return self._json(409, {"error": "برای این قابلیت باید متغیر OWNER_KEY روی سرور تنظیم شود"})
            try:
                minutes = int(data.get("minutes", 5))
            except (TypeError, ValueError):
                minutes = 5
            minutes = max(1, min(minutes, 60))
            with LOCK:
                live = [t for t in GUESTS if _guest_state(t)[0] in ("unused", "active")]
                for t in [t for t in GUESTS if _guest_state(t)[0] == "expired"][:-10]:
                    del GUESTS[t]  # فقط ۱۰ لینک منقضی آخر نگه داشته می‌شود
                if len(live) >= MAX_LIVE_LINKS:
                    return self._json(409, {"error": "تعداد لینک‌های فعال زیاد است"})
                tok = secrets.token_urlsafe(12)
                GUESTS[tok] = {"minutes": minutes, "created": _now(), "first": None, "sess": None}
            return self._json(200, {"path": f"/?g={tok}", "minutes": minutes})

        # ---------- دستیار پزشکی (پرسش و پاسخ آزاد؛ نیازمند کلید API)
        if path == "/api/assistant":
            if _hit("assist", self._ip(), 3600) > RATE_ASSIST:
                return self._json(429, {"error": "تعداد سؤال‌ها زیاد است؛ کمی بعد دوباره تلاش کنید"})
            raw = data.get("messages")
            clean = []
            for m in (raw[-10:] if isinstance(raw, list) else []):
                if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
                    t = str(m.get("content", "")).strip()[:1000]
                    if t:
                        clean.append({"role": m["role"], "content": t})
            while clean and clean[0]["role"] != "user":
                clean.pop(0)
            if not clean or clean[-1]["role"] != "user":
                return self._json(400, {"error": "سؤال خود را بنویسید"})
            last, mode = clean[-1]["content"], "llm"
            flags = tools.scan_red_flags(last)
            if flags:  # گارد: علامت خطر → پیام اورژانسی ثابت، بدون پرسیدن از مدل
                mode = "emergency"
                reply = ("⚠ پیام شما شامل علائم هشداردهنده است (" + "؛ ".join(f["label"] for f in flags) + "). "
                         "لطفاً همین الان با اورژانس ۱۱۵ تماس بگیرید یا به نزدیک‌ترین مرکز درمانی مراجعه کنید. "
                         "این دستیار برای موارد اورژانسی مناسب نیست.")
            elif not agent.llm_available():
                return self._json(503, {"error": "دستیار پزشکی فعلاً غیرفعال است (کلید API روی سرور تنظیم نشده)"})
            else:
                try:
                    reply = agent.ask_assistant(clean)
                except Exception:
                    reply = ""
                if not reply:
                    return self._json(502, {"error": "پاسخی از مدل دریافت نشد؛ کمی بعد دوباره تلاش کنید"})
            with LOCK:
                ASSIST_LOG.append({"ts": calendar_fa.stamp(), "q": last, "a": reply, "mode": mode})
                del ASSIST_LOG[:-200]
                _save_assist()
            return self._json(200, {"reply": reply, "mode": mode})

        # ---------- ثبت بیمار
        if path == "/api/intake":
            if _hit("intake", self._ip(), 3600) > RATE_INTAKE:
                return self._json(429, {"error": "تعداد درخواست‌ها زیاد است؛ کمی بعد دوباره تلاش کنید"})
            if len(CASES) >= MAX_CASES:
                return self._json(503, {"error": "ظرفیت نسخه‌ی آزمایشی پر شده است"})
            doc = DOCTORS.get(str(data.get("doctor_id", "")))
            if not doc:
                return self._json(400, {"error": "پزشک نامعتبر است"})
            answers_in = data.get("answers") if isinstance(data.get("answers"), dict) else {}
            fields = doc["form"]["fields"]
            vals, display, extra = {}, [], []
            for f in fields:
                if not _visible(f, vals):
                    continue
                v = answers_in.get(f["id"], "")
                v = "" if v is None else str(v).strip()[:1500]
                if f.get("required") and not v:
                    return self._json(400, {"error": f"«{f['label']}» را پر کنید"})
                vals[f["id"]] = v
                if v and f.get("flag_text") and v in _eqs(f.get("flag_if")):
                    extra.append(f["flag_text"])
                if v and f.get("bind") != "symptoms":
                    display.append({"label": f["label"], "value": _label_of(f, v)})
            raw = {f["bind"]: vals[f["id"]] for f in fields
                   if f.get("bind") and f["id"] in vals and f["bind"] != "symptoms"}
            if doc["kind"] == "obgyn":
                raw["sex"] = "female"
            patient = _clean_patient(raw)
            symptoms = next((vals[f["id"]] for f in fields if f.get("bind") == "symptoms" and f["id"] in vals), "")
            if not symptoms:
                return self._json(400, {"error": "لطفاً شرح حال خود را بنویسید"})
            if patient["age"] is None or not (0 <= patient["age"] <= 120):
                return self._json(400, {"error": "سن را درست وارد کنید"})
            imgs, err = _decode_images(data.get("images"))
            if err:
                return self._json(400, {"error": err})

            ctx = {
                "patient": patient, "symptoms": ". ".join([symptoms] + extra), "answers": vals,
                "answers_display": display, "trace": [],
                "doctor": {"id": doc["id"], "name": doc["name"], "kind": doc["kind"],
                           "gender": doc["gender"], "specialty": doc["specialty"]},
            }
            agent.run_agent(ctx)
            cid = secrets.token_hex(4)
            saved = []
            for i, (blob, mime, ext) in enumerate(imgs):
                fn = f"{cid}_{i}{ext}"
                with open(os.path.join(UPLOAD_DIR, fn), "wb") as f:
                    f.write(blob)
                saved.append({"file": fn, "mime": mime})
            outcome = ctx["outcome"]["type"]
            case = {
                "id": cid, "created": calendar_fa.stamp(), "created_ts": time.time(),
                "doctor_id": doc["id"], "patient": patient, "symptoms": symptoms, "answers_display": display,
                "status": OUTCOME_STATUS[outcome], "outcome": ctx["outcome"], "message": ctx["message"],
                "mode": ctx["mode"], "flags": ctx.get("flags", []), "risk_reasons": ctx.get("risk_reasons", []),
                "safety": ctx.get("safety", []), "rejected_drugs": ctx.get("rejected_drugs", []),
                "draft": ctx.get("draft"), "routing": ctx.get("routing"), "trace": ctx["trace"],
                "images": saved, "chat": [],
            }
            with LOCK:
                if case["status"] in ("referred", "emergency"):  # ارجاع حضوری / اورژانس → نزدیک‌ترین وقت واقعی پزشک
                    _book(case)
                    if case["appointment"] and case["status"] == "emergency":
                        case["appointment"]["followup"] = True  # وقت پیگیری؛ جایگزین اقدام اورژانسی نیست
                CASES[cid] = case
                _save()
            notify.notify_case(case, doc["name"])
            return self._json(200, public_case(case))

        # ---------- تصمیم پزشک
        m = re.fullmatch(r"/api/cases/([0-9a-f]{8})/(approve|reject|accept)", path)
        if m:
            doc = self._doctor()
            if not doc:
                return self._json(401, {"error": "رمز یا پزشک نادرست است"})
            cid, action = m.groups()
            with LOCK:
                c = CASES.get(cid)
                if not c:
                    return self._json(404, {"error": "یافت نشد"})
                if c["doctor_id"] != doc["id"]:
                    return self._json(403, {"error": "این پرونده متعلق به پزشک دیگری است"})
                note = str(data.get("note", ""))[:500]
                if action == "accept":
                    if c["status"] != "referred":
                        return self._json(409, {"error": "این پرونده در وضعیت قابل‌پذیرش نیست"})
                    c["status"], c["code_type"] = "accepted", "visit"
                    c["code"] = _new_code("visit")
                    c["approved_drugs"] = []
                    c["doctor_note"] = note
                    c["approved_at"] = calendar_fa.stamp()
                    c["message"] = f"{doc['name']} پرونده‌ی شما را پذیرفت. این «کد پذیرش» شماست؛ از بخش گفتگو می‌توانید با ایشان در ارتباط باشید."
                    if not c.get("appointment"):
                        _book(c)
                    _add_chat(c, "system", "پرونده‌ی شما پذیرفته شد. سؤال یا توضیح بیشتر خود را همین‌جا بنویسید.")
                else:
                    if c["status"] != "pending_doctor":
                        return self._json(409, {"error": "این مورد دیگر در انتظار تأیید نیست"})
                    c["doctor_note"] = note
                    if action == "reject":
                        c["status"] = "rejected"
                        c["message"] = "پزشک پیش‌نویس را تأیید نکرد. لطفاً برای ادامه با پزشک صحبت کنید."
                    else:
                        kind = c["draft"].get("kind", "rx")
                        if kind == "rx":
                            ids = set(data.get("drug_ids", []))
                            # پزشک فقط می‌تواند از داروهای پیش‌نویس انتخاب کند (نه چیز جدید اضافه کند)
                            chosen = [d for d in c["draft"]["drugs"] if d["id"] in ids]
                            if not chosen:
                                return self._json(400, {"error": "حداقل یک دارو را انتخاب کنید یا نسخه را رد کنید"})
                            c["approved_drugs"] = chosen
                            c["code_type"] = "prescription"
                            c["message"] = f"{doc['name']} نسخه را تأیید کرد. این کد را به داروخانه بدهید."
                            _add_chat(c, "system", "نسخه‌ی شما تأیید شد. اگر درباره‌ی مصرف داروها سؤالی دارید همین‌جا بپرسید.")
                        else:
                            c["approved_drugs"] = []
                            c["code_type"] = "visit"
                            c["message"] = f"{doc['name']} پرونده‌ی شما را تأیید کرد. این «کد پذیرش» شماست؛ از بخش گفتگو می‌توانید با ایشان در ارتباط باشید."
                            _add_chat(c, "system", "پرونده‌ی شما تأیید شد. سؤال یا توضیح بیشتر خود را همین‌جا بنویسید.")
                        c["code"] = _new_code(c["code_type"])
                        c["status"] = "approved"
                        c["approved_at"] = calendar_fa.stamp()
                        if kind == "summary" and not c.get("appointment"):  # دندان‌پزشکی/زنان: وقت ویزیت
                            _book(c)
                if note and c["status"] in CHAT_OPEN:
                    _add_chat(c, "doctor", note)
                _save()
                return self._json(200, public_case(c, include_trace=False))

        # ---------- جابه‌جایی وقت (اولین وقت آزاد بعدی)
        m = re.fullmatch(r"/api/cases/([0-9a-f]{8})/reschedule", path)
        if m:
            with LOCK:
                c = CASES.get(m.group(1))
                if not c:
                    return self._json(404, {"error": "یافت نشد"})
                if not c.get("appointment") or c["status"] in ("rejected", "dispensed"):
                    return self._json(409, {"error": "برای این پرونده وقتی قابل جابه‌جایی نیست"})
                if c.get("resched", 0) >= 3:
                    return self._json(429, {"error": "حداکثر ۳ بار می‌توانید وقت را جابه‌جا کنید"})
                d = DOCTORS[c["doctor_id"]]
                cur = datetime.fromisoformat(c["appointment"]["iso"])
                slot = calendar_fa.next_slot(d["schedule"], _taken(d["id"], c["id"]), after=cur + timedelta(minutes=1))
                if not slot:
                    return self._json(409, {"error": "وقت خالی دیگری در ۳ هفته‌ی آینده نیست"})
                fu = c["appointment"].get("followup")
                c["appointment"] = calendar_fa.describe(slot, d["schedule"])
                if fu:
                    c["appointment"]["followup"] = True
                c["resched"] = c.get("resched", 0) + 1
                _save()
                return self._json(200, public_case(c, include_trace=False))

        # ---------- گفتگو
        m = re.fullmatch(r"/api/cases/([0-9a-f]{8})/chat", path)
        if m:
            as_doctor = data.get("as") == "doctor"
            doc = self._doctor() if as_doctor else None
            if as_doctor and not doc:
                return self._json(401, {"error": "رمز یا پزشک نادرست است"})
            text = str(data.get("text", "")).strip()[:500]
            if not text:
                return self._json(400, {"error": "پیام خالی است"})
            with LOCK:
                c = CASES.get(m.group(1))
                if not c:
                    return self._json(404, {"error": "یافت نشد"})
                if as_doctor and c["doctor_id"] != doc["id"]:
                    return self._json(403, {"error": "این پرونده متعلق به پزشک دیگری است"})
                if c["status"] not in CHAT_OPEN:
                    return self._json(403, {"error": "گفتگو بعد از تأیید یا پذیرش پزشک فعال می‌شود"})
                if len(c.get("chat", [])) >= 300:
                    return self._json(429, {"error": "سقف پیام‌های این گفتگو پر شده است"})
                if as_doctor:
                    _add_chat(c, "doctor", text)
                    c["chat_alert"] = False
                else:
                    _add_chat(c, "patient", text)
                    if tools.scan_red_flags(text):
                        _add_chat(c, "system", "⚠ پیام شما شامل علائم هشداردهنده است. گفتگو برای موارد اورژانسی نیست؛ "
                                               "فوراً به اورژانس مراجعه کنید یا با ۱۱۵ تماس بگیرید. این پیام برای پزشک هم علامت‌گذاری شد.")
                        c["chat_alert"] = True
                _save()
                return self._json(200, {"chat": c["chat"]})

        # ---------- داروخانه
        m = re.fullmatch(r"/api/pharmacy/([A-Za-z0-9\- ]{4,20})/dispense", path)
        if m:
            want = _norm_code(m.group(1))
            with LOCK:
                for c in CASES.values():
                    if c.get("code") and _norm_code(c["code"]) == want:
                        if c.get("code_type") != "prescription":
                            return self._json(404, {"error": "این کد، «کد پذیرش ویزیت» است و نسخه‌ی دارویی نیست"})
                        if c["status"] == "dispensed":
                            return self._json(409, {"error": "این نسخه قبلاً تحویل داده شده است"})
                        c["status"] = "dispensed"
                        c["dispensed_at"] = calendar_fa.stamp()
                        _save()
                        return self._json(200, {"status": "dispensed", "dispensed_at": c["dispensed_at"]})
            return self._json(404, {"error": "نسخه‌ای با این کد پیدا نشد"})

        self._json(404, {"error": "یافت نشد"})


def make_server(host="127.0.0.1", port=8000):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    _load()
    _load_assist()
    # روی Render متغیر PORT تنظیم است و باید روی همه‌ی آدرس‌ها گوش داد؛ لوکال فقط روی 127.0.0.1
    host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    mode = "LLM" if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("AGENT_MODE") != "demo" else "demo"
    print(f"http://{host}:{port}   (حالت ایجنت: {mode})", flush=True)
    if DEMO_PINS:
        print("رمزهای دمو: سهرابی 1111 | مرادی 2222 | مهرجو 3333 | کرامتی 4444", flush=True)
        print("رمز مدیر (تب «مدیر»): 9999", flush=True)
    else:
        print("رمز پزشکان از متغیرهای محیطی خوانده شد.", flush=True)
        if not ADMIN_PIN:
            print("هشدار: ADMIN_PIN تنظیم نشده؛ تب «مدیر» غیرفعال است.", flush=True)
    make_server(host, port).serve_forever()
