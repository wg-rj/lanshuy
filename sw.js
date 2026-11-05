/* 澜枢 · 离线缓存（v2：页面走网络优先，更新立刻生效） */
const CACHE = 'jc-pocket-v4';
const ASSETS = ['./index.html', './manifest.json', './icon-192.png', './icon-512.png', './apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
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
  const isDoc = e.request.mode === 'navigate' || url.pathname.endsWith('index.html');

  if (isDoc) {
    // 页面：网络优先，且绕过 HTTP 缓存（GitHub Pages 的 max-age=600 会导致
    // 刚传的新版在手机上十分钟内看不到）。拿不到再用 SW 缓存兜底。
    e.respondWith(
      fetch(e.request, { cache: 'no-store' }).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {});
        return res;
      }).catch(() => caches.match(e.request).then(hit => hit || caches.match('./index.html')))
    );
  } else {
    // 图标等静态资源：缓存优先
    e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request)));
  }
});
