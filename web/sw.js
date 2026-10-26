/* 控制台主屏幕 App 的缓存（只在从主屏幕图标打开时注册）
 * - 页面本身（/ 和 index.php）：先联网取最新，1.5 秒没回来才用上次存的，保证改了代码马上生效
 * - assets/ 下带 ?v= 的文件：网址里就是修改时间，内容变了网址就变，存一次一直用；同一个文件的旧版本自动删掉
 * - Telegram 的脚本：先用存的，后台顺手更新
 * - 接口（api.php / speed.php / probe）和其他请求一律不碰
 */
var CACHE = 'console-v1';   // 改这个名字会清空旧缓存

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

function put(req, res) {
  if (!res || !res.ok || res.type === 'opaqueredirect') return;
  var copy = res.clone();
  caches.open(CACHE).then(function (c) { return c.put(req, copy); });
}

function pageFirstNetwork(e) {
  var key = new Request(self.registration.scope);           // 页面只存一份
  return new Promise(function (resolve) {
    var done = false;
    function give(r) { if (!done && r) { done = true; resolve(r); } }
    var t = setTimeout(function () {
      caches.match(key).then(function (r) { give(r); });
    }, 1500);
    fetch(e.request).then(function (res) {
      clearTimeout(t);
      if (res.ok) put(key, res);
      give(res);
    }, function () {
      clearTimeout(t);
      caches.match(key).then(function (r) { give(r || Response.error()); });
    });
  });
}

function assetCacheFirst(e, url) {
  return caches.open(CACHE).then(function (c) {
    return c.match(e.request).then(function (hit) {
      if (hit) return hit;
      return fetch(e.request).then(function (res) {
        if (res.ok) {
          c.put(e.request, res.clone());
          // 删掉同一个文件的旧版本
          c.keys().then(function (ks) {
            ks.forEach(function (k) {
              var u = new URL(k.url);
              if (u.pathname === url.pathname && u.search !== url.search) c.delete(k);
            });
          });
        }
        return res;
      });
    });
  });
}

function staleWhileRevalidate(e) {
  return caches.open(CACHE).then(function (c) {
    return c.match(e.request).then(function (hit) {
      var net = fetch(e.request).then(function (res) { if (res.ok || res.type === 'opaque') c.put(e.request, res.clone()); return res; });
      if (hit) { net.catch(function () {}); return hit; }
      return net;
    });
  });
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin === self.location.origin) {
    if (/\/(api|speed|probe)\.php$|\/probe\.(py|sh)$|\/sw\.js$/.test(url.pathname)) return;
    if (req.mode === 'navigate' && (url.pathname === '/' || url.pathname === '/index.php')) { e.respondWith(pageFirstNetwork(e)); return; }
    if (url.pathname.indexOf('/assets/') === 0 && url.searchParams.has('v')) { e.respondWith(assetCacheFirst(e, url)); return; }
    return;
  }
  if (url.href.indexOf('https://telegram.org/js/telegram-web-app.js') === 0) { e.respondWith(staleWhileRevalidate(e)); return; }
});

/* ---- 推送：服务器离线 / 恢复提醒 ---- */
self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : '' }; }
  // 桌面图标右上角的红色数字 = 现在离线的服务器台数（0 就去掉）
  var badge = Promise.resolve();
  try {
    if (typeof d.badge === 'number' && self.navigator.setAppBadge) {
      badge = (d.badge > 0 ? self.navigator.setAppBadge(d.badge) : self.navigator.clearAppBadge()).catch(function () {});
    }
  } catch (err) {}
  e.waitUntil(Promise.all([badge, self.registration.showNotification(d.title || '功能导航', {
    body: d.body || '',
    tag: d.tag || undefined,          // 同一台服务器的提醒互相替换，不会堆一排
    icon: 'assets/icon-192.png',
    data: { url: d.url || '/' }
  })]));
});
self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) { if ('focus' in list[i]) return list[i].focus(); }
    return self.clients.openWindow(url);
  }));
});
