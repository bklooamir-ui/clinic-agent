"""ابزارهای ایجنت پیش‌غربالگری (نسخه‌ی چندپزشکی).

نکته‌ی مهم برای ارائه: «گاردهای ایمنی» اینجا در کد پیاده شده‌اند، نه فقط در پرامپت.
حتی اگر مدل زبانی اشتباه کند، ابزارها اجازه‌ی ساخت خروجی ناامن را نمی‌دهند.
"""
import json
import os
import re
import time

import calendar_fa

BASE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    with open(os.path.join(BASE, "data", name), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- متن‌کاوی ساده
_NEG = re.compile(r"^.{0,20}?(ندارم|ندارد|نداره|نیست|نداشتم|(?<!\S)نه(?!\S)|without|\bno\b|\bnot\b)")
_CLAUSE_SPLIT = re.compile(r"\s(?:ولی|اما|اگرچه|but)\s|[،,.;؛!؟?\n]")


def norm(text):
    s = (text or "").lower()
    s = s.replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    s = re.sub(r"[ً-ٟ]", "", s)
    s = s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    return re.sub(r"\s+", " ", s).strip()


def has_kw(text, kw, check_negation=False):
    """آیا کلیدواژه در متن (نرمال‌شده) هست؟ اختیاری: نفی ساده مثل «درد قفسه ندارم»."""
    text, kw = norm(text), norm(kw)
    start = 0
    while True:
        i = text.find(kw, start)
        if i < 0:
            return False
        if not check_negation:
            return True
        after = _CLAUSE_SPLIT.split(text[i + len(kw):])[0]  # فقط تا پایان همین جمله/بند
        if not _NEG.match(after):
            return True
        start = i + len(kw)


def any_kw(text, kws, check_negation=False):
    return [k for k in kws if has_kw(text, k, check_negation)]


# ---------------------------------------------------------------- داده‌ها
FORMULARY = _load("formulary.json")["drugs"]
_GUIDE = _load("guidelines.json")
CONDITIONS = {c["id"]: c for c in _GUIDE["conditions"]}
REDFLAGS = _load("redflags.json")
PHYSIO = _load("physio.json")["areas"]
DOCTORS = _load("doctors.json")["doctors"]

_OB_KW = ["بارداری", "باردار", "قاعدگی", "پریود", "زایمان", "جنین", "رحم", "تخمدان",
          "ترشحات واژینال", "ترشحات زنانه", "پاپ اسمیر"]
_MSK_KW = ["کمردرد", "کمر درد", "درد کمر", "گردن درد", "گردن‌درد", "درد گردن", "زانو", "شانه", "مچ",
           "دیسک", "سیاتیک", "کشیدگی", "رگ به رگ", "ستون فقرات", "back pain", "knee", "shoulder"]
_DENT_KW = ["دندان", "لثه", "عصب کشی", "عصب‌کشی", "پوسیدگی", "ارتودنسی", "دندان عقل", "جرم گیری", "جرم‌گیری"]

_KIND_REASON = {
    "obgyn": "علائم یا شرایط شما (مثل بارداری یا مشکلات زنان) به پزشک زنان و زایمان مربوط است.",
    "physio": "شکایت شما از نوع دردهای اسکلتی‌عضلانی است و فیزیوتراپیست برای آن مناسب‌تر است.",
    "dentist": "شکایت شما مربوط به دندان یا لثه است و دندان‌پزشک برای آن مناسب‌تر است.",
}


def _doctor(ctx):
    return ctx.get("doctor") or {"id": "", "name": "پزشک", "kind": "gp", "gender": "male", "specialty": "پزشک عمومی"}


# ---------------------------------------------------------------- ابزارها
def scan_red_flags(text):
    """تشخیص علائم خطر در یک متن (هم در فرم و هم در گفتگو استفاده می‌شود)."""
    flags = []
    for rf in REDFLAGS:
        if any_kw(text, rf["keywords"], check_negation=True):
            flags.append({"id": rf["id"], "label": rf["label"], "message": rf["message"]})
    return flags


def check_red_flags(ctx, symptom_text=None):
    """علائم خطرناک (اورژانسی) را بررسی می‌کند."""
    flags = scan_red_flags(symptom_text or ctx["symptoms"])
    ctx["flags"] = flags
    ctx["red_flag"] = bool(flags)
    return {"flagged": bool(flags), "flags": flags}


def suggest_doctor(ctx):
    """بررسی اینکه آیا پزشک دیگری برای این شکایت مناسب‌تر است (فقط پیشنهاد، نه اجبار)."""
    doc = _doctor(ctx)
    p = ctx["patient"]
    text = ctx["symptoms"]
    ob = bool(p.get("pregnant")) or bool(any_kw(text, _OB_KW, True))
    dent = bool(any_kw(text, _DENT_KW, True))
    msk = bool(any_kw(text, _MSK_KW, True))
    kind, want = doc["kind"], None
    if kind in ("gp", "physio", "dentist") and ob and kind != "dentist":
        want = "obgyn"
    elif kind in ("gp", "physio", "obgyn") and dent and not ob:
        want = "dentist"
    elif kind in ("gp", "obgyn") and msk and not ob and not dent:
        want = "physio"
    suggestion = None
    if want:
        cands = sorted((d for d in DOCTORS if d["kind"] == want), key=lambda d: d["gender"] != doc.get("gender"))
        if cands:
            c = cands[0]
            suggestion = {"suggested_doctor": {"id": c["id"], "name": c["name"], "specialty": c["specialty"]},
                          "reason": _KIND_REASON[want]}
    ctx["routing"] = suggestion
    return {"suggestion": suggestion}


def check_risk_factors(ctx):
    """عوامل خطر بیمار که تجویز یا مدیریت بدون ویزیت حضوری را ناممکن می‌کند."""
    p = ctx["patient"]
    kind = _doctor(ctx)["kind"]
    reasons = []
    age = p.get("age")
    if age is not None and age < 12 and kind != "dentist":
        reasons.append("بیمار زیر ۱۲ سال است؛ ارزیابی و دوزبندی کودکان نیازمند ویزیت حضوری است.")
    if age is not None and age >= 75:
        reasons.append("سن بالای ۷۵ سال؛ نیاز به ارزیابی دقیق‌تر پزشک.")
    if p.get("pregnant") and kind == "gp":
        reasons.append("بارداری؛ هر دارویی باید با نظر پزشک زنان یا متخصص باشد.")
    if kind == "gp" and (p.get("duration_days") or 0) >= 7:
        reasons.append("علائم بیش از ۷ روز طول کشیده است.")
    limit = 9 if kind == "physio" else 8
    if (p.get("severity") or 0) >= limit:
        reasons.append(f"شدت علائم بالاست ({limit} از ۱۰ یا بیشتر).")
    if (p.get("temperature") or 0) >= 39.5:
        reasons.append("تب بالای ۳۹٫۵ درجه.")
    ctx["risk_reasons"] = reasons
    return {"has_risk": bool(reasons), "reasons": reasons}


def search_guidelines(ctx, symptom_text=None):
    """جستجوی ساده در پایگاه راهنماهای درمانی (در نسخه‌ی کامل: RAG روی منابع معتبر)."""
    text = symptom_text or ctx["symptoms"]
    scored = []
    for c in CONDITIONS.values():
        hits = any_kw(text, c["keywords"], check_negation=True)
        if hits:
            scored.append((len(hits), c, hits))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        ctx["condition"] = None
        return {"matched": False, "message": "هیچ راهنمای مرتبطی برای این علائم در پایگاه یافت نشد."}
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        ctx["condition"] = None
        return {
            "matched": False,
            "ambiguous": True,
            "candidates": [s[1]["name_fa"] for s in scored[:3]],
            "message": "علائم با چند وضعیت به یک اندازه سازگار است؛ تصمیم باید با پزشک باشد.",
        }
    _, c, hits = scored[0]
    ctx["condition"] = c["id"]
    return {
        "matched": True,
        "condition_id": c["id"],
        "condition": c["name_fa"],
        "matched_keywords": hits,
        "candidate_drug_ids": c["drugs"],
        "advice": c["advice"],
    }


def _check_one(patient, drug_id):
    d = FORMULARY[drug_id]
    reasons, cautions = [], []
    age = patient.get("age")
    if age is not None and age < d["min_age"]:
        reasons.append("سن بیمار کمتر از حداقل سن مجاز این دارو است.")
    if any_kw(patient.get("allergies", ""), d["block_allergy"]):
        reasons.append("سابقه حساسیت به این دارو یا داروهای هم‌خانواده.")
    if any_kw(patient.get("conditions", ""), d["block_conditions"]):
        reasons.append("بیماری زمینه‌ای که مصرف این دارو را ممنوع یا پرخطر می‌کند.")
    if any_kw(patient.get("current_meds", ""), d["block_meds"]):
        reasons.append("تداخل دارویی مهم با داروهای فعلی بیمار.")
    if patient.get("pregnant"):
        reasons.append("بارداری.")
    for c in d["caution_meds"]:
        if any_kw(patient.get("current_meds", ""), c["keywords"]):
            cautions.append(c["text"])
    return {
        "drug_id": drug_id,
        "name_fa": d["name_fa"],
        "status": "blocked" if reasons else "ok",
        "reasons": reasons,
        "cautions": cautions,
    }


def check_drug_safety(ctx, drug_ids):
    """آلرژی، بیماری زمینه‌ای، تداخل دارویی، سن و بارداری را برای هر دارو بررسی می‌کند."""
    results = []
    for did in drug_ids:
        if did not in FORMULARY:
            results.append({"drug_id": did, "status": "unknown", "reasons": ["دارو در فهرست مجاز نیست."], "cautions": []})
        else:
            results.append(_check_one(ctx["patient"], did))
    ctx["safety"] = results
    return {"results": results}


def create_draft_prescription(ctx, drug_ids, rationale=""):
    """پیش‌نویس نسخه می‌سازد. فقط پیش‌نویس؛ کد فقط بعد از تأیید پزشک صادر می‌شود.

    گاردهای سخت (حتی اگر مدل اشتباه کند):
      - فقط برای پزشک عمومی (فیزیوتراپی، دندان‌پزشکی و زنان از این مسیر دارو نمی‌گیرند).
      - با علائم خطر یا عوامل خطر، پیش‌نویس ساخته نمی‌شود.
      - فقط داروهای راهنمای مطابق‌شده مجازند و داروی ناایمن رد می‌شود.
    """
    if _doctor(ctx)["kind"] != "gp":
        return {"error": "تجویز دارو از طریق این ایجنت فقط برای پزشک عمومی مجاز است؛ برای این پزشک فقط خلاصه یا برنامه ساخته می‌شود."}
    if ctx.get("red_flag"):
        return {"error": "علائم خطر شناسایی شده است؛ ساخت نسخه ممنوع است. بیمار باید فوراً ارجاع شود."}
    if ctx.get("risk_reasons") is None:
        check_risk_factors(ctx)
    if ctx.get("risk_reasons"):
        return {"error": "عوامل خطر وجود دارد؛ ساخت نسخه ممنوع است. بیمار باید نزد پزشک ارجاع شود.", "reasons": ctx["risk_reasons"]}
    cid = ctx.get("condition")
    if not cid:
        return {"error": "ابتدا باید راهنمای درمانی مرتبط پیدا شود (search_guidelines)."}
    allowed = set(CONDITIONS[cid]["drugs"])
    final, rejected = [], []
    for did in drug_ids:
        if did not in FORMULARY or did not in allowed:
            rejected.append({"drug_id": did, "reason": "دارو برای این وضعیت در راهنما مجاز نیست."})
            continue
        res = _check_one(ctx["patient"], did)
        if res["status"] == "blocked":
            rejected.append({"drug_id": did, "reason": " ".join(res["reasons"])})
            continue
        d = FORMULARY[did]
        final.append({
            "id": did, "name_fa": d["name_fa"], "form": d["form"], "dose": d["dose"],
            "max": d["max"], "duration": d["duration"], "qty": d["qty"],
            "warnings": d["warnings"] + res["cautions"],
        })
    ctx["rejected_drugs"] = rejected
    if not final:
        return {"error": "هیچ داروی ایمنی برای این بیمار باقی نماند؛ بیمار باید نزد پزشک ارجاع شود.", "rejected": rejected}
    ctx["draft"] = {
        "kind": "rx",
        "condition": CONDITIONS[cid]["name_fa"],
        "drugs": final,
        "advice": CONDITIONS[cid]["advice"],
        "rationale": rationale,
    }
    return {"status": "draft_created", "drugs": [x["name_fa"] for x in final], "rejected": rejected,
            "note": "پیش‌نویس منتظر تأیید پزشک است. ایجنت نمی‌تواند کد نسخه صادر کند."}


def search_physio_plan(ctx):
    """برنامه‌ی تمرینی نمونه برای ناحیه‌ی درد (فقط برای فیزیوتراپی؛ بدون دارو)."""
    if _doctor(ctx)["kind"] != "physio":
        return {"matched": False, "message": "این ابزار فقط برای فیزیوتراپی است."}
    area = (ctx.get("answers") or {}).get("area", "")
    plan = PHYSIO.get(area)
    if not plan:
        ctx["draft"] = None
        return {"matched": False, "message": "برای این ناحیه برنامه‌ی آماده‌ای در پایگاه نیست؛ ارزیابی حضوری لازم است."}
    advice = list(plan["advice"])
    if (ctx["patient"].get("duration_days") or 0) > 90:
        advice.append("چون درد بیش از ۳ ماه طول کشیده، ارزیابی حضوری و برنامه‌ی تخصصی‌تر لازم است.")
    ctx["draft"] = {
        "kind": "plan",
        "condition": f"برنامه‌ی توان‌بخشی — {plan['title']}",
        "advice": advice,
        "exercises": plan["exercises"],
        "return_if": plan["return_if"],
        "rationale": f"ناحیه‌ی درد «{area}» است و علائم خطر یا عامل خطری گزارش نشده؛ برنامه‌ی عمومی کم‌خطر پیشنهاد شد تا دکتر بررسی و تأیید کند.",
    }
    return {"matched": True, "area": area, "exercises": [e["name"] for e in plan["exercises"]]}


def prepare_visit_summary(ctx):
    """خلاصه‌ی پرونده برای پزشک (زنان، دندان‌پزشکی، یا ناحیه‌ی بدون برنامه در فیزیوتراپی). بدون دارو."""
    kind = _doctor(ctx)["kind"]
    ans = ctx.get("answers") or {}
    p = ctx["patient"]
    points = [f"{a['label']}: {a['value']}" for a in ctx.get("answers_display", []) if a.get("value")]
    why = []
    if kind == "obgyn" and p.get("pregnant") and ans.get("bleeding") not in (None, "", "خیر"):
        why.append("لکه‌بینی یا خونریزی در بارداری")
    if kind == "dentist":
        if str(ans.get("swelling", "")).startswith("تورم موضعی"):
            why.append("تورم موضعی لثه یا گونه")
        if ans.get("fever") == "بله":
            why.append("تب همراه مشکل دندانی")
        if (p.get("severity") or 0) >= 7:
            why.append("شدت درد بالا")
    advice = {
        "obgyn": ["اطلاعات شما برای دکتر ثبت شد.",
                  "اگر خونریزی شدید، درد شدید شکم یا کاهش حرکت جنین پیدا کردید، فوراً به اورژانس مراجعه کنید."],
        "dentist": ["تا زمان ویزیت از غذاها و نوشیدنی‌های خیلی سرد، گرم یا سفت پرهیز کنید.",
                    "دهان را با آب ولرم و کمی نمک شست‌وشو دهید.",
                    "روی دندان یا لثه آسپرین یا مواد خانگی نگذارید.",
                    "اگر تورم صورت زیاد شد یا بلع و نفس‌کشیدن سخت شد، فوراً به اورژانس بروید."],
    }.get(kind, ["اطلاعات شما برای دکتر ثبت شد."])
    ctx["draft"] = {
        "kind": "summary",
        "condition": "خلاصه‌ی پرونده برای ویزیت",
        "points": points,
        "priority": "urgent" if why else "routine",
        "priority_reasons": why,
        "advice": advice,
        "rationale": "علائم خطر اورژانسی گزارش نشد. ایجنت دارو تجویز نمی‌کند و فقط اطلاعات را برای پزشک خلاصه کرد.",
    }
    return {"status": "summary_created", "priority": ctx["draft"]["priority"], "priority_reasons": why}


def refer_to_doctor(ctx, urgency="routine", reason=""):
    """ارجاع بیمار به پزشک. urgency: emergency | routine"""
    if ctx.get("red_flag"):
        urgency = "emergency"  # گارد: علائم خطر همیشه اورژانسی است
    ctx["outcome"] = {"type": "emergency" if urgency == "emergency" else "see_doctor", "reason": reason}
    ctx["draft"] = None
    return {"status": "referred", "urgency": urgency}


TOOLS = {
    "check_red_flags": check_red_flags,
    "suggest_doctor": suggest_doctor,
    "check_risk_factors": check_risk_factors,
    "search_guidelines": search_guidelines,
    "check_drug_safety": check_drug_safety,
    "create_draft_prescription": create_draft_prescription,
    "search_physio_plan": search_physio_plan,
    "prepare_visit_summary": prepare_visit_summary,
    "refer_to_doctor": refer_to_doctor,
}


def call_tool(ctx, name, args):
    """اجرای ابزار + ثبت در trace (برای نمایش مراحل تصمیم‌گیری در ارائه)."""
    fn = TOOLS.get(name)
    if fn is None:
        out = {"error": f"ابزار ناشناخته: {name}"}
    else:
        try:
            out = fn(ctx, **(args or {}))
        except TypeError as e:
            out = {"error": f"آرگومان نامعتبر: {e}"}
    ctx["trace"].append({
        "tool": name,
        "input": args or {},
        "output": out,
        "ts": calendar_fa.now().strftime("%H:%M:%S"),
    })
    return out
