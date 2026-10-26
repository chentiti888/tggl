<?php
// Mini App 入口：浏览器直接打开只显示 nginx 默认页；从 Telegram 里打开（网址带 tgWebAppData）才加载操作台。
$cfg = is_file(__DIR__ . '/config.php') ? (require __DIR__ . '/config.php') : [];
$title = json_encode((string)($cfg['title'] ?? '功能导航'), JSON_UNESCAPED_UNICODE);
$v = max(@filemtime(__DIR__ . '/assets/app.js'), @filemtime(__DIR__ . '/assets/app.css'));
header('Cache-Control: no-cache');
header('X-Robots-Tag: noindex, nofollow');
?><!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
<link rel="manifest" href="manifest.webmanifest?v=<?= (int)@filemtime(__DIR__ . '/manifest.webmanifest') ?>">
<link rel="apple-touch-icon" href="assets/apple-touch-icon.png?v=<?= (int)@filemtime(__DIR__ . '/assets/apple-touch-icon.png') ?>">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="<?= htmlspecialchars((string)($cfg['title'] ?? '功能导航'), ENT_QUOTES) ?>">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<style id="ngx">
html { color-scheme: light dark; }
body { width: 35em; margin: 0 auto;
font-family: Tahoma, Verdana, Arial, sans-serif; }
</style>
<script>
// 控制台模式下，第一帧就涂成最终页面的底色（黑色主题纯黑 / 白色主题 #f2f2f7），避免启动时先闪一下别的颜色；普通浏览器不受影响
(function () {
  var ok = /tgWebAppData=/.test(location.hash);
  try { ok = ok || window.navigator.standalone === true || (window.matchMedia && matchMedia('(display-mode: standalone)').matches); } catch (e) {}
  try { ok = ok || !!sessionStorage.getItem('__telegram__initParams'); } catch (e) {}
  if (!ok) return;
  var light = false;
  try { light = localStorage.getItem('mini_theme') === 'light'; } catch (e) {}
  var bg = light ? '#f2f2f7' : '#000000', r = document.documentElement;
  r.setAttribute('data-theme', light ? 'light' : 'dark');
  r.style.background = bg;
  r.style.colorScheme = light ? 'light' : 'dark';
  document.write('<style id="boot">html,body{background:' + bg + ' !important}<\/style>');
})();
</script>
</head>
<body>
<h1>Welcome to nginx!</h1>
<p>If you see this page, the nginx web server is successfully installed and
working. Further configuration is required.</p>

<p>For online documentation and support please refer to
<a href="http://nginx.org/">nginx.org</a>.<br/>
Commercial support is available at
<a href="http://nginx.com/">nginx.com</a>.</p>

<p><em>Thank you for using nginx.</em></p>
<script>
(function () {
  var ok = /tgWebAppData=/.test(location.hash);
  // 添加到主屏幕后以独立 App 方式打开：显示控制台（先要配对码登录）；普通浏览器依旧只看到 nginx 页
  try { ok = ok || window.navigator.standalone === true || (window.matchMedia && matchMedia('(display-mode: standalone)').matches); } catch (e) {}
  try { ok = ok || !!sessionStorage.getItem('__telegram__initParams'); } catch (e) {}
  if (!ok) return;
  // 从主屏幕图标打开时启用缓存（sw.js），下次打开少等网络；Telegram 里不启用
  try {
    if ('serviceWorker' in navigator && (window.navigator.standalone === true || matchMedia('(display-mode: standalone)').matches))
      navigator.serviceWorker.register('sw.js?v=<?= (int)@filemtime(__DIR__ . '/sw.js') ?>', { updateViaCache: 'none' }).catch(function () {});
  } catch (e) {}
  var d = document, h = d.head, v = '<?= (int)$v ?>';
  d.getElementById('ngx').remove();
  d.title = <?= $title ?>;
  d.documentElement.lang = 'zh-CN';
  var light = false;
  try { light = localStorage.getItem('mini_theme') === 'light'; } catch (e) {}
  d.documentElement.setAttribute('data-theme', light ? 'light' : 'dark');
  function meta(n, c) { var m = d.createElement('meta'); m.name = n; m.content = c; h.appendChild(m); }
  meta('viewport', 'width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover');
  meta('theme-color', light ? '#f2f2f7' : '#000000');
  var css = d.createElement('link'); css.rel = 'stylesheet'; css.href = 'assets/app.css?v=' + v; css.onload = function () { d.documentElement.style.background = ''; d.documentElement.style.colorScheme = ''; }; h.appendChild(css);
  d.body.innerHTML = '<div id="app"></div><div class="tabbar" id="tabbar"></div>';
  var boot = d.getElementById('boot'); if (boot) boot.remove();   // 底色已写在 <html> 上，等 app.css 接手
  function js(src, next) { var s = d.createElement('script'); s.src = src; s.onload = next; s.onerror = next; d.body.appendChild(s); }
  js('https://telegram.org/js/telegram-web-app.js', function () { js('assets/app.js?v=' + v); });
})();
</script>
</body>
</html>
