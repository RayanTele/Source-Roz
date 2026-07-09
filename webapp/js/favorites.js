// المفضّلة — تخزين محلي (localStorage) على جهاز المستخدم فقط.
const KEY = "roz_favorites_v1";

function read() {
  try { return new Set(JSON.parse(localStorage.getItem(KEY) || "[]")); }
  catch (_) { return new Set(); }
}
function write(set) {
  try { localStorage.setItem(KEY, JSON.stringify([...set])); } catch (_) {}
}

export const favorites = {
  has(id) { return read().has(id); },
  list() { return [...read()]; },
  count() { return read().size; },
  toggle(id) {
    const s = read();
    if (s.has(id)) s.delete(id); else s.add(id);
    write(s);
    return s.has(id);
  },
};
