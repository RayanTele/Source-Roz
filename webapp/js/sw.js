// Service Worker — كاش دون اتصال مع تدهور رشيق.
// القشرة (html/css/js): cache-first. الصور (/media): cache-first.
// الـ API (/api): network-first ثم كاش عند تعذّر الشبكة.

const VERSION = "roz-collectibles-v1";
const SHELL = [
  "./", "./index.html", "./css/app.css",
  "./js/config.js", "./js/platform.js", "./js/api.js",
  "./js/ui.js", "./js/app.js", "./js/favorites.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

function isApi(url) { return url.pathname.startsWith("/api/"); }
function isMedia(url) { return url.pathname.startsWith("/media/"); }

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  if (isApi(url)) {
    // network-first مع رجوع للكاش عند انقطاع الشبكة
    e.respondWith(
      fetch(req)
        .then((resp) => {
          const clone = resp.clone();
          caches.open(VERSION).then((c) => c.put(req, clone)).catch(() => {});
          return resp;
        })
        .catch(() => caches.match(req).then((m) => m || offlineJson()))
    );
    return;
  }

  if (isMedia(url) || SHELL.some((s) => url.pathname.endsWith(s.replace("./", "/")))) {
    // cache-first للقشرة والصور
    e.respondWith(
      caches.match(req).then((m) =>
        m || fetch(req).then((resp) => {
          const clone = resp.clone();
          caches.open(VERSION).then((c) => c.put(req, clone)).catch(() => {});
          return resp;
        }).catch(() => m)
      )
    );
  }
});

function offlineJson() {
  return new Response(JSON.stringify({ error: "offline" }), {
    status: 503, headers: { "Content-Type": "application/json" },
  });
}
