// إعدادات الواجهة — عنوان الـ API الأساسي.
// افتراضياً نفس نطاق الاستضافة (nginx يوجّه /api و /media للخدمة).
// يمكن تجاوزه عبر <meta name="roz-api-base"> أو window.ROZ_API_BASE.

function resolveApiBase() {
  if (typeof window !== "undefined" && window.ROZ_API_BASE) return window.ROZ_API_BASE;
  const meta = document.querySelector('meta[name="roz-api-base"]');
  const val = meta && meta.getAttribute("content");
  return (val || "").replace(/\/+$/, "");
}

export const API_BASE = resolveApiBase();
export const PAGE_SIZE = 24;
