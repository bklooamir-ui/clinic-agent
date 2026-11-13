"""اعلان فرم‌های جدید به صاحب پروژه از طریق ربات پیام‌رسان (اختیاری). فقط کتابخانه‌ی استاندارد.

متغیرهای محیطی:
  NOTIFY_BOT_TOKEN  توکن ربات (از BotFather در تلگرام، یا ربات بله)
  NOTIFY_CHAT_ID    شناسه‌ی چت شما با ربات
  NOTIFY_API_BASE   پیش‌فرض https://api.telegram.org  (برای بله: https://tapi.bale.ai)
اگر تنظیم نباشند، هیچ کاری انجام نمی‌شود.
"""
import json
import os
import threading
import urllib.request

STATUS_FA = {"emergency": "اورژانسی ⚠", "referred": "نیاز به ویزیت حضوری", "pending_doctor": "در انتظار تأیید پزشک"}


def enabled():
    return bool(os.environ.get("NOTIFY_BOT_TOKEN") and os.environ.get("NOTIFY_CHAT_ID"))


def format_case(case, doctor_name):
    p = case["patient"]
    lines = [f"🩺 فرم جدید برای {doctor_name}", f"زمان: {case['created']}",
             f"بیمار: {p.get('name') or 'بدون نام'} ({p.get('age')} ساله)",
             f"وضعیت: {STATUS_FA.get(case['status'], case['status'])}"]
    if case.get("appointment"):
        lines.append(f"وقت ویزیت: {case['appointment']['text']}")
    if case.get("payment"):
        lines.append(f"پرداخت آزمایشی: {case['payment']['amount']:,} تومان، کد {case['payment']['ref']}")
    lines.append(f"شرح حال: {case['symptoms']}")
    for a in case.get("answers_display", [])[:25]:
        lines.append(f"• {a['label']}: {a['value']}")
    if case.get("images"):
        lines.append(f"📷 {len(case['images'])} تصویر (در تب مدیر دیده می‌شود)")
    return "\n".join(lines)[:3800]


def _send(text):
    base = os.environ.get("NOTIFY_API_BASE", "https://api.telegram.org").rstrip("/")
    url = f"{base}/bot{os.environ['NOTIFY_BOT_TOKEN']}/sendMessage"
    body = json.dumps({"chat_id": os.environ["NOTIFY_CHAT_ID"], "text": text}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass  # اعلان نباید ثبت پرونده را خراب کند


def notify_case(case, doctor_name):
    """در پس‌زمینه می‌فرستد تا پاسخ بیمار کند نشود."""
    if enabled():
        threading.Thread(target=_send, args=(format_case(case, doctor_name),), daemon=True).start()
