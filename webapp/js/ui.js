// طبقة العرض فقط — بلا منطق أعمال. كل الأسعار تأتي جاهزة من الـ API.
import { platform } from "./platform.js";
import { favorites } from "./favorites.js";

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };

// ---------- تنبيه ----------
let toastTimer;
export function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 2400);
}

// ---------- هياكل تحميل ----------
export function skeletonCards(n) {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < n; i++) {
    const c = el("div", "card skel");
    c.setAttribute("aria-hidden", "true");
    c.innerHTML =
      '<div class="card-niche"></div>' +
      '<div class="card-body"><div class="skel-line w60"></div>' +
      '<div class="skel-line w40"></div><div class="skel-line w30"></div></div>';
    frag.appendChild(c);
  }
  return frag;
}

// ---------- قلب المفضّلة ----------
function favButton(item, extraClass) {
  const b = el("button", "fav-btn" + (extraClass ? " " + extraClass : ""));
  const on = favorites.has(item.id);
  b.setAttribute("aria-pressed", String(on));
  b.setAttribute("aria-label", on ? "إزالة من المفضّلة" : "إضافة إلى المفضّلة");
  b.innerHTML = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 20s-7-4.4-9.2-8.3C1.1 8.3 2.6 5 5.8 5c1.9 0 3.2 1.1 4.2 2.3C11 6.1 12.3 5 14.2 5 17.4 5 18.9 8.3 21.2 11.7 19 15.6 12 20 12 20Z"/></svg>';
  b.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const now = favorites.toggle(item.id);
    b.setAttribute("aria-pressed", String(now));
    b.setAttribute("aria-label", now ? "إزالة من المفضّلة" : "إضافة إلى المفضّلة");
    platform.haptic("light");
  });
  return b;
}

// ---------- بطاقة ----------
function priceTag(price) {
  const w = el("div", "price-tag");
  w.innerHTML =
    '<span class="price-amount">' + formatAmount(price.amount) + "</span>" +
    '<span class="price-cur">' + escapeHtml(price.currency) + "</span>";
  return w;
}

export function cardEl(item, onOpen) {
  const c = el("article", "card");
  c.setAttribute("role", "listitem");
  c.tabIndex = 0;
  const priceLabel = formatAmount(item.price.amount) + " " + item.price.currency;
  c.setAttribute("aria-label",
    (item.title || "مقتنى") + (item.collection ? "، " + item.collection : "") +
    (item.is_locked ? "، مقفول" : "") + "، السعر " + priceLabel);

  const niche = el("div", "card-niche");
  const imgUrl = (item.images && item.images.thumb) || item.thumbnail_url;
  if (imgUrl) {
    const img = el("img", "card-img");
    img.loading = "lazy"; img.decoding = "async"; img.alt = "";
    img.src = imgUrl;
    img.addEventListener("load", () => img.classList.add("loaded"));
    img.addEventListener("error", () => { niche.classList.add("blank"); img.remove(); });
    niche.appendChild(img);
  } else { niche.classList.add("blank"); }

  niche.appendChild(favButton(item));

  if (item.is_locked) {
    const lb = el("span", "lock-badge");
    lb.setAttribute("aria-hidden", "true");
    lb.innerHTML = '<svg viewBox="0 0 24 24" width="10" height="10" fill="currentColor"><path d="M6 10V8a6 6 0 1 1 12 0v2h1v11H5V10h1Zm2 0h8V8a4 4 0 0 0-8 0v2Z"/></svg> مقفول';
    niche.appendChild(lb);
  }
  c.appendChild(niche);

  const body = el("div", "card-body");
  const title = el("div", "card-title"); title.textContent = item.title || item.collection || "مقتنى";
  const coll = el("div", "card-coll"); coll.textContent = item.collection || "";
  body.appendChild(title); body.appendChild(coll);
  if (item.number != null) {
    const num = el("div", "card-num"); num.textContent = "#" + item.number; body.appendChild(num);
  }
  body.appendChild(priceTag(item.price));
  c.appendChild(body);

  const open = () => { platform.haptic("light"); onOpen(item); };
  c.addEventListener("click", open);
  c.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
  });
  return c;
}

// ---------- فئات (chips) ----------
export function renderChips(categories, activeId, onSelect) {
  const nav = $("#chips"); nav.innerHTML = "";
  for (const cat of categories) {
    const b = el("button", "chip");
    b.textContent = cat.label; b.dataset.id = cat.id;
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(cat.id === activeId));
    b.addEventListener("click", () => { platform.haptic("light"); onSelect(cat); });
    nav.appendChild(b);
  }
}

// ---------- الحالات ----------
export function renderEmpty(container, msg) {
  container.innerHTML =
    '<div class="empty" style="grid-column:1/-1"><div style="font-size:34px" aria-hidden="true">🔍</div>' +
    '<h3>لا توجد نتائج</h3><p>' + escapeHtml(msg || "جرّب تعديل البحث أو الفلاتر.") + "</p></div>";
}
export function footerLoading() { $("#gridFooter").innerHTML = '<div class="spinner" role="status" aria-label="جارٍ التحميل"></div>'; }
export function footerEnd(count) {
  $("#gridFooter").innerHTML = count > 0 ? '<span class="end-note">— وصلت إلى النهاية —</span>' : "";
}
export function footerClear() { $("#gridFooter").innerHTML = ""; }

export function setLoadMore(visible, onClick) {
  const wrap = $("#loadMoreWrap");
  wrap.hidden = !visible;
  const btn = $("#loadMoreBtn");
  btn.onclick = visible ? onClick : null;
}

// ---------- إدارة تركيز الأوراق ----------
let lastFocused = null;
function trapFocus(sheet) {
  const focusables = sheet.querySelectorAll('button, [href], input, [tabindex]:not([tabindex="-1"])');
  if (focusables.length) focusables[0].focus();
  sheet._trap = (e) => {
    if (e.key === "Escape") { closeSheets(); return; }
    if (e.key !== "Tab" || !focusables.length) return;
    const first = focusables[0], last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  sheet.addEventListener("keydown", sheet._trap);
}

// ---------- ورقة التفاصيل ----------
export function openDetails(item, onBuy) {
  lastFocused = document.activeElement;
  const sheet = $("#detailSheet"), back = $("#sheetBackdrop");
  sheet.setAttribute("aria-labelledby", "detailTitle");
  const attrs = [
    ["المجموعة", item.collection], ["الموديل", item.model],
    ["الخلفية", item.backdrop], ["الرمز", item.symbol],
  ].filter(([, v]) => v);
  if (item.number != null) attrs.push(["الرقم", "#" + item.number]);
  attrs.push(["الحالة", item.is_locked ? "مقفول" : "متاح"]);
  if (item.is_locked && item.unlock_date) attrs.push(["تاريخ الفتح", formatDate(item.unlock_date)]);
  const big = (item.images && item.images.original) || item.thumbnail_url;

  sheet.innerHTML =
    '<div class="sheet-grip" aria-hidden="true"></div><button class="sheet-close" aria-label="إغلاق">&times;</button>' +
    '<div class="detail-hero">' + (big
      ? '<img src="' + escapeAttr(big) + '" alt="' + escapeAttr(item.title || "") + '">'
      : '<span aria-hidden="true">💠</span>') + "</div>" +
    '<div style="display:flex;align-items:center;gap:10px">' +
      '<h2 class="detail-title" id="detailTitle">' + escapeHtml(item.title || "مقتنى") + "</h2></div>" +
    '<div class="detail-coll">' + escapeHtml(item.collection || "") + "</div>" +
    '<div class="attr-grid">' +
      attrs.map(([k, v]) =>
        '<div class="attr"><div class="attr-k">' + escapeHtml(k) + '</div><div class="attr-v">' + escapeHtml(String(v)) + "</div></div>"
      ).join("") + "</div>" +
    '<div class="detail-price"><span class="lbl">السعر</span>' +
      '<span class="val">' + formatAmount(item.price.amount) + " <small>" + escapeHtml(item.price.currency) + "</small></span></div>" +
    '<div style="display:flex;gap:10px;align-items:stretch">' +
      '<button class="btn-buy disabled" id="buyBtn" style="flex:1">شراء</button>' +
      '<span id="favSlot"></span></div>';

  sheet.querySelector("#favSlot").appendChild(favButton(item, "detail-fav"));
  sheet.querySelector(".sheet-close").addEventListener("click", closeSheets);
  sheet.querySelector("#buyBtn").addEventListener("click", () => { platform.haptic("medium"); onBuy(item); });
  back.hidden = false; sheet.hidden = false;
  back.onclick = closeSheets;
  trapFocus(sheet);
}

export function closeSheets() {
  for (const id of ["detailSheet", "sheetBackdrop", "filterSheet", "filterBackdrop"]) {
    const e = document.getElementById(id);
    if (e) { if (e._trap) { e.removeEventListener("keydown", e._trap); e._trap = null; } e.hidden = true; }
  }
  if (lastFocused && lastFocused.focus) { try { lastFocused.focus(); } catch (_) {} }
}

// ---------- ورقة الفلاتر ----------
export function openFilters(facets, current, onApply) {
  lastFocused = document.activeElement;
  const sheet = $("#filterSheet"), back = $("#filterBackdrop");
  sheet.setAttribute("aria-labelledby", "filterHeading");
  const sel = { ...current };
  const block = (title, key, values) => {
    if (!values || !values.length) return "";
    return '<div class="filter-block" data-key="' + key + '"><label>' + title + '</label><div class="opt-row">' +
      values.slice(0, 40).map((v) =>
        '<button class="opt" data-val="' + escapeAttr(v) + '" aria-pressed="' + (sel[key] === v) + '">' + escapeHtml(v) + "</button>"
      ).join("") + "</div></div>";
  };
  sheet.innerHTML =
    '<div class="sheet-grip" aria-hidden="true"></div><button class="sheet-close" aria-label="إغلاق">&times;</button>' +
    '<h3 class="sheet-h" id="filterHeading">تصفية</h3>' +
    block("المجموعة", "collection", facets.collections) +
    block("الموديل", "model", facets.models) +
    block("الخلفية", "backdrop", facets.backdrops) +
    block("الرمز", "symbol", facets.symbols) +
    '<div class="filter-block" data-key="locked"><label>الحالة</label><div class="opt-row">' +
      '<button class="opt" data-val="0" aria-pressed="' + (sel.locked === "0") + '">متاح</button>' +
      '<button class="opt" data-val="1" aria-pressed="' + (sel.locked === "1") + '">مقفول</button></div></div>' +
    '<div class="filter-block"><label>نطاق السعر</label><div class="price-range">' +
      '<input id="pmin" type="number" inputmode="decimal" aria-label="أدنى سعر" placeholder="من" value="' + escapeAttr(sel.price_min || "") + '">' +
      '<input id="pmax" type="number" inputmode="decimal" aria-label="أعلى سعر" placeholder="إلى" value="' + escapeAttr(sel.price_max || "") + '"></div></div>' +
    '<div class="filter-actions"><button class="btn-ghost" id="clearFilters">مسح</button>' +
      '<button class="btn-apply" id="applyFilters">تطبيق</button></div>';

  sheet.querySelectorAll(".opt").forEach((btn) => {
    btn.addEventListener("click", () => {
      const group = btn.closest("[data-key]");
      const key = group.dataset.key;
      const active = btn.getAttribute("aria-pressed") === "true";
      group.querySelectorAll(".opt").forEach((o) => o.setAttribute("aria-pressed", "false"));
      if (active) { delete sel[key]; }
      else { btn.setAttribute("aria-pressed", "true"); sel[key] = btn.dataset.val; }
    });
  });
  sheet.querySelector(".sheet-close").addEventListener("click", closeSheets);
  sheet.querySelector("#clearFilters").addEventListener("click", () => onApply({}));
  sheet.querySelector("#applyFilters").addEventListener("click", () => {
    const pmin = sheet.querySelector("#pmin").value.trim();
    const pmax = sheet.querySelector("#pmax").value.trim();
    if (pmin) sel.price_min = pmin; else delete sel.price_min;
    if (pmax) sel.price_max = pmax; else delete sel.price_max;
    onApply(sel);
  });
  back.hidden = false; sheet.hidden = false; back.onclick = closeSheets;
  trapFocus(sheet);
}

// ---------- قائمة الترتيب ----------
const SORTS = [
  { id: "newest", label: "الأحدث" }, { id: "oldest", label: "الأقدم" },
  { id: "cheapest", label: "الأرخص سعراً" }, { id: "expensive", label: "الأغلى سعراً" },
];
export function openSortMenu(current, onPick) {
  const menu = $("#sortMenu"), back = $("#sortBackdrop");
  menu.innerHTML = SORTS.map((s) =>
    '<div class="sort-item" role="menuitemradio" tabindex="0" data-id="' + s.id + '" aria-selected="' + (s.id === current) + '">' +
    "<span>" + s.label + "</span>" + (s.id === current ? '<span class="tick" aria-hidden="true">✓</span>' : "") + "</div>"
  ).join("");
  const pick = (id) => { platform.haptic("light"); onPick(id); };
  menu.querySelectorAll(".sort-item").forEach((it) => {
    it.addEventListener("click", () => pick(it.dataset.id));
    it.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(it.dataset.id); } });
  });
  back.hidden = false; menu.hidden = false;
  const firstItem = menu.querySelector(".sort-item"); if (firstItem) firstItem.focus();
  back.onclick = closeSortMenu;
}
export function closeSortMenu() { $("#sortMenu").hidden = true; $("#sortBackdrop").hidden = true; }

// ---------- أدوات ----------
function formatAmount(a) {
  const n = Number(a);
  if (!isFinite(n)) return String(a);
  return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}
function formatDate(iso) {
  try { return new Date(iso).toLocaleDateString("ar", { year: "numeric", month: "short", day: "numeric" }); }
  catch (_) { return iso; }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }
