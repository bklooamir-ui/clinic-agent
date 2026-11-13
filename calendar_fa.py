"""تاریخ و ساعت ایران (تقویم شمسی) و نوبت‌دهی. فقط با کتابخانه‌ی استاندارد.

ساعت ایران از سال ۱۴۰۱ ساعت تابستانی ندارد و ثابت UTC+03:30 است؛ پس به پایگاه منطقه‌ی زمانی
سیستم (که در ویندوز پیش‌فرض نیست) نیازی نداریم و روی هر سرور (حتی با ساعت UTC) درست کار می‌کند.
"""
from datetime import datetime, timedelta, timezone

IRAN = timezone(timedelta(hours=3, minutes=30))
WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]  # به ترتیب weekday() پایتون
MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def now():
    return datetime.now(IRAN)


def fa_digits(x):
    return str(x).translate(_FA)


def to_jalali(gy, gm, gd):
    """تبدیل تاریخ میلادی به شمسی (الگوریتم استاندارد jdf)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def date_fa(dt):
    jy, jm, jd = to_jalali(dt.year, dt.month, dt.day)
    return f"{WEEKDAYS[dt.weekday()]} {fa_digits(jd)} {MONTHS[jm - 1]} {fa_digits(jy)}"


def time_fa(dt):
    return fa_digits(dt.strftime("%H:%M"))


def stamp(dt=None):
    """رشته‌ی خوانا برای نمایش: «چهارشنبه ۲۰ آبان ۱۴۰۵، ۱۴:۰۵»"""
    dt = (dt or now()).astimezone(IRAN)
    return f"{date_fa(dt)}، {time_fa(dt)}"


def _hm(s):
    h, m = s.split(":")
    return int(h), int(m)


def slots_for_day(schedule, day):
    """همه‌ی نوبت‌های یک روز (day از نوع date) طبق برنامه‌ی پزشک؛ جمعه و روزهای غیرکاری خالی است."""
    if day.weekday() not in schedule["days"]:
        return []
    (sh, sm), (eh, em) = _hm(schedule["start"]), _hm(schedule["end"])
    t = datetime(day.year, day.month, day.day, sh, sm, tzinfo=IRAN)
    end = datetime(day.year, day.month, day.day, eh, em, tzinfo=IRAN)
    step, out = timedelta(minutes=schedule["slot"]), []
    while t + step <= end:
        out.append(t)
        t += step
    return out


def next_slot(schedule, taken, now_dt=None, lead_min=30, after=None, horizon_days=21, busy=None):
    """اولین نوبت آزاد: حداقل lead_min دقیقه بعد از «الان»، و (اگر داده شد) بعد از after.
    busy: تابع اختیاری که برای نوبت‌های «پرشده‌ی فرضی» True برمی‌گرداند."""
    now_dt = (now_dt or now()).astimezone(IRAN)
    earliest = now_dt + timedelta(minutes=lead_min)
    if after and after > earliest:
        earliest = after
    for i in range(horizon_days + 1):
        day = (now_dt + timedelta(days=i)).date()
        for s in slots_for_day(schedule, day):
            if s >= earliest and s.isoformat() not in taken and not (busy and busy(s)):
                return s
    return None


def describe(start, schedule):
    return {"iso": start.isoformat(), "date": date_fa(start), "time": time_fa(start),
            "text": f"{date_fa(start)} ساعت {time_fa(start)}", "minutes": schedule["slot"]}
