// المتحكّم الرئيسي — يربط المنصّة والـ API والعرض. بلا منطق أعمال.
import { PAGE_SIZE } from "./config.js";
import { platform, applyTheme } from "./platform.js";
import { api, ApiError } from "./api.js";
import { favorites } from "./favorites.js";
import * as ui from "./ui.js";

const $ = (s) => document.querySelector(s);

const state = {
  categories: [],
  facets: { collections: [], models: [], backdrops: [], symbols: [] },
  activeCat: "all",
  sort: "newest",
  q: "",
  filters: {},
  offset: 0,
  hasMore: true,
  loading: false,
  loadedCount: 0,
  favView: false,
};

function queryParams() {
  return {
    limit: PAGE_SIZE, offset: state.offset, sort: state.sort,
    q: state.q || undefined, ...state.filters,
  };
}

function setBusy(v) { $("#grid").setAttribute("aria-busy", String(v)); }

async function loadPage(reset) {
  if (state.loading) return;
  if (state.favView) return loadFavorites();
  if (reset) {
    state.offset = 0; state.hasMore = true; state.loadedCount = 0;
    $("#grid").innerHTML = "";
    $("#grid").appendChild(ui.skeletonCards(8));
    ui.setLoadMore(false);
  }
  if (!state.hasMore) return;
  state.loading = true; setBusy(true);
  if (!reset) ui.footerLoading();

  try {
    const data = await api.listCollectibles(queryParams());
    if (reset) $("#grid").innerHTML = "";
    const frag = document.createDocumentFragment();
    for (const item of data.items) frag.appendChild(ui.cardEl(item, openItem));
    $("#grid").appendChild(frag);

    state.loadedCount += data.items.length;
    state.offset += data.items.length;
    state.hasMore = data.paging.has_more && data.items.length > 0;

    if (state.loadedCount === 0) ui.renderEmpty($("#grid"));
    ui.footerClear();
    ui.setLoadMore(state.hasMore, () => loadPage(false));
    if (!state.hasMore) ui.footerEnd(state.loadedCount);
  } catch (e) {
    handleError(e, reset);
  } finally {
    state.loading = false; setBusy(false);
  }
}

async function loadFavorites() {
  state.loading = true; setBusy(true);
  $("#grid").innerHTML = ""; $("#grid").appendChild(ui.skeletonCards(4));
  ui.setLoadMore(false); ui.footerClear();
  const ids = favorites.list();
  try {
    const results = await Promise.allSettled(ids.map((id) => api.getCollectible(id)));
    const items = results.filter((r) => r.status === "fulfilled" && r.value).map((r) => r.value);
    $("#grid").innerHTML = "";
    if (!items.length) {
      ui.renderEmpty($("#grid"), "لم تُضِف أي مقتنى إلى المفضّلة بعد.");
    } else {
      const frag = document.createDocumentFragment();
      for (const it of items) frag.appendChild(ui.cardEl(it, openItem));
      $("#grid").appendChild(frag);
    }
  } catch (e) {
    handleError(e, true);
  } finally { state.loading = false; setBusy(false); }
}

function handleError(e, reset) {
  if (reset) $("#grid").innerHTML = "";
  ui.footerClear(); ui.setLoadMore(false);
  if (e instanceof ApiError && e.status === 401) {
    ui.renderEmpty($("#grid"), "تعذّر التحقق من هويتك. افتح المتجر من داخل تيليجرام.");
  } else if (e instanceof ApiError && e.status === 503) {
    ui.renderEmpty($("#grid"), "التسعير غير متاح مؤقتاً. أعد المحاولة بعد قليل.");
  } else {
    ui.renderEmpty($("#grid"), "تعذّر الاتصال. تحقّق من الشبكة وأعد المحاولة.");
  }
}

async function openItem(item) {
  ui.openDetails(item, () => ui.toast("الشراء غير متاح بعد — قريباً."));
  try {
    const fresh = await api.getCollectible(item.id);
    if (fresh && !$("#detailSheet").hidden) ui.openDetails(fresh, () => ui.toast("الشراء غير متاح بعد — قريباً."));
  } catch (_) {}
}

function selectCategory(cat) {
  state.activeCat = cat.id;
  state.favView = cat.id === "favorites";
  ui.renderChips(state.categories, state.activeCat, selectCategory);
  if (state.favView) { loadFavorites(); return; }
  if (cat.kind === "sort") { state.sort = cat.sort; loadPage(true); }
  else if (cat.kind === "collections") { openCollectionsPicker(); }
  else { loadPage(true); }
}

function openCollectionsPicker() {
  ui.openFilters(
    { collections: state.facets.collections },
    { collection: state.filters.collection },
    (sel) => {
      state.filters = { ...state.filters };
      if (sel.collection) state.filters.collection = sel.collection; else delete state.filters.collection;
      ui.closeSheets(); syncFilterDot(); loadPage(true);
    }
  );
}

let searchTimer;
function onSearch(val) {
  clearTimeout(searchTimer);
  $("#searchClear").hidden = !val;
  searchTimer = setTimeout(() => {
    state.q = val.trim();
    if (state.favView) { state.favView = false; state.activeCat = "all"; ui.renderChips(state.categories, "all", selectCategory); }
    loadPage(true);
  }, 300);
}

function syncFilterDot() {
  $("#filterDot").hidden = Object.keys(state.filters).length === 0;
}

function setupInfiniteScroll() {
  if (!("IntersectionObserver" in window)) return; // fallback: زر "تحميل المزيد"
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (en.isIntersecting && state.hasMore && !state.loading && !state.favView) loadPage(false);
    }
  }, { rootMargin: "600px 0px" });
  io.observe($("#sentinel"));
}

function setupOffline() {
  const bar = $("#offlineBar");
  const update = () => { bar.hidden = navigator.onLine; };
  window.addEventListener("online", update);
  window.addEventListener("offline", update);
  update();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("./js/sw.js").catch(() => {});
  }
}

async function boot() {
  platform.ready();
  applyTheme();
  platform.onThemeChange(() => applyTheme());
  setupOffline();

  $("#themeToggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    applyTheme(cur === "light" ? "dark" : "light");
    platform.haptic("light");
  });

  $("#searchInput").addEventListener("input", (e) => onSearch(e.target.value));
  $("#searchClear").addEventListener("click", () => {
    $("#searchInput").value = ""; $("#searchClear").hidden = true; state.q = ""; loadPage(true);
  });

  $("#sortBtn").addEventListener("click", () =>
    ui.openSortMenu(state.sort, (id) => { state.sort = id; ui.closeSortMenu(); loadPage(true); }));

  $("#filterBtn").addEventListener("click", () =>
    ui.openFilters(state.facets, state.filters, (sel) => {
      state.filters = sel; ui.closeSheets(); syncFilterDot(); loadPage(true);
    }));

  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { ui.closeSheets(); ui.closeSortMenu(); } });

  setupInfiniteScroll();
  $("#grid").appendChild(ui.skeletonCards(8));

  try {
    const [cats, facets] = await Promise.all([api.getCategories(), api.getFacets()]);
    state.categories = (cats && cats.categories) || defaultCategories();
    state.facets = facets || state.facets;
  } catch (_) {
    state.categories = defaultCategories();
  }
  // فئة المفضّلة (من طرف العميل)
  state.categories = [{ id: "favorites", label: "♥ المفضّلة", kind: "favorites" }, ...state.categories];
  ui.renderChips(state.categories, state.activeCat, selectCategory);

  await loadPage(true);
}

function defaultCategories() {
  return [
    { id: "all", label: "الكل", kind: "sort", sort: "newest" },
    { id: "new", label: "جديد", kind: "sort", sort: "newest" },
    { id: "cheapest", label: "الأرخص", kind: "sort", sort: "cheapest" },
    { id: "expensive", label: "الأغلى", kind: "sort", sort: "expensive" },
    { id: "collections", label: "المجموعات", kind: "collections" },
  ];
}

document.addEventListener("DOMContentLoaded", boot);
