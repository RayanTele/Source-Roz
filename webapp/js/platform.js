// طبقة المنصّة (Platform Adapter).
// تعزل منطق Telegram كي يعمل نفس الواجهة داخل Telegram WebApp أو كموقع مستقل
// مستقبلاً دون إعادة تصميم. لا منطق أعمال هنا — عرض/هوية/سمة فقط.

const tg = (typeof window !== "undefined" && window.Telegram && window.Telegram.WebApp) || null;

class TelegramPlatform {
  constructor(api) { this.api = api; }
  get name() { return "telegram"; }
  ready() {
    try { this.api.ready(); this.api.expand(); } catch (_) {}
  }
  // ترويسة المصادقة: initData موقّع من Telegram
  authHeader() {
    const d = this.api.initData || "";
    return d ? { Authorization: "tma " + d } : {};
  }
  get colorScheme() { return this.api.colorScheme === "light" ? "light" : "dark"; }
  onThemeChange(cb) {
    try { this.api.onEvent("themeChanged", cb); } catch (_) {}
  }
  setHeaderColor(hex) {
    try { this.api.setHeaderColor(hex); this.api.setBackgroundColor(hex); } catch (_) {}
  }
  haptic(kind = "light") {
    try { this.api.HapticFeedback.impactOccurred(kind); } catch (_) {}
  }
}

class WebPlatform {
  // وضع الموقع المستقل (مستقبلاً). المصادقة عبر جلسة ويب تُضاف لاحقاً.
  get name() { return "web"; }
  ready() {}
  authHeader() {
    // مؤقتاً: يسمح بتمرير توكن تطوير عبر window.ROZ_DEV_INIT_DATA (اختياري)
    const dev = typeof window !== "undefined" ? window.ROZ_DEV_INIT_DATA : null;
    return dev ? { Authorization: "tma " + dev } : {};
  }
  get colorScheme() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  onThemeChange(cb) {
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", cb);
    }
  }
  setHeaderColor() {}
  haptic() {}
}

export const platform = tg ? new TelegramPlatform(tg) : new WebPlatform();

// تطبيق السمة (light/dark) على الجذر + لون ترويسة Telegram
export function applyTheme(scheme) {
  const s = scheme || platform.colorScheme;
  document.documentElement.setAttribute("data-theme", s);
  const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta && bg) meta.setAttribute("content", bg);
  platform.setHeaderColor(bg || (s === "light" ? "#f6efee" : "#160f18"));
}
