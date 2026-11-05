/* 澜枢 · 离线缓存（v5：不再拦截页面导航，避免卡在启动图） */
const CACHE = 'jc-pocket-v5';
const ASSETS = ['./manifest.json', './icon-192.png', './icon-512.png', './apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(ASSETS))
      .catch(() => {})
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;

  const url = new URL(e.request.url);

  // 页面导航：完全不接管，直接交给浏览器走网络。
  // 之前这里用 respondWith + 缓存兜底，网络稍慢时导航会一直挂着，
  // TWA 就一直停在启动图。
  if (e.request.mode === 'navigate' || url.pathname.endsWith('.html') || url.pathname.endsWith('/')) {
    return;
  }

  // 同源静态资源（图标等）：缓存优先
  if (url.origin === location.origin) {
    e.respondWith(
      caches.match(e.request).then(hit => hit || fetch(e.request).catch(() => hit))
    );
  }
});
