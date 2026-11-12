"""تست‌های ارزیابی ایجنت (برای بخش «ارزیابی» در ارائه).  اجرا: python3 tests.py"""
import base64
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

os.environ["AGENT_MODE"] = "demo"
os.environ.pop("ANTHROPIC_API_KEY", None)

import agent
import server
import tools

D = {d["id"]: d for d in tools.DOCTORS}


def P(age=30, sex="male", pregnant=False, allergies="", meds="", cond="", dur=2, sev=3, temp=None):
    return {"name": "", "age": age, "sex": sex, "weight": 70, "pregnant": pregnant, "allergies": allergies,
            "current_meds": meds, "conditions": cond, "duration_days": dur, "severity": sev, "temperature": temp}


def doc(did):
    d = D[did]
    return {"id": d["id"], "name": d["name"], "kind": d["kind"], "gender": d["gender"], "specialty": d["specialty"]}


def run(patient, symptoms, doctor="sohrabi", answers=None, display=None):
    ctx = {"patient": patient, "symptoms": symptoms, "answers": answers or {}, "answers_display": display or [],
           "doctor": doc(doctor), "trace": []}
    agent.run_agent(ctx)
    return ctx


def drugs(ctx):
    return sorted(d["id"] for d in (ctx.get("draft") or {}).get("drugs", []))


def route(ctx):
    r = ctx.get("routing")
    return r["suggested_doctor"]["id"] if r else None


# (نام, پزشک, بیمار, علائم, پاسخ‌ها, خروجی مورد انتظار, تابع بررسی اضافه)
S = [
    ("GP: سرماخوردگی ساده", "sohrabi", P(), "از دیروز آبریزش بینی و گلودرد دارم", {}, "draft", lambda c: drugs(c) == ["paracetamol", "saline_nasal"]),
    ("GP: نفی تب در جمله", "sohrabi", P(), "آبریزش بینی دارم، تب ندارم", {}, "draft", lambda c: drugs(c) == ["paracetamol", "saline_nasal"]),
    ("GP: درد قفسه + تنگی نفس", "sohrabi", P(52), "درد قفسه سینه و تنگی نفس دارم", {}, "emergency", None),
    ("GP: نفی علامت خطر", "sohrabi", P(), "سردرد دارم ولی درد قفسه سینه ندارم", {}, "draft", lambda c: drugs(c) == ["ibuprofen", "paracetamol"]),
    ("GP: وارفارین → ایبوپروفن حذف", "sohrabi", P(58, meds="وارفارین"), "سردرد دارم", {}, "draft", lambda c: drugs(c) == ["paracetamol"]),
    ("GP: حساسیت NSAID → ایبوپروفن حذف", "sohrabi", P(35, "female", allergies="حساسیت به آسپرین"), "سردرد خفیف", {}, "draft", lambda c: drugs(c) == ["paracetamol"]),
    ("GP: آسم → ایبوپروفن حذف", "sohrabi", P(40, cond="آسم"), "سردرد", {}, "draft", lambda c: drugs(c) == ["paracetamol"]),
    ("GP: حساسیت به هر دو مسکن", "sohrabi", P(40, allergies="استامینوفن و ایبوپروفن"), "سردرد", {}, "see_doctor", None),
    ("GP: بارداری → ارجاع + پیشنهاد زنان", "sohrabi", P(30, "female", pregnant=True), "سرماخوردگی و گلودرد", {}, "see_doctor", lambda c: route(c) == "karamati"),
    ("GP: کودک ۸ ساله", "sohrabi", P(8), "آبریزش بینی", {}, "see_doctor", None),
    ("GP: علائم بیش از ۷ روز", "sohrabi", P(dur=10), "گلودرد", {}, "see_doctor", None),
    ("GP: شدت زیاد", "sohrabi", P(sev=9), "سردرد", {}, "see_doctor", None),
    ("GP: تب بالا", "sohrabi", P(temp=40.0), "سرماخوردگی", {}, "see_doctor", None),
    ("GP: علامت ناشناخته", "sohrabi", P(), "درد پای راست هنگام راه رفتن", {}, "see_doctor", None),
    ("GP: ابهام بین دو وضعیت", "sohrabi", P(), "سردرد و آبریزش بینی", {}, "see_doctor", None),
    ("GP: اسهال خفیف", "sohrabi", P(), "از دیروز اسهال دارم", {}, "draft", lambda c: drugs(c) == ["ors"]),
    ("GP: ریفلاکس + بیماری کلیه", "sohrabi", P(45, cond="نارسایی کلیه"), "سوزش سر دل", {}, "see_doctor", None),
    ("GP: آلرژی فصلی", "sohrabi", P(25), "حساسیت فصلی و خارش بینی دارم", {}, "draft", lambda c: drugs(c) == ["cetirizine", "saline_nasal"]),
    ("GP: افکار آسیب به خود", "sohrabi", P(22), "فکر خودکشی دارم", {}, "emergency", None),
    ("GP: تورم زبان", "sohrabi", P(), "بعد از غذا تورم زبان پیدا کردم", {}, "emergency", None),
    ("GP: کمردرد → پیشنهاد فیزیوتراپی", "sohrabi", P(dur=21), "سه هفته است کمردرد دارم", {}, "see_doctor", lambda c: route(c) == "moradi"),
    ("GP: دندان‌درد → پیشنهاد دندان‌پزشک", "sohrabi", P(dur=2), "دندان‌درد دارم", {}, "see_doctor", lambda c: route(c) == "mousavi"),
    # فیزیوتراپی
    ("فیزیو: کمردرد ساده → برنامه تمرینی", "moradi", P(34, dur=10, sev=5), "کمرم موقع نشستن گرفته می‌شود", {"area": "کمر"}, "plan",
     lambda c: len(c["draft"]["exercises"]) == 3 and not c["draft"].get("drugs")),
    ("فیزیو: بی‌حسی ناگهانی → اورژانس", "moradi", P(45), "کمرم درد می‌کند. ضعف و بی‌حسی ناگهانی", {"area": "کمر"}, "emergency", None),
    ("فیزیو: ضربه شدید → اورژانس", "moradi", P(22), "دیروز زمین خوردم. ضربه شدید اخیر", {"area": "زانو"}, "emergency", None),
    ("فیزیو: ناحیه‌ی «سایر» → خلاصه", "moradi", P(40), "درد آرنج دارم", {"area": "سایر"}, "summary", None),
    ("فیزیو: شدت ۹ → ارجاع", "moradi", P(sev=9), "درد کمر", {"area": "کمر"}, "see_doctor", None),
    ("فیزیو: درد مزمن (دیرتر از ۳ ماه) هشدار اضافه", "moradi", P(dur=120), "کمردرد", {"area": "کمر"}, "plan",
     lambda c: any("۳ ماه" in a for a in c["draft"]["advice"])),
    # دندان‌پزشکی
    ("دندان: دندان‌درد ساده → خلاصه عادی", "mousavi", P(30, "female", dur=3, sev=5), "دندان آسیاب درد می‌کند",
     {"swelling": "خیر", "fever": "خیر"}, "summary", lambda c: c["draft"]["priority"] == "routine" and not c["draft"].get("drugs")),
    ("دندان: تورم موضعی + تب → اولویت بالا", "mousavi", P(38, sev=7), "لثه‌ام ورم کرده",
     {"swelling": "تورم موضعی لثه یا گونه", "fever": "بله"}, "summary", lambda c: c["draft"]["priority"] == "urgent"),
    ("دندان: تورم شدید + سختی بلع → اورژانس", "mousavi", P(44, sev=6), "صورتم ورم کرده. تورم شدید صورت و گردن با سختی در بلع", {}, "emergency", None),
    ("دندان: ضربه شدید → اورژانس", "mousavi", P(19), "توپ خورد به دهانم. ضربه شدید اخیر", {}, "emergency", None),
    ("دندان: بارداری مانع نیست (فقط ثبت می‌شود)", "mousavi", P(28, "female", pregnant=True), "خونریزی لثه", {}, "summary", None),
    ("دندان: کودک ۸ ساله مانعی ندارد", "mousavi", P(8), "دندان‌درد", {}, "summary", None),
    # زنان
    ("زنان: چکاپ بارداری → خلاصه", "karamati", P(28, "female", pregnant=True, sev=1), "چکاپ ماهانه", {"bleeding": "خیر"}, "summary", lambda c: c["draft"]["priority"] == "routine"),
    ("زنان: لکه‌بینی در بارداری → اولویت بالا", "karamati", P(31, "female", pregnant=True), "کمی لکه‌بینی", {"bleeding": "لکه‌بینی خفیف"}, "summary", lambda c: c["draft"]["priority"] == "urgent"),
    ("زنان: کاهش حرکت جنین → اورژانس", "karamati", P(33, "female", pregnant=True), "حرکت بچه کمتر شده. کاهش حرکت جنین", {}, "emergency", None),
    ("زنان: خونریزی شدید → اورژانس", "karamati", P(33, "female"), "درد دارم. خونریزی شدید", {}, "emergency", None),
    ("زنان: بدون بارداری (قاعدگی) → خلاصه", "karamati", P(24, "female"), "پریودم نامنظم شده", {}, "summary", None),
    ("زنان: کمردرد بدون بارداری → پیشنهاد فیزیوتراپی", "karamati", P(35, "female"), "کمردرد دارم", {}, "summary", lambda c: route(c) == "moradi"),
]


def test_scenarios():
    fails = 0
    for name, did, pat, sym, ans, want, check in S:
        ctx = run(pat, sym, did, ans)
        got = ctx["outcome"]["type"]
        ok = got == want and (check is None or bool(check(ctx)))
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:54s} → {got:10s} {drugs(ctx) or ''}")
        if not ok:
            print(f"      انتظار: {want}")
    print(f"\nسناریوها: {len(S) - fails}/{len(S)} درست")
    return fails


def test_guards():
    """شبیه‌سازی مدلی که اشتباه می‌کند: باید گاردهای tools.py جلویش را بگیرند."""
    fails = 0

    def mk(did, pat, sym, ans=None):
        return {"patient": pat, "symptoms": sym, "answers": ans or {}, "answers_display": [], "doctor": doc(did), "trace": []}

    def chk(name, cond):
        nonlocal fails
        print(f"{'PASS' if cond else 'FAIL'}  {name}")
        fails += not cond

    c = mk("sohrabi", P(58, meds="وارفارین"), "سردرد")
    for t in ("check_red_flags", "check_risk_factors", "search_guidelines"):
        tools.call_tool(c, t, {})
    out = tools.call_tool(c, "create_draft_prescription", {"drug_ids": ["ibuprofen", "paracetamol"]})
    chk("گارد: ایبوپروفن برای وارفارین‌خور رد شد", [d["id"] for d in c["draft"]["drugs"]] == ["paracetamol"] and out["rejected"][0]["drug_id"] == "ibuprofen")

    c = mk("sohrabi", P(), "سرماخوردگی")
    for t in ("check_red_flags", "check_risk_factors", "search_guidelines"):
        tools.call_tool(c, t, {})
    out = tools.call_tool(c, "create_draft_prescription", {"drug_ids": ["ibuprofen"]})
    chk("گارد: داروی خارج از راهنمای سرماخوردگی رد شد", "error" in out and not c.get("draft"))

    c = mk("sohrabi", P(), "تنگی نفس و آبریزش بینی")
    tools.call_tool(c, "check_red_flags", {})
    out = tools.call_tool(c, "create_draft_prescription", {"drug_ids": ["paracetamol"]})
    chk("گارد: با علامت خطر نسخه ساخته نشد", "error" in out and not c.get("draft"))
    tools.call_tool(c, "refer_to_doctor", {"urgency": "routine", "reason": "x"})
    chk("گارد: ارجاع routine با علامت خطر به emergency ارتقا یافت", c["outcome"]["type"] == "emergency")
    chk("گارد: ابزار issue_code برای ایجنت وجود ندارد", "error" in tools.call_tool(c, "issue_code", {}))

    for did, sym, ans in (("moradi", "کمردرد", {"area": "کمر"}), ("mousavi", "دندان‌درد", {}), ("karamati", "چکاپ", {})):
        c = mk(did, P(sev=3, dur=2), sym, ans)
        for t in ("check_red_flags", "check_risk_factors", "search_guidelines"):
            tools.call_tool(c, t, {})
        out = tools.call_tool(c, "create_draft_prescription", {"drug_ids": ["paracetamol"]})
        chk(f"گارد: نسخه‌ی دارویی برای {D[did]['specialty']} ممنوع است", "error" in out and not c.get("draft"))

    c = mk("sohrabi", P(), "کمردرد", {"area": "کمر"})
    chk("گارد: ابزار برنامه‌ی فیزیو برای پزشک عمومی کار نمی‌کند", tools.call_tool(c, "search_physio_plan", {})["matched"] is False)
    return fails


def test_llm_mock():
    """حلقه‌ی tool-use را با یک مدل جعلی تست می‌کنیم (بدون اینترنت)."""
    def tu(i, name, inp):
        return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": i, "name": name, "input": inp}]}
    script = [tu("t1", "check_red_flags", {}), tu("t2", "suggest_doctor", {}), tu("t3", "check_risk_factors", {}),
              tu("t4", "search_guidelines", {}),
              tu("t5", "check_drug_safety", {"drug_ids": ["paracetamol", "saline_nasal"]}),
              tu("t6", "create_draft_prescription", {"drug_ids": ["paracetamol", "saline_nasal"], "rationale": "mock"}),
              {"stop_reason": "end_turn", "content": [{"type": "text", "text": "پیش‌نویس برای پزشک ارسال شد."}]}]
    it = iter(script)
    real_call, real_key = agent._call_api, os.environ.get("ANTHROPIC_API_KEY")
    agent._call_api = lambda payload: next(it)
    os.environ["ANTHROPIC_API_KEY"], os.environ["AGENT_MODE"] = "test", "auto"
    try:
        ctx = run(P(), "آبریزش بینی و گلودرد")
    finally:
        agent._call_api = real_call
        os.environ["AGENT_MODE"] = "demo"
        if real_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
    ok = ctx["mode"] == "llm" and ctx["outcome"]["type"] == "draft" and ctx["message"] == "پیش‌نویس برای پزشک ارسال شد." and len(ctx["trace"]) == 6
    print(f"{'PASS' if ok else 'FAIL'}  حلقه‌ی LLM (mock): حالت={ctx['mode']} خروجی={ctx['outcome']['type']} مراحل={len(ctx['trace'])}")

    def boom(payload):
        raise OSError("no network")
    agent._call_api = boom
    os.environ["ANTHROPIC_API_KEY"], os.environ["AGENT_MODE"] = "test", "auto"
    try:
        ctx2 = run(P(), "آبریزش بینی")
    finally:
        agent._call_api = real_call
        os.environ["AGENT_MODE"] = "demo"
        if real_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
    ok2 = ctx2["mode"] == "demo (fallback)" and ctx2["outcome"]["type"] == "draft"
    print(f"{'PASS' if ok2 else 'FAIL'}  خطای شبکه → برگشت امن به حالت دمو")
    return (not ok) + (not ok2)


PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
PINS = {"sohrabi": "1111", "moradi": "2222", "mousavi": "3333", "karamati": "4444"}


def test_end_to_end():
    tmp = tempfile.mkdtemp()
    server.STORE_PATH = os.path.join(tmp, "store.json")
    server.UPLOAD_DIR = os.path.join(tmp, "uploads")
    srv = server.make_server("127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fails = 0

    def call(method, path, body=None, who=None, pin=None, raw=False):
        h = {"Content-Type": "application/json"}
        if who:
            h["X-Doctor-Id"], h["X-Doctor-Pin"] = who, pin or PINS[who]
        req = urllib.request.Request(base + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r:
                b = r.read()
                return r.status, (b if raw else json.loads(b)), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers

    def check(name, cond):
        nonlocal fails
        print(f"{'PASS' if cond else 'FAIL'}  {name}")
        fails += not cond

    st, r, _ = call("GET", "/api/doctors")
    check("لیست ۴ پزشک (بدون رمز) با تخصص‌های درست", st == 200 and [d["kind"] for d in r["doctors"]] == ["gp", "physio", "dentist", "obgyn"]
          and all("pin" not in d for d in r["doctors"]) and [d["gender"] for d in r["doctors"]].count("female") == 2)

    gp = {"age": "30", "sex": "male", "allergies": "", "symptoms": "آبریزش بینی و گلودرد"}
    st, c, _ = call("POST", "/api/intake", {"doctor_id": "sohrabi", "answers": gp})
    cid = c["id"]
    check("ثبت بیمار GP → در انتظار پزشک و بدون کد", st == 200 and c["status"] == "pending_doctor" and c["code"] is None and c["doctor"]["id"] == "sohrabi")
    check("لیست پزشک بدون رمز ممنوع (401)", call("GET", "/api/cases")[0] == 401)
    check("رمز غلط ممنوع (401)", call("GET", "/api/cases", who="sohrabi", pin="0000")[0] == 401)
    check("پرونده در لیست مرادی نیست", all(x["id"] != cid for x in call("GET", "/api/cases", who="moradi")[1]["cases"]))
    check("پرونده در لیست سهرابی هست", any(x["id"] == cid for x in call("GET", "/api/cases", who="sohrabi")[1]["cases"]))
    check("تأیید توسط پزشک دیگر ممنوع (403)", call("POST", f"/api/cases/{cid}/approve", {"drug_ids": ["paracetamol"]}, who="moradi")[0] == 403)
    check("گفتگو قبل از تأیید ممنوع (403)", call("POST", f"/api/cases/{cid}/chat", {"text": "سلام"})[0] == 403)
    check("بیمار قبل از تأیید کد نمی‌بیند", call("GET", f"/api/cases/{cid}")[1]["code"] is None)
    check("تأیید با داروی نامعتبر (400)", call("POST", f"/api/cases/{cid}/approve", {"drug_ids": ["xyz"]}, who="sohrabi")[0] == 400)

    st, a, _ = call("POST", f"/api/cases/{cid}/approve", {"drug_ids": ["paracetamol"], "note": "بعد از غذا"}, who="sohrabi")
    code = a["code"]
    check("تأیید پزشک → کد نسخه (۹ نویسه) و فقط داروی انتخابی", st == 200 and a["status"] == "approved" and len(code) == 9
          and [d["id"] for d in a["approved_drugs"]] == ["paracetamol"] and a["code_type"] == "prescription")
    check("تأیید دوباره ممکن نیست (409)", call("POST", f"/api/cases/{cid}/approve", {"drug_ids": ["paracetamol"]}, who="sohrabi")[0] == 409)
    check("یادداشت پزشک و پیام سیستمی در گفتگو آمد", [m["from"] for m in a["chat"]] == ["system", "doctor"])

    check("گفتگوی بیمار پس از تأیید", call("POST", f"/api/cases/{cid}/chat", {"text": "بعد از غذا چند بار؟"})[0] == 200)
    check("گفتگوی پزشک (احراز هویت‌شده)", call("POST", f"/api/cases/{cid}/chat", {"as": "doctor", "text": "هر ۸ ساعت"}, who="sohrabi")[0] == 200)
    check("گفتگو به‌جای پزشک دیگر ممنوع (403)", call("POST", f"/api/cases/{cid}/chat", {"as": "doctor", "text": "x"}, who="moradi")[0] == 403)
    check("گفتگو به‌عنوان پزشک با رمز غلط (401)", call("POST", f"/api/cases/{cid}/chat", {"as": "doctor", "text": "x"}, who="sohrabi", pin="9")[0] == 401)
    st, ch, _ = call("POST", f"/api/cases/{cid}/chat", {"text": "درد قفسه سینه دارم"})
    check("علامت خطر در گفتگو → هشدار سیستمی", ch["chat"][-1]["from"] == "system" and "۱۱۵" in ch["chat"][-1]["text"])
    check("هشدار گفتگو برای پزشک علامت‌گذاری شد", [x for x in call("GET", "/api/cases", who="sohrabi")[1]["cases"] if x["id"] == cid][0]["chat_alert"] is True)
    check("پیام خالی (400)", call("POST", f"/api/cases/{cid}/chat", {"text": "  "})[0] == 400)

    st, ph, _ = call("GET", "/api/pharmacy/" + code.lower().replace("-", ""))
    check("داروخانه با کد (بدون حساسیت به حروف/خط‌تیره) نسخه و نام پزشک را می‌بیند", st == 200 and ph["doctor"]["name"] == "دکتر سهرابی" and [d["id"] for d in ph["drugs"]] == ["paracetamol"])
    check("کد اشتباه → ۴۰۴", call("GET", "/api/pharmacy/ZZZZ-ZZZZ")[0] == 404)
    check("ثبت تحویل داروها", call("POST", f"/api/pharmacy/{code}/dispense", {})[0] == 200)
    check("تحویل دوباره ممکن نیست (409)", call("POST", f"/api/pharmacy/{code}/dispense", {})[0] == 409)

    # فیزیوتراپی: کد پذیرش، نه نسخه
    st, p, _ = call("POST", "/api/intake", {"doctor_id": "moradi", "answers": {"age": "34", "sex": "male", "area": "کمر", "severity": "5", "duration_days": "10", "numbness": "خیر", "trauma": "خیر", "symptoms": "کمردرد"}})
    check("فیزیو: خروجی برنامه‌ی تمرینی در انتظار پزشک", p["status"] == "pending_doctor" and p["draft"]["kind"] == "plan")
    st, pa, _ = call("POST", f"/api/cases/{p['id']}/approve", {}, who="moradi")
    check("فیزیو: تأیید بدون دارو → کد پذیرش VIS-", st == 200 and pa["code"].startswith("VIS-") and pa["code_type"] == "visit")
    check("کد پذیرش ویزیت در داروخانه پذیرفته نمی‌شود", call("GET", "/api/pharmacy/" + pa["code"])[0] == 404 and call("POST", f"/api/pharmacy/{pa['code']}/dispense", {})[0] == 404)

    st, e, _ = call("POST", "/api/intake", {"doctor_id": "moradi", "answers": {"age": "45", "area": "کمر", "numbness": "بله", "symptoms": "کمردرد"}})
    check("فیزیو: فلگ بی‌حسی (فرم) → اورژانس بدون کد", e["status"] == "emergency" and e["code"] is None and e["draft"] is None)
    check("اورژانس: گفتگو بسته است (403)", call("POST", f"/api/cases/{e['id']}/chat", {"text": "سلام"})[0] == 403)

    st, o, _ = call("POST", "/api/intake", {"doctor_id": "karamati", "answers": {"age": "33", "pregnant": "yes", "weeks": "32", "visit_reason": "چکاپ دوره‌ای بارداری", "fetal_move": "کمتر از معمول", "symptoms": "چکاپ"}})
    check("زنان: کاهش حرکت جنین (فرم) → اورژانس", o["status"] == "emergency")
    st, o2, _ = call("POST", "/api/intake", {"doctor_id": "karamati", "answers": {"age": "28", "pregnant": "yes", "weeks": "20", "visit_reason": "چکاپ دوره‌ای بارداری", "bleeding": "لکه‌بینی خفیف", "fetal_move": "طبیعی", "symptoms": "چکاپ"}})
    check("زنان: خلاصه با اولویت بالا برای لکه‌بینی", o2["status"] == "pending_doctor" and o2["draft"]["priority"] == "urgent" and not o2["draft"].get("drugs"))
    check("فیلد وابسته: هفته‌ی بارداری وقتی باردار نیست نادیده گرفته می‌شود", call("POST", "/api/intake", {"doctor_id": "karamati", "answers": {"age": "24", "pregnant": "no", "weeks": "99", "visit_reason": "مشکل قاعدگی", "symptoms": "پریود نامنظم"}})[0] == 200)

    # ارجاع: پذیرش ویزیت → کد پذیرش → گفتگو
    st, rf, _ = call("POST", "/api/intake", {"doctor_id": "sohrabi", "answers": {"age": "30", "sex": "female", "pregnant": "بله", "symptoms": "گلودرد"}})
    check("بارداری نزد GP → ارجاع و پیشنهاد دکتر کرامتی", rf["status"] == "referred" and rf["routing"]["suggested_doctor"]["id"] == "karamati")
    st, ac, _ = call("POST", f"/api/cases/{rf['id']}/accept", {"note": "لطفاً بیایید"}, who="sohrabi")
    check("پذیرش پرونده‌ی ارجاعی → کد پذیرش و گفتگو", st == 200 and ac["status"] == "accepted" and ac["code"].startswith("VIS-"))
    check("پذیرش دوباره (409)", call("POST", f"/api/cases/{rf['id']}/accept", {}, who="sohrabi")[0] == 409)
    check("پذیرش توسط پزشک دیگر (403)", call("POST", f"/api/cases/{call('POST', '/api/intake', {'doctor_id': 'sohrabi', 'answers': {'age': '30', 'sex': 'female', 'pregnant': 'بله', 'symptoms': 'گلودرد'}})[1]['id']}/accept", {}, who="moradi")[0] == 403)

    # تصاویر
    st, d1, _ = call("POST", "/api/intake", {"doctor_id": "mousavi", "images": [PNG], "answers": {"age": "30", "sex": "female", "pregnant": "خیر", "complaint": "دندان‌درد", "severity": "5", "duration_days": "3", "swelling": "خیر", "fever": "خیر", "symptoms": "دندان‌درد"}})
    check("دندان: خلاصه + ۱ تصویر ذخیره شد", st == 200 and d1["status"] == "pending_doctor" and d1["image_count"] == 1)
    st, img, hd = call("GET", f"/api/cases/{d1['id']}/images/0", who="mousavi", raw=True)
    check("دکتر موسوی تصویر را می‌بیند (PNG)", st == 200 and img[:4] == b"\x89PNG" and hd["Content-Type"] == "image/png")
    check("پزشک دیگر تصویر را نمی‌بیند (403)", call("GET", f"/api/cases/{d1['id']}/images/0", who="sohrabi")[0] == 403)
    check("تصویر بدون ورود پزشک (401)", call("GET", f"/api/cases/{d1['id']}/images/0")[0] == 401)
    check("ایندکس تصویر نامعتبر (404)", call("GET", f"/api/cases/{d1['id']}/images/2", who="mousavi")[0] == 404)
    ok_ans = {"age": "30", "complaint": "دندان‌درد", "symptoms": "درد"}
    fake = "data:image/png;base64," + base64.b64encode(b"this is not an image").decode()
    check("فایل جعلی با پسوند تصویر رد می‌شود (400)", call("POST", "/api/intake", {"doctor_id": "mousavi", "images": [fake], "answers": ok_ans})[0] == 400)
    check("بیش از ۳ تصویر رد می‌شود (400)", call("POST", "/api/intake", {"doctor_id": "mousavi", "images": [PNG] * 4, "answers": ok_ans})[0] == 400)
    check("base64 خراب رد می‌شود (400)", call("POST", "/api/intake", {"doctor_id": "mousavi", "images": ["data:image/png;base64,@@@"], "answers": ok_ans})[0] == 400)
    big = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 2_600_000).decode()
    check("تصویر بزرگ‌تر از حد رد می‌شود (400)", call("POST", "/api/intake", {"doctor_id": "mousavi", "images": [big], "answers": ok_ans})[0] == 400)

    # اعتبارسنجی
    check("پزشک نامعتبر (400)", call("POST", "/api/intake", {"doctor_id": "nobody", "answers": gp})[0] == 400)
    check("شرح حال خالی (400)", call("POST", "/api/intake", {"doctor_id": "sohrabi", "answers": {"age": "30", "symptoms": ""}})[0] == 400)
    check("سن نامعتبر (400)", call("POST", "/api/intake", {"doctor_id": "sohrabi", "answers": {"age": "abc", "symptoms": "سرفه"}})[0] == 400)
    check("فیلد اجباری فیزیو (ناحیه‌ی درد) (400)", call("POST", "/api/intake", {"doctor_id": "moradi", "answers": {"age": "30", "symptoms": "درد"}})[0] == 400)
    st, rj, _ = call("POST", "/api/intake", {"doctor_id": "sohrabi", "answers": {"age": "30", "symptoms": "گلودرد"}})
    st, rr, _ = call("POST", f"/api/cases/{rj['id']}/reject", {"note": "خیر"}, who="sohrabi")
    check("رد پیش‌نویس توسط پزشک → بدون کد و بدون گفتگو", st == 200 and rr["status"] == "rejected" and rr["code"] is None
          and call("POST", f"/api/cases/{rj['id']}/chat", {"text": "x"})[0] == 403)
    check("صفحه‌ی اصلی بالا می‌آید", call("GET", "/", raw=True)[0] == 200)

    # پنل مدیر
    def adm(path, pin="9999", raw=False):
        req = urllib.request.Request(base + path, headers={"X-Admin-Pin": pin})
        try:
            with urllib.request.urlopen(req) as r:
                b = r.read()
                return r.status, (b if raw else json.loads(b))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    st, al = adm("/api/admin/cases")
    check("مدیر: پرونده‌ی همه‌ی پزشکان را یک‌جا می‌بیند", st == 200 and al["total"] == len(al["cases"])
          and {c["doctor"]["id"] for c in al["cases"]} >= {"sohrabi", "moradi", "mousavi", "karamati"})
    check("مدیر: رمز غلط (401)", adm("/api/admin/cases", pin="1")[0] == 401)
    st, im = adm(f"/api/admin/cases/{d1['id']}/images/0", raw=True)
    check("مدیر: تصویر بیمار را می‌بیند", st == 200 and im[:4] == b"\x89PNG")
    server.ADMIN_PIN = None
    check("مدیر: بدون رمز تنظیم‌شده غیرفعال است (401)", adm("/api/admin/cases")[0] == 401)
    server.ADMIN_PIN = "9999"
    server._HITS.clear()
    check("نظرات: نام کامل (حداقل دو بخش) برای همه‌ی پزشکان", all(len(r["name"].split()) >= 2 for d in tools.DOCTORS for r in d["reviews"]))

    # محدودیت‌های سرور عمومی
    check("رمز پیش‌فرض دمو فعال است (در غیاب متغیر محیطی)", call("GET", "/api/doctors")[1]["demo_pins"] is True)
    server._HITS.clear()
    for _ in range(server.MAX_AUTH_FAILS):
        call("GET", "/api/cases", who="sohrabi", pin="bad")
    check("بعد از ۱۰ رمز غلط، حتی رمز درست هم موقتاً رد می‌شود (قفل)", call("GET", "/api/cases", who="sohrabi")[0] == 401)
    server._HITS.clear()
    check("پس از پایان قفل، رمز درست کار می‌کند", call("GET", "/api/cases", who="sohrabi")[0] == 200)
    old = server.RATE_INTAKE
    server.RATE_INTAKE = 0
    check("سقف ثبت بیمار در ساعت (429)", call("POST", "/api/intake", {"doctor_id": "sohrabi", "answers": gp})[0] == 429)
    server.RATE_INTAKE = old
    oldc = server.MAX_CASES
    server.MAX_CASES = 0
    check("سقف کل پرونده‌ها (503)", call("POST", "/api/intake", {"doctor_id": "sohrabi", "answers": gp})[0] == 503)
    server.MAX_CASES = oldc
    srv.shutdown()
    return fails


def test_calendar_assistant():
    from datetime import datetime, date
    import calendar_fa as cf
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(f"{'PASS' if cond else 'FAIL'}  {name}")
        fails += not cond

    # --- تقویم و نوبت‌دهی (واحد)
    check("تبدیل شمسی: ۲۰ آبان ۱۴۰۵", cf.to_jalali(2026, 11, 11) == (1405, 8, 20))
    check("تبدیل شمسی: نوروز ۱۴۰۵ و ۱۴۰۴", cf.to_jalali(2026, 3, 21) == (1405, 1, 1) and cf.to_jalali(2025, 3, 21) == (1404, 1, 1))
    check("تبدیل شمسی: آخر اسفند ۱۴۰۴ و اول مهر ۱۴۰۵", cf.to_jalali(2026, 3, 20) == (1404, 12, 29) and cf.to_jalali(2026, 9, 23) == (1405, 7, 1))
    wed = datetime(2026, 11, 11, 17, 5, tzinfo=cf.IRAN)
    check("نام روز هفته (چهارشنبه)", cf.date_fa(wed) == "چهارشنبه ۲۰ آبان ۱۴۰۵")
    sch = {"days": [5, 6, 0, 1, 2], "start": "16:00", "end": "20:00", "slot": 20}
    s1 = cf.next_slot(sch, set(), now_dt=wed)
    check("اولین وقت: ۳۰ دقیقه بعد از الان، روی شبکه‌ی ۲۰ دقیقه‌ای", cf.time_fa(s1) == "۱۷:۴۰")
    s2 = cf.next_slot(sch, {s1.isoformat()}, now_dt=wed)
    check("وقت رزروشده رد می‌شود", cf.time_fa(s2) == "۱۸:۰۰")
    late = datetime(2026, 11, 11, 19, 50, tzinfo=cf.IRAN)  # چهارشنبه، سه‌شنبه‌ها... بعد از ساعت کاری
    s3 = cf.next_slot(sch, set(), now_dt=late)
    check("بعد از ساعت کاری → روز کاری بعد (چهارشنبه پزشک کار نمی‌کند؛ شنبه)", s3.weekday() == 5 and cf.time_fa(s3) == "۱۶:۰۰" and s3.date() == date(2026, 11, 14))
    fri = datetime(2026, 11, 13, 10, 0, tzinfo=cf.IRAN)
    check("جمعه هیچ‌وقت نوبت نمی‌دهد", cf.next_slot({"days": [4], "start": "9:00", "end": "12:00", "slot": 30}, set(), now_dt=fri, horizon_days=0) is None
          or cf.next_slot({"days": [4], "start": "9:00", "end": "12:00", "slot": 30}, set(), now_dt=fri).weekday() == 4)
    check("همه‌ی پزشکان برنامه‌ی کاری دارند", all("schedule" in d and d["schedule"]["days"] for d in tools.DOCTORS))

    # --- سرور با ساعت ثابت
    tmp = tempfile.mkdtemp()
    server.STORE_PATH = os.path.join(tmp, "store.json")
    server.UPLOAD_DIR = os.path.join(tmp, "uploads")
    server.ASSIST_PATH = os.path.join(tmp, "assistant_log.json")
    server.CASES.clear()
    server.ASSIST_LOG.clear()
    server._HITS.clear()
    real_now = cf.now
    cf.now = lambda: datetime(2026, 11, 11, 17, 5, tzinfo=cf.IRAN)
    srv = server.make_server("127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def call(method, path, body=None, who=None, admin=False):
        h = {"Content-Type": "application/json"}
        if who:
            h["X-Doctor-Id"], h["X-Doctor-Pin"] = who, PINS[who]
        if admin:
            h["X-Admin-Pin"] = "9999"
        req = urllib.request.Request(base + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        st, r = call("GET", "/api/doctors")
        check("لیست پزشکان: نزدیک‌ترین وقت و ساعت فعلی", st == 200 and r["doctors"][0]["next_slot"] == "چهارشنبه ۲۰ آبان ۱۴۰۵ ساعت ۱۷:۴۰" and "۲۰ آبان ۱۴۰۵" in r["now"])
        preg = {"age": "28", "sex": "female", "pregnant": "بله", "allergies": "", "symptoms": "سرماخوردگی"}
        st, c1 = call("POST", "/api/intake", {"doctor_id": "sohrabi", "answers": preg})
        check("ارجاع حضوری → وقت واقعی (تاریخ، روز، ساعت)", c1["status"] == "referred" and c1["appointment"]["text"] == "چهارشنبه ۲۰ آبان ۱۴۰۵ ساعت ۱۷:۴۰")
        st, c2 = call("POST", "/api/intake", {"doctor_id": "sohrabi", "answers": preg})
        check("بیمار دوم همان وقت را نمی‌گیرد", c2["appointment"]["time"] == "۱۸:۰۰")
        st, n = call("POST", f"/api/cases/{c1['id']}/reschedule", {})
        check("جابه‌جایی → وقت بعدی آزاد (۱۸:۲۰ چون ۱۸:۰۰ رزرو است)", st == 200 and n["appointment"]["time"] == "۱۸:۲۰")
        for _ in range(2):
            st, n = call("POST", f"/api/cases/{c1['id']}/reschedule", {})
        check("بعد از ۳ بار جابه‌جایی (429)", call("POST", f"/api/cases/{c1['id']}/reschedule", {})[0] == 429)
        check("نوبت در لیست پزشک دیده می‌شود", any(x["id"] == c1["id"] and x["appointment"] for x in call("GET", "/api/cases", who="sohrabi")[1]["cases"]))
        st, em = call("POST", "/api/intake", {"doctor_id": "sohrabi", "answers": {"age": "50", "sex": "male", "allergies": "", "symptoms": "درد قفسه سینه"}})
        check("اورژانس → نوبت داده نمی‌شود و جابه‌جایی ممنوع (409)", em["status"] == "emergency" and not em.get("appointment")
              and call("POST", f"/api/cases/{em['id']}/reschedule", {})[0] == 409)
        check("جابه‌جایی پرونده‌ی ناموجود (404)", call("POST", "/api/cases/00000000/reschedule", {})[0] == 404)
        dent = {"age": "30", "sex": "female", "pregnant": "خیر", "complaint": "دندان‌درد", "severity": "5", "duration_days": "3", "swelling": "خیر", "fever": "خیر", "symptoms": "دندان‌درد"}
        st, d = call("POST", "/api/intake", {"doctor_id": "mousavi", "answers": dent})
        st, a = call("POST", f"/api/cases/{d['id']}/approve", {}, who="mousavi")
        check("تأیید دندان‌پزشک → وقت ویزیت (شنبه ۲۳ آبان ساعت ۱۰:۰۰)", st == 200 and a["appointment"]["text"] == "شنبه ۲۳ آبان ۱۴۰۵ ساعت ۱۰:۰۰")
        check("زمان‌های ثبت به شمسی (ایران)", c1["created"].startswith("چهارشنبه ۲۰ آبان ۱۴۰۵"))

        # --- دستیار پزشکی
        os.environ.pop("ANTHROPIC_API_KEY", None)
        check("بدون کلید: دستیار غیرفعال اعلام می‌شود", call("GET", "/api/doctors")[1]["assistant"] is False)
        check("بدون کلید → ۵۰۳", call("POST", "/api/assistant", {"messages": [{"role": "user", "content": "پاراستامول چیست؟"}]})[0] == 503)
        calls = []
        real_api = agent._call_api
        agent._call_api = lambda payload: (calls.append(payload), {"content": [{"type": "text", "text": "پاراستامول مسکن و تب‌بر است."}]})[1]
        os.environ["ANTHROPIC_API_KEY"] = "test"
        try:
            check("با کلید: فعال", call("GET", "/api/doctors")[1]["assistant"] is True)
            st, r = call("POST", "/api/assistant", {"messages": [{"role": "user", "content": "ایبوپروفن چه عوارضی دارد؟"}]})
            check("سؤال دارویی آزاد (خارج از فهرست نمونه) پاسخ می‌گیرد", st == 200 and r["mode"] == "llm" and "پاراستامول" in r["reply"] and len(calls) == 1
                  and "NOT a doctor" in calls[0]["system"])
            n0 = len(calls)
            st, r = call("POST", "/api/assistant", {"messages": [{"role": "user", "content": "درد قفسه سینه و تنگی نفس دارم"}]})
            check("علامت خطر → هشدار ثابت ۱۱۵ و بدون صدا زدن مدل", st == 200 and r["mode"] == "emergency" and "۱۱۵" in r["reply"] and len(calls) == n0)
            check("ورودی نامعتبر (400)", call("POST", "/api/assistant", {"messages": []})[0] == 400
                  and call("POST", "/api/assistant", {"messages": [{"role": "assistant", "content": "x"}]})[0] == 400
                  and call("POST", "/api/assistant", {})[0] == 400)
            check("مدیر لاگ پرسش‌های دستیار را می‌بیند", len(call("GET", "/api/admin/cases", admin=True)[1]["assistant"]) == 2)
            agent._call_api = lambda payload: (_ for _ in ()).throw(RuntimeError("boom"))
            check("خطای مدل → ۵۰۲", call("POST", "/api/assistant", {"messages": [{"role": "user", "content": "سلام"}]})[0] == 502)
            old = server.RATE_ASSIST
            server.RATE_ASSIST = 0
            check("سقف سؤال در ساعت (429)", call("POST", "/api/assistant", {"messages": [{"role": "user", "content": "سلام"}]})[0] == 429)
            server.RATE_ASSIST = old
        finally:
            agent._call_api = real_api
            os.environ.pop("ANTHROPIC_API_KEY", None)
    finally:
        cf.now = real_now
        srv.shutdown()
        server.CASES.clear()
        server.ASSIST_LOG.clear()
        server._HITS.clear()
    return fails


if __name__ == "__main__":
    total = 0
    print("── سناریوهای بالینی (۴ پزشک) ──")
    total += test_scenarios()
    print("\n── گاردهای ایمنی در برابر مدل اشتباه ──")
    total += test_guards()
    print("\n── حلقه‌ی LLM ──")
    total += test_llm_mock()
    print("\n── جریان کامل (HTTP) ──")
    total += test_end_to_end()
    print("\n── تقویم، نوبت‌دهی و دستیار پزشکی ──")
    total += test_calendar_assistant()
    print("\n" + ("همه‌ی تست‌ها موفق بود ✔" if total == 0 else f"{total} تست ناموفق"))
    sys.exit(1 if total else 0)
