
/* Clinic Agent 2.0
   فاز دوم: جست‌وجو و مشاهده پرونده بیماران
   اتصال به API فعلی پروژه
*/

(() => {
  "use strict";

  const style = document.createElement("style");
  style.textContent = `
    .patients-box {
      direction: rtl;
      background: #fff;
      border: 1px solid #dfe5ec;
      border-radius: 14px;
      padding: 16px;
      margin: 16px 0;
      color: #1c2430;
      font-family: Tahoma, sans-serif;
    }
    .patients-box input {
      width: 100%;
      padding: 11px;
      border: 1px solid #dfe5ec;
      border-radius: 9px;
      margin: 10px 0;
      box-sizing: border-box;
      font: inherit;
    }
    .patients-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    .patients-table th,
    .patients-table td {
      padding: 10px 7px;
      border-bottom: 1px solid #edf0f4;
      text-align: right;
    }
    .patients-table th {
      background: #f4f7fa;
      color: #344054;
    }
    .patients-table button {
      background: #0f766e;
      color: white;
      border: 0;
      padding: 6px 12px;
      border-radius: 7px;
      cursor: pointer;
      font: inherit;
    }
    .patients-message {
      color: #667085;
      padding: 12px 0;
      font-size: 13px;
    }
    .patients-details {
      background: #f4f8f8;
      border-radius: 10px;
      padding: 12px;
      margin-top: 12px;
      line-height: 2;
      overflow-wrap: anywhere;
    }
    @media(max-width:600px) {
      .patients-table { font-size: 11px; }
      .patients-table th, .patients-table td {
        padding: 7px 4px;
      }
    }
  `;
  document.head.appendChild(style);

  let cases = [];

  function el(tag, text, parent) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (parent) parent.appendChild(node);
    return node;
  }

  function createPanel() {
    const panel = document.createElement("section");
    panel.className = "patients-box";

    el("h3", "مدیریت پرونده بیماران", panel);
    el("p", "جست‌وجو و مشاهده پرونده‌های ثبت‌شده در کلینیک", panel);

    const search = el("input", undefined, panel);
    search.type = "search";
    search.placeholder = "جست‌وجو بر اساس نام یا کد پرونده...";

    const message = el("div", "برای مشاهده پرونده‌ها، ابتدا رمز مدیر را وارد کنید.", panel);
    message.className = "patients-message";

    const tableWrap = el("div", undefined, panel);
    const details = el("div", undefined, panel);

    search.addEventListener("input", () => {
      renderTable(search.value, tableWrap, details);
    });

    const refresh = el("button", "به‌روزرسانی پرونده‌ها", panel);
    refresh.type = "button";
    refresh.style.cssText =
      "background:#0f766e;color:white;border:0;border-radius:8px;padding:9px 14px;cursor:pointer;font:inherit";

    refresh.addEventListener("click", () => loadPatients(message, tableWrap, details));

    const admin = document.querySelector("#admin-list");
    if (admin && admin.parentNode) {
      admin.parentNode.insertBefore(panel, admin);
    } else {
      document.body.appendChild(panel);
    }

    loadPatients(message, tableWrap, details);
    return { search, tableWrap, details, message };
  }

  async function loadPatients(message, tableWrap, details) {
    message.textContent = "در حال دریافت پرونده‌ها...";

    const pin = sessionStorage.getItem("apin") || "";

    if (!pin) {
      message.textContent =
        "ابتدا رمز مدیر را در بخش داشبورد مدیریت وارد و تأیید کنید.";
      return;
    }

    try {
      const response = await fetch("/api/admin/cases", {
        headers: { "X-Admin-Pin": pin },
        cache: "no-store"
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || "دریافت اطلاعات ناموفق بود.");
      }

      cases = Array.isArray(data.cases) ? data.cases : [];

      message.textContent =
        "تعداد پرونده‌های ثبت‌شده: " +
        cases.length.toLocaleString("fa-IR");

      renderTable("", tableWrap, details);

    } catch (error) {
      message.textContent = error.message;
    }
  }

  function renderTable(query, wrap, details) {
    wrap.replaceChildren();
    details.replaceChildren();

    const q = query.trim().toLocaleLowerCase("fa");

    const filtered = cases.filter(item => {
      const patient = item.patient || {};
      const name = String(patient.name || "").toLocaleLowerCase("fa");
      const code = String(item.code || "").toLocaleLowerCase("fa");

      return name.includes(q) || code.includes(q);
    });

    if (!filtered.length) {
      el("p", "پرونده‌ای با این مشخصات پیدا نشد.", wrap);
      return;
    }

    const table = el("table", undefined, wrap);
    table.className = "patients-table";

    const thead = el("thead", undefined, table);
    const header = el("tr", undefined, thead);

    ["نام بیمار", "کد پرونده", "سن", "عملیات"].forEach(title => {
      el("th", title, header);
    });

    const tbody = el("tbody", undefined, table);

    filtered.forEach(item => {
      const patient = item.patient || {};
      const row = el("tr", undefined, tbody);

      el("td", patient.name || "نام ثبت نشده", row);
      el("td", item.code || "—", row);
      el("td", patient.age == null ? "—" :
        Number(patient.age).toLocaleString("fa-IR"), row);

      const cell = el("td", undefined, row);
      const button = el("button", "مشاهده", cell);

      button.addEventListener("click", () => showDetails(item, details));
    });
  }

  function showDetails(item, target) {
    target.replaceChildren();

    const patient = item.patient || {};
    const box = el("div", undefined, target);
    box.className = "patients-details";

    el("h3", "جزئیات پرونده", box);

    const fields = [
      ["نام بیمار", patient.name],
      ["سن", patient.age],
      ["جنسیت", patient.sex],
      ["کد پرونده", item.code],
      ["علائم ثبت‌شده", item.symptoms],
      ["حساسیت‌ها", patient.allergies],
      ["داروهای مصرفی", patient.current_meds],
      ["بیماری‌های زمینه‌ای", patient.conditions]
    ];

    fields.forEach(([label, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        const line = el("div", undefined, box);
        const strong = el("strong", label + ": ", line);
        el("span", String(value), line);
      }
    });

    const close = el("button", "بستن جزئیات", box);
    close.type = "button";
    close.style.cssText =
      "margin-top:12px;padding:8px 14px;background:#475467;color:white;border:0;border-radius:7px;cursor:pointer";
    close.addEventListener("click", () => target.replaceChildren());
  }

  function start() {
    if (document.querySelector(".patients-box")) return;
    createPanel();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
