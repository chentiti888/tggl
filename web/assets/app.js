/* 功能导航 Mini App 前端（原生 JS，无依赖） */
(function () {
  'use strict';
  var tg = (window.Telegram && Telegram.WebApp) || null;
  if (tg) { try { tg.ready(); tg.expand(); } catch (e) {} }
  var root = document.documentElement;
  try { if (window.navigator.standalone === true || matchMedia('(display-mode: standalone)').matches) root.classList.add('standalone'); } catch (e) {}
  // 主屏幕 App：整页一旦被系统推动（滚到底回弹卡住等），立刻拉回原位，导航栏就不会跑到中间
  if (root.classList.contains('standalone')) {
    var typing = function () { var a = document.activeElement; return !!a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName); };
    // 正在输入时不拉回：键盘弹出来时系统要把页面往上推，好让输入框露在键盘上面
    var fixTop = function () { if (!typing() && (window.scrollY || window.pageYOffset || document.documentElement.scrollTop)) window.scrollTo(0, 0); };
    document.addEventListener('focusout', function () { setTimeout(fixTop, 300); });
    window.addEventListener('scroll', fixTop, { passive: true });
    window.addEventListener('touchend', function () { setTimeout(fixTop, 350); }, { passive: true });
    if (window.visualViewport) window.visualViewport.addEventListener('resize', function () { setTimeout(fixTop, 50); });
  }
  var scheme = 'dark';   // 默认黑色；首页时钟后面的按钮可切换黑 / 白，选择记在本机
  try { if (localStorage.getItem('mini_theme') === 'light') scheme = 'light'; } catch (e) {}
  function applyTheme() {
    root.setAttribute('data-theme', scheme === 'light' ? 'light' : 'dark');
    try { if (tg) { var bgc = scheme === 'light' ? '#e8e9ec' : '#000000', hdc = scheme === 'light' ? '#dcdee2' : '#141414'; tg.setHeaderColor(hdc); tg.setBackgroundColor(bgc); if (tg.setBottomBarColor) tg.setBottomBarColor(bgc); } } catch (e) {}
  }
  function setScheme(s) { scheme = s; try { localStorage.setItem('mini_theme', s); } catch (e) {} applyTheme(); }
  applyTheme();

  // ------------------------------------------------------------ 工具函数
  function el(tag, attrs) {
    var n = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      var v = attrs[k];
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.slice(0, 2) === 'on') n.addEventListener(k.slice(2), v);
      else if (k === 'style') n.style.cssText = v;
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (var i = 2; i < arguments.length; i++) add(n, arguments[i]);
    return n;
  }
  function add(n, c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { add(n, x); }); return; }
    n.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  function hap(kind) { try { tg && tg.HapticFeedback && (kind === 'ok' || kind === 'err' ? tg.HapticFeedback.notificationOccurred(kind === 'ok' ? 'success' : 'error') : tg.HapticFeedback.impactOccurred('light')); } catch (e) {} }
  function toast(msg, err) {
    var t = el('div', { class: 'toast' + (err ? ' err' : ''), text: msg });
    document.body.appendChild(t);
    hap(err ? 'err' : 'ok');
    setTimeout(function () { t.remove(); }, 2600);
  }
  function fmtBytes(n) {
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
    n = Number(n) || 0;
    while (n >= 1024 && i < 4) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : Math.round(n)) + ' ' + u[i];
  }
  function copy(text) {
    function done() { toast(text.length <= 45 ? '已复制 ' + text : '已复制'); }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
    function fallback() {
      var t = el('textarea', { style: 'position:fixed;opacity:0' }); t.value = text; document.body.appendChild(t);
      t.select(); try { document.execCommand('copy'); done(); } catch (e) { toast('复制失败', true); } t.remove();
    }
  }
  // ------------------------------------------------------------ 页面里所有 IP / 域名都能点击复制
  // 自动找出文字里的 IPv4 / IPv6 / 域名，包成可点的小块；按钮、输入框里的不处理（避免和按钮自己的点击冲突）
  var IP_RE = /(\b(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}\b)|((?:[0-9a-fA-F]{1,4}:){1,7}:?(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4})*)?|::(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4})*)?)|((?:\*\.)?(?:[a-zA-Z0-9_](?:[a-zA-Z0-9_-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,24}\b)/g;
  // 这些后缀是文件名，不是域名（cc 是真实的域名后缀，不在这里）
  var NOT_TLD = /\.(js|css|sh|py|json|jsonl|md|php|txt|log|conf|cfg|ini|html?|png|jpe?g|gif|svg|webp|ico|gz|tgz|zip|tar|xz|xdb|service|bak|tmp|exe|so|ya?ml|toml|lock|pid|sock|csv|xml|pem|key|crt|db|sql|deb|rpm|apk|ipa|dmg)$/i;
  // 想让某块区域不被识别：给它加 class="nocopy"（以后做「整块可点」的卡片时用得上）
  var IP_SKIP = 'button,a,input,textarea,select,script,style,.ipc,.tapcopy,.nocopy,[contenteditable]';
  function isIp6(t) { return t.indexOf('::') >= 0 ? (t.match(/:/g) || []).length >= 2 && /[0-9a-f]/i.test(t) : (t.match(/:/g) || []).length >= 4; }
  function markIps(rootNode) {
    if (!rootNode || rootNode.nodeType !== 1 && rootNode.nodeType !== 3) return;
    var texts = [];
    if (rootNode.nodeType === 3) texts.push(rootNode);
    else {
      if (rootNode.closest && rootNode.closest(IP_SKIP)) return;
      var w = document.createTreeWalker(rootNode, NodeFilter.SHOW_TEXT, null);
      while (w.nextNode()) texts.push(w.currentNode);
    }
    texts.forEach(function (tn) {
      var t = tn.nodeValue;
      if (!t || t.length < 3 || !/[.:]/.test(t)) return;
      var par = tn.parentNode;
      if (!par || par.nodeType !== 1 || par.closest(IP_SKIP)) return;
      IP_RE.lastIndex = 0;
      var m, last = 0, frag = null;
      while ((m = IP_RE.exec(t))) {
        var ip = m[0];
        if (m[2] && !isIp6(ip)) { if (!ip) IP_RE.lastIndex++; continue; }
        if (m[3] && NOT_TLD.test(ip)) continue;                 // 文件名不算域名
        if (m[3] && t.charAt(m.index - 1) === '@') continue;       // 邮箱地址不处理
        frag = frag || document.createDocumentFragment();
        if (m.index > last) frag.appendChild(document.createTextNode(t.slice(last, m.index)));
        frag.appendChild(el('span', { class: 'ipc', text: ip }));
        last = m.index + ip.length;
      }
      if (!frag) return;
      if (last < t.length) frag.appendChild(document.createTextNode(t.slice(last)));
      par.replaceChild(frag, tn);
    });
  }
  document.addEventListener('click', function (e) {
    var n = e.target && e.target.closest && e.target.closest('.ipc');
    if (n) { e.stopPropagation(); if (n.closest('label')) e.preventDefault(); copy(n.textContent); }
  });
  try {
    new MutationObserver(function (list) {
      list.forEach(function (mu) { for (var i = 0; i < mu.addedNodes.length; i++) markIps(mu.addedNodes[i]); });
    }).observe(document.body, { childList: true, subtree: true });
  } catch (e) {}

  function openLink(url) {
    if (tg && tg.openLink) tg.openLink(url); else window.open(url, '_blank', 'noopener');
  }
  function timeText(ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000), p = function (x) { return (x < 10 ? '0' : '') + x; };
    return (d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }
  function zoomImg(src) {
    var z = el('div', { class: 'zoom', onclick: function () { z.remove(); } }, el('img', { src: src }));
    document.body.appendChild(z);
  }
  function mapImg(src) { return src ? el('img', { class: 'mapimg', src: src, alt: '地图', onclick: function () { zoomImg(src); } }) : null; }

  // ------------------------------------------------------------ 接口
  var initData = (tg && tg.initData) || '';
  // 主屏幕 App（不在 Telegram 里）用「设备令牌」登录：令牌只存在本机，服务器那边可随时撤销
  var devTok = '';
  try { devTok = localStorage.getItem('mini_dev') || ''; } catch (e) {}
  if (!/^[0-9a-f]{64}$/.test(devTok)) devTok = '';
  function authHeaders(h) {
    h = h || {};
    if (initData) h['X-Init-Data'] = initData; else if (devTok) h['X-Device-Token'] = devTok;
    return h;
  }
  function devLost() {          // 令牌被撤销 / 过期：清掉，回到配对页
    devTok = '';
    try { localStorage.removeItem('mini_dev'); } catch (e) {}
    setTimeout(function () { location.reload(); }, 800);
  }
  // 下拉刷新期间追踪 API 请求，等页面数据请求结束后再开始收尾。
  var ptrRefreshTracking = false, ptrRefreshPending = 0, ptrRefreshLastDone = 0, ptrRefreshGeneration = 0;
  function api(action, params, tries) {
    tries = tries == null ? 2 : tries;
    // 会改数据 / 会开任务的请求不自动重发，免得重复提交
    var safe = !/_edit$|^job_start$|^job_cancel$/.test(action);
    var httpSt = 0;
    var request = fetch('api.php?a=' + encodeURIComponent(action), {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(params || {})
    }).then(function (r) { if (cfChallenged(r)) return cfPass(); httpSt = r.status; return r.text(); }, function () {
      // 手机网络抖动 / 连接被系统掐断时 fetch 会直接失败（Safari 显示 Load failed）：自动重试，实在不行再提示
      if (safe && tries > 0) return new Promise(function (res) { setTimeout(res, 700); }).then(function () { return api(action, params, tries - 1); }).then(function (d) { return { __data: d }; });
      throw new Error('网络不稳定，请检查网络后重试');
    }).then(function (t) {
      if (t && t.__data !== undefined) return t.__data;
      var j; try { j = JSON.parse(t); } catch (e) { throw new Error('服务器没有正确响应' + (httpSt ? '（HTTP ' + httpSt + '）' : '')); }
      if (!j.ok) {
        if (!initData && devTok && /设备登录已失效/.test(j.error || '')) { devLost(); toast('设备登录已失效，请重新配对', true); }
        throw new Error(j.error || '请求失败');
      }
      return j.data;
    });
    var tracked = ptrRefreshTracking;
    var trackedGeneration = ptrRefreshGeneration;
    if (tracked) ptrRefreshPending++;
    return request.then(function (data) {
      if (tracked && trackedGeneration === ptrRefreshGeneration) { ptrRefreshPending = Math.max(0, ptrRefreshPending - 1); ptrRefreshLastDone = Date.now(); }
      return data;
    }, function (err) {
      if (tracked && trackedGeneration === ptrRefreshGeneration) { ptrRefreshPending = Math.max(0, ptrRefreshPending - 1); ptrRefreshLastDone = Date.now(); }
      throw err;
    });
  }
  // 网站开了 5 秒盾（或安全级别很高）时，页面后台请求会收到 Cloudflare 的检查页而不是数据。
  // 这时整页刷新一次：浏览器过一下验证、拿到通行 Cookie，之后的请求就正常了；刷新后回到原来的页面。
  function cfChallenged(r) {
    if (r.headers.get('cf-mitigated') === 'challenge') return true;
    var ct = r.headers.get('content-type') || '';
    return (r.status === 403 || r.status === 503 || r.status === 429) && ct.indexOf('text/html') >= 0;
  }
  function cfPass() {
    var last = 0;
    try { last = +sessionStorage.getItem('mini_cf_pass') || 0; } catch (e) {}
    if (Date.now() - last < 20000) throw new Error('网站开着 5 秒盾，验证没通过：请彻底关闭 Mini App 后重新打开');
    try {
      sessionStorage.setItem('mini_cf_pass', String(Date.now()));
      if (cur) sessionStorage.setItem('mini_resume', JSON.stringify({ tab: cur.tab, sub: cur.sub, t: Date.now() }));
    } catch (e) {}
    toast('网站开着 5 秒盾，正在重新验证…');
    setTimeout(function () { location.reload(); }, 600);
    return new Promise(function () {});      // 等刷新，不再往下走
  }
  function run(btn, fn) {   // 按钮点击期间禁用，出错弹提示；返回 true=成功
    if (btn) btn.setAttribute('disabled', '');
    return Promise.resolve().then(fn).then(function () { return true; }, function (e) { toast(e.message, true); return false; })
      .then(function (okk) { if (btn) btn.removeAttribute('disabled'); return okk; });
  }

  // ------------------------------------------------------------ 弹层
  function sheet(title, fields, okText, onOk, extra) {
    var inputs = {};
    var bg = el('div', { class: 'sheet-bg', onclick: function (e) { if (e.target === bg) bg.remove(); } });
    var body = el('div', { class: 'sheet' }, el('h2', { text: title }));
    var wraps = {};
    (fields || []).forEach(function (f) {
      var inp, wrap;
      if (f.options) {          // 下拉选择：options = [[值, 显示文字], ...]
        inp = el('select', { class: 'input' });
        f.options.forEach(function (o) { inp.appendChild(el('option', { value: String(o[0]), text: o[1] })); });
        inp.value = String(f.value == null ? f.options[0][0] : f.value);
        wrap = el('div', { class: 'f' }, f.label ? el('label', { text: f.label }) : null, inp);
      } else if (f.check) {     // 开关（勾选框）
        inp = el('input', { type: 'checkbox' });
        inp.checked = !!f.value;
        wrap = el('div', { class: 'f' }, el('label', { class: 'chk' }, inp, el('span', { text: f.check })));
      } else {
        inp = f.multi ? el('textarea', { class: 'input', placeholder: f.ph || '', rows: 4 }) : el('input', { class: 'input', placeholder: f.ph || '', autocomplete: 'off', autocapitalize: 'off' });
        inp.value = f.value || '';
        wrap = el('div', { class: 'f' }, f.label ? el('label', { text: f.label }) : null, inp);
      }
      inputs[f.key] = inp; wraps[f.key] = wrap;
      body.appendChild(wrap);
    });
    // 字段可以写 only: ['MX'] —— 只在「类型」下拉选到这些值时才显示
    var typeSel = inputs.type && inputs.type.tagName === 'SELECT' ? inputs.type : null;
    function syncOnly() { (fields || []).forEach(function (f) { if (f.only && typeSel) wraps[f.key].style.display = f.only.indexOf(typeSel.value) >= 0 ? '' : 'none'; }); }
    if (typeSel) { typeSel.addEventListener('change', syncOnly); syncOnly(); }
    if (extra) body.appendChild(extra);
    var ok = el('button', { class: 'btn primary', text: okText || '确定' });
    ok.addEventListener('click', function () {
      var vals = {}; for (var k in inputs) vals[k] = inputs[k].type === 'checkbox' ? inputs[k].checked : inputs[k].value;
      run(ok, function () { return onOk(vals); }).then(function (good) { if (good) bg.remove(); });
    });
    body.appendChild(el('div', { class: 'acts' }, el('button', { class: 'btn', text: '取消', onclick: function () { bg.remove(); } }), ok));
    bg.appendChild(body);
    document.body.appendChild(bg);
    var first = body.querySelector('input,textarea'); if (first) setTimeout(function () { first.focus(); }, 120);
    return bg;
  }
  function confirmBox(title, text, onOk) {
    var bg = el('div', { class: 'sheet-bg' });
    var ok = el('button', { class: 'btn danger', text: '确定' });
    ok.addEventListener('click', function () { run(ok, onOk).then(function (good) { if (good) bg.remove(); }); });
    bg.appendChild(el('div', { class: 'sheet' }, el('h2', { text: title }), el('p', { class: 'hint', text: text }),
      el('div', { class: 'acts' }, el('button', { class: 'btn', text: '取消', onclick: function () { bg.remove(); } }), ok)));
    document.body.appendChild(bg);
  }

  // ------------------------------------------------------------ 图标
  var ICON = {
    home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
    nav: '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>',
    tools: '<path d="M14.5 6.5a4 4 0 0 0-5 5L3 18l3 3 6.5-6.5a4 4 0 0 0 5-5l-2.5 2.5-2.5-.5-.5-2.5z"/>',
    dns: '<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9z"/>',
    kit: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2M2 13h20M10 13v2h4v-2"/>',
    own: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>',
    usr: '<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>',
    pin: '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4"/>',
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    net: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3.2 3 3.2 15 0 18M12 3c-3.2 3-3.2 15 0 18"/>',
    bld: '<rect x="5" y="3" width="14" height="18" rx="1.5"/><path d="M9 7h1M14 7h1M9 11h1M14 11h1M9 15h1M14 15h1M10 21v-3h4v3"/>',
    warn: '<path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4.5M12 17.2h.01"/>',
    srv: '<rect x="3" y="3.5" width="18" height="7" rx="2"/><rect x="3" y="13.5" width="18" height="7" rx="2"/><path d="M7 7h.01M7 17h.01M11 7h6M11 17h6"/>',
    pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    qping: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.8 2.6 15.2 0 18M12 3c-2.6 2.8-2.6 15.2 0 18"/>',
    qroute: '<circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="6" r="2.2"/><path d="M8.2 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.8"/>',
    qscan: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6.4-6.4"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
    qpin: '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.4"/>',
    qflow: '<path d="M3.5 17c3 0 3.5-9 6.5-9s3 6 5.5 6 2.5-4 5-4"/><path d="M3.5 20.5h17"/>',
    qhist: '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6"/><path d="M3.5 4.5V8H7"/><path d="M12 7.5V12l3 2"/>',
    qlock: '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
    qsearch: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    qgrp: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    qipq: '<path d="M12 3l7.5 3v5.5c0 4.6-3.1 8-7.5 9.8-4.4-1.8-7.5-5.2-7.5-9.8V6z"/><path d="M8.8 12.2l2.2 2.2 4.3-4.4"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    admin: '<rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><path d="M7 7h.01M7 17h.01"/>'
  };
  // 状态图标统一成圆点：✅🟢 绿，⚠️🟡 黄，❌🔴⛔ 红；其它图标（旗帜、网络等）原样显示
  var ST_MAP = { '✅': 'ok', '✔️': 'ok', '🟢': 'ok', '⚠️': 'warn', '⚠': 'warn', '🟡': 'warn', '❌': 'bad', '🔴': 'bad', '⛔': 'bad' };
  function stIcon(t) {
    var k = ST_MAP[String(t || '').trim()];
    if (k) return el('span', { class: 'ic' }, el('i', { class: 'sdot ' + k }));
    var m = String(t || '').replace(/\uFE0F/g, '').trim();
    var g = /^[\u{1F1E6}-\u{1F1FF}]{2}$/u.test(m) ? 'pin' : m === '🕸' ? 'net' : m === '🏢' ? 'bld' : '';   // 国旗 / 网络 / 机房 统一成黑白线条图标
    return g ? el('span', { class: 'ic mono-ic' }, icon(g)) : el('span', { class: 'ic', text: t });
  }
  function rolePill(owner) { return el('span', { class: 'pill role ' + (owner ? 'own' : 'usr') }, icon(owner ? 'own' : 'usr'), owner ? '所有者' : '授权用户'); }
  // 文字里的彩色圆点/感叹号 → 统一的柔和圆点 / 黑白线条图标
  var RICH_DOT = { '🟢': '', '✅': '', '✔': '', '🟡': 'warn', '🟠': 'warn', '🔴': 'bad', '❌': 'bad', '⛔': 'bad', '🔵': 'blue' };
  function rich(text) {
    var f = document.createDocumentFragment();
    String(text == null ? '' : text).split(/(🟢|✅|✔\uFE0F?|🟡|🟠|🔴|❌|⛔|🔵|⚠\uFE0F?)/u).forEach(function (p) {
      if (!p) return;
      var k = p.replace(/\uFE0F/g, '');
      if (k === '⚠') { f.appendChild(el('span', { class: 'wi' }, icon('warn'))); }
      else if (RICH_DOT.hasOwnProperty(k)) f.appendChild(el('i', { class: 'sdot inl ' + RICH_DOT[k] }));
      else f.appendChild(document.createTextNode(p));
    });
    return f;
  }
  var ICON_X = {
    pxg: '<path d="M12 3.5l9 4.8-9 4.8-9-4.8z"/><path d="M3 12.6l9 4.8 9-4.8"/><path d="M3 16.9l9 4.6 9-4.6"/>',
    more: '<circle cx="5" cy="12" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="19" cy="12" r="1.9"/>',
    go: '<path d="M9.5 6l6 6-6 6"/>'
  };
  function moreBtn(label, fn, extra) {
    var b = el('button', { class: 'ssh-more', type: 'button', 'aria-label': label || '更多', onclick: fn }); b.appendChild(icon('more')); return b;
  }
  // 节点分组：节点上设置了「分组」就用它；没设置就按名字里的国旗 / 国家名自动归到对应国家，认不出的放「其他」
  var CTRY = [['US', '美国', ['美国', '美國']], ['JP', '日本', ['日本', '东京', '東京', '大阪']], ['HK', '香港', ['香港']], ['SG', '新加坡', ['新加坡', '狮城']],
    ['TW', '台湾', ['台湾', '台灣']], ['KR', '韩国', ['韩国', '韓國', '首尔']], ['MO', '澳门', ['澳门', '澳門']], ['GB', '英国', ['英国', '英國', '伦敦']],
    ['DE', '德国', ['德国', '德國', '法兰克福']], ['FR', '法国', ['法国', '法國', '巴黎']], ['NL', '荷兰', ['荷兰', '荷蘭']], ['CA', '加拿大', ['加拿大']],
    ['AU', '澳大利亚', ['澳大利亚', '澳洲']], ['RU', '俄罗斯', ['俄罗斯', '俄羅斯']], ['IN', '印度', ['印度']], ['MY', '马来西亚', ['马来西亚']],
    ['TH', '泰国', ['泰国']], ['VN', '越南', ['越南']], ['PH', '菲律宾', ['菲律宾']], ['ID', '印尼', ['印尼', '印度尼西亚']], ['TR', '土耳其', ['土耳其']],
    ['BR', '巴西', ['巴西']], ['AR', '阿根廷', ['阿根廷']], ['CL', '智利', ['智利']], ['ZA', '南非', ['南非']], ['AE', '阿联酋', ['阿联酋', '迪拜']],
    ['IT', '意大利', ['意大利']], ['ES', '西班牙', ['西班牙']], ['SE', '瑞典', ['瑞典']], ['CH', '瑞士', ['瑞士']], ['PL', '波兰', ['波兰']],
    ['FI', '芬兰', ['芬兰']], ['UA', '乌克兰', ['乌克兰']], ['IE', '爱尔兰', ['爱尔兰']], ['NZ', '新西兰', ['新西兰']], ['MX', '墨西哥', ['墨西哥']],
    ['CN', '中国', ['中国', '国内']]];      // 港澳台排在「中国」前面
  function ctryFlag(code) { return String.fromCodePoint(0x1F1E6 + code.charCodeAt(0) - 65, 0x1F1E6 + code.charCodeAt(1) - 65); }
  function ctryOf(name) {
    name = String(name || '');
    var m = name.match(/[\u{1F1E6}-\u{1F1FF}]{2}/u), code = '', hit = null, i, j;
    if (m) { var cp = Array.from(m[0]); code = String.fromCharCode(cp[0].codePointAt(0) - 0x1F1E6 + 65, cp[1].codePointAt(0) - 0x1F1E6 + 65); }
    for (i = 0; i < CTRY.length && !hit; i++) {
      if (code) { if (CTRY[i][0] === code) hit = CTRY[i]; continue; }
      for (j = 0; j < CTRY[i][2].length; j++) if (name.indexOf(CTRY[i][2][j]) >= 0) { hit = CTRY[i]; break; }
    }
    if (hit) return { code: hit[0], name: hit[1] };
    if (code) { var nm = code; try { nm = new Intl.DisplayNames(['zh-CN'], { type: 'region' }).of(code) || code; } catch (e) {} return { code: code, name: nm }; }
    return { code: '', name: '其他' };
  }
  var PX_NOGRP = '未分组';
  function pxGroupOf(n) { return String(n.group || '').trim(); }
  function pxGrpIcon() { return el('span', { class: 'dz-ic' }, icon('pxg')); }       // 节点分组：层叠图标，不用文件夹
  function pxGroupOpts(d) { return [['', PX_NOGRP]].concat((d.groups || []).map(function (g) { return [g, g]; })); }
  // 分组管理：分组是独立的，可以先建空分组，再把节点放进去
  function pxGroupsMenu(d0) {
    var bg = el('div', { class: 'sheet-bg', onclick: function (e) { if (e.target === bg) { bg.remove(); go(cur.tab, cur.sub, true); } } });
    var box = el('div', { class: 'sheet' });
    bg.appendChild(box); document.body.appendChild(bg);
    function close() { bg.remove(); go(cur.tab, cur.sub, true); }
    function load() { return api('px', null, 0).then(draw).catch(function (e) { toast(e.message, true); }); }
    function act(fn) { run(null, function () { return fn().then(load); }); }
    function draw(d) {
      clear(box);
      box.appendChild(el('h2', { text: '分组管理' }));
      var cnt = {}, n0 = 0;
      d.nodes.forEach(function (n) { if (n.group) cnt[n.group] = (cnt[n.group] || 0) + 1; else n0++; });
      d.groups.forEach(function (g, i) {
        box.appendChild(el('div', { class: 'grp-row' }, pxGrpIcon(), el('b', { text: g }), el('span', { class: 'grp-c', text: (cnt[g] || 0) + ' 个' }), el('span', { class: 'sp' }),
          i ? el('button', { class: 'btn sm ghost', text: '上移', onclick: function () {
            var o = d.groups.slice(); o.splice(i - 1, 0, o.splice(i, 1)[0]);
            act(function () { return api('px_node_edit', { op: 'group_order', groups: o }); });
          } }) : null,
          el('button', { class: 'btn sm', text: '改名', onclick: function () {
            sheet('分组改名', [{ key: 'name', label: '新名称', value: g }], '保存', function (v) { return api('px_node_edit', { op: 'group_rename', old: g, name: v.name }).then(load); });
          } }),
          el('button', { class: 'btn sm danger', text: '删除', onclick: function () {
            confirmBox('删除分组「' + g + '」', '组里的 ' + (cnt[g] || 0) + ' 个节点会回到「' + PX_NOGRP + '」，节点本身不受影响。', function () { return api('px_node_edit', { op: 'group_del', name: g }).then(load); });
          } })));
      });
      if (!d.groups.length) box.appendChild(el('div', { class: 'hint', style: 'margin:8px 0', text: '还没有分组。点下面「新建分组」，可以先建空分组，之后添加节点时选择，或在节点详情里「设置分组」。' }));
      box.appendChild(el('div', { class: 'grp-row' }, pxGrpIcon(), el('b', { text: PX_NOGRP }), el('em', { class: 'grp-def', text: '默认' }), el('span', { class: 'grp-c', text: n0 + ' 个' })));
      box.appendChild(el('button', { class: 'btn primary block', style: 'margin-top:12px', text: '＋ 新建分组', onclick: function () {
        sheet('新建分组', [{ key: 'name', label: '分组名称', ph: '例如：新加坡 / 日本 / 落地机' }], '创建', function (v) { return api('px_node_edit', { op: 'group_add', name: v.name }).then(load); });
      } }));
      box.appendChild(el('div', { class: 'acts' }, el('button', { class: 'btn', text: '完成', onclick: close })));
    }
    draw(d0);
    load();
  }
  function pxGroupList(box, d, rowFn) {
    var nodes = d.nodes, fold = {}, q = '';
    try { fold = JSON.parse(localStorage.getItem('mini_grp_px') || '{}') || {}; } catch (e) {}
    var search = nodes.length >= 8 ? el('input', { class: 'input', placeholder: '🔍 搜索', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', style: 'margin-bottom:10px' }) : null;
    var body = el('div');
    if (search) { box.appendChild(search); search.addEventListener('input', function () { q = search.value.trim().toLowerCase(); draw(); }); }
    box.appendChild(body);
    function draw() {
      clear(body);
      var list = q ? nodes.filter(function (n) { return n.name.toLowerCase().indexOf(q) >= 0 || pxGroupOf(n).toLowerCase().indexOf(q) >= 0; }) : nodes;
      if (q && !list.length) { body.appendChild(el('div', { class: 'hint', style: 'text-align:center;margin:16px 0', text: '没有找到匹配的节点' })); return; }
      if (!d.groups.length) {            // 一个分组都没建：平铺
        var one = el('div', { class: 'card gh-card' }); list.forEach(function (n) { one.appendChild(rowFn(n)); }); body.appendChild(one); return;
      }
      var by = {}, names = d.groups.slice();
      list.forEach(function (n) { var k = pxGroupOf(n) || PX_NOGRP; (by[k] = by[k] || []).push(n); });
      if (by[PX_NOGRP]) names.push(PX_NOGRP);
      names.forEach(function (k) {
        var items = by[k] || [];
        if (q && !items.length) return;
        var closed = !q && !!fold[k], rows = el('div', { class: 'card gh-card' + (closed ? ' grp-closed' : '') });
        var bad = items.some(function (n) { return n.seen && !n.online; });
        items.forEach(function (n) { rows.appendChild(rowFn(n)); });
        if (!items.length) rows.appendChild(el('div', { class: 'hint', style: 'padding:12px 14px', text: '空分组：在节点详情里「设置分组」放进来' }));
        var h = el('button', { class: 'card dz grp-bar' + (closed ? ' closed' : '') + (bad ? ' grp-bad' : ''), type: 'button', onclick: function () {
          if (q) return;
          closed = !closed;
          if (closed) fold[k] = 1; else delete fold[k];
          try { localStorage.setItem('mini_grp_px', JSON.stringify(fold)); } catch (e) {}
          h.classList.toggle('closed', closed); rows.classList.toggle('grp-closed', closed);
        } }, pxGrpIcon(), el('span', { class: 'dz-t' }, el('b', { text: k })),
          (function () {
            var on = items.filter(function (n) { return n.online; }).length;
            return el('small', { class: 'grp-n', style: bad ? 'color:var(--bad);font-weight:600' : '', text: items.length ? on + '/' + items.length : '0' });
          })(), el('span', { class: 'go-ar grp-arr' }, icon('go')));
        body.appendChild(h); body.appendChild(rows);
      });
    }
    draw();
  }
  function goArrow() { return el('span', { class: 'go-ar' }, icon('go')); }
  function icon(name) { var s = el('span'); s.innerHTML = '<svg viewBox="0 0 24 24">' + (ICON[name] || ICON_X[name]) + '</svg>'; return s.firstChild; }

  // ------------------------------------------------------------ 应用骨架
  var app = document.getElementById('app');
  var tabbar = document.getElementById('tabbar');
  var me = null;            // /home 返回的信息
  var cur = { tab: 'home', sub: null };
  var timers = [];
  var epoch = 0;
  function stopTimers() { epoch++; timers.forEach(function (t) { clearInterval(t); clearTimeout(t); }); timers = []; }
  var navData = null;
  var navIcons = {}, iconFallback = false;     // 导航网站图标：域名 → data URI（'' = 没有图标）
  try { navIcons = JSON.parse(localStorage.getItem('mini_nav_icons') || '{}') || {}; if (typeof navIcons !== 'object' || Array.isArray(navIcons)) navIcons = {}; } catch (e) { navIcons = {}; }
  function saveIcons() { try { localStorage.setItem('mini_nav_icons', JSON.stringify(navIcons)); } catch (e) {} }

  var TABS = [['home', '首页'], ['nav', '导航'], ['tools', '工具'], ['docs', '文档'], ['admin', '管理']];   // 「解析」换成了「文档」；域名解析从 Cloudflare 进
  function drawTabs() {
    clear(tabbar);
    var nav = el('nav');
    TABS.forEach(function (t) {
      if (t[0] === 'admin' && me && !me.owner) return;
      var on = cur.tab === t[0] || (t[0] === 'tools' && cur.tab === 'dns');   // 域名解析 / R2 页面属于工具里的 Cloudflare
      nav.appendChild(el('button', { class: on ? 'on' : '', onclick: function () { go(t[0]); } }, icon(t[0]), t[1]));
    });
    tabbar.appendChild(nav);
  }
  // 返回按钮：记住是从哪一页点进来的，返回时回到上一页（点底部导航切换页面时清空）
  var navStack = [];
  function go(tab, sub, viaBack) {
    if (!viaBack) {
      if (!sub) navStack = [];
      else if (cur && !(cur.tab === tab && cur.sub === (sub || null))) {
        // 记下上一页离开时的画面和滚动位置，左边缘右滑返回时垫在下面
        navStack.push({ tab: cur.tab, sub: cur.sub, snap: app.firstChild || null, sy: app.scrollTop || window.scrollY || 0 });
        if (navStack.length > 20) navStack.shift();
        for (var si = 0; si < navStack.length - 3; si++) navStack[si].snap = null;   // 只留最近 3 页的画面，省内存
      }
    }
    stopTimers();
    cur = { tab: tab, sub: sub || null };
    drawTabs();
    hap();
    var page = el('div', { class: 'page' });
    clear(app).appendChild(page);
    window.scrollTo(0, 0); app.scrollTop = 0;
    var fn = { home: pageHome, nav: pageNav, tools: pageTools, dns: pageDns, docs: pageDocs, admin: pageAdmin }[tab];
    fn(page, sub);
    r2BarSync();
    if (tg && tg.BackButton) {
      if (sub) { tg.BackButton.show(); tg.BackButton.offClick(backHandler); tg.BackButton.onClick(backHandler); } else tg.BackButton.hide();
    }
  }
  function backHandler() {
    var p = navStack.pop();
    if (p) return go(p.tab, p.sub, true);
    if (cur.tab === 'dns') return go('tools', 'cf', true);      // 域名解析 / R2 没有上一页时回到工具里的 Cloudflare
    if ((cur.tab === 'admin' || cur.tab === 'tools') && cur.sub && cur.sub.indexOf('server:') === 0) go(cur.tab, 'servers', true); else go(cur.tab, null, true);
  }
  function top(title, back) {
    return el('div', { class: 'top' }, back ? el('button', { class: 'back', text: '‹', onclick: backHandler }) : null, el('h1', { text: title }));
  }
  function skeleton(n) { var f = document.createDocumentFragment(); for (var i = 0; i < (n || 3); i++) f.appendChild(el('div', { class: 'skeleton' })); return f; }
  function failBox(msg, retry) {
    return el('div', { class: 'card empty' }, el('span', { class: 'big wi' }, icon('warn')), el('div', { text: msg }), retry ? el('button', { class: 'btn sm', style: 'margin-top:12px', text: '重试', onclick: retry }) : null);
  }

  // ============================================================ 主屏幕登录
  function devName() {
    var u = navigator.userAgent || '';
    var k = /iPhone/.test(u) ? 'iPhone' : /iPad/.test(u) ? 'iPad' : /Android/.test(u) ? 'Android' : /Mac/.test(u) ? 'Mac' : /Windows/.test(u) ? 'Windows' : '设备';
    return k + ' 主屏幕';
  }
  function pairPage() {
    var tb = document.getElementById('tabbar'); if (tb) tb.style.display = 'none';
    var inp = el('input', { class: 'input pair-in', placeholder: '输入配对码', autocomplete: 'off', autocapitalize: 'characters', spellcheck: 'false', maxlength: '12' });
    var btn = el('button', { class: 'btn primary', text: '配对并登录' });
    var msg = el('div', { class: 'hint', style: 'margin-top:10px;min-height:20px' });
    function submit() {
      if (!inp.value.trim()) return;
      run(btn, function () {
        return api('pair_claim', { code: inp.value, name: devName() }, 0).then(function (r) {
          try { localStorage.setItem('mini_dev', r.token); } catch (e) { throw new Error('浏览器不让保存登录信息，无法使用'); }
          location.reload();
        }).catch(function (e) { msg.textContent = e.message; throw e; });
      });
    }
    btn.addEventListener('click', submit);
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
    clear(app).appendChild(el('div', { class: 'page' }, el('div', { class: 'card pair' },
      el('h3', { class: 'h3i nobar' }, icon('kit'), el('span', { text: '设备配对' })),
      el('div', { class: 'hint', text: '1. 在 Telegram 里打开本控制台，进入「管理」页最下面的「主屏幕登录」，点「生成配对码」（授权用户在「工具」页最下面）。' }),
      el('div', { class: 'hint', text: '2. 把配对码输入下面，配对一次后长期免登录。' }),
      el('div', { style: 'margin-top:14px' }, inp), el('div', { style: 'margin-top:12px' }, btn), msg)));
  }
  function devEntry(tab) {          // 管理页最下面的一行入口，点进去是子页面
    return el('button', { class: 'card dev-entry', onclick: function () { go(tab, 'devices'); } },
      el('span', { class: 'dev-entry-t' }, el('b', { text: '主屏幕登录' }), el('small', { text: '配对码 · 已配对设备 · 离线推送' })), goArrow());
  }
  function pageDevices(page) {
    page.appendChild(top('主屏幕登录', true));
    page.appendChild(devCard());
    page.appendChild(pushCard());
  }
  // 服务器离线推送：只能在主屏幕 App 里开（iPhone 要 iOS 16.4 以上）。服务器离线 5 分钟推一次，恢复再推一次
  function pushCard() {
    var body = el('div'), card = el('div', { class: 'card' }, el('h3', { class: 'h3i nobar' }, icon('srv'), el('span', { text: '服务器离线推送' })), body);
    function hint(t) { clear(body).appendChild(el('div', { class: 'hint', text: t })); }
    if (!root.classList.contains('standalone')) { hint('在主屏幕 App 里打开这一页才能开启推送（iPhone 需要 iOS 16.4 以上）。开启后，服务器离线满 5 分钟会推送提醒，恢复在线再提醒一次。'); return card; }
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) { hint('这台手机的系统版本不支持网页推送，请升级到 iOS 16.4 以上。'); return card; }
    var key = null, sub = null, my = epoch;
    function b64(s) { s = s.replace(/-/g, '+').replace(/_/g, '/'); s += '==='.slice((s.length + 3) % 4); var r = atob(s), a = new Uint8Array(r.length); for (var i = 0; i < r.length; i++) a[i] = r.charCodeAt(i); return a; }
    function draw(on) {
      clear(body);
      body.appendChild(el('div', { class: 'hint', text: on ? '已开启：服务器离线满 5 分钟会推送到这台手机，恢复在线再推送一次。' : '开启后，服务器离线满 5 分钟会推送到这台手机，恢复在线再推送一次。' }));
      if (Notification.permission === 'denied') { body.appendChild(el('div', { class: 'hint', style: 'color:var(--bad)', text: '通知权限被拒绝了：请到 iPhone「设置 → 通知」里找到本 App 打开通知，再回来点开启。' })); }
      var acts = el('div', { class: 'acts', style: 'margin-top:10px;display:flex;gap:8px' });
      if (on) {
        var t = el('button', { class: 'btn sm', text: '发送测试' });
        t.addEventListener('click', function () { run(t, function () { return api('push_test').then(function () { toast('8 秒后发送：现在回到桌面或锁屏等着', false); }); }); });
        var off = el('button', { class: 'btn sm danger', text: '关闭推送' });
        off.addEventListener('click', function () {
          run(off, function () {
            var ep = sub && sub.endpoint;
            return (sub ? sub.unsubscribe() : Promise.resolve()).catch(function () {}).then(function () { return ep ? api('push_unsub', { endpoint: ep }) : null; })
              .then(function () { sub = null; draw(false); toast('已关闭'); });
          });
        });
        acts.appendChild(t); acts.appendChild(off);
      } else {
        var go1 = el('button', { class: 'btn primary sm', text: '开启推送' });
        go1.addEventListener('click', function () {
          // 苹果要求：申请通知权限必须直接在点击里调用
          var perm = Notification.requestPermission();
          run(go1, function () {
            return Promise.resolve(perm).then(function (r) {
              if (r !== 'granted') throw new Error('没有允许通知，开启不了');
              return navigator.serviceWorker.ready;
            }).then(function (reg) {
              return (key ? Promise.resolve(key) : api('push_key').then(function (k) { return key = k.key; })).then(function (k) {
                return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(k) });
              });
            }).then(function (s1) {
              sub = s1;
              return api('push_sub', s1.toJSON());
            }).then(function () { draw(true); toast('推送已开启'); });
          });
        });
        acts.appendChild(go1);
      }
      body.appendChild(acts);
    }
    hint('读取中…');
    setTimeout(function () { if (epoch === my && body.textContent === '读取中…') hint('推送服务没有启动：请确认网站根目录有 sw.js，然后关掉 App 重新打开。'); }, 6000);
    api('push_key').then(function (k) { key = k.key; }).catch(function () {});
    var swv = el('div', { class: 'hint', style: 'margin-top:8px;font-size:11px' });
    navigator.serviceWorker.ready.then(function (reg) {
      // 排查用：显示手机上正在用的 sw.js 版本（没有 ?v= 的是旧版，收不到推送）
      var u = (reg.active && reg.active.scriptURL) || '', m = /[?&]v=(\d+)/.exec(u);
      swv.textContent = m ? '推送服务版本：' + timeText(+m[1]) : '推送服务版本：旧版（请划掉 App 重新打开两次）';
      card.appendChild(swv);
      return reg.pushManager.getSubscription();
    }).then(function (s1) {
      sub = s1;
      if (!s1) return false;
      return api('push_state', { endpoint: s1.endpoint }).then(function (r) { return r.on; });
    }).then(function (on) { if (epoch === my) draw(!!on); }, function (e) { if (epoch === my) hint(e.message || '读取失败'); });
    return card;
  }
  function devCard() {
    var inTg = !!initData;
    var list = el('div', { class: 'dev-list' }, el('div', { class: 'hint', text: '读取中…' }));
    var gen = inTg ? el('button', { class: 'btn primary sm', text: '生成配对码' }) : null;
    var out = el('div', { class: 'pair-out' });
    function draw(d) {
      clear(list);
      if (!d.list.length) list.appendChild(el('div', { class: 'hint', text: '还没有设备。' }));
      d.list.forEach(function (x) {
        list.appendChild(el('div', { class: 'kv dev' },
          el('span', { class: 'dev-n' }, el('b', { text: x.name + (x.current ? '（本机）' : '') }), el('small', { text: '最近 ' + timeText(x.last) + (x.ip ? ' · ' + x.ip : '') })),
          el('button', { class: 'btn sm danger', text: x.current ? '退出' : '撤销', onclick: function () {
            confirmBox(x.current ? '退出本机' : '撤销设备', x.current ? '退出后需要重新配对才能再用。' : '撤销后这台设备立刻不能再登录。', function () {
              return api('device_revoke', { id: x.id }).then(function () { if (x.current) { devLost(); } else { toast('已撤销'); load(); } });
            });
          } })));
      });
    }
    function load() { api('devices').then(draw, function (e) { clear(list).appendChild(el('div', { class: 'hint', text: e.message })); }); }
    if (gen) gen.addEventListener('click', function () {
      run(gen, function () {
        return api('pair_new').then(function (r) {
          var left = r.ttl, num = el('div', { class: 'pair-code', text: r.code.slice(0, 4) + ' ' + r.code.slice(4), onclick: function () { copy(r.code); } });
          var tip = el('div', { class: 'hint' });
          clear(out).appendChild(el('div', null, num, tip));
          var t = setInterval(function () {
            left--; if (left <= 0 || !document.body.contains(num)) { clearInterval(t); if (left <= 0) clear(out); return; }
            tip.textContent = '在主屏幕 App 里输入，' + Math.floor(left / 60) + ':' + ('0' + (left % 60)).slice(-2) + ' 内有效，只能用一次';
          }, 1000);
          tip.textContent = '在主屏幕 App 里输入，5:00 内有效，只能用一次';
          timers.push(t);
        });
      });
    });
    load();
    return el('div', { class: 'card' },
      el('div', { class: 'hint', text: inTg ? '把控制台添加到手机主屏幕后，输入配对码即可免打开 Telegram，有效期 365 天，每天使用自动续期。' : '本机已配对。下面是你名下已配对的设备，可随时撤销。' }),
      gen, out, list);
  }

  // ============================================================ 首页
  function pageHome(page, sub) {
    if (sub === 'lat') return pageLat(page);
    if (sub === 'spd') return pageSpd(page);
    if (sub === 'gh' || sub === 'cf' || (sub && /^git/.test(sub))) {
      if (!me || !me.owner) { page.appendChild(lockBox('只有所有者可以使用')); return; }
      if (sub === 'gh') return pageGhHub(page);
      if (sub === 'cf') return pageCfHub(page);
      return gitRoute(page, sub);
    }
    page.appendChild(skeleton(3));
    api('home').then(function (d) {
      me = d; drawTabs();
      clear(page);
      page.classList.add('pg-home');
      if (d.owner) page.classList.add('pg-home6');   // 6 个快捷工具：只让快捷工具卡片撑满，其他卡片保持原来大小
      page.appendChild(el('div', { class: 'card hero' },
        el('div', { class: 'hero-cap', text: 'NETWORK CONSOLE' }),
        el('div', { class: 'hero-t', text: 'Keep Online' }),
        el('div', { class: 'meta' },
          el('span', { class: 'pill ok' }, el('i', { class: 'dot' }), '服务在线'),
          rolePill(!!d.owner),
          null)));
      if (d.admin) page.appendChild(spdCard(true));
      page.appendChild(el('div', { class: 'card qk' }, el('h3', { class: 'h3i nobar' }, icon('kit'), el('span', { text: '快捷工具' })),
        d.owner ? el('div', { class: 'grid g2 qk6' }, quick('qping', '全球 Ping', 'ping'), quick('qroute', '路由追踪', 'route'), quick('qscan', '端口扫描', 'scan'),
          quick('qipq', 'IP 质量', 'ipq'), quick('qgit', 'GitHub', 'home:gh'), quick('dns', 'Cloudflare', 'home:cf'))
          : el('div', { class: 'grid g2' }, quick('qping', '全球 Ping', 'ping'), quick('qroute', '路由追踪', 'route'), quick('qscan', '端口扫描', 'scan'), quick('qipq', 'IP 质量', 'ipq'))));
      if (d.admin) page.appendChild(overviewCard(d));
      var cu = function (c, t) { return el('div', { class: c, text: t }); };
      var cH = cu('cu', '--'), cM = cu('cu', '--'), cS = cu('cu s', '--');
      var tgl = el('button', { class: 'tg', 'aria-label': '切换黑白主题' });
      var drawTg = function () { clear(tgl); tgl.appendChild(icon(scheme === 'light' ? 'moon' : 'sun')); };
      drawTg();
      tgl.addEventListener('click', function () { setScheme(scheme === 'light' ? 'dark' : 'light'); drawTg(); hap(); });
      var clock = el('div', { class: 'clock' }, el('div', { class: 'clock-r' }, cH, el('div', { class: 'cc', text: ':' }), cM, el('div', { class: 'cc', text: ':' }), cS, tgl));
      page.appendChild(clock);
      var paint = function (t) {
        var p = function (n) { return (n < 10 ? '0' : '') + n; };
        cH.textContent = p(t.getUTCHours()); cM.textContent = p(t.getUTCMinutes()); cS.textContent = p(t.getUTCSeconds());
      };
      var mt = /^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)$/.exec(d.time || '');
      if (mt) {            // 按服务器给的时间每秒走字（不依赖手机自己的时区）
        var base = Date.UTC(+mt[1], mt[2] - 1, +mt[3], +mt[4], +mt[5], +mt[6]), t0 = Date.now(), myEpoch = epoch;
        paint(new Date(base));
        timers.push(setInterval(function () {
          if (epoch !== myEpoch) return;
          var t = new Date(base + Date.now() - t0);
          paint(t);
        }, 1000));
      }
    }).catch(function (e) { clear(page); page.appendChild(e.message.indexOf('权限') >= 0 ? lockBox(e.message) : failBox(e.message, function () { go('home'); })); });
  }
  // 顶部：美元对人民币汇率
  function fxCard() {
    var num = el('b', { class: 'fx-num', text: '——' }), chg = el('span', { class: 'fx-chg' });
    var node = el('div', { class: 'card fx' },
      el('div', { class: 'fx-head' },
        el('div', { class: 'fx-pair' }, el('em', { text: 'USD-CNY' })),
        el('span', { class: 'pill ok' }, el('i', { class: 'dot' }), '实时')),
      el('div', { class: 'fx-main' }, num, chg));
    var my = epoch;
    function load() {
      api('fx').then(function (d) {
        if (epoch !== my) return;
        num.textContent = d.rate.toFixed(4);
        chg.className = 'fx-chg';
        if (d.prev && d.prev !== d.rate) { var up = d.rate > d.prev; chg.className = 'fx-chg ' + (up ? 'up' : 'dn'); chg.textContent = (up ? '▲ ' : '▼ ') + Math.abs(d.rate - d.prev).toFixed(4); } else chg.textContent = '';
      }).catch(function () { if (epoch === my) { chg.className = 'fx-chg'; chg.textContent = '暂时不可用'; } });
    }
    load(); timers.push(setInterval(load, 60000));
    return node;
  }
  // ============================================================ 延迟监控（首页卡片 + 详情页）
  // 数据来自机器人后台：每隔一段时间从国内电信 / 联通 / 移动各一个探针 ping 每台服务器
  var LAT_C = ['#7aa7ff', '#b49cff', '#46d39a'], LAT_N = ['电信', '联通', '移动'], latGid = 0, latSel = null, latRange = '24h';
  function latPath(vals, w, h, lo, hi) {     // 返回 [折线, 填充]；null 处断开
    var segs = [], cur = [];
    vals.forEach(function (v, i) {
      if (v == null) { if (cur.length) segs.push(cur); cur = []; return; }
      cur.push([i * w / Math.max(1, vals.length - 1), h - (v - lo) / Math.max(1e-6, hi - lo) * h]);
    });
    if (cur.length) segs.push(cur);
    function pts(sg) { return sg.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' L'); }
    return [segs.map(function (sg) { return 'M' + pts(sg); }).join(' '),
            segs.map(function (sg) { return 'M' + sg[0][0].toFixed(1) + ',' + h + ' L' + pts(sg) + ' L' + sg[sg.length - 1][0].toFixed(1) + ',' + h + 'Z'; }).join(' ')];
  }
  function latSpark(vals, color, w, h) {
    var nums = vals.filter(function (v) { return v != null; }), box = el('span', { class: 'csp' });
    if (nums.length < 2) {
      box.innerHTML = '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none"><line x1="0" x2="' + w + '" y1="' + (h - 1) + '" y2="' + (h - 1) + '" stroke="var(--line2)" stroke-dasharray="3 3"/></svg>';
      return box;
    }
    var lo = Math.min.apply(0, nums) * .85, hi = Math.max.apply(0, nums) * 1.08, p = latPath(vals, w, h, lo, hi), id = 'lsg' + (++latGid);
    box.innerHTML = '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none"><defs><linearGradient id="' + id + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="' + color + '" stop-opacity=".28"/><stop offset="1" stop-color="' + color + '" stop-opacity="0"/></linearGradient></defs>' +
      '<path d="' + p[1] + '" fill="url(#' + id + ')"/><path d="' + p[0] + '" fill="none" stroke="' + color + '" stroke-width="1.4" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>';
    return box;
  }
  function latGauge(pct) {
    var r = 31, c = 2 * Math.PI * r, arc = c * .75, has = pct != null, on = arc * Math.max(0, Math.min(100, pct || 0)) / 100, id = 'lgg' + (++latGid);
    var col = !has || pct >= 95 ? ['#2fb380', '#7af0bf', 'rgba(70,211,154,.45)'] : pct >= 85 ? ['#d99a1e', '#ffd36b', 'rgba(255,200,87,.45)'] : ['#d94a5e', '#ff8a9b', 'rgba(255,107,129,.45)'];
    var w = el('div', { class: 'cg' });
    w.innerHTML = '<svg class="cgs" viewBox="0 0 72 72"><defs><linearGradient id="' + id + '" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="' + col[0] + '"/><stop offset="1" stop-color="' + col[1] + '"/></linearGradient></defs>' +
      '<g transform="rotate(135 36 36)"><circle cx="36" cy="36" r="' + r + '" fill="none" stroke="rgba(127,127,127,.18)" stroke-width="5" stroke-linecap="round" stroke-dasharray="' + arc.toFixed(1) + ' ' + c.toFixed(1) + '"/>' +
      (has ? '<circle cx="36" cy="36" r="' + r + '" fill="none" stroke="url(#' + id + ')" stroke-width="5" stroke-linecap="round" stroke-dasharray="' + on.toFixed(1) + ' ' + c.toFixed(1) + '" style="filter:drop-shadow(0 0 3px ' + col[2] + ')"/>' : '') + '</g></svg>';
    w.appendChild(el('div', { class: 'cgt' }, el('b', null, has ? String(pct) : '--', has ? el('em', { text: '%' }) : null), el('span', { text: '可用率' })));
    return w;
  }
  function latCard(link) {
    var gaugeBox = el('div'), rows = el('div', { class: 'crs' });
    var card = el(link ? 'button' : 'div', { class: 'card cl' + (link ? ' tap' : ''), type: link ? 'button' : null, onclick: link ? function () { go('home', 'lat'); } : null },
      el('div', { class: 'cbody' }, gaugeBox, rows, link ? el('span', { class: 'chev', text: '›' }) : null));
    function draw(d) {
      clear(gaugeBox).appendChild(latGauge(d.avail));
      clear(rows);
      d.isp.forEach(function (x, i) {
        rows.appendChild(el('div', { class: 'cr' }, el('i', { class: 'ldot', style: '--c:' + LAT_C[i] }), el('span', { class: 'cn', text: x.name }),
          latSpark(x.series || [], LAT_C[i], 110, 15), el('b', null, x.avg == null ? '--' : String(Math.round(x.avg)), el('em', { text: 'ms' }))));
      });
    }
    draw({ avail: null, isp: LAT_N.map(function (n) { return { name: n, avg: null, series: [] }; }) });
    var my = epoch;
    card.reload = function () {
      return api('lat').then(function (d) { if (epoch !== my) return; draw(d); if (card.onData) card.onData(d); })
        .catch(function (e) { if (epoch === my && card.onError) card.onError(e); });
    };
    setTimeout(card.reload, 0);
    timers.push(setInterval(card.reload, 60000));
    return card;
  }
  // ------------------------------------------------------------ 首页：网络测速（手机 ↔ 本站服务器）
  var SPD_KEY = 'mini_speed';
  function spdFmt(v) { return v == null ? '--' : v >= 1000 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2); }
  function spdDial(mbps, phase, mono) {     // 小码表：0~1000 Mbps 对数刻度；mono = 不在测速时用灰色
    var r = 19, c = 2 * Math.PI * r, arc = c * .75, f = mbps ? Math.min(1, Math.log10(1 + mbps) / 3) : 0;
    var col = phase === 'u' ? '#6ea8ff' : (mono && !phase) ? 'var(--faint)' : 'var(--ok)', ang = 135 + 270 * f;
    var w = el('div', { class: 'spd-dial' + (phase ? ' run' : '') });
    w.innerHTML = '<svg viewBox="0 0 48 48"><g transform="rotate(135 24 24)">' +
      '<circle cx="24" cy="24" r="' + r + '" fill="none" stroke="var(--line2)" stroke-width="3.5" stroke-linecap="round" stroke-dasharray="' + arc.toFixed(1) + ' ' + c.toFixed(1) + '"/>' +
      (f > 0 ? '<circle cx="24" cy="24" r="' + r + '" fill="none" stroke="' + col + '" stroke-width="3.5" stroke-linecap="round" stroke-dasharray="' + (arc * f).toFixed(1) + ' ' + c.toFixed(1) + '"/>' : '') + '</g>' +
      '<line x1="24" y1="24" x2="' + (24 + 13 * Math.cos(ang * Math.PI / 180)).toFixed(1) + '" y2="' + (24 + 13 * Math.sin(ang * Math.PI / 180)).toFixed(1) + '" stroke="var(--text)" stroke-width="2.2" stroke-linecap="round"/>' +
      '<circle cx="24" cy="24" r="3" fill="var(--text)"/></svg>';
    return w;
  }
  function spdCard(link) {
    var last = null; try { last = JSON.parse(localStorage.getItem(SPD_KEY) || 'null'); } catch (e) {}
    var dialBox = el('div'), dn = el('b'), up = el('b');
    var btnTxt = el('span', { text: '测速' });
    var btn = el('button', { class: 'spd-go', type: 'button' }, el('i', { class: 'spd-play' }), btnTxt);
    function fxm(arrow, cls, num) { return el('div', { class: 'spd-m ' + cls }, el('div', { class: 'spd-v' }, el('span', { class: 'spd-ar', text: arrow }), num)); }
    // 和以前汇率卡一样的两行排版：上面 仪表 + SPEEDTEST 标签 + 单位，下面 下载 / 上传 + 测速按钮
    var card = el('div', { class: 'card spd fxs' + (link ? ' tap' : ''), onclick: link ? function () { go('home', 'spd'); } : null },
      el('div', { class: 'fx-head' },
        el('div', { class: 'spd-hl' }, dialBox, el('em', { class: 'spd-tag', text: 'SPEEDTEST' })),
        el('span', { class: 'spd-unit', text: 'Mbps' })),
      el('div', { class: 'spd-main' }, el('div', { class: 'spd-nums' }, fxm('↓', 'd', dn), el('i', { class: 'spd-sep' }), fxm('↑', 'u', up)), el('span', { class: 'sp' }), btn));
    function paint(d, u, phase, live) {
      dn.textContent = spdFmt(d); up.textContent = spdFmt(u);
      dn.parentNode.parentNode.classList.toggle('on', phase === 'd'); up.parentNode.parentNode.classList.toggle('on', phase === 'u');
      clear(dialBox).appendChild(spdDial(live != null ? live : (u != null && phase !== 'd' ? Math.max(d || 0, u) : d), phase, true));   // 平时灰色，测下载绿、测上传蓝
    }
    paint(last && last.d, last && last.u, null);
    // 测速是全局任务：切到别的页面再回来，会接着显示还在跑的那次，不会再开第二次（两次同时跑会互相抢带宽，结果不准）
    function attach(sr) {
      btn.setAttribute('disabled', ''); card.classList.add('busy');
      sr.view = function (x) { if (card.isConnected) paint(x.d, x.phase === 'u' ? x.u : null, x.phase, x.live); };
      sr.end = function (x) {
        if (!card.isConnected) return;
        if (x.err) toast(x.err.message || '测速失败', true);
        try { last = JSON.parse(localStorage.getItem(SPD_KEY) || 'null') || last; } catch (e) {}
        btn.removeAttribute('disabled'); card.classList.remove('busy');
        paint(last && last.d, last && last.u, null);
      };
      sr.view(sr);
    }
    btn.addEventListener('click', function (ev) {
      ev.stopPropagation();
      attach(SPD || spdStart());
    });
    if (SPD) attach(SPD);
    return card;
  }
  var SPD = null;     // 正在跑的本机测速（全局，不跟着页面走）
  function spdStart() {
    var sr = SPD = { d: null, u: null, phase: 'd', live: 0, view: null, end: null, err: null };
    function show() { if (sr.view) sr.view(sr); }
    speedTicket().then(function (t) {
      show();
      return spdDown(t, 8000, function (v) { sr.d = v; sr.live = v; show(); }).then(function (v) {
        sr.d = v; sr.phase = 'u'; sr.live = 0; show();
        return spdUp(t, 8000, function (x) { sr.u = x; sr.live = x; show(); });
      }).then(function (v) {
        sr.u = v;
        try { localStorage.setItem(SPD_KEY, JSON.stringify({ d: sr.d, u: v, ts: Date.now() })); } catch (e) {}
      });
    }).catch(function (e) { sr.err = e; }).then(function () { SPD = null; if (sr.end) sr.end(sr); });
    return sr;
  }
  // 网络测速子页：上面是本机，下面是各台服务器（由探针向 Cloudflare 测速节点测）
  function pageSpd(page) {
    page.appendChild(top('网络测速', true));
    page.appendChild(spdCard(false));
    var list = el('div', { class: 'card spl' }, skeleton(2));
    page.appendChild(list);
    var my = epoch, timer = null, data = null;
    function vals(d, u) {
      return el('div', { class: 'spr-v' },
        el('div', null, el('span', { class: 'spd-ar d', text: '↓' }), el('b', { text: spdFmt(d) })),
        el('div', null, el('span', { class: 'spd-ar u', text: '↑' }), el('b', { text: spdFmt(u) })));
    }
    function row(s) {
      var r = s.run, running = r && (r.state === 'wait' || r.state === 'run'), last = s.last || {};
      var old = s.online && s.ver < data.min_ver, meta;
      if (running) meta = r.state === 'wait' ? '等待探针…' : r.phase === 'u' ? '测上传…' : (r.phase === 'd' || r.d != null) ? '测下载…' : '连接测速点…';
      else if (r && r.state === 'err') meta = r.err || '测速失败';
      else if (!s.online) meta = '离线';
      else if (old) meta = '探针需更新';
      else meta = last.ts ? [String(last.node || '').replace(/Speedtest /g, ''), last.ping != null ? Math.round(last.ping) + 'ms' : '', timeText(last.ts)].filter(Boolean).join(' · ') : '未测速';
      var btn = el('button', { class: 'spd-go', type: 'button' }, old ? null : el('i', { class: 'spd-play' }), el('span', { text: old ? '更新' : '测速' }));
      if (running || !s.online) btn.setAttribute('disabled', '');
      if (!running && !s.online) btn.classList.add('off');
      btn.addEventListener('click', function () {
        if (old) { api('server_edit', { op: 'cmd', id: s.id }).then(function (x) { showCmd(x.cmd, '在这台服务器上执行，更新探针'); }).catch(function (e) { toast(e.message, true); }); return; }
        btn.setAttribute('disabled', '');
        api('speed_edit', { op: 'run', id: s.id }).then(load, function (e) { toast(e.message, true); btn.removeAttribute('disabled'); });
      });
      return el('div', { class: 'spr' + (running ? ' run' : '') },
        // 和顶部测速卡一样的小码表：指针指向这台的下载速度，测速时指针跟着实时数字跑；离线变淡
        el('span', { class: 'spr-dial' + (s.online ? '' : ' off') }, spdDial(running ? (r.phase === 'u' ? r.u : r.d) : last.d, running ? (r.phase === 'u' ? 'u' : 'd') : null, true)),   // 平时灰色，测下载变绿、测上传变蓝
        el('div', { class: 'spr-n' }, el('b', { text: s.name }), el('span', { class: (r && r.state === 'err') ? 'bad' : '', text: meta })),
        running ? vals(r.d, r.u) : vals(last.d, last.u), btn);
    }
    function draw() {
      clear(list).appendChild(el('h3', null, el('span', { text: '服务器带宽' }), el('span', { class: 'sp' }), el('em', { class: 'spl-u', text: 'Mbps' })));
      if (!data.items.length) { list.appendChild(el('div', { class: 'hint', text: '还没有服务器。先在「服务器监控」里添加并安装探针。' })); return; }
      data.items.forEach(function (s) { list.appendChild(row(s)); });
    }
    function load() {
      return api('speed').then(function (d) {
        if (epoch !== my) return;
        data = d; draw();
        var busy = d.items.some(function (s) { return s.run && (s.run.state === 'wait' || s.run.state === 'run'); });
        clearTimeout(timer); timer = setTimeout(load, busy ? 1500 : 15000); timers.push(timer);
      }).catch(function (e) { if (epoch === my && !data) { clear(list).appendChild(el('div', { class: 'hint', text: e.message })); } });
    }
    load();
  }
  function speedTicket() {
    return fetch('speed.php?op=t', { method: 'POST', headers: authHeaders(), cache: 'no-store' })
      .then(function (r) { return r.json(); }, function () { throw new Error('网络不稳定，请稍后再试'); })
      .then(function (j) { if (!j || !j.ok) throw new Error((j && j.error) || '测速接口不可用'); return j.t; }, function (e) { throw e.message ? e : new Error('测速接口不可用：请确认已上传 speed.php'); });
  }
  // 多路并发、跑满 ms 毫秒；前 1.5 秒算预热不计入，结果单位 Mbps
  function spdMeter(ms, onRate, finish) {
    var bytes = 0, base = 0, t0 = performance.now(), tw = 0, done = false, warm = Math.min(1500, ms / 4);
    function rate() { var dt = (performance.now() - (tw || t0)) / 1000; return dt > .2 ? (bytes - base) * 8 / dt / 1e6 : null; }
    var iv = setInterval(function () {
      var el2 = performance.now() - t0;
      if (!tw && el2 >= warm) { tw = performance.now(); base = bytes; }
      var r = rate(); if (r != null && tw) onRate(r);
      if (el2 >= ms) end();
    }, 200);
    function end() { if (done) return; done = true; clearInterval(iv); finish(tw ? rate() : (bytes * 8 / ((performance.now() - t0) / 1000) / 1e6)); }
    return { add: function (n) { bytes += n; }, end: end, done: function () { return done; } };
  }
  function spdDown(t, ms, onRate) {
    return new Promise(function (resolve, reject) {
      var ctrls = [], fails = 0;
      var m = spdMeter(ms, onRate, function (v) { ctrls.forEach(function (c) { try { c.abort(); } catch (e) {} }); if (v == null || !(v > 0)) reject(new Error('下载测速失败')); else resolve(v); });
      function one() {
        if (m.done()) return;
        var c = new AbortController(); ctrls.push(c);
        fetch('speed.php?op=d&n=' + (25 << 20) + '&t=' + encodeURIComponent(t) + '&r=' + Math.random(), { cache: 'no-store', signal: c.signal }).then(function (r) {
          if (!r.ok || !r.body) throw new Error('bad');
          var rd = r.body.getReader();
          function pump() { return rd.read().then(function (x) { if (x.done) return one(); m.add(x.value.length); if (m.done()) { try { rd.cancel(); } catch (e) {} return; } return pump(); }); }
          return pump();
        }).catch(function () { if (!m.done() && ++fails > 6) m.end(); else if (!m.done()) setTimeout(one, 300); });
      }
      for (var i = 0; i < 4; i++) one();
    });
  }
  function spdUp(t, ms, onRate) {
    var blob = new Uint8Array(900 * 1024);
    for (var o = 0; o < blob.length; o += 65536) crypto.getRandomValues(blob.subarray(o, Math.min(blob.length, o + 65536)));
    return new Promise(function (resolve, reject) {
      var xhrs = [], fails = 0;
      var m = spdMeter(ms, onRate, function (v) { xhrs.forEach(function (x) { try { x.abort(); } catch (e) {} }); if (v == null || !(v > 0)) reject(new Error('上传测速失败')); else resolve(v); });
      function one() {
        if (m.done()) return;
        var x = new XMLHttpRequest(), sent = 0; xhrs.push(x);
        x.open('POST', 'speed.php?op=u&t=' + encodeURIComponent(t) + '&r=' + Math.random());
        x.setRequestHeader('Content-Type', 'application/octet-stream');
        x.upload.onprogress = function (e) { m.add(e.loaded - sent); sent = e.loaded; };
        x.onload = function () { if (x.status === 200) { m.add(blob.length - sent); one(); } else if (++fails > 6) m.end(); else setTimeout(one, 300); };
        x.onerror = function () { if (!m.done() && ++fails > 6) m.end(); else if (!m.done()) setTimeout(one, 300); };
        x.send(blob);
      }
      for (var i = 0; i < 4; i++) one();
    });
  }
  function latFmt(ts, long) {
    var d = new Date(ts * 1000), p = function (x) { return (x < 10 ? '0' : '') + x; };
    return long ? (d.getMonth() + 1) + '-' + p(d.getDate()) : p(d.getHours()) + ':' + p(d.getMinutes());
  }
  function pageLat(page) {
    page.appendChild(top('延迟监控', true));
    var sum = latCard(false), chartCard = el('div', { class: 'card' }), listCard = el('div', { class: 'card' }), data = null;
    page.appendChild(sum); page.appendChild(chartCard); page.appendChild(listCard);
    chartCard.appendChild(skeleton(1));
    var runBtn = el('button', { class: 'btn sm', text: '立即检测' });
    runBtn.addEventListener('click', function () {
      run(runBtn, function () {
        return api('lat_edit', { op: 'run' }).then(function (r) {
          toast(r.started ? '已开始检测，大约 1 分钟后出结果' : (r.msg || '正在检测中'));
          var my = epoch;
          timers.push(setTimeout(function () { if (epoch === my) sum.reload(); }, 50000));
        });
      });
    });
    function cell(v) {
      if (!v) return el('span', { class: 'dv', text: '—' });
      if (v.ms == null) return el('span', { class: 'dv bad', text: '超时' });
      return el('span', { class: 'dv' + (v.ms >= 150 || v.loss >= 20 ? ' warn' : ''), text: String(Math.round(v.ms)) });
    }
    function level(sv) {
      if (!sv.v) return 'off';
      if (sv.v.some(function (x) { return x && x.ms == null; })) return 'bad';
      return sv.v.some(function (x) { return x && (x.ms >= 150 || x.loss >= 20); }) ? 'warn' : '';
    }
    function drawList(d) {
      clear(listCard).appendChild(el('h3', { class: 'h3i nobar' }, icon('srv'), el('span', { text: '全部服务器' }), el('span', { class: 'sp' }), runBtn));
      if (!d.servers.length) { listCard.appendChild(el('div', { class: 'hint', text: '还没有服务器。先在「服务器监控」里添加并安装探针。' })); return; }
      listCard.appendChild(el('div', { class: 'drow h' }, el('i'), el('span', { class: 'dn', text: '服务器' }), LAT_N.map(function (n) { return el('span', { class: 'dv', text: n }); })));
      d.servers.forEach(function (sv) {
        listCard.appendChild(el('button', { class: 'drow' + (sv.id === latSel ? ' on' : ''), type: 'button', onclick: function () { latSel = sv.id; drawList(d); loadChart(); } },
          el('i', { class: 'sdot ' + level(sv) }), el('span', { class: 'dn', text: sv.name }), cell(sv.v && sv.v[0]), cell(sv.v && sv.v[1]), cell(sv.v && sv.v[2])));
      });
      listCard.appendChild(el('div', { class: 'hint', style: 'margin-top:10px', text: (d.last ? '上次检测 ' + timeText(d.last) + ' · ' : '还没有检测数据 · ') + '每 ' + Math.round(d.every / 60) + ' 分钟从国内电信、联通、移动各测一次' }));
    }
    function chip(key, label) {
      return el('button', { class: 'chip' + (latRange === key ? ' on' : ''), type: 'button', onclick: function () { latRange = key; loadChart(); } }, label);
    }
    function drawChart(d) {
      clear(chartCard);
      chartCard.appendChild(el('h3', { class: 'h3i nobar' }, icon('srv'), el('span', { text: d.name }), el('span', { class: 'sp' }), chip('24h', '24 小时'), chip('7d', '7 天')));
      var last = d.pts.length ? d.pts[d.pts.length - 1] : null;
      chartCard.appendChild(el('div', { class: 'dlg' }, LAT_N.map(function (n, i) {
        var v = last && last[i + 1];
        return el('span', null, el('i', { style: 'background:' + LAT_C[i] }), n + ' ' + (!v ? '—' : v[0] == null ? '超时' : Math.round(v[0])));
      }), el('span', { class: 'u', text: 'ms' })));
      if (d.pts.length < 2) { chartCard.appendChild(el('div', { class: 'hint', text: '数据还不够画曲线，检测几轮之后就有了。' })); return; }
      var W = 340, H = 120, series = [0, 1, 2].map(function (i) { return d.pts.map(function (p) { return p[i + 1] && p[i + 1][0] != null ? p[i + 1][0] : null; }); });
      var all = [].concat(series[0], series[1], series[2]).filter(function (v) { return v != null; });
      var lo = all.length ? Math.min.apply(0, all) * .8 : 0, hi = all.length ? Math.max.apply(0, all) * 1.1 : 1;
      var html = '<svg class="dch" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">';
      [30, 60, 90].forEach(function (y) { html += '<line x1="0" x2="' + W + '" y1="' + y + '" y2="' + y + '" stroke="var(--line)" stroke-dasharray="3 4"/>'; });
      series.forEach(function (sr, i) { html += '<path d="' + latPath(sr, W, H, lo, hi)[0] + '" fill="none" stroke="' + LAT_C[i] + '" stroke-width="1.5" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'; });
      var box = el('div'); box.innerHTML = html + '</svg>'; chartCard.appendChild(box.firstChild);
      var long = latRange === '7d', n = d.pts.length, ax = el('div', { class: 'dax' });
      [0, .25, .5, .75, 1].forEach(function (f, k) { ax.appendChild(el('span', { text: k === 4 ? '现在' : latFmt(d.pts[Math.round(f * (n - 1))][0], long) })); });
      chartCard.appendChild(ax);
      var loss = d.pts.map(function (p) { var m = 0; for (var i = 1; i <= 3; i++) if (p[i]) m = Math.max(m, p[i][1] || 0); return m; });
      var cnt = loss.filter(function (l) { return l > 0; }).length, bw = W / n, bars = '';
      loss.forEach(function (l, i) { var hgt = l ? Math.max(4, l * .26) : 2; bars += '<rect x="' + (i * bw + bw * .15).toFixed(1) + '" y="' + (28 - hgt).toFixed(1) + '" width="' + Math.max(1.5, bw * .7).toFixed(1) + '" height="' + hgt.toFixed(1) + '" rx="1" fill="' + (l ? 'var(--bad)' : 'var(--line2)') + '"/>'; });
      chartCard.appendChild(el('div', { class: 'dls' }, el('span', { text: '丢包' }), el('small', { text: cnt ? (long ? '7 天' : '24 小时') + '内 ' + cnt + ' 次' : '没有丢包' })));
      var b2 = el('div'); b2.innerHTML = '<svg class="dlb" viewBox="0 0 ' + W + ' 28" preserveAspectRatio="none">' + bars + '</svg>'; chartCard.appendChild(b2.firstChild);
    }
    function loadChart() {
      if (!latSel) { clear(chartCard).appendChild(el('div', { class: 'hint', text: '还没有服务器数据。' })); return; }
      var my = epoch;
      api('lat_server', { id: latSel, range: latRange }).then(function (d) { if (epoch === my) drawChart(d); })
        .catch(function (e) { if (epoch === my) clear(chartCard).appendChild(el('div', { class: 'hint', text: e.message })); });
    }
    sum.onData = function (d) {
      data = d;
      if (!latSel || !d.servers.some(function (s) { return s.id === latSel; })) latSel = d.servers.length ? d.servers[0].id : null;
      drawList(d); loadChart();
    };
    sum.onError = function (e) {     // 出错时只显示一个提示框（不要卡片套卡片），下面的列表先隐藏
      if (data) return;
      var box = failBox(e.message, function () { go('home', 'lat'); });
      chartCard.replaceWith(box); chartCard = box; listCard.style.display = 'none';
    };
  }
  function lockBox(msg) { return el('div', { class: 'card lock' }, el('div', { class: 'big wi' }, icon('qlock')), el('h2', { text: '无法使用' }), el('div', { class: 'hint', text: msg })); }
  function stat(v, l) { return el('div', { class: 'card stat', style: 'margin:0' }, el('b', { text: v }), el('span', { text: l })); }
  function quick(ic, name, key) {
    return el('button', { class: 'tool', onclick: function () { if (key.indexOf('home:') === 0) go('home', key.slice(5)); else go('tools', key); } }, el('span', { class: 'ti' }, icon(ic)), el('b', { text: name }));   // 首页统一用黑白线条图标
  }

  // ============================================================ 网站导航
  var editMode = false;
  function pageNav(page) {
    page.appendChild(top('网站导航'));
    var holder = el('div'); page.appendChild(holder); holder.appendChild(skeleton(4));
    function draw() {
      clear(holder);
      var search = el('input', { class: 'input', placeholder: '🔍 搜索网站', style: 'margin-bottom:6px' });
      var bar = el('div', { class: 'row', style: 'margin-bottom:4px' }, search);
      if (me && me.admin) bar.appendChild(el('button', { class: 'btn' + (editMode ? ' primary' : ''), text: editMode ? '完成' : '编辑', onclick: function () { editMode = !editMode; draw(); } }));
      holder.appendChild(bar);
      var list = el('div'); holder.appendChild(list);
      function render() {
        clear(list);
        var q = search.value.trim().toLowerCase(), shown = 0;
        navData.forEach(function (c) {
          var sites = c.sites.map(function (s, i) { return { s: s, i: i }; }).filter(function (x) { return !q || (x.s.name + ' ' + x.s.url).toLowerCase().indexOf(q) >= 0; });
          if (q && !sites.length) return;
          shown++;
          list.appendChild(el('div', { class: 'cat-title' }, c.name, el('span', { class: 'sp' }),
            editMode ? [el('button', { class: 'btn sm', text: '＋ 网站', onclick: function () { siteForm(c.name); } }), el('button', { class: 'btn sm', text: '改名', onclick: function () { sheet('分类改名', [{ key: 'name', label: '新名称', value: c.name }], '保存', function (v) { return navEdit({ op: 'rename_cat', cat: c.name, name: v.name }); }); } }), el('button', { class: 'btn sm danger', text: '删', onclick: function () { confirmBox('删除分类', '「' + c.name + '」和里面的 ' + c.sites.length + ' 个网站会一起删除。', function () { return navEdit({ op: 'del_cat', cat: c.name }); }); } })] : null));
          var g = el('div', { class: 'grid g2' });
          sites.forEach(function (x) { g.appendChild(siteTile(c, x.s, x.i)); });
          if (!sites.length) g.appendChild(el('div', { class: 'hint', text: '这个分类还没有网站' }));
          list.appendChild(g);
        });
        if (!shown) list.appendChild(el('div', { class: 'empty' }, el('span', { class: 'big wi' }, icon('qsearch')), q ? '没有找到匹配的网站' : '还没有任何分类'));
        if (editMode) list.appendChild(el('button', { class: 'btn block', style: 'margin-top:16px', text: '＋ 新建分类', onclick: function () { sheet('新建分类', [{ key: 'name', label: '分类名称' }], '创建', function (v) { return navEdit({ op: 'add_cat', name: v.name }); }); } }));
      }
      search.addEventListener('input', render);
      render();
    }
    // ---- 网站图标：由服务器代取并缓存（手机直连谷歌在国内经常失败）；本机再存一份，下次秒显
    //      服务器接口不可用时退回原来的方式：手机直接请求谷歌图标服务
    var iconMy = epoch;
    function setIcon(ico, host) {
      var d = navIcons[host];
      if (d) { clear(ico); ico.appendChild(el('img', { src: d, alt: '' })); }
      else if (iconFallback) googleIcon(ico, host);
    }
    function googleIcon(ico, host) {
      if (ico.querySelector('img')) return;
      var img = el('img', { src: 'https://www.google.com/s2/favicons?domain=' + encodeURIComponent(host) + '&sz=64', referrerpolicy: 'no-referrer' });   // 不能用 lazy：图片还没放进页面，lazy 的话有的浏览器永远不加载
      img.onload = function () { if (img.naturalWidth > 16 && !ico.querySelector('img')) { clear(ico); ico.appendChild(img); } };
    }
    function paintIcons() {
      Array.prototype.forEach.call(holder.querySelectorAll('.ico[data-host]'), function (ico) { setIcon(ico, ico.getAttribute('data-host')); });
    }
    function navHosts(onlyMissing) {
      var hs = {};
      (navData || []).forEach(function (c) { c.sites.forEach(function (st) {
        try { var h = new URL(st.url).hostname.toLowerCase().replace(/\.$/, ''); if (h && (!onlyMissing || !(h in navIcons))) hs[h] = 1; } catch (e) {}
      }); });
      return Object.keys(hs);
    }
    function loadIcons(onlyMissing, retry) {
      var hosts = navHosts(onlyMissing);
      if (!hosts.length) return;
      api('favicons', { hosts: hosts }).then(function (r) {
        if (iconMy !== epoch) return;
        var ic = r.icons || {};
        Object.keys(ic).forEach(function (h) { navIcons[h] = ic[h] || ''; });
        saveIcons(); paintIcons();
        if (r.pending && r.pending.length && !retry) timers.push(setTimeout(function () { if (iconMy === epoch) loadIcons(false, true); }, 5000));
      }).catch(function () {
        if (iconMy !== epoch) return;
        iconFallback = true; paintIcons();
      });
    }
    function navEdit(p) { return api('nav_edit', p).then(function (d) { navData = d.categories; toast('已保存'); draw(); loadIcons(true); }); }
    function siteForm(cat, idx, site) {
      sheet(site ? '编辑网站' : '添加网站到「' + cat + '」', [{ key: 'name', label: '名称', value: site ? site.name : '' }, { key: 'url', label: '网址', value: site ? site.url : '', ph: 'https://' }], '保存',
        function (v) { return navEdit(site ? { op: 'edit_site', cat: cat, index: idx, name: v.name, url: v.url } : { op: 'add_site', cat: cat, name: v.name, url: v.url }); });
    }
    function siteTile(c, s, i) {
      var host = ''; try { host = new URL(s.url).hostname.toLowerCase().replace(/\.$/, ''); } catch (e) {}
      var ico = el('div', { class: 'ico', text: (s.name || '?').trim().charAt(0).toUpperCase() });
      if (host) { ico.setAttribute('data-host', host); setIcon(ico, host); }
      var t = el('div', { class: 'site' + (editMode ? ' ed' : ''), onclick: function () { if (!editMode) openLink(s.url); } }, ico, el('div', { class: 't' }, el('b', { text: s.name }), el('span', { text: host || s.url })));
      if (editMode) t.appendChild(el('div', { class: 'x' }, el('button', { text: '✎', onclick: function (e) { e.stopPropagation(); siteForm(c.name, i, s); } }), el('button', { text: '✕', onclick: function (e) { e.stopPropagation(); confirmBox('删除网站', '删除「' + s.name + '」？', function () { return navEdit({ op: 'del_site', cat: c.name, index: i }); }); } })));
      return t;
    }
    (navData ? Promise.resolve({ categories: navData }) : api('nav')).then(function (d) { navData = d.categories; draw(); loadIcons(false); }).catch(function (e) { clear(holder).appendChild(failBox(e.message, function () { go('nav'); })); });
  }

  // ============================================================ 工具
  var TOOLS = [
    ['ping', 'qping', '全球 Ping', '18 个节点实时延迟', '#4dd4ff'],
    ['route', 'qroute', '路由追踪', '三网回程 + 地图', '#8b7bff'],
    ['scan', 'qscan', '端口扫描', '常用 / 范围 / 全端口', '#3ddc97'],
    ['ipq', 'qipq', 'IP 质量', '风险 · 原生 · 黑名单', '#ffc857'],
    ['ipinfo', 'qpin', 'IP 信息', '归属地 · 运营商 · 线路', '#ff6b81']
  ];
  function pageTools(page, sub) {
    if (sub && /^ssh/.test(sub) && me && me.owner) return sshRoute(page, sub);
    if (sub && /^px/.test(sub) && me && me.owner) return pxRoute(page, sub);
    if ((sub === 'gh' || sub === 'cf' || (sub && /^git/.test(sub))) && me && me.owner) return sub === 'gh' ? pageGhHub(page) : sub === 'cf' ? pageCfHub(page) : gitRoute(page, sub);
    if (sub === 'servers' && me && me.admin) return pageServers(page);
    if (sub === 'srvgroups' && me && me.admin) return pageGroups(page);
    if (sub === 'devices' && me && me.admin) return pageDevices(page);
    if (sub && sub.indexOf('server:') === 0 && me && me.admin) return pageServer(page, sub.slice(7));
    var t = TOOLS.filter(function (x) { return x[0] === sub; })[0];
    if (!t) {
      if (me && me.owner) {        // 所有者：网络工具、服务器监控、GitHub、Cloudflare 都在首页有入口，这里只放 SFTP 服务器
        page.appendChild(top('工具'));
        page.appendChild(sshTile());
        page.appendChild(pxTile());
        return;
      }
      page.appendChild(top('网络工具'));
      var g = el('div', { class: 'grid g2' });
      TOOLS.forEach(function (x) { g.appendChild(el('button', { class: 'tool', style: '--tc:' + x[4], onclick: function () { go('tools', x[0]); } }, el('span', { class: 'ti' }, icon(x[1])), el('b', { text: x[2] }), el('span', { text: x[3] }))); });
      if (me && me.admin) g.appendChild(el('button', { class: 'tool', style: '--tc:#9aa0ac', onclick: function () { go('tools', 'servers'); } }, el('span', { class: 'ti' }, icon('srv')), el('b', { text: '服务器监控' }), el('span', { text: '多台服务器状态' })));
      if (me && me.owner) {
        g.appendChild(el('button', { class: 'tool', style: '--tc:#9aa0ac', onclick: function () { go('tools', 'gh'); } }, el('span', { class: 'ti' }, icon('qgit')), el('b', { text: 'GitHub' }), el('span', { text: '仓库 · 文件 · 提交记录' })));
        g.appendChild(el('button', { class: 'tool', style: '--tc:#f38020', onclick: function () { go('tools', 'cf'); } }, el('span', { class: 'ti' }, icon('dns')), el('b', { text: 'Cloudflare' }), el('span', { text: '域名解析 · R2 存储' })));
      }
      page.appendChild(g);
      page.appendChild(el('div', { class: 'hint', text: '检测由服务器上的机器人执行，结果与机器人里的一致。' }));
      if (me && me.admin && !me.owner) page.appendChild(devEntry('tools'));   // 授权用户没有管理页，入口放这里
      return;
    }
    page.appendChild(top(t[2], true));
    ({ ping: toolPing, route: toolRoute, scan: toolScan, ipq: toolIpq, ipinfo: toolIpinfo })[sub](page);
  }

  function targetBox(placeholder, btnText, onGo, extra) {
    var inp = el('input', { class: 'input', placeholder: placeholder, autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', inputmode: 'url' });
    var btn = el('button', { class: 'btn primary', text: btnText });
    function fire() { var v = inp.value.trim(); if (!v) { toast('请先输入地址', true); return; } onGo(v, btn); }
    btn.addEventListener('click', fire);
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') fire(); });
    var card = el('div', { class: 'card' }, el('div', { class: 'row' }, inp, btn), extra || null);
    return { card: card, input: inp, btn: btn };
  }

  // 最近记录：下拉展开（kind = ping / hosts / scan）；点一行把内容填进输入框并自动收起
  function recentChips(kind, onPick) {
    var wrap = el('div', { class: 'rec', style: 'display:none' });
    var cnt = el('span', { class: 'cnt' });
    var head = el('button', { class: 'rec-h', type: 'button' }, el('span', { class: 'ic' }, icon('qhist')), el('span', { class: 'tt', text: '最近记录' }), cnt, el('span', { class: 'sp' }), el('span', { class: 'arr', text: '⌄' }));
    var list = el('div', { class: 'rec-list' });
    head.addEventListener('click', function () { wrap.classList.toggle('open'); hap(); });
    wrap.appendChild(head); wrap.appendChild(el('div', { class: 'rec-body' }, list));
    api('recent').then(function (d) {
      var items = d[kind] || [];
      if (!items.length) return;
      items.forEach(function (r) {
        var main = typeof r === 'string' ? r : (kind === 'ping' ? r.label : r.host);
        var extra = typeof r === 'string' || kind === 'ping' ? '' : (r.label || '');
        list.appendChild(el('button', { class: 'rec-i', type: 'button', onclick: function () { wrap.classList.remove('open'); onPick(r); hap(); } },
          el('span', { class: 'm mono', text: main }), extra ? el('span', { class: 'x', text: extra }) : null));
      });
      cnt.textContent = items.length;
      wrap.style.display = '';
    }).catch(function () {});
    return wrap;
  }

  // 轮询任务：onTick(progress)；完成后 resolve(result)；离开页面自动停止
  function pollJob(id, onTick) {
    var my = epoch, fails = 0;
    return new Promise(function (resolve, reject) {
      function tick() {
        if (my !== epoch) return;
        api('job_get', { id: id }).then(function (j) {
          if (my !== epoch) return;
          fails = 0;
          if (onTick) onTick(j.progress, j);
          if (j.done) { if (j.error && !j.result) reject(new Error(j.error)); else resolve(j.result); return; }
          timers.push(setTimeout(tick, 900));
        }).catch(function (e) {
          if (my !== epoch) return;
          // 网络抖一下就放弃的话，按钮会被放开、任务却还在服务器上跑；多等几轮再判失败
          if (++fails <= 5 && /网络/.test(e.message || '')) { timers.push(setTimeout(tick, 1500 * fails)); return; }
          reject(e);
        });
      }
      tick();
    });
  }
  // 接上一个已经在服务器上跑的任务（或刚开始的任务）：显示进度，完成后显示结果
  function jobAttach(box, jid, view) {
    clear(box);
    var prog = el('div'); box.appendChild(prog);
    var cancelBtn = el('button', { class: 'btn sm danger', text: '取消', onclick: function () { api('job_cancel', { id: jid }).catch(function () {}); } });
    view.start && view.start(prog, cancelBtn);
    return pollJob(jid, function (p) { view.tick && view.tick(prog, p); })
      .then(function (res) { clear(box); view.done(box, res); hap('ok'); })
      .catch(function (e) { clear(box); box.appendChild(failBox(e.message)); hap('err'); });
  }
  function jobRunner(box, kind, params, view) {
    return api('job_start', Object.assign({ kind: kind }, params)).then(function (r) { return jobAttach(box, r.id, view); });
  }
  // 切换到别的页面再回来：服务器上这类任务还在跑（或刚跑完还没看过结果），就接回来显示，不用重新开始
  function resumeJob(kind, box, view, btn) {
    var my = epoch;
    // 先禁用开始按钮，查清楚服务器上有没有这类任务在跑，再决定接回来还是放开（否则回来那一瞬间还能点出第二个任务）
    if (btn) btn.setAttribute('disabled', '');
    function release() { if (btn && my === epoch) btn.removeAttribute('disabled'); }
    api('job_active').then(function (d) {
      if (my !== epoch) return;
      var j = (d.jobs || []).filter(function (x) { return x.kind === kind; }).pop();
      if (j && !box.firstChild) jobAttach(box, j.id, view).then(release);
      else release();
    }).catch(release);
  }

  // ---------------- 全球 Ping
  function toolPing(page) {
    var tb = targetBox('IP / 域名，可加 :端口（如 1.1.1.1:443）', '开始测试', function (v, btn) {
      run(btn, function () { return jobRunner(out, 'ping', { target: v }, pingView); });
    });
    tb.card.appendChild(recentChips('ping', function (r) { tb.input.value = r.label; tb.btn.click(); }));
    page.appendChild(tb.card);
    var out = el('div'); page.appendChild(out);
    var pingView = {
      start: function (prog) { prog.appendChild(el('div', { class: 'card' }, el('h3', { text: '测试中 · 全球节点' }), el('div', { class: 'nodes', id: 'nodes' }))); },
      tick: function (prog, p) { var n = prog.querySelector('.nodes'); if (n) drawNodes(n, p.nodes || []); },
      done: function (box, r) {
        var s = r.stat;
        box.appendChild(el('div', { class: 'card' },
          el('h3', { text: '结果 · ' + r.target + (r.port ? ':' + r.port : '') + ' · ' + r.proto }),
          el('div', { class: 'grid g3' }, stat(s.min != null ? s.min + 'ms' : '—', '最低'), stat(s.avg != null ? s.avg + 'ms' : '—', '平均'), stat(s.ok + '/' + s.total, '成功节点'))));
        box.appendChild(el('div', { class: 'card' }, el('h3', { text: '各地延迟' }), drawNodes(el('div', { class: 'nodes' }), r.nodes)));
        if (r.map) box.appendChild(el('div', { class: 'card' }, mapImg(r.map)));
        if (r.ips.length) box.appendChild(el('div', { class: 'card' }, el('h3', { text: r.domain ? '解析到 ' + r.ips.length + ' 个 IP' : 'IP 信息' }), r.ips.map(ipCard)));
      }
    };
    resumeJob('ping', out, pingView, tb.btn);
  }
  function drawNodes(n, nodes) {
    clear(n);
    nodes.forEach(function (x) {
      var cls = 'node ' + x.state;
      if (x.state === 'ok') cls += x.rtt > 250 ? ' slow' : x.rtt > 120 ? ' mid' : '';
      n.appendChild(el('div', { class: cls }, el('span', { text: x.flag }), el('span', { class: 'nm', text: x.name }), el('span', { class: 'ms', text: x.state === 'run' ? '…' : x.text })));
    });
    return n;
  }
  function ipCard(c) {
    var bits = [c.country, c.location].filter(Boolean).join(' ');
    var net = [c.isp, c.line].filter(Boolean).join(' · ');
    return el('div', { class: 'ipcard' },
      el('div', { class: 'ip mono', text: c.ip, onclick: function () { copy(c.ip); } }),
      bits ? el('div', { class: 'sub' }, icon('pin'), bits + (c.note || '')) : null,
      net ? el('div', { class: 'sub' }, icon('bld'), net) : null,
      c.asn ? el('div', { class: 'sub' }, icon('net'), c.asn) : null,
      c.sources && c.sources.length ? el('div', { class: 'sub', text: '来源：' + c.sources.join(' · ') }) : null);
  }

  // ---------------- 路由追踪
  function toolRoute(page) {
    var pick = 'SH_CT';
    var opts = [['SH_CT', '电信', ['#60a5fa']], ['SH_CU', '联通', ['#f87171']], ['SH_CM', '移动', ['#4ade80']], ['all', '三网', ['#60a5fa', '#f87171', '#4ade80']]];
    var seg = el('div', { class: 'seg', style: 'margin-top:10px' });
    opts.forEach(function (o) {
      var dots = el('span', { class: 'cdots' });
      o[2].forEach(function (c) { dots.appendChild(el('i', { class: 'sdot', style: '--c:' + c })); });
      seg.appendChild(el('button', { class: 'seg-b' + (o[0] === pick ? ' on' : ''), onclick: function (e) {
        pick = o[0]; Array.prototype.forEach.call(seg.children, function (b) { b.classList.remove('on'); }); e.currentTarget.classList.add('on');
      } }, dots, el('span', { text: o[1] })));
    });
    var tb = targetBox('目标 IP / 域名', '开始追踪', function (v, btn) {
      run(btn, function () { return jobRunner(out, 'route', { target: v, sources: pick === 'all' ? 'all' : [pick] }, routeView); });
    }, seg);
    tb.card.appendChild(recentChips('hosts', function (h) { tb.input.value = h; }));
    page.appendChild(tb.card);
    page.appendChild(el('div', { class: 'hint', style: 'margin:-4px 4px 10px', text: '从国内三大运营商节点回程到目标，通常需要 20~60 秒。' }));
    var out = el('div'); page.appendChild(out);
    var routeView = {
      start: function (prog, cancel) { prog.appendChild(el('div', { class: 'card' }, el('div', { class: 'row' }, el('span', { class: 'spin' }), el('b', { text: '正在追踪路由…', style: 'flex:1' }), cancel), el('div', { class: 'items', style: 'margin-top:10px' }))); },
      tick: function (prog, p) { var it = prog.querySelector('.items'); if (!it) return; clear(it); (p.items || []).forEach(function (x) { it.appendChild(el('span', { class: 'pill ' + (x.state === 'ok' ? 'ok' : x.state === 'fail' ? 'bad' : ''), style: 'margin-right:6px', text: x.carrier + ' ' + (x.state === 'ok' ? '完成' : x.state === 'fail' ? '失败' : '追踪中') })); }); },
      done: function (box, r) {
        r.runs.forEach(function (run) {
          var card = el('div', { class: 'card' }, el('h3', { text: run.carrier + (run.source ? ' · ' + run.source : '') }));
          if (run.error) { var he = el('div', { class: 'hint' }); he.appendChild(rich('❌ ' + run.error)); card.appendChild(he); box.appendChild(card); return; }
          card.appendChild(el('div', { style: 'margin-bottom:10px' }, el('span', { class: 'pill ' + (run.reached ? 'ok' : 'warn'), text: run.reached ? '已到达目标' : '未完整到达' }), ' ', run.target_ip ? el('span', { class: 'pill mono', text: run.target_ip }) : null));
          if (run.verdict) { var vd = el('div', { class: 'verdict' }); vd.appendChild(rich(run.verdict)); card.appendChild(vd); }
          if (run.map) card.appendChild(el('div', { style: 'margin-bottom:12px' }, mapImg(run.map)));
          var max = Math.max.apply(null, run.hops.map(function (h) { return h.rtt || 0; }).concat([1]));
          var hops = el('div', { class: 'hops' });
          run.hops.forEach(function (h) {
            hops.appendChild(el('div', { class: 'hop' + (h.ip ? '' : ' none') }, el('div', { class: 'n', text: h.n }),
              el('div', { class: 'b' }, el('div', { class: 'ip mono', text: h.ip || '* * *（无响应）' }), h.place || h.as ? el('div', { class: 'pl', text: [h.place, h.as].filter(Boolean).join(' · ') }) : null,
                h.rtt != null ? el('div', { class: 'bar' + (h.rtt > 250 ? ' bad' : h.rtt > 120 ? ' warn' : ' ok'), style: 'margin-top:5px' }, el('i', { style: 'width:' + Math.max(4, Math.round(h.rtt / max * 100)) + '%' })) : null),
              el('div', { class: 'r', text: h.rtt != null ? h.rtt + ' ms' : '' })));
          });
          card.appendChild(hops);
          box.appendChild(card);
        });
      }
    };
    resumeJob('route', out, routeView, tb.btn);
  }

  // ---------------- 端口扫描
  function toolScan(page) {
    var spec = '', specs = [['', '常用'], ['1-1000', '1-1000'], ['22,80,443,3306,6379', '常见服务'], ['all', '全部端口']];
    var chips = el('div', { class: 'chips' });
    var custom = el('input', { class: 'input', placeholder: '自定义端口，如 22,80,8000-9000', style: 'margin-top:10px' });
    specs.forEach(function (s) { chips.appendChild(el('button', { class: 'chip' + (s[0] === spec ? ' on' : ''), text: s[1], onclick: function (e) { spec = s[0]; custom.value = ''; Array.prototype.forEach.call(chips.children, function (b) { b.classList.remove('on'); }); e.target.classList.add('on'); } })); });
    var tb = targetBox('目标 IP / 域名', '开始扫描', function (v, btn) {
      var ports = custom.value.trim() || spec;
      run(btn, function () { return jobRunner(out, 'scan', { target: v, ports: ports }, scanView); });
    }, el('div', null, chips, custom));
    tb.card.appendChild(recentChips('scan', function (r) { tb.input.value = r.host; custom.value = r.spec || ''; spec = ''; Array.prototype.forEach.call(chips.children, function (b) { b.classList.remove('on'); }); }));
    page.appendChild(tb.card);
    var out = el('div'); page.appendChild(out);
    var scanView = {
      start: function (prog, cancel) { prog.appendChild(el('div', { class: 'card' }, el('div', { class: 'row' }, el('b', { class: 'lab', style: 'flex:1', text: '扫描中…' }), cancel), el('div', { class: 'bar', style: 'margin:10px 0' }, el('i', { style: 'width:0%' })), el('div', { class: 'hint pr' }), el('div', { class: 'ops' }))); },
      tick: function (prog, p) {
        if (!p || !p.total) return;
        var pct = Math.round(p.scanned / p.total * 100);
        prog.querySelector('.bar > i').style.width = pct + '%';
        prog.querySelector('.lab').textContent = '扫描 ' + p.label + ' · ' + pct + '%';
        prog.querySelector('.pr').textContent = p.scanned + ' / ' + p.total + ' 个端口 · 已发现开放 ' + p.open.length + ' 个 · ' + (p.elapsed || 0) + 's';
        var ops = prog.querySelector('.ops'); clear(ops); p.open.slice().sort(function (a, b) { return a - b; }).forEach(function (x) { ops.appendChild(el('span', { class: 'pill ok', style: 'margin:3px 4px 0 0', text: x })); });
      },
      done: function (box, r) {
        var c = el('div', { class: 'card' }, el('h3', { text: r.label }),
          el('div', { style: 'margin-bottom:10px' }, el('span', { class: 'pill' + (r.open.length ? ' warn' : ' ok'), text: '开放 ' + r.open.length + ' 个' }), ' ', el('span', { class: 'pill', text: '已扫 ' + r.scanned + '/' + r.total }), ' ', el('span', { class: 'pill', text: r.elapsed + 's' }), r.cancelled ? el('span', { class: 'pill bad', text: '已取消' }) : null));
        if (!r.open.length) c.appendChild(el('div', { class: 'empty' }, el('span', { class: 'big wi' }, icon('qipq')), '没有发现开放端口'));
        r.open.forEach(function (o) { c.appendChild(el('div', { class: 'kv' }, el('span', { class: 'mono', text: o.port }), el('span', { text: o.service || '未知服务' }))); });
        box.appendChild(c);
      }
    };
    resumeJob('scan', out, scanView, tb.btn);
  }

  // ---------------- IP 质量
  var IPQ = null;     // 最近一次 IP 质量检测：{target, p, res, err, done}（全局，切页回来能接着看）
  function toolIpq(page) {
    var tb = targetBox('IP / 域名', '开始检测', function (v, btn) {
      if (IPQ && !IPQ.done) return;
      var q = IPQ = { target: v, done: false };
      q.p = api('ipq', { target: v }).then(function (r) { q.res = r; }, function (e) { q.err = e; }).then(function () { q.done = true; });
      show(q);
    });
    tb.card.appendChild(recentChips('hosts', function (h) { tb.input.value = h; }));
    page.appendChild(tb.card);
    var out = el('div'); page.appendChild(out);
    var my = epoch;
    function show(q) {
      tb.input.value = q.target;
      if (!q.done) {
        tb.btn.setAttribute('disabled', '');
        clear(out).appendChild(el('div', { class: 'card empty' }, el('span', { class: 'spin' }), el('div', { class: 'hint', text: '正在查询多个数据源，约 10 秒…' })));
        q.p.then(function () { if (epoch === my) show(q); });
        return;
      }
      tb.btn.removeAttribute('disabled');
      clear(out);
      if (q.err) { if (!q.shown) toast(q.err.message, true); q.shown = true; return; }
      var r = q.res;
      var color = r.level === 'good' ? 'var(--ok)' : r.level === 'warn' ? 'var(--warn)' : 'var(--bad)';
      out.appendChild(el('div', { class: 'card' }, el('div', { class: 'score', style: '--c:' + color }, el('div', { class: 'em', text: r.level === 'good' ? '🟢' : r.level === 'warn' ? '🟡' : '🔴' }),
        el('div', null, el('b', { text: r.verdict }), el('div', { class: 'hint mono', text: r.target + (r.target !== r.ip ? ' → ' + r.ip : '') })))));
      r.groups.forEach(function (g) {
        var c = el('div', { class: 'card' }, el('h3', { text: g.title }));
        g.rows.forEach(function (row) { c.appendChild(el('div', { class: 'qrow' }, stIcon(row.icon), el('span', { class: 'lb', text: row.label }), el('span', { class: 'vl', text: row.value }))); });
        out.appendChild(c);
      });
      if (!q.shown) hap('ok');
      q.shown = true;
    }
    if (IPQ) show(IPQ);
  }

  // ---------------- IP 信息
  function toolIpinfo(page) {
    var tb = targetBox('IP / 域名', '查询', function (v, btn) {
      run(btn, function () {
        clear(out).appendChild(skeleton(2));
        return api('ipinfo', { target: v }).then(function (r) {
          clear(out).appendChild(el('div', { class: 'card' }, el('h3', { text: r.domain ? r.target + ' 解析到 ' + r.ips.length + ' 个 IP' : r.target }), r.ips.map(ipCard)));
        }).catch(function (e) { clear(out); toast(e.message, true); });
      });
    });
    tb.card.appendChild(recentChips('hosts', function (h) { tb.input.value = h; }));
    page.appendChild(tb.card);
    var out = el('div'); page.appendChild(out);
  }

  // ============================================================ 解析（Cloudflare 域名解析，仅所有者）
  var dnsZones = null, dnsRecs = {}, dnsType = 'ALL', dnsQ = '', dnsOpen = {};
  var DNS_TYPES = ['A', 'AAAA', 'CNAME', 'TXT', 'MX', 'NS'];
  var DNS_TTL = [[1, '自动'], [60, '1 分钟'], [300, '5 分钟'], [600, '10 分钟'], [3600, '1 小时'], [86400, '1 天']];
  function relName(name, zone) {
    if (name === zone) return '@';
    return name.slice(-(zone.length + 1)) === '.' + zone ? name.slice(0, -(zone.length + 1)) : name;
  }
  function ttlText(t) { return t === 1 ? '自动' : t >= 3600 && t % 3600 === 0 ? t / 3600 + ' 小时' : t >= 60 && t % 60 === 0 ? t / 60 + ' 分钟' : t + ' 秒'; }
  function expInfo(z) {
    if (!z.exp) return { t: z.exp_err ? '到期日查不到' : '到期日查询中…', c: '' };
    var d = new Date(z.exp * 1000), p = function (x) { return (x < 10 ? '0' : '') + x; };
    var date = d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
    var days = Math.floor((z.exp * 1000 - Date.now()) / 86400000);
    if (days < 0) return { t: '已过期 ' + date, c: 'bad' };
    return { t: '到期 ' + date + ' · 剩 ' + days + ' 天', c: days < 7 ? 'bad' : days < 30 ? 'warn' : '' };
  }
  function zoneStatus(z) { return z.paused ? '已暂停' : z.status === 'active' ? '正常' : z.status === 'pending' ? '待验证' : z.status; }

  // ---- 换 IP：先查出所有指向旧 IP 的记录，勾选确认后批量替换
  function swapSheet(zone) {
    sheet(zone ? '换 IP · ' + zone.name : '换 IP（所有域名）', [
      { key: 'old', label: '旧 IP', ph: '例如 1.2.3.4' },
      { key: 'new', label: '新 IP', ph: '例如 5.6.7.8' }
    ], '查找记录', function (v) {
      return api('cf_swap', { old: v.old.trim(), new: v.new.trim(), zone: zone ? zone.id : undefined }).then(swapPreview);
    }, el('div', { class: 'hint', text: '会找出' + (zone ? '这个域名' : '所有域名') + '里内容等于旧 IP 的 A / AAAA 记录，确认后才会修改。' }));
  }
  function swapPreview(d) {
    var bg = el('div', { class: 'sheet-bg', onclick: function (e) { if (e.target === bg) bg.remove(); } });
    var body = el('div', { class: 'sheet' }, el('h2', { text: '确认换 IP' }), el('div', { class: 'hint mono', text: d.old + '  →  ' + d.new }));
    bg.appendChild(body); document.body.appendChild(bg);
    var cancel = el('button', { class: 'btn', text: d.items.length ? '取消' : '关闭', onclick: function () { bg.remove(); } });
    if (!d.items.length) {
      body.appendChild(el('div', { class: 'card empty', style: 'margin-top:10px' }, '没有找到指向 ' + d.old + ' 的记录'));
      body.appendChild(el('div', { class: 'acts' }, cancel));
      return;
    }
    var boxes = [], list = el('div', { class: 'swl' });
    var ok = el('button', { class: 'btn primary' });
    function count() { var n = boxes.filter(function (b) { return b[0].checked; }).length; ok.textContent = '替换选中的 ' + n + ' 条'; return n; }
    d.items.forEach(function (x) {
      var cb = el('input', { type: 'checkbox' }); cb.checked = true; cb.addEventListener('change', count);
      boxes.push([cb, x]);
      list.appendChild(el('label', { class: 'chk' }, cb, el('span', null, el('b', { text: x.name }), el('small', { text: x.zone + ' · ' + x.type + (x.proxied ? ' · 橙云' : '') + (x.comment ? ' · ' + x.comment : '') }))));
    });
    body.appendChild(el('div', { class: 'hint', text: '找到 ' + d.items.length + ' 条，取消勾选的不会修改：' }));
    body.appendChild(list);
    count();
    ok.addEventListener('click', function () {
      var keys = boxes.filter(function (b) { return b[0].checked; }).map(function (b) { return b[1].key; });
      if (!keys.length) { toast('请至少勾选一条', true); return; }
      run(ok, function () {
        return api('cf_swap_edit', { old: d.old, new: d.new, keys: keys }).then(function (r) {
          bg.remove();
          dnsRecs = {};
          if (cur.tab === 'dns') go('dns', cur.sub);
          var msg = '已更新 ' + r.done.length + ' 条';
          if (r.missing) msg += '，' + r.missing + ' 条已被改动过（跳过）';
          if (!r.failed.length) { toast(msg); return; }
          var fb = el('div', { class: 'sheet-bg' }), fs = el('div', { class: 'sheet' }, el('h2', { text: '部分失败' }), el('div', { class: 'hint', text: msg + '，失败 ' + r.failed.length + ' 条：' }));
          r.failed.forEach(function (x) { fs.appendChild(el('div', { class: 'kv' }, el('span', { class: 'mono', style: 'width:auto', text: x.name }), el('span', { text: x.error }))); });
          fs.appendChild(el('div', { class: 'acts' }, el('button', { class: 'btn primary', text: '知道了', onclick: function () { fb.remove(); } })));
          fb.appendChild(fs); document.body.appendChild(fb); hap('err');
        });
      });
    });
    body.appendChild(el('div', { class: 'acts' }, cancel, ok));
  }
  // ---- 清除缓存
  function purgeSheet(zone) {
    sheet('清除缓存 · ' + zone.name, [
      { key: 'type', label: '范围', options: [['all', '清除全部缓存'], ['urls', '只清除指定网址']], value: 'all' },
      { key: 'urls', label: '网址（一行一个，最多 30 个）', multi: true, ph: 'https://' + zone.name + '/style.css', only: ['urls'] }
    ], '清除', function (v) {
      return api('cf_edit', { op: 'purge', zone: zone.id, mode: v.type, urls: v.urls }).then(function (d) {
        toast(d.purged === 'all' ? '已清除 ' + zone.name + ' 的全部缓存' : '已清除 ' + d.purged + ' 个网址的缓存');
      });
    });
  }

  function pageDns(page, sub) {
    if (me && !me.owner) { page.appendChild(lockBox('解析管理只有所有者可以使用')); return; }
    if (sub && sub.indexOf('z:') === 0) return pageDnsZone(page, sub.slice(2));
    if (sub && sub.indexOf('zs:') === 0) return pageDnsSec(page, sub.slice(3));
    if (sub === 'r2') return pageR2(page);
    if (sub && sub.indexOf('r2s:') === 0) return pageR2Bucket(page, sub.slice(4), '', true);
    if (sub && sub.indexOf('r2b:') === 0) { var r2a = sub.slice(4), r2i = r2a.indexOf(':'); return pageR2Bucket(page, r2a.slice(0, r2i), r2a.slice(r2i + 1), false); }
    page.appendChild(top('域名解析', true));
    page.appendChild(rootSeg('dom'));
    var holder = el('div'); page.appendChild(holder); holder.appendChild(skeleton(3));
    var myEpoch = epoch, polls = 0;
    function load(refresh) {
      return api('cf', refresh ? { refresh: 1 } : {}).then(function (d) {
        dnsZones = d.zones; clear(holder);
        if (!d.zones.length) { holder.appendChild(el('div', { class: 'card empty' }, el('span', { class: 'big wi' }, icon('dns')), '这个 Token 下没有域名')); return; }
        d.zones.forEach(function (z) {
          var ex = expInfo(z);
          holder.appendChild(el('button', { class: 'card dz', onclick: function () { go('dns', 'z:' + z.id); } },
            el('span', { class: 'dz-ic' }, icon('dns')),
            el('span', { class: 'dz-t' }, el('b', { text: z.name }), el('small', { class: ex.c, text: ex.t })),
            el('span', { class: 'pill ' + (z.status === 'active' && !z.paused ? 'ok' : 'warn'), text: zoneStatus(z) }),
            goArrow()));
        });
        holder.appendChild(el('div', { class: 'hint', text: '点一个域名管理它的解析记录。改动立刻生效，并记录在服务器的 cf_audit.jsonl 里。到期日每 12 小时更新一次。' }));
        if (d.exp_pending && polls < 12) {    // 到期日在后台查询：每 3 秒再取一次，最多等 36 秒
          polls++;
          timers.push(setTimeout(function () { if (epoch === myEpoch) load(false); }, 3000));
        }
      }).catch(function (e) { clear(holder).appendChild(failBox(e.message, function () { go('dns', 'dom', true); })); });
    }
    load(false);
  }

  function pageDnsZone(page, zid) {
    var known = (dnsZones || []).filter(function (z) { return z.id === zid; })[0];
    var heading = top(known ? known.name : '解析记录', true);
    page.appendChild(heading);
    page.appendChild(zoneSeg(zid, 'rec'));
    var holder = el('div'); page.appendChild(holder);
    var search = el('input', { class: 'input', placeholder: '🔍 搜索名称 / 内容', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false' });
    search.value = dnsQ;
    var refreshBtn = el('button', { class: 'btn', text: '↻', 'aria-label': '刷新', onclick: function () { run(refreshBtn, fetchRecs); } });
    var addBtn = el('button', { class: 'btn primary', text: '＋ 添加', onclick: function () { recSheet(null); } });
    var chips = el('div', { class: 'tabs-x', style: 'margin-top:8px' });
    var listBox = el('div');
    var foot = el('div', { class: 'hint' });
    function zoneObj() { var z = cache ? cache.zone : known; if (!z) toast('还在读取，请稍等', true); return z; }
    holder.appendChild(el('div', { class: 'row' }, search, refreshBtn, addBtn));
    holder.appendChild(el('div', { class: 'row', style: 'margin-top:8px' },
      el('button', { class: 'btn sm', style: 'flex:1', text: '⇄ 换 IP', onclick: function () { var z = zoneObj(); if (z) swapSheet(z); } }),
      el('button', { class: 'btn sm', style: 'flex:1', text: '清除缓存', onclick: function () { var z = zoneObj(); if (z) purgeSheet(z); } })));
    holder.appendChild(chips); holder.appendChild(listBox); holder.appendChild(foot);
    var cache = dnsRecs[zid];
    if (!cache) listBox.appendChild(skeleton(4));

    function zname() { return cache.zone.name; }
    function drawChips() {
      clear(chips);
      var counts = {}, order = [];
      cache.records.forEach(function (r) { counts[r.type] = (counts[r.type] || 0) + 1; });
      DNS_TYPES.concat(Object.keys(counts).filter(function (t) { return DNS_TYPES.indexOf(t) < 0; })).forEach(function (t) { if (counts[t]) order.push(t); });
      if (dnsType !== 'ALL' && !counts[dnsType]) dnsType = 'ALL';
      chips.appendChild(el('button', { class: 'chip' + (dnsType === 'ALL' ? ' on' : ''), text: '全部 ' + cache.records.length, onclick: function () { dnsType = 'ALL'; drawChips(); drawList(); } }));
      order.forEach(function (t) { chips.appendChild(el('button', { class: 'chip' + (dnsType === t ? ' on' : ''), text: t + ' ' + counts[t], onclick: function () { dnsType = t; drawChips(); drawList(); } })); });
    }
    function matches(r) {
      if (dnsType !== 'ALL' && r.type !== dnsType) return false;
      var q = dnsQ.trim().toLowerCase();
      return !q || (r.name + ' ' + r.content + ' ' + r.comment).toLowerCase().indexOf(q) >= 0;
    }
    function upsert(rec, open) {
      var i = -1;
      cache.records.forEach(function (r, k) { if (r.id === rec.id) i = k; });
      if (i >= 0) cache.records[i] = rec; else cache.records.push(rec);
      cache.records.sort(function (a, b) { return a.name < b.name ? -1 : a.name > b.name ? 1 : a.type < b.type ? -1 : a.type > b.type ? 1 : 0; });
      if (open) dnsOpen[rec.id] = true;
      drawChips(); drawList();
    }
    function removeRec(id) {
      cache.records = cache.records.filter(function (r) { return r.id !== id; });
      delete dnsOpen[id];
      drawChips(); drawList();
    }
    function payload(op, v, r) {
      return { op: op, zone: zid, id: r ? r.id : undefined, type: v.type, name: v.name, content: v.content, priority: v.priority, ttl: Number(v.ttl), proxied: v.proxied, comment: v.comment };
    }
    function recSheet(r) {
      var zn = zname(), isNew = !r;
      var ttls = DNS_TTL.slice();
      if (r && !ttls.some(function (o) { return o[0] === r.ttl; })) ttls.push([r.ttl, ttlText(r.ttl)]);
      sheet(isNew ? '添加解析记录' : '编辑解析记录', [
        { key: 'type', label: '类型', options: DNS_TYPES.map(function (t) { return [t, t]; }), value: r ? r.type : (DNS_TYPES.indexOf(dnsType) >= 0 ? dnsType : 'A') },
        { key: 'name', label: '名称（@ 表示根域名 ' + zn + '）', ph: '例如 www', value: r ? relName(r.name, zn) : '' },
        { key: 'content', label: '内容', ph: 'IP 地址 / 目标域名 / 文本', value: r ? r.content : '' },
        { key: 'priority', label: '优先级', only: ['MX'], ph: '10', value: r && r.priority != null ? String(r.priority) : '10' },
        { key: 'ttl', label: 'TTL', options: ttls, value: r ? r.ttl : 1 },
        { key: 'proxied', check: '经过 Cloudflare 代理（橙云，TTL 自动）', only: ['A', 'AAAA', 'CNAME'], value: r ? r.proxied : false },
        { key: 'comment', label: '备注（可不填）', value: r ? r.comment : '' }
      ], '保存', function (v) {
        return api('cf_edit', payload(isNew ? 'add' : 'edit', v, r)).then(function (d) { upsert(d.record, true); toast(isNew ? '已添加' : '已保存'); });
      });
    }
    function undoBar(rec) {
      var bar = el('div', { class: 'toast undo' }, el('span', { text: '已删除 ' + relName(rec.name, zname()) + '  ' }),
        el('button', { class: 'btn sm primary', text: '撤销', onclick: function () {
          bar.remove();
          run(null, function () {
            return api('cf_edit', { op: 'add', zone: zid, type: rec.type, name: rec.name, content: rec.content, priority: rec.priority, ttl: rec.ttl, proxied: rec.proxied, comment: rec.comment })
              .then(function (d) { upsert(d.record, true); toast('已恢复'); });
          });
        } }));
      document.body.appendChild(bar);
      setTimeout(function () { bar.remove(); }, 8000);
    }
    function row(r) {
      var zn = zname();
      var item = el('div', { class: 'note nitem dr' + (dnsOpen[r.id] ? ' open' : '') });
      var main = el('button', { class: 'dr-main', type: 'button' },
        el('span', { class: 'pill dt', text: r.type }),
        el('span', { class: 'dr-t' }, el('b', { text: relName(r.name, zn) }), el('small', { class: 'mono', text: r.content + (r.type === 'MX' && r.priority != null ? '  · 优先级 ' + r.priority : '') })));
      main.addEventListener('click', function () { item.classList.toggle('open'); if (item.classList.contains('open')) dnsOpen[r.id] = true; else delete dnsOpen[r.id]; hap(); });
      var cloud;
      if (r.proxiable) {
        cloud = el('button', { class: 'cfc' + (r.proxied ? ' on' : ''), type: 'button', 'aria-label': r.proxied ? '已代理，点击改为仅 DNS' : '仅 DNS，点击开启代理' }, icon('dns'));
        cloud.addEventListener('click', function () {
          run(cloud, function () { return api('cf_edit', { op: 'proxy', zone: zid, id: r.id, proxied: !r.proxied }).then(function (d) { upsert(d.record); toast(d.record.proxied ? '已开启代理（橙云）' : '已改为仅 DNS（灰云）'); }); });
        });
      } else cloud = el('span', { class: 'cfc none' });
      item.appendChild(el('div', { class: 'dr-h' }, main, cloud));
      var detail = el('div', { class: 'nbc' },
        el('div', { class: 'kv' }, el('span', { text: '完整名称' }), el('span', { class: 'mono', text: r.name })),
        el('div', { class: 'kv' }, el('span', { text: '内容' }), el('span', { class: 'mono', text: r.content })),
        el('div', { class: 'kv' }, el('span', { text: 'TTL' }), el('span', { text: ttlText(r.ttl) })),
        r.proxiable ? el('div', { class: 'kv' }, el('span', { text: '代理' }), el('span', { text: r.proxied ? '已代理（橙云）' : '仅 DNS（灰云）' })) : null,
        r.comment ? el('div', { class: 'kv' }, el('span', { text: '备注' }), el('span', { text: r.comment })) : null,
        r.modified ? el('small', { class: 'nm', text: '修改于 ' + timeText(r.modified) }) : null,
        r.editable ? null : el('div', { class: 'hint', text: '这种类型暂不支持在这里修改，可以删除，修改请到 Cloudflare 后台。' }),
        el('div', { class: 'row', style: 'margin-top:10px' },
          el('button', { class: 'btn sm primary', style: 'flex:1', text: '复制', onclick: function () { copy(r.content); } }),
          r.editable ? el('button', { class: 'btn sm', style: 'flex:1', text: '编辑', onclick: function () { recSheet(r); } }) : null,
          el('button', { class: 'btn sm danger', style: 'flex:1', text: '删除', onclick: function () {
            confirmBox('删除解析记录', r.type + ' ' + r.name + ' → ' + r.content + '。删除后立即生效。', function () {
              return api('cf_edit', { op: 'del', zone: zid, id: r.id }).then(function (d) { removeRec(r.id); if (r.editable) undoBar(d.deleted); else toast('已删除'); });
            });
          } })));
      item.appendChild(el('div', { class: 'nb' }, el('div', { class: 'nbi' }, detail)));
      return item;
    }
    function drawList() {
      clear(listBox);
      var list = cache.records.filter(matches);
      if (!cache.records.length) listBox.appendChild(el('div', { class: 'card empty' }, el('span', { class: 'big wi' }, icon('dns')), '这个域名还没有解析记录'));
      else if (!list.length) listBox.appendChild(el('div', { class: 'card empty' }, '没有符合条件的记录'));
      list.forEach(function (r) { listBox.appendChild(row(r)); });
      foot.textContent = cache.records.length ? '显示 ' + list.length + ' / ' + cache.records.length + ' 条 · 点右侧云朵可切换代理' : '';
    }
    search.addEventListener('input', function () { dnsQ = search.value; if (cache) drawList(); });
    function fetchRecs() {
      return api('cf', { zone: zid }).then(function (d) {
        cache = dnsRecs[zid] = d;
        heading.querySelector('h1').textContent = d.zone.name;
        drawChips(); drawList();
      }).catch(function (e) {
        if (cache) throw e;
        clear(listBox).appendChild(failBox(e.message, function () { go('dns', 'z:' + zid); }));
      });
    }
    if (cache) { drawChips(); drawList(); }
    fetchRecs().catch(function (e) { toast(e.message, true); });
  }

  // ============================================================ 解析 → 安全防护 / R2 存储（仅所有者）
  ICON.docs = '<path d="M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>';
  ICON.qgit = '<circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="8" r="2.2"/><path d="M6 8.2v7.6M18 10.2c0 4-6 3.2-11 6"/>';
  ICON.shield = '<path d="M12 3l7.5 3v5.5c0 4.6-3.1 8-7.5 9.8-4.4-1.8-7.5-5.2-7.5-9.8V6z"/>';
  ICON.bucket = '<ellipse cx="12" cy="5.5" rx="8" ry="2.5"/><path d="M4 5.5v13c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5v-13"/><path d="M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5"/>';
  ICON.folder = '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>';
  ICON.file = '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>';

  // 解析页顶部 / 域名页里的分段切换：切换不进返回栈（返回直接回上一级）
  function dnsSeg(items, on) {
    var s = el('div', { class: 'seg', style: 'margin:-4px 0 12px' });
    items.forEach(function (it) {
      s.appendChild(el('button', { class: it[0] === on ? 'on' : '', text: it[1], onclick: function () { if (it[0] !== on) go('dns', it[2], true); } }));
    });
    return s;
  }
  function zoneSeg(zid, on) { return dnsSeg([['rec', '解析记录', 'z:' + zid], ['sec', '安全防护', 'zs:' + zid]], on); }
  function rootSeg(on) { return dnsSeg([['dom', '域名', 'dom'], ['r2', 'R2 存储', 'r2']], on); }
  function swRow(title, desc, checked, onChange, extraCls) {
    var cb = el('input', { type: 'checkbox' }); cb.checked = !!checked;
    cb.addEventListener('change', function () {
      var want = cb.checked; cb.checked = !want; cb.disabled = true;      // 先不变，等接口成功再变
      Promise.resolve().then(function () { return onChange(want); })
        .then(function (okk) { if (okk !== false) cb.checked = want; }, function (e) { toast(e.message, true); })
        .then(function () { cb.disabled = false; });
    });
    return el('label', { class: 'chk dl' + (extraCls ? ' ' + extraCls : '') }, cb, el('span', null, el('b', { text: title }), desc ? el('small', { text: desc }) : null));
  }
  function permHint(text) { return el('div', { class: 'hint warnl' }, el('span', { class: 'wi' }, icon('warn')), text); }

  // ---------------------------------------------------------------- 安全防护（按域名）
  var SEC_LEVELS = [['off', '关闭'], ['essentially_off', '基本关闭'], ['low', '低'], ['medium', '中（默认）'], ['high', '高']];
  var SEC_TTL = [[300, '5 分钟'], [900, '15 分钟'], [1800, '30 分钟'], [3600, '1 小时'], [7200, '2 小时'], [28800, '8 小时'], [86400, '1 天'], [604800, '1 周']];
  var WAF_ACTS = [['managed_challenge', '托管质询（推荐）'], ['block', '直接阻止'], ['js_challenge', 'JS 质询'], ['challenge', '交互式质询'], ['skip', '跳过（放行）']];
  var WAF_ACT_PILL = { block: ['阻止', 'bad'], managed_challenge: ['质询', 'warn'], js_challenge: ['JS质询', 'warn'], challenge: ['验证', 'warn'], skip: ['放行', 'ok'], log: ['记录', ''] };
  var WAF_TPL = [['country_block', '屏蔽国家 / 地区'], ['country_allow', '只允许这些国家 / 地区'], ['ip', '屏蔽 IP / 网段'], ['path', '保护路径（如后台）'], ['ua', '屏蔽 User-Agent'], ['asn', '屏蔽 ASN（机房网络）'], ['expr', '自定义表达式']];

  function pageDnsSec(page, zid) {
    var known = (dnsZones || []).filter(function (z) { return z.id === zid; })[0];
    var heading = top(known ? known.name : '安全防护', true);
    page.appendChild(heading);
    page.appendChild(zoneSeg(zid, 'sec'));
    var holder = el('div'); page.appendChild(holder); holder.appendChild(skeleton(3));
    var myEpoch = epoch, d = null;

    function load() {
      return api('cf_sec', { zone: zid }).then(function (r) { if (epoch !== myEpoch) return; d = r; heading.querySelector('h1').textContent = r.zone.name; draw(); })
        .catch(function (e) { if (epoch === myEpoch) clear(holder).appendChild(failBox(e.message, function () { go('dns', 'zs:' + zid, true); })); });
    }
    function set(key, value, msg) {
      return api('cf_sec_edit', { op: 'setting', zone: zid, key: key, value: value }).then(function (r) { d.settings = r.settings; toast(msg); draw(); });
    }
    function draw() {
      clear(holder);
      holder.appendChild(guardCard());
      holder.appendChild(eventsCard());
      holder.appendChild(wafCard());
      holder.appendChild(rlCard());
      holder.appendChild(httpsCard());
      holder.appendChild(el('div', { class: 'hint', text: '改动立刻生效，并记录在服务器的 cf_audit.jsonl 里。规则从上到下依次匹配。' }));
    }
    // 确认框包成 Promise：点确定并执行成功 → true，点取消 → false
    function confirmP(title, text, action) {
      return new Promise(function (res) {
        var done = false;
        confirmBox(title, text, function () { return action().then(function () { done = true; res(true); }); });
        var t = setInterval(function () { if (!document.querySelector('.sheet-bg')) { clearInterval(t); if (!done) res(false); } }, 300);
      });
    }
    function selRow(label, opts, value, onPick, hint, topLine) {
      var sel = el('select', { class: 'input sel-sm' });
      opts.forEach(function (o) { sel.appendChild(el('option', { value: String(o[0]), text: o[1] })); });
      sel.value = String(value);
      sel.disabled = !d.perm.settings;
      sel.addEventListener('change', function () {
        var v = sel.value, txt = sel.options[sel.selectedIndex].text; sel.disabled = true;
        Promise.resolve().then(function () { return onPick(v, txt); }).then(function (okk) { if (okk === false) draw(); }, function (e) { toast(e.message, true); draw(); });
      });
      var f = document.createDocumentFragment();
      f.appendChild(el('div', { class: 'kv kvs', style: topLine ? 'border-top:1px dashed var(--line);margin-top:4px' : null }, el('span', { text: label }), el('span', null, sel)));
      if (hint) f.appendChild(el('div', { class: 'hint', style: 'margin-top:2px', text: hint }));
      return f;
    }

    // ---- 攻击防护：5 秒盾 / 安全级别 / 浏览器检查 / Bot 攻击模式 / 质询有效期
    function guardCard() {
      var s = d.settings, uam = s.security_level === 'under_attack';
      var card = el('div', { class: 'card' + (uam ? ' uam-on' : '') }, el('h3', { text: '攻击防护' }));
      if (!d.perm.settings) { card.appendChild(permHint('Token 缺少「区域 → 区域设置 → 编辑」权限，这部分只能查看。')); }
      card.appendChild(swRow('我正遭受攻击（5 秒盾）', uam ? '已开启：所有访客先过 Cloudflare 检查页。攻击停了记得关掉。' : '被 CC 时打开，所有访客先过检查页。平时别开，会影响 API、订阅链接和搜索引擎。', uam, function (on) {
        if (!on) return set('security_level', s.prev_level || 'medium', '已关闭 5 秒盾').then(function () { return true; });
        function goUam(force) { return api('cf_sec_edit', { op: 'setting', zone: zid, key: 'security_level', value: 'under_attack', force: force ? 1 : undefined }); }
        return confirmP('开启 5 秒盾？', '所有访客（包括你的 API、面板订阅链接、机器人回调）都要先过浏览器检查，非浏览器的请求会被拦。确定只在被攻击时开启。' +
          '操控台不受影响（会自动过一次验证）；服务器探针会按登记的服务器 IP 放行，照常上报。', function () {
          return goUam(false).then(function (x) { d.settings = x.settings; toast('已开启 5 秒盾'); draw(); }, function (e) {
            if ((e.message || '').indexOf('UAM_KEEP_FAIL:') !== 0) throw e;
            // 探针放行规则建不了（通常是 Token 没有「配置规则」权限）：先不开，说清楚后果，让用户决定
            setTimeout(function () {
              confirmBox('探针放行规则建不了', e.message.slice(14) + ' 不放行就开 5 秒盾，开着期间所有服务器探针都会显示离线（操控台本身不受影响）。仍然要开吗？', function () {
                return goUam(true).then(function (x) { d.settings = x.settings; toast('已开启 5 秒盾（探针没有放行）'); draw(); });
              });
            }, 350);
            draw();      // 这次没开成：开关回到关闭
          });
        });
      }, 'uam'));
      var lv = el('select', { class: 'input sel-sm' });
      SEC_LEVELS.forEach(function (o) { lv.appendChild(el('option', { value: o[0], text: o[1] })); });
      lv.value = uam ? (s.prev_level || 'medium') : s.security_level;
      lv.disabled = uam || !d.perm.settings;
      lv.addEventListener('change', function () { var v = lv.value; lv.disabled = true; set('security_level', v, '安全级别已改为「' + lv.options[lv.selectedIndex].text + '」').catch(function (e) { toast(e.message, true); draw(); }); });
      card.appendChild(el('div', { class: 'kv kvs' }, el('span', { text: '安全级别' }), el('span', null, lv)));
      card.appendChild(el('div', { class: 'hint', style: 'margin-top:2px', text: uam ? '5 秒盾开着时安全级别不能单独改。' : '级别越高，可疑访客越容易被要求验证。' }));
      card.appendChild(swRow('浏览器完整性检查', '拦掉请求头明显是伪造或缺失的访问（常见于扫描器、垃圾爬虫）。', s.browser_check, function (on) { return set('browser_check', on, on ? '已开启浏览器完整性检查' : '已关闭浏览器完整性检查'); }));
      if (d.perm.bot) card.appendChild(swRow('Bot 攻击模式', '自动质询已知的恶意机器人。注意：开启后 WAF「放行」规则对它无效，可能误拦你自己的脚本、探针。', s.bot_fight, function (on) { return set('bot_fight', on, on ? '已开启 Bot 攻击模式' : '已关闭 Bot 攻击模式'); }));
      else card.appendChild(permHint('Bot 攻击模式需要 Token 有「区域 → 自动程序管理 → 编辑」权限。'));
      if (d.perm.bot) card.appendChild(swRow('拦截 AI 爬虫', '阻止 GPTBot、ClaudeBot、Bytespider 等 AI 公司的爬虫抓取网站内容。搜索引擎爬虫不受影响。', s.ai_bots, function (on) { return set('ai_bots', on, on ? '已开启 AI 爬虫拦截' : '已关闭 AI 爬虫拦截'); }));
      var ttl = el('select', { class: 'input sel-sm' });
      SEC_TTL.forEach(function (o) { ttl.appendChild(el('option', { value: String(o[0]), text: o[1] })); });
      ttl.value = String(s.challenge_ttl);
      ttl.disabled = !d.perm.settings;
      ttl.addEventListener('change', function () { var v = Number(ttl.value); ttl.disabled = true; set('challenge_ttl', v, '质询有效期已改为 ' + ttl.options[ttl.selectedIndex].text).catch(function (e) { toast(e.message, true); draw(); }); });
      card.appendChild(el('div', { class: 'kv kvs', style: 'border-top:1px dashed var(--line);margin-top:4px' }, el('span', { text: '验证有效期' }), el('span', null, ttl)));
      card.appendChild(el('div', { class: 'hint', style: 'margin-top:2px', text: '访客通过一次验证后，多久内不用再验证。' }));
      return card;
    }

    // ---- HTTPS / SSL
    var SSL_OPTS = [['off', '关闭（不加密）'], ['flexible', '灵活'], ['full', '完全'], ['strict', '完全（严格）']];
    var SSL_WARN = {
      off: '访客将无法用 https:// 打开网站，所有流量明文传输。一般不要选。',
      flexible: '访客到 Cloudflare 加密，Cloudflare 到你服务器走 http。服务器本身会强制跳转 https 的话，会出现「重定向次数过多」打不开。',
      full: 'Cloudflare 到你服务器也走 https，但不检查证书是否有效（自签证书也行）。服务器没开 443 会打不开。',
      strict: 'Cloudflare 到你服务器走 https，并要求服务器证书有效（正规证书或 Cloudflare 源服务器证书）。证书过期或不匹配会出现 526 错误。'
    };
    function httpsCard() {
      var s = d.settings;
      var card = el('div', { class: 'card' }, el('h3', { text: 'HTTPS / SSL' }));
      if (!d.perm.settings) { card.appendChild(permHint('Token 缺少「区域 → 区域设置 → 编辑」权限。')); return card; }
      card.appendChild(selRow('SSL 模式', SSL_OPTS, s.ssl || 'full', function (v, txt) {
        return confirmP('改为「' + txt + '」？', SSL_WARN[v] + ' 改错了网站可能打不开，可以随时改回。', function () { return set('ssl', v, 'SSL 模式已改为「' + txt + '」'); });
      }, '用 wsm 申请了证书的网站选「完全（严格）」最安全。'));
      card.appendChild(swRow('始终使用 HTTPS', '访客打开 http:// 时自动跳到 https://。', s.always_use_https, function (on) { return set('always_use_https', on, on ? '已开启始终使用 HTTPS' : '已关闭始终使用 HTTPS'); }));
      card.appendChild(swRow('自动 HTTPS 重写', '把网页里写死的 http:// 图片、脚本链接自动改成 https://，避免浏览器报「不安全」。', s.automatic_https_rewrites, function (on) { return set('automatic_https_rewrites', on, on ? '已开启自动 HTTPS 重写' : '已关闭自动 HTTPS 重写'); }));
      card.appendChild(selRow('最低 TLS', [['1.0', 'TLS 1.0'], ['1.1', 'TLS 1.1'], ['1.2', 'TLS 1.2（推荐）'], ['1.3', 'TLS 1.3']], s.min_tls_version || '1.0', function (v, txt) {
        if (v !== '1.3') return set('min_tls_version', v, '最低 TLS 已改为 ' + txt);
        return confirmP('最低 TLS 改为 1.3？', '比较旧的手机、系统和一些代理客户端只支持 TLS 1.2，会连不上网站。', function () { return set('min_tls_version', v, '最低 TLS 已改为 ' + txt); });
      }, '低于这个版本的连接会被拒绝。1.2 兼顾安全和兼容。', true));
      card.appendChild(swRow('TLS 1.3', '更快更安全的加密协议，建议开着。', s.tls_1_3, function (on) { return set('tls_1_3', on, on ? '已开启 TLS 1.3' : '已关闭 TLS 1.3'); }));
      return card;
    }

    // ---- 安全事件：最近被拦截 / 质询的请求
    var EV_ACT = { block: ['阻止', 'bad'], managed_challenge: ['托管质询', 'warn'], challenge: ['交互式质询', 'warn'], jschallenge: ['JS 质询', 'warn'],
      js_challenge: ['JS 质询', 'warn'], log: ['记录', ''], skip: ['放行', 'ok'], allow: ['放行', 'ok'], bypass: ['放行', 'ok'],
      connection_close: ['断开', 'bad'], force_connection_close: ['断开', 'bad'], managed_challenge_skipped: ['自动通过', 'ok'],
      managed_challenge_non_interactive_solved: ['自动通过', 'ok'], managed_challenge_interactive_solved: ['通过验证', 'ok'],
      challenge_solved: ['通过验证', 'ok'], jschallenge_solved: ['通过验证', 'ok'], challenge_failed: ['验证失败', 'bad'], jschallenge_failed: ['验证失败', 'bad'] };
    var EV_SRC = { firewallcustom: 'WAF 规则', ratelimit: '速率限制', securitylevel: '安全级别', bic: '浏览器检查', botfight: 'Bot 攻击模式',
      l7ddos: 'DDoS 防护', firewallmanaged: '托管规则', hot: '防盗链', uablock: 'UA 封禁', iprules: 'IP 访问规则', ipaccessrules: 'IP 访问规则',
      firewallrules: '旧版防火墙规则', waf: '旧版 WAF', validation: '请求校验', zonelockdown: '区域锁定', botmanagement: '机器人管理' };
    function evAct(a) { if (EV_ACT[a]) return EV_ACT[a]; if (/_solved$/.test(a)) return ['通过验证', 'ok']; if (/_failed$/.test(a)) return ['验证失败', 'bad']; return [a || '未知', '']; }
    function evSrc(x) { return EV_SRC[String(x || '').toLowerCase()] || x || ''; }
    function evAgo(t) {
      var s = Math.max(0, Math.floor(Date.now() / 1000 - t));
      return s < 60 ? '刚刚' : s < 3600 ? Math.floor(s / 60) + ' 分钟前' : s < 86400 ? Math.floor(s / 3600) + ' 小时前' : timeText(t);
    }
    var ev = null, evErr = null, evBusy = false, evOpen = {}, evPage = 0, EV_PAGE = 10;
    var evShow = false; try { evShow = localStorage.getItem('mini_ev_open') === '1'; } catch (e) {}     // 记录列表默认收起，展开状态记在本机
    function loadEvents() {
      if (evBusy) return Promise.resolve(); evBusy = true;
      return api('cf_events', { zone: zid, limit: 100 }).then(function (r) { ev = r; evErr = null; evPage = 0; }, function (e) { evErr = e.message; })
        .then(function () { evBusy = false; if (epoch === myEpoch && d) draw(); });
    }
    function eventsCard() {
      var card = el('div', { class: 'card' });
      var rb = el('button', { class: 'btn sm', text: '↻', 'aria-label': '刷新', onclick: function () { rb.setAttribute('disabled', ''); loadEvents(); } });
      if (evBusy) rb.setAttribute('disabled', '');
      var win = ev ? (ev.window >= 86400 ? '24 小时' : ev.window >= 3600 ? Math.round(ev.window / 3600) + ' 小时' : '') : '';
      card.appendChild(el('div', { class: 'row', style: 'margin-bottom:8px' }, el('h3', { style: 'margin:0;flex:1', text: '安全事件' + (win ? '（最近 ' + win + '）' : '') }), rb));
      if (!ev && !evErr) { card.appendChild(skeleton(2)); if (!evBusy) loadEvents(); return card; }
      if (evErr) { card.appendChild(el('div', { class: 'hint', text: evErr })); return card; }
      if (ev.perm === false) { card.appendChild(permHint('看拦截记录需要给 Token 加「区域 → 分析 → 读取」权限。')); return card; }
      var keys = Object.keys(ev.counts || {}).sort(function (a, b) { return ev.counts[b] - ev.counts[a]; });
      if (keys.length) {
        var sum = el('div', { class: 'evsum' });
        keys.forEach(function (k) { var a = evAct(k); sum.appendChild(el('span', { class: 'pill ' + a[1], text: a[0] + ' ' + ev.counts[k] })); });
        card.appendChild(sum);
      }
      if (!ev.events.length) { card.appendChild(el('div', { class: 'empty', style: 'padding:16px 8px' }, el('span', { class: 'big wi' }, icon('shield')), '这段时间没有被拦截或质询的请求')); return card; }
      // 下拉：点一下展开 / 收起记录列表；展开后每页 10 条，可以翻页
      var total = ev.events.length, pages = Math.max(1, Math.ceil(total / EV_PAGE));
      if (evPage >= pages) evPage = pages - 1;
      card.appendChild(el('button', { class: 'ev-tg' + (evShow ? ' on' : ''), type: 'button', onclick: function () {
        evShow = !evShow; try { localStorage.setItem('mini_ev_open', evShow ? '1' : '0'); } catch (e) {} hap(); draw();
      } }, el('span', { text: evShow ? '收起记录' : '查看记录' }), el('span', { class: 'cnt', text: total + ' 条' }), el('span', { class: 'sp' }), el('span', { class: 'arr', text: '⌄' })));
      if (!evShow) return card;
      var list = el('div', { class: 'evlist' });
      ev.events.slice(evPage * EV_PAGE, (evPage + 1) * EV_PAGE).forEach(function (e, j) {
        var i = evPage * EV_PAGE + j;
        var a = evAct(e.action), key = e.ray || String(i);
        var item = el('div', { class: 'note nitem dr' + (evOpen[key] ? ' open' : '') });
        var main = el('button', { class: 'dr-main', type: 'button' },
          el('span', { class: 'pill dt ' + a[1], text: a[0] }),
          el('span', { class: 'dr-t' }, el('b', { class: 'mono', style: 'font-size:13px', text: e.path || '/' }),
            el('small', { text: [e.ip, e.country, evAgo(e.t), e.rule || evSrc(e.source)].filter(Boolean).join(' · ') })));
        main.addEventListener('click', function () { item.classList.toggle('open'); if (item.classList.contains('open')) evOpen[key] = 1; else delete evOpen[key]; hap(); });
        item.appendChild(el('div', { class: 'dr-h' }, main));
        function kv(k, v, mono) { return v ? el('div', { class: 'kv' }, el('span', { text: k }), el('span', { class: mono ? 'mono' : null, style: mono ? 'font-size:12px' : null, text: v })) : null; }
        item.appendChild(el('div', { class: 'nb' }, el('div', { class: 'nbi' }, el('div', { class: 'nbc' },
          kv('时间', timeText(e.t)), kv('IP', e.ip, true), kv('国家', e.country), kv('ASN', e.asn ? 'AS' + e.asn + (e.asn_name ? ' ' + e.asn_name : '') : ''),
          kv('请求', [e.method, e.host].filter(Boolean).join(' ')), kv('路径', e.path, true), kv('来源', evSrc(e.source)), kv('规则', e.rule),
          kv('UA', e.ua, true), kv('Ray ID', e.ray, true),
          el('div', { class: 'row', style: 'margin-top:10px' },
            el('button', { class: 'btn sm', style: 'flex:1', text: '复制 IP', onclick: function () { copy(e.ip); } }),
            d.waf.perm !== false ? el('button', { class: 'btn sm danger', style: 'flex:1', text: '封这个 IP', onclick: function () {
              confirmBox('封禁 ' + e.ip + '？', '会加进 WAF 规则「手动封禁 IP」（没有就新建一条），这个 IP 之后访问一律阻止。要解封就去编辑那条规则。', function () {
                return api('cf_sec_edit', { op: 'waf_ban_ip', zone: zid, ip: e.ip }).then(function (x) { d.waf = x.waf; toast('已封禁 ' + e.ip); draw(); });
              });
            } }) : null)))));
        list.appendChild(item);
      });
      card.appendChild(list);
      if (pages > 1) {
        var prev = el('button', { class: 'btn sm', text: '‹ 上一页', onclick: function () { evPage--; hap(); draw(); } });
        var next = el('button', { class: 'btn sm', text: '下一页 ›', onclick: function () { evPage++; hap(); draw(); } });
        if (evPage <= 0) prev.setAttribute('disabled', '');
        if (evPage >= pages - 1) next.setAttribute('disabled', '');
        card.appendChild(el('div', { class: 'pager' }, prev, el('span', { class: 'pg', text: (evPage + 1) + ' / ' + pages }), next));
      }
      card.appendChild(el('div', { class: 'hint', text: '共 ' + total + ' 条' + (total >= 100 ? '（最多显示最近 100 条）' : '') + '。点一条看详情；误拦了正常访客就去调整对应的规则或设置。' }));
      return card;
    }

    // ---- WAF 自定义规则
    function wafCard() {
      var w = d.waf;
      var card = el('div', { class: 'card' });
      card.appendChild(el('div', { class: 'row', style: 'margin-bottom:8px' }, el('h3', { style: 'margin:0;flex:1', text: 'WAF 自定义规则' }),
        el('span', { class: 'pill', text: w.rules.length + ' / ' + w.limit }),
        w.perm !== false ? el('button', { class: 'btn sm primary', text: '＋ 添加', onclick: function () { if (w.rules.length >= w.limit) toast('免费版最多 ' + w.limit + ' 条，先删掉一条再加', true); else wafSheet(null); } }) : null));
      if (w.perm === false) { card.appendChild(permHint('Token 缺少「区域 → 区域 WAF → 编辑」权限。')); return card; }
      if (!w.rules.length) card.appendChild(el('div', { class: 'empty', style: 'padding:16px 8px' }, el('span', { class: 'big wi' }, icon('shield')), '还没有规则。常用：只允许中国访问、保护后台路径。'));
      w.rules.forEach(function (r, i) { card.appendChild(wafRow(r, i)); });
      return card;
    }
    function wafRow(r, i) {
      var ap = WAF_ACT_PILL[r.action] || [r.action, ''];
      var item = el('div', { class: 'note nitem dr' + (r.enabled ? '' : ' off') });
      var main = el('button', { class: 'dr-main', type: 'button' },
        el('span', { class: 'pill dt ' + ap[1], text: ap[0] }),
        el('span', { class: 'dr-t' }, el('b', { text: r.description || '未命名规则' }), el('small', { text: r.summary || r.expression })));
      main.addEventListener('click', function () { item.classList.toggle('open'); hap(); });
      var tg2 = el('input', { type: 'checkbox', class: 'tgl', 'aria-label': '启用' }); tg2.checked = !!r.enabled;
      tg2.addEventListener('change', function () {
        var on = tg2.checked; tg2.disabled = true;
        api('cf_sec_edit', { op: 'waf_toggle', zone: zid, id: r.id, enabled: on }).then(function (x) { d.waf = x.waf; toast(on ? '已启用' : '已停用'); draw(); })
          .catch(function (e) { tg2.checked = !on; tg2.disabled = false; toast(e.message, true); });
      });
      item.appendChild(el('div', { class: 'dr-h' }, main, el('label', { class: 'tglw' }, tg2)));
      item.appendChild(el('div', { class: 'nb' }, el('div', { class: 'nbi' }, el('div', { class: 'nbc' },
        el('div', { class: 'kv' }, el('span', { text: '动作' }), el('span', { text: (WAF_ACTS.filter(function (a) { return a[0] === r.action; })[0] || [0, r.action])[1] })),
        el('div', { class: 'kv' }, el('span', { text: '状态' }), el('span', { text: r.enabled ? '启用中' : '已停用' })),
        el('div', { class: 'kv' }, el('span', { text: '表达式' }), el('span', { class: 'mono', style: 'font-size:12px', text: r.expression })),
        el('div', { class: 'row', style: 'margin-top:10px' },
          el('button', { class: 'btn sm', style: 'flex:1', text: '↑ 上移', disabled: i === 0 ? true : null, onclick: function () { move(r, -1); } }),
          el('button', { class: 'btn sm', style: 'flex:1', text: '编辑', onclick: function () { wafSheet(r); } }),
          el('button', { class: 'btn sm danger', style: 'flex:1', text: '删除', onclick: function () {
            confirmBox('删除 WAF 规则', '「' + (r.description || r.expression) + '」删除后立即生效。', function () {
              return api('cf_sec_edit', { op: 'waf_del', zone: zid, id: r.id }).then(function (x) { d.waf = x.waf; toast('已删除'); draw(); });
            });
          } }))))));
      return item;
    }
    function move(r, dir) {
      run(null, function () { return api('cf_sec_edit', { op: 'waf_move', zone: zid, id: r.id, dir: dir }).then(function (x) { d.waf = x.waf; draw(); }); });
    }
    function wafSheet(r) {
      var t = r && r.tpl ? r.tpl : { type: r ? 'expr' : 'country_allow', value: r ? r.expression : 'CN' };
      var val = function (k) { return t.type === k ? t.value : ''; };
      sheet(r ? '编辑 WAF 规则' : '添加 WAF 规则', [
        { key: 'name', label: '规则名称', ph: '例如 只允许中国访问', value: r ? r.description : '' },
        { key: 'type', label: '规则类型', options: WAF_TPL, value: t.type },
        { key: 'countries', label: '国家 / 地区代码（空格或逗号分隔）', ph: 'CN HK TW', only: ['country_block', 'country_allow'], value: val('country_block') || val('country_allow') || 'CN' },
        { key: 'ips', label: 'IP 或网段，一行一个', ph: '1.2.3.4\n5.6.7.0/24', multi: true, only: ['ip'], value: val('ip') },
        { key: 'path', label: '路径开头（一行一个）', ph: '/admin\n/wp-login.php', multi: true, only: ['path'], value: val('path') },
        { key: 'ua', label: 'User-Agent 包含（一行一个，不分大小写）', ph: 'python-requests\ncurl', multi: true, only: ['ua'], value: val('ua') },
        { key: 'asn', label: 'ASN 编号（空格分隔）', ph: '14061 16509 45102', only: ['asn'], value: val('asn') },
        { key: 'expr', label: 'Cloudflare 规则表达式', ph: '(ip.geoip.country ne "CN")', multi: true, only: ['expr'], value: val('expr') },
        { key: 'action', label: '匹配后', options: WAF_ACTS, value: r ? r.action : 'managed_challenge' }
      ], '保存', function (v) {
        return api('cf_sec_edit', { op: r ? 'waf_edit' : 'waf_add', zone: zid, id: r ? r.id : undefined, name: v.name, type: v.type, countries: v.countries, ips: v.ips, path: v.path, ua: v.ua, asn: v.asn, expr: v.expr, action: v.action })
          .then(function (x) { d.waf = x.waf; toast(r ? '已保存' : '已添加'); draw(); });
      }, el('div', { class: 'hint', text: '「只允许」类型会对其它所有地区执行所选动作。保护后台建议用「托管质询」，自己访问时点一下就能过。' }));
    }

    // ---- 速率限制（防 CC）
    function rlCard() {
      var l = d.rl;
      var card = el('div', { class: 'card' });
      card.appendChild(el('div', { class: 'row', style: 'margin-bottom:8px' }, el('h3', { style: 'margin:0;flex:1', text: '速率限制（防 CC）' }),
        el('span', { class: 'pill', text: l.rules.length + ' / ' + l.limit }),
        l.perm !== false ? el('button', { class: 'btn sm primary', text: '＋ 添加', onclick: function () { if (l.rules.length >= l.limit) toast('免费版只能有 ' + l.limit + ' 条速率限制', true); else rlSheet(null); } }) : null));
      if (l.perm === false) { card.appendChild(permHint('Token 缺少「区域 → 区域 WAF → 编辑」权限。')); return card; }
      if (!l.rules.length) card.appendChild(el('div', { class: 'empty', style: 'padding:16px 8px' }, el('span', { class: 'big wi' }, icon('pulse')), '没有速率限制。同一个 IP 短时间请求太多次时自动拦截。'));
      l.rules.forEach(function (r) {
        var item = el('div', { class: 'note nitem dr' + (r.enabled ? '' : ' off') });
        var main = el('button', { class: 'dr-main', type: 'button' },
          el('span', { class: 'pill dt ' + (r.action === 'block' ? 'bad' : 'warn'), text: r.action === 'block' ? '阻止' : '质询' }),
          el('span', { class: 'dr-t' }, el('b', { text: r.description || '速率限制' }), el('small', { text: (r.path || '全部路径') + ' · 每 ' + r.period + ' 秒超过 ' + r.requests + ' 次' + (r.action === 'block' ? ' · 封 ' + r.timeout + ' 秒' : ' · 质询') })));
        main.addEventListener('click', function () { item.classList.toggle('open'); hap(); });
        var tg2 = el('input', { type: 'checkbox', class: 'tgl' }); tg2.checked = !!r.enabled;
        tg2.addEventListener('change', function () {
          var on = tg2.checked; tg2.disabled = true;
          api('cf_sec_edit', { op: 'rl_toggle', zone: zid, id: r.id, enabled: on }).then(function (x) { d.rl = x.rl; toast(on ? '已启用' : '已停用'); draw(); })
            .catch(function (e) { tg2.checked = !on; tg2.disabled = false; toast(e.message, true); });
        });
        item.appendChild(el('div', { class: 'dr-h' }, main, el('label', { class: 'tglw' }, tg2)));
        item.appendChild(el('div', { class: 'nb' }, el('div', { class: 'nbi' }, el('div', { class: 'nbc' },
          el('div', { class: 'kv' }, el('span', { text: '计数方式' }), el('span', { text: '按访客 IP' })),
          el('div', { class: 'kv' }, el('span', { text: '表达式' }), el('span', { class: 'mono', style: 'font-size:12px', text: r.expression })),
          el('div', { class: 'row', style: 'margin-top:10px' },
            el('button', { class: 'btn sm', style: 'flex:1', text: '编辑', onclick: function () { rlSheet(r); } }),
            el('button', { class: 'btn sm danger', style: 'flex:1', text: '删除', onclick: function () {
              confirmBox('删除速率限制', '删除后立即生效。', function () { return api('cf_sec_edit', { op: 'rl_del', zone: zid, id: r.id }).then(function (x) { d.rl = x.rl; toast('已删除'); draw(); }); });
            } }))))));
        card.appendChild(item);
      });
      card.appendChild(el('div', { class: 'hint', text: '免费版：计数周期和封禁时长都固定 10 秒。' }));
      return card;
    }
    function rlSheet(r) {
      var periods = d.rl.periods || [10], timeouts = d.rl.timeouts || [10];
      sheet(r ? '编辑速率限制' : '添加速率限制', [
        { key: 'name', label: '规则名称', ph: '例如 防 CC', value: r ? r.description : '防 CC' },
        { key: 'path', label: '路径开头（留空 = 全站）', ph: '/api/', value: r ? r.path : '' },
        { key: 'requests', label: '同一 IP 在计数周期内最多请求几次', ph: '50', value: r ? String(r.requests) : '50' },
        { key: 'period', label: '计数周期', options: periods.map(function (x) { return [x, x + ' 秒']; }), value: r ? r.period : periods[0] },
        { key: 'action', label: '超过后', options: [['block', '直接阻止'], ['managed_challenge', '托管质询']], value: r ? r.action : 'block' },
        { key: 'timeout', label: '封禁时长（只对「直接阻止」有效）', options: timeouts.map(function (x) { return [x, x < 60 ? x + ' 秒' : x / 60 + ' 分钟']; }), value: r ? r.timeout : timeouts[0] }
      ], '保存', function (v) {
        var n = parseInt(v.requests, 10);
        if (!(n >= 1 && n <= 100000)) throw new Error('请求次数请填 1 ~ 100000');
        return api('cf_sec_edit', { op: r ? 'rl_edit' : 'rl_add', zone: zid, id: r ? r.id : undefined, name: v.name, path: v.path, requests: n, period: Number(v.period), action: v.action, timeout: Number(v.timeout) })
          .then(function (x) { d.rl = x.rl; toast(r ? '已保存' : '已添加'); draw(); });
      }, el('div', { class: 'hint', text: '普通网站全站建议 10 秒 50~100 次；登录、注册接口可以更严，例如 10 秒 5 次。' }));
    }
    load();
  }

  // ---------------------------------------------------------------- R2 存储（按账户）
  var R2_LOC = [['', '自动（就近）'], ['apac', '亚太'], ['wnam', '北美西部'], ['enam', '北美东部'], ['weur', '西欧'], ['eeur', '东欧']];
  var r2Data = null;
  try { r2Data = JSON.parse(localStorage.getItem('mini_r2') || 'null'); } catch (e) {}     // 上次的结果：打开页面先显示，后台再刷新
  function r2Save(x) { r2Data = x; try { localStorage.setItem('mini_r2', JSON.stringify(x)); } catch (e) {} }
  function r2LocText(l) { var x = R2_LOC.filter(function (o) { return o[0] === String(l || '').toLowerCase(); })[0]; return x ? x[1] : (l || '自动'); }
  function r2Usage(b) { var mk = b.partial ? '≥' : ''; return b.objects == null ? '用量未知' : mk + b.objects + ' 个文件 · ' + mk + fmtBytes(b.size); }
  function r2Name(k) { var s = k.replace(/\/$/, ''); return s.slice(s.lastIndexOf('/') + 1) + (k.slice(-1) === '/' ? '/' : ''); }

  function pageR2(page) {
    page.appendChild(top('R2 存储', true));
    page.appendChild(rootSeg('r2'));
    var holder = el('div'); page.appendChild(holder);
    var myEpoch = epoch, fresh = false;
    if (r2Data) draw(); else holder.appendChild(skeleton(3));
    function draw() {
      var d = r2Data; clear(holder);
      if (!fresh) holder.appendChild(el('div', { class: 'hint', style: 'display:flex;align-items:center;gap:8px;margin:-4px 2px 10px' }, el('span', { class: 'spin' }), '显示的是上次的数据，正在刷新…'));
      var objs = 0, size = 0, known = d.buckets.some(function (b) { return b.objects != null; });
      d.buckets.forEach(function (b) { objs += b.objects || 0; size += b.size || 0; });
      holder.appendChild(el('div', { class: 'grid g3', style: 'margin-bottom:12px' },
        el('div', { class: 'card stat' }, el('b', { text: d.buckets.length }), el('span', { text: '存储桶' })),
        el('div', { class: 'card stat' }, el('b', { text: known ? objs : '—' }), el('span', { text: '文件' })),
        el('div', { class: 'card stat' }, el('b', { text: known ? fmtBytes(size).replace(' ', '') : '—' }), el('span', { text: '已用 / 免费 10GB' }))));
      holder.appendChild(el('button', { class: 'card dz', onclick: newBucket },
        el('span', { class: 'dz-ic swap', text: '＋' }),
        el('span', { class: 'dz-t' }, el('b', { text: '新建存储桶' }), el('small', { text: '名称只能用小写字母、数字和 -' })),
        goArrow()));
      if (!d.buckets.length) holder.appendChild(el('div', { class: 'card empty' }, el('span', { class: 'big wi' }, icon('bucket')), '这个账户下还没有存储桶'));
      d.buckets.forEach(function (b) {
        holder.appendChild(el('button', { class: 'card dz', onclick: function () { go('dns', 'r2b:' + b.name + ':'); } },
          el('span', { class: 'dz-ic' }, icon('bucket')),
          el('span', { class: 'dz-t' }, el('b', { text: b.name }), el('small', { text: r2Usage(b) + ' · ' + r2LocText(b.location) })),
          el('span', { class: 'pill ' + (b.public || b.domains ? 'ok' : ''), text: b.public || b.domains ? '公开' : '私有' }),
          goArrow()));
      });
      if (!d.s3) holder.appendChild(el('div', { class: 'card' }, permHint('还没配置 R2 的 Access Key，只能管理存储桶。要浏览、上传文件：Cloudflare → R2 → 管理 API 令牌，创建一个「对象读和写」的令牌，把 Access Key ID 和 Secret 写进机器人目录的 .env（R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY），重启机器人。')));
      holder.appendChild(el('div', { class: 'hint', text: '账户：' + d.account.name + '。' + (d.live ? '用量是遍历桶里的文件实时算出来的（缓存 1 分钟）' + (d.buckets.some(function (x) { return x.partial; }) ? '，文件太多的桶只统计了前 5 万个。' : '。') : d.usage ? '用量来自 Cloudflare 的统计，会晚几分钟到一两个小时，不是实时的；配好 R2 的 Access Key 后会改为实时。' : '看不到用量：Token 需要加「账户 → 账户分析 → 读取」权限。') }));
    }
    function newBucket() {
      sheet('新建存储桶', [
        { key: 'name', label: '名称', ph: '例如 my-files' },
        { key: 'loc', label: '位置', options: R2_LOC, value: '' }
      ], '创建', function (v) {
        var n = v.name.trim();
        if (!/^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/.test(n)) throw new Error('名称 3~63 位，只能小写字母、数字和 -，且不能以 - 开头结尾');
        return api('r2_edit', { op: 'bucket_add', name: n, location: v.loc }).then(function (x) { r2Save(x); fresh = true; draw(); toast('已创建 ' + n); });
      }, el('div', { class: 'hint', text: '新桶默认私有，创建后可以在桶里打开公开访问或绑定自己的域名。' }));
    }
    api('r2').then(function (x) { r2Save(x); if (epoch !== myEpoch) return; fresh = true; draw(); })
      .catch(function (e) {
        if (epoch !== myEpoch) return;
        if (!r2Data) return clear(holder).appendChild(failBox(e.message, function () { go('dns', 'r2', true); }));
        fresh = true; draw(); toast('刷新失败，显示的是上次的数据：' + e.message, true);
      });
  }

  // sub：r2b:<桶名>:<目录前缀>（文件）/ r2s:<桶名>（设置）
  function pageR2Bucket(page, name, prefix, setMode) {
    var A_LIST = 'r2_list', A_EDIT = 'r2_edit';
    var heading = top(name, true);
    page.appendChild(heading);
    page.appendChild(dnsSeg([['files', '文件', 'r2b:' + name + ':'], ['set', '设置', 'r2s:' + name]], setMode ? 'set' : 'files'));
    var holder = el('div'); page.appendChild(holder);
    if (setMode) return r2BucketSettings(holder, name);

    // 路径导航：根目录 / a / b
    var crumbs = el('div', { class: 'tabs-x crumbs' });
    crumbs.appendChild(el('button', { class: 'chip' + (prefix ? '' : ' on'), text: '根目录', onclick: function () { if (prefix) go('dns', 'r2b:' + name + ':'); } }));
    var acc = '';
    prefix.split('/').filter(Boolean).forEach(function (p, k, arr) {
      acc += p + '/';
      var to = acc;
      crumbs.appendChild(el('span', { class: 'crumb-sep', text: '/' }));
      crumbs.appendChild(el('button', { class: 'chip' + (k === arr.length - 1 ? ' on' : ''), text: p, onclick: function () { if (to !== prefix) go('dns', 'r2b:' + name + ':' + to); } }));
    });
    var search = el('input', { class: 'input', placeholder: '🔍 在当前目录搜索', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false' });
    var fileIn = el('input', { type: 'file', multiple: true, style: 'display:none' });
    var upBtn = el('button', { class: 'btn primary', text: '↑ 上传', onclick: function () { fileIn.click(); } });
    var refreshBtn = el('button', { class: 'btn', text: '↻', 'aria-label': '刷新', onclick: function () { run(refreshBtn, function () { return list(true); }); } });
    holder.appendChild(el('div', { class: 'row' }, search, refreshBtn, upBtn, fileIn));
    holder.appendChild(el('div', { class: 'row', style: 'margin-top:8px' },
      el('button', { class: 'btn sm', style: 'flex:1', text: '＋ 新建文件夹', onclick: newFolder })));
    holder.appendChild(crumbs);
    var upBox = el('div');
    var listBox = el('div'); listBox.appendChild(skeleton(4));
    var foot = el('div', { class: 'hint' });
    holder.appendChild(upBox); holder.appendChild(listBox); holder.appendChild(foot);
    var data = { folders: [], files: [], next: null, public_url: null }, q = '', myEpoch = epoch;

    function list(reset, cursor) {
      return api(A_LIST, { bucket: name, prefix: prefix, cursor: cursor || undefined }).then(function (x) {
        if (epoch !== myEpoch) return;
        if (reset || !cursor) data = { folders: [], files: [] };
        data.folders = data.folders.concat(x.folders); data.files = data.files.concat(x.files);
        data.next = x.next; data.public_url = x.public_url;
        draw();
      });
    }
    function draw() {
      clear(listBox);
      var ql = q.trim().toLowerCase();
      var fs = data.folders.filter(function (f) { return !ql || f.toLowerCase().indexOf(ql) >= 0; });
      var fl = data.files.filter(function (f) { return !ql || f.key.toLowerCase().indexOf(ql) >= 0; });
      if (!data.folders.length && !data.files.length) listBox.appendChild(el('div', { class: 'card empty' }, el('span', { class: 'big wi' }, icon('folder')), prefix ? '这个文件夹是空的' : '桶里还没有文件，点「上传」放第一个'));
      fs.forEach(function (f) {
        listBox.appendChild(el('div', { class: 'card dz fz', role: 'button', onclick: function () { go('dns', 'r2b:' + name + ':' + f); } },
          el('span', { class: 'dz-ic' }, icon('folder')), el('span', { class: 'dz-t' }, el('b', { text: r2Name(f) })),
          moreBtn('更多', function (e) { e.stopPropagation(); folderMenu(f); })));
      });
      fl.forEach(function (f) { listBox.appendChild(fileRow(f)); });
      if (data.next) listBox.appendChild(el('button', { class: 'btn block', style: 'margin-top:4px', text: '加载更多', onclick: function (e) { run(e.currentTarget, function () { return list(false, data.next); }); } }));
      foot.textContent = data.folders.length + ' 个文件夹 · ' + data.files.length + ' 个文件' + (data.next ? '（还有更多）' : '') + (data.public_url ? ' · 公开地址 ' + data.public_url : ' · 私有桶，下载链接 1 小时内有效');
    }
    function fileRow(f) {
      return el('div', { class: 'card dz fz' },
        el('span', { class: 'dz-ic' }, icon('file')),
        el('span', { class: 'dz-t' }, el('b', { text: r2Name(f.key) }), el('small', { text: fmtBytes(f.size) + ' · ' + timeText(f.modified) })),
        moreBtn('更多', function () { fileMenu(f); }));
    }
    // 点 ⋯ 弹出的详情页（和 SFTP 页一致）：先 close() 再做事，避免弹层叠在一起
    function menuSheet(title, rows, btns) {
      var bg = el('div', { class: 'sheet-bg', onclick: function (e) { if (e.target === bg) bg.remove(); } });
      function close() { bg.remove(); }
      var box = el('div', { class: 'sheet' }, el('h2', { text: title }));
      (rows || []).forEach(function (r) { if (r) box.appendChild(el('div', { class: 'kv' }, el('span', { text: r[0] }), el('span', { class: r[2] ? 'mono' : '', text: r[1] }))); });
      btns.forEach(function (x, i) {
        box.appendChild(el('button', { class: 'btn block' + (x[2] ? ' ' + x[2] : ''), style: 'margin-top:' + (i ? 8 : 12) + 'px', text: x[0], onclick: function (e) { close(); x[1](e); } }));
      });
      box.appendChild(el('div', { class: 'acts' }, el('button', { class: 'btn', text: '取消', onclick: close })));
      bg.appendChild(box); document.body.appendChild(bg);
    }
    function folderMenu(f) {
      menuSheet(r2Name(f).replace(/\/$/, ''), [['路径', f, 1]], [['重命名', function () { renameItem('folder', f); }]]);
    }
    function fileMenu(f) {
      var pub = data.public_url ? data.public_url.replace(/\/$/, '') + '/' + f.key.split('/').map(encodeURIComponent).join('/') : null;
      menuSheet(r2Name(f.key), [['路径', f.key, 1], ['大小', fmtBytes(f.size)], f.type ? ['类型', f.type, 1] : null, ['修改时间', timeText(f.modified)]], [
        [pub ? '复制链接' : '复制临时链接', function () {
          if (pub) return copy(pub);
          api(A_EDIT, { op: 'sign', bucket: name, key: f.key }).then(function (x) { copy(x.url); }, function (e) { toast(e.message, true); });
        }],
        ['下载', function () { sshDl('r2_dl_begin', { bucket: name, key: f.key }, 'r2_dl_chunk', r2Name(f.key)); }],
        ['重命名', function () { renameItem('file', f.key); }],
        ['删除', function () {
          confirmBox('删除文件', f.key + '（' + fmtBytes(f.size) + '）删除后无法恢复。', function () {
            return api(A_EDIT, { op: 'del', bucket: name, key: f.key }).then(function () { data.files = data.files.filter(function (x) { return x.key !== f.key; }); draw(); toast('已删除'); });
          });
        }, 'danger']
      ]);
    }
    function renameItem(kind, key) {
      var isDir = kind === 'folder', cur0 = r2Name(key).replace(/\/$/, '');
      var bg = sheet(isDir ? '重命名文件夹' : '重命名文件', [{ key: 'n', label: '新名称', value: cur0 }], '保存', function (v) {
        var n = String(v.n || '').trim();
        if (!n || n === '.' || n === '..' || /[\\\/#?]/.test(n) || /[\x00-\x1f]/.test(n)) {
          throw new Error('名称不能为空，也不能包含 / \\ # ? 或控制字符');
        }
        if (n === cur0) throw new Error('新名称和原来一样');
        return api(A_EDIT, { op: 'rename', bucket: name, kind: kind, key: key, new_name: n }).then(function (x) {
          toast(isDir ? '已重命名，移动了 ' + (x && x.count || 0) + ' 个对象' : '已重命名');
          return list(true);
        }, function (e) {
          // 文件夹很大时网关会先超时，但服务器还在继续改：刷新一下让你看到真实结果
          if (isDir && /连接不上机器人接口|没有正确响应|网络不稳定/.test(e.message)) {
            list(true).catch(function () {});
            throw new Error('文件夹较大，服务器可能还在处理，已刷新目录，稍后再点刷新看结果');
          }
          throw e;
        });
      });
      // 打开后只选中名字部分（不含扩展名），直接输入就是改名
      setTimeout(function () {
        var inp = bg.querySelector('input.input'); if (!inp) return;
        var dot = isDir ? -1 : cur0.lastIndexOf('.');
        inp.focus();
        try { inp.setSelectionRange(0, dot > 0 ? dot : cur0.length); } catch (e) {}
      }, 200);
    }
    function newFolder() {
      sheet('新建文件夹', [{ key: 'n', label: '文件夹名', ph: '例如 images' }], '创建', function (v) {
        var n = v.n.trim().replace(/^\/+|\/+$/g, '');
        if (!n || /[\\#?]/.test(n)) throw new Error('文件夹名不能为空，也不能有 \\ # ?');
        return api(A_EDIT, { op: 'mkdir', bucket: name, key: prefix + n + '/' }).then(function () { go('dns', 'r2b:' + name + ':' + prefix + n + '/'); });
      }, el('div', { class: 'hint', text: 'R2 没有真正的文件夹，这里会放一个空的占位，往里面传文件就行。' }));
    }
    // 上传是全局任务（见下面 r2Upload）：切到别的页面再回来，进度框会接回来；有任务在跑时不能再发新的
    function hookUp() {
      clear(upBox);
      if (!R2UP) { upBtn.removeAttribute('disabled'); return; }
      upBtn.setAttribute('disabled', '');
      if (R2UP.bucket === name) upBox.appendChild(R2UP.box);
    }
    r2UpPage = { bucket: name, box: upBox, hook: hookUp, reload: function () { if (epoch === myEpoch) list(true).catch(function () {}); } };
    hookUp();
    fileIn.addEventListener('change', function () {
      var files = Array.prototype.slice.call(fileIn.files || []); fileIn.value = '';
      if (!files.length) return;
      if (R2UP) { toast('已经有上传任务在进行，等它传完再传', true); return; }
      r2Upload(name, prefix, files);
    });
    search.addEventListener('input', function () { q = search.value; draw(); });
    list(true).catch(function (e) { if (epoch === myEpoch) clear(listBox).appendChild(failBox(e.message, function () { go('dns', 'r2b:' + name + ':' + prefix, true); })); });
  }
  // ---- R2 上传（全局任务）：不跟着页面走，切换分类 / 页面不会中断，回到桶里能接着看进度
  // 上传本身在手机浏览器里跑：只要 Mini App 没被关掉就会继续；上传期间关闭 Mini App 会先弹确认
  var R2UP = null, r2UpPage = null, r2Bar = null;
  function r2Upload(bucket, prefix, files) {
    var u = R2UP = {
      bucket: bucket, prefix: prefix, files: files, k: 0, ok: 0, status: '准备中',
      total: files.reduce(function (s, f) { return s + f.size; }, 0), doneBytes: 0, sent: 0, meter: sshMeter(),
      box: el('div', { class: 'card' }, el('div', { class: 'row' }, el('span', { class: 'spin' }), el('b', { class: 'lab', style: 'flex:1', text: '准备中…' })),
        el('div', { class: 'bar blue', style: 'margin-top:10px' }, el('i', { style: 'width:0%' })), el('div', { class: 'hint pr' }))
    };
    try { if (tg && tg.enableClosingConfirmation) tg.enableClosingConfirmation(); } catch (e) {}
    if (r2UpPage && r2UpPage.box.isConnected) r2UpPage.hook();
    r2UpPaint();
    function one(k) {
      if (k >= files.length) return Promise.resolve();
      var f = files[k], key = prefix + f.name;
      u.k = k; u.sent = 0; u.status = '准备中'; r2UpPaint();
      function prog(sent) { u.sent = sent; r2UpPaint(); }
      function status(t) { u.status = t; r2UpPaint(); }
      var job;
      if (f.size > MP_MIN) job = mpUpload(bucket, f, key, prog, status);     // 大文件：分片上传，断一下只重传那一片
      else {
        job = api('r2_edit', { op: 'upload_url', bucket: bucket, key: key, type: f.type || 'application/octet-stream', size: f.size, origin: location.origin }).then(function (x) {
          status('上传中');
          return xhrPut(x.url, f, prog);
        });
      }
      return job.then(function () { u.ok++; }, function (e) { toast(f.name + '：' + e.message, true); })
        .then(function () { u.doneBytes += f.size; u.sent = 0; return one(k + 1); });
    }
    one(0).then(function () {
      R2UP = null;
      try { if (tg && tg.disableClosingConfirmation) tg.disableClosingConfirmation(); } catch (e) {}
      if (u.ok) toast('已上传 ' + u.ok + ' 个文件' + (u.ok < files.length ? '，' + (files.length - u.ok) + ' 个失败' : ''));
      r2BarSync();
      var pg = r2UpPage;
      if (pg && pg.box.isConnected) { pg.hook(); if (pg.bucket === bucket) pg.reload(); }
    });
  }
  function r2UpPaint() {
    var u = R2UP; if (!u) return;
    var f = u.files[u.k], now = u.doneBytes + u.sent;
    var pct = Math.min(100, Math.round(now / Math.max(u.total, 1) * 100));
    u.pct = pct;
    u.box.querySelector('.lab').textContent = u.status + ' · 上传 ' + (u.k + 1) + ' / ' + u.files.length + '：' + (f ? f.name : '');
    u.box.querySelector('.bar > i').style.width = pct + '%';
    u.box.querySelector('.pr').textContent = fmtBytes(now) + ' / ' + fmtBytes(u.total) + ' · ' + pct + '%' + (u.sent > 0 ? ' · ' + u.meter(now) : '');
    r2BarSync();
  }
  // 不在这个桶的页面时，底部显示一条「正在上传」，点一下回到桶里
  function r2BarSync() {
    var u = R2UP, show = !!u && !u.box.isConnected;
    if (!show) { if (r2Bar) { r2Bar.remove(); r2Bar = null; } return; }
    if (!r2Bar) {
      r2Bar = el('button', { class: 'upbar', type: 'button', onclick: function () { if (R2UP) go('dns', 'r2b:' + R2UP.bucket + ':' + R2UP.prefix); } },
        el('span', { class: 'spin' }), el('span', { class: 'upbar-t' }), el('span', { class: 'upbar-p' }), el('i', { class: 'upbar-bar' }));
      document.body.appendChild(r2Bar);
    }
    r2Bar.querySelector('.upbar-t').textContent = 'R2 上传中 ' + (u.k + 1) + ' / ' + u.files.length + ' · ' + u.bucket;
    r2Bar.querySelector('.upbar-p').textContent = (u.pct || 0) + '%';
    r2Bar.querySelector('.upbar-bar').style.width = (u.pct || 0) + '%';
  }
  // 分片上传：每片 16MB 起（超大文件自动加大），同时传 3 片，每片失败自动重试 5 次；每片的上传地址现取，不会因为总时间太长而过期
  function mpUpload(bucket, f, key, prog, status) {
    var up = null, partSize = 0, nparts = 0, etags = {}, loaded = {}, finished = 0;
    function sent() { var s = 0; for (var n in loaded) s += loaded[n]; return s; }
    function doPart(n, attempt) {
      var from = (n - 1) * partSize, blob = f.slice(from, Math.min(from + partSize, f.size));
      return api('r2_edit', { op: 'mp_part', bucket: bucket, key: key, upload_id: up, n: n }).then(function (x) {
        return xhrPut(x.url, blob, function (b) { loaded[n] = b; prog(sent()); });
      }).then(function (etag) {
        if (!etag) throw new Error('读不到分片校验值（ETag），存储桶的跨域设置需要暴露 ETag');
        etags[n] = etag; loaded[n] = blob.size; finished++; prog(sent());
        status('上传中 · 已完成 ' + finished + ' / ' + nparts + ' 片');
      }, function (e) {
        loaded[n] = 0; prog(sent());
        if (attempt >= 5) throw e;
        status('第 ' + n + ' 片出错，' + attempt + ' / 5 次重试中');
        return new Promise(function (r) { setTimeout(r, 1500 * attempt); }).then(function () { return doPart(n, attempt + 1); });
      });
    }
    return api('r2_edit', { op: 'mp_start', bucket: bucket, key: key, type: f.type || 'application/octet-stream', size: f.size, origin: location.origin }).then(function (x) {
      up = x.upload_id; partSize = x.part_size; nparts = Math.ceil(f.size / partSize);
      status('上传中 · 共 ' + nparts + ' 片');
      return new Promise(function (res, rej) {
        var next = 1, active = 0, failed = false;
        function launch() {
          if (failed) return;
          if (finished === nparts) return res();
          while (active < 3 && next <= nparts) {
            active++;
            doPart(next++, 1).then(function () { active--; launch(); }, function (e) { active--; if (!failed) { failed = true; rej(e); } });
          }
        }
        launch();
      });
    }).then(function () {
      status('合并分片中');
      var parts = []; for (var n = 1; n <= nparts; n++) parts.push({ n: n, etag: etags[n] });
      return api('r2_edit', { op: 'mp_done', bucket: bucket, key: key, upload_id: up, parts: parts });
    }).catch(function (e) {
      if (up) api('r2_edit', { op: 'mp_abort', bucket: bucket, key: key, upload_id: up }).catch(function () {});
      throw e;
    });
  }
  var MP_MIN = 32 * 1024 * 1024;      // 超过 32MB 用分片上传
  function xhrPut(url, file, onProg) {
    return new Promise(function (res, rej) {
      var x = new XMLHttpRequest();
      x.open('PUT', url);
      x.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
      var timer = null;
      function arm() { clearTimeout(timer); timer = setTimeout(function () { x.abort(); rej(new Error('30 秒没有任何进度，已中止：网络不通，或存储桶的跨域设置没放行这个页面')); }, 30000); }
      x.upload.onprogress = function (e) { arm(); if (e.lengthComputable) onProg(e.loaded); };
      x.onload = function () { clearTimeout(timer); if (x.status >= 200 && x.status < 300) res(x.getResponseHeader('ETag')); else rej(new Error('上传失败（' + x.status + '）')); };
      x.onerror = function () { clearTimeout(timer); rej(new Error('上传失败，网络中断或存储桶跨域没放行')); };
      arm();
      x.send(file);
    });
  }

  function r2BucketSettings(holder, name) {
    holder.appendChild(skeleton(2));
    var myEpoch = epoch, b = null;
    function load() {
      return api('r2_bucket', { bucket: name }).then(function (x) { if (epoch !== myEpoch) return; b = x; draw(); })
        .catch(function (e) { if (epoch === myEpoch) clear(holder).appendChild(failBox(e.message, function () { go('dns', 'r2s:' + name, true); })); });
    }
    function edit(p, msg) { return api('r2_edit', Object.assign({ bucket: name }, p)).then(function (x) { b = x; draw(); if (msg) toast(msg); }); }
    function draw() {
      clear(holder);
      holder.appendChild(el('div', { class: 'card' }, el('h3', { text: '概况' }),
        el('div', { class: 'kv' }, el('span', { text: '文件' }), el('span', { text: r2Usage(b) })),
        el('div', { class: 'kv' }, el('span', { text: '位置' }), el('span', { text: r2LocText(b.location) })),
        el('div', { class: 'kv' }, el('span', { text: '创建于' }), el('span', { text: timeText(b.created) })),
        el('div', { class: 'kv' }, el('span', { text: 'S3 地址' }), el('span', { class: 'mono tapcopy', style: 'font-size:12px', text: b.s3_endpoint, onclick: function () { copy(b.s3_endpoint); } }))));
      var pc = el('div', { class: 'card' }, el('h3', { text: '公开访问' }));
      pc.appendChild(swRow('r2.dev 公开网址', b.dev_url ? '任何人都能用这个网址下载桶里的文件（Cloudflare 会限速，正式用请绑自己的域名）。' : '打开后桶里的文件可以通过 Cloudflare 提供的 r2.dev 网址直接访问。', !!b.dev_url, function (on) { return edit({ op: 'dev_url', enabled: on }, on ? '已开启 r2.dev 公开网址' : '已关闭 r2.dev 公开网址'); }));
      if (b.dev_url) pc.appendChild(el('div', { class: 'kv' }, el('span', { text: '网址' }), el('span', { class: 'mono tapcopy', style: 'font-size:12px', text: b.dev_url, onclick: function () { copy(b.dev_url); } })));
      pc.appendChild(el('div', { class: 'row', style: 'margin:12px 0 6px' }, el('b', { style: 'flex:1;font-size:14px', text: '自定义域名' }),
        el('button', { class: 'btn sm primary', text: '＋ 绑定', onclick: bindDomain })));
      if (!b.domains.length) pc.appendChild(el('div', { class: 'hint', style: 'margin-top:0', text: '没有绑定。绑定后可以用 https://你的域名/文件路径 访问，走 Cloudflare 缓存，没有限速。' }));
      b.domains.forEach(function (dm) {
        pc.appendChild(el('div', { class: 'kv' }, el('span', { class: 'mono', style: 'width:auto;flex:1;color:var(--text)', text: dm.domain }),
          el('span', { style: 'flex:none;display:flex;gap:6px;align-items:center' },
            el('span', { class: 'pill ' + (dm.status === 'active' ? 'ok' : 'warn'), text: dm.status === 'active' ? '生效中' : '验证中' }),
            el('button', { class: 'btn sm danger', text: '解绑', onclick: function () { confirmBox('解绑域名', dm.domain + ' 解绑后不能再访问这个桶。', function () { return edit({ op: 'domain_del', domain: dm.domain }, '已解绑'); }); } }))));
      });
      holder.appendChild(pc);
      var cc = el('div', { class: 'card' }, el('h3', { text: '跨域（CORS）' }));
      cc.appendChild(el('div', { class: 'kv' }, el('span', { text: '允许来源' }), el('span', { class: 'mono', style: 'font-size:12px', text: b.cors.length ? b.cors.join('\n') : '未设置' })));
      cc.appendChild(el('div', { class: 'row', style: 'margin-top:10px' }, el('button', { class: 'btn sm', style: 'flex:1', text: '修改', onclick: function () {
        sheet('跨域允许来源', [{ key: 'o', label: '一行一个，* 表示全部', multi: true, ph: 'https://example.com', value: b.cors.join('\n') }], '保存', function (v) {
          return edit({ op: 'cors', origins: v.o.split(/\s+/).filter(Boolean) }, '已保存');
        }, el('div', { class: 'hint', text: '网页里用 JS 直接读写这个桶的文件时才需要。Mini App 自己的上传地址会自动加进来。' }));
      } })));
      holder.appendChild(cc);
      holder.appendChild(el('div', { class: 'card' }, el('h3', { text: '危险操作' }),
        el('button', { class: 'btn danger block', text: '删除存储桶', onclick: function () {
          if (b.objects) { toast('桶里还有 ' + b.objects + ' 个文件，Cloudflare 要求先清空才能删除', true); return; }
          confirmBox('删除存储桶', '删除 ' + name + ' 后无法恢复。桶里还有文件时 Cloudflare 会拒绝。', function () { return api('r2_edit', { op: 'bucket_del', bucket: name }).then(function (x) { r2Save(x); toast('已删除'); go('dns', 'r2', true); }); });
        } }),
        el('div', { class: 'hint', text: '只能删除空桶。' })));
    }
    function bindDomain() {
      var zones = (dnsZones || []).filter(function (z) { return z.status === 'active'; });
      if (!zones.length) { toast('先在「域名」里确认有已生效的域名', true); return; }
      sheet('绑定自定义域名', [
        { key: 'sub', label: '子域名', ph: '例如 files' },
        { key: 'zone', label: '主域名', options: zones.map(function (z) { return [z.id, z.name]; }), value: zones[0].id }
      ], '绑定', function (v) {
        var z = zones.filter(function (x) { return x.id === v.zone; })[0];
        var full = (v.sub.trim() ? v.sub.trim().replace(/\.$/, '') + '.' : '') + z.name;
        return edit({ op: 'domain_add', zone: z.id, domain: full }, '已绑定 ' + full + '，一两分钟后生效');
      }, el('div', { class: 'hint', text: 'Cloudflare 会自动加好解析记录和证书。这个子域名不能已有别的解析记录。' }));
    }
    load();
  }

  // ============================================================ 管理（仅管理员）
  // ---- 主机状态仪表（原位更新，带颜色分级 + 趋势线）
  var SVGNS = 'http://www.w3.org/2000/svg', gid = 0;
  function svg(tag, attrs) { var n = document.createElementNS(SVGNS, tag); for (var k in attrs) n.setAttribute(k, attrs[k]); return n; }
  var LEVELS = {
    lo:  { a: '#2ee6a6', b: '#27c4e8', c: 'rgba(46,230,166,.55)' },
    mid: { a: '#ffe27a', b: '#ff9f43', c: 'rgba(255,184,77,.55)' },
    hi:  { a: '#ff9a8a', b: '#ff3d5a', c: 'rgba(255,77,100,.6)' }
  };
  function levelOf(p) { return p >= 85 ? 'hi' : p >= 60 ? 'mid' : 'lo'; }
  function dial(label) {
    var id = 'g' + (++gid), R = 38, C = 2 * Math.PI * R, ARC = C * 0.75, TICKS = 27;
    var root = el('div', { class: 'gauge' });
    var sv = svg('svg', { viewBox: '0 0 100 100' });
    var defs = svg('defs', {}), grad = svg('linearGradient', { id: id, x1: '0', y1: '1', x2: '1', y2: '0' });
    var s1 = svg('stop', { offset: '0%' }), s2 = svg('stop', { offset: '100%' });
    grad.appendChild(s1); grad.appendChild(s2); defs.appendChild(grad); sv.appendChild(defs);
    var ticks = [];
    for (var i = 0; i < TICKS; i++) {
      var ang = (135 + i * 270 / (TICKS - 1)) * Math.PI / 180, r1 = 46, r2 = 49.5;
      var t = svg('line', { x1: 50 + r1 * Math.cos(ang), y1: 50 + r1 * Math.sin(ang), x2: 50 + r2 * Math.cos(ang), y2: 50 + r2 * Math.sin(ang), class: 'tk' });
      ticks.push(t); sv.appendChild(t);
    }
    var base = { cx: 50, cy: 50, r: R, fill: 'none', 'stroke-linecap': 'round', transform: 'rotate(135 50 50)' };
    sv.appendChild(svg('circle', Object.assign({ class: 'trk', 'stroke-width': 8, 'stroke-dasharray': ARC + ' ' + C }, base)));
    var fg = svg('circle', Object.assign({ class: 'val', 'stroke-width': 8, stroke: 'url(#' + id + ')', 'stroke-dasharray': '0 ' + C }, base));
    sv.appendChild(fg);
    root.appendChild(sv);
    var num = el('div', { class: 'num' }, el('b', { text: '0' }), el('small', { text: '%' }));
    root.appendChild(num);
    var sub = el('div', { class: 's' });
    root.appendChild(el('div', { class: 'l', text: label })); root.appendChild(sub);
    return {
      node: root,
      set: function (pct, subText) {
        pct = Math.max(0, Math.min(100, pct)); var lv = LEVELS[levelOf(pct)];
        s1.setAttribute('stop-color', lv.a); s2.setAttribute('stop-color', lv.b);
        fg.setAttribute('stroke-dasharray', Math.max(0.01, ARC * pct / 100) + ' ' + C);
        root.style.setProperty('--gc', lv.c); root.style.setProperty('--ga', lv.a);
        var lit = Math.round(TICKS * pct / 100);
        ticks.forEach(function (t, i) { t.style.stroke = i < lit ? lv.a : ''; t.style.opacity = i < lit ? 1 : ''; });
        num.firstChild.textContent = pct < 10 ? pct.toFixed(1) : Math.round(pct);
        sub.textContent = subText || '';
      }
    };
  }
  function spark(color1, color2) {
    var id = 'sp' + (++gid), w = 300, h = 54;
    var sv = svg('svg', { viewBox: '0 0 ' + w + ' ' + h, class: 'spark', preserveAspectRatio: 'none' });
    var defs = svg('defs', {}), g = svg('linearGradient', { id: id, x1: '0', y1: '0', x2: '0', y2: '1' });
    g.appendChild(svg('stop', { offset: '0%', 'stop-color': color1, 'stop-opacity': '.45' })); g.appendChild(svg('stop', { offset: '100%', 'stop-color': color1, 'stop-opacity': '0' }));
    defs.appendChild(g); sv.appendChild(defs);
    var area = svg('path', { fill: 'url(#' + id + ')' }), line = svg('path', { fill: 'none', stroke: color2 || color1, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' });
    sv.appendChild(area); sv.appendChild(line);
    return {
      node: sv,
      draw: function (arr, max) {
        if (arr.length < 2) return;
        max = Math.max(max || 0, 1e-6); var pts = arr.map(function (v, i) { return [i / (arr.length - 1) * w, h - 4 - (Math.min(v, max) / max) * (h - 10)]; });
        var d = pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' ');
        line.setAttribute('d', d); area.setAttribute('d', d + ' L' + w + ' ' + h + ' L0 ' + h + ' Z');
      }
    };
  }
  // 每 3 秒轮询，10 分钟后自动停止；切换页面或刷新会重新开始。上一次没返回就跳过这一轮
  function livePoll(fn, pillBox) {
    var t0 = Date.now(), id, my = epoch, busy = false;
    function paint(on) {
      if (!pillBox) return;
      clear(pillBox);
      pillBox.appendChild(on ? el('span', { class: 'pill ok' }, el('i', { class: 'dot' }), '实时') : el('span', { class: 'pill', text: '已停止刷新' }));
    }
    function run() {
      if (busy) return;
      busy = true;
      var r; try { r = fn(); } catch (e) { r = null; }
      Promise.resolve(r).then(function () { busy = false; }, function () { busy = false; });
    }
    function tick() {
      if (epoch !== my) { clearInterval(id); return; }
      if (Date.now() - t0 > 600000) { clearInterval(id); paint(false); return; }
      run();
    }
    paint(true); run(); id = setInterval(tick, 3000); timers.push(id);
  }
  function statusView(withTrend) {
    var cpu = dial('CPU'), mem = dial('内存'), disk = dial('硬盘');
    var up = el('b', { class: 'up' }), down = el('b', { class: 'dn' });
    var root = el('div', { class: 'statusv' },
      el('div', { class: 'gauges' }, cpu.node, mem.node, disk.node),
      el('div', { class: 'speed' },
        el('div', null, el('span', { class: 'ar up', text: '↑' }), up, el('span', { text: '上传' })),
        el('div', null, el('span', { class: 'ar dn', text: '↓' }), down, el('span', { text: '下载' }))));
    var hc = [], hu = [], hd = [], sc, sn1, sn2;
    if (withTrend) {
      sc = spark('#2ee6a6', '#5ff0c0'); sn1 = spark('#ffb454', '#ffc978'); sn2 = spark('#46d39a', '#7af0bf');
      root.appendChild(el('div', { class: 'trend' }, el('div', { class: 'tl' }, el('span', { text: 'CPU 趋势' }), el('em', { text: '最近约 2 分钟' })), sc.node));
      root.appendChild(el('div', { class: 'trend' }, el('div', { class: 'tl' }, el('span', { text: '网络趋势' }), el('em', { class: 'lg' }, el('i', { class: 'u' }), '上传 ', el('i', { class: 'd' }), '下载')),
        el('div', { class: 'two' }, sn1.node, sn2.node)));
    }
    function push(a, v) { a.push(v); if (a.length > 30) a.shift(); }
    return {
      node: root,
      update: function (s) {
        cpu.set(s.cpu, s.cores + ' 核 · 负载 ' + s.load[0]);
        mem.set(s.mem.pct, fmtBytes(s.mem.used) + ' / ' + fmtBytes(s.mem.total));
        disk.set(s.disk.pct, fmtBytes(s.disk.used) + ' / ' + fmtBytes(s.disk.total));
        up.textContent = fmtBytes(s.net.up) + '/s'; down.textContent = fmtBytes(s.net.down) + '/s';
        if (withTrend) {
          push(hc, s.cpu); push(hu, s.net.up); push(hd, s.net.down);
          sc.draw(hc, 100); var m = Math.max.apply(null, hu.concat(hd).concat([1024])); sn1.draw(hu, m); sn2.draw(hd, m);
        }
      }
    };
  }
  function pageAdmin(page, sub) {
    if (!me || !me.admin) { page.appendChild(lockBox('只有管理员可以使用')); return; }
    if (sub === 'servers') return pageServers(page);
    if (sub === 'srvgroups') return pageGroups(page);
    if (sub && sub.indexOf('server:') === 0) return pageServer(page, sub.slice(7));
    if (sub === 'devices') return pageDevices(page);
    if (!me.owner) { page.appendChild(lockBox('管理页面只有所有者可以使用')); return; }
    if (sub && /^git/.test(sub)) return gitRoute(page, sub);
    if (sub && /^ssh/.test(sub)) return sshRoute(page, sub);
    page.appendChild(top('服务器管理'));

    var ub = el('div', { class: 'card au' });          // 授权用户卡片：放在页面最下面（主屏幕登录之后）
    function auRow(id, sub, owner, onDel) {
      return el('div', { class: 'au-row' },
        el('span', { class: 'au-av' + (owner ? ' own' : '') }, icon(owner ? 'own' : 'usr')),
        el('span', { class: 'au-tx' }, el('b', { text: id }), el('small', { text: sub })),
        owner ? el('span', { class: 'au-badge', text: '所有者' }) : el('button', { class: 'au-x', text: '移除', onclick: onDel }));
    }
    var auOpen = false;       // 默认收起，点标题展开
    function drawUsers(d) {
      clear(ub);
      ub.classList.toggle('open', auOpen);
      ub.appendChild(el('button', { class: 'au-h', onclick: function () { auOpen = !auOpen; ub.classList.toggle('open', auOpen); } },
        el('span', { class: 'au-t' }, icon('usr'), el('b', { text: '授权用户' })),
        el('span', { class: 'au-n', text: (d.owners.length + d.users.length) + ' 人' }),
        el('span', { class: 'au-arr', text: '›' })));
      var body = el('div', { class: 'au-b' }), inner = el('div', { class: 'au-in' });
      body.appendChild(inner);
      d.owners.forEach(function (o) { inner.appendChild(auRow(o, '所有者 · 全部权限', true)); });
      d.users.forEach(function (u) {
        inner.appendChild(auRow(u.id, u.note || '授权用户', false, function () { confirmBox('移除授权', '移除后 ' + u.id + ' 将无法使用机器人。', function () { return api('users_edit', { op: 'del', id: u.id }).then(drawUsers); }); }));
      });
      inner.appendChild(el('button', { class: 'au-add', onclick: function () { sheet('添加授权用户', [{ key: 'id', label: 'Telegram 用户 ID（对方发 /myid 可得）', ph: '123456789' }, { key: 'note', label: '备注（可选）' }], '添加', function (v) { return api('users_edit', { op: 'add', id: v.id, note: v.note }).then(drawUsers); }); } }, el('span', { text: '＋' }), el('span', { text: '添加授权用户' })));
      inner.appendChild(el('div', { class: 'au-hint', text: '授权用户和所有者权限相同，所有功能都能使用。' }));
      ub.appendChild(body);
    }
    api('users').then(drawUsers).catch(function (e) { ub.appendChild(el('div', { class: 'hint', text: e.message })); });
    page.appendChild(devEntry('admin'));
    page.appendChild(ub);
  }

  // ============================================================ 远程服务器：用 SFTP 管理别的服务器上的文件（只有所有者能用）
  // 路由（都在「管理」标签页下）：ssh = 服务器列表；sshd:<服务器id>|<目录> = 浏览目录；sshf:<服务器id>|<文件> = 查看 / 编辑文件
  function sTab() { return cur.tab === 'tools' ? 'tools' : 'admin'; }
  var sshAutoEdit = '';        // 从「⋯ → 编辑」进来的文件路径：打开文件页后直接进入编辑，保存 / 取消后回到目录
  function sshRoute(page, sub) {
    if (sub === 'ssh') return pageSshList(page);
    var i = sub.indexOf(':');
    var fn = i > 0 ? { sshd: pageSshDir, sshf: pageSshFile }[sub.slice(0, i)] : null;
    return fn ? fn(page, sub.slice(i + 1)) : pageSshList(page);
  }
  // ============================================================ 安装代理（节点 agent + 订阅）
  // 路由（工具标签页下）：px = 节点列表 + 订阅地址；pxn:<节点id> = 节点详情（安装 / 重装 / 卸载协议、导入链接）
  var PX_OP = { hy2: '安装 hy2', anytls: '安装 anytls', tuic: '安装 tuic', reality: '安装 reality', rm_hy2: '卸载 hy2', rm_anytls: '卸载 anytls', rm_tuic: '卸载 tuic', rm_reality: '卸载 reality',
    restart_hy2: '重启 hy2', restart_anytls: '重启 anytls', restart_tuic: '重启 tuic', restart_reality: '重启 reality', update: '更新节点程序', uninstall_agent: '卸载节点程序' };
  // 协议：[键, 名称, 未安装时的说明, 传输层, 默认端口]
  var PX_PROTOS = [['hy2', 'Hysteria2', 'UDP · 可选混淆 / 端口跳跃', 'UDP', '443'], ['anytls', 'AnyTLS', 'TCP · sing-box 内核', 'TCP', '8443'],
    ['tuic', 'TUIC', 'UDP · QUIC v5 · sing-box 内核', 'UDP', '8443'],
    ['reality', 'VLESS Reality', 'TCP · 不需要域名和证书 · sing-box 内核', 'TCP', '443']];
  function pxProto(k) { return PX_PROTOS.filter(function (x) { return x[0] === k; })[0]; }
  var PX_ST = { queue: '排队中', run: '执行中…', ok: '完成', fail: '失败' };
  function pxRoute(page, sub) {
    if (sub.indexOf('pxn:') === 0) return pageProxyNode(page, sub.slice(4));
    return pageProxy(page);
  }
  function pxTile() {
    var sub = el('span', { class: 'st-sub', text: '一行命令装节点 · hy2 / anytls / tuic / reality · 订阅地址' });
    var ic = el('span', { class: 'st-ic breathe' }, icon('tools'));
    api('px', null, 0).then(function (d) {
      if (!d.nodes.length) return;
      var up = d.nodes.filter(function (n) { return n.online; }).length;
      sub.textContent = d.nodes.length + ' 个节点 · ' + up + ' 个在线 · hy2 / anytls / tuic / reality';
      ic.classList.add(up === d.nodes.length ? 'on' : 'off');
    }, function () {});
    return el('button', { class: 'card srv-tile', onclick: function () { go('tools', 'px'); } },
      ic, el('span', { class: 'st-tx' }, el('b', { text: '安装代理' }), sub), goArrow());
  }
  function pxCopyRow(tag, value, label) {
    return el('button', { class: 'gh-url', onclick: function () { copy(value); } },
      el('span', { class: 'gh-ut', text: tag }), el('span', { class: 'mono gh-uv', text: label || value }), el('span', { class: 'gh-uc', text: '复制' }));
  }
  function pxCmdSheet(cmd, title) {
    var bg = el('div', { class: 'sheet-bg', onclick: function (e) { if (e.target === bg) bg.remove(); } },
      el('div', { class: 'sheet' }, el('h2', { text: title || '安装命令' }),
        el('div', { class: 'hint', style: 'margin:0 0 8px', text: '在节点服务器上用 root 执行下面这一行（点一下复制）。装好后这里会自动显示在线。' }),
        el('div', { class: 'px-cmd' }, el('pre', { class: 'mono', text: cmd }),
          el('button', { class: 'btn sm primary', text: '复制', onclick: function () { copy(cmd); } })),
        el('div', { class: 'acts' }, el('button', { class: 'btn', text: '关闭', onclick: function () { bg.remove(); } }))));
    document.body.appendChild(bg);
  }
  function pxCheck(btn) {
    run(btn, function () {
      return api('px_check', null, 0).then(function (r) {
        var body = el('div');
        r.items.forEach(function (x) {
          body.appendChild(el('div', { class: 'px-job ' + (x.ok ? 'ok' : 'fail'), style: 'flex-wrap:wrap' }, el('b', { text: x.name }), el('span', { text: x.ok ? '通过' : '有问题 (' + x.code + ')' }),
            el('small', { style: 'flex-basis:100%;margin:2px 0 0;white-space:normal;line-height:1.5', text: x.note })));
        });
        var blocked = r.items.some(function (x) { return x.cf; });
        var fix = blocked ? el('button', { class: 'btn primary block', style: 'margin:10px 0 0', text: '自动放行（Cloudflare）', onclick: function (e) {
          run(e.currentTarget, function () { return api('px_cf').then(function () { bg.remove(); toast('已加放行规则，再点一次「测试」看看'); }); });
        } }) : null;
        var bg = el('div', { class: 'sheet-bg', onclick: function (e) { if (e.target === bg) bg.remove(); } },
          el('div', { class: 'sheet' }, el('h2', { text: '订阅检测' }),
            el('div', { class: 'hint', style: 'margin:0 0 8px', text: '服务器自己去访问一遍 ' + r.url + '，和手机走同一条路径。' }), body, fix,
            el('div', { class: 'acts' }, el('button', { class: 'btn', text: '关闭', onclick: function () { bg.remove(); } }))));
        document.body.appendChild(bg);
      });
    });
  }
  function pxSubCard(sub) {
    var card = el('div', { class: 'card gh-clone' }), open = false;
    function draw(s) {
      clear(card);
      card.appendChild(el('h3', { class: 'h3i nobar sub-h' + (open ? '' : ' closed'), onclick: function () { open = !open; draw(s); } },
        icon('link'), el('span', { text: '订阅地址' }), el('span', { class: 'sp' }), el('span', { class: 'go-ar grp-arr' }, icon('go'))));
      if (!open) return;
      card.appendChild(pxCopyRow('自动', s.auto));
      card.appendChild(pxCopyRow('Clash', s.clash));
      card.appendChild(pxCopyRow('sing-box', s.singbox));
      card.appendChild(pxCopyRow('小火箭', s.links));
      var srUrl = 'shadowrocket://add/sub://' + btoa(s.links) + '?remark=' + encodeURIComponent(s.name || location.hostname);
      card.appendChild(pxCopyRow('小火箭导入', srUrl));
      card.appendChild(el('button', { class: 'btn sm block', style: 'margin-top:8px', text: '用小火箭打开（带上订阅名称）', onclick: function () { location.href = srUrl; } }));
      card.appendChild(el('button', { class: 'gh-url px-dom', style: 'margin-top:10px', onclick: function () {
        sheet('订阅名称', [{ key: 'name', label: '客户端导入后显示的名字（留空 = 不指定，客户端会显示域名）', value: s.name || '', ph: '比如：我的节点' }], '保存', function (v) {
          return api('px_sub', { op: 'name', name: v.name }).then(function (r) { toast('已保存'); draw(r); });
        }, el('div', { class: 'hint', style: 'margin:-2px 0 8px', text: '已经导入的订阅，要在客户端里「更新订阅」才会换成新名字；小火箭（Shadowrocket）不读响应里的名字：请用订阅地址下面的「用小火箭打开」导入，导入时会带上这个名称（和 Xboard 的做法一样）。' }));
      } }, el('span', { class: 'gh-ut', text: '名称' }), el('span', { class: 'gh-uv', text: s.name || '未设置（显示域名）' }), el('span', { class: 'gh-uc', text: '修改' })));
      card.appendChild(el('div', { class: 'row', style: 'margin-top:10px' },
        el('button', { class: 'btn sm', style: 'flex:1', text: '测试', onclick: function (e) { pxCheck(e.currentTarget); } }),
        el('button', { class: 'btn sm', style: 'flex:1', text: '重置', onclick: function () {
          confirmBox('重置订阅密钥', '旧的订阅地址会立刻失效，客户端需要重新导入新地址。', function () { return api('px_sub', { op: 'reset' }).then(function (r) { draw(r); toast('已重置'); }); });
        } })));
      card.appendChild(el('div', { class: 'hint', text: '「自动」会按客户端识别格式；小火箭（Shadowrocket）、v2rayN、NekoBox 等用「小火箭」那一行（通用链接列表）；小火箭想带名称导入，点「用小火箭打开」，Clash / Mihomo 用 Clash，sing-box / Hiddify 用 sing-box。订阅里只包含已装好的节点。' }));
    }
    draw(sub);
    return card;
  }
  function pageProxy(page) {
    page.appendChild(top('安装代理', true));
    var acts = el('div', { class: 'gh-acts' }), subBox = el('div'), card = el('div', { class: 'card gh-card' }, skeleton(2)), nodesBox = el('div');
    page.appendChild(acts); page.appendChild(card); page.appendChild(nodesBox); page.appendChild(subBox);
    api('px').then(function (d) {
      clear(card);
      function domainSheet() {
        sheet('设置域名', [{ key: 'url', label: '订阅 / 安装用的域名（https:// 开头；留空 = 用默认）', value: d.custom || '', ph: 'https://sub.example.com' }], '保存', function (v) {
          return api('px_domain', { url: v.url }).then(function (r) {
            toast(r.cf === 'ok' ? '已保存，并已在 Cloudflare 放行订阅' : (r.cf || '').indexOf('err:') === 0 ? '已保存；Cloudflare 放行没成功：' + r.cf.slice(4) : '已保存');
            go(cur.tab, cur.sub, true);
          });
        }, el('div', { class: 'hint', style: 'margin:-2px 0 8px', text: '这个域名要解析到本站同一个目录（里面有 px.php）。改域名只影响之后生成的安装命令和订阅地址，已装好的节点仍连旧域名。' }));
      }
      if (!d.web) {
        acts.appendChild(el('button', { class: 'btn primary', text: '设置域名', onclick: domainSheet }));
        card.remove(); acts.appendChild(el('div', { class: 'hint', text: '先设置一个域名（https:// 开头），安装命令和订阅地址都会用它。可以用单独的域名，不必和小程序同域。' }));
        return;
      }
      acts.appendChild(el('button', { class: 'btn primary', text: '＋ 添加节点', onclick: function () {
        sheet('添加节点', [{ key: 'name', label: '名称（随便起）', value: '', ph: '比如：东京 1' }].concat(d.groups.length ? [{ key: 'group', label: '分组', options: pxGroupOpts(d), value: '' }] : []), '生成安装命令', function (v) {
          return api('px_node_edit', { op: 'add', name: v.name, group: v.group || '' }).then(function (n) { pxCmdSheet(n.cmd, '在节点服务器上执行'); go('tools', 'pxn:' + n.id); });
        });
      } }));
      acts.appendChild(el('button', { class: 'btn', text: '分组', onclick: function () { pxGroupsMenu(d); } }));
      if (!d.nodes.length) card.appendChild(el('div', { class: 'hint', style: 'margin:8px 0', text: '还没有节点。点「添加节点」得到一行命令，到节点服务器上执行即可。' }));
      if (d.nodes.length) {
        card.remove();
        pxGroupList(nodesBox, d, function (n) {
          var tags = [];
          if (n.hy2) tags.push('hy2' + (n.hy2.hop ? '·跳跃' : '') + (n.hy2.obfs ? '·混淆' : ''));
          if (n.anytls) tags.push('anytls');
          if (n.tuic) tags.push('tuic');
          if (n.reality) tags.push('reality');
          return el('button', { class: 'gh-row', onclick: function () { go('tools', 'pxn:' + n.id); } },
            el('span', { class: 'gh-ri ssh-st ' + (n.online ? 'on' : 'off') }, icon('srv')),
            el('span', { class: 'gh-rt' }, el('b', { text: n.name }), el('small', {}, !n.online && n.seen ? el('span', { style: 'color:var(--bad);font-weight:600', text: '离线 · ' }) : null, document.createTextNode((n.addr || n.ip || '等待上线') + (tags.length ? ' · ' + tags.join(' · ') : (n.online ? ' · 未安装协议' : '')))) ),
            goArrow());
        });
      }
      subBox.appendChild(pxSubCard(d.sub));
      subBox.appendChild(el('button', { class: 'gh-url px-dom', onclick: domainSheet },
        el('span', { class: 'gh-ut', text: '域名' }), el('span', { class: 'mono gh-uv', text: d.domain }), el('span', { class: 'gh-uc', text: '修改' })));
    }).catch(function (e) { clear(card).appendChild(failBox(e.message, function () { go(cur.tab, cur.sub, true); })); });
  }

  function pxSvcText(p) {
    var s = p.svc;
    return s === 'active' ? '运行中' : s === 'failed' ? '已崩溃' : s === 'none' ? '服务不存在' : s === 'inactive' ? '已停止' : '检测中';
  }
  function pxProtoSheet(n, proto, onDone) {
    var c = n[proto], pr = pxProto(proto);
    var re = proto === 'reality';
    var fields = re ? [
      { key: 'port', label: proto + ' 端口（' + pr[3] + '）', value: c ? String(c.port) : pr[4] },
      { key: 'sni', label: '伪装网站（借它的 TLS 握手；要支持 TLS 1.3，且节点能访问）', value: c ? c.sni : 'www.microsoft.com', ph: 'www.microsoft.com' }
    ] : [
      { key: 'port', label: proto + ' 端口（' + pr[3] + '）', value: c ? String(c.port) : pr[4] },
      { key: 'domain', label: '域名证书（可选）：托管在 Cloudflare 的域名，自动解析到节点并申请证书', value: c && c.domain ? c.domain : '', ph: '新的子域名，如 us1.example.com；留空 = 自签证书' },
      { key: 'sni', label: '伪装域名（SNI，仅自签证书时用）', value: c ? c.sni : 'cdn.jsdelivr.net' }
    ];
    if (proto === 'hy2') {
      fields.push({ key: 'obfs', check: '开启混淆（salamander，密码自动生成）', value: c ? c.obfs : false });
      fields.push({ key: 'hop', check: '开启端口跳跃（iptables 转发一段 UDP 端口）', value: c ? !!c.hop : false });
      fields.push({ key: 'hop_range', label: '跳跃端口范围（开启跳跃时生效）', value: c && c.hop ? c.hop : '20000-30000' });
    }
    sheet((c ? '重新配置 ' : '安装 ') + pr[1], fields, c ? '应用' : '开始安装', function (v) {
      var p = { id: n.id, proto: proto, port: v.port, sni: v.sni, domain: re ? '' : v.domain };
      if (proto === 'hy2') { p.obfs = v.obfs; p.hop = v.hop; p.hop_range = v.hop_range; }
      return api('px_install', p).then(function () { toast('任务已下发'); onDone(); });
    }, el('div', { class: 'hint', style: 'margin:-2px 0 8px', text: re ? '不需要域名和证书。节点会自动生成密钥，客户端直接用导入链接 / 订阅即可（客户端要支持 Reality：v2rayN、Shadowrocket、NekoBox、Hiddify、Clash Meta、sing-box 都可以）。端口建议用 443；伪装网站换成别的也行，只要它支持 TLS 1.3。' : '不填域名 = 自签证书，客户端要开「允许不安全」。填域名 = 用 Cloudflare 自动建灰云解析（必须是没用过的新子域名，不能是订阅 / 小程序在用的域名），节点用 80 端口向 Let\'s Encrypt 申请正式证书（节点的 80 端口要空闲，云安全组要放行 TCP 80），客户端不用开允许不安全。' }));
  }
  function pageProxyNode(page, id) {
    page.appendChild(top('节点', true));
    var head = el('div', { class: 'card' }, skeleton(1)), protos = el('div'), links = el('div'), jobs = el('div');
    page.appendChild(head); page.appendChild(protos); page.appendChild(links); page.appendChild(jobs);
    var timer = null, lastKey = '';
    function again() { go(cur.tab, cur.sub, true); }
    function tick(fast) {
      clearTimeout(timer);
      timer = setTimeout(function () { if (!document.body.contains(head)) return; if (document.hidden && !fast) return tick(false); load(); }, fast ? 2500 : 15000);
    }
    function load() {
      api('px_node', { id: id }, 0).then(function (n) {
        var key = JSON.stringify(n);
        if (key !== lastKey) { lastKey = key; draw(n); }
        tick(n.busy || !n.online);
      }, function (e) { if (!lastKey) clear(head).appendChild(failBox(e.message, again)); else tick(false); });
    }
    function draw(n) {
      var h1 = page.querySelector('.top h1'); if (h1) h1.textContent = n.name;
      clear(head);
      head.appendChild(el('div', { class: 'ssh-r' },
        el('div', { class: 'gh-row', style: 'cursor:default' },
          el('span', { class: 'gh-ri ssh-st ' + (n.online ? 'on' : 'off') }, icon('srv')),
          el('span', { class: 'gh-rt' }, el('b', { text: n.name + (n.online ? ' · 在线' : ' · 离线') }),
            el('small', { text: (n.addr || n.ip || '地址未知') + (n.os ? ' · ' + n.os : '') + (n.ver && n.ver < n.new_ver ? ' · 有新版程序' : '') }))),
        moreBtn('更多', function () { menu(n); })));
      if (!n.seen) {
        head.appendChild(el('div', { class: 'hint', text: '还没有收到这个节点的报到。在节点服务器上执行安装命令后，这里会自动变成在线。' }));
        head.appendChild(el('button', { class: 'btn primary block', style: 'margin-top:10px', text: '查看安装命令', onclick: function () { pxCmdSheet(n.cmd); } }));
      } else if (!n.online) {
        head.appendChild(el('div', { class: 'hint', text: '节点现在连不上（服务器关机 / agent 没运行 / 网络不通）。恢复后会自动变回在线。' }));
      }
      clear(protos);
      PX_PROTOS.forEach(function (pr) {
        var c = n[pr[0]], card = el('div', { class: 'card' });
        card.appendChild(el('h3', { class: 'h3i nobar' }, icon('link'), el('span', { text: pr[1] }), el('span', { class: 'sp' }),
          el('span', { class: 'hint', style: 'margin:0', text: c ? pxSvcText(c) : '未安装' })));
        if (c) {
          var info = pr[3] + ' ' + c.port + (pr[0] === 'reality' ? ' · 伪装 ' + c.sni : c.domain ? ' · 证书 ' + c.domain : ' · 自签 SNI ' + c.sni);
          if (pr[0] === 'hy2') info += (c.obfs ? ' · 混淆' : '') + (c.hop ? ' · 跳跃 ' + c.hop : '');
          card.appendChild(el('div', { class: 'hint', style: 'margin:2px 0 10px', text: info }));
          card.appendChild(el('div', { class: 'gh-acts' },
            el('button', { class: 'btn sm', text: '重新配置', disabled: !n.online || n.busy, onclick: function () { pxProtoSheet(n, pr[0], again); } }),
            el('button', { class: 'btn sm', text: '重启', disabled: !n.online || n.busy, onclick: function (e) { var b = e.currentTarget; run(b, function () { return api('px_op', { id: id, op: 'restart_' + pr[0] }).then(load); }); } }),
            el('button', { class: 'btn sm danger', text: '卸载', disabled: !n.online || n.busy, onclick: function () {
              confirmBox('卸载 ' + pr[1], '会停止服务并删除它的配置，这个节点的订阅里也不再包含它。', function () { return api('px_remove', { id: id, proto: pr[0] }).then(load); });
            } })));
        } else {
          card.appendChild(el('div', { class: 'hint', style: 'margin:2px 0 10px', text: pr[2] }));
          card.appendChild(el('button', { class: 'btn primary block', text: '安装 ' + pr[1], disabled: !n.online || n.busy, onclick: function () { pxProtoSheet(n, pr[0], again); } }));
        }
        protos.appendChild(card);
      });
      // 导入链接
      clear(links);
      if (n.hy2 || n.anytls || n.tuic || n.reality) {
        var lc = el('div', { class: 'card gh-clone' }, el('h3', { class: 'h3i nobar' }, icon('link'), el('span', { text: '导入链接' })));
        links.appendChild(lc);
        api('px_links', { id: id }, 0).then(function (r) {
          if (!r.links.length) { lc.appendChild(el('div', { class: 'hint', text: '节点还没有可用地址：等它上线后会显示，或在「⋯」里手动填写节点地址。' })); return; }
          r.links.forEach(function (x) { lc.appendChild(pxCopyRow(x.t, x.link)); });
          lc.appendChild(el('div', { class: 'hint', text: '点一下复制，到客户端（v2rayN / Shadowrocket / NekoBox / Hiddify 等）选「从剪贴板导入」。整套节点用上面的订阅地址更方便。' }));
        }, function () {});
      }
      // 任务记录
      clear(jobs);
      if (n.jobs && n.jobs.length) {
        var jc = el('div', { class: 'card' }, el('h3', { class: 'h3i nobar' }, icon('list'), el('span', { text: '任务记录' })));
        n.jobs.slice(0, 4).forEach(function (j, i) {
          var row = el('div', { class: 'px-job ' + j.state }, el('b', { text: PX_OP[j.op] || j.op }), el('span', { text: PX_ST[j.state] || j.state }),
            el('small', { text: j.t ? timeText(j.t) : '' }));
          jc.appendChild(row);
          if (j.log && (i === 0 || j.state === 'fail')) jc.appendChild(el('pre', { class: 'px-log mono', text: j.log }));
          else if (j.state === 'run' || j.state === 'queue') jc.appendChild(el('div', { class: 'hint', style: 'margin:0 0 6px', text: j.state === 'queue' ? '等节点领取任务（最多几秒）…' : '节点正在安装，第一次装内核要下载，可能要一两分钟…' }));
        });
        jobs.appendChild(jc);
      }
    }
    function menu(n) {
      var bg = el('div', { class: 'sheet-bg', onclick: function (e) { if (e.target === bg) bg.remove(); } });
      function item(t, fn, cls) { return el('button', { class: 'btn block ' + (cls || ''), style: 'margin:6px 0', text: t, onclick: function () { bg.remove(); fn(); } }); }
      var body = el('div', { class: 'sheet' }, el('h2', { text: n.name }),
        item('重命名 / 设置节点地址', function () {
          sheet('节点信息', [{ key: 'name', label: '名称', value: n.name }, { key: 'addr', label: '节点地址（IP 或域名；留空 = 用节点上报的 IP）', value: n.addr, ph: n.ip || 'example.com' }], '保存', function (v) {
            return api('px_node_edit', { op: 'ren', id: id, name: v.name, addr: v.addr }).then(again);
          });
        }),
        item('设置分组', function () {
          api('px', null, 0).then(function (d) {
            if (!d.groups.length) { toast('还没有分组，先回列表页点「分组」新建', true); return; }
            sheet('设置分组', [{ key: 'g', label: '放进哪个分组', options: pxGroupOpts(d), value: n.group || '' }], '保存', function (v) {
              return api('px_node_edit', { op: 'group', id: id, group: v.g }).then(function () { toast('已保存'); again(); });
            });
          }, function (e) { toast(e.message, true); });
        }),
        item('查看安装命令', function () { pxCmdSheet(n.cmd); }),
        n.online && n.ver < n.new_ver ? item('更新节点程序', function () { api('px_op', { id: id, op: 'update' }).then(load, function (e) { toast(e.message, true); }); }) : null,
        item('重置节点密钥（旧 agent 失效）', function () {
          confirmBox('重置节点密钥', '旧的安装命令和已装的 agent 会立刻失效，需要在节点上重新执行新命令。', function () {
            return api('px_node_edit', { op: 'token', id: id }).then(function (r) { pxCmdSheet(r.cmd); again(); });
          });
        }),
        item('删除节点', function () {
          confirmBox('删除节点', n.online ? '会让节点卸载 agent 并从列表移除；已装好的 hy2 / anytls / tuic / reality 仍在运行，不受影响。' : '节点不在线，只从列表移除。以后要卸载 agent：bash /opt/px-agent/px.sh uninstall', function () {
            return api('px_node_edit', { op: 'del', id: id, force: !n.online }).then(function () { toast('已删除'); backHandler(); });
          });
        }, 'danger'),
        el('div', { class: 'acts' }, el('button', { class: 'btn', text: '取消', onclick: function () { bg.remove(); } })));
      bg.appendChild(body); document.body.appendChild(bg);
    }
    load();
  }

  function sshTile() {
    var sub = el('span', { class: 'st-sub', text: '保存服务器 · 浏览 / 上传 / 下载 / 编辑文件' });
    var ic = el('span', { class: 'st-ic breathe' }, icon('srv'));
    api('ssh').then(function (d) {
      if (!d.servers.length) return;
      sub.textContent = '已保存 ' + d.servers.length + ' 台 · 浏览 / 上传 / 下载 / 编辑文件';
      api('ssh_ping', null, 0).then(function (r) {         // 全部在线 = 绿色呼吸；有离线的 = 红色呼吸
        var allUp = d.servers.every(function (x) { return r.up[x.id]; });
        ic.classList.add(allUp ? 'on' : 'off');
      }, function () {});
    }, function () {});
    return el('button', { class: 'card srv-tile', onclick: function () { go('tools', 'ssh'); } },
      ic, el('span', { class: 'st-tx' }, el('b', { text: 'SFTP 服务器' }), sub), goArrow());
  }
  function sshPJoin(dir, name) { return (dir === '/' ? '' : dir) + '/' + name; }
  function sshDirOf(p) { var i = p.lastIndexOf('/'); return i <= 0 ? '/' : p.slice(0, i); }
  function sshAgo(ts) { return ts ? timeText(ts).split(' ')[0] : ''; }

  function sshEditSheet(s, done) {
    var isNew = !s;
    var bg, del = null;
    if (!isNew) del = el('button', { class: 'btn danger block', style: 'margin:4px 0 10px', text: '删除这台服务器', onclick: function () {
      confirmBox('删除服务器', '只会删除这里保存的登录信息，不会影响服务器本身。', function () { return api('ssh_edit', { op: 'del', id: s.id }).then(function () { bg.remove(); toast('已删除'); done(true); }); });
    } });
    bg = sheet(isNew ? '添加服务器' : '编辑服务器', [
      { key: 'name', label: '名称（随便起，可留空）', value: s ? s.name : '', ph: '比如：东京 VPS' },
      { key: 'host', label: '服务器 IP 或域名', value: s ? s.host : '', ph: '1.2.3.4' },
      { key: 'port', label: 'SSH 端口', value: s ? String(s.port) : '22' },
      { key: 'user', label: '用户名', value: s ? s.user : 'root' },
      { key: 'type', label: '登录方式', options: [['password', '密码'], ['key', '私钥']], value: s ? s.auth : 'password' },
      { key: 'password', label: s && s.has_pw ? '密码（不改就留空）' : '密码', only: ['password'] },
      { key: 'key', label: s && s.has_key ? '私钥（不改就留空）' : '私钥（整段粘贴，含 BEGIN / END 两行）', only: ['key'], multi: true },
      { key: 'passphrase', label: '私钥密码（私钥没设密码就留空）', only: ['key'] },
      { key: 'dir', label: '默认打开的目录（自定义，不存在会自动创建；留空 = 登录后的主目录）', value: s ? s.dir : '', ph: '例如 /data/files，目录不存在会自动创建' },
      s ? { key: 'clear_fp', check: '清除已记住的服务器指纹（服务器重装后才需要）' } : null
    ].filter(Boolean), '保存', function (v) {
      return api('ssh_edit', { op: isNew ? 'add' : 'edit', id: s && s.id, name: v.name, host: v.host, port: v.port, user: v.user, auth: v.type,
        password: v.password, key: v.key, passphrase: v.passphrase, dir: v.dir, clear_fp: v.clear_fp ? 1 : 0 }).then(function (r) { toast('已保存'); done(false, r); });
    }, del);
    var pw = bg.querySelectorAll('input.input');          // 密码类输入框遮住内容
    Array.prototype.forEach.call(pw, function (inp, i) { var lab = inp.parentNode.querySelector('label'); if (lab && /密码/.test(lab.textContent)) { inp.type = 'password'; inp.autocomplete = 'new-password'; } });
    var ta = bg.querySelector('textarea'); if (ta) { ta.setAttribute('spellcheck', 'false'); ta.style.fontFamily = 'ui-monospace,Menlo,monospace'; ta.style.fontSize = '12px'; }
    if (ta) {       // 私钥可以直接从手机「文件」里选，不用手动复制粘贴
      var imp = el('button', { class: 'btn sm', style: 'margin-top:8px', text: '从文件导入私钥', onclick: function () {
        var fi = el('input', { type: 'file', style: 'display:none' });
        document.body.appendChild(fi);
        fi.addEventListener('change', function () {
          var f = fi.files && fi.files[0]; fi.remove(); if (!f) return;
          if (f.size > 20000) { toast('这个文件太大，不像是私钥', true); return; }
          f.text().then(function (t) {
            if (t.indexOf('PRIVATE KEY') < 0) { toast('这个文件里没有找到私钥（要有 BEGIN … PRIVATE KEY 那一行）', true); return; }
            ta.value = t.trim(); toast('已导入：' + f.name);
          }, function () { toast('读取文件失败', true); });
        });
        fi.click();
        setTimeout(function () { if (fi.parentNode && !(fi.files && fi.files.length)) fi.remove(); }, 120000);
      } });
      ta.parentNode.appendChild(imp);
    }
    return bg;
  }

  function pageSshList(page) {
    page.appendChild(top('SFTP 服务器', true));
    var acts = el('div', { class: 'gh-acts' }, el('button', { class: 'btn primary', text: '＋ 添加服务器', onclick: function () { sshEditSheet(null, function () { go(cur.tab, cur.sub, true); }); } }));
    var card = el('div', { class: 'card gh-card' }, skeleton(2));
    page.appendChild(acts); page.appendChild(card);
    api('ssh').then(function (d) {
      clear(card);
      if (!d.paramiko) {
        var c = 'apt install -y python3-paramiko && systemctl restart okbot';
        card.appendChild(el('div', { class: 'hint', style: 'margin:0 0 6px', text: '服务器上还没有安装 SSH 组件（paramiko），先在运行机器人的服务器上执行下面这条（点一下复制），再回来用。' }));
        card.appendChild(el('button', { class: 'gh-url', onclick: function () { copy(c); } }, el('span', { class: 'mono gh-uv', text: c }), el('span', { class: 'gh-uc', text: '复制' })));
        card.appendChild(el('div', { class: 'hint', text: '如果提示 apt 里没有这个包，改用：pip3 install paramiko --break-system-packages' }));
      }
      if (!d.servers.length) card.appendChild(el('div', { class: 'hint', style: 'margin:8px 0', text: '还没有服务器' }));
      d.servers.forEach(function (s) {
        card.appendChild(el('div', { class: 'ssh-r' },
          el('button', { class: 'gh-row', onclick: function () { go(sTab(), 'sshd:' + s.id + '|'); } },
            el('span', { class: 'gh-ri ssh-st', 'data-id': s.id }, icon('srv')),
            el('span', { class: 'gh-rt' }, el('b', { text: s.name }), el('small', { text: s.user + '@' + s.host + (s.port !== 22 ? ':' + s.port : '') + (s.auth === 'key' ? ' · 私钥' : '') }))),
          moreBtn('编辑', function () { sshEditSheet(s, function () { go(cur.tab, cur.sub, true); }); })));
      });
      if (d.servers.length) api('ssh_ping', null, 0).then(function (r) {        // 在线绿 / 离线红（只试端口通不通）
        Array.prototype.forEach.call(card.querySelectorAll('.ssh-st'), function (n) { n.classList.add(r.up[n.getAttribute('data-id')] ? 'on' : 'off'); });
      }, function () {});
    }).catch(function (e) { clear(card).appendChild(failBox(e.message, function () { go(cur.tab, cur.sub, true); })); });
  }

  function sshCrumbs(id, path, isFile) {
    var box = el('div', { class: 'gh-crumbs' });
    box.appendChild(el('button', { text: '/', onclick: function () { go(sTab(), 'sshd:' + id + '|/'); } }));
    var parts = path.split('/').filter(Boolean);
    parts.forEach(function (p, i) {
      if (i) box.appendChild(el('span', { text: '/' }));
      var to = '/' + parts.slice(0, i + 1).join('/'), last = i === parts.length - 1;
      box.appendChild(el('button', { class: last ? 'on' : '', text: p, onclick: last ? null : function () { go(sTab(), 'sshd:' + id + '|' + to); } }));
    });
    return box;
  }

  // 传文件的进度弹层。传输被打断（切到后台 / 网络断了）时变成「已中断」，回到本页面自动继续，也可以点「继续」
  function sshProgSheet(title) {
    var stat = el('div', { class: 'hint', style: 'white-space:pre-line;line-height:1.6', text: '准备中…' }), prog = el('div', { class: 'gh-prog', style: 'display:block' }, el('i'));
    var acts = el('div', { class: 'acts' }), st = { stop: false, paused: null };
    var cancel = el('button', { class: 'btn', text: '取消', onclick: function () { st.stop = true; st.paused = null; if (st.onCancel) st.onCancel(); bg.remove(); document.removeEventListener('visibilitychange', onVis); } });
    var goBtn = el('button', { class: 'btn primary', text: '继续', onclick: function () { resume(); } });
    acts.appendChild(cancel);
    var h = el('h2', { text: title });
    var bg = el('div', { class: 'sheet-bg' }, el('div', { class: 'sheet' }, h, stat, prog, acts));
    document.body.appendChild(bg);
    function resume() {
      var f = st.paused; if (!f || st.stop) return;
      st.paused = null; goBtn.remove(); prog.firstChild.style.opacity = ''; f();
    }
    function onVis() { if (!document.hidden && st.paused) setTimeout(resume, 400); }
    document.addEventListener('visibilitychange', onVis);
    st.set = function (t, pct) { stat.textContent = t; prog.firstChild.style.width = Math.round(pct * 100) + '%'; };
    st.title = function (t) { h.textContent = t; };
    st.close = function () { bg.remove(); document.removeEventListener('visibilitychange', onVis); };
    st.fail = function (m) { st.set(m, 0); prog.style.display = 'none'; cancel.textContent = '关闭'; document.removeEventListener('visibilitychange', onVis); };
    st.pause = function (msg, fn) { stat.textContent = msg; prog.firstChild.style.opacity = '.4'; st.paused = fn; acts.insertBefore(goBtn, cancel); };
    st.acts = acts; st.cancel = cancel; st.stat = stat;
    return st;
  }
  // 传输时遇到网络问题就暂停等待继续；服务器明确报的错（权限、任务过期等）直接失败
  function sshRetryable(e) { return /没有正确响应|网络不稳定/.test(e.message || ''); }
  function sshResumable(ps, fn) {
    return fn().catch(function (e) {
      if (ps.stop || !sshRetryable(e)) throw e;
      return new Promise(function (res) { ps.pause('传输中断了（切到后台会暂停）。回到本页面会自动继续，也可以点「继续」。', function () { res(sshResumable(ps, fn)); }); });
    });
  }
  function sshStore(k, v) { try { if (v == null) localStorage.removeItem(k); else if (v === undefined) return null; else localStorage.setItem(k, v); } catch (e) {} }
  function sshLoad(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }

  // 传输速度（MB/s）：取最近 3 秒的平均
  function sshMeter() {
    var pts = [];
    return function (bytes) {
      var now = Date.now(); pts.push([now, bytes]);
      while (pts.length > 2 && now - pts[0][0] > 3000) pts.shift();
      var dt = (now - pts[0][0]) / 1000;
      return dt > 0.3 ? ((bytes - pts[0][1]) / dt / 1048576).toFixed(2) + ' MB/s' : '计算中…';
    };
  }
  function sshMB(n) { return n < 102400 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(n >= 10485760 ? 1 : 2) + ' MB'; }
  function sshRetry(ps, call) {       // 单个请求：偶尔被限流 / 网络抖动，等一下重发（上传按位置写、下载按块读，重发都不会出错）
    return (function go(left) {
      return call().catch(function (e) {
        if (left > 0 && !ps.stop && sshRetryable(e)) {
          ps.lastErr = { t: Date.now(), m: e.message };            // 进度里显示原因（比如 HTTP 429 = 被限流）
          if (ps.slow) ps.slow();                                  // 通知上传降低并发
          return new Promise(function (r) { setTimeout(r, 1000 * (7 - left)); }).then(function () { return go(left - 1); });
        }
        throw e;
      });
    })(4);
  }
  function sshUploadOne(id, dir, file, ps, idx, cnt) {
    var label = (cnt > 1 ? '（' + idx + '/' + cnt + '）' : '') + file.name, jobId = null;
    var key = 'ssh_up|' + id + '|' + dir + '|' + file.name + '|' + file.size + '|' + file.lastModified;     // 同一个文件被打断后，再选一次就能接着传
    function begin(over) { return api('ssh_up_begin', { id: id, dir: dir, name: file.name, size: file.size, overwrite: over ? 1 : 0 }); }
    ps.set('准备上传 ' + label, 0);
    var saved = sshLoad(key);
    var start = saved ? api('ssh_up_state', { job: saved }, 0).then(function (st) { return { job: saved, chunk: st.chunk, chunks: st.chunks, resumed: true }; }, function () { sshStore(key, null); return null; }) : Promise.resolve(null);
    return start.then(function (b) {
      if (b) return b;
      return begin(false).then(function (nb) {
        if (nb.exists) {
          if (!window.confirm('「' + file.name + '」在服务器上已经有了，要覆盖吗？')) return 'skip';
          return begin(true);
        }
        return nb;
      });
    }).then(function (b) {
      if (b === 'skip') return 'skip';
      jobId = b.job; sshStore(key, b.job);
      ps.onCancel = function () { api('ssh_up_cancel', { job: jobId }, 0).catch(function () {}); sshStore(key, null); };
      ps.set((b.resumed ? '继续上传 ' : '开始传输 ') + label + '…', 0.01);
      function transfer() {
        return api('ssh_up_state', { job: jobId }, 2).then(function (st) {
          var have = {}, sent = 0, todo = [];
          st.got.forEach(function (n) { have[n] = 1; });
          function size(n) { return Math.min(b.chunk, file.size - n * b.chunk); }
          for (var n = 0; n < b.chunks; n++) { if (have[n]) sent += Math.max(0, size(n)); else todo.push(n); }
          var k = 0, speed = sshMeter(), base = sent, limit = 4, started = 0;
          ps.slow = function () { if (limit > 1) limit--; };       // 出错就降并发：被限流时 1 个也能传，不会一起卡死
          function retryNote() { return ps.lastErr && Date.now() - ps.lastErr.t < 20000 ? '\n⚠ 重试中：' + ps.lastErr.m + '（已自动降低并发）' : ''; }
          function worker(wid) {
            if (ps.stop) return Promise.reject(new Error('已取消'));
            if (k >= todo.length || wid >= limit) return Promise.resolve();
            var n = todo[k++], blob = file.slice(n * b.chunk, Math.min(file.size, (n + 1) * b.chunk));
            return blob.arrayBuffer().then(function (buf) {
              var data = ghB64(buf);
              return sshRetry(ps, function () { return api('ssh_up_chunk', { job: jobId, n: n, data: data }, 3); });
            }).then(function () {
              sent += blob.size;
              ps.set('上传 ' + label + '\n' + sshMB(sent) + ' / ' + sshMB(file.size) + ' · ' + speed(sent - base + 0) + retryNote(), sent / Math.max(1, file.size) * 0.98);
              return worker(wid);
            });
          }
          var ws = []; for (var i = 0; i < 4; i++) ws.push(worker(i));
          return Promise.all(ws);
        });
      }
      return sshResumable(ps, transfer).then(function () {
        ps.set('正在完成 ' + label + '…', 0.99);
        return sshRetry(ps, function () { return api('ssh_up_commit', { job: jobId }); }).then(function () { sshStore(key, null); return 'ok'; });
      });
    }).catch(function (e) {
      if (jobId && !ps.stop) { if (/过期|已结束/.test(e.message)) sshStore(key, null); else { api('ssh_up_cancel', { job: jobId }, 0).catch(function () {}); sshStore(key, null); } }
      throw e;
    });
  }
  function sshPickUpload(id, dir, done) {
    var inp = el('input', { type: 'file', multiple: '', style: 'display:none' });
    document.body.appendChild(inp);
    inp.addEventListener('change', function () {
      var files = Array.prototype.slice.call(inp.files || []); inp.remove();
      if (!files.length) return;
      var ps = sshProgSheet('上传到 ' + dir), okn = 0, i = 0;
      (function next() {
        if (i >= files.length) { ps.close(); toast(okn ? '已上传 ' + okn + ' 个文件' : '没有上传文件'); if (okn) done(); return; }
        sshUploadOne(id, dir, files[i], ps, i + 1, files.length).then(function (r) { if (r === 'ok') okn++; i++; next(); }, function (e) {
          if (ps.stop) { if (okn) done(); return; }
          ps.fail('「' + files[i].name + '」：' + e.message); if (okn) done();
        });
      })();
    });
    inp.click();
    setTimeout(function () { if (inp.parentNode && !inp.files.length) inp.remove(); }, 120000);
  }
  // 下载：手机分块从这台服务器取（服务器边读目标服务器边发），拼好后用系统「分享」存到「文件」。
  // 中断后回到本页面会自动只补取缺的块。注意：文件会先整个放进手机内存，特别大的文件（几百 MB 以上）手机可能撑不住。
  function sshDownload(id, path) { return sshDl('ssh_dl_begin', { id: id, path: path }, 'ssh_dl_chunk', path.split('/').pop()); }
  function sshDl(beginAct, beginParams, chunkAct, shownName) {
    var ps = sshProgSheet('下载 ' + shownName);
    function bytes(b64) { var bin = atob(b64), a = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i); return a; }
    ps.set('准备下载…', 0);
    api(beginAct, beginParams).then(function (r) {
      var total = Math.max(1, Math.ceil(r.size / r.chunk)), parts = [], got = 0;
      ps.onCancel = function () { api(chunkAct, { job: r.job, end: 1 }, 0).catch(function () {}); };
      function transfer() {
        var todo = []; for (var n = 0; n < total; n++) if (!parts[n]) todo.push(n);
        var k = 0, speed = sshMeter(), base = got;
        function worker() {
          if (ps.stop) return Promise.reject(new Error('已取消'));
          if (k >= todo.length) return Promise.resolve();
          var n = todo[k++];
          return sshRetry(ps, function () { return api(chunkAct, { job: r.job, n: n, done: 0 }, 3); }).then(function (c) {
            parts[n] = bytes(c.data); got += parts[n].length;
            ps.set('下载 ' + r.name + '\n' + sshMB(got) + ' / ' + sshMB(r.size) + ' · ' + speed(got), got / Math.max(1, r.size));
            return worker();
          });
        }
        return Promise.all([worker(), worker(), worker(), worker()]);
      }
      return (r.size ? sshResumable(ps, transfer) : Promise.resolve()).then(function () {
        api(chunkAct, { job: r.job, end: 1 }, 0).catch(function () {});
        var file = new File(parts, r.name, { type: 'application/octet-stream' });
        ps.set(sshMB(r.size), 1);
        ps.title('保存 ' + r.name);
        var canShare = false; try { canShare = !!(navigator.canShare && navigator.canShare({ files: [file] })); } catch (e) {}
        var save = el('button', { class: 'btn primary', text: canShare ? '另存为' : '下载' });
        save.addEventListener('click', function () {
          if (canShare) navigator.share({ files: [file] }).then(function () { ps.close(); }, function (e) { if (e && e.name !== 'AbortError') toast('没能打开分享：' + e.message, true); });
          else { var u = URL.createObjectURL(file), a = el('a', { href: u, download: r.name }); document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 60000); ps.close(); }
        });
        ps.acts.appendChild(save);
      });
    }).catch(function (e) { if (!ps.stop) ps.fail(e.message); });
  }

  function sshModeOctal(mode) {
    mode = String(mode || '');
    if (/^[0-7]{3,4}$/.test(mode)) return mode;
    var n = 0;
    for (var i = 0; i < 9; i++) {
      var c = mode.charAt(i), bit = i % 3 === 0 ? 4 : i % 3 === 1 ? 2 : 1;
      if (c !== '-' && c) n |= bit << (6 - Math.floor(i / 3) * 3);
    }
    return ('00' + n.toString(8)).slice(-3);
  }
  function sshChmod(id, path, currentMode, done) {
    sheet('修改权限', [{ key: 'mode', label: '权限（八进制）', value: sshModeOctal(currentMode), ph: '例如：644、755、600' }], '保存', function (v) {
      var mode = (v.mode || '').trim();
      if (!/^[0-7]{3,4}$/.test(mode)) {
        return Promise.reject(new Error('权限格式不正确，请输入 3 或 4 位八进制数字，例如 644、755'));
      }
      return api('ssh_chmod', { id: id, path: path, mode: mode }).then(function () {
        toast('权限已修改'); done();
      });
    });
  }

  function sshRename(id, path, done) {
    var old = path.split('/').pop();
    sheet('重命名', [{ key: 'n', label: '新名字', value: old }], '保存', function (v) {
      var n = (v.n || '').trim(); if (!n || n === old) return Promise.reject(new Error('名字没有变化'));
      return api('ssh_mv', { id: id, path: path, name: n }).then(function () { toast('已重命名'); done(n); });
    });
  }
  function sshDelete(id, path, isDir, done) {
    var n = path.split('/').pop();
    confirmBox(isDir ? '删除文件夹' : '删除文件', '确定删除服务器上的「' + n + '」' + (isDir ? '？里面所有文件都会一起删除，' : '？') + '不能恢复。', function () {
      return api('ssh_rm', { id: id, path: path }).then(function () { toast('已删除'); done(); });
    });
  }

  function pageSshDir(page, arg) {
    var a = gsplit(arg), id = a[0], want = a[1];
    var h1 = top('文件', true); page.appendChild(h1);
    var crumbHold = el('div'); page.appendChild(crumbHold);
    var acts = el('div', { class: 'gh-acts' }), upBtn = el('button', { class: 'btn primary' }, ghIco('up'), el('span', { text: '上传' }));
    var mkBtn = el('button', { class: 'btn', text: '新建文件夹' }), reBtn = el('button', { class: 'btn', text: '刷新' });
    acts.appendChild(upBtn); acts.appendChild(mkBtn); acts.appendChild(reBtn); page.appendChild(acts);
    var card = el('div', { class: 'card gh-card' }, skeleton(4)); page.appendChild(card);
    var path = want || '';
    reBtn.addEventListener('click', function () { go(cur.tab, cur.sub, true); });
    api('ssh_ls', { id: id, path: want }).then(function (d) {
      path = d.path;
      h1.querySelector('h1').textContent = path === '/' ? '/' : path.split('/').pop();
      crumbHold.appendChild(sshCrumbs(id, path));
      upBtn.addEventListener('click', function () { sshPickUpload(id, path, function () { go(cur.tab, cur.sub, true); }); });
      mkBtn.addEventListener('click', function () {
        sheet('新建文件夹', [{ key: 'n', label: '文件夹名字' }], '创建', function (v) {
          return api('ssh_mkdir', { id: id, dir: path, name: (v.n || '').trim() }).then(function () { toast('已创建'); go(cur.tab, cur.sub, true); });
        });
      });
      clear(card);
      if (path !== '/') card.appendChild(el('button', { class: 'gh-row', onclick: function () { go(sTab(), 'sshd:' + id + '|' + sshDirOf(path)); } },
        el('span', { class: 'gh-ri d' }, ghIco('dir')), el('span', { class: 'gh-rt' }, el('b', { text: '..' }), el('small', { text: '上一级' }))));
      if (!d.items.length) card.appendChild(el('div', { class: 'hint', style: 'margin:6px 0', text: '这个文件夹是空的。' }));
      d.items.forEach(function (x) {
        var full = sshPJoin(path, x.name);
        card.appendChild(el('div', { class: 'ssh-r' },
          el(x.dir ? 'button' : 'div', x.dir ? { class: 'gh-row', onclick: function () { go(sTab(), 'sshd:' + id + '|' + full); } } : { class: 'gh-row', style: 'cursor:default' },
            el('span', { class: 'gh-ri ' + (x.dir ? 'd' : 'f') }, ghIco(x.dir ? 'dir' : 'file')),
            el('span', { class: 'gh-rt' }, el('b', { text: x.name }), el('small', { text: (x.dir ? '' : fmtBytes(x.size) + ' · ') + sshAgo(x.mtime) + ' · ' + x.mode + (x.link ? ' · 链接' : '') })),
            x.dir ? goArrow() : null),
          moreBtn('更多', function () { sshMenu(x, full); })));
      });
      if (d.more) card.appendChild(el('div', { class: 'hint', text: '文件太多，只显示了前 3000 个。' }));
    }).catch(function (e) { clear(card).appendChild(failBox(e.message, function () { go(cur.tab, cur.sub, true); })); });
    function sshMenu(x, full) {
      var bg = el('div', { class: 'sheet-bg', onclick: function (e) { if (e.target === bg) bg.remove(); } });
      function close() { bg.remove(); }
      var box = el('div', { class: 'sheet' }, el('h2', { text: x.name }), el('div', { class: 'hint', text: full }));
      if (!x.dir && x.size <= 512 * 1024) box.appendChild(el('button', { class: 'btn block', style: 'margin-top:10px', text: '编辑', onclick: function () { close(); sshAutoEdit = full; go(sTab(), 'sshf:' + id + '|' + full); } }));
      box.appendChild(el('button', { class: 'btn block', style: 'margin-top:' + (!x.dir && x.size <= 512 * 1024 ? 8 : 10) + 'px', text: '修改权限', onclick: function () { close(); sshChmod(id, full, x.modeOctal || x.mode, function () { go(cur.tab, cur.sub, true); }); } }));
      if (!x.dir) box.appendChild(el('button', { class: 'btn block', style: 'margin-top:8px', text: '下载', onclick: function () { close(); sshDownload(id, full); } }));
      box.appendChild(el('button', { class: 'btn block', style: 'margin-top:8px', text: '重命名', onclick: function () { close(); sshRename(id, full, function () { go(cur.tab, cur.sub, true); }); } }));
      box.appendChild(el('button', { class: 'btn block', style: 'margin-top:8px', text: '复制路径', onclick: function () { close(); copy(full); } }));
      box.appendChild(el('button', { class: 'btn danger block', style: 'margin-top:8px', text: '删除', onclick: function () { close(); sshDelete(id, full, x.dir, function () { go(cur.tab, cur.sub, true); }); } }));
      box.appendChild(el('div', { class: 'acts' }, el('button', { class: 'btn', text: '取消', onclick: close })));
      bg.appendChild(box); document.body.appendChild(bg);
    }
  }

  function pageSshFile(page, arg) {
    var a = gsplit(arg), id = a[0], path = a[1], name = path.split('/').pop(), dir = sshDirOf(path);
    page.appendChild(top(name, true));
    page.appendChild(sshCrumbs(id, path, true));
    var acts = el('div', { class: 'gh-acts' }); page.appendChild(acts);
    var card = el('div', { class: 'card gh-view' }, skeleton(4)); page.appendChild(card);
    var auto = sshAutoEdit === path; sshAutoEdit = '';
    function finish() { if (auto) backHandler(); else go(cur.tab, cur.sub, true); }
    function edit(f) {
      var crlf = /\r\n/.test(f.text), orig = f.text.replace(/\r\n/g, '\n');
      var ta = el('textarea', { class: 'gh-edit mono', spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off', autocorrect: 'off', wrap: 'off' });
      ta.value = orig;
      var save = el('button', { class: 'btn primary', text: '保存' }), cancel = el('button', { class: 'btn', text: '取消' });
      acts.style.display = 'none';
      clear(card).appendChild(el('div', { class: 'gh-edbar' }, el('span', { class: 'hint', style: 'margin:0', text: '编辑 ' + name }), el('span', { class: 'sp' }), cancel, save)); card.appendChild(ta);
      function fit() { ta.style.height = 'auto'; ta.style.height = Math.max(240, ta.scrollHeight + 4) + 'px'; }
      ta.addEventListener('input', fit); setTimeout(fit, 0);
      cancel.addEventListener('click', function () {
        if (ta.value === orig) { finish(); return; }
        confirmBox('放弃修改', '改过的内容不会保存。', function () { finish(); return Promise.resolve(); });
      });
      save.addEventListener('click', function () {
        if (ta.value === orig) { toast('没有改动'); return; }
        run(save, function () {
          var text = crlf ? ta.value.replace(/\n/g, '\r\n') : ta.value;
          return api('ssh_read', { id: id, path: path }).then(function (now) {
            if (now.mtime && f.mtime && now.mtime !== f.mtime && !window.confirm('这个文件在你打开之后被改过了，继续保存会覆盖那次修改。继续吗？')) throw new Error('已取消保存');
            return api('ssh_write', { id: id, path: path, text: text });
          }).then(function () { toast('已保存'); finish(); });
        });
      });
    }
    api('ssh_read', { id: id, path: path }).then(function (f) {
      clear(card);
      card.appendChild(el('div', { class: 'gh-meta' }, el('span', { text: fmtBytes(f.size) }), f.text != null ? el('span', { text: (f.text ? f.text.replace(/\n$/, '').split('\n').length : 0) + ' 行' }) : null));
      if (f.text != null && f.editable) acts.appendChild(el('button', { class: 'btn primary', text: '编辑', onclick: function () { edit(f); } }));
      if (auto && f.text != null && f.editable) edit(f);
      if (f.text != null) acts.appendChild(el('button', { class: 'btn', text: '复制内容', onclick: function () { copy(f.text); } }));
      acts.appendChild(el('button', { class: 'btn', onclick: function () { sshDownload(id, path); } }, ghIco('down'), el('span', { text: '下载' })));
      acts.appendChild(el('button', { class: 'btn', text: '重命名', onclick: function () { sshRename(id, path, function (n) { go(sTab(), 'sshf:' + id + '|' + sshPJoin(dir, n), true); }); } }));
      acts.appendChild(el('button', { class: 'btn danger', text: '删除', onclick: function () { sshDelete(id, path, false, function () { backHandler(); }); } }));
      if (f.big) { card.appendChild(el('div', { class: 'hint', text: '文件超过 512KB，这里不显示内容，可以点「下载」保存到手机。' })); return; }
      if (f.binary) { card.appendChild(el('div', { class: 'hint', text: '这是二进制文件，不能直接显示，可以点「下载」保存到手机。' })); return; }
      if (!f.editable) card.appendChild(el('div', { class: 'hint', text: '文件超过 40KB，只能查看；要修改请下载后改好再上传覆盖。' }));
      card.appendChild(ghCode(f.text));
    }).catch(function (e) { clear(card).appendChild(failBox(e.message, function () { go(cur.tab, cur.sub, true); })); });
  }

  // ============================================================ 文档：随手记，每篇分次添加文字，点一下就复制
  var docsData = null, docsOpen = {};
  try { docsOpen = JSON.parse(localStorage.getItem('mini_docs_open') || '{}') || {}; } catch (e) { docsOpen = {}; }
  function docsSaveOpen() { try { localStorage.setItem('mini_docs_open', JSON.stringify(docsOpen)); } catch (e) {} }
  function docAgo(ts) {
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return '刚刚';
    if (s < 3600) return Math.floor(s / 60) + ' 分钟前';
    if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
    if (s < 86400 * 30) return Math.floor(s / 86400) + ' 天前';
    return timeText(ts).split(' ')[0];
  }
  function pageDocs(page) {
    var hd = top('文档');
    hd.appendChild(el('button', { class: 'doc-new2', 'aria-label': '新建文档', text: '＋', onclick: function () {
      sheet('新建文档', [{ key: 't', label: '文档标题', ph: '比如：服务器密码' }], '新建', function (v) {
        var t = (v.t || '').trim(); if (!t) return Promise.reject(new Error('先写个标题'));
        return api('docs_edit', { op: 'add', title: t }).then(function (d) { docsData = d.docs; docsOpen[docsData[0].id] = 1; docsSaveOpen(); render(); });
      });
    } }));
    page.appendChild(hd);
    var search = el('input', { class: 'input doc-search', placeholder: '🔍 搜索', autocomplete: 'off' });
    var listBox = el('div', { class: 'doc-list' });
    page.appendChild(search); page.appendChild(listBox);
    if (docsData) render(); else listBox.appendChild(skeleton(3));
    function edit(p) { return api('docs_edit', p).then(function (d) { docsData = d.docs; render(); }); }
    search.addEventListener('input', render);
    function render() {
      clear(listBox);
      var q = search.value.trim().toLowerCase();
      search.style.display = docsData.length > 5 ? '' : 'none';
      var shown = docsData.filter(function (d) {
        return !q || d.title.toLowerCase().indexOf(q) >= 0 || d.items.some(function (it) { return (it.text + ' ' + (it.label || '')).toLowerCase().indexOf(q) >= 0; });
      });
      if (!docsData.length) listBox.appendChild(el('div', { class: 'card empty' }, el('span', { class: 'big wi' }, icon('docs')), '还没有文档，点右上角「＋」新建一篇。'));
      else if (!shown.length) listBox.appendChild(el('div', { class: 'hint', style: 'text-align:center;margin:20px 0', text: '没有找到匹配的文档' }));
      shown.forEach(function (d) { listBox.appendChild(docCard(d, q)); });
    }
    function addItem(d) {
      sheet('添加到「' + d.title + '」', [{ key: 'l', label: '小标题（可选）', ph: '比如：git 令牌' }, { key: 'x', label: '内容（点一下复制的就是这里的文字）', multi: true }], '添加', function (v) {
        if (!(v.x || '').trim()) return Promise.reject(new Error('先输入内容'));
        return edit({ op: 'item_add', id: d.id, text: v.x, label: v.l }).then(function () { docsOpen[d.id] = 1; docsSaveOpen(); render(); });
      });
    }
    function docMenu(d) {
      var bg = el('div', { class: 'sheet-bg', onclick: function (e) { if (e.target === bg) bg.remove(); } });
      function close() { bg.remove(); }
      bg.appendChild(el('div', { class: 'sheet' }, el('h2', { text: d.title }),
        el('button', { class: 'btn block', style: 'margin-top:6px', text: '改标题', onclick: function () { close(); sheet('改标题', [{ key: 't', label: '标题', value: d.title }], '保存', function (v) { return edit({ op: 'ren', id: d.id, title: v.t }); }); } }),
        el('button', { class: 'btn block', style: 'margin-top:8px', text: '移到最上面', onclick: function () { close(); run(null, function () { return edit({ op: 'top', id: d.id }); }); } }),
        el('button', { class: 'btn danger block', style: 'margin-top:8px', text: '删除文档', onclick: function () { close(); confirmBox('删除文档', '「' + d.title + '」和里面的 ' + d.items.length + ' 条内容会一起删除，不能恢复。', function () { return edit({ op: 'del', id: d.id }); }); } }),
        el('div', { class: 'acts' }, el('button', { class: 'btn', text: '取消', onclick: close }))));
      document.body.appendChild(bg);
    }
    function docCard(d, q) {
      var open = !!docsOpen[d.id] || !!q;
      var card = el('div', { class: 'card doc' + (open ? ' open' : '') });
      var main = el('button', { class: 'doc-hm', onclick: function () {
        open = !open; card.classList.toggle('open', open);
        if (open) docsOpen[d.id] = 1; else delete docsOpen[d.id];
        docsSaveOpen();
      } },
        el('span', { class: 'doc-ic' }, icon('docs')),
        el('span', { class: 'doc-t' }, el('b', { text: d.title }), el('small', { text: d.items.length + ' 条 · ' + docAgo(d.t) })),
        el('span', { class: 'doc-arr', text: '›' }));
      var head = el('div', { class: 'doc-h' }, main,
        el('button', { class: 'doc-ib', 'aria-label': '添加内容', text: '＋', onclick: function () { addItem(d); } }),
        el('button', { class: 'doc-ib', 'aria-label': '文档设置', text: '⋯', onclick: function () { docMenu(d); } }));
      var body = el('div', { class: 'doc-b' });
      var inner = el('div', { class: 'doc-in' });
      body.appendChild(inner);
      if (!d.items.length) inner.appendChild(el('div', { class: 'hint', style: 'margin:10px 0 8px', text: '还没有内容，点右上角「＋」添加。' }));
      d.items.forEach(function (it) {
        var hit = q && (it.text + ' ' + (it.label || '')).toLowerCase().indexOf(q) >= 0;
        var row = el('div', { class: 'doc-it nocopy' + (hit ? ' hit' : '') },
          el('button', { class: 'doc-tx', title: '点一下复制', onclick: function () { copy(it.text); row.classList.add('copied'); setTimeout(function () { row.classList.remove('copied'); }, 900); } },
            it.label ? el('span', { class: 'doc-lb', text: it.label }) : null, el('span', { class: 'doc-v', text: it.text })),
          el('button', { class: 'doc-more', text: '⋯', 'aria-label': '更多', onclick: function () { itemMenu(d, it); } }));
        inner.appendChild(row);
      });
      card.appendChild(head); card.appendChild(body);
      return card;
    }
    function itemMenu(d, it) {
      var bg = el('div', { class: 'sheet-bg', onclick: function (e) { if (e.target === bg) bg.remove(); } });
      function close() { bg.remove(); }
      bg.appendChild(el('div', { class: 'sheet' }, el('h2', { text: it.label || d.title }),
        el('div', { class: 'doc-prev', text: it.text.length > 200 ? it.text.slice(0, 200) + '…' : it.text }),
        el('div', { class: 'hint', style: 'margin:6px 0 4px', text: '添加于 ' + timeText(it.t) }),
        el('button', { class: 'btn block', style: 'margin-top:10px', text: '复制', onclick: function () { copy(it.text); close(); } }),
        el('button', { class: 'btn block', style: 'margin-top:8px', text: '编辑', onclick: function () {
          close();
          sheet('编辑', [{ key: 'l', label: '小标题（可选）', value: it.label || '' }, { key: 'x', label: '内容', value: it.text, multi: true }], '保存', function (v) { return edit({ op: 'item_edit', id: d.id, iid: it.id, text: v.x, label: v.l }); });
        } }),
        el('button', { class: 'btn danger block', style: 'margin-top:8px', text: '删除这条', onclick: function () {
          close();
          confirmBox('删除这条', '删除后不能恢复。', function () { return edit({ op: 'item_del', id: d.id, iid: it.id }); });
        } }),
        el('div', { class: 'acts' }, el('button', { class: 'btn', text: '取消', onclick: close }))));
      document.body.appendChild(bg);
    }
    api('docs').then(function (d) { docsData = d.docs; render(); }).catch(function (e) { if (!docsData) clear(listBox).appendChild(failBox(e.message, function () { go('docs', null, true); })); });
  }

  // ============================================================ 首页快捷入口：GitHub / Cloudflare 独立子页面（只有所有者能用）
  // GitHub 的子页面在首页和管理页都能打开：在哪个标签页点进去，就留在哪个标签页
  function gTab() { return cur.tab === 'home' || cur.tab === 'tools' ? cur.tab : 'admin'; }
  function gitRoute(page, sub) {
    if (sub === 'git') return pageGitRepos(page);
    if (sub === 'gittok') return pageGitTok(page);
    var i = sub.indexOf(':');
    var fn = i > 0 ? { gitr: pageGitDir, gitf: pageGitFile, gitc: pageGitCommits, gitcd: pageGitCommit }[sub.slice(0, i)] : null;
    return fn ? fn(page, sub.slice(i + 1)) : pageGitRepos(page);
  }
  function hubStat(label, val, cls) {
    return el('div', { class: 'hub-st' }, el('b', { class: cls || '', text: val }), el('span', { text: label }));
  }
  function hubAct(ic, label, fn) {
    return el('button', { class: 'card hub-act', onclick: fn }, el('span', { class: 'hub-ai' }, ic), el('span', { text: label }));
  }

  function pageGhHub(page) {
    page.appendChild(top('GitHub', true));
    var hero = el('div', { class: 'card hub-hero' }, skeleton(1));
    var acts = el('div', { class: 'grid g3 hub-acts' });
    var list = el('div', { class: 'card gh-card' }, skeleton(3));
    page.appendChild(hero); page.appendChild(acts); page.appendChild(list);
    acts.appendChild(hubAct(ghIco('git'), '新建仓库', ghNewRepo));
    acts.appendChild(hubAct(ghIco('lock'), '令牌设置', function () { go(gTab(), 'gittok'); }));
    acts.appendChild(hubAct(icon('net'), '打开网站', function () { openLink('https://github.com/'); }));
    api('gh').then(function (g) {
      clear(hero);
      if (!g.set || g.error) {
        hero.appendChild(el('div', { class: 'hint', style: 'margin:0', text: g.error || '还没有设置 GitHub 令牌。设置后就能在这里管理仓库：上传 / 删除 / 编辑文件、看提交记录、新建仓库。' }));
        hero.appendChild(el('button', { class: 'btn primary block', style: 'margin-top:12px', text: g.set ? '检查令牌' : '设置 GitHub 令牌', onclick: function () { go(gTab(), 'gittok'); } }));
        list.remove(); return;
      }
      var av = el('span', { class: 'hub-av', text: (g.login || '?').charAt(0).toUpperCase() });
      if (g.avatar) { var im = el('img', { src: g.avatar + (g.avatar.indexOf('?') < 0 ? '?' : '&') + 's=120', alt: '', referrerpolicy: 'no-referrer' }); im.onload = function () { clear(av).appendChild(im); }; }
      var stats = el('div', { class: 'hub-sts' }, hubStat('仓库', '…'), hubStat('私有', '…'), hubStat('最近推送', '…'));
      hero.appendChild(el('div', { class: 'hub-id' }, av,
        el('div', { class: 'hub-nm' }, el('b', { text: g.name || g.login }), el('small', { text: '@' + g.login })),
        el('span', { class: 'pill ok' }, el('i', { class: 'dot' }), '已连接')));
      hero.appendChild(stats);
      api('gh_repos', { per: 100 }).then(function (r) {
        var items = r.items, priv = items.filter(function (x) { return x.private; }).length;
        clear(stats);
        stats.appendChild(hubStat('仓库', items.length + (r.more ? '+' : '')));
        stats.appendChild(hubStat('私有', String(priv)));
        stats.appendChild(hubStat('最近推送', items[0] ? ghAgo(items[0].pushed).replace(' ', '') : '—'));
        clear(list);
        var search = el('input', { class: 'input', placeholder: '🔍 搜索仓库', style: 'margin-bottom:4px' });
        var rows = el('div');
        list.appendChild(el('h3', { class: 'h3i' }, ghIco('git'), el('span', { text: '仓库' }), el('span', { class: 'sp' }), el('span', { class: 'hub-cnt', text: items.length + ' 个' })));
        if (items.length > 6) list.appendChild(search);
        list.appendChild(rows);
        function render() {
          clear(rows);
          var q = search.value.trim().toLowerCase();
          var show = items.filter(function (x) { return !q || (x.name + ' ' + x.desc).toLowerCase().indexOf(q) >= 0; });
          if (!show.length) rows.appendChild(el('div', { class: 'hint', style: 'margin:8px 0', text: q ? '没有匹配的仓库' : '还没有仓库，点上面「新建仓库」建一个。' }));
          show.forEach(function (x) { rows.appendChild(ghRepoRow(x)); });
        }
        search.addEventListener('input', render); render();
      }).catch(function (e) { clear(list).appendChild(el('div', { class: 'hint', text: e.message })); });
    }).catch(function (e) { clear(hero).appendChild(failBox(e.message, function () { go(cur.tab, cur.sub, true); })); list.remove(); });
  }

  function pageCfHub(page) {
    page.appendChild(top('Cloudflare', true));
    var stats = el('div', { class: 'card hub-hero' }, el('div', { class: 'hub-sts' }, hubStat('域名', '…'), hubStat('30 天内到期', '…'), hubStat('R2 已用', '…')));
    var acts = el('div', { class: 'grid g3 hub-acts' });
    var zc = el('div', { class: 'card gh-card' }, skeleton(3));
    var rc = el('div', { class: 'card gh-card' }, skeleton(2));
    page.appendChild(stats); page.appendChild(acts); page.appendChild(zc); page.appendChild(rc);
    acts.appendChild(hubAct(icon('dns'), '域名解析', function () { go('dns', 'dom'); }));
    acts.appendChild(hubAct(icon('bucket'), 'R2 存储', function () { go('dns', 'r2'); }));
    acts.appendChild(hubAct(el('span', { class: 'hub-tx', text: '⇄' }), '换 IP', function () { swapSheet(null); }));
    var st = stats.firstChild;
    function setStat(i, v, cls) { var b = st.children[i].firstChild; b.textContent = v; b.className = cls || ''; }
    api('cf').then(function (d) {
      dnsZones = d.zones;
      var soon = d.zones.filter(function (z) { return z.exp && (z.exp * 1000 - Date.now()) < 30 * 86400000; }).length;
      setStat(0, String(d.zones.length)); setStat(1, String(soon), soon ? 'warn' : '');
      clear(zc);
      zc.appendChild(el('h3', { class: 'h3i' }, icon('dns'), el('span', { text: '域名' }), el('span', { class: 'sp' }), el('span', { class: 'hub-cnt', text: d.zones.length + ' 个' })));
      if (!d.zones.length) zc.appendChild(el('div', { class: 'hint', style: 'margin:8px 0', text: '这个 Token 下没有域名' }));
      d.zones.forEach(function (z) {
        var ex = expInfo(z);
        zc.appendChild(el('button', { class: 'gh-row', onclick: function () { go('dns', 'z:' + z.id); } },
          el('span', { class: 'gh-ri d' }, icon('dns')),
          el('span', { class: 'gh-rt' }, el('b', { text: z.name }), el('small', { class: ex.c, text: ex.t })),
          el('span', { class: 'pill ' + (z.status === 'active' && !z.paused ? 'ok' : 'warn'), text: zoneStatus(z) }),
          goArrow()));
      });
    }).catch(function (e) { setStat(0, '—'); setStat(1, '—'); clear(zc).appendChild(el('div', { class: 'hint', text: e.message })); });
    function drawR2(x) {
      var size = 0, known = x.buckets.some(function (b) { return b.objects != null; });
      x.buckets.forEach(function (b) { size += b.size || 0; });
      setStat(2, known ? fmtBytes(size).replace(' ', '') : '—');
      clear(rc);
      rc.appendChild(el('h3', { class: 'h3i' }, icon('bucket'), el('span', { text: 'R2 存储桶' }), el('span', { class: 'sp' }),
        el('button', { class: 'btn sm', text: '全部 ›', onclick: function () { go('dns', 'r2'); } })));
      if (!x.buckets.length) rc.appendChild(el('div', { class: 'hint', style: 'margin:8px 0', text: '还没有存储桶' }));
      x.buckets.slice(0, 4).forEach(function (b) {
        rc.appendChild(el('button', { class: 'gh-row', onclick: function () { go('dns', 'r2b:' + b.name + ':'); } },
          el('span', { class: 'gh-ri' }, icon('bucket')),
          el('span', { class: 'gh-rt' }, el('b', { text: b.name }), el('small', { text: r2Usage(b) + ' · ' + r2LocText(b.location) })),
          el('span', { class: 'pill ' + (b.public || b.domains ? 'ok' : ''), text: b.public || b.domains ? '公开' : '私有' }),
          goArrow()));
      });
    }
    if (r2Data) drawR2(r2Data);
    api('r2').then(function (x) { r2Save(x); drawR2(x); }).catch(function (e) { if (!r2Data) { setStat(2, '—'); clear(rc).appendChild(el('div', { class: 'hint', text: 'R2：' + e.message })); } });
  }

  // ============================================================ GitHub 仓库管理（只有所有者能用）
  // 子页面：git 仓库列表 / gittok 令牌 / gitr:仓库|路径 文件夹 / gitf:仓库|路径 文件 / gitc:仓库|路径 提交记录 / gitcd:仓库|提交编号 改动
  function gsplit(s) { var i = s.indexOf('|'); return i < 0 ? [s, ''] : [s.slice(0, i), s.slice(i + 1)]; }
  function ghAgo(iso) {
    var t = Date.parse(iso); if (!t) return '';
    var s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return '刚刚';
    if (s < 3600) return Math.floor(s / 60) + ' 分钟前';
    if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
    if (s < 86400 * 30) return Math.floor(s / 86400) + ' 天前';
    var d = new Date(t); return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate();
  }
  function ghIco(kind) {
    var P = {
      git: '<circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="8" r="2.2"/><path d="M6 8.2v7.6M18 10.2c0 4-6 3.2-11 6"/>',
      dir: '<path d="M3 7.5a1.5 1.5 0 0 1 1.5-1.5h4.2l2 2h8.8a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>',
      file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
      lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
      up: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
      down: '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
      hist: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'
    };
    var s = el('span'); s.innerHTML = '<svg viewBox="0 0 24 24">' + P[kind] + '</svg>'; return s.firstChild;
  }
  // GitHub 已经把 README / Markdown 排好版并过滤过；这里再去一遍脚本和事件属性，链接一律新窗口打开
  function ghHtml(html) {
    var box = el('div', { class: 'gh-md' });
    try {
      var doc = new DOMParser().parseFromString('<div>' + html + '</div>', 'text/html');
      Array.prototype.forEach.call(doc.querySelectorAll('script,style,iframe,object,embed,form,link,meta'), function (n) { n.remove(); });
      Array.prototype.forEach.call(doc.querySelectorAll('*'), function (n) {
        Array.prototype.slice.call(n.attributes).forEach(function (a) {
          if (/^on/i.test(a.name) || (/^(href|src)$/i.test(a.name) && /^\s*javascript:/i.test(a.value))) n.removeAttribute(a.name);
        });
        if (n.tagName === 'A') n.addEventListener('click', function (e) { var h = n.getAttribute('href'); if (h && /^https?:/i.test(h)) { e.preventDefault(); openLink(h); } else e.preventDefault(); });
      });
      var root0 = doc.body.firstChild;
      while (root0 && root0.firstChild) box.appendChild(root0.firstChild);
      // 代码块右上角加「复制」按钮（README 里的安装命令、地址一点就复制）
      Array.prototype.forEach.call(box.querySelectorAll('pre'), function (pre) {
        var wrap = el('div', { class: 'gh-pre' });
        pre.parentNode.insertBefore(wrap, pre); wrap.appendChild(pre);
        wrap.appendChild(el('button', { class: 'gh-cp', text: '复制', onclick: function (e) { e.stopPropagation(); copy(pre.textContent.replace(/\n+$/, '')); } }));
      });
      // GitHub 有时把代码块包在 div.highlight 里，外层的 .snippet-clipboard-content 自带的按钮用不了，去掉
      Array.prototype.forEach.call(box.querySelectorAll('clipboard-copy, .zeroclipboard-container'), function (n) { n.remove(); });
    } catch (e) { box.textContent = '（无法显示）'; }
    box.classList.add('nocopy');
    return box;
  }

  // 管理页上的 GitHub 卡片：最近更新的几个仓库
  function gitCard() {
    var body = el('div', null, el('div', { class: 'hint', text: '读取中…' }));
    var head = el('h3', { class: 'h3i' }, ghIco('git'), el('span', { text: 'GitHub 仓库' }), el('span', { class: 'sp' }));
    var card = el('div', { class: 'card gh-card' }, head, body);
    api('gh').then(function (g) {
      clear(body);
      if (!g.set) {
        body.appendChild(el('div', { class: 'hint', style: 'margin-top:0', text: '设置一个 GitHub 令牌，就能在这里管理仓库：上传 / 删除文件、看提交记录、新建仓库，不用再登录 GitHub 网站。' }));
        body.appendChild(el('button', { class: 'btn primary block', style: 'margin-top:12px', text: '设置 GitHub 令牌', onclick: function () { go(gTab(), 'gittok'); } }));
        return;
      }
      if (g.error) {
        body.appendChild(el('div', { class: 'hint', style: 'margin-top:0', text: g.error }));
        body.appendChild(el('button', { class: 'btn block', style: 'margin-top:12px', text: '检查令牌', onclick: function () { go(gTab(), 'gittok'); } }));
        return;
      }
      head.appendChild(el('button', { class: 'btn sm', text: '全部 ›', onclick: function () { go(gTab(), 'git'); } }));
      var list = el('div', { class: 'gh-list' }, el('div', { class: 'hint', text: '读取中…' }));
      body.appendChild(list);
      api('gh_repos', { per: 4 }).then(function (r) {
        clear(list);
        if (!r.items.length) list.appendChild(el('div', { class: 'hint', text: '还没有仓库' }));
        r.items.slice(0, 4).forEach(function (x) { list.appendChild(ghRepoRow(x)); });
        list.appendChild(el('div', { class: 'gh-foot' },
          el('span', { class: 'gh-who', text: '@' + g.login }),
          el('button', { class: 'btn sm', text: '＋ 新建仓库', onclick: ghNewRepo })));
      }).catch(function (e) { clear(list).appendChild(el('div', { class: 'hint', text: e.message })); });
    }).catch(function (e) { clear(body).appendChild(el('div', { class: 'hint', text: e.message })); });
    return card;
  }
  function ghRepoRow(x) {
    return el('button', { class: 'gh-row', onclick: function () { go(gTab(), 'gitr:' + x.full + '|'); } },
      el('span', { class: 'gh-ri' }, ghIco(x.private ? 'lock' : 'git')),
      el('span', { class: 'gh-rt' }, el('b', { text: x.name }), el('small', { text: [x.private ? '私有' : '公开', x.lang, ghAgo(x.pushed) + '更新'].filter(Boolean).join(' · ') })),
      goArrow());
  }
  function ghNewRepo() {
    sheet('新建仓库', [
      { key: 'name', label: '仓库名（英文、数字、- _ .）', ph: 'my-project' },
      { key: 'desc', label: '简介（可选）' },
      { key: 'private', check: '私有仓库（只有你能看到）', value: true }
    ], '创建', function (v) {
      return api('gh_repo_new', { name: v.name.trim(), desc: v.desc, private: v.private }).then(function (r) { toast('已创建 ' + r.name); go(gTab(), 'gitr:' + r.full + '|'); });
    });
  }

  function pageGitTok(page) {
    page.appendChild(top('GitHub 令牌', true));
    var card = el('div', { class: 'card' }, el('div', { class: 'hint', text: '读取中…' }));
    page.appendChild(card);
    function draw(g) {
      clear(card);
      if (g.set && !g.error) {
        card.appendChild(el('div', { class: 'kv' }, el('span', { text: '账号' }), el('span', { text: '@' + g.login + (g.name ? '（' + g.name + '）' : '') })));
        card.appendChild(el('div', { class: 'kv' }, el('span', { text: '令牌' }), el('span', { class: 'mono', text: '••••' + (g.tail || '') })));
        if (g.classic) card.appendChild(el('div', { class: 'kv' }, el('span', { text: '权限' }), el('span', { text: g.scopes.join(', ') || '无' })));
        if (g.classic && g.scopes.indexOf('repo') < 0) card.appendChild(el('div', { class: 'hint', style: 'color:var(--warn)', text: '没有勾选 repo 权限：私有仓库看不到，也不能上传。' }));
        if (g.classic && g.scopes.indexOf('delete_repo') < 0) card.appendChild(el('div', { class: 'hint', text: '没有勾选 delete_repo：不能在这里删除仓库（其他功能不受影响）。' }));
      } else if (g.error) card.appendChild(el('div', { class: 'hint', style: 'margin-top:0;color:var(--bad)', text: g.error }));
      if (g.env) { card.appendChild(el('div', { class: 'hint', text: '令牌是用服务器环境变量 GITHUB_TOKEN 设置的，要在服务器上修改。' })); return; }
      var inp = el('input', { class: 'input mono', placeholder: g.set ? '粘贴新令牌可替换' : 'ghp_ 开头的令牌', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', style: 'margin-top:12px' });
      var save = el('button', { class: 'btn primary block', style: 'margin-top:10px', text: g.set ? '替换令牌' : '保存令牌' });
      save.addEventListener('click', function () {
        var t = inp.value.trim(); if (!t) { toast('请先粘贴令牌', true); return; }
        run(save, function () { return api('gh_token', { token: t }).then(function (d) { toast('已保存'); draw(d); }); });
      });
      card.appendChild(inp); card.appendChild(save);
      if (g.set) card.appendChild(el('button', { class: 'btn danger block', style: 'margin-top:10px', text: '删除令牌', onclick: function () {
        confirmBox('删除令牌', '删除后这里就不能再管理 GitHub，仓库本身不受影响。', function () { return api('gh_token', { op: 'del' }).then(draw); });
      } }));
      card.appendChild(el('div', { class: 'gh-steps' },
        el('b', { text: '怎么获取令牌' }),
        el('ol', null,
          el('li', { text: '电脑或手机浏览器登录 GitHub，打开 Settings → Developer settings → Personal access tokens → Tokens (classic)。' }),
          el('li', { text: '点 Generate new token (classic)，Expiration 选 No expiration（或自己定一个期限）。' }),
          el('li', { text: '勾选 repo 和 delete_repo 两项，生成后把 ghp_ 开头的令牌复制过来粘贴保存。' })),
        el('div', { class: 'hint', text: '令牌只保存在你自己的服务器上，不会发到别处。不想用了，在 GitHub 上删除这个令牌就立刻失效。' })));
    }
    api('gh', { force: 1 }).then(draw).catch(function (e) { clear(card).appendChild(failBox(e.message, function () { go(cur.tab, cur.sub, true); })); });
  }

  function pageGitRepos(page) {
    page.appendChild(top('GitHub 仓库', true));
    var bar = el('div', { class: 'row', style: 'margin-bottom:10px' });
    var search = el('input', { class: 'input', placeholder: '🔍 搜索仓库' });
    bar.appendChild(search);
    bar.appendChild(el('button', { class: 'btn', text: '＋ 新建', onclick: ghNewRepo }));
    page.appendChild(bar);
    var card = el('div', { class: 'card gh-card' }, skeleton(3)); page.appendChild(card);
    var all = [], pg = 1, more = false;
    var foot = el('div', { class: 'gh-foot' });
    function render() {
      clear(card);
      var q = search.value.trim().toLowerCase();
      var rows = all.filter(function (x) { return !q || (x.name + ' ' + x.desc).toLowerCase().indexOf(q) >= 0; });
      if (!rows.length) card.appendChild(el('div', { class: 'hint', style: 'margin:6px 0', text: q ? '没有匹配的仓库' : '还没有仓库' }));
      rows.forEach(function (x) { card.appendChild(ghRepoRow(x)); });
      clear(foot);
      foot.appendChild(el('button', { class: 'btn sm', text: '令牌设置', onclick: function () { go(gTab(), 'gittok'); } }));
      if (more) foot.appendChild(el('button', { class: 'btn sm', text: '加载更多', onclick: function (e) { var b = e.currentTarget; run(b, load); } }));
      card.appendChild(foot);
    }
    function load() { return api('gh_repos', { page: pg }).then(function (r) { all = all.concat(r.items); more = r.more; pg++; render(); }); }
    search.addEventListener('input', render);
    load().catch(function (e) { clear(card).appendChild(failBox(e.message, function () { go(cur.tab, cur.sub, true); })); });
  }

  // 面包屑：仓库名 / 文件夹 / 文件夹，点哪一级就回到哪一级
  function ghCrumbs(repo, path, isFile) {
    var box = el('div', { class: 'gh-crumbs' });
    box.appendChild(el('button', { text: repo.split('/')[1], onclick: function () { go(gTab(), 'gitr:' + repo + '|'); } }));
    var parts = path ? path.split('/') : [];
    parts.forEach(function (p, i) {
      box.appendChild(el('span', { text: '/' }));
      var to = parts.slice(0, i + 1).join('/');
      var last = i === parts.length - 1;
      box.appendChild(el('button', { class: last ? 'on' : '', text: p, onclick: last ? null : function () { go(gTab(), 'gitr:' + repo + '|' + to); } }));
    });
    return box;
  }

  function pageGitDir(page, arg) {
    var a = gsplit(arg), repo = a[0], path = a[1];
    page.appendChild(top(path ? path.split('/').pop() : repo.split('/')[1], true));
    page.appendChild(ghCrumbs(repo, path));
    if (!path) {   // 仓库首页：显示 Git 地址，点一下复制
      var urls = [['HTTPS', 'https://github.com/' + repo + '.git'], ['SSH', 'git@github.com:' + repo + '.git']];
      page.appendChild(el('div', { class: 'card gh-clone' }, urls.map(function (u) {
        return el('button', { class: 'gh-url', onclick: function () { copy(u[1]); } },
          el('span', { class: 'gh-ut', text: u[0] }), el('span', { class: 'mono gh-uv', text: u[1] }), el('span', { class: 'gh-uc', text: '复制' }));
      })));
    }
    var acts = el('div', { class: 'gh-acts' });
    var upBtn = el('button', { class: 'btn primary' }, ghIco('up'), el('span', { text: '上传' }));
    var histBtn = el('button', { class: 'btn' }, ghIco('hist'), el('span', { text: '记录' }));
    var selBtn = el('button', { class: 'btn', text: '选择' });
    acts.appendChild(upBtn);
    acts.appendChild(el('button', { class: 'btn', onclick: function () {
      sheet('新建文件夹', [
        { key: 'name', label: '文件夹名（在「' + (path || '仓库根目录') + '」里创建；写 a/b 可以一次建多层）', ph: '例如 web' },
        { key: 'msg', label: '提交说明（可选）', ph: '新建文件夹' }
      ], '创建', function (v) {
        var nm = (v.name || '').trim().replace(/^\/+|\/+$/g, '');
        if (!nm) return Promise.reject(new Error('先填文件夹名'));
        return api('gh_mkdir', { repo: repo, dir: path, name: nm, msg: v.msg }).then(function () { toast('已创建'); go(cur.tab, cur.sub, true); });
      }, el('div', { class: 'hint', style: 'margin:-2px 0 8px', text: 'Git 不能保存空文件夹，所以会在里面放一个空的 .gitkeep 占位文件。往里面传了真正的文件之后，可以把 .gitkeep 删掉。' }));
    } }, ghIco('dir'), el('span', { text: '新建文件夹' })));
    if (!path) acts.appendChild(el('button', { class: 'btn', onclick: function (e) { ghZip(e.currentTarget); } }, ghIco('down'), el('span', { text: '下载' })));
    acts.appendChild(histBtn); acts.appendChild(selBtn);
    // 整个仓库打包下载：服务器先从 GitHub 取回 ZIP，手机再分块取过来，最后用系统「分享」存到「文件」里你选的目录
    // （手机直接打开 codeload.github.com 在国内经常卡住；保存按钮要你手指点一下，iPhone 才允许弹出分享）
    function ghZip(btn) {
      var stop = false, prog = el('div', { class: 'gh-prog', style: 'display:block' }, el('i')), stat = el('div', { class: 'hint', text: '服务器正在从 GitHub 打包…' });
      var acts2 = el('div', { class: 'acts' }), cancel = el('button', { class: 'btn', text: '取消', onclick: function () { stop = true; bg.remove(); } });
      acts2.appendChild(cancel);
      var bg = el('div', { class: 'sheet-bg' });
      var title = el('h2', { text: '下载 ' + repo.split('/')[1] + '.zip' });
      bg.appendChild(el('div', { class: 'sheet' }, title, stat, prog, acts2));
      document.body.appendChild(bg);
      prog.firstChild.style.width = '8%';
      function bytes(b64) { var bin = atob(b64), a = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i); return a; }
      api('gh_zip', { repo: repo }).then(function (r) {
        var total = Math.max(1, Math.ceil(r.size / r.chunk)), parts = [], got = 0;
        function next(n) {
          if (stop) return Promise.reject(new Error('已取消'));
          return api('gh_zip_chunk', { id: r.id, n: n, done: n === total - 1 ? 1 : 0 }, 3).then(function (c) {
            parts[n] = bytes(c.data); got += parts[n].length;
            stat.textContent = '下载到手机 ' + fmtBytes(got) + ' / ' + fmtBytes(r.size);
            prog.firstChild.style.width = Math.round(10 + got / Math.max(1, r.size) * 90) + '%';
            return n + 1 < total ? next(n + 1) : null;
          });
        }
        return next(0).then(function () {
          var file = new File(parts, r.name, { type: 'application/zip' });
          stat.textContent = fmtBytes(r.size);
          prog.firstChild.style.width = '100%';
          title.textContent = '保存 ' + r.name;
          var canShare = false; try { canShare = !!(navigator.canShare && navigator.canShare({ files: [file] })); } catch (e) {}
          var save = el('button', { class: 'btn primary', text: canShare ? '另存为' : '下载' });
          save.addEventListener('click', function () {
            if (canShare) {
              navigator.share({ files: [file] }).then(function () { bg.remove(); }, function (e) { if (e && e.name !== 'AbortError') toast('没能打开分享：' + e.message, true); });
            } else {
              var u = URL.createObjectURL(file), a = el('a', { href: u, download: r.name });
              document.body.appendChild(a); a.click(); a.remove();
              setTimeout(function () { URL.revokeObjectURL(u); }, 60000); bg.remove();
            }
          });
          acts2.appendChild(save);
        });
      }).catch(function (e) { if (stop) return; stat.textContent = e.message; prog.style.display = 'none'; cancel.textContent = '关闭'; });
    }
    page.appendChild(acts);
    var card = el('div', { class: 'card gh-card' }, skeleton(4)); page.appendChild(card);
    var extra = el('div'); page.appendChild(extra);
    var items = [], selMode = false, picked = {};
    histBtn.addEventListener('click', function () { go(gTab(), 'gitc:' + repo + '|' + path); });
    upBtn.addEventListener('click', function () { ghPickUpload(repo, path, items, null, function () { go(cur.tab, cur.sub, true); }); });
    var delBar = el('div', { class: 'gh-delbar' });
    function render() {
      clear(card);
      if (!items.length) card.appendChild(el('div', { class: 'hint', style: 'margin:6px 0', text: '这里还是空的，点「上传文件」放点东西进来。' }));
      items.forEach(function (x) {
        var chk = selMode ? el('span', { class: 'gh-chk' + (picked[x.path] ? ' on' : '') }) : null;
        card.appendChild(el('button', { class: 'gh-row gh-f' + (picked[x.path] ? ' sel' : ''), onclick: function () {
          if (selMode) { if (picked[x.path]) delete picked[x.path]; else picked[x.path] = x; render(); return; }
          if (x.type === 'dir') go(gTab(), 'gitr:' + repo + '|' + x.path);
          else if (x.type === 'file') go(gTab(), 'gitf:' + repo + '|' + x.path);
          else toast(x.type === 'submodule' ? '这是子模块，不能在这里打开' : '这是链接文件，不能在这里打开');
        } }, chk,
          el('span', { class: 'gh-ri ' + (x.type === 'dir' ? 'd' : 'f') }, ghIco(x.type === 'dir' ? 'dir' : 'file')),
          el('span', { class: 'gh-rt' }, el('b', { text: x.name }), x.type === 'file' ? el('small', { text: fmtBytes(x.size) }) : null),
          x.type === 'dir' && !selMode ? goArrow() : null));
      });
      var n = Object.keys(picked).length;
      clear(delBar);
      if (selMode) {
        delBar.appendChild(el('span', { text: n ? '已选 ' + n + ' 项' : '点选文件或文件夹' }));
        var one = n === 1 ? picked[Object.keys(picked)[0]] : null;
        delBar.appendChild(el('button', { class: 'btn sm', text: '重命名', disabled: one && one.type !== 'submodule' ? null : true, onclick: function () {
          ghRename(repo, one.path, one.type === 'dir', function () { selMode = false; picked = {}; selBtn.textContent = '选择'; selBtn.classList.remove('primary'); go(cur.tab, cur.sub, true); });
        } }));
        var del = el('button', { class: 'btn sm danger', text: '删除', disabled: n ? null : true });
        del.addEventListener('click', function () {
          var ps = Object.keys(picked), dirs = ps.filter(function (p) { return picked[p].type === 'dir'; }).length;
          confirmBox('删除 ' + n + ' 项', (dirs ? '文件夹里的所有文件会一起删除。' : '') + '删除会作为一次提交记录在仓库里，需要时可以从提交记录里找回。', function () {
            return api('gh_del', { repo: repo, paths: ps }).then(function (r) { toast('已删除 ' + r.n + ' 个文件'); selMode = false; picked = {}; go(cur.tab, cur.sub, true); });
          });
        });
        delBar.appendChild(del);
        card.appendChild(delBar);
      }
    }
    selBtn.addEventListener('click', function () { selMode = !selMode; picked = {}; selBtn.textContent = selMode ? '取消' : '选择'; selBtn.classList.toggle('primary', selMode); render(); });
    api('gh_dir', { repo: repo, path: path }).then(function (d) {
      items = d.items; render();
      if (d.readme) extra.appendChild(el('div', { class: 'card' }, el('h3', { text: 'README' }), ghHtml(d.readme)));
      if (!path) extra.appendChild(el('div', { class: 'gh-danger' },
        el('button', { class: 'btn sm', text: '在 GitHub 打开', onclick: function () { openLink('https://github.com/' + repo); } }),
        el('span', { class: 'sp' }),
        el('button', { class: 'btn sm danger', text: '删除仓库', onclick: function () { ghDelRepo(repo); } })));
    }).catch(function (e) { clear(card).appendChild(failBox(e.message, function () { go(cur.tab, cur.sub, true); })); });
  }
  // 重命名 / 移动：写新的完整路径；改中间的文件夹名就是移动，文件夹不存在会自动建
  function ghRename(repo, path, isDir, done) {
    sheet(isDir ? '重命名文件夹' : '重命名文件', [
      { key: 'p', label: '新路径（可以只改名字，也可以改前面的文件夹把它移过去；文件夹不存在会自动建）', value: path },
      { key: 'msg', label: '提交说明（可选）', ph: '重命名 ' + path.split('/').pop() }
    ], '确定', function (v) {
      var np = (v.p || '').trim().replace(/^\/+|\/+$/g, '').replace(/\/{2,}/g, '/');
      if (!np) return Promise.reject(new Error('新路径不能为空'));
      if (np === path) return Promise.reject(new Error('新名字和原来一样'));
      return api('gh_mv', { repo: repo, src: path, dst: np, msg: v.msg }).then(function (r) {
        toast(r.n > 1 ? '已移动 ' + r.n + ' 个文件' : '已重命名');
        if (done) done(r.path);
      });
    });
  }
  function ghDelRepo(repo) {
    var name = repo.split('/')[1];
    sheet('删除仓库', [{ key: 'c', label: '这个操作不能撤销，仓库里所有文件和提交记录都会永久删除。请输入仓库名「' + name + '」确认', ph: name }], '永久删除', function (v) {
      if (v.c.trim() !== name) return Promise.reject(new Error('输入的仓库名不对'));
      return api('gh_repo_del', { repo: repo, confirm: v.c.trim() }).then(function () { toast('已删除 ' + name); navStack = []; go(gTab(), gTab() === 'admin' ? 'git' : 'gh'); });
    });
  }

  // 选文件 → 确认（目标文件夹、说明）→ 分块上传 → 服务器提交。replaceName：替换某个文件时固定用它的名字
  function ghPickUpload(repo, path, existing, replaceName, done) {
    var inp = el('input', { type: 'file', style: 'display:none' });
    if (!replaceName) inp.multiple = true;
    document.body.appendChild(inp);
    inp.addEventListener('change', function () {
      var files = Array.prototype.slice.call(inp.files || []); inp.remove();
      if (!files.length) return;
      ghUploadSheet(repo, path, files, existing || [], replaceName, done);
    });
    inp.click();
    setTimeout(function () { if (inp.parentNode && !inp.files.length) inp.remove(); }, 120000);
  }
  function ghUploadSheet(repo, path, files, existing, replaceName, done) {
    var names = {}; existing.forEach(function (x) { names[x.name] = 1; });
    var total = files.reduce(function (s, f) { return s + f.size; }, 0);
    var list = el('div', { class: 'gh-uplist' });
    files.forEach(function (f) {
      var nm = replaceName || f.name;
      list.appendChild(el('div', { class: 'gh-upf' }, el('span', { text: nm }), el('small', { text: fmtBytes(f.size) + (replaceName || names[nm] ? ' · 替换' : ' · 新增') })));
    });
    var prog = el('div', { class: 'gh-prog' }, el('i')), stat = el('div', { class: 'hint' });
    var extra = el('div', null, list, prog, stat);
    var fields = [];
    if (!replaceName) fields.push({ key: 'dir', label: '上传到文件夹（留空 = 仓库根目录，可以写新的文件夹名）', value: path });
    fields.push({ key: 'msg', label: '提交说明（可选）', ph: replaceName ? '更新 ' + replaceName : (files.length === 1 ? '上传 ' + files[0].name : '上传 ' + files.length + ' 个文件') });
    var bg = sheet(replaceName ? '替换「' + replaceName + '」' : '上传 ' + files.length + ' 个文件（' + fmtBytes(total) + '）', fields, '开始上传', function (v) {
      var dir = replaceName ? path : (v.dir || '').trim().replace(/^\/+|\/+$/g, '');
      bg.classList.add('busy');
      return ghUpload(repo, dir, files, replaceName, v.msg, function (txt, pct) { stat.textContent = txt; prog.firstChild.style.width = Math.round(pct * 100) + '%'; prog.style.display = 'block'; })
        .then(function (r) { toast('已上传 ' + r.n + ' 个文件'); if (done) done(); }, function (e) { bg.classList.remove('busy'); stat.textContent = ''; prog.style.display = 'none'; throw e; });
    }, extra);
    setTimeout(function () { var a0 = document.activeElement; if (a0 && a0.blur && bg.contains(a0)) a0.blur(); }, 200);   // 不自动弹键盘
  }
  function ghB64(buf) {
    var bytes = new Uint8Array(buf), s = '', step = 0x8000;
    for (var i = 0; i < bytes.length; i += step) s += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
    return btoa(s);
  }
  function ghUpload(repo, dir, files, replaceName, msg, onProg) {
    var total = files.reduce(function (s, f) { return s + f.size; }, 0) || 1, sent = 0;
    onProg('准备上传…', 0);
    return api('gh_up_begin', { repo: repo, dir: dir, files: files.map(function (f) { return { name: replaceName || f.name, size: f.size }; }) }).then(function (b) {
      var jobs = [];
      files.forEach(function (f, i) { for (var n = 0; n < Math.max(1, Math.ceil(f.size / b.chunk)); n++) jobs.push([i, n]); });
      var k = 0;
      function worker() {
        if (k >= jobs.length) return Promise.resolve();
        var j = jobs[k++], f = files[j[0]], blob = f.slice(j[1] * b.chunk, Math.min(f.size, (j[1] + 1) * b.chunk));
        return blob.arrayBuffer().then(function (buf) {
          return api('gh_up_chunk', { id: b.id, i: j[0], n: j[1], data: ghB64(buf) }, 3);
        }).then(function () {
          sent += blob.size; onProg('上传到服务器 ' + fmtBytes(sent) + ' / ' + fmtBytes(total), sent / total * 0.85);
          return worker();
        });
      }
      return Promise.all([worker(), worker(), worker(), worker()]).then(function () {
        onProg('提交到 GitHub…', 0.88);
        return api('gh_up_commit', { id: b.id, msg: msg || '' });
      }).then(function () {
        return new Promise(function (res, rej) {
          var tries = 0;
          (function poll() {
            api('gh_up_state', { id: b.id }).then(function (s) {
              if (s.state === 'done') { onProg('完成', 1); res(s); return; }
              if (s.state === 'error') { rej(new Error(s.error || '上传失败')); return; }
              onProg('提交到 GitHub：' + (s.step || '处理中'), 0.9);
              if (++tries > 300) { rej(new Error('提交时间太长，请稍后到提交记录里看看是否成功')); return; }
              setTimeout(poll, 1000);
            }, rej);
          })();
        });
      });
    });
  }

  function pageGitFile(page, arg) {
    var a = gsplit(arg), repo = a[0], path = a[1], name = path.split('/').pop(), dirPath = path.split('/').slice(0, -1).join('/');
    page.appendChild(top(name, true));
    page.appendChild(ghCrumbs(repo, path, true));
    var acts = el('div', { class: 'gh-acts' }); page.appendChild(acts);
    var card = el('div', { class: 'card gh-view' }, skeleton(4)); page.appendChild(card);
    // 在线编辑：改的是源码（Markdown 也是改源码），保存时作为一次提交；内容走分块上传，大文件也能存
    function ghEdit(f) {
      var crlf = /\r\n/.test(f.text), orig = f.text.replace(/\r\n/g, '\n');
      var ta = el('textarea', { class: 'gh-edit mono', spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off', autocorrect: 'off', wrap: 'off' });
      ta.value = orig;
      var save = el('button', { class: 'btn primary', text: '保存' }), cancel = el('button', { class: 'btn', text: '取消' });
      var bar = el('div', { class: 'gh-edbar' }, el('span', { class: 'hint', style: 'margin:0', text: '编辑 ' + name }), el('span', { class: 'sp' }), cancel, save);
      acts.style.display = 'none';
      clear(card).appendChild(bar); card.appendChild(ta);
      function fit() { ta.style.height = 'auto'; ta.style.height = Math.max(240, ta.scrollHeight + 4) + 'px'; }
      ta.addEventListener('input', fit); setTimeout(fit, 0);
      cancel.addEventListener('click', function () {
        if (ta.value === orig) { go(cur.tab, cur.sub, true); return; }
        confirmBox('放弃修改', '改过的内容不会保存。', function () { go(cur.tab, cur.sub, true); return Promise.resolve(); });
      });
      save.addEventListener('click', function () {
        if (ta.value === orig) { toast('没有改动'); return; }
        sheet('保存到 GitHub', [{ key: 'msg', label: '提交说明（可选）', ph: '修改 ' + name }], '保存', function (v) {
          var text = crlf ? ta.value.replace(/\n/g, '\r\n') : ta.value;
          // 保存前看一眼：如果别处已经改过这个文件，先确认要不要覆盖
          return api('gh_file', { repo: repo, path: path }).then(function (now) {
            if (now.sha !== f.sha && !window.confirm('这个文件在你打开之后被改过了（可能是别处提交的），继续保存会覆盖那次修改。继续吗？')) throw new Error('已取消保存');
            var blob = new Blob([text], { type: 'text/plain' });
            return ghUpload(repo, dirPath, [blob], name, v.msg || ('修改 ' + name), function (t) { save.textContent = t.length > 8 ? '保存中…' : t; });
          }).then(function () { toast('已保存'); go(cur.tab, cur.sub, true); }, function (e) { save.textContent = '保存'; throw e; });
        });
      });
    }
    api('gh_file', { repo: repo, path: path }).then(function (f) {
      clear(card);
      // 原始文件地址（raw.githubusercontent.com）；.sh 脚本再给一条「下载并运行」的命令。分支从 GitHub 返回的网页地址里取
      var branch = ''; try { var bu = decodeURIComponent(f.url), bi = bu.indexOf('/blob/'), tail = '/' + path; if (bi > 0 && bu.slice(-tail.length) === tail) branch = bu.slice(bi + 6, bu.length - tail.length); } catch (e) {}
      if (branch) {
        var raw = 'https://raw.githubusercontent.com/' + repo + '/' + branch.split('/').map(encodeURIComponent).join('/') + '/' + path.split('/').map(encodeURIComponent).join('/');
        var rows = [['原始地址', raw]];
        if (/\.sh$/i.test(name)) rows.push(['下载运行', 'curl -fsSL ' + raw + ' -o ' + name + ' && bash ' + name]);
        page.insertBefore(el('div', { class: 'card gh-clone' }, rows.map(function (u) {
          return el('button', { class: 'gh-url', onclick: function () { copy(u[1]); } },
            el('span', { class: 'gh-ut', text: u[0] }), el('span', { class: 'mono gh-uv', text: u[1] }), el('span', { class: 'gh-uc', text: '复制' }));
        }), el('div', { class: 'hint', style: 'margin:2px 0 8px', text: '私有仓库的原始地址需要登录才能下载，公开仓库可以直接用。' })), acts);
      }
      card.appendChild(el('div', { class: 'gh-meta' }, el('span', { text: fmtBytes(f.size) }), f.text != null ? el('span', { text: (f.text ? f.text.replace(/\n$/, '').split('\n').length : 0) + ' 行' }) : null));
      if (f.text != null) acts.appendChild(el('button', { class: 'btn primary', text: '编辑', onclick: function () { ghEdit(f); } }));
      if (f.text != null) acts.appendChild(el('button', { class: 'btn', text: '复制内容', onclick: function () { copy(f.text); } }));
      acts.appendChild(el('button', { class: 'btn', text: '替换', onclick: function () { ghPickUpload(repo, dirPath, [], name, function () { go(cur.tab, cur.sub, true); }); } }));
      acts.appendChild(el('button', { class: 'btn', text: '记录', onclick: function () { go(gTab(), 'gitc:' + repo + '|' + path); } }));
      acts.appendChild(el('button', { class: 'btn', text: '重命名', onclick: function () {
        ghRename(repo, path, false, function (np) { go(gTab(), 'gitf:' + repo + '|' + np, true); });
      } }));
      acts.appendChild(el('button', { class: 'btn danger', text: '删除', onclick: function () {
        confirmBox('删除文件', '删除「' + name + '」？会作为一次提交记录在仓库里。', function () {
          return api('gh_del', { repo: repo, paths: [path] }).then(function () { toast('已删除'); backHandler(); });
        });
      } }));
      if (f.img) { card.appendChild(el('div', { class: 'gh-img' }, el('img', { src: f.img, alt: name }))); return; }
      if (f.big) { card.appendChild(el('div', { class: 'hint', text: '文件太大，这里不显示内容。' })); card.appendChild(el('button', { class: 'btn sm', style: 'margin-top:10px', text: '在 GitHub 打开', onclick: function () { openLink(f.url); } })); return; }
      if (f.binary) { card.appendChild(el('div', { class: 'hint', text: '这是二进制文件，不能直接显示。' })); return; }
      var code = ghCode(f.text);
      if (f.html) {
        var md = ghHtml(f.html), showMd = true;
        var tg2 = el('div', { class: 'seg', style: 'margin:4px 0 10px' });
        var b1 = el('button', { class: 'on', text: '预览' }), b2 = el('button', { text: '源码' });
        function sw(m) { showMd = m; b1.classList.toggle('on', m); b2.classList.toggle('on', !m); md.style.display = m ? '' : 'none'; code.style.display = m ? 'none' : ''; }
        b1.addEventListener('click', function () { sw(true); }); b2.addEventListener('click', function () { sw(false); });
        tg2.appendChild(b1); tg2.appendChild(b2);
        card.appendChild(tg2); card.appendChild(md); card.appendChild(code); sw(true);
      } else card.appendChild(code);
    }).catch(function (e) { clear(card).appendChild(failBox(e.message, function () { go(cur.tab, cur.sub, true); })); });
  }
  // 代码：带行号，横向滚动；太长只显示前 3000 行
  function ghCode(text) {
    var lines = text.replace(/\r\n/g, '\n').split('\n');
    if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
    var cut = lines.length > 3000;
    if (cut) lines = lines.slice(0, 3000);
    var w = String(lines.length).length;
    var pre = el('pre', { class: 'gh-code nocopy' });
    pre.textContent = lines.map(function (l, i) { var n = String(i + 1); return new Array(w - n.length + 1).join(' ') + n + '  ' + l; }).join('\n');
    var box = el('div', null, pre);
    if (cut) box.appendChild(el('div', { class: 'hint', text: '文件太长，只显示前 3000 行，完整内容请用「复制内容」。' }));
    return box;
  }

  function pageGitCommits(page, arg) {
    var a = gsplit(arg), repo = a[0], path = a[1];
    page.appendChild(top('提交记录', true));
    page.appendChild(el('div', { class: 'hint', style: 'margin:-4px 2px 10px' , text: repo.split('/')[1] + (path ? ' / ' + path : '') }));
    var card = el('div', { class: 'card gh-card' }, skeleton(5)); page.appendChild(card);
    var all = [], pg = 1, more = false;
    function render() {
      clear(card);
      if (!all.length) card.appendChild(el('div', { class: 'hint', style: 'margin:6px 0', text: '还没有提交记录' }));
      all.forEach(function (c) {
        card.appendChild(el('button', { class: 'gh-row gh-cm', onclick: function () { go(gTab(), 'gitcd:' + repo + '|' + c.sha); } },
          el('span', { class: 'gh-rt' }, el('b', { text: c.msg.split('\n')[0] || '（无说明）' }), el('small', null, el('span', { class: 'mono', text: c.sha.slice(0, 7) }), ' · ' + c.author + ' · ' + ghAgo(c.date))),
          goArrow()));
      });
      if (more) card.appendChild(el('div', { class: 'gh-foot' }, el('span'), el('button', { class: 'btn sm', text: '加载更多', onclick: function (e) { run(e.currentTarget, load); } })));
    }
    function load() { return api('gh_commits', { repo: repo, path: path, page: pg }).then(function (r) { all = all.concat(r.items); more = r.more; pg++; render(); }); }
    load().catch(function (e) { clear(card).appendChild(failBox(e.message, function () { go(cur.tab, cur.sub, true); })); });
  }

  function pageGitCommit(page, arg) {
    var a = gsplit(arg), repo = a[0], sha = a[1];
    page.appendChild(top('改动详情', true));
    var head = el('div', { class: 'card' }, skeleton(2)); page.appendChild(head);
    var box = el('div'); page.appendChild(box);
    var ST = { added: ['新增', 'ok'], removed: ['删除', 'bad'], modified: ['修改', ''], renamed: ['改名', 'warn'], copied: ['复制', ''], changed: ['修改', ''] };
    api('gh_commit', { repo: repo, sha: sha }).then(function (c) {
      clear(head);
      var lines = c.msg.split('\n');
      head.appendChild(el('div', { class: 'gh-cmt' }, lines[0]));
      if (lines.length > 1) head.appendChild(el('div', { class: 'gh-cmb', text: lines.slice(1).join('\n').trim() }));
      head.appendChild(el('div', { class: 'gh-meta' },
        el('span', { class: 'mono', text: c.sha.slice(0, 7), onclick: function () { copy(c.sha); } }),
        el('span', { text: c.author + ' · ' + ghAgo(c.date) }),
        el('span', { class: 'gh-add', text: '+' + c.add }), el('span', { class: 'gh-del', text: '−' + c.del }),
        el('span', { text: c.files.length + ' 个文件' })));
      c.files.forEach(function (f) {
        var st = ST[f.status] || [f.status, ''];
        var fc = el('div', { class: 'card gh-fc' },
          el('div', { class: 'gh-fh' }, el('span', { class: 'pill ' + st[1], text: st[0] }), el('b', { text: f.old ? f.old + ' → ' + f.name : f.name }),
            el('span', { class: 'gh-add', text: '+' + f.add }), el('span', { class: 'gh-del', text: '−' + f.del })));
        if (f.patch) {
          var pre = el('pre', { class: 'gh-diff nocopy' });
          f.patch.split('\n').forEach(function (l) {
            var c0 = l.charAt(0);
            pre.appendChild(el('div', { class: c0 === '+' ? 'a' : c0 === '-' ? 'd' : c0 === '@' ? 'h' : '', text: l || ' ' }));
          });
          fc.appendChild(pre);
          if (f.cut) fc.appendChild(el('div', { class: 'hint', text: '改动太多，只显示一部分。' }));
        } else fc.appendChild(el('div', { class: 'hint', text: f.nopatch ? '二进制文件或改动太大，不显示具体内容。' : '没有内容改动。' }));
        box.appendChild(fc);
      });
      if (c.url) box.appendChild(el('button', { class: 'btn block', text: '在 GitHub 打开', onclick: function () { openLink(c.url); } }));
    }).catch(function (e) { clear(head).appendChild(failBox(e.message, function () { go(cur.tab, cur.sub, true); })); });
  }

  // ============================================================ 多服务器监控
  // 首页：运行总览（取代主机状态）。没问题只显示一行，有问题列出原因
  function overviewProblems(sv) {
    var out = [];
    sv.items.forEach(function (s) {
      if (!s.seen) return;
      if (!s.online) out.push({ lv: 'bad', name: s.name, text: '离线 ' + (s.age >= 60 ? Math.floor(s.age / 60) + ' 分钟' : s.age + ' 秒'), id: s.id });
      else if (s.steal >= 25) out.push({ lv: 'bad', name: s.name, text: '被抢占 ' + Math.round(s.steal) + '%', id: s.id });
    });
    return out;
  }
  function overviewCard(d) {
    var tabOf = me && me.owner ? 'admin' : 'tools';
    var ic = el('span', { class: 'ov-ic' }, el('i')), ttl = el('b', { text: '服务器监控' }), sub = el('span', { class: 'ov-sub', text: '读取中…' });
    var list = el('div', { class: 'ov-list' });
    var head = el('button', { class: 'ov-h', onclick: function () { go(tabOf, 'servers'); } }, ic, el('span', { class: 'ov-tx' }, ttl, sub), goArrow());
    var box = el('div', { class: 'card ov load' }, head, list);
    livePoll(function () {
      return api('servers').then(function (sv) {
        var probs = overviewProblems(sv), lv = probs.some(function (x) { return x.lv === 'bad'; }) ? 'bad' : probs.length ? 'warn' : 'ok';
        box.className = 'card ov ' + (sv.total ? lv : 'load');
        // 桌面图标上的红色数字跟着离线台数走（打开 App 就校正一次；只有主屏幕 App 支持）
        try { if (navigator.setAppBadge && root.classList.contains('standalone')) { var off = Math.max(0, (sv.total || 0) - (sv.online || 0)); (off ? navigator.setAppBadge(off) : navigator.clearAppBadge()).catch(function () {}); } } catch (e) {}   // 还没有服务器：灰色，不显示绿色“正常”
        sub.textContent = !sv.total && lv === 'ok' ? '还没有添加服务器，点这里添加' : (lv === 'ok' ? '运行正常' : probs.length + ' 项需要处理') + ' · ' + sv.online + '/' + sv.total + ' 在线';
        clear(list);
        probs.slice(0, 4).forEach(function (x) {
          list.appendChild(el(x.id ? 'button' : 'div', { class: 'ov-row ' + x.lv, onclick: x.id ? function () { go(tabOf, 'server:' + x.id); } : null },
            el('i', { class: 'ov-d' }), el('b', { text: x.name }), el('span', { class: 'sp' }), el('span', { class: 'ov-t', text: x.text })));
        });
        if (probs.length > 4) list.appendChild(el('div', { class: 'ov-more', text: '还有 ' + (probs.length - 4) + ' 项…' }));
      });
    });
    return box;
  }
  function serversTile(sum) {
    var sub = el('span', { class: 'st-sub', text: sum ? (sum.total ? sum.online + ' 台在线 · 共 ' + sum.total + ' 台' : '还没有添加服务器，点这里添加') : '点击查看' });
    var tile = el('button', { class: 'card srv-tile', onclick: function () { go(me && me.owner ? 'admin' : 'tools', 'servers'); } },
      el('span', { class: 'st-ic' }, icon('srv')),
      el('span', { class: 'st-tx' }, el('b', { text: '服务器监控' }), sub),
      sum && sum.total ? el('span', { class: 'pill ' + (sum.online === sum.total ? 'ok' : 'warn') }, el('i', { class: 'dot' }), sum.online + '/' + sum.total) : null,
      goArrow());
    return tile;
  }
  // 上传/下载：数字加一条渐隐细线（按对数刻度，10MB/s 记满；几 B 到几十 MB 都看得出差别）
  function netRow(net) {
    function one(cls, arrow, bytes) {
      var pct = bytes > 0 ? Math.max(3, Math.min(100, Math.log10(bytes + 1) / 7 * 100)) : 0;
      return el('div', { class: 'nt ' + cls }, el('div', { class: 'nv', text: arrow + ' ' + fmtBytes(bytes) + '/s' }), el('div', { class: 'ntr' }, el('i', { style: 'width:' + pct + '%' })));
    }
    return el('div', { class: 'sv-n2' }, one('up', '↑', net.up), one('dn', '↓', net.down));
  }
  function loadCls(p) { return p >= 90 ? 'bad' : p >= 70 ? 'warn' : 'ok'; }
  function miniBar(label, pct) {
    // 样式直接写在元素上，不依赖 css 是否已更新
    return el('div', { class: 'mr', style: 'display:grid;grid-template-columns:30px 1fr 34px;align-items:center;gap:6px;font-size:11px;color:var(--dim)' },
      el('span', { text: label }),
      el('div', { class: 'bar ' + loadCls(pct), style: 'height:5px;width:100%' }, el('i', { style: 'width:' + Math.max(3, Math.min(100, pct)) + '%' })),
      el('em', { style: 'font-style:normal;text-align:right;color:inherit', text: Math.round(pct) + '%' }));
  }
  // 改了服务器 IP 之后，5 秒盾放行名单的更新结果
  function uamNote(r) {
    if (r === 'ok') toast('网站正开着 5 秒盾：已把这个 IP 加进放行名单');
    else if (r) toast('5 秒盾放行名单没更新成功：' + r, true);
  }
  function showCmd(cmd, title) {
    var box = el('div', { class: 'cmdbox mono', text: cmd });
    var extra = el('div', null,
      el('p', { class: 'hint', text: '在要监控的那台服务器上用 root 粘贴执行（需要 bash 和 curl）。装好几秒后这台就会出现在列表里。' }), box,
      el('button', { class: 'btn', style: 'margin-top:10px;width:100%', text: '复制命令', onclick: function () { copy(cmd); } }));
    sheet(title || '安装命令', [], '完成', function () { return Promise.resolve(); }, extra);
  }
  function grpIcon() { return el('span', { class: 'dz-ic' }, icon('qgrp')); }   // 分组前面统一用文件夹图标
  function pageServers(page) {
    page.appendChild(top('服务器监控', true));
    var pills = el('div', { class: 'meta', style: 'margin:0 0 10px' });
    var list = el('div');
    var head = el('div', { class: 'card' }, el('h3', { class: 'h3i' }, icon('srv'), el('span', { text: '我的服务器' }), el('span', { class: 'sp' }),
      el('button', { class: 'btn sm', text: '分组', onclick: function () { go(cur.tab, 'srvgroups'); } }),
      el('button', { class: 'btn sm', style: 'margin-left:6px', text: '＋ 添加', onclick: addServer })), pills);
    page.appendChild(head); page.appendChild(list);
    var groups = [], defName = '未分组';
    function addServer(ev) {
      // 先查探针网址所在的域名开没开 5 秒盾：开着就必须填 IP（不然装不上）；没开就不用填
      var btn = ev && ev.currentTarget;
      run(btn, function () {
        return api('server_edit', { op: 'uam_state' }).catch(function () { return { uam: null }; }).then(openSheet);
      });
      function openSheet(st) {
        var f = [{ key: 'name', label: '给这台服务器起个名字', ph: '例如：东京 VPS' }];
        if (groups.length) f.push({ key: 'group', label: '放进分组', options: [['', defName]].concat(groups.map(function (g) { return [g, g]; })), value: '' });
        if (st.uam) f.push({ key: 'ip', label: '这台服务器的公网 IP（必填）', ph: '例如 1.2.3.4' });
        else if (st.uam === null) f.push({ key: 'ip', label: '这台服务器的公网 IP（可不填）', ph: '例如 1.2.3.4' });
        var tip = st.uam
          ? el('div', { class: 'hint warnl' }, el('span', { class: 'wi' }, icon('warn')), el('span', { style: 'flex:1;min-width:0', text: '「' + (st.zone || '网站') + '」正开着 5 秒盾：必须填这台服务器的公网 IP，否则安装命令下载不了探针、也上报不了。填了会自动加进放行名单。' }))
          : st.uam === null ? el('div', { class: 'hint', text: '没能确认网站有没有开 5 秒盾。开着的话要填这台服务器的公网 IP，否则装不上；没开可以不填，探针上报后会自动记下 IP。' }) : null;
        sheet('添加服务器', f, '生成安装命令', function (v) {
          var ip = (v.ip || '').trim();
          if (st.uam && !ip) throw new Error('网站开着 5 秒盾，必须填这台服务器的公网 IP');
          return api('server_edit', { op: 'add', name: v.name, group: v.group || '', ip: ip }).then(function (r) {
            uamNote(r.uam);
            setTimeout(function () { showCmd(r.cmd, '安装命令'); }, 150); refresh();
          });
        }, tip);
      }
    }
    function card(s) {
      var c = el('button', { class: 'card sv' + (s.online ? '' : ' off'), onclick: function () { go(cur.tab, 'server:' + s.id); } },
        el('div', { class: 'sv-h' }, el('i', { class: 'sd ' + (s.online ? 'on' : '') }), el('b', { text: s.name }),
          s.online && s.steal >= 5 ? el('span', { class: 'pill st-tag ' + (s.steal >= 25 ? 'bad' : 'warn'), text: '被抢 ' + Math.round(s.steal) + '%' }) : null));
      if (s.online) {
        c.appendChild(miniBar('CPU', s.cpu)); c.appendChild(miniBar('内存', s.mem.pct)); c.appendChild(miniBar('硬盘', s.disk.pct));
        c.appendChild(netRow(s.net));
      } else {
        c.appendChild(el('div', { class: 'sv-off', text: s.seen ? '已离线 · ' + (s.age >= 60 ? Math.floor(s.age / 60) + ' 分钟前在线' : s.age + ' 秒前在线') : '等待探针连接…' }));
      }
      return c;
    }
    var fold = {};                        // 收起的分组（记在本机，下次打开保持）
    try { fold = JSON.parse(localStorage.getItem('mini_srv_fold') || '{}') || {}; } catch (e) {}
    function section(name, items) {     // 一个分组：可点的小标题（在线数 / 总数 + 箭头）+ 这一组的服务器
      var on = items.filter(function (s) { return s.online; }).length, closed = !!fold[name];
      var g = el('div', { class: 'grid g2' + (closed ? ' grp-closed' : '') });
      var off = items.length - on;
      // 和「域名解析」列表一样的长条，点一下展开 / 收起
      var h = el('button', { class: 'card dz grp-bar' + (closed ? ' closed' : ''), type: 'button', onclick: function () {
        closed = !closed;
        if (closed) fold[name] = 1; else delete fold[name];
        try { localStorage.setItem('mini_srv_fold', JSON.stringify(fold)); } catch (e) {}
        h.classList.toggle('closed', closed); g.classList.toggle('grp-closed', closed);
      } }, grpIcon(name),
        el('span', { class: 'dz-t' }, el('b', { text: name })),
        // 右边状态点：绿 = 全部在线，黄 = 有离线，红 = 全部离线
        el('i', { class: 'sdot grp-dot' + (!items.length ? ' off' : !off ? '' : on ? ' warn' : ' bad'), title: items.length ? on + '/' + items.length + ' 在线' : '还没有服务器' }),
        el('span', { class: 'go-ar grp-arr' }, icon('go')));
      items.forEach(function (s) { g.appendChild(card(s)); });
      if (!items.length) g.appendChild(el('div', { class: 'hint grp-empty', text: '这个分组还没有服务器。打开服务器详情点「分组」，或添加服务器时选这个分组。' }));
      list.appendChild(h); list.appendChild(g);
    }
    var last = null;
    function draw() {
      var d = last; if (!d) return;
      groups = d.groups || []; defName = d.default_group || '未分组';
      var byG = {}; d.items.forEach(function (s) { (byG[s.group || ''] = byG[s.group || ''] || []).push(s); });
      var ungrouped = byG[''] || [];
      clear(list);
      if (!d.items.length) { list.appendChild(el('div', { class: 'card empty' }, el('span', { class: 'big wi' }, icon('srv')), el('div', { text: '还没有服务器' }), el('div', { class: 'hint', text: '点右上角「＋ 添加」，在目标服务器上执行一行命令即可' }))); return; }
      if (!groups.length) {                       // 还没建分组：和以前一样平铺
        var g0 = el('div', { class: 'grid g2' }); d.items.forEach(function (s) { g0.appendChild(card(s)); }); list.appendChild(g0); return;
      }
      groups.forEach(function (g) { section(g, byG[g] || []); });   // 每个分组都显示长条（空分组也显示），点开看服务器
      if (ungrouped.length) section(defName, ungrouped);
    }
    function refresh() {
      return api('servers').then(function (d) {
        last = d;
        clear(pills);
        pills.appendChild(el('span', { class: 'pill ' + (d.total && d.online === d.total ? 'ok' : d.online ? 'warn' : '') }, el('i', { class: 'dot' }), d.online + ' 台在线'));
        draw();
      }).catch(function (e) { if (!list.firstChild) list.appendChild(failBox(e.message, function () { go(cur.tab, 'servers'); })); });
    }
    livePoll(refresh);
  }
  // 分组管理：新建 / 改名 / 删除 / 上移
  function pageGroups(page) {
    page.appendChild(top('服务器分组', true));
    var box = el('div', { class: 'card' }, skeleton(2));
    page.appendChild(box);
    var tip = el('div', { class: 'hint', style: 'margin:0 4px' });
    page.appendChild(tip);
    function draw(d) {
      clear(box);
      box.appendChild(el('h3', { class: 'h3i nobar' }, icon('qgrp'), el('span', { text: '我的分组' }), el('span', { class: 'sp' }),
        el('button', { class: 'btn sm', text: '＋ 新建', onclick: function () {
          sheet('新建分组', [{ key: 'name', label: '分组名称', ph: '例如：香港 / 日本 / 落地机' }], '创建', function (v) { return api('server_edit', { op: 'group_add', name: v.name }).then(load); });
        } })));
      var cnt = {}, def = d.default_group || '未分组', nDef = 0;
      d.items.forEach(function (s) { if (s.group) cnt[s.group] = (cnt[s.group] || 0) + 1; else nDef++; });
      tip.textContent = '「' + def + '」是默认组：没放进其他分组的服务器都在这里，可以改名，不能删除。删除其他分组时，组里的服务器会回到「' + def + '」。要把服务器放进分组：打开服务器详情，点「分组」。';
      d.groups.forEach(function (g, i) {
        box.appendChild(el('div', { class: 'grp-row' }, grpIcon(g), el('b', { text: g }), el('span', { class: 'grp-c', text: (cnt[g] || 0) + ' 台' }), el('span', { class: 'sp' }),
          i ? el('button', { class: 'btn sm ghost', text: '上移', onclick: function () {
            var o = d.groups.slice(); o.splice(i - 1, 0, o.splice(i, 1)[0]);
            run(null, function () { return api('server_edit', { op: 'group_order', groups: o }).then(load); });
          } }) : null,
          el('button', { class: 'btn sm', text: '改名', onclick: function () {
            sheet('分组改名', [{ key: 'name', label: '新名称', value: g }], '保存', function (v) { return api('server_edit', { op: 'group_rename', old: g, name: v.name }).then(load); });
          } }),
          el('button', { class: 'btn sm danger', text: '删除', onclick: function () {
            confirmBox('删除分组「' + g + '」', '组里的 ' + (cnt[g] || 0) + ' 台服务器会回到「' + def + '」，服务器本身不受影响。', function () { return api('server_edit', { op: 'group_del', name: g }).then(load); });
          } })));
      });
      box.appendChild(el('div', { class: 'grp-row' }, grpIcon(def), el('b', { text: def }), el('em', { class: 'grp-def', text: '默认' }), el('span', { class: 'grp-c', text: nDef + ' 台' }), el('span', { class: 'sp' }),
        el('button', { class: 'btn sm', text: '改名', onclick: function () {
          sheet('默认组改名', [{ key: 'name', label: '新名称', value: def, ph: '例如：其他 / 待分配' }], '保存', function (v) { return api('server_edit', { op: 'group_default', name: v.name }).then(load); });
        } })));
    }
    function load() { return api('servers').then(draw).catch(function (e) { clear(box).appendChild(failBox(e.message, load)); }); }
    load();
  }
  // 服务器详情里的「解析联动」：IP 变了自动改解析 / 离线自动切换（只有所有者能看到）
  function dnsLinkCard(id) {
    var card = el('div', { class: 'card' }, el('h3', { text: '解析联动' }), el('div', { class: 'hint', text: '读取中…' }));
    function render(d) {
      clear(card);
      card.appendChild(el('h3', { text: '解析联动' }));
      card.appendChild(el('div', { class: 'kv' }, el('span', { text: '服务器 IP' }), el('span', { class: 'mono' + (d.ip ? ' tapcopy' : ''), title: d.ip ? '点击复制' : null, text: d.ip || '—', onclick: d.ip ? function () { copy(d.ip); } : null })));
      if (d.pending_ip) card.appendChild(el('div', { class: 'kv' }, el('span', { text: '新 IP' }), el('span', { class: 'mono', style: 'color:var(--warn)', text: d.pending_ip + '（确认中）' })));
      card.appendChild(el('div', { class: 'kv' }, el('span', { text: '相关解析' }), el('span', { text: d.records.length ? d.records.map(function (r) { return r.name; }).join('、') : '没有' })));
      if (d.parked.length) card.appendChild(el('div', { class: 'kv' }, el('span', { text: '已摘除' }), el('span', { style: 'color:var(--warn)', text: d.parked.map(function (x) { return x.name; }).join('、') })));
      function sw(title, desc, key) {
        var cb = el('input', { type: 'checkbox' }); cb.checked = !!d[key];
        cb.addEventListener('change', function () {
          var p = { op: 'dns', id: id, auto: key === 'auto' ? cb.checked : d.auto, failover: key === 'failover' ? cb.checked : d.failover };
          cb.disabled = true;
          api('server_edit', p).then(function (nd) { toast(cb.checked ? '已开启「' + title + '」' : '已关闭「' + title + '」'); d = nd; render(nd); })
            .catch(function (e) { cb.checked = !cb.checked; cb.disabled = false; toast(e.message, true); });
        });
        card.appendChild(el('label', { class: 'chk dl' }, cb, el('span', null, el('b', { text: title }), el('small', { text: desc }))));
      }
      sw('IP 变了自动改解析', '探针从新 IP 连续上报 3 分钟后，把所有指向旧 IP 的 A 记录改成新 IP。', 'auto');
      sw('离线自动切换', '离线 3 分钟后临时删掉指向这台的 A 记录（同名还有其他在线服务器时才删），恢复在线 2 分钟后按原样加回。', 'failover');
      card.appendChild(el('div', { class: 'hint', text: '有变动会在 Telegram 里通知你。只处理 IPv4（A 记录）。' }));
    }
    api('server_dns', { id: id }).then(render).catch(function (e) {
      clear(card); card.appendChild(el('h3', { text: '解析联动' })); card.appendChild(el('div', { class: 'hint', text: e.message }));
    });
    return card;
  }

  function pageServer(page, id) {
    page.appendChild(top('服务器详情', true));
    var body = el('div', null, skeleton(2));
    page.appendChild(body);
    var sv = null, info = el('div', { style: 'margin-top:12px' }), title, state, my = epoch, cur_s = null;
    function act(name, op, ok) { return el('button', { class: 'btn sm' + (op === 'del' ? ' danger' : ''), text: name, onclick: ok }); }
    function build(s) {
      clear(body);
      title = el('b', { text: s.name }); state = el('span', { class: 'pill' });
      body.appendChild(el('div', { class: 'card' }, el('h3', null, el('i', { class: 'sd' }), title, el('span', { class: 'sp' }), state), el('div', { class: 'sv-body' })));
      sv = statusView(true);
      var cardBody = body.querySelector('.sv-body'); cardBody.appendChild(sv.node); cardBody.appendChild(info);
      body.appendChild(el('div', { class: 'card' }, el('h3', { text: '管理' }),
        el('div', { class: 'acts-row' },
          act('分组', 'group', function () {
            api('servers').then(function (d) {
              if (!d.groups.length) { toast('还没有分组，先到「我的服务器 → 分组」里新建', true); return; }
              sheet('放进分组', [{ key: 'group', label: '分组', options: [['', d.default_group || '未分组']].concat(d.groups.map(function (g) { return [g, g]; })), value: s.group || '' }], '保存', function (v) {
                return api('server_edit', { op: 'group', id: id, group: v.group }).then(function () { s.group = v.group; toast('已放进「' + (v.group || d.default_group || '未分组') + '」'); });
              });
            }).catch(function (e) { toast(e.message, true); });
          }),
          act('重命名', 'rename', function () { sheet('重命名', [{ key: 'name', label: '名称', value: s.name }], '保存', function (v) { return api('server_edit', { op: 'rename', id: id, name: v.name }).then(function () { title.textContent = v.name.trim() || s.name; s.name = title.textContent; }); }); }),
          act('公网 IP', 'ip', function () {
            sheet('这台服务器的公网 IP', [{ key: 'ip', label: '公网 IP（留空 = 不手填，按探针上报的自动识别）', value: s.allow_ip || '', ph: '例如 1.2.3.4' }], '保存', function (v) {
              return api('server_edit', { op: 'allow_ip', id: id, ip: v.ip.trim() }).then(function (r) { s.allow_ip = r.allow_ip; if (r.uam) uamNote(r.uam); else toast(r.allow_ip ? '已保存' : '已清除'); });
            }, el('div', { class: 'hint', text: '网站开 5 秒盾时，只有登记过的服务器 IP 能装探针、上报数据。这台换了 IP 或还没上报过时，在这里填上它的公网 IP。' }));
          }),
          act('安装命令', 'cmd', function () { api('server_edit', { op: 'cmd', id: id }).then(function (r) { showCmd(r.cmd); }).catch(function (e) { toast(e.message, true); }); }),
          act('重置密钥', 'regen', function () { confirmBox('重置密钥', '重置后旧探针会被拒绝，需要在这台服务器上重新执行新的安装命令。', function () { return api('server_edit', { op: 'regen', id: id }).then(function (r) { setTimeout(function () { showCmd(r.cmd, '新的安装命令'); }, 150); }); }); }),
          act('卸载探针', 'uninstall', function () {
            if (!(cur_s && cur_s.online && cur_s.ver >= 2)) { toast('只有在线且已更新到新版探针的机器才能远程卸载；旧版请在目标机器执行：bash /opt/nav-probe/probe.sh uninstall', true); return; }
            confirmBox('卸载探针', '会通知目标机器上的探针停止并删除自己（下一次上报时执行，约几秒），然后这台会从列表里移除。', function () { return api('server_edit', { op: 'uninstall', id: id }).then(function () { toast('已下发，探针正在卸载'); }); });
          }),
          act('删除', 'del', function () { confirmBox('删除服务器', '只会从操作台移除。目标服务器上的探针需要自己卸载：bash /opt/nav-probe/probe.sh uninstall', function () { return api('server_edit', { op: 'del', id: id }).then(function () { backHandler(); }); }); }))));
      if (me && me.owner) body.appendChild(dnsLinkCard(id));
    }
    function refresh() {
      return api('server_get', { id: id }).then(function (s) {
        if (epoch !== my) return;
        cur_s = s;
        if (!sv) build(s);
        var dot = body.querySelector('.card h3 .sd'); if (dot) dot.className = 'sd ' + (s.online ? 'on' : '');
        state.className = 'pill ' + (s.uninstall ? 'warn' : s.online ? 'ok' : 'bad'); state.textContent = s.uninstall ? '卸载中' : s.online ? '在线' : (s.seen ? '离线' : '等待连接');
        if (!s.seen) { clear(info).appendChild(el('div', { class: 'hint', text: '还没收到这台服务器的数据。请在目标服务器上执行「安装命令」。' })); return; }
        sv.update(s);
        clear(info).appendChild(el('div', null,
          el('div', { class: 'kv' }, el('span', { text: '主机' }), el('span', { text: s.host })),
          s.ip4 ? el('div', { class: 'kv' }, el('span', { text: 'IPv4' }), el('span', { class: 'mono tapcopy', title: '点击复制', text: s.ip4, onclick: function () { copy(s.ip4); } })) : null,
          s.ip6 ? el('div', { class: 'kv' }, el('span', { text: 'IPv6' }), el('span', { class: 'mono tapcopy', title: '点击复制', style: 'word-break:break-all;text-align:right', text: s.ip6, onclick: function () { copy(s.ip6); } })) : null,
          el('div', { class: 'kv' }, el('span', { text: '系统' }), el('span', { text: s.os })),
          el('div', { class: 'kv' }, el('span', { text: 'CPU' }), el('span', { style: 'text-align:right;word-break:break-word', text: (s.cpu_model ? s.cpu_model + ' · ' : '') + s.cores + ' 核' })),
          s.steal >= 1 ? el('div', { class: 'kv' }, el('span', { text: '被抢占' }), el('span', { style: s.steal >= 25 ? 'color:var(--bad)' : s.steal >= 5 ? 'color:var(--warn)' : '', text: s.steal + '%（宿主机占用）' })) : null,
          el('div', { class: 'kv' }, el('span', { text: '内存' }), el('span', { text: fmtBytes(s.mem.total) })),
          el('div', { class: 'kv' }, el('span', { text: '硬盘' }), el('span', { text: fmtBytes(s.disk.total) })),
          el('div', { class: 'kv' }, el('span', { text: '负载' }), el('span', { class: 'mono', text: s.load.join('  ') })),
          el('div', { class: 'kv' }, el('span', { text: '累计流量' }), el('span', { text: '↑ ' + fmtBytes(s.net.up_total) + '  ↓ ' + fmtBytes(s.net.down_total) })),
          el('div', { class: 'kv' }, el('span', { text: '已运行' }), el('span', { text: s.uptime || '—' }))));
      }).catch(function (e) {
        if (e.message.indexOf('不存在') >= 0) { toast('这台服务器已移除'); backHandler(); return; }
        if (!sv) { clear(body); body.appendChild(failBox(e.message, function () { go(cur.tab, cur.sub); })); }
      });
    }
    livePoll(refresh);
  }

  // ------------------------------------------------------------ 启动
  if (!initData && !devTok) { pairPage(); return; }
  // 下拉刷新：只显示 iOS 风格动态指示器，不显示任何「下拉刷新 / 松手刷新」文字。
  // 只在主屏幕 App 里启用：Telegram 里往下拉是收起小程序，两者会冲突。
  if (!initData) (function () {
    var ind = el('div', { class: 'ptr' }), y0 = 0, dy = 0, on = false, busy = false, TH = 70;
    // 指示器背景透明，只保留 iOS 风格动态圆环；下拉时页面内容跟手下移。
    ind.style.background = 'transparent';
    ind.style.border = '0';
    ind.style.boxShadow = 'none';
    ind.style.backdropFilter = 'none';
    ind.style.webkitBackdropFilter = 'none';
    var ptrStyle = el('style', { text: '@keyframes ptr-ios-rotate{to{transform:rotate(360deg)}}.ptr-ios-spinner{position:relative;display:inline-block;width:19px;height:19px;vertical-align:middle;animation:ptr-ios-rotate .85s steps(8) infinite}.ptr-ios-spinner i{position:absolute;left:8px;top:1px;width:3px;height:6px;border-radius:2px;background:currentColor;transform-origin:1.5px 8.5px}.ptr-ios-spinner i:nth-child(1){transform:rotate(0deg);opacity:1}.ptr-ios-spinner i:nth-child(2){transform:rotate(45deg);opacity:.88}.ptr-ios-spinner i:nth-child(3){transform:rotate(90deg);opacity:.76}.ptr-ios-spinner i:nth-child(4){transform:rotate(135deg);opacity:.64}.ptr-ios-spinner i:nth-child(5){transform:rotate(180deg);opacity:.52}.ptr-ios-spinner i:nth-child(6){transform:rotate(225deg);opacity:.4}.ptr-ios-spinner i:nth-child(7){transform:rotate(270deg);opacity:.28}.ptr-ios-spinner i:nth-child(8){transform:rotate(315deg);opacity:.16}' });
    document.head.appendChild(ptrStyle);
    function showIosSpinner() {
      if (ind.querySelector('.ptr-ios-spinner')) return; // 不在每次 touchmove 时重置动画
      clear(ind);
      var spinner = el('span', { class: 'ptr-ios-spinner', 'aria-label': '刷新中' });
      for (var si = 0; si < 8; si++) spinner.appendChild(el('i'));
      ind.appendChild(spinner);
    }
    document.body.appendChild(ind);
    function reset() { ind.style.transform = 'translate(-50%,-60px)'; ind.style.opacity = '0'; ind.classList.remove('go'); clear(ind); }
    function settlePage(done) {
      app.style.transition = 'transform .22s cubic-bezier(.2,.75,.25,1)';
      app.style.transform = 'translateY(0px)';
      setTimeout(function () {
        app.style.transition = '';
        app.style.transform = '';
        app.style.willChange = '';
        if (done) done();
      }, 230);
    }
    reset();
    document.addEventListener('touchstart', function (e) {
      on = false; dy = 0;
      if (busy || e.touches.length !== 1 || (window.scrollY || document.documentElement.scrollTop || app.scrollTop) > 0) return;
      if (document.querySelector('.sheet-bg, .zoom') || /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || ''))) return;
      y0 = e.touches[0].clientY; on = true;
    }, { passive: true });
    document.addEventListener('touchmove', function (e) {
      if (!on) return;
      dy = e.touches[0].clientY - y0;
      if (dy <= 0) { reset(); return; }
      var d = Math.min(dy * .5, 90);
      showIosSpinner(); // 任意下拉距离都只显示同一个 iOS 动态指示器，不显示文字状态
      app.style.transition = 'none';
      app.style.willChange = 'transform';
      app.style.transform = 'translateY(' + d + 'px)';
      ind.style.transform = 'translate(-50%,' + (d - 40) + 'px)';
      ind.style.opacity = String(Math.min(1, d / 50));
    }, { passive: true });
    function end() {
      if (!on) return; on = false;
      var fire = dy * .5 >= TH * .5;
      if (fire && cur) {
        showIosSpinner();
        ind.style.transform = 'translate(-50%,10px)'; ind.style.opacity = '1';
        busy = true; hap('ok');
        // 等本轮页面刷新请求全部结束，再额外停留 0.2 秒；隐藏指示器后 0.10 秒再回弹。
        // 每轮使用独立代号，超时后迟到的旧请求不能修改新一轮的计数。
        ptrRefreshGeneration++;
        ptrRefreshPending = 0;
        ptrRefreshLastDone = Date.now();
        ptrRefreshTracking = true;
        try { go(cur.tab, cur.sub, true); } catch (e) {}
        var refreshWaitStart = Date.now();
        (function waitRefreshDone() {
          var now = Date.now();
          var idleDone = ptrRefreshPending === 0 && now - ptrRefreshLastDone >= 200;
          // 兜底：异常请求不能让页面永久停在下拉状态。
          if (idleDone || now - refreshWaitStart >= 3000) {
            ptrRefreshTracking = false;
            reset();
            setTimeout(function () {
              settlePage(function () { busy = false; });
            }, 100);
          } else {
            setTimeout(waitRefreshDone, 50);
          }
        })();
      } else {
        settlePage();
        reset();
      }
    }
    document.addEventListener('touchend', end, { passive: true });
    document.addEventListener('touchcancel', function () { on = false; settlePage(); reset(); }, { passive: true });
    // 左边缘右滑返回（和 iOS 原生一样）：从屏幕最左边往右拖，当前页跟着手往右走，下面垫着上一页的画面；
    // 拖过三分之一或快速一划就返回上一页，不够就弹回
    var ex = 0, ey = 0, et = 0, edx = 0, st = 0, under = null, dim = null, prev = null;   // st：0 没在拖，1 刚按下还没判断方向，2 正在拖
    function edgeClean() {
      if (under && under.parentNode) under.remove();
      under = dim = null;
      app.style.transition = app.style.transform = app.style.boxShadow = app.style.background = app.style.willChange = app.style.zIndex = app.style.position = app.style.minHeight = '';
    }
    document.addEventListener('touchstart', function (e) {
      st = 0;
      if (!cur || !cur.sub || e.touches.length !== 1 || e.touches[0].clientX > 24) return;
      if (document.querySelector('.sheet-bg, .zoom') || !app.firstChild) return;
      ex = e.touches[0].clientX; ey = e.touches[0].clientY; et = Date.now(); edx = 0; st = 1;
    }, { passive: true });
    document.addEventListener('touchmove', function (e) {
      if (!st) return;
      var dx = e.touches[0].clientX - ex, dy = e.touches[0].clientY - ey;
      if (st === 1) {
        if (Math.abs(dy) > 10 && Math.abs(dy) > dx) { st = 0; return; }     // 竖着滑：交给页面滚动
        if (dx < 10) return;
        st = 2; on = false; reset();                                           // 确定是横着拖：取消下拉刷新
        edgeClean();
        var cs = getComputedStyle(app), bgc = getComputedStyle(document.body).backgroundColor;
        if (!bgc || bgc === 'rgba(0, 0, 0, 0)' || bgc === 'transparent') bgc = root.getAttribute('data-theme') === 'light' ? '#f2f2f7' : '#000';
        prev = navStack[navStack.length - 1] || null;
        // 下面垫一层：上一页离开时留下的画面（只用来看，松手后照常重新载入上一页）
        under = el('div', { class: 'swipe-under' });
        under.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;overflow:hidden;pointer-events:none;background:' + bgc;
        var inner = el('div');
        inner.style.cssText = 'max-width:720px;margin:0 auto;box-sizing:border-box;padding:' + cs.paddingTop + ' ' + cs.paddingRight + ' 0 ' + cs.paddingLeft + ';transform:translateY(' + (-(prev && prev.sy) || 0) + 'px)';
        if (prev && prev.snap) { prev.snap.style.animation = 'none'; inner.appendChild(prev.snap); }
        dim = el('div'); dim.style.cssText = 'position:absolute;left:0;top:0;right:0;bottom:0;background:#000;opacity:.28';
        under.appendChild(inner); under.appendChild(dim);
        document.body.insertBefore(under, app);
        if (cs.position === 'static') app.style.position = 'relative';
        app.style.zIndex = '1'; app.style.minHeight = '100vh'; app.style.background = bgc; app.style.willChange = 'transform'; app.style.transition = 'none';
        app.style.boxShadow = '-10px 0 28px rgba(0,0,0,.35)';
        if (app.firstChild && app.firstChild.style) app.firstChild.style.animation = 'none';
      }
      e.preventDefault();
      edx = Math.max(0, dx);
      var w = window.innerWidth || 375, k = Math.min(1, edx / w);
      app.style.transform = 'translateX(' + edx + 'px)';
      under.style.transform = 'translateX(' + (-(1 - k) * w * 0.3) + 'px)';      // 上一页从左边稍微缩进的位置跟着移回来（视差）
      dim.style.opacity = String(0.28 * (1 - k));
    }, { passive: false });
    function edgeEnd() {
      if (st !== 2) { st = 0; return; }
      st = 0;
      var w = window.innerWidth || 375, fast = edx > 40 && edx / Math.max(1, Date.now() - et) > 0.5, u = under, d = dim, p = prev;
      var ease = 'transform .22s cubic-bezier(.2,.8,.2,1)';
      app.style.transition = ease; u.style.transition = ease; d.style.transition = 'opacity .22s';
      if (edx > w / 3 || fast) {
        app.style.transform = 'translateX(' + w + 'px)'; u.style.transform = 'translateX(0)'; d.style.opacity = '0';
        setTimeout(function () {
          // 上一页在屏幕外（右边）重新载入，这段时间继续显示垫在下面的旧画面；
          // 等新页面的数据到了（没有灰色占位块了，最多等 1.5 秒）再一下子换上，看不到「先灰块再出内容」的闪动
          app.style.transition = 'none';
          backHandler();
          if (app.firstChild && app.firstChild.style) app.firstChild.style.animation = 'none';   // 已经看到上一页了，不再淡入一次
          var t0 = Date.now(), pg0 = app.firstChild;
          (function swap() {
            if (app.firstChild === pg0 && app.querySelector('.skeleton') && Date.now() - t0 < 1500) { setTimeout(swap, 40); return; }
            if (p && p.sy) app.scrollTop = p.sy;
            app.style.transform = '';
            requestAnimationFrame(function () { if (p && p.snap && p.snap.parentNode) p.snap.remove(); edgeClean(); });
          })();
        }, 230);
      } else {
        app.style.transform = 'translateX(0)'; u.style.transform = 'translateX(' + (-w * 0.3) + 'px)'; d.style.opacity = '.28';
        setTimeout(function () { if (p && p.snap && p.snap.parentNode) p.snap.remove(); edgeClean(); }, 240);
      }
    }
    document.addEventListener('touchend', edgeEnd, { passive: true });
    document.addEventListener('touchcancel', edgeEnd, { passive: true });
  })();
  drawTabs();
  // 刚因为 5 秒盾验证刷新过：回到刷新前的页面
  var resume = null;
  try { resume = JSON.parse(sessionStorage.getItem('mini_resume') || 'null'); sessionStorage.removeItem('mini_resume'); } catch (e) {}
  if (resume && Date.now() - resume.t < 60000 && /^(home|nav|tools|dns|docs|admin)$/.test(resume.tab)) {
    go('home');
    if (resume.tab !== 'home' || resume.sub) go(resume.tab, resume.sub);
  } else go('home');
})();
