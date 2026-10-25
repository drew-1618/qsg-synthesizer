// Service worker for the installed app. Served from /sw.js so its scope is the
// whole site. It doesn't cache the app or the API: the server is local, and a
// stale page would confuse more than it saves. Its one job is to show
// offline.html instead of the browser's error page when the app isn't running
// (e.g. the user clicked the app icon before starting it).
const CACHE = "qsg-offline-v2";
// Some networks (VPNs, firewalls, WSL) drop connections to a closed port
// instead of refusing them, so a stopped app would otherwise mean a long hang.
// The app answers in milliseconds when it's up, and the offline page reloads
// as soon as it is, so a slow start costs nothing.
const NETWORK_TIMEOUT_MS = 4000;
const OFFLINE_URL = "/static/offline.html";
// Everything offline.html needs to render on its own.
const OFFLINE_ASSETS = [
  OFFLINE_URL,
  "/static/tailwind.css",
  "/static/favicon.svg",
  "/static/fonts/ibm-plex-sans-latin-wght.woff2",
  "/static/fonts/archivo-latin-wdth.woff2",
  "/static/fonts/jetbrains-mono-latin-wght.woff2",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(OFFLINE_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

function fetchWithTimeout(request) {
  const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), NETWORK_TIMEOUT_MS));
  return Promise.race([fetch(request), timeout]);
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  if (request.mode === "navigate") {
    event.respondWith(fetchWithTimeout(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  // The offline page's own CSS and fonts: fresh from the server when it's up.
  const path = new URL(request.url).pathname;
  if (OFFLINE_ASSETS.includes(path)) {
    event.respondWith(fetchWithTimeout(request).catch(() => caches.match(path)));
  }
});
