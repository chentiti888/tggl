#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
功能导航 Mini App 接口（和机器人同一个进程运行，不用改 dh.py）

用法：把本文件放在 dh.py 旁边，把原来的启动命令
    python3 dh.py
改成
    python3 miniapp_api.py
机器人照常工作；同时在 127.0.0.1 开一个只给本机用的接口 (端口默认 8787,
可用环境变量 MINIAPP_PORT 或配置文件里的 port 修改)，
PHP 页面（api.php）把 Mini App 的请求转发到这里。

安全：
  1. 只监听 127.0.0.1，外网访问不到；
  2. 每个请求必须带 PHP 和这里共用的密钥（X-Api-Key）；
  3. 每个请求必须带 Telegram 签名的 initData，这里用机器人 Token 校验签名，
     再用 dh.py 自己的权限函数判断这个人能不能用（和机器人完全一致）。
只用 Python 标准库，不需要额外安装任何依赖。
"""
import asyncio
from collections import deque
import base64
import calendar
import hashlib
import hmac
import html as _html
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dh  # noqa: E402  复用机器人里已有的全部功能

# 数据目录和 dh.py 一致: 默认程序所在目录, 环境变量 NAV_DATA_DIR 可改; 旧的 /root 文件自动搬过来
DATA_DIR = os.environ.get("NAV_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
os.makedirs(DATA_DIR, exist_ok=True)


def _data_path(name):
    new = os.path.join(DATA_DIR, name)
    old = os.path.join("/root", name)
    if os.path.abspath(old) != os.path.abspath(new) and os.path.exists(old) and not os.path.exists(new):
        try:
            os.replace(old, new)
            print(f"已把旧数据 {old} 移到 {new}")
        except OSError:
            try:
                import shutil
                shutil.move(old, new)
            except Exception as e:
                print(f"迁移旧数据 {old} 失败, 继续使用旧位置: {e}")
                return old
    return new


CONF_FILE = os.environ.get("MINIAPP_CONF") or _data_path("nav_miniapp.json")
MAX_BODY = 64 * 1024
SSH_BODY_MAX = 64 * 1024 * 1024     # 远程服务器上传分块的请求上限（其他接口仍是 MAX_BODY）
JOB_KEEP = 1800          # 任务结果保留 30 分钟
JOB_PER_USER = 2         # 每人同时最多跑 2 个任务

# ---------------------------------------------------------------- 配置


def load_conf():
    conf = {"api_key": "", "web_url": "", "host": "127.0.0.1", "port": 8787, "max_age": 86400}
    try:
        with open(CONF_FILE, "r", encoding="utf-8") as f:
            conf.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print("Mini App 配置读取失败：", e)
    if not conf.get("api_key"):
        conf["api_key"] = secrets.token_hex(24)
        try:
            with open(CONF_FILE, "w", encoding="utf-8") as f:
                json.dump(conf, f, ensure_ascii=False, indent=2)
            os.chmod(CONF_FILE, 0o600)
            print(f"Mini App：已生成密钥并保存到 {CONF_FILE}")
        except Exception as e:
            print("Mini App 配置保存失败：", e)
    return conf


CONF = load_conf()
if (os.environ.get("MINIAPP_PORT") or "").strip().isdigit():   # 环境变量优先 (部署脚本分配的端口)
    CONF["port"] = int(os.environ.get("MINIAPP_PORT").strip())

# ---------------------------------------------------------------- 校验 initData


class ApiError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def verify_init_data(init_data, token, max_age):
    """校验 Telegram Mini App 的 initData，返回 user dict；不合法抛 ApiError。"""
    if not init_data or not token:
        raise ApiError("缺少登录信息，请从机器人里打开", 401)
    pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    got = pairs.pop("hash", "")
    if not got:
        raise ApiError("登录信息不完整", 401)
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        raise ApiError("登录签名不正确", 401)
    try:
        age = time.time() - int(pairs.get("auth_date", "0"))
    except ValueError:
        age = 10 ** 9
    if age > max_age:
        raise ApiError("登录已过期，请关闭后重新打开", 401)
    try:
        user = json.loads(pairs.get("user", "{}"))
        user["id"] = int(user["id"])
    except Exception:
        raise ApiError("登录信息里没有用户", 401)
    return user


# ---------------------------------------------------------------- 小工具

TAG_RE = re.compile(r"<[^>]+>")


def plain(text):
    return _html.unescape(TAG_RE.sub("", text or ""))


def b64(data):
    if not data:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def need_rate(uid, cost=1):
    limited = dh.rate_check(uid, cost)
    if limited:
        raise ApiError(limited, 429)


def clean(text, limit):
    text = (text or "").strip()
    if not text:
        raise ApiError("内容不能为空")
    if len(text) > limit:
        raise ApiError(f"太长了，最多 {limit} 个字")
    return text


def norm_url(url):
    url = (url or "").strip()
    if not url:
        raise ApiError("网址不能为空")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if len(url) > 500 or " " in url:
        raise ApiError("网址格式不正确")
    return url


def target_host(text):
    target = dh._parse_ping_target(text or "")
    if target is None:
        raise ApiError("地址格式不正确，请输入 IP 或域名")
    return target[0], target[1]


ACTIONS = {}
OWNER_ONLY = {"users", "users_edit", "cf", "cf_edit",       # 授权用户管理 / Cloudflare 解析 / 安全防护 / R2：只有所有者能用
              "cf_sec", "cf_sec_edit", "cf_events", "r2", "r2_bucket", "r2_list", "r2_edit"}


def action(name, admin=False):
    def deco(fn):
        ACTIONS[name] = (fn, admin)
        return fn
    return deco


# ---------------------------------------------------------------- 首页 / 主机状态


@action("home")
async def a_home(ctx, p):
    cats = _nav_cats(ctx)
    uptime = None
    try:
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
    except Exception:
        pass
    return {
        "greeting": dh._greeting(),
        "name": ctx["name"],
        "uid": ctx["uid"],
        "admin": ctx["admin"],
        "owner": dh.is_owner(ctx["uid"]),
        "categories": len(cats),
        "sites": sum(len(v) for v in cats.values()),
        "notes": sum(len(c.get("items", [])) for c in dh._note_cats(ctx["uid"])),
        "uptime": dh._fmt_duration(uptime) if uptime else None,
        "bot_uptime": dh._fmt_duration(time.time() - dh.BOT_STARTED),
        "time": dh._fmt_time(time.time(), "%Y-%m-%d %H:%M:%S"),
        "servers": servers_summary(ctx["uid"]) if ctx["admin"] else None,
    }


@action("status", admin=True)
async def a_status(ctx, p):
    cpu1, idle1 = dh._read_cpu()
    rx1, tx1 = dh._read_net()
    await asyncio.sleep(1)
    cpu2, idle2 = dh._read_cpu()
    rx2, tx2 = dh._read_net()
    cpu = 100 * (1 - (idle2 - idle1) / max(1, cpu2 - cpu1))
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, value = line.split(":", 1)
            mem[key] = int(value.split()[0]) * 1024
    total = mem.get("MemTotal", 0)
    used = total - mem.get("MemAvailable", mem.get("MemFree", 0))
    disk = dh.shutil.disk_usage("/")
    with open("/proc/uptime") as f:
        uptime = float(f.read().split()[0])
    load = os.getloadavg()
    return {
        "host": dh.socket.gethostname(),
        "os": f"{dh.platform.system()} {dh.platform.release()}",
        "cores": os.cpu_count(),
        "cpu": round(cpu, 1),
        "mem": {"used": used, "total": total, "pct": round(100 * used / max(1, total), 1)},
        "disk": {"used": disk.used, "total": disk.total,
                 "pct": round(100 * disk.used / max(1, disk.total), 1)},
        "load": [round(x, 2) for x in load],
        "net": {"up": tx2 - tx1, "down": rx2 - rx1, "up_total": tx2, "down_total": rx2},
        "uptime": dh._fmt_duration(uptime),
        "bot_uptime": dh._fmt_duration(time.time() - dh.BOT_STARTED),
        "time": dh._fmt_time(time.time(), "%H:%M:%S"),
    }


# ---------------------------------------------------------------- 美元汇率（首页顶部）

FX = {"rate": None, "prev": None, "time": 0, "try": 0}


def _fx_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read().decode("utf-8"))


def _fx_fetch():
    try:
        d = _fx_json("https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=usd,cny")["tether"]
        return float(d["cny"]) / float(d["usd"])
    except Exception:
        pass
    try:
        return float(_fx_json("https://open.er-api.com/v6/latest/USD")["rates"]["CNY"])
    except Exception:
        return None


@action("fx")
async def a_fx(ctx, p):
    now = time.time()
    if now - FX["try"] >= 30:          # 最多每 30 秒真正去取一次，多人同时看也只取一次
        FX["try"] = now
        v = await asyncio.to_thread(_fx_fetch)
        if v:
            if FX["rate"] is not None and abs(v - FX["rate"]) > 1e-9:
                FX["prev"] = FX["rate"]
            FX["rate"] = v
            FX["time"] = now
    if not FX["rate"] or now - FX["time"] > 3600:
        raise ApiError("暂时取不到汇率")
    return {"rate": round(FX["rate"], 4), "prev": round(FX["prev"], 4) if FX["prev"] else None,
            "time": dh._fmt_time(FX["time"], "%H:%M:%S")}


# ---------------------------------------------------------------- 网站导航


def _nav_cats(ctx, create=False):
    """导航数据：所有者用机器人自己的那份（和机器人里的导航同一份）；
    授权用户各用各的，互相看不到，也看不到所有者的。"""
    if dh.is_owner(ctx["uid"]):
        return dh.data.setdefault("categories", {}) if create else dh.data.get("categories", {})
    box = dh.data.setdefault("mini_nav", {}) if create else dh.data.get("mini_nav", {})
    uid = str(ctx["uid"])
    if create:
        return box.setdefault(uid, {})
    return box.get(uid, {})


@action("nav")
async def a_nav(ctx, p):
    return {"categories": [
        {"name": name, "sites": [{"name": s.get("name", ""), "url": s.get("url", "")}
                                 for s in sites]}
        for name, sites in _nav_cats(ctx).items()
    ]}


def _cat(ctx, name):
    cats = _nav_cats(ctx, True)
    if name not in cats:
        raise ApiError("分类不存在")
    return cats[name]


def _cat_name(name):
    name = (name or "").strip()
    if not name:
        raise ApiError("分类名称不能为空")
    err = dh._category_name_error(name)
    if err:
        raise ApiError(plain(err).replace("❌", "").strip())
    return name


@action("nav_edit", admin=True)
async def a_nav_edit(ctx, p):
    op = p.get("op")
    cats = _nav_cats(ctx, True)
    if op == "add_cat":
        name = _cat_name(p.get("name"))
        if name in cats:
            raise ApiError("已经有同名分类了")
        cats[name] = []
    elif op == "rename_cat":
        old, name = p.get("cat"), _cat_name(p.get("name"))
        _cat(ctx, old)
        if name != old and name in cats:
            raise ApiError("已经有同名分类了")
        renamed = {(name if k == old else k): v for k, v in cats.items()}
        cats.clear()
        cats.update(renamed)
    elif op == "del_cat":
        _cat(ctx, p.get("cat"))
        cats.pop(p["cat"])
    elif op in ("add_site", "edit_site", "del_site"):
        sites = _cat(ctx, p.get("cat"))
        if op == "add_site":
            sites.append({"name": clean(p.get("name"), 40), "url": norm_url(p.get("url"))})
        else:
            try:
                idx = int(p.get("index"))
                sites[idx]
            except Exception:
                raise ApiError("网站不存在")
            if op == "edit_site":
                sites[idx] = {"name": clean(p.get("name"), 40), "url": norm_url(p.get("url"))}
            else:
                sites.pop(idx)
    else:
        raise ApiError("未知操作")
    dh.save_data(dh.data)
    return await a_nav(ctx, p)


# ---------------------------------------------------------------- 导航网站图标（服务器代取 + 缓存）
# 手机直接请求谷歌图标服务在国内经常失败，这里改由服务器去取，取到后存在本机，下次直接返回。
# 顺序：谷歌图标服务 → 网站自己的 /favicon.ico → 网站首页里 <link rel="icon"> 指向的图。
# 只处理导航里已经有的域名，并拒绝解析到内网地址的域名，避免被拿来探测内网。

ICON_DIR = os.path.join(DATA_DIR, "nav_icons")
ICON_OK_TTL = 30 * 86400        # 取到了：30 天后再更新
ICON_FAIL_TTL = 86400           # 没取到：1 天后再试
ICON_MAX = 64 * 1024            # 单个图标最大 64KB
ICON_HTML_MAX = 300 * 1024
ICON_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
ICON_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
ICON_TASKS = {}                 # host -> 正在取的后台任务（同一个域名不重复取）
ICON_VER = 2                    # 取图标的办法改了就加 1
ICON_SEM = asyncio.Semaphore(6)


def _icon_public(host):
    """域名解析出来的地址必须全部是公网地址。"""
    try:
        infos = dh.socket.getaddrinfo(host, None)
    except Exception:
        return False
    addrs = {i[4][0] for i in infos}
    try:
        return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)
    except ValueError:
        return False


class _IconRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlsplit(newurl)
        if u.scheme not in ("http", "https") or not u.hostname or not _icon_public(u.hostname):
            return None                  # 跳到内网或奇怪的地址：不跟
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_ICON_OPENER = urllib.request.build_opener(_IconRedirect)


def _icon_get(url, limit):
    """GET 一个地址，返回 (内容, 最终地址, content-type)；失败或超出大小返回 None。"""
    req = urllib.request.Request(url, headers={"User-Agent": ICON_UA, "Accept": "*/*"})
    try:
        with _ICON_OPENER.open(req, timeout=6) as r:
            if r.status != 200:
                return None
            body = r.read(limit + 1)
            if len(body) > limit:
                return None
            return body, r.geturl(), (r.headers.get("Content-Type") or "").lower()
    except Exception:
        return None


def _icon_kind(b):
    """认出图片格式，返回 (mime, 宽度)；不是图片返回 (None, 0)。宽度未知时给 32。"""
    if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) >= 24:
        return "image/png", int.from_bytes(b[16:20], "big")
    if b[:4] == b"\x00\x00\x01\x00" and len(b) >= 22:
        n = int.from_bytes(b[4:6], "little")
        widths = [(b[6 + 16 * i] or 256) for i in range(n) if len(b) >= 6 + 16 * i + 16]
        return "image/x-icon", max(widths or [0])
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg", 32
    if b[:6] in (b"GIF87a", b"GIF89a") and len(b) >= 10:
        return "image/gif", int.from_bytes(b[6:8], "little")
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp", 32
    head = b[:512].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "image/svg+xml", 32
    return None, 0


def _icon_data(b, min_w=16):
    mime, w = _icon_kind(b)
    if not mime or w < min_w:
        return None
    return f"data:{mime};base64," + base64.b64encode(b).decode()


_ICON_LINK_RE = re.compile(r"<link\b[^>]*>", re.I)


def _icon_links(html, base):
    """从首页 HTML 里找图标地址，大图标（apple-touch-icon）优先。"""
    found = []
    for tag in _ICON_LINK_RE.findall(html):
        rel = re.search(r'rel\s*=\s*["\']?([^"\'>]+)', tag, re.I)
        href = re.search(r'href\s*=\s*["\']?([^"\'\s>]+)', tag, re.I)
        if not rel or not href or "icon" not in rel.group(1).lower():
            continue
        u = urllib.parse.urljoin(base, _html.unescape(href.group(1)))
        if u.startswith(("http://", "https://")):
            found.append((0 if "apple-touch" in rel.group(1).lower() else 1, u))
    found.sort(key=lambda x: x[0])
    return [u for _p, u in found][:4]


def _icon_parents(host):
    """子域名取不到时退到上一级域名：portal.orangevps.com → orangevps.com（不退到 com 这种后缀）。"""
    parts = host.split(".")
    out = []
    while len(parts) > 2:
        parts = parts[1:]
        if len(parts) == 2 and len(parts[0]) <= 3 and len(parts[1]) == 2:   # com.cn / co.uk 这类不算
            break
        out.append(".".join(parts))
    return out


def _icon_services(host):
    """公共图标服务：谷歌 → DuckDuckGo。"""
    g = _icon_get(f"https://www.google.com/s2/favicons?domain={urllib.parse.quote(host)}&sz=64", ICON_MAX)
    if g:
        d = _icon_data(g[0], min_w=17)            # 谷歌没有时会给一个 16px 的默认地球
        if d:
            return d
    r = _icon_get(f"https://icons.duckduckgo.com/ip3/{urllib.parse.quote(host)}.ico", ICON_MAX)
    if r:
        d = _icon_data(r[0])
        if d:
            return d
    return None


def _icon_fetch_sync(host):
    """真正去取图标（在线程里跑）。返回 data URI 或 None。"""
    if not ICON_HOST_RE.match(host) or not _icon_public(host):
        return None
    d = _icon_from_site(host)
    if d:
        return d
    for parent in _icon_parents(host):
        if _icon_public(parent):
            d = _icon_from_site(parent)
            if d:
                return d
    return None


def _icon_from_site(host):
    d = _icon_services(host)
    if d:
        return d
    r = _icon_get(f"https://{host}/favicon.ico", ICON_MAX)
    if r:
        d = _icon_data(r[0])
        if d:
            return d
    page = _icon_get(f"https://{host}/", ICON_HTML_MAX)
    if page and ("html" in page[2] or not page[2]):
        html = page[0].decode("utf-8", "replace")
        for u in _icon_links(html, page[1]):
            h = urllib.parse.urlsplit(u).hostname
            if not h or not _icon_public(h):
                continue
            r = _icon_get(u, ICON_MAX)
            if r:
                d = _icon_data(r[0])
                if d:
                    return d
    return None


def _icon_path(host):
    return os.path.join(ICON_DIR, host + ".json")


def _icon_cached(host):
    """返回 (是否还新鲜, data URI 或 None)；没有缓存返回 (False, None)。"""
    try:
        with open(_icon_path(host), "r", encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        return False, None
    d = c.get("d") or None
    fresh = time.time() - float(c.get("t") or 0) < (ICON_OK_TTL if d else ICON_FAIL_TTL)
    if not d and c.get("v") != ICON_VER:       # 取图标的办法更新过：以前没取到的马上重试
        fresh = False
    return fresh, d


def _icon_save(host, data):
    try:
        os.makedirs(ICON_DIR, exist_ok=True)
        tmp = _icon_path(host) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "d": data or "", "v": ICON_VER}, f)
        os.replace(tmp, _icon_path(host))
    except Exception as e:
        print("保存导航图标失败：", host, e)


async def _icon_refresh(host):
    async with ICON_SEM:
        try:
            data = await asyncio.to_thread(_icon_fetch_sync, host)
        except Exception as e:
            print("取导航图标出错：", host, repr(e))
            data = None
    old_fresh, old = _icon_cached(host)
    if data or not old:                 # 这次没取到但以前有：保留旧图标
        _icon_save(host, data)
    else:
        _icon_save(host, old)
    return data or old


def _icon_task(host):
    t = ICON_TASKS.get(host)
    if t is None or t.done():
        t = asyncio.get_running_loop().create_task(_icon_refresh(host))
        ICON_TASKS[host] = t
        t.add_done_callback(lambda _t, h=host: ICON_TASKS.pop(h, None) if ICON_TASKS.get(h) is _t else None)
    return t


def _nav_hosts(ctx):
    hosts = set()
    for sites in (_nav_cats(ctx) or {}).values():
        for st in sites or []:
            try:
                h = (urllib.parse.urlsplit(str(st.get("url") or "")).hostname or "").lower().rstrip(".")
            except Exception:
                h = ""
            if h:
                hosts.add(h)
    return hosts


@action("favicons")
async def a_favicons(ctx, p):
    want = [str(h or "").strip().lower().rstrip(".") for h in (p.get("hosts") or [])][:80]
    allowed = _nav_hosts(ctx)
    icons, waiting = {}, {}
    for h in dict.fromkeys(want):
        if h not in allowed or not ICON_HOST_RE.match(h):
            continue
        fresh, d = _icon_cached(h)
        icons[h] = d
        if not fresh:
            waiting[h] = _icon_task(h)     # 过期或没有：后台去取（有旧的先用旧的）
    if waiting:
        done, _pending = await asyncio.wait(list(waiting.values()), timeout=12)
        for h, t in waiting.items():
            if t in done and not t.cancelled() and t.exception() is None:
                icons[h] = t.result()
    pending = [h for h, t in waiting.items() if not t.done()]
    return {"icons": icons, "pending": pending}


# ---------------------------------------------------------------- 授权用户


@action("users", admin=True)
async def a_users(ctx, p):
    return {
        "owners": [str(i) for i in dh.ADMIN_IDS],
        "users": [{"id": k, "note": v or ""} for k, v in dh._allowed_users().items()],
    }


@action("users_edit", admin=True)
async def a_users_edit(ctx, p):
    op = p.get("op")
    users = dh._allowed_users()
    if op == "add":
        uid = str(p.get("id", "")).strip()
        if not uid.isdigit():
            raise ApiError("用户 ID 必须是数字")
        users[uid] = (p.get("note") or "").strip()[:20]
    elif op == "del":
        users.pop(str(p.get("id")), None)
    else:
        raise ApiError("未知操作")
    dh.save_data(dh.data)
    return await a_users(ctx, p)


# ---------------------------------------------------------------- 记事本


def _notes_out(uid):
    return {"cats": [
        {"name": c.get("name", ""),
         "items": [{"title": i.get("title", ""), "text": i.get("text", ""), "time": i.get("time", 0)}
                   for i in c.get("items", [])]}
        for c in dh._note_cats(uid)
    ]}


@action("notes")
async def a_notes(ctx, p):
    return _notes_out(ctx["uid"])


def _note_title(v):
    """标题可以留空；最多 40 个字，不能换行。"""
    v = " ".join(str(v or "").split())
    if len(v) > 40:
        raise ApiError("标题太长了，最多 40 个字")
    return v


@action("notes_edit")
async def a_notes_edit(ctx, p):
    uid, op = ctx["uid"], p.get("op")
    cats = dh._note_cats(uid)
    try:
        ci = int(p.get("cat", -1))
    except (TypeError, ValueError):
        ci = -1
    if op == "add_cat":
        if len(cats) >= dh.NOTE_CAT_LIMIT:
            raise ApiError(f"最多只能建 {dh.NOTE_CAT_LIMIT} 个分类")
        name = (p.get("name") or "").strip()
        err = dh._note_cat_name_error(uid, name)
        if err:
            raise ApiError(plain(err).replace("❌", "").strip())
        cats.append({"name": name, "items": []})
    else:
        cat = dh._note_cat(uid, ci)
        if cat is None:
            raise ApiError("分类不存在")
        if op == "rename_cat":
            name = (p.get("name") or "").strip()
            err = dh._note_cat_name_error(uid, name, skip=ci)
            if err:
                raise ApiError(plain(err).replace("❌", "").strip())
            cat["name"] = name
        elif op == "del_cat":
            cats.pop(ci)
        elif op == "add":
            item = {"text": clean(p.get("text"), dh.NOTE_MAX_LEN), "time": int(time.time())}
            title = _note_title(p.get("title"))
            if title:
                item["title"] = title
            cat["items"].append(item)
        elif op == "edit":
            try:
                item = cat["items"][int(p.get("index"))]
            except Exception:
                raise ApiError("这条已经不存在了")
            item["text"] = clean(p.get("text"), dh.NOTE_MAX_LEN)
            title = _note_title(p.get("title"))
            if title:
                item["title"] = title
            else:
                item.pop("title", None)
        elif op == "del":
            try:
                cat["items"].pop(int(p.get("index")))
            except Exception:
                raise ApiError("这条已经不存在了")
        else:
            raise ApiError("未知操作")
    dh.save_data(dh.data)
    return _notes_out(uid)


# ---------------------------------------------------------------- Cloudflare 域名解析
# Token 只放在服务器上：环境变量 CF_API_TOKEN，或 nav_miniapp.json 里的 "cf_token"。
# 建议在 Cloudflare 建一个只有「区域:读取 + DNS:编辑」权限、并限定到你自己域名的 Token。
# 只有所有者能用（OWNER_ONLY）；每次增 / 改 / 删都会追加一行到 cf_audit.jsonl。

CF_API = "https://api.cloudflare.com/client/v4"
CF_PROXIABLE = ("A", "AAAA", "CNAME")
CF_EDIT_TYPES = ("A", "AAAA", "CNAME", "TXT", "MX", "NS")   # 其它类型(SRV/CAA 等)只能查看和删除
CF_AUDIT_FILE = _data_path("cf_audit.jsonl")
CF_ZONES = {"ts": 0, "items": []}
CF_HEX32 = re.compile(r"^[0-9a-f]{32}$")
CF_NAME_RE = re.compile(r"^(\*\.)?[a-z0-9_]([a-z0-9_\-.]*[a-z0-9_])?$")
CF_HOST_RE = re.compile(r"^[a-z0-9_]([a-z0-9_\-.]*[a-z0-9_])?$")


def _cf_token():
    tok = (os.environ.get("CF_API_TOKEN") or CONF.get("cf_token") or "").strip()
    if not tok:
        raise ApiError("还没有配置 Cloudflare Token：在服务器环境变量设置 CF_API_TOKEN，"
                       "或在 nav_miniapp.json 里加 \"cf_token\"，然后重启机器人", 400)
    return tok


CF_NEED_DNS = "（需要「区域:读取」和「DNS:编辑」）"
CF_NEED_PURGE = "（清除缓存需要给 Token 加「区域 → 缓存清除 → 清除」权限）"


class CfPermError(ApiError):
    """Token 权限不够（401 / 403）。"""


class CfNotFound(ApiError):
    """Cloudflare 返回 404（例如还没建过的规则集）。"""


def _cf_call_sync(method, path, body=None, query=None, need=CF_NEED_DNS):
    url = CF_API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + _cf_token(),
        "Content-Type": "application/json",
        "User-Agent": "nav-miniapp/1.0",
    })
    status = 200
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except Exception as e:
        raise ApiError("连接不上 Cloudflare：" + str(getattr(e, "reason", e))[:80], 502)
    try:
        j = json.loads(raw.decode() or "{}")
    except Exception:
        raise ApiError("Cloudflare 返回的内容无法识别", 502)
    if status in (401, 403):
        raise CfPermError("Cloudflare Token 无效或权限不够" + (need or ""), 400)
    if not j.get("success"):
        msgs = [str(e.get("message") or "") for e in (j.get("errors") or []) if isinstance(e, dict)]
        text = "Cloudflare：" + ("；".join(m for m in msgs if m) or f"请求失败 ({status})")[:200]
        raise (CfNotFound if status == 404 else ApiError)(text)
    return j


async def _cf_call(method, path, body=None, query=None, need=CF_NEED_DNS):
    return await asyncio.to_thread(_cf_call_sync, method, path, body, query, need)


async def _cf_zones(force=False):
    if not force and CF_ZONES["items"] and time.time() - CF_ZONES["ts"] < 60:
        return CF_ZONES["items"]
    items, page = [], 1
    while page <= 5:
        j = await _cf_call("GET", "/zones", query={"per_page": 50, "page": page})
        items += [{"id": z["id"], "name": z["name"], "status": z.get("status", ""), "paused": bool(z.get("paused")),
                   "plan": ((z.get("plan") or {}).get("legacy_id") or "free"),
                   "account": {"id": (z.get("account") or {}).get("id"), "name": (z.get("account") or {}).get("name")}}
                  for z in j.get("result") or []]
        if page >= int((j.get("result_info") or {}).get("total_pages", 1)):
            break
        page += 1
    items.sort(key=lambda z: z["name"])
    CF_ZONES.update(ts=time.time(), items=items)
    return items


async def _cf_zone(p):
    zid = str(p.get("zone") or "")
    if not CF_HEX32.match(zid):
        raise ApiError("域名参数不正确")
    for force in (False, True):
        for z in await _cf_zones(force):
            if z["id"] == zid:
                return z
    raise ApiError("这个域名不在当前 Token 的权限范围内", 404)


def _cf_ts(s):
    try:
        return calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0


def _cf_view(r):
    typ = r.get("type", "")
    return {"id": r.get("id"), "type": typ, "name": r.get("name", ""), "content": r.get("content", ""),
            "ttl": r.get("ttl", 1), "proxied": bool(r.get("proxied")), "proxiable": typ in CF_PROXIABLE,
            "priority": r.get("priority"), "comment": r.get("comment") or "",
            "modified": _cf_ts(r.get("modified_on")), "editable": typ in CF_EDIT_TYPES}


async def _cf_get_record(zone, p):
    rid = str(p.get("id") or "")
    if not CF_HEX32.match(rid):
        raise ApiError("记录参数不正确")
    try:
        j = await _cf_call("GET", f"/zones/{zone['id']}/dns_records/{rid}")
    except ApiError:
        raise ApiError("这条记录已经不存在了，请刷新")
    return j["result"]


def _cf_host(text, what):
    h = str(text or "").strip().lower().rstrip(".")
    if not h or len(h) > 253 or not CF_HOST_RE.match(h):
        raise ApiError(f"{what}格式不正确")
    return h


def _cf_fields(zone, p, typ):
    """校验并整理要写给 Cloudflare 的字段。"""
    zn = zone["name"]
    raw = str(p.get("name") or "").strip().lower().rstrip(".")
    if raw in ("", "@"):
        name = zn
    elif raw == zn or raw.endswith("." + zn):
        name = raw
    else:
        name = raw + "." + zn
    if len(name) > 253 or not CF_NAME_RE.match(name):
        raise ApiError("名称格式不正确（只支持英文、数字、-、_，泛解析写 *）")
    content = str(p.get("content") or "").strip()
    if not content:
        raise ApiError("内容不能为空")
    out = {"type": typ, "name": name}
    if typ == "A":
        try:
            content = str(ipaddress.IPv4Address(content))
        except ValueError:
            raise ApiError("A 记录的内容必须是 IPv4 地址")
    elif typ == "AAAA":
        try:
            content = str(ipaddress.IPv6Address(content))
        except ValueError:
            raise ApiError("AAAA 记录的内容必须是 IPv6 地址")
    elif typ in ("CNAME", "NS", "MX"):
        content = _cf_host(content, "目标地址")
    elif typ == "TXT":
        if len(content) > 2048:
            raise ApiError("TXT 内容太长了，最多 2048 个字符")
    out["content"] = content
    if typ == "MX":
        try:
            prio = int(p.get("priority"))
            if not 0 <= prio <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            raise ApiError("MX 记录的优先级要填 0 到 65535 的整数")
        out["priority"] = prio
    try:
        ttl = int(p.get("ttl") or 1)
    except (TypeError, ValueError):
        raise ApiError("TTL 不正确")
    if ttl != 1 and not 60 <= ttl <= 86400:
        raise ApiError("TTL 只能是「自动」或 60 到 86400 秒")
    proxied = bool(p.get("proxied")) and typ in CF_PROXIABLE
    out["proxied"] = proxied
    out["ttl"] = 1 if proxied else ttl
    comment = str(p.get("comment") or "").strip()
    if len(comment) > 100:
        raise ApiError("备注太长了，最多 100 个字")
    out["comment"] = comment
    return out


def _cf_audit(ctx, op, zone, before, after):
    try:
        row = {"t": int(time.time()), "uid": ctx["uid"], "op": op, "zone": zone["name"],
               "before": before, "after": after}
        with open(CF_AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.chmod(CF_AUDIT_FILE, 0o600)
    except Exception as e:
        print("Cloudflare 操作日志写入失败：", e)


async def _cf_records(zone):
    recs, page = [], 1
    while page <= 5:
        j = await _cf_call("GET", f"/zones/{zone['id']}/dns_records", query={"per_page": 1000, "page": page})
        recs += [_cf_view(r) for r in j.get("result") or []]
        if page >= int((j.get("result_info") or {}).get("total_pages", 1)):
            break
        page += 1
    recs.sort(key=lambda r: (r["name"], r["type"]))
    return recs


@action("cf", admin=True)
async def a_cf(ctx, p):
    if not p.get("zone"):
        zones, pending = _exp_attach(await _cf_zones(force=bool(p.get("refresh"))), force=bool(p.get("refresh")))
        return {"zones": zones, "exp_pending": pending}
    zone = await _cf_zone(p)
    return {"zone": zone, "records": await _cf_records(zone)}


@action("cf_edit", admin=True)
async def a_cf_edit(ctx, p):
    need_rate(ctx["uid"], 1)
    op = p.get("op")
    zone = await _cf_zone(p)
    base = f"/zones/{zone['id']}/dns_records"
    if op == "purge":
        return await _cf_purge(ctx, zone, p)
    if op == "add":
        typ = str(p.get("type") or "").upper()
        if typ not in CF_EDIT_TYPES:
            raise ApiError("不支持的记录类型")
        j = await _cf_call("POST", base, _cf_fields(zone, p, typ))
        rec = _cf_view(j["result"])
        _cf_audit(ctx, "add", zone, None, rec)
        return {"record": rec}
    old = await _cf_get_record(zone, p)
    before = _cf_view(old)
    if op == "edit":
        if old.get("type") not in CF_EDIT_TYPES:
            raise ApiError("这种类型的记录暂不支持在这里修改，请到 Cloudflare 后台操作")
        typ = str(p.get("type") or old["type"]).upper()
        if typ not in CF_EDIT_TYPES:
            raise ApiError("不支持的记录类型")
        j = await _cf_call("PATCH", f"{base}/{old['id']}", _cf_fields(zone, p, typ))
        rec = _cf_view(j["result"])
        _cf_audit(ctx, "edit", zone, before, rec)
        return {"record": rec}
    if op == "proxy":
        if old.get("type") not in CF_PROXIABLE:
            raise ApiError("这种类型的记录不能开启 Cloudflare 代理")
        on = bool(p.get("proxied"))
        body = {"proxied": on}
        if on:
            body["ttl"] = 1
        j = await _cf_call("PATCH", f"{base}/{old['id']}", body)
        rec = _cf_view(j["result"])
        _cf_audit(ctx, "proxy", zone, before, rec)
        return {"record": rec}
    if op == "del":
        await _cf_call("DELETE", f"{base}/{old['id']}")
        _cf_audit(ctx, "del", zone, before, None)
        return {"deleted": before}
    raise ApiError("未知操作")


# ---- 域名到期日：用公开的 RDAP 查询（不管在哪家注册都能查），查不到再用 WHOIS
# 结果缓存到 cf_expiry.json，12 小时刷新一次；查询在后台进行，不拖慢页面。
CF_EXP_FILE = _data_path("cf_expiry.json")
CF_EXP_TTL = 12 * 3600
CF_EXP_FAIL_TTL = 3600
CF_EXP_BUSY = set()
CF_EXP_TASKS = set()       # 保留后台任务的引用，避免被回收
CF_RDAP_BOOT = {"t": 0, "map": {}}
CF_RDAP_LOCK = threading.Lock()
try:
    with open(CF_EXP_FILE, "r", encoding="utf-8") as _f:
        CF_EXP = json.load(_f)
    if not isinstance(CF_EXP, dict):
        CF_EXP = {}
except Exception:
    CF_EXP = {}

_EXP_RE = re.compile(r"(?im)^\s*(?:registry expiry date|registrar registration expiration date|expiration date|"
                     r"expiry date|expiration time|expire date|expires on|expires|paid-till|renewal date)\s*:\s*(.+?)\s*$")


def _exp_parse(s):
    s = str(s or "").strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%Y.%m.%d", 10), ("%Y/%m/%d", 10), ("%d-%b-%Y", 11), ("%d.%m.%Y", 10)):
        try:
            return calendar.timegm(time.strptime(s[:n], fmt))
        except ValueError:
            continue
    return 0


def _http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"Accept": "application/rdap+json, application/json",
                                               "User-Agent": "nav-miniapp/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _rdap_base(tld):
    with CF_RDAP_LOCK:
        _rdap_boot()
    return CF_RDAP_BOOT["map"].get(tld)


def _rdap_boot():
    if time.time() - CF_RDAP_BOOT["t"] > 86400 or not CF_RDAP_BOOT["map"]:
        try:
            m = {}
            for svc in _http_json("https://data.iana.org/rdap/dns.json").get("services", []):
                for t in svc[0]:
                    if svc[1]:
                        m[t.lower()] = svc[1][0]
            CF_RDAP_BOOT.update(t=time.time(), map=m)
        except Exception as e:
            print("RDAP 列表下载失败：", e)
            CF_RDAP_BOOT["t"] = time.time() - 86400 + 600     # 10 分钟后再试


def _rdap_expiry(name):
    tld = name.rsplit(".", 1)[-1].lower()
    base = _rdap_base(tld)
    url = (base.rstrip("/") + "/domain/" + name) if base else ("https://rdap.org/domain/" + name)
    j = _http_json(url)
    for e in j.get("events") or []:
        if str(e.get("eventAction", "")).lower() in ("expiration", "registration expiration"):
            ts = _exp_parse(e.get("eventDate"))
            if ts:
                return ts
    return 0


def _whois_query(server, q):
    import socket
    with socket.create_connection((server, 43), timeout=8) as s:
        s.sendall((q + "\r\n").encode())
        buf = b""
        while len(buf) < 200000:
            chunk = s.recv(8192)
            if not chunk:
                break
            buf += chunk
    return buf.decode("utf-8", "replace")


def _whois_expiry(name):
    tld = name.rsplit(".", 1)[-1].lower()
    m = re.search(r"(?im)^\s*(?:whois|refer):\s*(\S+)", _whois_query("whois.iana.org", tld))
    if not m:
        return 0
    text = _whois_query(m.group(1), name)
    m2 = _EXP_RE.search(text)
    return _exp_parse(m2.group(1)) if m2 else 0


def _exp_lookup(name):
    err = ""
    try:
        ts = _rdap_expiry(name)
        if ts:
            return ts, ""
    except Exception as e:
        err = str(e)[:80]
    try:
        ts = _whois_expiry(name)
        if ts:
            return ts, ""
    except Exception as e:
        err = err or str(e)[:80]
    print(f"域名到期日查询失败：{name} {err}")
    return 0, "查不到到期日"


async def _exp_refresh(names):
    names = [n for n in names if n not in CF_EXP_BUSY]
    if not names:
        return
    CF_EXP_BUSY.update(names)

    async def one(n):
        try:
            ts, err = await asyncio.to_thread(_exp_lookup, n)
        except Exception as e:
            print("到期日查询出错：", n, repr(e))
            ts, err = 0, "查不到到期日"
        CF_EXP[n] = {"exp": ts, "t": int(time.time()), "err": err}

    try:
        await asyncio.gather(*(one(n) for n in names))     # 几个域名同时查
    finally:
        CF_EXP_BUSY.difference_update(names)
    if True:
        try:
            tmp = CF_EXP_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(CF_EXP, f, ensure_ascii=False, indent=1)
            os.replace(tmp, CF_EXP_FILE)
        except Exception as e:
            print("到期日缓存保存失败：", e)


def _exp_attach(zones, force=False):
    """给域名列表加上到期日；过期的缓存在后台刷新。返回是否还有没查完的。"""
    stale, now = [], time.time()
    out = []
    for z in zones:
        c = CF_EXP.get(z["name"]) or {}
        ttl = CF_EXP_TTL if c.get("exp") else CF_EXP_FAIL_TTL
        if force or not c or now - c.get("t", 0) > ttl:
            stale.append(z["name"])
        out.append(dict(z, exp=c.get("exp") or 0, exp_err=c.get("err") or ""))
    if stale:
        task = asyncio.get_running_loop().create_task(_exp_refresh(stale))
        CF_EXP_TASKS.add(task)
        task.add_done_callback(CF_EXP_TASKS.discard)
    pending = any(not (CF_EXP.get(n) or {}) for n in stale) or (force and bool(stale))
    return out, pending


# ---- 清除缓存
CF_PURGE_MAX = 30


async def _cf_purge(ctx, zone, p):
    path = f"/zones/{zone['id']}/purge_cache"
    if p.get("mode") == "all":
        await _cf_call("POST", path, {"purge_everything": True}, need=CF_NEED_PURGE)
        _cf_audit(ctx, "purge", zone, None, {"all": True})
        return {"purged": "all"}
    urls = []
    for line in re.split(r"[\s,]+", str(p.get("urls") or "")):
        line = line.strip()
        if not line:
            continue
        if not re.match(r"^https?://", line, re.I):
            line = "https://" + line
        try:
            host = (urllib.parse.urlsplit(line).hostname or "").lower()
        except ValueError:
            host = ""
        if not host or not (host == zone["name"] or host.endswith("." + zone["name"])):
            raise ApiError(f"这个网址不属于 {zone['name']}：{line[:80]}")
        if len(line) > 1000:
            raise ApiError("网址太长了")
        if line not in urls:
            urls.append(line)
    if not urls:
        raise ApiError("请填写要清除的网址，一行一个")
    if len(urls) > CF_PURGE_MAX:
        raise ApiError(f"一次最多清除 {CF_PURGE_MAX} 个网址")
    await _cf_call("POST", path, {"files": urls}, need=CF_NEED_PURGE)
    _cf_audit(ctx, "purge", zone, None, {"files": urls})
    return {"purged": len(urls)}


# ---- 换 IP：找出所有域名里指向旧 IP 的 A / AAAA 记录，确认后批量改成新 IP

def _cf_ip(text, what):
    try:
        return ipaddress.ip_address(str(text or "").strip())
    except ValueError:
        raise ApiError(f"{what}不是有效的 IP 地址")


def _cf_ip_pair(p):
    old = _cf_ip(p.get("old"), "旧 IP ")
    new = _cf_ip(p.get("new"), "新 IP ")
    if old.version != new.version:
        raise ApiError("新旧 IP 要同一种类型（都是 IPv4 或都是 IPv6）")
    if old == new:
        raise ApiError("新旧 IP 一样，不用换")
    return str(old), str(new), ("A" if old.version == 4 else "AAAA")


async def _cf_matches(old, typ, zone_id=None):
    """返回 [(zone, record_view)]：所有内容等于 old 的记录。"""
    zones = await _cf_zones()
    if zone_id:
        zones = [z for z in zones if z["id"] == zone_id]
    sem = asyncio.Semaphore(4)

    async def one(z):
        async with sem:
            return z, await _cf_records(z)

    out = []
    for z, recs in await asyncio.gather(*(one(z) for z in zones)):
        for r in recs:
            if r["type"] == typ:
                try:
                    same = str(ipaddress.ip_address(r["content"])) == old
                except ValueError:
                    same = False
                if same:
                    out.append((z, r))
    return out


@action("cf_swap", admin=True)
async def a_cf_swap(ctx, p):
    """预览：只读，不会改任何东西。"""
    old, new, typ = _cf_ip_pair(p)
    if not p.get("zone"):                  # 不再支持一次换所有域名：只能在某个域名里换
        raise ApiError("请在具体的域名里换 IP")
    zone = await _cf_zone(p)
    found = await _cf_matches(old, typ, zone["id"])
    return {"old": old, "new": new, "items": [
        {"key": z["id"] + ":" + r["id"], "zone": z["name"], "name": r["name"], "type": r["type"],
         "proxied": r["proxied"], "comment": r["comment"]} for z, r in found]}


@action("cf_swap_edit", admin=True)
async def a_cf_swap_edit(ctx, p):
    need_rate(ctx["uid"], 1)
    old, new, typ = _cf_ip_pair(p)
    keys = p.get("keys")
    if not isinstance(keys, list) or not keys:
        raise ApiError("请至少勾选一条记录")
    if len(keys) > 300:
        raise ApiError("一次最多改 300 条")
    want = {str(k) for k in keys}
    zids = {k.split(":", 1)[0] for k in want}
    if len(zids) != 1:                     # 一次只改一个域名里的记录
        raise ApiError("一次只能改一个域名里的记录")
    zone = await _cf_zone({"zone": zids.pop()})
    found = [(z, r) for z, r in await _cf_matches(old, typ, zone["id"]) if z["id"] + ":" + r["id"] in want]
    sem = asyncio.Semaphore(4)

    async def one(z, r):
        async with sem:
            try:
                j = await _cf_call("PATCH", f"/zones/{z['id']}/dns_records/{r['id']}", {"content": new})
                _cf_audit(ctx, "swap_ip", z, r, _cf_view(j["result"]))
                return {"zone": z["name"], "name": r["name"], "ok": True}
            except ApiError as e:
                return {"zone": z["name"], "name": r["name"], "ok": False, "error": str(e)}

    res = await asyncio.gather(*(one(z, r) for z, r in found))
    return {"done": [x for x in res if x["ok"]], "failed": [x for x in res if not x["ok"]],
            "missing": len(want) - len(found)}


# ---------------------------------------------------------------- Cloudflare 安全防护（攻击防护 / WAF 自定义规则 / 速率限制）
# 和解析共用同一个 Token。需要的额外权限（缺哪个，页面上对应那块会提示，其它照常用）：
#   区域 → 区域设置 → 编辑          （5 秒盾 / 安全级别 / 浏览器检查 / 验证有效期）
#   区域 → 区域 WAF → 编辑          （WAF 自定义规则 / 速率限制）
#   区域 → Bot Management → 编辑    （Bot 攻击模式）

CF_NEED_SET = "（需要给 Token 加「区域 → 区域设置 → 编辑」权限）"
CF_NEED_WAF = "（需要给 Token 加「区域 → 区域 WAF → 编辑」权限）"
CF_NEED_CFG = "（需要给 Token 加「区域 → 配置规则 → 编辑」权限）"
UAM_KEEP_DESC = "操控台放行（5 秒盾时不拦）"      # 名字沿用：老版本建的同名规则会被自动改成新写法
UAM_KEEP_IP_MAX = 150        # 放行的探针 IP 最多这么多个（Cloudflare 规则表达式有长度上限）
CF_NEED_BOT = "（需要给 Token 加「区域 → Bot Management → 编辑」权限）"
SEC_STATE_FILE = _data_path("cf_sec_state.json")      # 开 5 秒盾前的安全级别，关掉时恢复
SEC_LEVELS = ("off", "essentially_off", "low", "medium", "high", "under_attack")
SEC_TTLS = (300, 900, 1800, 2700, 3600, 7200, 10800, 14400, 28800, 57600, 86400, 604800, 2592000, 31536000)
WAF_PHASE = "http_request_firewall_custom"
RL_PHASE = "http_ratelimit"
WAF_ACTIONS = ("block", "managed_challenge", "js_challenge", "challenge", "skip")
# 各套餐的数量 / 参数限制（超出时 Cloudflare 也会拒绝，这里只是让页面提前知道）
PLAN_LIMITS = {
    "free": {"waf": 5, "rl": 1, "periods": [10], "timeouts": [10]},
    "pro": {"waf": 20, "rl": 2, "periods": [10, 60], "timeouts": [10, 60]},
    "business": {"waf": 100, "rl": 5, "periods": [10, 60, 120, 300, 600], "timeouts": [10, 60, 120, 300, 600, 3600, 86400]},
    "enterprise": {"waf": 1000, "rl": 100, "periods": [10, 60, 120, 300, 600, 3600], "timeouts": [10, 60, 120, 300, 600, 3600, 86400]},
}
# 「放行」规则跳过的东西：剩下的自定义规则 + 速率限制 + 旧版安全功能
WAF_SKIP_PARAMS = {"ruleset": "current", "phases": [RL_PHASE],
                   "products": ["bic", "securityLevel", "uaBlock", "zoneLockdown", "hot"]}
CC_RE = re.compile(r"^[A-Z]{2}$")
PATH_RE = re.compile(r"^/[^\s\"\\]*$")


def _sec_state():
    try:
        with open(SEC_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _sec_state_set(zid, level):
    st = _sec_state()
    st[zid] = level
    try:
        with open(SEC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception as e:
        print("保存安全级别失败：", e)


def _plan(zone):
    return PLAN_LIMITS.get(zone.get("plan") or "free", PLAN_LIMITS["free"])


SEC_KEYS = ("security_level", "browser_check", "challenge_ttl",
            "ssl", "always_use_https", "automatic_https_rewrites", "min_tls_version", "tls_1_3")
SSL_MODES = ("off", "flexible", "full", "strict")
TLS_MINS = ("1.0", "1.1", "1.2", "1.3")
ONOFF_KEYS = ("browser_check", "always_use_https", "automatic_https_rewrites")


async def _sec_settings(zone):
    try:
        j = await _cf_call("GET", f"/zones/{zone['id']}/settings", need=CF_NEED_SET)
        vals = {s.get("id"): s for s in j.get("result") or [] if isinstance(s, dict)}
    except CfPermError:
        # 一次读全部设置的接口会顺带检查很多别的权限；逐项读只需要「区域设置」
        rs = await asyncio.gather(*[_cf_call("GET", f"/zones/{zone['id']}/settings/{k}", need=CF_NEED_SET)
                                    for k in SEC_KEYS], return_exceptions=True)
        if isinstance(rs[0], BaseException):
            raise rs[0]
        vals = {k: (r.get("result") or {}) for k, r in zip(SEC_KEYS, rs) if isinstance(r, dict)}

    def v(k, default=None):
        return (vals.get(k) or {}).get("value", default)
    level = v("security_level", "medium")
    prev = _sec_state().get(zone["id"])
    if level != "under_attack":
        prev = level
    out = {"security_level": level, "prev_level": prev if prev in SEC_LEVELS[:5] else "medium",
           "browser_check": v("browser_check") == "on", "challenge_ttl": int(v("challenge_ttl", 1800) or 1800),
           "ssl": v("ssl"), "always_use_https": v("always_use_https") == "on",
           "automatic_https_rewrites": v("automatic_https_rewrites") == "on",
           "min_tls_version": v("min_tls_version"), "tls_1_3": v("tls_1_3") in ("on", "zrt"),
           "editable": all((vals.get(k) or {}).get("editable", True) for k in ("security_level", "browser_check"))}
    return out


BOT_KEEP = ("fight_mode", "ai_bots_protection", "enable_js")    # 免费版可改的字段，改一项时其它照原样带上


async def _bot_state(zone):
    j = await _cf_call("GET", f"/zones/{zone['id']}/bot_management", need=CF_NEED_BOT)
    return j.get("result") or {}


def _bot_view(r):
    return {"bot_fight": bool(r.get("fight_mode")),
            "ai_bots": (r.get("ai_bots_protection") or "disabled") == "block",
            "ai_supported": "ai_bots_protection" in r}


async def _ruleset(zone, phase):
    """返回 (ruleset_id, rules)；还没有建过这个阶段的规则集就是 (None, [])。"""
    try:
        j = await _cf_call("GET", f"/zones/{zone['id']}/rulesets/phases/{phase}/entrypoint", need=CF_NEED_WAF)
    except CfNotFound:
        return None, []
    r = j.get("result") or {}
    return r.get("id"), list(r.get("rules") or [])


def _q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _waf_expr(t, p):
    """把页面上的「规则类型 + 值」变成 Cloudflare 表达式，返回 (表达式, 简短说明)。"""
    def items(key, sep=r"[\s,，;；]+"):
        return [x for x in re.split(sep, str(p.get(key) or "").strip()) if x]
    if t in ("country_block", "country_allow"):
        cc = [x.upper() for x in items("countries")]
        bad = [x for x in cc if not CC_RE.match(x)]
        if not cc or bad:
            raise ApiError("国家 / 地区请填两位字母代码，例如 CN HK US" + (f"（{bad[0]} 不对）" if bad else ""))
        cc = list(dict.fromkeys(cc))[:50]
        s = "{" + " ".join(_q(c) for c in cc) + "}"
        if t == "country_block":
            return f"(ip.geoip.country in {s})", "国家 / 地区是 " + "、".join(cc) + " 时"
        return f"(not ip.geoip.country in {s})", "国家 / 地区不是 " + "、".join(cc) + " 时"
    if t == "ip":
        nets = []
        for x in items("ips"):
            try:
                nets.append(str(ipaddress.ip_network(x, strict=False)) if "/" in x else str(ipaddress.ip_address(x)))
            except ValueError:
                raise ApiError(f"「{x[:40]}」不是有效的 IP 或网段")
        if not nets:
            raise ApiError("请至少填一个 IP 或网段")
        nets = list(dict.fromkeys(nets))[:200]
        return "(ip.src in {" + " ".join(nets) + "})", "IP 属于 " + "、".join(nets[:4]) + ("…" if len(nets) > 4 else "")
    if t == "path":
        paths = items("path", r"[\s,，]+")
        bad = [x for x in paths if not PATH_RE.match(x) or len(x) > 200]
        if not paths or bad:
            raise ApiError("路径要以 / 开头，不能有空格和引号" + (f"（{bad[0][:40]}）" if bad else ""))
        paths = list(dict.fromkeys(paths))[:30]
        return ("(" + " or ".join(f'http.request.uri.path wildcard r"{x.rstrip("*")}*"' for x in paths) + ")",
                "路径以 " + "、".join(paths) + " 开头")
    if t == "ua":
        uas = [x.strip().lower() for x in re.split(r"[\n,，]+", str(p.get("ua") or "")) if x.strip()]
        if not uas:
            raise ApiError("请至少填一个 User-Agent 关键词")
        uas = list(dict.fromkeys(uas))[:30]
        return ("(" + " or ".join(f"lower(http.user_agent) contains {_q(x)}" for x in uas) + ")",
                "UA 包含 " + "、".join(uas))
    if t == "asn":
        nums = []
        for x in items("asn"):
            x = x.upper().removeprefix("AS")
            if not x.isdigit() or not 0 < int(x) < 4294967296:
                raise ApiError(f"「{x[:20]}」不是有效的 ASN 编号")
            nums.append(str(int(x)))
        if not nums:
            raise ApiError("请至少填一个 ASN 编号")
        nums = list(dict.fromkeys(nums))[:100]
        return "(ip.geoip.asnum in {" + " ".join(nums) + "})", "ASN 是 " + "、".join("AS" + n for n in nums)
    if t == "expr":
        e = str(p.get("expr") or "").strip()
        if not e:
            raise ApiError("表达式不能为空")
        if len(e) > 4000:
            raise ApiError("表达式太长了")
        return e, e
    raise ApiError("未知的规则类型")


_STR_RE = r'"((?:[^"\\]|\\.)*)"'


def _waf_parse(expr):
    """把本程序生成的表达式还原成「类型 + 值」（编辑时回填）；认不出来就当自定义表达式。"""
    e = (expr or "").strip()
    m = re.fullmatch(r"\((not )?ip\.(?:geoip|src)\.country in \{((?:\s*\"[A-Z]{2}\"\s*)+)\}\)", e)
    if m:
        cc = re.findall(r'"([A-Z]{2})"', m.group(2))
        return {"type": "country_allow" if m.group(1) else "country_block", "value": " ".join(cc)}
    m = re.fullmatch(r"\(ip\.src in \{([0-9a-fA-F:./\s]+)\}\)", e)
    if m:
        return {"type": "ip", "value": "\n".join(m.group(1).split())}
    m = re.fullmatch(r"\(ip\.(?:geoip|src)\.asnum in \{([\d\s]+)\}\)", e)
    if m:
        return {"type": "asn", "value": " ".join(m.group(1).split())}
    parts = e[1:-1].split(" or ") if e.startswith("(") and e.endswith(")") else []
    if parts and all(re.fullmatch(r'http\.request\.uri\.path wildcard r"(/[^"\s]*)\*"', x) for x in parts):
        return {"type": "path", "value": "\n".join(re.fullmatch(r'.*r"(/[^"\s]*)\*"', x).group(1) for x in parts)}
    if parts and all(re.fullmatch(r"lower\(http\.user_agent\) contains " + _STR_RE, x) for x in parts):
        vals = [re.fullmatch(r".*contains " + _STR_RE, x).group(1).replace('\\"', '"').replace("\\\\", "\\") for x in parts]
        return {"type": "ua", "value": "\n".join(vals)}
    return {"type": "expr", "value": e}


def _waf_summary(tpl):
    t, v = tpl["type"], tpl["value"]
    if t == "expr":
        return v
    try:
        key = {"country_block": "countries", "country_allow": "countries", "ip": "ips", "path": "path", "ua": "ua", "asn": "asn"}[t]
        return _waf_expr(t, {key: v})[1]
    except Exception:
        return v


def _waf_view(r):
    tpl = _waf_parse(r.get("expression"))
    return {"id": r.get("id"), "description": r.get("description") or "", "expression": r.get("expression") or "",
            "action": r.get("action"), "enabled": r.get("enabled", True) is not False,
            "tpl": tpl, "summary": _waf_summary(tpl)}


def _rl_view(r):
    rl = r.get("ratelimit") or {}
    e = r.get("expression") or ""
    m = re.fullmatch(r'\(http\.request\.uri\.path wildcard r"(/[^"\s]*)\*"\)', e)
    path = m.group(1) if m else ("" if e in ("true", "(true)") else e)
    if path == "/":
        path = ""
    return {"id": r.get("id"), "description": r.get("description") or "", "expression": e, "path": path,
            "action": r.get("action"), "enabled": r.get("enabled", True) is not False,
            "requests": rl.get("requests_per_period"), "period": rl.get("period"),
            "timeout": rl.get("mitigation_timeout", 0)}


async def _waf_block(zone):
    try:
        _rid, rules = await _ruleset(zone, WAF_PHASE)
        return {"perm": True, "limit": _plan(zone)["waf"], "rules": [_waf_view(r) for r in rules]}
    except CfPermError:
        return {"perm": False, "limit": _plan(zone)["waf"], "rules": []}


async def _rl_block(zone):
    pl = _plan(zone)
    try:
        _rid, rules = await _ruleset(zone, RL_PHASE)
        return {"perm": True, "limit": pl["rl"], "periods": pl["periods"], "timeouts": pl["timeouts"],
                "rules": [_rl_view(r) for r in rules]}
    except CfPermError:
        return {"perm": False, "limit": pl["rl"], "periods": pl["periods"], "timeouts": pl["timeouts"], "rules": []}


@action("cf_sec", admin=True)
async def a_cf_sec(ctx, p):
    zone = await _cf_zone(p)
    out = {"zone": zone, "perm": {"settings": True, "bot": True}}

    async def settings():
        try:
            return await _sec_settings(zone)
        except CfPermError:
            out["perm"]["settings"] = False
            return {"security_level": "medium", "prev_level": "medium", "browser_check": False, "challenge_ttl": 1800}

    async def bot():
        try:
            return _bot_view(await _bot_state(zone))
        except (CfPermError, CfNotFound):
            out["perm"]["bot"] = False
            return {"bot_fight": False, "ai_bots": False, "ai_supported": False}
    s, b, w, r = await asyncio.gather(settings(), bot(), _waf_block(zone), _rl_block(zone))
    s.update(b)
    out.update(settings=s, waf=w, rl=r)
    return out


def _probe_ips():
    """所有登记服务器的出口 IP（探针上报时 Cloudflare 看到的来源 IP，加上本机网卡 IPv4/IPv6、解析联动的 IP）。
    IPv6 按 /64 放行，避免服务器换了同一网段里的地址就被拦。"""
    out = set()
    for s_ in SERVERS:
        live = LIVE.get(s_["id"]) or {}
        for c in (s_.get("allow_ip"), live.get("ip"), live.get("ip4"), (live.get("m") or {}).get("ip6"), s_.get("dns_ip")):
            try:
                a = ipaddress.ip_address(str(c or "").strip())
            except ValueError:
                continue
            if not a.is_global:
                continue
            out.add(str(a) if a.version == 4 else str(ipaddress.ip_network(f"{a}/64", strict=False)))
    return sorted(out)[:UAM_KEEP_IP_MAX]


def _ip_public(v):
    """用户手填的服务器公网 IP：空 = 不填；不是公网 IP 就报错。"""
    v = str(v or "").strip()
    if not v:
        return ""
    try:
        a = ipaddress.ip_address(v)
    except ValueError:
        raise ApiError("IP 格式不正确，填服务器的公网 IPv4 或 IPv6")
    if not a.is_global:
        raise ApiError("这不是公网 IP（内网 / 本机地址不用填）")
    return str(a)


async def _uam_probe_zone():
    """探针上报网址（web_url）所在的 Cloudflare 域名，以及它现在是不是开着 5 秒盾：(zone, True/False)。
    不在当前 Token 管的域名里 / 没配 Token 时返回 (None, False)；查询出错抛 ApiError。"""
    host = (urllib.parse.urlsplit((CONF.get("web_url") or "").strip()).hostname or "").lower()
    if not host or not _cf_ready():
        return None, False
    zone = next((z for z in await _cf_zones() if host == z["name"] or host.endswith("." + z["name"])), None)
    if not zone:
        return None, False
    j = await _cf_call("GET", f"/zones/{zone['id']}/settings/security_level", need=CF_NEED_SET)
    return zone, (j.get("result") or {}).get("value") == "under_attack"


async def _uam_refresh(ctx):
    """探针网址所在的域名正开着 5 秒盾时，重新生成放行名单（新加的服务器 IP 立刻生效）。
    返回 None（不需要）/ "ok" / 出错原因。"""
    try:
        zone, on = await _uam_probe_zone()
        if not on:
            return None
        await _uam_keep(ctx, zone)
        return "ok"
    except ApiError as e:
        return str(e)


def _probe_site(zone):
    """探针上报的网址（nav_miniapp.json 的 web_url）属于这个域名时，返回 (host, 目录)；否则 None。"""
    u = urllib.parse.urlsplit((CONF.get("web_url") or "").strip())
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or not (host == zone["name"] or host.endswith("." + zone["name"])):
        return None
    path = (u.path or "/").rstrip("/") + "/"
    if not re.fullmatch(r"/[A-Za-z0-9._~\-/]{0,200}", path) or ".." in path:
        return None
    return host, path


async def _uam_keep(ctx, zone):
    """开 5 秒盾前，加一条配置规则：只对「登记过的服务器 IP」放行探针上报（probe.php）和安装脚本（probe.sh / probe.py）。
    探针用 curl 上报，过不了检查页；只按来源 IP 放行，别人刷 probe.php 照样被检查页挡住。
    操控台的 api.php / speed.php 不放行：页面过一次验证就能正常请求（被拦时前端会自动刷新过验证）。
    返回放行了多少个 IP；不需要放行（探针网址不在这个域名下 / 没有服务器）时返回 0。"""
    site = _probe_site(zone)
    ips = _probe_ips() if site else []
    phase = "http_config_settings"
    base = f"/zones/{zone['id']}/rulesets"
    try:
        j = await _cf_call("GET", f"{base}/phases/{phase}/entrypoint", need=CF_NEED_CFG)
        r = j.get("result") or {}
    except CfNotFound:
        r = None
    old = next((x for x in (r or {}).get("rules") or [] if x.get("description") == UAM_KEEP_DESC), None)
    if not ips:
        if old:        # 以前建过（老版本放行过整个接口），现在不需要了就删掉
            await _cf_call("DELETE", f"{base}/{r['id']}/rules/{old['id']}", need=CF_NEED_CFG)
            _cf_audit(ctx, "uam_keep", zone, old.get("expression"), None)
        return 0
    host, path = site
    files = " ".join(_q(path + f) for f in ("probe.php", "probe.sh", "probe.py"))     # 上报 + 安装 / 更新探针时下载的脚本
    expr = f"(http.host eq {_q(host)} and http.request.uri.path in {{{files}}} and ip.src in {{{' '.join(ips)}}})"
    rule = {"description": UAM_KEEP_DESC, "expression": expr, "action": "set_config",
            "action_parameters": {"security_level": "essentially_off"}, "enabled": True}
    if r is None:
        await _cf_call("PUT", f"{base}/phases/{phase}/entrypoint", {"rules": [rule]}, need=CF_NEED_CFG)
    elif old:
        if old.get("expression") == expr and old.get("enabled", True) and (old.get("action_parameters") or {}).get("security_level") == "essentially_off":
            return len(ips)
        await _cf_call("PATCH", f"{base}/{r['id']}/rules/{old['id']}", rule, need=CF_NEED_CFG)
    else:
        await _cf_call("POST", f"{base}/{r['id']}/rules", rule, need=CF_NEED_CFG)
    _cf_audit(ctx, "uam_keep", zone, old and old.get("expression"), expr)
    return len(ips)


async def _rules_write(ctx, zone, phase, op, rule_id=None, rule=None, position=None):
    """在某个阶段的规则集里增 / 改 / 删 / 移动一条规则；规则集不存在时自动建。"""
    rsid, rules = await _ruleset(zone, phase)
    old = next((r for r in rules if r.get("id") == rule_id), None) if rule_id else None
    if rule_id and not old:
        raise ApiError("这条规则已经不存在了，请刷新")
    base = f"/zones/{zone['id']}/rulesets"
    if op == "add":
        if rsid is None:
            await _cf_call("PUT", f"{base}/phases/{phase}/entrypoint", {"rules": [rule]}, need=CF_NEED_WAF)
        else:
            await _cf_call("POST", f"{base}/{rsid}/rules", rule, need=CF_NEED_WAF)
    elif op == "edit":
        await _cf_call("PATCH", f"{base}/{rsid}/rules/{rule_id}", rule, need=CF_NEED_WAF)
    elif op == "move":
        body = {k: old[k] for k in ("action", "expression", "description", "enabled", "action_parameters", "ratelimit", "logging") if k in old}
        body["position"] = position
        await _cf_call("PATCH", f"{base}/{rsid}/rules/{rule_id}", body, need=CF_NEED_WAF)
    elif op == "del":
        await _cf_call("DELETE", f"{base}/{rsid}/rules/{rule_id}", need=CF_NEED_WAF)
    _cf_audit(ctx, phase.replace("http_", "") + "_" + op, zone, old, rule)
    return old


@action("cf_sec_edit", admin=True)
async def a_cf_sec_edit(ctx, p):
    need_rate(ctx["uid"], 1)
    zone = await _cf_zone(p)
    op = str(p.get("op") or "")
    zid = zone["id"]

    if op == "setting":
        key, val = p.get("key"), p.get("value")
        if key == "security_level":
            if val not in SEC_LEVELS:
                raise ApiError("安全级别不正确")
            cur = await _sec_settings(zone)
            if val == "under_attack" and cur["security_level"] != "under_attack":
                # 探针用 curl 上报，过不了检查页：先给登记过的服务器 IP 开个口子，不然一开 5 秒盾所有服务器都显示离线
                try:
                    await _uam_keep(ctx, zone)
                except ApiError as e:
                    if not p.get("force"):
                        raise ApiError("UAM_KEEP_FAIL:" + str(e), 409)
                _sec_state_set(zid, cur["security_level"])
            await _cf_call("PATCH", f"/zones/{zid}/settings/security_level", {"value": val}, need=CF_NEED_SET)
            _cf_audit(ctx, "security_level", zone, cur["security_level"], val)
        elif key == "browser_check":
            await _cf_call("PATCH", f"/zones/{zid}/settings/browser_check", {"value": "on" if val else "off"}, need=CF_NEED_SET)
            _cf_audit(ctx, "browser_check", zone, None, bool(val))
        elif key == "challenge_ttl":
            try:
                val = int(val)
            except (TypeError, ValueError):
                val = 0
            if val not in SEC_TTLS:
                raise ApiError("验证有效期不正确")
            await _cf_call("PATCH", f"/zones/{zid}/settings/challenge_ttl", {"value": val}, need=CF_NEED_SET)
            _cf_audit(ctx, "challenge_ttl", zone, None, val)
        elif key in ("bot_fight", "ai_bots"):
            cur = await _bot_state(zone)
            body = {k: cur[k] for k in BOT_KEEP if k in cur}
            if key == "bot_fight":
                body["fight_mode"] = bool(val)
            else:
                body["ai_bots_protection"] = "block" if val else "disabled"
            await _cf_call("PUT", f"/zones/{zid}/bot_management", body, need=CF_NEED_BOT)
            _cf_audit(ctx, key, zone, None, bool(val))
        elif key in ONOFF_KEYS[1:]:
            await _cf_call("PATCH", f"/zones/{zid}/settings/{key}", {"value": "on" if val else "off"}, need=CF_NEED_SET)
            _cf_audit(ctx, key, zone, None, bool(val))
        elif key == "ssl":
            if val not in SSL_MODES:
                raise ApiError("SSL 模式不正确")
            await _cf_call("PATCH", f"/zones/{zid}/settings/ssl", {"value": val}, need=CF_NEED_SET)
            _cf_audit(ctx, "ssl", zone, None, val)
        elif key == "min_tls_version":
            if val not in TLS_MINS:
                raise ApiError("TLS 版本不正确")
            await _cf_call("PATCH", f"/zones/{zid}/settings/min_tls_version", {"value": val}, need=CF_NEED_SET)
            _cf_audit(ctx, "min_tls_version", zone, None, val)
        elif key == "tls_1_3":
            await _cf_call("PATCH", f"/zones/{zid}/settings/tls_1_3", {"value": "on" if val else "off"}, need=CF_NEED_SET)
            _cf_audit(ctx, "tls_1_3", zone, None, bool(val))
        else:
            raise ApiError("未知的设置项")
        s = await _sec_settings(zone)
        try:
            s.update(_bot_view(await _bot_state(zone)))
        except ApiError:
            s.update(bot_fight=False, ai_bots=False, ai_supported=False)
        return {"settings": s}

    if op.startswith("waf_"):
        sub = op[4:]
        rid = str(p.get("id") or "") or None
        if sub in ("add", "edit"):
            action_ = p.get("action")
            if action_ not in WAF_ACTIONS:
                raise ApiError("动作不正确")
            t = str(p.get("type") or "")
            expr, _summary = _waf_expr(t, p)
            name = str(p.get("name") or "").strip()[:200] or _summary[:60]
            rule = {"action": action_, "expression": expr, "description": name, "enabled": True}
            if sub == "add":
                _rid, rules = await _ruleset(zone, WAF_PHASE)
                if len(rules) >= _plan(zone)["waf"]:
                    raise ApiError(f"当前套餐最多 {_plan(zone)['waf']} 条自定义规则，先删掉一条再加")
            else:
                _rid, rules = await _ruleset(zone, WAF_PHASE)
                old = next((r for r in rules if r.get("id") == rid), None)
                if old is None:
                    raise ApiError("这条规则已经不存在了，请刷新")
                rule["enabled"] = old.get("enabled", True) is not False
                if action_ == "skip" and old.get("action") == "skip" and old.get("action_parameters"):
                    rule["action_parameters"] = old["action_parameters"]   # 保留原来跳过的范围
            if action_ == "skip" and "action_parameters" not in rule:
                rule["action_parameters"] = WAF_SKIP_PARAMS
            await _rules_write(ctx, zone, WAF_PHASE, sub, rid, rule)
        elif sub == "toggle":
            _rid, rules = await _ruleset(zone, WAF_PHASE)
            old = next((r for r in rules if r.get("id") == rid), None)
            if old is None:
                raise ApiError("这条规则已经不存在了，请刷新")
            rule = {k: old[k] for k in ("action", "expression", "description", "action_parameters", "logging") if k in old}
            rule["enabled"] = bool(p.get("enabled"))
            await _rules_write(ctx, zone, WAF_PHASE, "edit", rid, rule)
        elif sub == "move":
            _rid, rules = await _ruleset(zone, WAF_PHASE)
            ids = [r.get("id") for r in rules]
            if rid not in ids:
                raise ApiError("这条规则已经不存在了，请刷新")
            i = ids.index(rid)
            if p.get("dir") == -1 and i > 0:
                await _rules_write(ctx, zone, WAF_PHASE, "move", rid, position={"before": ids[i - 1]})
            elif p.get("dir") == 1 and i < len(ids) - 1:
                await _rules_write(ctx, zone, WAF_PHASE, "move", rid, position={"after": ids[i + 1]})
        elif sub == "del":
            await _rules_write(ctx, zone, WAF_PHASE, "del", rid)
        elif sub == "ban_ip":
            ip = str(p.get("ip") or "").strip()
            try:
                ip = str(ipaddress.ip_address(ip))
            except ValueError:
                raise ApiError("IP 不正确")
            _rid, rules = await _ruleset(zone, WAF_PHASE)
            old = next((r for r in rules if r.get("description") == BAN_RULE_NAME and r.get("action") == "block"
                        and _waf_parse(r.get("expression"))["type"] == "ip"), None)
            if old:
                ips = _waf_parse(old["expression"])["value"].split("\n")
                if ip in ips:
                    raise ApiError(f"{ip} 已经在「{BAN_RULE_NAME}」里了")
                expr, _sm = _waf_expr("ip", {"ips": "\n".join(ips + [ip])})
                rule = {"action": "block", "expression": expr, "description": BAN_RULE_NAME, "enabled": True}
                await _rules_write(ctx, zone, WAF_PHASE, "edit", old["id"], rule)
            else:
                if len(rules) >= _plan(zone)["waf"]:
                    raise ApiError(f"WAF 规则已满（{_plan(zone)['waf']} 条），先删掉一条再封")
                expr, _sm = _waf_expr("ip", {"ips": ip})
                rule = {"action": "block", "expression": expr, "description": BAN_RULE_NAME, "enabled": True}
                await _rules_write(ctx, zone, WAF_PHASE, "add", None, rule)
        else:
            raise ApiError("未知操作")
        return {"waf": await _waf_block(zone)}

    if op.startswith("rl_"):
        sub = op[3:]
        rid = str(p.get("id") or "") or None
        pl = _plan(zone)
        if sub in ("add", "edit"):
            path = str(p.get("path") or "").strip()
            if path and (not PATH_RE.match(path) or len(path) > 200):
                raise ApiError("路径要以 / 开头，不能有空格和引号")
            try:
                n = int(p.get("requests"))
                if not 1 <= n <= 1000000:
                    raise ValueError
            except (TypeError, ValueError):
                raise ApiError("请求次数请填 1 ~ 1000000 的整数")
            period = int(p.get("period") or pl["periods"][0])
            if period not in pl["periods"]:
                raise ApiError("当前套餐的计数周期只能是 " + " / ".join(f"{x} 秒" for x in pl["periods"]))
            action_ = p.get("action")
            if action_ not in ("block", "managed_challenge"):
                raise ApiError("动作不正确")
            timeout = int(p.get("timeout") or pl["timeouts"][0])
            if action_ != "block":
                timeout = 0                      # 质询类动作 Cloudflare 要求封禁时长为 0（每次请求都质询）
            elif timeout not in pl["timeouts"]:
                raise ApiError("当前套餐的封禁时长只能是 " + " / ".join(f"{x} 秒" for x in pl["timeouts"]))
            rule = {"action": action_, "description": str(p.get("name") or "").strip()[:200] or "速率限制",
                    "expression": f'(http.request.uri.path wildcard r"{(path or "/").rstrip("*")}*")',
                    "enabled": True,
                    "ratelimit": {"characteristics": ["ip.src", "cf.colo.id"], "period": period,
                                  "requests_per_period": n, "mitigation_timeout": timeout}}
            _rid, rules = await _ruleset(zone, RL_PHASE)
            if sub == "add" and len(rules) >= pl["rl"]:
                raise ApiError(f"当前套餐最多 {pl['rl']} 条速率限制")
            if sub == "edit":
                old = next((r for r in rules if r.get("id") == rid), None)
                if old is None:
                    raise ApiError("这条规则已经不存在了，请刷新")
                rule["enabled"] = old.get("enabled", True) is not False
            await _rules_write(ctx, zone, RL_PHASE, sub, rid, rule)
        elif sub == "toggle":
            _rid, rules = await _ruleset(zone, RL_PHASE)
            old = next((r for r in rules if r.get("id") == rid), None)
            if old is None:
                raise ApiError("这条规则已经不存在了，请刷新")
            rule = {k: old[k] for k in ("action", "expression", "description", "ratelimit", "action_parameters", "logging") if k in old}
            rule["enabled"] = bool(p.get("enabled"))
            await _rules_write(ctx, zone, RL_PHASE, "edit", rid, rule)
        elif sub == "del":
            await _rules_write(ctx, zone, RL_PHASE, "del", rid)
        else:
            raise ApiError("未知操作")
        return {"rl": await _rl_block(zone)}
    raise ApiError("未知操作")


# ---- 安全事件（最近被拦截 / 质询的请求）：GraphQL 分析接口，需要「区域 → 分析 → 读取」
CF_NEED_LOG = "（需要给 Token 加「区域 → 分析 → 读取」权限）"
EV_FIELDS = ("action clientIP clientCountryName clientAsn clientASNDescription clientRequestHTTPHost "
             "clientRequestHTTPMethodName clientRequestPath clientRequestQuery datetime rayName ruleId source userAgent")
EV_FIELDS_MIN = "action clientIP clientCountryName clientRequestPath datetime ruleId source"
EV_WINDOWS = (86400, 21600, 3600)          # 免费版能查的时间范围较短：查不了 24 小时就缩短
BAN_RULE_NAME = "手动封禁 IP"


class _GqlFieldError(Exception):
    pass


def _cf_graphql_sync(query, variables, need=CF_NEED_LOG):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(CF_API + "/graphql", data=body, method="POST", headers={
        "Authorization": "Bearer " + _cf_token(), "Content-Type": "application/json", "User-Agent": "nav-miniapp/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise CfPermError("Cloudflare Token 无效或权限不够" + need, 400)
        raise ApiError(f"Cloudflare 分析接口出错 ({e.code})", 502)
    except Exception as e:
        raise ApiError("连接不上 Cloudflare：" + str(getattr(e, "reason", e))[:80], 502)
    errs = [str(x.get("message") or "") for x in (j.get("errors") or []) if isinstance(x, dict)]
    if errs:
        text = "；".join(errs)[:200]
        low = text.lower()
        if "authz" in low or "not authorized" in low or "unauthorized" in low or "permission" in low:
            raise CfPermError("Cloudflare Token 权限不够" + need, 400)
        if "unknown field" in low or "cannot query field" in low:
            raise _GqlFieldError(text)
        raise ApiError("Cloudflare：" + text)
    return j.get("data") or {}


def _events_sync(zid, fields, window, limit):
    end = time.time()
    v = {"z": zid, "s": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end - window)),
         "e": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end))}
    q = ("query($z:String!,$s:Time!,$e:Time!){viewer{zones(filter:{zoneTag:$z}){"
         f"firewallEventsAdaptive(limit:{limit},filter:{{datetime_geq:$s,datetime_leq:$e}},orderBy:[datetime_DESC]){{{fields}}}"
         "}}}")
    zones = (_cf_graphql_sync(q, v).get("viewer") or {}).get("zones") or [{}]
    events = zones[0].get("firewallEventsAdaptive") or []
    counts = None
    try:
        qg = ("query($z:String!,$s:Time!,$e:Time!){viewer{zones(filter:{zoneTag:$z}){"
              "firewallEventsAdaptiveGroups(limit:50,filter:{datetime_geq:$s,datetime_leq:$e}){count dimensions{action}}}}}")
        zg = (_cf_graphql_sync(qg, v).get("viewer") or {}).get("zones") or [{}]
        counts = {}
        for g in zg[0].get("firewallEventsAdaptiveGroups") or []:
            a = (g.get("dimensions") or {}).get("action") or "?"
            counts[a] = counts.get(a, 0) + int(g.get("count") or 0)
    except ApiError:
        pass
    return events, counts


async def _rule_names(zone):
    names = {}
    for ph in (WAF_PHASE, RL_PHASE):
        try:
            _rid, rules = await _ruleset(zone, ph)
            names.update({r.get("id"): r.get("description") or "" for r in rules})
        except ApiError:
            pass
    return names


@action("cf_events", admin=True)
async def a_cf_events(ctx, p):
    zone = await _cf_zone(p)
    try:
        limit = max(10, min(100, int(p.get("limit") or 50)))
    except (TypeError, ValueError):
        limit = 50
    events = counts = None
    window = EV_WINDOWS[0]
    fields = EV_FIELDS
    last = None
    for window in EV_WINDOWS:
        try:
            try:
                events, counts = await asyncio.to_thread(_events_sync, zone["id"], fields, window, limit)
            except _GqlFieldError:
                fields = EV_FIELDS_MIN
                events, counts = await asyncio.to_thread(_events_sync, zone["id"], fields, window, limit)
            break
        except CfPermError:
            return {"perm": False, "events": [], "counts": {}, "window": window}
        except ApiError as e:
            last = e
            continue
    if events is None:
        raise last or ApiError("读取安全事件失败")
    names = await _rule_names(zone)
    out = []
    for e in events:
        path = (e.get("clientRequestPath") or "") + (e.get("clientRequestQuery") or "")
        out.append({"t": _ts_iso(e.get("datetime")), "action": e.get("action") or "", "ip": e.get("clientIP") or "",
                    "country": e.get("clientCountryName") or "", "asn": e.get("clientAsn") or "",
                    "asn_name": e.get("clientASNDescription") or "", "host": e.get("clientRequestHTTPHost") or "",
                    "method": e.get("clientRequestHTTPMethodName") or "", "path": path[:300],
                    "source": e.get("source") or "", "rule": names.get(e.get("ruleId"), ""),
                    "ray": e.get("rayName") or "", "ua": (e.get("userAgent") or "")[:300]})
    if counts is None:
        counts = {}
        for e in out:
            counts[e["action"]] = counts.get(e["action"], 0) + 1
    return {"perm": True, "events": out, "counts": counts, "window": window}


# ---------------------------------------------------------------- Cloudflare R2 存储
# 存储桶管理走 Cloudflare API（同一个 Token，需要加「账户 → R2 存储 → 编辑」权限；
# 用量统计另需「账户 → 账户分析 → 读取」，没有也能用，只是不显示文件数和大小）。
# 浏览 / 上传 / 下载 / 删除文件走 R2 的 S3 接口，需要另外一对 Access Key：
#   Cloudflare → R2 → 管理 API 令牌 → 创建（对象读和写），把两个值写进机器人目录的 .env：
#     R2_ACCESS_KEY_ID=...
#     R2_SECRET_ACCESS_KEY=...
#   （或写在 nav_miniapp.json 的 "r2_key_id" / "r2_secret"），然后重启机器人。
# 有多个 Cloudflare 账户时可以用 R2_ACCOUNT_ID（或 "r2_account"）指定用哪个。
# 上传：后端签一个 1 小时有效的上传地址，手机直接 PUT 到 R2，不经过本机。

CF_NEED_R2 = "（需要给 Token 加「账户 → R2 存储 → 编辑」权限）"
R2_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
R2_LOCS = ("", "apac", "wnam", "enam", "weur", "eeur", "oc")
R2_ACCT = {"ts": 0, "item": None}
R2_USAGE = {"ts": 0, "acct": None, "items": {}}
R2_SIGN_TTL = 3600


def _r2_keys():
    kid = (os.environ.get("R2_ACCESS_KEY_ID") or CONF.get("r2_key_id") or "").strip()
    sec = (os.environ.get("R2_SECRET_ACCESS_KEY") or CONF.get("r2_secret") or "").strip()
    return (kid, sec) if kid and sec else None


async def _r2_account():
    if R2_ACCT["item"] and time.time() - R2_ACCT["ts"] < 600:
        return R2_ACCT["item"]
    want = (os.environ.get("R2_ACCOUNT_ID") or CONF.get("r2_account") or "").strip()
    accts = []
    try:
        j = await _cf_call("GET", "/accounts", query={"per_page": 50}, need=CF_NEED_R2)
        accts = [{"id": a["id"], "name": a.get("name") or a["id"]} for a in j.get("result") or [] if a.get("id")]
    except ApiError:
        pass
    if not accts:                     # Token 只有区域权限时 /accounts 可能是空的：从域名里找账户
        for z in await _cf_zones():
            a = z.get("account") or {}
            if a.get("id") and all(x["id"] != a["id"] for x in accts):
                accts.append({"id": a["id"], "name": a.get("name") or a["id"]})
    if want:
        acct = next((a for a in accts if a["id"] == want), {"id": want, "name": want})
    elif accts:
        acct = accts[0]
    else:
        raise ApiError("找不到 Cloudflare 账户" + CF_NEED_R2)
    if not CF_HEX32.match(acct["id"]):
        raise ApiError("R2 账户 ID 格式不正确")
    R2_ACCT.update(ts=time.time(), item=acct)
    return acct


def _r2_usage_sync(acct_id):
    """用 GraphQL 分析接口取每个桶的文件数和大小（取最近一次统计）。拿不到就返回 {}。"""
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 2 * 86400))
    q = ("query($a:String!,$s:Time!,$e:Time!){viewer{accounts(filter:{accountTag:$a}){"
         "r2StorageAdaptiveGroups(limit:10000,filter:{datetime_geq:$s,datetime_leq:$e},orderBy:[datetime_DESC]){"
         "max{objectCount payloadSize metadataSize} dimensions{bucketName datetime}}}}}")
    body = json.dumps({"query": q, "variables": {"a": acct_id, "s": start, "e": end}}).encode()
    req = urllib.request.Request(CF_API + "/graphql", data=body, method="POST", headers={
        "Authorization": "Bearer " + _cf_token(), "Content-Type": "application/json", "User-Agent": "nav-miniapp/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode() or "{}")
        groups = j["data"]["viewer"]["accounts"][0]["r2StorageAdaptiveGroups"]
    except Exception:
        return {}
    out = {}
    for g in groups:                       # 已按时间倒序：每个桶只取最新的一条
        name = (g.get("dimensions") or {}).get("bucketName")
        if name and name not in out:
            m = g.get("max") or {}
            out[name] = {"objects": int(m.get("objectCount") or 0),
                         "size": int(m.get("payloadSize") or 0) + int(m.get("metadataSize") or 0)}
    return out


async def _r2_usage(acct_id, force=False):
    if not force and R2_USAGE["acct"] == acct_id and time.time() - R2_USAGE["ts"] < 300:
        return R2_USAGE["items"]
    items = await asyncio.to_thread(_r2_usage_sync, acct_id)
    R2_USAGE.update(ts=time.time(), acct=acct_id, items=items)
    return items


R2_LIVE = {}          # 桶名 -> (时间, 用量)：遍历桶得到的实时用量，缓存 60 秒


def _r2_live_sync(acct_id, bucket):
    """直接遍历桶累加文件数和大小（实时，Cloudflare 的统计接口会晚几分钟到几小时）。最多翻 50 页，超过标注 partial。"""
    import xml.etree.ElementTree as ET
    objs = size = 0
    cursor = None
    for _ in range(50):
        q = {"list-type": "2", "max-keys": "1000"}
        if cursor:
            q["continuation-token"] = cursor
        _st, raw = _s3_call_sync(acct_id, "GET", bucket, query=q)
        root = ET.fromstring(raw)
        ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
        for x in root.findall(ns + "Contents"):
            k = x.find(ns + "Key")
            if k is not None and (k.text or "").endswith("/"):
                continue
            objs += 1
            sz = x.find(ns + "Size")
            size += int(sz.text or 0) if sz is not None else 0
        t = root.find(ns + "NextContinuationToken")
        trunc = root.find(ns + "IsTruncated")
        if trunc is None or trunc.text != "true" or t is None or not t.text:
            return {"objects": objs, "size": size, "partial": False}
        cursor = t.text
    return {"objects": objs, "size": size, "partial": True}


async def _r2_live(acct_id, names, force=False):
    """有 S3 密钥时取实时用量；失败就返回空，调用方退回 Cloudflare 的统计数字。"""
    if not _r2_keys():
        return {}

    async def one(n):
        c = R2_LIVE.get(n)
        if c and not force and time.time() - c[0] < 60:
            return n, c[1]
        try:
            d = await asyncio.to_thread(_r2_live_sync, acct_id, n)
        except Exception:
            return n, None
        R2_LIVE[n] = (time.time(), d)
        return n, d
    return {n: d for n, d in await asyncio.gather(*[one(n) for n in names]) if d}


def _r2_name(p, key="bucket"):
    n = str(p.get(key) or "").strip()
    if not R2_BUCKET_RE.match(n):
        raise ApiError("存储桶名称不正确")
    return n


async def _r2_buckets(acct):
    j = await _cf_call("GET", f"/accounts/{acct['id']}/r2/buckets", query={"per_page": 1000}, need=CF_NEED_R2)
    res = j.get("result") or {}
    return list(res.get("buckets") if isinstance(res, dict) else res or [])


async def _r2_dev(acct, name):
    try:
        j = await _cf_call("GET", f"/accounts/{acct['id']}/r2/buckets/{name}/domains/managed", need=CF_NEED_R2)
        r = j.get("result") or {}
        return ("https://" + r["domain"]) if r.get("enabled") and r.get("domain") else None
    except ApiError:
        return None


async def _r2_domains(acct, name):
    try:
        j = await _cf_call("GET", f"/accounts/{acct['id']}/r2/buckets/{name}/domains/custom", need=CF_NEED_R2)
    except ApiError:
        return []
    out = []
    for d in (j.get("result") or {}).get("domains") or []:
        st = d.get("status") or {}
        ok = d.get("enabled") and st.get("ownership") == "active" and st.get("ssl") == "active"
        out.append({"domain": d.get("domain"), "status": "active" if ok else "pending", "zone": d.get("zoneId")})
    return out


async def _r2_cors(acct, name):
    try:
        j = await _cf_call("GET", f"/accounts/{acct['id']}/r2/buckets/{name}/cors", need=CF_NEED_R2)
    except ApiError:                              # 没设置过 CORS 时 Cloudflare 返回错误
        return []
    return list((j.get("result") or {}).get("rules") or [])


def _ts_iso(s):
    try:
        return calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0


async def _r2_overview(force=False):
    acct = await _r2_account()
    buckets = await _r2_buckets(acct)
    names = [b.get("name") for b in buckets if b.get("name")]
    k = len(names)
    # 公开网址 / 自定义域名 / 实时用量 一起发出去，不再一轮等一轮（每轮都要跨网络问一次 Cloudflare）
    res = await asyncio.gather(_r2_live(acct["id"], names, force),
                               *[_r2_dev(acct, n) for n in names], *[_r2_domains(acct, n) for n in names])
    live, devs, doms = res[0], res[1:1 + k], res[1 + k:]
    # 统计接口（GraphQL）很慢，只在实时统计拿不到时才去查
    usage = await _r2_usage(acct["id"], force) if any(n not in live for n in names) else {}
    out = []
    for b, dev, dm in zip([b for b in buckets if b.get("name")], devs, doms):
        u = live.get(b["name"]) or usage.get(b["name"]) or {}
        out.append({"name": b["name"], "location": (b.get("location") or "").lower(), "created": _ts_iso(b.get("creation_date")),
                    "objects": u.get("objects"), "size": u.get("size"), "partial": bool(u.get("partial")),
                    "public": bool(dev), "domains": len(dm)})
    out.sort(key=lambda x: x["name"])
    return {"account": acct, "s3": bool(_r2_keys()), "usage": bool(usage or live) or not out, "live": bool(live), "buckets": out}


async def _r2_bucket_full(name):
    acct = await _r2_account()
    try:
        j = await _cf_call("GET", f"/accounts/{acct['id']}/r2/buckets/{name}", need=CF_NEED_R2)
    except CfNotFound:
        raise ApiError("这个存储桶不存在了", 404)
    b = j.get("result") or {}
    dev, dm, cors, live = await asyncio.gather(_r2_dev(acct, name), _r2_domains(acct, name), _r2_cors(acct, name), _r2_live(acct["id"], [name]))
    usage = {} if name in live else await _r2_usage(acct["id"])
    u = live.get(name) or usage.get(name) or {}
    origins = []
    for r in cors:
        for o in (r.get("allowed") or {}).get("origins") or []:
            if o not in origins:
                origins.append(o)
    return {"name": name, "location": (b.get("location") or "").lower(), "created": _ts_iso(b.get("creation_date")),
            "objects": u.get("objects"), "size": u.get("size"), "dev_url": dev, "domains": dm, "cors": origins,
            "s3_endpoint": f"https://{acct['id']}.r2.cloudflarestorage.com/{name}", "s3": bool(_r2_keys())}


def _r2_audit(ctx, op, bucket, detail):
    _cf_audit(ctx, "r2_" + op, {"name": "r2:" + bucket}, None, detail)


# ---- S3 签名（AWS Signature V4，只用标准库）

def _s3_quote(s, safe="-_.~"):
    return urllib.parse.quote(s, safe=safe)


def _s3_sign_key(secret, date, region="auto", service="s3"):
    k = hmac.new(("AWS4" + secret).encode(), date.encode(), hashlib.sha256).digest()
    for part in (region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    return k


def _s3_canon_query(q):
    return "&".join(f"{_s3_quote(k)}={_s3_quote(str(v))}" for k, v in sorted(q.items()))


def _s3_request(method, host, path, query, keys, headers=None, payload=b"", now=None, region="auto"):
    """返回带 Authorization 头的 headers（payload 已签）。path 要求已经 URL 编码。"""
    t = now or time.gmtime()
    amz = time.strftime("%Y%m%dT%H%M%SZ", t)
    date = amz[:8]
    ph = hashlib.sha256(payload).hexdigest()
    h = {"host": host, "x-amz-content-sha256": ph, "x-amz-date": amz}
    for k, v in (headers or {}).items():
        h[k.lower()] = v
    names = sorted(h)
    canon = "\n".join([method, path, _s3_canon_query(query),
                       "".join(f"{k}:{str(h[k]).strip()}\n" for k in names), ";".join(names), ph])
    scope = f"{date}/{region}/s3/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canon.encode()).hexdigest()])
    sig = hmac.new(_s3_sign_key(keys[1], date, region), sts.encode(), hashlib.sha256).hexdigest()
    h["authorization"] = f"AWS4-HMAC-SHA256 Credential={keys[0]}/{scope}, SignedHeaders={';'.join(names)}, Signature={sig}"
    return h


def _s3_presign(method, host, path, keys, expires=R2_SIGN_TTL, extra=None, now=None, region="auto"):
    t = now or time.gmtime()
    amz = time.strftime("%Y%m%dT%H%M%SZ", t)
    date = amz[:8]
    scope = f"{date}/{region}/s3/aws4_request"
    q = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{keys[0]}/{scope}", "X-Amz-Date": amz,
         "X-Amz-Expires": str(expires), "X-Amz-SignedHeaders": "host"}
    q.update(extra or {})
    canon = "\n".join([method, path, _s3_canon_query(q), f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canon.encode()).hexdigest()])
    sig = hmac.new(_s3_sign_key(keys[1], date, region), sts.encode(), hashlib.sha256).hexdigest()
    return f"https://{host}{path}?{_s3_canon_query(q)}&X-Amz-Signature={sig}"


def _r2_need_keys():
    keys = _r2_keys()
    if not keys:
        raise ApiError("还没配置 R2 的 Access Key：在机器人目录的 .env 里加 R2_ACCESS_KEY_ID 和 R2_SECRET_ACCESS_KEY，重启机器人")
    return keys


def _r2_key(p, allow_dir=False):
    k = str(p.get("key") or "")
    if not k or len(k.encode()) > 1024 or k.startswith("/") or "\x00" in k or (k.endswith("/") and not allow_dir):
        raise ApiError("文件路径不正确")
    if any(x in (".", "..") for x in k.rstrip("/").split("/")):
        raise ApiError("文件路径不能包含 . 或 ..")
    return k


def _s3_call_sync(acct_id, method, bucket, key="", query=None, payload=b"", headers=None):
    keys = _r2_need_keys()
    host = f"{acct_id}.r2.cloudflarestorage.com"
    path = "/" + bucket + ("/" + _s3_quote(key, safe="-_.~/") if key else "")
    query = query or {}
    h = _s3_request(method, host, path, query, keys, headers, payload)
    url = f"https://{host}{path}" + ("?" + _s3_canon_query(query) if query else "")
    req = urllib.request.Request(url, data=payload if method in ("PUT", "POST") else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        m = re.search(rb"<Message>(.*?)</Message>", raw or b"")
        msg = m.group(1).decode(errors="replace") if m else f"HTTP {e.code}"
        if e.code in (401, 403):
            raise ApiError("R2 Access Key 不正确或没有这个桶的权限：" + msg[:120])
        if e.code == 404:
            raise ApiError("存储桶或文件不存在", 404)
        raise ApiError("R2：" + msg[:160], 502)
    except Exception as e:
        raise ApiError("连接不上 R2：" + str(getattr(e, "reason", e))[:80], 502)


def _s3_list_sync(acct_id, bucket, prefix, cursor):
    import xml.etree.ElementTree as ET
    q = {"list-type": "2", "delimiter": "/", "max-keys": "200", "prefix": prefix}
    if cursor:
        q["continuation-token"] = cursor
    _st, raw = _s3_call_sync(acct_id, "GET", bucket, query=q)
    root = ET.fromstring(raw)
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""

    def tx(node, name):
        x = node.find(ns + name)
        return x.text if x is not None and x.text is not None else ""
    folders = [tx(c, "Prefix") for c in root.findall(ns + "CommonPrefixes")]
    files = []
    for c in root.findall(ns + "Contents"):
        k = tx(c, "Key")
        if k == prefix:           # 文件夹自己的占位对象不显示
            continue
        files.append({"key": k, "size": int(tx(c, "Size") or 0), "modified": _ts_iso(tx(c, "LastModified"))})
    nxt = tx(root, "NextContinuationToken") if tx(root, "IsTruncated") == "true" else None
    return {"folders": folders, "files": files, "next": nxt or None}


async def _r2_public_base(acct, name):
    dm = [d for d in await _r2_domains(acct, name) if d["status"] == "active"]
    if dm:
        return "https://" + dm[0]["domain"]
    return await _r2_dev(acct, name)


async def _r2_ensure_cors(acct, name, origin, need_etag=False):
    """上传前确保这个桶允许 Mini App 的网址直接 PUT（只追加，不动已有规则）。分片上传还要求把 ETag 响应头暴露给页面。"""
    rules = await _r2_cors(acct, name)
    for r in rules:
        al = r.get("allowed") or {}
        if (origin in (al.get("origins") or []) or "*" in (al.get("origins") or [])) and "PUT" in (al.get("methods") or []):
            if not need_etag or "etag" in [str(x).lower() for x in (r.get("exposeHeaders") or [])] or "*" in (r.get("exposeHeaders") or []):
                return
    rules.append({"allowed": {"origins": [origin], "methods": ["GET", "HEAD", "PUT"], "headers": ["*"]},
                  "exposeHeaders": ["ETag"], "maxAgeSeconds": 3600})
    await _cf_call("PUT", f"/accounts/{acct['id']}/r2/buckets/{name}/cors", {"rules": rules}, need=CF_NEED_R2)


@action("r2", admin=True)
async def a_r2(ctx, p):
    return await _r2_overview(force=bool(p.get("refresh")))


@action("r2_bucket", admin=True)
async def a_r2_bucket(ctx, p):
    return await _r2_bucket_full(_r2_name(p))


@action("r2_list", admin=True)
async def a_r2_list(ctx, p):
    name = _r2_name(p)
    prefix = str(p.get("prefix") or "")
    if prefix and (not prefix.endswith("/") or prefix.startswith("/") or len(prefix.encode()) > 1024):
        raise ApiError("目录不正确")
    cursor = str(p.get("cursor") or "")[:2048] or None
    if not cursor:
        R2_LIVE.pop(name, None)          # 看文件列表（上传 / 删除后会自动刷新）就让用量重新统计
    acct = await _r2_account()
    listing, base = await asyncio.gather(asyncio.to_thread(_s3_list_sync, acct["id"], name, prefix, cursor), _r2_public_base(acct, name))
    listing["public_url"] = base
    return listing


@action("r2_edit", admin=True)
async def a_r2_edit(ctx, p):
    op = str(p.get("op") or "")
    acct = await _r2_account()
    base = f"/accounts/{acct['id']}/r2/buckets"

    if op == "bucket_add":
        need_rate(ctx["uid"], 1)
        name = _r2_name(p, "name")
        loc = str(p.get("location") or "").lower()
        if loc not in R2_LOCS:
            raise ApiError("位置不正确")
        body = {"name": name}
        if loc:
            body["locationHint"] = loc
        await _cf_call("POST", base, body, need=CF_NEED_R2)
        _r2_audit(ctx, "bucket_add", name, body)
        return await _r2_overview(force=True)

    name = _r2_name(p)
    if op == "bucket_del":
        need_rate(ctx["uid"], 1)
        await _cf_call("DELETE", f"{base}/{name}", need=CF_NEED_R2)
        _r2_audit(ctx, "bucket_del", name, None)
        return await _r2_overview(force=True)
    if op == "dev_url":
        need_rate(ctx["uid"], 1)
        on = bool(p.get("enabled"))
        await _cf_call("PUT", f"{base}/{name}/domains/managed", {"enabled": on}, need=CF_NEED_R2)
        _r2_audit(ctx, "dev_url", name, on)
        return await _r2_bucket_full(name)
    if op == "domain_add":
        need_rate(ctx["uid"], 1)
        zone = await _cf_zone(p)
        dom = _cf_host(p.get("domain"), "域名")
        if dom != zone["name"] and not dom.endswith("." + zone["name"]):
            raise ApiError("域名不属于所选的主域名")
        await _cf_call("POST", f"{base}/{name}/domains/custom", {"domain": dom, "zoneId": zone["id"], "enabled": True}, need=CF_NEED_R2)
        _r2_audit(ctx, "domain_add", name, dom)
        return await _r2_bucket_full(name)
    if op == "domain_del":
        need_rate(ctx["uid"], 1)
        dom = _cf_host(p.get("domain"), "域名")
        await _cf_call("DELETE", f"{base}/{name}/domains/custom/{dom}", need=CF_NEED_R2)
        _r2_audit(ctx, "domain_del", name, dom)
        return await _r2_bucket_full(name)
    if op == "cors":
        need_rate(ctx["uid"], 1)
        origins = []
        for o in p.get("origins") or []:
            o = str(o).strip().rstrip("/")
            if o != "*" and not re.fullmatch(r"https?://[a-zA-Z0-9.\-]+(:\d{1,5})?", o):
                raise ApiError(f"「{o[:60]}」不是有效的来源，格式像 https://example.com")
            if o not in origins:
                origins.append(o)
        if origins:
            rules = [{"allowed": {"origins": origins[:50], "methods": ["GET", "HEAD", "PUT", "POST", "DELETE"], "headers": ["*"]},
                      "exposeHeaders": ["ETag"], "maxAgeSeconds": 3600}]
            await _cf_call("PUT", f"{base}/{name}/cors", {"rules": rules}, need=CF_NEED_R2)
        else:
            try:
                await _cf_call("DELETE", f"{base}/{name}/cors", need=CF_NEED_R2)
            except CfNotFound:
                pass
        _r2_audit(ctx, "cors", name, origins)
        return await _r2_bucket_full(name)

    # ---- 文件
    if op == "sign":
        _r2_need_keys()
        key = _r2_key(p)
        extra = {}
        if p.get("download"):
            fn = key.rsplit("/", 1)[-1]
            extra["response-content-disposition"] = "attachment; filename*=UTF-8''" + _s3_quote(fn)
        url = _s3_presign("GET", f"{acct['id']}.r2.cloudflarestorage.com", "/" + name + "/" + _s3_quote(key, safe="-_.~/"), _r2_keys(), extra=extra)
        return {"url": url, "expires": R2_SIGN_TTL}
    if op == "upload_url":
        keys = _r2_need_keys()
        key = _r2_key(p)
        origin = str(p.get("origin") or "").strip().rstrip("/")
        if not re.fullmatch(r"https://[a-zA-Z0-9.\-]+(:\d{1,5})?", origin):
            raise ApiError("页面来源不正确，请从 Telegram 里打开 Mini App")
        await _r2_ensure_cors(acct, name, origin)
        url = _s3_presign("PUT", f"{acct['id']}.r2.cloudflarestorage.com", "/" + name + "/" + _s3_quote(key, safe="-_.~/"), keys)
        _r2_audit(ctx, "upload", name, {"key": key, "size": p.get("size")})
        R2_USAGE["ts"] = 0; R2_LIVE.pop(name, None)
        return {"url": url, "expires": R2_SIGN_TTL}
    if op in ("mp_start", "mp_part", "mp_done", "mp_abort"):
        keys = _r2_need_keys()
        key = _r2_key(p)
        qpath = "/" + name + "/" + _s3_quote(key, safe="-_.~/")
        host = f"{acct['id']}.r2.cloudflarestorage.com"
        if op == "mp_start":
            origin = str(p.get("origin") or "").strip().rstrip("/")
            if not re.fullmatch(r"https://[a-zA-Z0-9.\-]+(:\d{1,5})?", origin):
                raise ApiError("页面来源不正确，请从 Telegram 里打开 Mini App")
            try:
                size = int(p.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            if size <= 0 or size > 5 * 1024 ** 4:
                raise ApiError("文件大小不正确")
            await _r2_ensure_cors(acct, name, origin, need_etag=True)
            hdr = {"content-type": str(p.get("type") or "application/octet-stream")[:100]}
            _st, raw = await asyncio.to_thread(_s3_call_sync, acct["id"], "POST", name, key, {"uploads": ""}, b"", hdr)
            m_ = re.search(rb"<UploadId>(.*?)</UploadId>", raw or b"")
            if not m_:
                raise ApiError("R2 没有返回上传编号", 502)
            part = max(16 * 1024 * 1024, -(-size // 9000))
            part = -(-part // (1024 * 1024)) * (1024 * 1024)
            _r2_audit(ctx, "upload", name, {"key": key, "size": size, "multipart": True})
            R2_USAGE["ts"] = 0; R2_LIVE.pop(name, None)
            return {"upload_id": m_.group(1).decode(), "part_size": part}
        uid_ = str(p.get("upload_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9._~+/=\-]{1,1024}", uid_):
            raise ApiError("上传编号不正确")
        if op == "mp_part":
            try:
                n = int(p.get("n"))
            except (TypeError, ValueError):
                n = 0
            if not 1 <= n <= 10000:
                raise ApiError("分片编号不正确")
            url = _s3_presign("PUT", host, qpath, keys, expires=3600, extra={"partNumber": str(n), "uploadId": uid_})
            return {"url": url}
        if op == "mp_abort":
            try:
                await asyncio.to_thread(_s3_call_sync, acct["id"], "DELETE", name, key, {"uploadId": uid_})
            except ApiError:
                pass
            return {"ok": True}
        parts = p.get("parts")
        if not isinstance(parts, list) or not 1 <= len(parts) <= 10000:
            raise ApiError("分片列表不正确")
        xml = "<CompleteMultipartUpload>"
        last = 0
        for it in sorted(parts, key=lambda x: int((x or {}).get("n") or 0)):
            n = int((it or {}).get("n") or 0)
            et = str((it or {}).get("etag") or "").strip()
            if n <= last or n > 10000 or not re.fullmatch(r'"?[A-Za-z0-9\-]{8,128}"?', et):
                raise ApiError("分片信息不正确")
            last = n
            xml += f"<Part><PartNumber>{n}</PartNumber><ETag>{et.strip(chr(34))}</ETag></Part>"
        xml += "</CompleteMultipartUpload>"
        _st, raw = await asyncio.to_thread(_s3_call_sync, acct["id"], "POST", name, key, {"uploadId": uid_}, xml.encode(), {"content-type": "application/xml"})
        if b"<Error>" in (raw or b""):
            m_ = re.search(rb"<Message>(.*?)</Message>", raw)
            raise ApiError("合并分片失败：" + (m_.group(1).decode(errors="replace")[:120] if m_ else "未知错误"), 502)
        R2_USAGE["ts"] = 0; R2_LIVE.pop(name, None)
        return {"ok": True}
    if op == "del":
        need_rate(ctx["uid"], 1)
        key = _r2_key(p, allow_dir=True)
        await asyncio.to_thread(_s3_call_sync, acct["id"], "DELETE", name, key)
        _r2_audit(ctx, "del", name, key)
        R2_USAGE["ts"] = 0; R2_LIVE.pop(name, None)
        return {"ok": True}
    if op == "mkdir":
        key = _r2_key(p, allow_dir=True)
        if not key.endswith("/"):
            raise ApiError("文件夹路径不正确")
        await asyncio.to_thread(_s3_call_sync, acct["id"], "PUT", name, key, None, b"", {"content-type": "application/x-directory"})
        _r2_audit(ctx, "mkdir", name, key)
        return {"ok": True}
    raise ApiError("未知操作")


# ---------------------------------------------------------------- IP 信息 / IP 质量


def _ip_card(ip, api, extra, ping_results=None, sources=None):
    prof = None
    try:
        prof = dh.ip_profile(api, extra, ping_results)
    except Exception as e:
        print("整理 IP 信息失败：", ip, e)
    card = {"ip": ip, "sources": sources or []}
    if prof:
        as_text = (prof.get("as") or "").split(",")[0].strip()
        card.update(country=prof.get("country") or "", location=prof.get("location") or "",
                    isp=prof.get("isp") or "", line=prof.get("line") or "",
                    asn=as_text, note=prof.get("note") or "")
    return card


async def _resolve(host):
    """返回 (是否域名, IP 列表, {ip: 来源})"""
    try:
        dh.ipaddress.ip_address(host)
        return False, [host], {}
    except ValueError:
        pass
    sources = {}
    try:
        sources = await asyncio.to_thread(dh.resolve_all_ips, host)
    except Exception as e:
        print("多地解析失败：", e)
    ips = list(sources)
    if not ips:
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, None, type=dh.socket.SOCK_STREAM), 6)
            ips = list(dict.fromkeys(i[4][0] for i in infos))
            sources = {ip: ["本机"] for ip in ips}
        except Exception:
            pass
    ips.sort(key=lambda ip: ":" in ip)
    return True, ips, sources


@action("ipinfo")
async def a_ipinfo(ctx, p):
    host, _ = target_host(p.get("target"))
    need_rate(ctx["uid"])
    is_domain, ips, sources = await _resolve(host)
    if not ips:
        raise ApiError("无法解析这个地址")
    ips = ips[:10]
    infos = await asyncio.to_thread(dh._ip_info_bundle, ips)
    return {"target": host, "domain": is_domain,
            "ips": [_ip_card(ip, *(infos.get(ip) or ({}, {})), None, sources.get(ip)) for ip in ips]}


def _parse_ipq(text):
    lines = [plain(x) for x in text.split("\n")]
    out = {"verdict": "", "groups": []}
    group = None
    for line in lines[3:]:
        line = line.strip()
        if not line or line.startswith("━") or line.startswith("💡"):
            continue
        if not out["verdict"]:
            out["verdict"] = line
            continue
        if line.startswith(("📋", "🛡")):
            group = {"title": line[1:].strip(), "rows": []}
            out["groups"].append(group)
            continue
        m = re.match(r"^(\S+)\s+([^：]+)：(.*)$", line)
        if m and group is not None:
            group["rows"].append({"icon": m.group(1), "label": m.group(2), "value": m.group(3)})
    v = out["verdict"]
    out["level"] = "good" if v.startswith("🟢") else "warn" if v.startswith("🟡") else "bad"
    out["verdict"] = re.sub(r"^\S+\s*", "", v)
    return out


@action("ipq")
async def a_ipq(ctx, p):
    host, _ = target_host(p.get("target"))
    need_rate(ctx["uid"])
    dh.recent_target_add(ctx["uid"], host)
    try:
        ip = await asyncio.to_thread(dh._ipq_resolve, host)
    except Exception:
        ip = None
    if not ip:
        raise ApiError("无法解析这个地址")
    q = await asyncio.to_thread(dh.ip_quality, ip)
    result = _parse_ipq(dh.ip_quality_text(host, ip, q))
    result.update(target=host, ip=ip)
    return result


# ---------------------------------------------------------------- 后台任务（全球 Ping / 路由 / 端口扫描）

JOBS = {}


def new_job(ctx, kind, title):
    now = time.time()
    for jid in [k for k, j in JOBS.items() if now - j["created"] > JOB_KEEP]:
        JOBS.pop(jid, None)
    mine = [j for j in JOBS.values() if j["uid"] == ctx["uid"] and not j["done"]]
    # 同类任务同一时间只允许跑一个：切页面回来再点“开始”也发不出第二个，不会把机器拖卡
    if any(j["kind"] == kind for j in mine):
        raise ApiError("这个功能还有任务在运行，请等它完成或先取消")
    if len(mine) >= JOB_PER_USER:
        raise ApiError("你有任务还在运行，请等它完成或先取消")
    jid = secrets.token_urlsafe(9)
    JOBS[jid] = {"id": jid, "uid": ctx["uid"], "kind": kind, "title": title, "created": now,
                 "done": False, "cancel": False, "error": None, "result": None, "progress": {},
                 "seen": False}
    return JOBS[jid]


def spawn(job, coro):
    async def runner():
        try:
            await coro
        except asyncio.CancelledError:
            job["error"] = "已取消"
        except ApiError as e:
            job["error"] = str(e)
        except Exception as e:
            print("Mini App 任务出错：", job["kind"], repr(e))
            job["error"] = str(e) or "任务出错，请稍后重试"
        finally:
            job["done"] = True
            job["finished"] = time.time()
    job["task"] = asyncio.create_task(runner())


# ---- 全球 Ping

async def run_ping(job, host, port, proto):
    nodes = [{"flag": n["flag"], "name": dh._global_ping_label(n), "state": "wait", "text": "排队中"}
             for n in dh.GLOBAL_PING_NODES]
    job["progress"] = {"nodes": nodes}
    plans = await asyncio.to_thread(dh._plan_global_ping)
    loop = asyncio.get_running_loop()

    async def one(i, cands):
        node = dh.GLOBAL_PING_NODES[i]
        if not cands:
            res = {"error": "无探针"}
        else:
            nodes[i].update(state="run", text="测试中")
            try:
                res = await asyncio.wait_for(
                    loop.run_in_executor(dh._GP_POOL, dh._global_ping_one, host, port, cands,
                                         proto, dh._global_ping_label(node)),
                    dh.GLOBAL_PING_NODE_MAX)
            except asyncio.TimeoutError:
                res = {"error": "超时"}
            except Exception as e:
                print("全球Ping节点出错：", e)
                res = {"error": "请求失败"}
        text = dh._global_ping_cell(res)
        rtt = res.get("rtt")
        ok = text.endswith("ms")
        nodes[i].update(state="ok" if ok else "fail", text=text,
                        rtt=rtt if ok else None)
        return res

    ping_task = asyncio.gather(*[one(i, c) for i, c in enumerate(plans)])
    is_domain, ips, sources = await _resolve(host)
    results = await ping_task
    for r in results:
        ip = r.get("ip")
        if ip and ip not in ips:
            ips.append(ip)
            sources[ip] = ["探针"]
    ips = ips[:10]
    infos = await asyncio.to_thread(dh._ip_info_bundle, ips) if ips else {}
    cards = []
    for ip in ips:
        api, extra = infos.get(ip) or ({}, {})
        hits = [r for r in results if r.get("ip") == ip] or (results if len(ips) == 1 else [])
        cards.append(_ip_card(ip, api, extra, hits, sources.get(ip)))
    rtts = [n["rtt"] for n in nodes if n.get("rtt") is not None]
    image = None
    try:
        image = await asyncio.to_thread(dh._render_ping_map, results)
    except Exception as e:
        print("生成全球Ping地图失败：", e)
    job["result"] = {
        "target": host, "port": port, "proto": proto, "domain": is_domain,
        "nodes": nodes, "ips": cards, "map": b64(image),
        "stat": {"ok": len(rtts), "total": len(nodes),
                 "min": round(min(rtts)) if rtts else None,
                 "avg": round(sum(rtts) / len(rtts)) if rtts else None,
                 "max": round(max(rtts)) if rtts else None},
    }


# ---- 路由追踪

ROUTE_NAMES = {"SH_CT": "电信", "SH_CU": "联通", "SH_CM": "移动"}


def route_once(target, code):
    location = next((loc for _l, c, loc in dh.TRACE_SOURCES if c == code), None)
    if location is None:
        raise ApiError("检测起点不存在")
    runs = dh._trace_with_fallback(target, location)
    raw_geo = dh._geo_batch([h["ip"] for _s, hops in runs for h in hops if h["ip"]])
    try:
        raw_geo = dh._refine_geo(runs, raw_geo)
    except Exception as e:
        print("精确定位失败：", e)
    analysed = []
    for src, hops in runs:
        geo = dh._sanitize_geo(src, hops, raw_geo)
        analysed.append({"source": src, "hops": hops, "geo": geo,
                         "route_profile": dh._route_profile(hops, geo),
                         "verdict": dh._classify_route(hops, geo, src)})
    best = dh._pick_run(analysed)
    source, hops, geo = best["source"], best["hops"], best["geo"]
    verdict = dh._verdict_summary(analysed)
    try:
        geo = dh._verify_exit(source, hops, geo)
    except Exception as e:
        print("出口定位失败：", e)
    points = dh._build_points(source, hops, geo)
    image = None
    if points:
        try:
            image = dh._render_route_map(points)
        except Exception as e:
            print("生成路由地图失败：", e)
    out_hops = []
    for h in hops:
        info = (geo.get(h["ip"]) if h["ip"] else None) or {}
        place = " ".join(x for x in (info.get("country"), info.get("regionName"), info.get("city")) if x)
        out_hops.append({"n": h["n"], "ip": h["ip"], "rtt": round(h["rtt"], 1) if h["rtt"] is not None else None,
                         "place": place, "as": (info.get("as") or "").split(",")[0]})
    return {
        "code": code, "carrier": ROUTE_NAMES.get(code, code),
        "source": source.get("label"), "reached": bool(source.get("reached")),
        "target_ip": source.get("target_ip"),
        "verdict": plain(verdict), "hops": out_hops,
        "points": [{"lat": pt["lat"], "lon": pt["lon"], "first": pt.get("first"), "last": pt.get("last"),
                    "start": bool(pt.get("start")), "dest": bool(pt.get("dest"))} for pt in points],
        "map": b64(image),
    }


async def run_route(job, target, codes):
    job["progress"] = {"items": [{"code": c, "carrier": ROUTE_NAMES.get(c, c), "state": "run"} for c in codes]}
    items = job["progress"]["items"]

    async def one(i, code):
        try:
            res = await asyncio.to_thread(route_once, target, code)
            items[i]["state"] = "ok"
            return res
        except Exception as e:
            items[i]["state"] = "fail"
            return {"code": code, "carrier": ROUTE_NAMES.get(code, code), "error": str(e) or "追踪失败"}

    results = await asyncio.gather(*[one(i, c) for i, c in enumerate(codes)])
    if all(r.get("error") for r in results):
        raise ApiError(results[0]["error"])
    job["result"] = {"target": target, "runs": results}


# ---- 端口扫描

async def run_scan(job, label, ip, ports):
    total = len(ports)
    started = time.time()
    open_ports = []
    counter = {"scanned": 0}
    job["progress"] = {"scanned": 0, "total": total, "open": open_ports, "label": label}
    timeout = dh.SCAN_TIMEOUT if total <= 2000 else dh.SCAN_TIMEOUT_LARGE
    port_iter = iter(ports)

    async def worker():
        while not job["cancel"]:
            port = next(port_iter, None)
            if port is None:
                return
            try:
                for attempt in range(3):
                    try:
                        async with dh._scan_slots():
                            _r, writer = await asyncio.wait_for(
                                dh.asyncio.open_connection(ip, port), timeout=timeout)
                            open_ports.append(port)
                            writer.close()
                            try:
                                await writer.wait_closed()
                            except Exception:
                                pass
                        break
                    except OSError as e:
                        if e.errno == dh.errno.EMFILE and attempt < 2:
                            await asyncio.sleep(0.2)
                            continue
                        break
                    except Exception:
                        break
            finally:
                counter["scanned"] += 1

    workers = [asyncio.create_task(worker()) for _ in range(max(1, min(dh._scan_concurrency(), total)))]
    while True:
        _d, pending = await asyncio.wait(workers, timeout=0.8)
        job["progress"]["scanned"] = counter["scanned"]
        job["progress"]["elapsed"] = round(time.time() - started, 1)
        if not pending or job["cancel"]:
            break
    await asyncio.gather(*workers, return_exceptions=True)
    ports_out = sorted(open_ports)
    job["result"] = {
        "label": label, "scanned": counter["scanned"], "total": total, "cancelled": job["cancel"],
        "elapsed": round(time.time() - started, 1),
        "open": [{"port": pt, "service": dh.PORT_SERVICES.get(pt, "")} for pt in ports_out],
    }


@action("job_start")
async def a_job_start(ctx, p):
    kind = p.get("kind")
    uid = ctx["uid"]
    if kind == "ping":
        parsed = dh._parse_global_ping_input(p.get("target") or "")
        if not parsed:
            raise ApiError("地址格式不正确，请输入 IP 或域名（可加端口）")
        host, port = parsed[0], parsed[1]
        proto = parsed[2]
        if p.get("port"):
            try:
                port = int(p["port"])
                if not 1 <= port <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                raise ApiError("端口范围 1-65535")
            proto = "TCP"
        need_rate(uid)
        dh.gping_recent_add(uid, host, port, proto)
        job = new_job(ctx, "ping", host if not port else f"{host}:{port}")
        spawn(job, run_ping(job, host, port, proto))
    elif kind == "route":
        host, _ = target_host(p.get("target"))
        codes = p.get("sources") or ["SH_CT"]
        if codes == "all":
            codes = list(ROUTE_NAMES)
        codes = [c for c in codes if c in ROUTE_NAMES][:3]
        if not codes:
            raise ApiError("请选择检测起点")
        need_rate(uid, len(codes))
        dh.recent_target_add(uid, host)
        job = new_job(ctx, "route", host)
        spawn(job, run_route(job, host, codes))
    elif kind == "scan":
        text = (p.get("target") or "").strip()
        parts = text.split(None, 1)
        if not parts:
            raise ApiError("请输入要扫描的 IP 或域名")
        target = dh._parse_ping_target(parts[0])
        if target is None:
            raise ApiError("地址格式不正确，请输入 IP 或域名")
        host, single = target
        spec = (p.get("ports") or (parts[1] if len(parts) > 1 else "")).strip()
        if spec:
            ports, err = dh._parse_port_spec(spec)
            if err:
                raise ApiError(err)
        elif single:
            ports = [int(single)]
        else:
            ports = list(dh.COMMON_PORTS)
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=dh.socket.SOCK_STREAM)
            ip = infos[0][4][0]
        except Exception:
            raise ApiError("无法解析这个地址")
        need_rate(uid)
        dh.recent_target_add(uid, host)
        dh.scan_recent_add(uid, host, spec or (str(single) if single else ""))
        label = host if host == ip else f"{host} ({ip})"
        job = new_job(ctx, "scan", label)
        spawn(job, run_scan(job, label, ip, ports))
    else:
        raise ApiError("未知任务")
    return {"id": job["id"]}


def _job_of(ctx, p):
    job = JOBS.get(p.get("id"))
    if not job or job["uid"] != ctx["uid"]:
        raise ApiError("任务不存在或已过期", 404)
    return job


@action("job_active")
async def a_job_active(ctx, p):
    """切换页面再回来时，前端靠它找回任务：还在跑的，或已跑完但结果还没被看过的。"""
    jobs = [j for j in JOBS.values()
            if j["uid"] == ctx["uid"] and (not j["done"] or not j.get("seen"))]
    jobs.sort(key=lambda j: j["created"])
    return {"jobs": [{"id": j["id"], "kind": j["kind"], "title": j["title"], "done": j["done"]}
                     for j in jobs]}


@action("job_get")
async def a_job_get(ctx, p):
    job = _job_of(ctx, p)
    if job["done"]:
        job["seen"] = True        # 结果已经交给前端显示过了，之后不再自动接回
    return {"id": job["id"], "kind": job["kind"], "title": job["title"], "done": job["done"],
            "error": job["error"], "progress": job["progress"], "result": job["result"],
            "elapsed": round((job.get("finished") or time.time()) - job["created"], 1)}


@action("job_cancel")
async def a_job_cancel(ctx, p):
    job = _job_of(ctx, p)
    job["cancel"] = True
    task = job.get("task")
    if task and not task.done() and job["kind"] != "scan":
        task.cancel()
    return {"ok": True}


@action("recent")
async def a_recent(ctx, p):
    """和机器人共用同一份「最近记录」：Ping / 路由 / IP 质量共用一份，端口扫描单独一份（带端口）。"""
    uid, out = ctx["uid"], {}
    try:
        out["ping"] = [{"label": dh._gping_label(i), "host": i.get("host"),
                        "port": i.get("port"), "proto": i.get("proto")}
                       for i in dh.gping_recent_list(uid)]
    except Exception:
        out["ping"] = []
    try:
        out["hosts"] = list(dh.recent_targets(uid))
    except Exception:
        out["hosts"] = []
    try:
        out["scan"] = [{"host": i["host"], "spec": i.get("spec") or "",
                        "label": dh._scan_spec_label(i.get("spec"))} for i in dh.scan_recent_list(uid)]
    except Exception:
        out["scan"] = []
    return out


# ---------------------------------------------------------------- HTTP 服务（只用标准库）


async def respond(writer, code, payload):
    body = json.dumps(payload, ensure_ascii=False).encode()
    reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
              404: "Not Found", 413: "Payload Too Large", 429: "Too Many Requests",
              500: "Internal Server Error"}.get(code, "OK")
    writer.write((f"HTTP/1.1 {code} {reason}\r\nContent-Type: application/json; charset=utf-8\r\n"
                  f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                  "Connection: close\r\n\r\n").encode() + body)
    try:
        await writer.drain()
    except Exception:
        pass


# ---------------------------------------------------------------- 主屏幕设备登录
# 在 Telegram 里点「生成配对码」→ 在主屏幕 App 里输入 → 换到一个长期设备令牌。
# 服务器只存令牌的 sha256；365 天滑动有效（每天最多续一次）；每人最多 10 台；随时可撤销。

DEV_FILE = _data_path("nav_devices.json")
DEV_DAYS = 365
DEV_MAX = 10
PAIR_TTL = 300
PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_DEV_LOCK = threading.Lock()
_PAIRS = {}                      # code -> (uid, expire)
_PAIR_FAILS = deque()            # 最近的配对失败时间
_PAIR_IP = {}                    # ip -> deque(失败时间)


def _dev_load():
    try:
        with open(DEV_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _dev_save(d):
    tmp = DEV_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, DEV_FILE)


def _tok_hash(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def device_auth(tok, ip=""):
    """令牌有效返回 (uid, 设备 id)，否则抛 401。"""
    if not re.fullmatch(r"[0-9a-f]{64}", tok or ""):
        raise ApiError("设备登录已失效，请重新配对", 401)
    h = _tok_hash(tok)
    now = time.time()
    with _DEV_LOCK:
        d = _dev_load()
        rec = d.get(h)
        if not rec or now - rec.get("last", 0) > DEV_DAYS * 86400:
            if rec:
                d.pop(h, None)
                _dev_save(d)
            raise ApiError("设备登录已失效，请重新配对", 401)
        if now - rec.get("last", 0) > 86400:       # 滑动续期：一天最多写一次
            rec["last"] = int(now)
            rec["ip"] = ip[:45]
            _dev_save(d)
    return int(rec["uid"]), rec["id"]


def _pair_cleanup(now):
    for c in [c for c, (_u, exp) in _PAIRS.items() if exp < now]:
        _PAIRS.pop(c, None)


@action("pair_new", admin=True)
async def a_pair_new(ctx, p):
    if ctx.get("via") != "tg":
        raise ApiError("请在 Telegram 里生成配对码")
    now = time.time()
    _pair_cleanup(now)
    for c in [c for c, (u, _e) in _PAIRS.items() if u == ctx["uid"]]:
        _PAIRS.pop(c, None)                         # 一人同时只有一个有效码
    code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(8))
    _PAIRS[code] = (ctx["uid"], now + PAIR_TTL)
    return {"code": code, "ttl": PAIR_TTL}


async def a_pair_claim(ip, p):
    now = time.time()
    code = re.sub(r"[\s-]", "", str(p.get("code") or "")).upper()
    while _PAIR_FAILS and now - _PAIR_FAILS[0] > 600:
        _PAIR_FAILS.popleft()
    for k in [k for k, q in _PAIR_IP.items() if not q or now - q[-1] > 600]:
        _PAIR_IP.pop(k, None)                     # 清掉 10 分钟前的记录，免得越积越多
    mine = _PAIR_IP.setdefault(ip, deque())
    while mine and now - mine[0] > 600:
        mine.popleft()
    if len(_PAIR_FAILS) >= 200 or len(mine) >= 5:      # 单个 IP 10 分钟 5 次；全站 200 次（防别人故意刷错把你锁住）
        raise ApiError("尝试次数过多，请 10 分钟后再试", 429)
    _pair_cleanup(now)
    ent = _PAIRS.pop(code, None) if re.fullmatch(r"[A-Z0-9]{8}", code) else None
    if not ent or ent[1] < now:
        _PAIR_FAILS.append(now)
        mine.append(now)
        raise ApiError("配对码不对或已过期", 400)
    uid = ent[0]
    if not dh.is_admin(uid):
        raise ApiError("没有权限", 403)
    name = re.sub(r"[<>&\"'\x00-\x1f]", "", str(p.get("name") or "主屏幕"))[:30] or "主屏幕"
    tok = secrets.token_hex(32)
    with _DEV_LOCK:
        d = _dev_load()
        mine_devs = sorted([(h, r) for h, r in d.items() if r.get("uid") == uid], key=lambda x: x[1].get("last", 0))
        while len(mine_devs) >= DEV_MAX:               # 超过上限，挤掉最久没用的
            d.pop(mine_devs.pop(0)[0], None)
        did = secrets.token_hex(4)
        d[_tok_hash(tok)] = {"id": did, "uid": uid, "name": name, "created": int(now),
                             "last": int(now), "ip": ip[:45]}
        _dev_save(d)
    return {"token": tok, "days": DEV_DAYS}


@action("devices", admin=True)
async def a_devices(ctx, p):
    with _DEV_LOCK:
        d = _dev_load()
    out = [{"id": r["id"], "name": r.get("name", ""), "created": r.get("created", 0),
            "last": r.get("last", 0), "ip": r.get("ip", ""), "current": r["id"] == ctx.get("dev")}
           for r in d.values() if r.get("uid") == ctx["uid"]]
    out.sort(key=lambda r: -r["last"])
    return {"list": out, "days": DEV_DAYS, "via": ctx.get("via")}


@action("device_revoke", admin=True)
async def a_device_revoke(ctx, p):
    did = str(p.get("id") or "")
    with _DEV_LOCK:
        d = _dev_load()
        hit = [h for h, r in d.items() if r.get("uid") == ctx["uid"] and r.get("id") == did]
        for h in hit:
            d.pop(h, None)
        if hit:
            _dev_save(d)
    if not hit:
        raise ApiError("设备不存在")
    _push_drop_dev(ctx["uid"], did)
    return {"revoked": did}


async def handle(reader, writer):
    try:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin1").split("\r\n")
            method, path, _v = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            length = int(headers.get("content-length") or 0)
            if length > (SSH_BODY_MAX if path == "/api/ssh_up_chunk" else MAX_BODY):      # 只有上传分块允许大请求
                return await respond(writer, 413, {"ok": False, "error": "请求太大"})
            raw = await asyncio.wait_for(reader.readexactly(length), 10) if length else b""
        except Exception:
            return await respond(writer, 400, {"ok": False, "error": "请求格式错误"})
        if not hmac.compare_digest(headers.get("x-api-key", ""), CONF["api_key"]):
            return await respond(writer, 403, {"ok": False, "error": "接口密钥不正确"})
        if method == "POST" and path == "/probe":
            code, out = probe_report(headers, raw)
            return await respond(writer, code, out)
        if path.startswith("/pub/"):                 # 节点 agent 报到 / 订阅（px.php 转发；agent 用自己的令牌，订阅用订阅密钥）
            pname, _, pq = path[5:].partition("?")
            try:
                if pname == "agent" and method == "POST":
                    code, text, _nid = px_agent(headers, raw)
                    return await respond_text(writer, code, text)
                if pname == "sh" and method == "GET":
                    code, text, extra = await asyncio.to_thread(px_sh_serve)
                    return await respond_text(writer, code, text, "text/plain; charset=utf-8", extra)
                if pname == "sub":
                    code, ctype, text, extra = px_sub_serve(headers, pq)
                    return await respond_text(writer, code, text, ctype, extra)
            except Exception as e:
                print("代理接口出错：", repr(e))
                return await respond_text(writer, 500, "ERR=server\n")
            return await respond_text(writer, 404, "ERR=notfound\n")
        if method != "POST" or not path.startswith("/api/"):
            return await respond(writer, 404, {"ok": False, "error": "接口不存在"})
        name = path[5:].split("?", 1)[0]
        if name == "pair_claim":
            try:
                pj = json.loads(raw.decode() or "{}")
                cip0 = re.sub(r"[^0-9a-fA-F:.]", "", headers.get("x-client-ip", ""))[:45] or "?"
                data = await a_pair_claim(cip0, pj if isinstance(pj, dict) else {})
                return await respond(writer, 200, {"ok": True, "data": data})
            except ApiError as e:
                return await respond(writer, e.code, {"ok": False, "error": str(e)})
            except Exception as e:
                print("配对出错：", repr(e))
                return await respond(writer, 500, {"ok": False, "error": "服务器出错，请稍后重试"})
        entry = ACTIONS.get(name)
        if not entry:
            return await respond(writer, 404, {"ok": False, "error": "接口不存在"})
        fn, need_admin = entry
        try:
            params = json.loads(raw.decode() or "{}")
            if not isinstance(params, dict):
                raise ValueError
        except Exception:
            return await respond(writer, 400, {"ok": False, "error": "参数格式错误"})
        cip = re.sub(r"[^0-9a-fA-F:.]", "", headers.get("x-client-ip", ""))[:45] or "?"
        try:
            user = None
            dev = None
            via = "tg"
            if headers.get("x-device-token"):
                uid, dev = device_auth(headers["x-device-token"], cip)
                user = {"id": uid, "first_name": str(uid)}
                via = "device"
                if not dh.is_admin(uid):                # 权限被收回后设备立刻失效
                    raise ApiError("设备登录已失效，请重新配对", 401)
            else:
                user = verify_init_data(headers.get("x-init-data", ""), dh.BOT_TOKEN, int(CONF["max_age"]))
            uid = user["id"]
            if not dh.can_use_tools(uid):
                raise ApiError(f"你没有使用权限，你的 ID：{uid}", 403)
            admin = dh.is_admin(uid)
            if need_admin and not admin:
                raise ApiError("只有管理员可以操作", 403)
            if name in OWNER_ONLY and not dh.is_owner(uid):
                raise ApiError("只有所有者可以操作", 403)
            ctx = {"uid": uid, "admin": admin, "via": via, "dev": dev,
                   "name": (user.get("first_name") or user.get("username") or str(uid))}
            data = await fn(ctx, params)
            await respond(writer, 200, {"ok": True, "data": data})
        except ApiError as e:
            await respond(writer, e.code, {"ok": False, "error": str(e)})
        except Exception as e:
            print("Mini App 接口出错：", name, repr(e))
            await respond(writer, 500, {"ok": False, "error": "服务器出错，请稍后重试"})
    finally:
        try:
            writer.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 离线 IP 库自动更新
# dh.py 只在库文件不存在时才下载，之后永远用旧的。这里每隔几小时看一眼文件有多旧，
# 超过 30 天就重新下载；下载的新库先用几个已知 IP 试查，没问题才替换，失败就继续用旧的。

IPDB_CHECK_EVERY = 6 * 3600
IPDB_MAX_AGE = 30 * 86400
IPDB_TEST_IPS = ("114.114.114.114", "8.8.8.8", "223.5.5.5")


def ipdb_update_once(force=False):
    """返回 'not due' / 'updated' / 'failed'。"""
    try:
        age = time.time() - os.path.getmtime(dh.IP2REGION_FILE)
    except OSError:
        age = None
    if not force and age is not None and age < IPDB_MAX_AGE:
        return "not due"
    for url in dh.IP2REGION_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read()
        except Exception as e:
            print("更新 IP 库下载失败：", url, e)
            continue
        if len(payload) < 1_000_000:
            continue
        old = dh._IP2REGION.get("buf")
        dh._IP2REGION["buf"] = payload              # 先试用新库
        try:
            hits = [dh.ip2region_search(ip) for ip in IPDB_TEST_IPS]
            ok = any(h and h.get("country") for h in hits)
        except Exception:
            ok = False
        if not ok:
            dh._IP2REGION["buf"] = old
            print("新 IP 库自检没通过，继续使用旧库：", url)
            continue
        tmp = dh.IP2REGION_FILE + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(payload)
            os.replace(tmp, dh.IP2REGION_FILE)
        except Exception as e:
            print("保存新 IP 库失败：", e)       # 内存里已经是新库，下次重启再试
            return "failed"
        print("离线 IP 库已更新：", url)
        return "updated"
    return "failed"


def _ipdb_loop():
    time.sleep(120)                                  # 等机器人启动完
    while True:
        try:
            ipdb_update_once()
        except Exception as e:
            print("IP 库自动更新出错：", e)
        time.sleep(IPDB_CHECK_EVERY)


# ---------------------------------------------------------------- GitHub 仓库管理（只有所有者能用）
# 在主屏幕 App / Telegram 里直接管理 GitHub：仓库列表、新建 / 删除仓库、浏览文件、看提交记录和改动、上传 / 删除文件。
# 令牌（GitHub Personal access token）只存在服务器上：在 App 里填一次，保存在 nav_github.json（权限 600），
# 也可以用环境变量 GITHUB_TOKEN（优先）。推荐 classic 令牌，勾选 repo 和 delete_repo。
# 上传：手机把文件切成小块发过来（每次请求最大 64KB），服务器拼好后一次提交（多个文件 = 一个 commit）。

GH_API = "https://api.github.com"
GH_FILE = _data_path("nav_github.json")
GH_UP_DIR = os.path.join(DATA_DIR, "gh_upload")
GH_CHUNK = 40 * 1024              # 每块原始字节（base64 后约 54KB，小于 64KB 的请求上限）
GH_FILE_MAX = 10 * 1024 * 1024    # 单个文件最大 10MB
GH_UP_MAX = 25 * 1024 * 1024      # 一次上传合计最大 25MB
GH_UP_FILES = 50                  # 一次最多 50 个文件
GH_UP_KEEP = 3600                 # 没提交的上传 1 小时后清掉
GH_VIEW_TEXT = 512 * 1024         # 文本文件最多显示 512KB
GH_VIEW_IMG = 2 * 1024 * 1024     # 图片最多显示 2MB
GH_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
GH_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
GH_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
GH_IMG = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
          "webp": "image/webp", "svg": "image/svg+xml", "ico": "image/x-icon", "bmp": "image/bmp"}
GH_UPLOADS = {}                   # 上传 id → 状态（提交进度 / 结果）
GH_USER = {"t": 0, "tok": "", "d": None}

OWNER_ONLY.update({"gh", "gh_token", "gh_repos", "gh_repo", "gh_repo_new", "gh_repo_del", "gh_dir", "gh_file",
                   "gh_commits", "gh_commit", "gh_zip", "gh_zip_chunk", "gh_up_begin", "gh_up_chunk", "gh_up_commit", "gh_up_state", "gh_del", "gh_mv"})


class GhNotFound(ApiError):
    pass


class GhEmpty(ApiError):
    """仓库还是空的（一个提交都没有）。"""


def _gh_load():
    try:
        with open(GH_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _gh_save(d):
    tmp = GH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, GH_FILE)


def _gh_token(required=True):
    tok = (os.environ.get("GITHUB_TOKEN") or _gh_load().get("token") or "").strip()
    if not tok and required:
        raise ApiError("还没有设置 GitHub 令牌", 400)
    return tok


def _gh_call_sync(method, path, body=None, query=None, accept=None, token=None, raw=False, timeout=25):
    """调 GitHub 接口。返回 (内容, 响应头)；raw=True 时内容是 bytes，否则是解析好的 JSON。"""
    url = (path if path.startswith("https://") else GH_API + path) + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + (token or _gh_token()),
        "Accept": accept or "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "nav-miniapp/1.0",
        **({"Content-Type": "application/json"} if data is not None else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, out, hdr = r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        status, out, hdr = e.code, e.read(), e.headers
    except Exception as e:
        raise ApiError("连接不上 GitHub：" + str(getattr(e, "reason", e))[:80], 502)
    if status < 300:
        if raw:
            return out, hdr
        try:
            return (json.loads(out.decode() or "null") if out else None), hdr
        except Exception:
            raise ApiError("GitHub 返回的内容无法识别", 502)
    try:
        j = json.loads(out.decode() or "{}")
    except Exception:
        j = {}
    msg = str(j.get("message") or "")
    if status == 401:
        raise ApiError("GitHub 令牌无效或已过期，请到「GitHub 令牌」里重新设置", 400)
    if status == 403 and "rate limit" in msg.lower():
        raise ApiError("GitHub 接口调用太频繁，请过几分钟再试", 429)
    if status in (403, 404) and "delete" in method.lower() and "/repos/" in path and path.count("/") == 3:
        raise ApiError("没有删除仓库的权限：令牌需要勾选 delete_repo", 400)
    if status == 403:
        raise ApiError("令牌权限不够（需要 repo 权限）" + ("：" + msg[:80] if msg else ""), 400)
    if status == 404:
        raise GhNotFound("找不到（仓库或文件不存在，或令牌没有这个仓库的权限）", 404)
    if status == 409 and "empty" in msg.lower():
        raise GhEmpty("仓库还是空的", 409)
    errs = "；".join(str(e.get("message") or e.get("code") or "") for e in (j.get("errors") or []) if isinstance(e, dict))
    if status == 422 and "name already exists" in (errs + msg).lower():
        raise ApiError("已经有同名仓库了", 400)
    raise ApiError("GitHub：" + ((errs or msg) or f"出错（{status}）")[:160], 400)


async def _gh(method, path, body=None, query=None, accept=None, raw=False, timeout=25):
    out, _h = await asyncio.to_thread(_gh_call_sync, method, path, body, query, accept, None, raw, timeout)
    return out


def _gh_repo(name):
    name = str(name or "").strip()
    if not GH_REPO_RE.match(name) or ".." in name:
        raise ApiError("仓库名不正确")
    return name


def _gh_path(path, allow_root=True):
    """仓库里的路径：去掉首尾斜杠，不许 .. 和奇怪的字符。"""
    path = str(path or "").strip().strip("/")
    path = re.sub(r"/{2,}", "/", path)
    if not path:
        if allow_root:
            return ""
        raise ApiError("路径不能为空")
    if len(path) > 400 or any(seg in ("", ".", "..") for seg in path.split("/")) or re.search(r"[\x00-\x1f\\]", path):
        raise ApiError("路径不正确：" + path[:60])
    return path


def _gh_q(path):
    return urllib.parse.quote(path, safe="/")


async def _gh_me(force=False):
    tok = _gh_token()
    if not force and GH_USER["d"] and GH_USER["tok"] == tok and time.time() - GH_USER["t"] < 300:
        return GH_USER["d"]
    u, h = await asyncio.to_thread(_gh_call_sync, "GET", "/user", None, None, None, tok)
    d = {"login": u.get("login"), "name": u.get("name") or "", "avatar": u.get("avatar_url") or "",
         "scopes": [s.strip() for s in (h.get("X-OAuth-Scopes") or "").split(",") if s.strip()],
         "classic": h.get("X-OAuth-Scopes") is not None}
    GH_USER.update(t=time.time(), tok=tok, d=d)
    return d


@action("gh", admin=True)
async def a_gh(ctx, p):
    tok = _gh_token(False)
    if not tok:
        return {"set": False}
    try:
        me = await _gh_me(bool(p.get("force")))
    except ApiError as e:
        return {"set": True, "error": str(e), "env": bool(os.environ.get("GITHUB_TOKEN"))}
    return {"set": True, "env": bool(os.environ.get("GITHUB_TOKEN")), "tail": tok[-4:], **me}


@action("gh_token", admin=True)
async def a_gh_token(ctx, p):
    if os.environ.get("GITHUB_TOKEN"):
        raise ApiError("令牌是用服务器环境变量 GITHUB_TOKEN 设置的，要在服务器上修改")
    if p.get("op") == "del":
        d = _gh_load()
        d.pop("token", None)
        _gh_save(d)
        GH_USER.update(t=0, tok="", d=None)
        return {"set": False}
    tok = str(p.get("token") or "").strip()
    if not re.match(r"^[A-Za-z0-9_]{20,255}$", tok):
        raise ApiError("令牌格式不对：应该是 ghp_ 或 github_pat_ 开头的一串字符")
    try:
        u, h = await asyncio.to_thread(_gh_call_sync, "GET", "/user", None, None, None, tok)
    except ApiError as e:
        raise ApiError("这个令牌用不了：" + str(e))
    d = _gh_load()
    d["token"] = tok
    _gh_save(d)
    GH_USER.update(t=0, tok="", d=None)
    return await a_gh(ctx, {"force": True})


def _gh_repo_out(r):
    return {"full": r.get("full_name"), "name": r.get("name"), "private": bool(r.get("private")),
            "desc": r.get("description") or "", "branch": r.get("default_branch") or "main",
            "pushed": r.get("pushed_at") or r.get("updated_at") or "", "size": r.get("size") or 0,
            "lang": r.get("language") or "", "fork": bool(r.get("fork")), "archived": bool(r.get("archived")),
            "owner": (r.get("owner") or {}).get("login") or "", "url": r.get("html_url") or ""}


@action("gh_repos", admin=True)
async def a_gh_repos(ctx, p):
    page = max(1, min(50, int(p.get("page") or 1)))
    per = max(1, min(100, int(p.get("per") or 50)))
    rows = await _gh("GET", "/user/repos", query={"sort": "pushed", "per_page": per, "page": page,
                                                   "affiliation": "owner,collaborator,organization_member"})
    return {"items": [_gh_repo_out(r) for r in rows or []], "more": len(rows or []) >= per}


@action("gh_repo", admin=True)
async def a_gh_repo(ctx, p):
    repo = _gh_repo(p.get("repo"))
    r = await _gh("GET", "/repos/" + repo)
    return _gh_repo_out(r)


@action("gh_repo_new", admin=True)
async def a_gh_repo_new(ctx, p):
    name = str(p.get("name") or "").strip()
    if not GH_NAME_RE.match(name) or name in (".", ".."):
        raise ApiError("仓库名只能用英文字母、数字、- _ .，最长 100 个字符")
    desc = str(p.get("desc") or "").strip()[:300]
    r = await _gh("POST", "/user/repos", {"name": name, "description": desc, "private": bool(p.get("private", True)),
                                          "auto_init": True})
    print("GitHub 新建仓库：", r.get("full_name"), "by", ctx["uid"])
    return _gh_repo_out(r)


@action("gh_repo_del", admin=True)
async def a_gh_repo_del(ctx, p):
    repo = _gh_repo(p.get("repo"))
    if str(p.get("confirm") or "").strip() != repo.split("/")[1]:
        raise ApiError("确认的仓库名不对，没有删除")
    await _gh("DELETE", "/repos/" + repo)
    print("GitHub 删除仓库：", repo, "by", ctx["uid"])
    return {"ok": True}


def _gh_item(x):
    return {"name": x.get("name"), "path": x.get("path"), "type": x.get("type"),
            "size": x.get("size") or 0, "sha": x.get("sha")}


@action("gh_dir", admin=True)
async def a_gh_dir(ctx, p):
    repo, path = _gh_repo(p.get("repo")), _gh_path(p.get("path"))
    try:
        rows = await _gh("GET", f"/repos/{repo}/contents/{_gh_q(path)}")
    except GhNotFound:
        if path:
            raise
        return {"items": [], "empty": True}
    if not isinstance(rows, list):
        raise ApiError("这不是文件夹")
    items = [_gh_item(x) for x in rows]
    items.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    out = {"items": items}
    if not path:            # 根目录：顺带取 README（GitHub 排好版的 HTML）
        try:
            html = await _gh("GET", f"/repos/{repo}/readme", accept="application/vnd.github.html", raw=True)
            out["readme"] = html.decode("utf-8", "replace")[:400000]
        except ApiError:
            pass
    return out


@action("gh_file", admin=True)
async def a_gh_file(ctx, p):
    repo, path = _gh_repo(p.get("repo")), _gh_path(p.get("path"), False)
    meta = await _gh("GET", f"/repos/{repo}/contents/{_gh_q(path)}")
    if isinstance(meta, list) or meta.get("type") != "file":
        raise ApiError("这不是文件")
    size, name = int(meta.get("size") or 0), meta.get("name") or path
    out = {"name": name, "path": path, "size": size, "sha": meta.get("sha"), "url": meta.get("html_url") or ""}
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    limit = GH_VIEW_IMG if ext in GH_IMG else GH_VIEW_TEXT
    if size > limit:
        out["big"] = True
        return out
    if meta.get("content") and meta.get("encoding") == "base64":
        body = base64.b64decode(meta["content"])
    else:
        body = await _gh("GET", f"/repos/{repo}/contents/{_gh_q(path)}", accept="application/vnd.github.raw", raw=True)
    if ext in GH_IMG:
        out["img"] = f"data:{GH_IMG[ext]};base64," + base64.b64encode(body).decode()
        return out
    if b"\x00" in body[:8000]:
        out["binary"] = True
        return out
    try:
        out["text"] = body.decode("utf-8")
    except UnicodeDecodeError:
        try:
            out["text"] = body.decode("gb18030")
        except UnicodeDecodeError:
            out["binary"] = True
            return out
    if ext in ("md", "markdown"):
        try:
            html = await _gh("GET", f"/repos/{repo}/contents/{_gh_q(path)}", accept="application/vnd.github.html", raw=True)
            out["html"] = html.decode("utf-8", "replace")[:400000]
        except ApiError:
            pass
    return out


@action("gh_commits", admin=True)
async def a_gh_commits(ctx, p):
    repo = _gh_repo(p.get("repo"))
    page = max(1, min(100, int(p.get("page") or 1)))
    q = {"per_page": 30, "page": page}
    if p.get("path"):
        q["path"] = _gh_path(p.get("path"))
    try:
        rows = await _gh("GET", f"/repos/{repo}/commits", query=q)
    except GhEmpty:
        return {"items": [], "more": False}
    items = []
    for c in rows or []:
        cm = c.get("commit") or {}
        au = cm.get("author") or {}
        items.append({"sha": c.get("sha"), "msg": (cm.get("message") or "").strip(),
                      "author": au.get("name") or (c.get("author") or {}).get("login") or "",
                      "date": au.get("date") or (cm.get("committer") or {}).get("date") or ""})
    return {"items": items, "more": len(items) >= 30}


@action("gh_commit", admin=True)
async def a_gh_commit(ctx, p):
    repo, sha = _gh_repo(p.get("repo")), str(p.get("sha") or "")
    if not GH_SHA_RE.match(sha):
        raise ApiError("提交编号不正确")
    c = await _gh("GET", f"/repos/{repo}/commits/{sha}")
    cm = c.get("commit") or {}
    budget = 4000                   # 整个提交最多显示 4000 行改动，太大的文件只给统计
    files = []
    for f in c.get("files") or []:
        patch = f.get("patch")
        lines = patch.split("\n") if patch else []
        cut = False
        if len(lines) > 600 or len(lines) > budget:
            lines, cut = lines[:min(600, max(0, budget))], True
        budget -= len(lines)
        files.append({"name": f.get("filename"), "old": f.get("previous_filename") or "", "status": f.get("status"),
                      "add": f.get("additions") or 0, "del": f.get("deletions") or 0,
                      "patch": "\n".join(lines) if lines else None, "cut": cut, "nopatch": patch is None})
    return {"sha": c.get("sha"), "msg": (cm.get("message") or "").strip(),
            "author": (cm.get("author") or {}).get("name") or "", "date": (cm.get("author") or {}).get("date") or "",
            "add": (c.get("stats") or {}).get("additions") or 0, "del": (c.get("stats") or {}).get("deletions") or 0,
            "files": files, "url": c.get("html_url") or ""}


class _GhNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                      # 不跟跳转：要的就是跳转后的下载地址


def _gh_zip_sync(repo):
    """整个仓库打包下载：GitHub 会跳转到 codeload.github.com 的临时地址（私有仓库带几分钟有效的临时令牌）。"""
    req = urllib.request.Request(GH_API + f"/repos/{repo}/zipball", method="GET", headers={
        "Authorization": "Bearer " + _gh_token(), "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "nav-miniapp/1.0"})
    try:
        urllib.request.build_opener(_GhNoRedirect).open(req, timeout=20)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location") or ""
        if e.code in (301, 302, 303, 307) and loc.startswith("https://"):
            return loc
        if e.code == 404:
            raise ApiError("找不到这个仓库，或者仓库还是空的", 404)
        raise ApiError(f"GitHub 打包失败（{e.code}）", 400)
    except Exception as e:
        raise ApiError("连接不上 GitHub：" + str(getattr(e, "reason", e))[:80], 502)
    raise ApiError("GitHub 没有返回下载地址", 502)


GH_ZIP_MAX = 60 * 1024 * 1024       # 仓库打包最大 60MB
GH_ZIP_CHUNK = 384 * 1024           # 每次给手机 384KB（base64 后约 512KB）


def _gh_zip_fetch(repo, dest):
    """服务器先把 ZIP 下载到本地临时目录，手机再分块取回（手机直连 codeload.github.com 在国内经常打不开）。"""
    url = _gh_zip_sync(repo)
    req = urllib.request.Request(url, headers={"User-Agent": "nav-miniapp/1.0"})
    size = 0
    try:
        with urllib.request.urlopen(req, timeout=40) as r, open(dest, "wb") as fh:
            while True:
                b = r.read(256 * 1024)
                if not b:
                    break
                size += len(b)
                if size > GH_ZIP_MAX:
                    raise ApiError(f"仓库打包后超过 {GH_ZIP_MAX // 1024 // 1024}MB，请到电脑上用 git clone 下载")
                fh.write(b)
    except ApiError:
        raise
    except Exception as e:
        raise ApiError("从 GitHub 下载打包文件失败：" + str(getattr(e, "reason", e))[:80], 502)
    return size


@action("gh_zip", admin=True)
async def a_gh_zip(ctx, p):
    repo = _gh_repo(p.get("repo"))
    _gh_up_clean()
    zid = secrets.token_hex(8)
    d = os.path.join(GH_UP_DIR, zid)
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        size = await asyncio.wait_for(asyncio.to_thread(_gh_zip_fetch, repo, os.path.join(d, "repo.zip")), 42)
    except asyncio.TimeoutError:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        raise ApiError("打包下载太慢，请稍后再试", 504)
    except Exception:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        raise
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"uid": ctx["uid"], "zip": True, "t": time.time()}, fh)
    return {"id": zid, "size": size, "chunk": GH_ZIP_CHUNK, "name": repo.split("/")[1] + ".zip"}


@action("gh_zip_chunk", admin=True)
async def a_gh_zip_chunk(ctx, p):
    d, m = _gh_up_meta(ctx["uid"], p.get("id"))
    if not m.get("zip"):
        raise ApiError("下载编号不正确")
    fp = os.path.join(d, "repo.zip")
    try:
        n = int(p.get("n"))
        size = os.path.getsize(fp)
    except Exception:
        raise ApiError("下载已过期，请重新点下载")
    if not 0 <= n * GH_ZIP_CHUNK < max(1, size):
        raise ApiError("分块编号不正确")
    with open(fp, "rb") as fh:
        fh.seek(n * GH_ZIP_CHUNK)
        b = fh.read(GH_ZIP_CHUNK)
    last = n * GH_ZIP_CHUNK + len(b) >= size
    if last and p.get("done"):
        import shutil
        shutil.rmtree(d, ignore_errors=True)     # 取完最后一块就删掉临时文件
    return {"data": base64.b64encode(b).decode(), "last": last}


# ---- 上传：begin（登记文件）→ chunk（分块）→ commit（后台提交，前端查 gh_up_state 看进度）

def _gh_up_clean():
    now = time.time()
    try:
        names = os.listdir(GH_UP_DIR)
    except FileNotFoundError:
        return
    for n in names:
        d = os.path.join(GH_UP_DIR, n)
        try:
            if now - os.path.getmtime(d) > GH_UP_KEEP:
                import shutil
                shutil.rmtree(d, ignore_errors=True)
                GH_UPLOADS.pop(n, None)
        except OSError:
            pass


def _gh_up_meta(uid, up_id):
    if not re.match(r"^[0-9a-f]{16}$", str(up_id or "")):
        raise ApiError("上传编号不正确")
    d = os.path.join(GH_UP_DIR, up_id)
    try:
        with open(os.path.join(d, "meta.json"), "r", encoding="utf-8") as f:
            m = json.load(f)
    except Exception:
        raise ApiError("上传已过期，请重新选择文件")
    if m.get("uid") != uid:
        raise ApiError("上传已过期，请重新选择文件")
    return d, m


async def _gh_clean_loop():
    """每 10 分钟清一次上传 / 下载留下的临时文件（超过 1 小时没用完的），机器人启动时也先清一次。"""
    while True:
        try:
            await asyncio.to_thread(_gh_up_clean)
        except Exception as e:
            print("清理 GitHub 临时文件出错：", e)
        await asyncio.sleep(600)


@action("gh_up_begin", admin=True)
async def a_gh_up_begin(ctx, p):
    repo, folder = _gh_repo(p.get("repo")), _gh_path(p.get("dir"))
    files = p.get("files")
    if not isinstance(files, list) or not files:
        raise ApiError("没有选择文件")
    if len(files) > GH_UP_FILES:
        raise ApiError(f"一次最多上传 {GH_UP_FILES} 个文件")
    out, total, seen = [], 0, set()
    for f in files:
        name = str((f or {}).get("name") or "").strip()
        size = int((f or {}).get("size") or 0)
        if not name or "/" in name or "\\" in name or name in (".", "..") or len(name) > 200 or re.search(r"[\x00-\x1f]", name):
            raise ApiError("文件名不正确：" + name[:40])
        if size < 0 or size > GH_FILE_MAX:
            raise ApiError(f"「{name}」超过 {GH_FILE_MAX // 1024 // 1024}MB，太大了")
        full = _gh_path((folder + "/" if folder else "") + name, False)
        if full in seen:
            raise ApiError("有重名的文件：" + name)
        seen.add(full)
        total += size
        out.append({"name": name, "path": full, "size": size, "chunks": max(1, -(-size // GH_CHUNK))})
    if total > GH_UP_MAX:
        raise ApiError(f"合计超过 {GH_UP_MAX // 1024 // 1024}MB，请分几次上传")
    _gh_up_clean()
    up_id = secrets.token_hex(8)
    d = os.path.join(GH_UP_DIR, up_id)
    os.makedirs(d, mode=0o700, exist_ok=True)
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"uid": ctx["uid"], "repo": repo, "files": out, "t": time.time()}, fh, ensure_ascii=False)
    return {"id": up_id, "chunk": GH_CHUNK, "files": out}


@action("gh_up_chunk", admin=True)
async def a_gh_up_chunk(ctx, p):
    d, m = _gh_up_meta(ctx["uid"], p.get("id"))
    try:
        i, n = int(p.get("i")), int(p.get("n"))
        f = m["files"][i]
    except Exception:
        raise ApiError("分块编号不正确")
    if not 0 <= n < f["chunks"]:
        raise ApiError("分块编号不正确")
    try:
        data = base64.b64decode(str(p.get("data") or ""), validate=True)
    except Exception:
        raise ApiError("分块内容不正确")
    if len(data) > GH_CHUNK:
        raise ApiError("分块太大")
    with open(os.path.join(d, f"{i}.{n}"), "wb") as fh:
        fh.write(data)
    return {"ok": True}


def _gh_up_run(up_id, d, m, msg):
    """在线程里跑：拼文件 → 提交到 GitHub。进度写在 GH_UPLOADS[up_id]。"""
    st = GH_UPLOADS[up_id]
    repo, files = m["repo"], m["files"]
    try:
        blobs = []
        for i, f in enumerate(files):
            parts = []
            for n in range(f["chunks"]):
                fp = os.path.join(d, f"{i}.{n}")
                if not os.path.exists(fp):
                    if f["size"] == 0 and n == 0:
                        continue
                    raise ApiError(f"「{f['name']}」没有传完整，请重新上传")
                with open(fp, "rb") as fh:
                    parts.append(fh.read())
            body = b"".join(parts)
            if len(body) != f["size"]:
                raise ApiError(f"「{f['name']}」大小对不上，请重新上传")
            blobs.append((f["path"], body))
        info, _h = _gh_call_sync("GET", "/repos/" + repo)
        branch = info.get("default_branch") or "main"
        try:
            ref, _h = _gh_call_sync("GET", f"/repos/{repo}/git/ref/heads/{_gh_q(branch)}")
            base = ref["object"]["sha"]
        except (GhNotFound, GhEmpty):
            base = None
        if base is None:
            # 空仓库：Git 数据接口用不了，只能一个文件一个提交
            sha = None
            for k, (path, body) in enumerate(blobs):
                st["step"] = f"上传第 {k + 1}/{len(blobs)} 个文件"
                r, _h = _gh_call_sync("PUT", f"/repos/{repo}/contents/{_gh_q(path)}",
                                      {"message": msg, "content": base64.b64encode(body).decode(), "branch": branch},
                                      timeout=120)
                sha = ((r or {}).get("commit") or {}).get("sha")
            st.update(state="done", sha=sha, n=len(blobs))
            return
        commit, _h = _gh_call_sync("GET", f"/repos/{repo}/git/commits/{base}")
        tree = []
        for k, (path, body) in enumerate(blobs):
            st["step"] = f"上传第 {k + 1}/{len(blobs)} 个文件"
            b, _h = _gh_call_sync("POST", f"/repos/{repo}/git/blobs",
                                  {"content": base64.b64encode(body).decode(), "encoding": "base64"}, timeout=120)
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": b["sha"]})
        st["step"] = "生成提交"
        t, _h = _gh_call_sync("POST", f"/repos/{repo}/git/trees", {"base_tree": commit["tree"]["sha"], "tree": tree})
        c, _h = _gh_call_sync("POST", f"/repos/{repo}/git/commits", {"message": msg, "tree": t["sha"], "parents": [base]})
        _gh_call_sync("PATCH", f"/repos/{repo}/git/refs/heads/{_gh_q(branch)}", {"sha": c["sha"]})
        st.update(state="done", sha=c["sha"], n=len(blobs))
        print("GitHub 上传：", repo, len(blobs), "个文件", c["sha"][:7])
    except ApiError as e:
        st.update(state="error", error=str(e))
    except Exception as e:
        print("GitHub 上传出错：", repr(e))
        st.update(state="error", error="提交出错：" + str(e)[:100])
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


@action("gh_up_commit", admin=True)
async def a_gh_up_commit(ctx, p):
    d, m = _gh_up_meta(ctx["uid"], p.get("id"))
    up_id = p["id"]
    if up_id in GH_UPLOADS:
        return {k: v for k, v in GH_UPLOADS[up_id].items() if k != "uid"}
    names = [f["name"] for f in m["files"]]
    msg = str(p.get("msg") or "").strip()[:500] or ("上传 " + names[0] if len(names) == 1 else f"上传 {len(names)} 个文件")
    GH_UPLOADS[up_id] = {"state": "running", "step": "准备中", "uid": ctx["uid"], "t": time.time()}
    threading.Thread(target=_gh_up_run, args=(up_id, d, m, msg), daemon=True).start()
    return {k: v for k, v in GH_UPLOADS[up_id].items() if k != "uid"}


@action("gh_up_state", admin=True)
async def a_gh_up_state(ctx, p):
    st = GH_UPLOADS.get(str(p.get("id") or ""))
    if not st or st.get("uid") != ctx["uid"]:
        raise ApiError("找不到这次上传")
    for k in [k for k, v in GH_UPLOADS.items() if v.get("state") != "running" and time.time() - v.get("t", 0) > GH_UP_KEEP]:
        GH_UPLOADS.pop(k, None)
    return {k: v for k, v in st.items() if k != "uid"}


def _gh_del_sync(repo, paths, msg):
    info, _h = _gh_call_sync("GET", "/repos/" + repo)
    branch = info.get("default_branch") or "main"
    ref, _h = _gh_call_sync("GET", f"/repos/{repo}/git/ref/heads/{_gh_q(branch)}")
    base = ref["object"]["sha"]
    commit, _h = _gh_call_sync("GET", f"/repos/{repo}/git/commits/{base}")
    full, _h = _gh_call_sync("GET", f"/repos/{repo}/git/trees/{commit['tree']['sha']}", query={"recursive": "1"})
    if full.get("truncated"):
        raise ApiError("仓库文件太多，没法在这里删除，请到 GitHub 网站上删")
    gone = [x["path"] for x in full.get("tree") or [] if x.get("type") == "blob"
            and any(x["path"] == q or x["path"].startswith(q + "/") for q in paths)]
    if not gone:
        raise ApiError("没有找到要删除的文件")
    tree = [{"path": q, "mode": "100644", "type": "blob", "sha": None} for q in gone]
    t, _h = _gh_call_sync("POST", f"/repos/{repo}/git/trees", {"base_tree": commit["tree"]["sha"], "tree": tree})
    c, _h = _gh_call_sync("POST", f"/repos/{repo}/git/commits", {"message": msg, "tree": t["sha"], "parents": [base]})
    _gh_call_sync("PATCH", f"/repos/{repo}/git/refs/heads/{_gh_q(branch)}", {"sha": c["sha"]})
    return {"sha": c["sha"], "n": len(gone)}


@action("gh_del", admin=True)
async def a_gh_del(ctx, p):
    repo = _gh_repo(p.get("repo"))
    paths = p.get("paths")
    if not isinstance(paths, list) or not paths or len(paths) > 100:
        raise ApiError("没有选择要删除的文件")
    paths = [_gh_path(x, False) for x in paths]
    msg = str(p.get("msg") or "").strip()[:500] or ("删除 " + paths[0] if len(paths) == 1 else f"删除 {len(paths)} 项")
    r = await asyncio.to_thread(_gh_del_sync, repo, paths, msg)
    print("GitHub 删除文件：", repo, r["n"], "个", r["sha"][:7])
    return r


def _gh_mv_sync(repo, src, dst, msg):
    """重命名 / 移动文件或文件夹：一次提交里“在新位置加上 + 删掉旧位置”，文件内容不重新上传（直接复用原来的 blob）。"""
    info, _h = _gh_call_sync("GET", "/repos/" + repo)
    branch = info.get("default_branch") or "main"
    ref, _h = _gh_call_sync("GET", f"/repos/{repo}/git/ref/heads/{_gh_q(branch)}")
    base = ref["object"]["sha"]
    commit, _h = _gh_call_sync("GET", f"/repos/{repo}/git/commits/{base}")
    full, _h = _gh_call_sync("GET", f"/repos/{repo}/git/trees/{commit['tree']['sha']}", query={"recursive": "1"})
    if full.get("truncated"):
        raise ApiError("仓库文件太多，没法在这里重命名，请到 GitHub 网站上操作")
    ents = [x for x in full.get("tree") or [] if x.get("type") in ("blob", "commit")]
    moving = [x for x in ents if x["path"] == src or x["path"].startswith(src + "/")]
    if not moving:
        raise ApiError("没有找到这个文件 / 文件夹")
    if any(x["type"] == "commit" for x in moving):
        raise ApiError("里面有子模块，不能在这里移动")
    parts = dst.split("/")
    for x in ents:
        if x["path"] == dst or x["path"].startswith(dst + "/"):
            raise ApiError("新位置已经有同名的文件或文件夹：" + dst)
        if any(x["path"] == "/".join(parts[:i]) for i in range(1, len(parts))):
            raise ApiError("「" + x["path"] + "」是一个文件，不能当文件夹用")
    tree = []
    for x in moving:
        tree.append({"path": dst + x["path"][len(src):], "mode": x["mode"], "type": "blob", "sha": x["sha"]})
        tree.append({"path": x["path"], "mode": x["mode"], "type": "blob", "sha": None})
    t, _h = _gh_call_sync("POST", f"/repos/{repo}/git/trees", {"base_tree": commit["tree"]["sha"], "tree": tree})
    c, _h = _gh_call_sync("POST", f"/repos/{repo}/git/commits", {"message": msg, "tree": t["sha"], "parents": [base]})
    _gh_call_sync("PATCH", f"/repos/{repo}/git/refs/heads/{_gh_q(branch)}", {"sha": c["sha"]})
    return {"sha": c["sha"], "n": len(moving), "path": dst}


@action("gh_mv", admin=True)
async def a_gh_mv(ctx, p):
    repo = _gh_repo(p.get("repo"))
    src, dst = _gh_path(p.get("src"), False), _gh_path(p.get("dst"), False)
    if src == dst:
        raise ApiError("新名字和原来一样")
    if dst.startswith(src + "/"):
        raise ApiError("不能把文件夹移到它自己里面")
    if any(len(seg) > 255 for seg in dst.split("/")):
        raise ApiError("名字太长了")
    msg = str(p.get("msg") or "").strip()[:500] or f"重命名 {src} → {dst}"
    r = await asyncio.to_thread(_gh_mv_sync, repo, src, dst, msg)
    print("GitHub 重命名：", repo, src, "→", dst, r["n"], "个", r["sha"][:7])
    return r


# ---------------------------------------------------------------- 文档（随手记：每篇文档分次添加的文字，点一下就能复制）
# 每个人各存各的（按 Telegram 用户 ID 分开），保存在 nav_docs.json（权限 600，里面可能有密码之类的内容）。

DOCS_FILE = _data_path("nav_docs.json")
DOCS_MAX = 300              # 每人最多 300 篇
DOCS_ITEMS_MAX = 500        # 每篇最多 500 条
DOCS_TEXT_MAX = 20000       # 每条最多 2 万字
DOCS_LOCK = threading.Lock()


def _docs_load():
    try:
        with open(DOCS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _docs_save(d):
    tmp = DOCS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, DOCS_FILE)


def _docs_out(docs):
    return {"docs": docs}


@action("docs", admin=True)
async def a_docs(ctx, p):
    with DOCS_LOCK:
        return _docs_out(_docs_load().get(str(ctx["uid"]), []))


@action("docs_edit", admin=True)
async def a_docs_edit(ctx, p):
    op = str(p.get("op") or "")
    now = int(time.time())

    def title_of(v):
        t = str(v or "").strip()
        if not t:
            raise ApiError("标题不能为空")
        if len(t) > 60:
            raise ApiError("标题太长了，最多 60 个字")
        return t

    def label_of(v):                       # 每条的小标题（可选），复制时不带它
        t = str(v or "").strip()
        if len(t) > 40:
            raise ApiError("小标题太长了，最多 40 个字")
        return t

    def text_of(v):
        t = str(v or "").rstrip()
        if not t.strip():
            raise ApiError("内容不能为空")
        if len(t) > DOCS_TEXT_MAX:
            raise ApiError(f"内容太长了，每条最多 {DOCS_TEXT_MAX} 个字")
        return t

    with DOCS_LOCK:
        allx = _docs_load()
        docs = allx.setdefault(str(ctx["uid"]), [])

        def find(did):
            for d in docs:
                if d.get("id") == did:
                    return d
            raise ApiError("这篇文档不存在了，请刷新")

        def find_item(d, iid):
            for it in d.get("items", []):
                if it.get("id") == iid:
                    return it
            raise ApiError("这条内容不存在了，请刷新")

        if op == "add":
            if len(docs) >= DOCS_MAX:
                raise ApiError(f"最多 {DOCS_MAX} 篇文档")
            d = {"id": secrets.token_hex(6), "title": title_of(p.get("title")), "t": now, "items": []}
            first = str(p.get("text") or "").strip()
            if first:
                d["items"].append({"id": secrets.token_hex(6), "text": text_of(p.get("text")), "t": now})
            docs.insert(0, d)
        elif op == "ren":
            d = find(p.get("id"))
            d["title"] = title_of(p.get("title"))
            d["t"] = now
        elif op == "del":
            d = find(p.get("id"))
            docs.remove(d)
        elif op == "top":                      # 移到最上面
            d = find(p.get("id"))
            docs.remove(d)
            docs.insert(0, d)
        elif op == "item_add":
            d = find(p.get("id"))
            if len(d.setdefault("items", [])) >= DOCS_ITEMS_MAX:
                raise ApiError(f"每篇最多 {DOCS_ITEMS_MAX} 条")
            it = {"id": secrets.token_hex(6), "text": text_of(p.get("text")), "t": now}
            if label_of(p.get("label")):
                it["label"] = label_of(p.get("label"))
            d["items"].append(it)
            d["t"] = now
        elif op == "item_edit":
            d = find(p.get("id"))
            it = find_item(d, p.get("iid"))
            it["text"] = text_of(p.get("text"))
            if "label" in p:
                lb = label_of(p.get("label"))
                if lb:
                    it["label"] = lb
                else:
                    it.pop("label", None)
            it["t"] = d["t"] = now
        elif op == "item_del":
            d = find(p.get("id"))
            d["items"].remove(find_item(d, p.get("iid")))
            d["t"] = now
        else:
            raise ApiError("未知操作")
        _docs_save(allx)
        return _docs_out(docs)


# ---------------------------------------------------------------- 远程服务器文件管理（SFTP，只有所有者能用）
# 在 App 里保存服务器的 IP / 端口 / 用户名 + 密码或私钥，然后像文件管理器一样浏览目录、上传 / 下载 / 编辑 / 新建文件夹 / 重命名 / 删除。
# 密码和私钥只存在服务器上（nav_ssh.json，权限 600），接口永远不会把它们再发回手机。
# 第一次连接会记住服务器的指纹，以后指纹变了会拒绝连接（防中间人），确实重装了服务器就编辑一下清掉指纹。
# 依赖：paramiko（apt install -y python3-paramiko，或 pip3 install paramiko --break-system-packages）。

import posixpath
import stat as _stat

SSH_FILE = _data_path("nav_ssh.json")
SSH_LOCK = threading.Lock()         # 保护连接池（连接时会持有，最长十几秒）
SSH_CFG = threading.Lock()          # 保护 nav_ssh.json 的读写（很快）
SSH_MAX = 200                       # 最多保存多少台
SSH_UP_CHUNK = 2 * 1024 * 1024      # 上传每块（base64 后约 2.7MB；nginx 的 client_max_body_size 和 PHP 的 post_max_size 要不小于 4MB）
SSH_DL_CHUNK = 4 * 1024 * 1024      # 下载每块（响应不受请求大小限制）
SSH_EDIT_MAX = 40 * 1024            # 在线编辑最大 40KB（请求体上限 64KB）
SSH_VIEW_MAX = 512 * 1024           # 在线查看最大 512KB
SSH_KEEP = 3600                     # 下载任务 1 小时没动静就清掉
SSH_KEEP_UP = 6 * 3600              # 上传任务保留 6 小时（中断后可以续传）
SSH_IDLE = 90                       # 连接空闲 90 秒后断开
SSH_POOL = {}                       # 服务器 id → {"cli", "sftp", "t"}
SSH_JOBS = {}                       # 进行中的上传 / 下载任务（只在内存里）
SSH_JOBS_LOCK = threading.Lock()

OWNER_ONLY.update({"ssh", "ssh_edit", "ssh_ls", "ssh_read", "ssh_write", "ssh_mkdir", "ssh_rm", "ssh_mv",
                   "ssh_up_begin", "ssh_up_chunk", "ssh_up_commit", "ssh_up_cancel", "ssh_up_state",
                   "ssh_dl_begin", "ssh_dl_chunk", "ssh_ping", "r2_dl_begin", "r2_dl_chunk"})


def _ssh_pm():
    try:
        import paramiko
        return paramiko
    except Exception:
        raise ApiError("服务器上还没有安装 paramiko。在服务器执行：apt install -y python3-paramiko 或 pip3 install paramiko --break-system-packages，然后 systemctl restart okbot", 400)


def _ssh_load():
    try:
        with open(SSH_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _ssh_save(d):
    tmp = SSH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SSH_FILE)


def _ssh_servers():
    s = _ssh_load().get("servers")
    return s if isinstance(s, list) else []


def _ssh_get(sid):
    for s in _ssh_servers():
        if s.get("id") == str(sid or ""):
            return s
    raise ApiError("找不到这台服务器，可能已经被删除", 404)


def _ssh_pub(s):
    return {"id": s["id"], "name": s.get("name") or s["host"], "host": s["host"], "port": s.get("port") or 22,
            "user": s.get("user") or "root", "auth": s.get("auth") or "password", "dir": s.get("dir") or "",
            "has_pw": bool(s.get("password")), "has_key": bool(s.get("key")), "fp": s.get("fp") or "", "t": s.get("t") or 0}


def _ssh_close(sid):
    ent = SSH_POOL.pop(sid, None)
    if ent:
        try:
            ent["cli"].close()
        except Exception:
            pass


def _ssh_load_key(pm, text, pw):
    import io
    last = None
    for cls in (pm.Ed25519Key, pm.RSAKey, pm.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(text), password=pw or None)
        except pm.PasswordRequiredException:
            raise ApiError("这个私钥有密码，请在「私钥密码」里填上", 400)
        except Exception as e:
            last = e
    raise ApiError("私钥格式不对或私钥密码错误（需要 OpenSSH / PEM 格式）：" + str(last)[:60], 400)


def _ssh_connect(s):
    """连接服务器，返回 (client, sftp)。同一台服务器的连接会复用，空闲 90 秒后断开。"""
    pm = _ssh_pm()
    sid = s["id"]
    with SSH_LOCK:
        ent = SSH_POOL.get(sid)
        if ent:
            tr = ent["cli"].get_transport()
            if tr is not None and tr.is_active():
                ent["t"] = time.time()
                return ent["cli"], ent["sftp"]
            _ssh_close(sid)
        known = s.get("fp") or ""
        seen = {}

        class Policy(pm.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                fp = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
                seen["fp"] = fp
                if known and fp != known:
                    raise pm.SSHException("HOSTKEY_CHANGED")

        cli = pm.SSHClient()
        cli.set_missing_host_key_policy(Policy())
        kw = dict(hostname=s["host"], port=int(s.get("port") or 22), username=s.get("user") or "root",
                  timeout=12, banner_timeout=15, auth_timeout=15, allow_agent=False, look_for_keys=False)
        if (s.get("auth") or "password") == "key":
            if not s.get("key"):
                raise ApiError("这台服务器还没有保存私钥，请先编辑填上", 400)
            kw["pkey"] = _ssh_load_key(pm, s["key"], s.get("passphrase") or "")
        else:
            if not s.get("password"):
                raise ApiError("这台服务器还没有保存密码，请先编辑填上", 400)
            kw["password"] = s["password"]
        try:
            cli.connect(**kw)
            sftp = cli.open_sftp()
            sftp.get_channel().settimeout(30)
        except pm.AuthenticationException:
            cli.close()
            raise ApiError("登录失败：用户名、密码或私钥不对（也可能服务器禁用了密码登录）", 400)
        except pm.SSHException as e:
            cli.close()
            if "HOSTKEY_CHANGED" in str(e):
                raise ApiError("服务器指纹和上次不一样！可能服务器重装过，也可能被人冒充。确认无误后，编辑这台服务器并点「清除指纹」", 400)
            raise ApiError("SSH 连接失败：" + str(e)[:100], 502)
        except Exception as e:
            cli.close()
            raise ApiError("连接不上 %s:%s：%s" % (s["host"], s.get("port") or 22, (str(e) or e.__class__.__name__)[:80]), 502)
        if not known and seen.get("fp"):                       # 第一次连接：记住指纹
            with SSH_CFG:
                d = _ssh_load()
                for x in d.get("servers", []):
                    if x.get("id") == sid:
                        x["fp"] = seen["fp"]
                        s["fp"] = seen["fp"]
                _ssh_save(d)
        SSH_POOL[sid] = {"cli": cli, "sftp": sftp, "t": time.time()}
        return cli, sftp


def _ssh_err(e, what=""):
    """把 SFTP / 网络错误翻成人话。"""
    import errno
    if isinstance(e, ApiError):
        return e
    no = getattr(e, "errno", None)
    if no == errno.ENOENT or isinstance(e, FileNotFoundError):
        return ApiError("找不到：" + what, 404)
    if no == errno.EACCES or isinstance(e, PermissionError):
        return ApiError("没有权限：" + what, 400)
    if no == errno.EEXIST:
        return ApiError("已经存在：" + what, 400)
    if isinstance(e, (EOFError, ConnectionError, TimeoutError)) or "closed" in str(e).lower() or "timed out" in str(e).lower():
        return ApiError("连接中断了，再点一次试试", 502)
    return ApiError((what + "：" if what else "") + (str(e) or e.__class__.__name__)[:100], 400)


SSH_SLOCKS = {}                     # 服务器 id → 这条 SFTP 连接的锁
SSH_SLOCKS_LOCK = threading.Lock()


def _ssh_slock(sid):
    """同一条 SFTP 连接同一时间只能有一个线程在收发：paramiko 的 SFTPClient 多线程同时用会互相「抢走」对方的回应，
    结果就是请求永远等不到回应、整条连接卡死（上传 / 下载并发时就是这样卡住的）。可重入：同一线程里嵌套用没问题。"""
    with SSH_SLOCKS_LOCK:
        lk = SSH_SLOCKS.get(sid)
        if lk is None:
            lk = SSH_SLOCKS[sid] = threading.RLock()
        return lk


def _ssh_run(sid, fn, what=""):
    """取连接并执行 fn(sftp)。连接断了就丢掉重连一次。"""
    s = _ssh_get(sid)
    for attempt in (0, 1):
        try:
            _cli, sftp = _ssh_connect(s)
            with _ssh_slock(sid):
                return fn(sftp)
        except ApiError:
            raise
        except (EOFError, ConnectionError, OSError, Exception) as e:
            if attempt == 0 and (isinstance(e, (EOFError, ConnectionError)) or "closed" in str(e).lower() or "Socket" in str(e) or "timed out" in str(e).lower()):
                _ssh_close(sid)
                continue
            raise _ssh_err(e, what)


def _ssh_path(path):
    path = str(path or "").strip()
    if "\x00" in path or len(path) > 2000:
        raise ApiError("路径不正确")
    if not path.startswith("/"):
        raise ApiError("路径要以 / 开头")
    return posixpath.normpath(path) if path != "/" else "/"


def _ssh_name(name):
    name = str(name or "").strip()
    if not name or "/" in name or name in (".", "..") or len(name) > 250 or re.search(r"[\x00-\x1f]", name):
        raise ApiError("名字不正确：" + name[:40])
    return name


def _ssh_mode(m):
    return "".join(("r" if m & (1 << (8 - i)) and i % 3 == 0 else "w" if m & (1 << (8 - i)) and i % 3 == 1 else "x" if m & (1 << (8 - i)) else "-") for i in range(9))


@action("ssh", admin=True)
async def a_ssh(ctx, p):
    try:
        import paramiko  # noqa: F401
        ok = True
    except Exception:
        ok = False
    return {"servers": [_ssh_pub(s) for s in _ssh_servers()], "paramiko": ok}


@action("ssh_ping", admin=True)
async def a_ssh_ping(ctx, p):
    """各台服务器的 SSH 端口通不通（只试 TCP 连接，3 秒超时，不登录）。"""
    import socket

    def one(s):
        try:
            socket.create_connection((s["host"], int(s.get("port") or 22)), timeout=3).close()
            return True
        except Exception:
            return False
    lst = _ssh_servers()
    res = await asyncio.gather(*[asyncio.to_thread(one, s) for s in lst])
    return {"up": {s["id"]: ok for s, ok in zip(lst, res)}}


@action("ssh_edit", admin=True)
async def a_ssh_edit(ctx, p):
    op = str(p.get("op") or "")
    with SSH_CFG:
        d = _ssh_load()
        lst = d.setdefault("servers", [])
        if not isinstance(lst, list):
            lst = d["servers"] = []
        if op == "del":
            sid = str(p.get("id") or "")
            d["servers"] = [x for x in lst if x.get("id") != sid]
            _ssh_save(d)
            _ssh_close(sid)
            return {"servers": [_ssh_pub(s) for s in d["servers"]]}
        if op not in ("add", "edit"):
            raise ApiError("操作不正确")
        host = str(p.get("host") or "").strip()
        if not host or len(host) > 253 or re.search(r"[\s/\\@]", host):
            raise ApiError("服务器地址不正确（填 IP 或域名）")
        try:
            port = int(p.get("port") or 22)
        except Exception:
            port = 0
        if not 1 <= port <= 65535:
            raise ApiError("端口不正确")
        user = str(p.get("user") or "root").strip()
        if not user or len(user) > 64 or re.search(r"[\s\x00]", user):
            raise ApiError("用户名不正确")
        auth = "key" if p.get("auth") == "key" else "password"
        start = str(p.get("dir") or "").strip()
        if start:
            start = _ssh_path(start)
        if op == "add":
            if len(lst) >= SSH_MAX:
                raise ApiError("服务器太多了")
            s = {"id": secrets.token_hex(6), "t": int(time.time())}
            lst.insert(0, s)
        else:
            s = next((x for x in lst if x.get("id") == str(p.get("id") or "")), None)
            if not s:
                raise ApiError("找不到这台服务器", 404)
        changed = (s.get("host"), s.get("port"), s.get("user")) != (host, port, user) and op == "edit"
        s.update({"name": str(p.get("name") or "").strip()[:40] or host, "host": host, "port": port, "user": user, "auth": auth, "dir": start})
        if p.get("password"):
            s["password"] = str(p["password"])[:500]
        if p.get("key"):
            k = str(p["key"]).strip() + "\n"
            if "PRIVATE KEY" not in k or len(k) > 20000:
                raise ApiError("私钥格式不对，要粘贴整段（含 BEGIN / END 那两行）")
            s["key"] = k
        if p.get("passphrase"):
            s["passphrase"] = str(p["passphrase"])[:500]
        if p.get("clear_fp") or changed:
            s["fp"] = ""
        if auth == "password" and not s.get("password"):
            raise ApiError("请填写密码")
        if auth == "key" and not s.get("key"):
            raise ApiError("请粘贴私钥")
        _ssh_save(d)
        _ssh_close(s["id"])
        return {"servers": [_ssh_pub(x) for x in lst], "id": s["id"]}


def _ssh_mkdirs(sftp, path):
    """像 mkdir -p 一样把目录一层层建出来。"""
    cur = ""
    for seg in [x for x in path.split("/") if x]:
        cur += "/" + seg
        try:
            if _stat.S_ISDIR(sftp.stat(cur).st_mode or 0):
                continue
        except Exception:
            pass
        sftp.mkdir(cur)


@action("ssh_ls", admin=True)
async def a_ssh_ls(ctx, p):
    sid = p.get("id")

    def go(sftp):
        path = str(p.get("path") or "").strip()
        preset = ""
        if not path:
            preset = _ssh_get(sid).get("dir") or ""
            path = preset or sftp.normalize(".")
        path = _ssh_path(path)
        try:
            attrs = sftp.listdir_attr(path)
        except Exception as e:
            if preset and getattr(e, "errno", None) == 2:       # 自己设置的默认目录还不存在：自动创建
                _ssh_mkdirs(sftp, path)
                attrs = sftp.listdir_attr(path)
            elif path != "/" and not p.get("path"):
                path = "/"
                attrs = sftp.listdir_attr(path)
            else:
                raise
        items = []
        for a in attrs:
            m = a.st_mode or 0
            isdir, link = _stat.S_ISDIR(m), _stat.S_ISLNK(m)
            if link:
                try:
                    isdir = _stat.S_ISDIR(sftp.stat(posixpath.join(path, a.filename)).st_mode)
                except Exception:
                    pass
            items.append({"name": a.filename, "dir": isdir, "link": link, "size": a.st_size or 0,
                          "mtime": a.st_mtime or 0, "mode": _ssh_mode(m)})
        items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
        return {"path": path, "items": items[:3000], "more": len(items) > 3000}
    return await asyncio.to_thread(_ssh_run, sid, go, str(p.get("path") or "目录"))


@action("ssh_read", admin=True)
async def a_ssh_read(ctx, p):
    sid, path = p.get("id"), _ssh_path(p.get("path"))

    def go(sftp):
        st = sftp.stat(path)
        if _stat.S_ISDIR(st.st_mode):
            raise ApiError("这是个文件夹")
        size = st.st_size or 0
        if size > SSH_VIEW_MAX:
            return {"path": path, "size": size, "big": True}
        with sftp.open(path, "rb") as f:
            b = f.read(SSH_VIEW_MAX + 1)
        if b"\x00" in b[:4096]:
            return {"path": path, "size": size, "binary": True}
        try:
            text = b.decode("utf-8")
        except UnicodeDecodeError:
            return {"path": path, "size": size, "binary": True}
        return {"path": path, "size": size, "text": text, "editable": len(b) <= SSH_EDIT_MAX, "mtime": st.st_mtime or 0}
    return await asyncio.to_thread(_ssh_run, sid, go, path)


@action("ssh_write", admin=True)
async def a_ssh_write(ctx, p):
    sid, path = p.get("id"), _ssh_path(p.get("path"))
    text = p.get("text")
    if not isinstance(text, str):
        raise ApiError("没有内容")
    b = text.encode("utf-8")
    if len(b) > SSH_EDIT_MAX:
        raise ApiError("在线编辑最多 %dKB，太大的文件请下载修改后再上传覆盖" % (SSH_EDIT_MAX // 1024))

    def go(sftp):
        try:
            mode = sftp.stat(path).st_mode
        except Exception:
            mode = None
        tmp = path + ".nav-tmp"
        with sftp.open(tmp, "wb") as f:
            f.write(b)
        if mode is not None:
            try:
                sftp.chmod(tmp, _stat.S_IMODE(mode))
            except Exception:
                pass
        try:
            sftp.posix_rename(tmp, path)
        except Exception:
            try:
                sftp.remove(path)
            except Exception:
                pass
            sftp.rename(tmp, path)
        return {"ok": True, "size": len(b)}
    return await asyncio.to_thread(_ssh_run, sid, go, path)


@action("ssh_mkdir", admin=True)
async def a_ssh_mkdir(ctx, p):
    sid = p.get("id")
    path = posixpath.join(_ssh_path(p.get("dir")), _ssh_name(p.get("name")))

    def go(sftp):
        sftp.mkdir(path)
        return {"ok": True}
    return await asyncio.to_thread(_ssh_run, sid, go, path)


@action("ssh_mv", admin=True)
async def a_ssh_mv(ctx, p):
    sid = p.get("id")
    src = _ssh_path(p.get("path"))
    dst = posixpath.join(posixpath.dirname(src), _ssh_name(p.get("name"))) if not p.get("to") else _ssh_path(p.get("to"))
    if src == "/":
        raise ApiError("不能改根目录")

    def go(sftp):
        try:
            sftp.stat(dst)
            raise ApiError("目标位置已经有同名的了")
        except ApiError:
            raise
        except Exception:
            pass
        sftp.rename(src, dst)
        return {"ok": True}
    return await asyncio.to_thread(_ssh_run, sid, go, src)


def _ssh_rm_tree(sftp, path, depth=0, cnt=None):
    cnt = cnt if cnt is not None else [0]
    if depth > 30:
        raise ApiError("目录层级太深，请到服务器上删除")
    for a in sftp.listdir_attr(path):
        full = posixpath.join(path, a.filename)
        cnt[0] += 1
        if cnt[0] > 20000:
            raise ApiError("里面文件太多（超过 2 万个），请到服务器上删除")
        if _stat.S_ISDIR(a.st_mode or 0):
            _ssh_rm_tree(sftp, full, depth + 1, cnt)
        else:
            sftp.remove(full)
    sftp.rmdir(path)


@action("ssh_rm", admin=True)
async def a_ssh_rm(ctx, p):
    sid, path = p.get("id"), _ssh_path(p.get("path"))
    if path.count("/") < 2 or path in ("/", "/root", "/home", "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/boot", "/opt", "/proc", "/sys", "/dev"):
        raise ApiError("这个目录太重要了，不允许在这里删除")

    def go(sftp):
        st = sftp.lstat(path)
        if _stat.S_ISDIR(st.st_mode or 0):
            _ssh_rm_tree(sftp, path)
        else:
            sftp.remove(path)
        return {"ok": True}
    return await asyncio.to_thread(_ssh_run, sid, go, path)


# ---- 上传 / 下载：手机和这台服务器之间分块传（每次请求最大 64KB），这台服务器再和目标服务器之间用 SFTP 传。
# 两个方向都是「边传边写 / 边读边发」，不在这台服务器上存临时文件，所以文件多大都行（受手机内存和网速限制）。
# 上传：先在目标服务器建一个 .nav-part 临时文件，每块按位置直接写进去，全部到齐后一次改名成正式文件名。

def _ssh_clean():
    now = time.time()
    with SSH_JOBS_LOCK:
        old = [(k, v) for k, v in SSH_JOBS.items() if now - v["t"] > (SSH_KEEP_UP if v.get("kind") == "up" else SSH_KEEP)]
        for k, _v in old:
            SSH_JOBS.pop(k, None)
    for _k, v in old:
        if v.get("kind") == "up":                       # 没传完就放弃的上传：关句柄，把目标服务器上的临时文件删掉
            _ssh_up_drop(v)
    with SSH_LOCK:
        for sid, ent in list(SSH_POOL.items()):
            if now - ent["t"] > SSH_IDLE:
                _ssh_close(sid)


async def _ssh_clean_loop():
    while True:
        try:
            await asyncio.to_thread(_ssh_clean)
        except Exception as e:
            print("清理远程服务器临时任务出错：", e)
        await asyncio.sleep(60)


def _ssh_drain(fh):
    """等 pipelined 写入的确认全部回来（和 paramiko 自己等确认的写法一样）。
    注意不能用 fh.stat() 当「确认」：stat 会把还没读的写入确认顺手吞掉，paramiko 之后再去等这些确认就永远等不到，
    表现就是第一块传完后整个上传卡死。"""
    reqs = getattr(fh, "_reqs", None)
    sftp = getattr(fh, "sftp", None)
    if reqs is None or sftp is None:
        return
    from paramiko.sftp import CMD_STATUS
    while reqs:
        req = reqs.popleft()
        t, _msg = sftp._read_response(req)          # 写入失败时这里会直接抛错（paramiko 已经检查过状态）
        if t != CMD_STATUS:
            raise IOError("写入没有得到确认")


def _ssh_job(uid, job, kind):
    with SSH_JOBS_LOCK:
        j = SSH_JOBS.get(str(job or ""))
    if not j or j.get("uid") != uid or j.get("kind") != kind:
        raise ApiError("任务已过期，请重新操作")
    j["t"] = time.time()
    return j


def _ssh_up_drop(j, remove=True):
    """结束一个上传任务：关掉远程临时文件的句柄，需要的话把临时文件删掉。"""
    j["closed"] = True
    fh = j.get("fh")
    if fh is not None:
        try:
            with _ssh_slock(j["sid"]), j["lock"]:
                fh.close()
        except Exception:
            pass
        j["fh"] = None
    if remove:
        try:
            _ssh_run(j["sid"], lambda sf: sf.remove(j["tmp"]), "")
        except Exception:
            pass


@action("ssh_up_begin", admin=True)
async def a_ssh_up_begin(ctx, p):
    sid = p.get("id")
    path = posixpath.join(_ssh_path(p.get("dir")), _ssh_name(p.get("name")))
    try:
        size = int(p.get("size") or 0)
    except Exception:
        raise ApiError("文件大小不正确")
    if size < 0:
        raise ApiError("文件大小不正确")
    tmp = path + ".nav-part"

    def go(sftp):
        try:
            st = sftp.stat(path)
            if _stat.S_ISDIR(st.st_mode or 0):
                raise ApiError("目标位置有个同名的文件夹")
            if not p.get("overwrite"):
                return None
        except ApiError:
            raise
        except Exception:
            pass
        fh = sftp.open(tmp, "wb")                       # 临时文件的句柄整个上传期间一直开着，每块直接按位置写进去
        fh.set_pipelined(True)
        return fh
    fh = await asyncio.to_thread(_ssh_run, sid, go, path)
    if fh is None:
        return {"exists": True, "path": path}
    await asyncio.to_thread(_ssh_clean)
    job = secrets.token_hex(8)
    chunks = max(1, -(-size // SSH_UP_CHUNK))
    with SSH_JOBS_LOCK:
        SSH_JOBS[job] = {"uid": ctx["uid"], "kind": "up", "sid": sid, "path": path, "tmp": tmp, "size": size, "chunks": chunks,
                         "got": set(), "fh": fh, "lock": threading.Lock(), "t": time.time()}
    return {"job": job, "chunk": SSH_UP_CHUNK, "chunks": chunks, "path": path}


@action("ssh_up_chunk", admin=True)
async def a_ssh_up_chunk(ctx, p):
    j = _ssh_job(ctx["uid"], p.get("job"), "up")
    try:
        n = int(p.get("n"))
    except Exception:
        raise ApiError("分块编号不正确")
    if not 0 <= n < j["chunks"]:
        raise ApiError("分块编号不正确")
    try:
        data = base64.b64decode(str(p.get("data") or ""), validate=True)
    except Exception:
        raise ApiError("分块内容不正确")
    if len(data) > SSH_UP_CHUNK:
        raise ApiError("分块太大")

    def reopen():                                       # 连接断过 / 句柄失效：不清空地重新打开临时文件，接着写
        def op(sf):
            fh = sf.open(j["tmp"], "r+b")
            fh.set_pipelined(True)
            return fh
        j["fh"] = _ssh_run(j["sid"], op, j["path"])

    def go():
        with _ssh_slock(j["sid"]), j["lock"]:          # 先拿连接锁再拿任务锁（和完成上传时的顺序一致，不会互相等死）
            if j.get("closed"):
                raise ApiError("上传任务已结束，请重新上传")
            for attempt in (0, 1):
                try:
                    if j.get("fh") is None:
                        reopen()
                    fh = j["fh"]
                    fh.seek(n * SSH_UP_CHUNK)
                    fh.write(data)
                    fh.flush()
                    _ssh_drain(fh)                      # 等这一块的写入确认全部回来（中断后续传才可靠）
                    break
                except ApiError:
                    raise
                except Exception:
                    j["fh"] = None
                    if attempt:
                        raise
        ent = SSH_POOL.get(j["sid"])
        if ent:
            ent["t"] = time.time()                      # 上传期间不让连接因为空闲被断开
    try:
        await asyncio.to_thread(go)
    except ApiError:
        raise
    except Exception as e:
        raise _ssh_err(e, j["path"])
    j["got"].add(n)
    return {"ok": True}


@action("ssh_up_state", admin=True)
async def a_ssh_up_state(ctx, p):
    """续传用：这个上传任务还在不在、哪些块已经收到。"""
    j = _ssh_job(ctx["uid"], p.get("job"), "up")
    return {"chunks": j["chunks"], "chunk": SSH_UP_CHUNK, "size": j["size"], "path": j["path"], "got": sorted(j["got"])}


@action("ssh_up_commit", admin=True)
async def a_ssh_up_commit(ctx, p):
    j = _ssh_job(ctx["uid"], p.get("job"), "up")
    if j["size"] > 0 and len(j["got"]) < j["chunks"]:
        raise ApiError("文件没有传完整（%d/%d 块），请重新上传" % (len(j["got"]), j["chunks"]))

    def go(sftp):
        with j["lock"]:
            if j.get("fh") is not None:
                try:
                    j["fh"].close()                     # 等远程确认全部写完
                except Exception:
                    pass
                j["fh"] = None
        if (sftp.stat(j["tmp"]).st_size or 0) != j["size"]:
            raise ApiError("写入的大小对不上，请重新上传")
        try:
            sftp.posix_rename(j["tmp"], j["path"])
        except Exception:
            try:
                sftp.remove(j["path"])
            except Exception:
                pass
            sftp.rename(j["tmp"], j["path"])
    await asyncio.to_thread(_ssh_run, j["sid"], go, j["path"])
    with SSH_JOBS_LOCK:
        SSH_JOBS.pop(str(p.get("job")), None)
    return {"ok": True}


@action("ssh_up_cancel", admin=True)
async def a_ssh_up_cancel(ctx, p):
    with SSH_JOBS_LOCK:
        j = SSH_JOBS.pop(str(p.get("job") or ""), None)
    if j and j.get("uid") == ctx["uid"] and j.get("kind") == "up":
        await asyncio.to_thread(_ssh_up_drop, j)
    return {"ok": True}


@action("ssh_dl_begin", admin=True)
async def a_ssh_dl_begin(ctx, p):
    sid, path = p.get("id"), _ssh_path(p.get("path"))

    def go(sftp):
        st = sftp.stat(path)
        if _stat.S_ISDIR(st.st_mode or 0):
            raise ApiError("文件夹不能直接下载")
        return st.st_size or 0
    size = await asyncio.to_thread(_ssh_run, sid, go, path)
    await asyncio.to_thread(_ssh_clean)
    job = secrets.token_hex(8)
    with SSH_JOBS_LOCK:
        SSH_JOBS[job] = {"uid": ctx["uid"], "kind": "dl", "sid": sid, "path": path, "size": size, "t": time.time()}
    return {"job": job, "name": posixpath.basename(path) or "file", "size": size, "chunk": SSH_DL_CHUNK}


@action("ssh_dl_chunk", admin=True)
async def a_ssh_dl_chunk(ctx, p):
    j = _ssh_job(ctx["uid"], p.get("job"), "dl")
    if p.get("end"):                                    # 手机全部取完了：结束任务
        with SSH_JOBS_LOCK:
            SSH_JOBS.pop(str(p.get("job")), None)
        return {"ok": True}
    try:
        n = int(p.get("n"))
    except Exception:
        raise ApiError("分块编号不正确")
    if not 0 <= n * SSH_DL_CHUNK < max(1, j["size"]):
        raise ApiError("分块编号不正确")

    def go(sftp):
        with sftp.open(j["path"], "rb") as f:
            off = n * SSH_DL_CHUNK
            size = max(0, min(SSH_DL_CHUNK, j["size"] - off))
            if not size:
                return b""
            return b"".join(f.readv([(off, size)]))      # readv 一次发出全部读请求（流水线），比逐个 32KB 来回快得多
    b = await asyncio.to_thread(_ssh_run, j["sid"], go, j["path"])
    last = n * SSH_DL_CHUNK + len(b) >= j["size"]
    if last and p.get("done"):
        with SSH_JOBS_LOCK:
            SSH_JOBS.pop(str(p.get("job")), None)
    return {"data": base64.b64encode(b).decode(), "last": last}


# ---- R2 文件下载：和 SFTP 下载一样，手机分块从这台服务器取，不再跳外部链接
def _r2_http(method, url, rng=None):
    req = urllib.request.Request(url, method=method, headers={"Range": rng} if rng else {})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, dict(r.headers), (r.read() if method == "GET" else b"")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ApiError("文件不存在", 404)
        raise ApiError("从 R2 读取失败（%d）" % e.code, 502)
    except Exception as e:
        raise ApiError("连接不上 R2：" + str(getattr(e, "reason", e))[:80], 502)


@action("r2_dl_begin", admin=True)
async def a_r2_dl_begin(ctx, p):
    keys = _r2_need_keys()
    acct = await _r2_account()
    name, key = _r2_name(p), _r2_key(p)
    host, path = f"{acct['id']}.r2.cloudflarestorage.com", "/" + name + "/" + _s3_quote(key, safe="-_.~/")
    _st, hdr, _b = await asyncio.to_thread(_r2_http, "HEAD", _s3_presign("HEAD", host, path, keys))
    size = int({k.lower(): v for k, v in hdr.items()}.get("content-length") or 0)
    await asyncio.to_thread(_ssh_clean)
    job = secrets.token_hex(8)
    with SSH_JOBS_LOCK:
        SSH_JOBS[job] = {"uid": ctx["uid"], "kind": "r2dl", "host": host, "path": path, "size": size, "t": time.time()}
    return {"job": job, "name": key.rsplit("/", 1)[-1], "size": size, "chunk": SSH_DL_CHUNK}


@action("r2_dl_chunk", admin=True)
async def a_r2_dl_chunk(ctx, p):
    j = _ssh_job(ctx["uid"], p.get("job"), "r2dl")
    if p.get("end"):
        with SSH_JOBS_LOCK:
            SSH_JOBS.pop(str(p.get("job")), None)
        return {"ok": True}
    try:
        n = int(p.get("n"))
    except Exception:
        raise ApiError("分块编号不正确")
    if not 0 <= n * SSH_DL_CHUNK < max(1, j["size"]):
        raise ApiError("分块编号不正确")
    a = n * SSH_DL_CHUNK
    b2 = min(j["size"], a + SSH_DL_CHUNK) - 1
    url = _s3_presign("GET", j["host"], j["path"], _r2_need_keys())      # 每块重新签名，下载再久也不会过期
    _st, _h, data = await asyncio.to_thread(_r2_http, "GET", url, "bytes=%d-%d" % (a, b2))
    return {"data": base64.b64encode(data).decode(), "last": b2 + 1 >= j["size"]}


# ---------------------------------------------------------------- 安装代理（节点 agent + 订阅）
# 在「工具 → 安装代理」添加节点，得到一行安装命令；在节点服务器上执行后装一个小程序（web/px.sh），
# 它每 5 秒向 px.php 报到并领取任务（只向外发请求，不开任何端口）。之后在小程序里点安装 hy2 / anytls，
# 任务由节点上的 agent 执行。agent 与服务器之间用「KEY=VALUE」纯文本通信：服务器下发的值都先用正则校验，
# agent 收到后再校验一次，不会执行服务器发来的任意命令。订阅地址：web/px.php?s=订阅密钥（只有所有者能看到）。

PX_FILE = _data_path("nav_px.json")
PX_LOCK = threading.Lock()
PX_MAX = 50
PX_VER = 5                          # agent 最新版本（px.sh 里的 AGENT_VER 要一致）
PX_SH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "px.sh")   # agent 脚本放在机器人目录（网站目录被改也影响不到）
PX_KEY_FILE = _data_path("nav_px_sign.key")                                 # 给 agent 脚本签名的私钥（自动生成，只有 root 能读）
PX_ONLINE = 150                     # 超过 150 秒没报到 = 离线（agent 平时每 60 秒拉一次）
PX_IDLE = 10                        # 平时 agent 隔多少秒拉一次（短连接，几乎不占 CPU；任务最多等 10 秒领取）
PX_FAST = 3                         # 有人在看这个节点 / 有任务时加快到每 3 秒
PX_HOT_FOR = 300                    # 加快持续 5 分钟，没人看了再回到 60 秒
PX_HOT = {}                         # 节点 id → 加快到什么时候（只在内存里）
PX_BUSY = 900                       # 正在执行任务时放宽到 15 分钟
PX_LIVE = {}                        # 节点 id → {"ts", "ip", "st": {...}}（只在内存里）
PX_OPS = ("update", "restart_hy2", "restart_anytls", "uninstall_agent", "rm_hy2", "rm_anytls", "hy2", "anytls")
PX_ALNUM = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
PX_SNI_RE = re.compile(r"^(?=.{1,100}$)[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$")
PX_ADDR_RE = re.compile(r"^(?=.{1,100}$)[A-Za-z0-9.\-:]+$")

OWNER_ONLY.update({"px", "px_domain", "px_check", "px_cf", "px_node", "px_node_edit", "px_install", "px_remove", "px_op", "px_sub", "px_links"})


def _px_load():
    try:
        with open(PX_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _px_save(d):
    tmp = PX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PX_FILE)


def _px_rand(n=16):
    return "".join(secrets.choice(PX_ALNUM) for _ in range(n))


def _px_web():
    """安装命令 / 订阅地址用的网址：优先用小程序里设置的域名，其次 nav_miniapp.json 的 px_url，最后是 web_url。"""
    web = (_px_load().get("domain") or CONF.get("px_url") or CONF.get("web_url") or "").strip().rstrip("/")
    if not web.startswith("https://"):
        raise ApiError("还没有设置域名：先在「安装代理」页面点「设置域名」")
    return web


def _px_data():
    d = _px_load()
    d.setdefault("nodes", [])
    if not d.get("sub"):
        d["sub"] = _px_rand(24)
        _px_save(d)
    return d


def _px_node_of(d, nid):
    for n in d["nodes"]:
        if n["id"] == str(nid or ""):
            return n
    raise ApiError("节点不存在", 404)


def _px_online(n):
    live = PX_LIVE.get(n["id"])
    if not live:
        return False
    age = time.time() - live["ts"]
    busy = any(j["state"] == "run" for j in n.get("jobs", []))
    return age < (PX_BUSY if busy else PX_ONLINE)


def _px_addr(n):
    return n.get("addr") or (PX_LIVE.get(n["id"]) or {}).get("ip") or n.get("ip") or ""


def _px_view(n, full=False):
    live = PX_LIVE.get(n["id"]) or {}
    st = live.get("st") or n.get("st") or {}
    out = {"id": n["id"], "name": n["name"], "addr": n.get("addr") or "", "ip": live.get("ip") or n.get("ip") or "",
           "online": _px_online(n), "seen": bool(live or n.get("seen")),
           "host": n.get("host") or "", "os": n.get("os") or "", "ver": n.get("ver") or 0, "new_ver": PX_VER,
           "hy2": _px_proto_view(n.get("hy2"), st.get("hy2")),
           "anytls": _px_proto_view(n.get("anytls"), st.get("anytls")),
           "busy": any(j["state"] in ("queue", "run") for j in n.get("jobs", [])),
           "deleting": bool(n.get("del"))}
    if full:
        out["cmd"] = _px_cmd(n)
        out["jobs"] = [dict(j) for j in reversed(n.get("jobs", []))][:8]
    return out


def _px_proto_view(cfg, svc):
    if not cfg:
        return None
    v = {"port": cfg["port"], "sni": cfg["sni"], "svc": svc or "unknown", "domain": cfg.get("domain") or ""}
    if "obfs" in cfg:
        v["obfs"] = bool(cfg["obfs"])
        v["hop"] = cfg.get("hop") or ""
    return v


def _px_cmd(n):
    web = _px_web()
    return f"curl -fsSL '{web}/px.php?a=sh' | bash -s -- {web} {n['token']}"


def _px_job(n, op, **args):
    j = {"id": _px_rand(10), "op": op, "state": "queue", "t": int(time.time()), "args": args, "log": ""}
    n.setdefault("jobs", []).append(j)
    n["jobs"] = n["jobs"][-12:]
    PX_HOT[n["id"]] = time.time() + PX_HOT_FOR            # 有任务：让 agent 加快拉取
    return j


def _px_busy(n):
    if any(j["state"] in ("queue", "run") for j in n.get("jobs", [])):
        raise ApiError("这个节点还有任务在执行，请等它完成", 400)


def _px_port(v, default, name):
    try:
        p = int(str(v or "").strip() or default)
    except ValueError:
        raise ApiError(f"{name}端口必须是数字", 400)
    if not 1 <= p <= 65535:
        raise ApiError(f"{name}端口要在 1–65535 之间", 400)
    return p


def _px_sni(v, default):
    s = str(v or "").strip().lower() or default
    if not PX_SNI_RE.match(s):
        raise ApiError("伪装域名（SNI）格式不对，比如 cdn.jsdelivr.net", 400)
    return s


@action("px")
async def a_px(ctx, p):
    d = _px_data()
    try:
        web = _px_web()
    except ApiError:
        web = ""
    return {"web": bool(web), "domain": web, "custom": d.get("domain") or "", "nodes": [_px_view(n) for n in d["nodes"]],
            "sub": _px_sub_urls(web, d["sub"]) if web else None}


@action("px_domain")
async def a_px_domain(ctx, p):
    v = str(p.get("url") or "").strip().rstrip("/")
    if v and not re.fullmatch(r"https://[A-Za-z0-9]([A-Za-z0-9.-]{0,98}[A-Za-z0-9])?(:\d{1,5})?", v):
        raise ApiError("格式不对：要以 https:// 开头，只写域名，比如 https://sub.example.com", 400)
    with PX_LOCK:
        d = _px_data()
        if v:
            d["domain"] = v
        else:
            d.pop("domain", None)
        _px_save(d)
    out = await a_px(ctx, {})
    try:                                      # 顺手给新域名加 Cloudflare 放行规则（失败不影响保存）
        out["cf"] = await _px_cf_allow(ctx)
    except ApiError as e:
        out["cf"] = "err:" + str(e)[:120]
    return out


def _px_sub_urls(web, tok):
    b = f"{web}/px.php?s={tok}"
    return {"auto": b, "clash": b + "&f=clash", "singbox": b + "&f=sb", "links": b + "&f=b64", "token": tok}


def _px_fetch(url, ua):
    """从服务器自己去请求一遍（和手机客户端走同一条路：域名 → Cloudflare → nginx → PHP）。返回 (状态码, 响应头, 内容前 4000 字节)。"""
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status, dict((k.lower(), v) for k, v in r.headers.items()), r.read(4000)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4000)
        except Exception:
            body = b""
        return e.code, dict((k.lower(), v) for k, v in (e.headers or {}).items()), body
    except Exception as e:
        return 0, {}, str(e)[:160].encode()


def _px_check_sync(web, tok):
    out = []
    for label, path, ua in (("订阅（小火箭）", f"/px.php?s={tok}&f=b64", "Shadowrocket/2000 CFNetwork Darwin"),
                            ("订阅（Clash）", f"/px.php?s={tok}&f=clash", "clash.meta"),
                            ("安装脚本", "/px.php?a=sh", "curl/8")):
        code, h, body = _px_fetch(web + path, ua)
        r = {"name": label, "code": code, "ok": False, "note": ""}
        srv = (h.get("server") or "").lower()
        text = body.decode("utf-8", "replace")
        if code == 0:
            r["note"] = "连不上：" + text + "。检查域名解析、证书、nginx 有没有这个站点"
        elif code == 200:
            if "a=sh" in path:
                r["ok"] = text.startswith("#!/usr/bin/env bash") and bool(h.get("x-px-sig"))
                r["note"] = "正常" if r["ok"] else "返回的不是脚本内容（可能是别的站点 / 验证页）"
            elif "f=b64" in path:
                try:
                    links = base64.b64decode(text + "=" * (-len(text) % 4)).decode().split("\n")
                    links = [x for x in links if x.startswith(("hysteria2://", "anytls://"))]
                except Exception:
                    links = []
                r["ok"] = bool(links)
                r["note"] = f"正常，{len(links)} 个节点" if links else "返回 200 但里面没有节点链接（节点还没装好 / 没有地址 / 内容被替换成验证页）"
            else:
                r["ok"] = "proxies:" in text
                r["note"] = "正常" if r["ok"] else "返回 200 但不是 Clash 配置"
        elif code in (403, 503) and ("cloudflare" in srv or "cf-ray" in h or "cf-mitigated" in h):
            r["note"] = "被 Cloudflare 拦了（5 秒盾 / 浏览器完整性检查 / WAF）：点下面「自动放行」给 /px.php 加跳过规则（Bot Fight Mode 在免费版里放行不了，要在 Cloudflare 里关掉）"
            r["cf"] = True
        elif code == 403:
            r["note"] = "403：订阅密钥不对，或被 nginx / WAF 拒绝"
        elif code == 404:
            r["note"] = "404：nginx 没有放行这个文件 / 站点目录不对"
        elif code >= 500:
            r["note"] = f"{code}：服务器出错（okbot 没运行 / PHP 报错 / config.php 里的 api_url 不对）"
        else:
            r["note"] = f"状态码 {code}"
        out.append(r)
    return out


@action("px_check")
async def a_px_check(ctx, p):
    with PX_LOCK:
        d = _px_data()
        web = _px_web()
        tok = d["sub"]
    return {"url": web, "items": await asyncio.to_thread(_px_check_sync, web, tok)}


@action("px_node")
async def a_px_node(ctx, p):
    with PX_LOCK:
        d = _px_data()
        n = _px_node_of(d, p.get("id"))
        PX_HOT[n["id"]] = time.time() + PX_HOT_FOR             # 有人在看这个节点：agent 加快拉取
        return _px_view(n, True)


@action("px_node_edit")
async def a_px_node_edit(ctx, p):
    op = str(p.get("op") or "")
    with PX_LOCK:
        d = _px_data()
        if op == "add":
            if len(d["nodes"]) >= PX_MAX:
                raise ApiError(f"最多 {PX_MAX} 个节点", 400)
            name = _txt(p.get("name"), 40).strip() or f"节点 {len(d['nodes']) + 1}"
            _px_web()
            n = {"id": _px_rand(8), "name": name, "token": _px_rand(32), "addr": "", "created": int(time.time()), "jobs": []}
            d["nodes"].append(n)
            PX_HOT[n["id"]] = time.time() + PX_HOT_FOR
            _px_save(d)
            return _px_view(n, True)
        n = _px_node_of(d, p.get("id"))
        if op == "ren":
            n["name"] = _txt(p.get("name"), 40).strip() or n["name"]
            addr = str(p.get("addr") or "").strip()
            if addr and not PX_ADDR_RE.match(addr):
                raise ApiError("节点地址格式不对（填 IP 或域名，不要带 http:// 和端口）", 400)
            n["addr"] = addr
            _px_save(d)
            return {"ok": True}
        if op == "token":                       # 重置安装密钥：旧 agent 立刻失效
            n["token"] = _px_rand(32)
            PX_LIVE.pop(n["id"], None)
            _px_save(d)
            return _px_view(n, True)
        if op == "del":
            if _px_online(n) and not p.get("force"):
                _px_busy(n)
                n["del"] = True                 # 先让 agent 卸载自己，完成后再从列表移除
                _px_job(n, "uninstall_agent")
                _px_save(d)
                return {"pending": True}
            d["nodes"] = [x for x in d["nodes"] if x is not n]
            PX_LIVE.pop(n["id"], None)
            _px_save(d)
            return {"pending": False}
    raise ApiError("不支持的操作", 400)


async def _px_dns_point(host, ip):
    """用 Cloudflare 把 host 解析到节点 IP（灰云，只做 DNS：申请证书要直连节点的 80 端口）。已有同名 A / AAAA 记录就改，没有就建。"""
    if not _cf_ready():
        raise ApiError("还没有配置 Cloudflare Token：先在「Cloudflare」里设置，才能自动解析域名和申请证书", 400)
    zone = next((z for z in await _cf_zones() if host == z["name"] or host.endswith("." + z["name"])), None)
    if not zone:
        raise ApiError("这个域名不在当前 Cloudflare Token 管理的域名里", 400)
    typ = "AAAA" if ":" in ip else "A"
    mine = {(urllib.parse.urlsplit(u).hostname or "").lower() for u in (_px_web(), CONF.get("web_url") or "", CONF.get("px_url") or "")}
    if host in mine:
        raise ApiError(f"{host} 是小程序 / 订阅在用的域名，不能指到节点上（会让订阅和节点程序都连不上）。换一个新的子域名，比如 us1.{zone['name']}", 400)
    base = f"/zones/{zone['id']}/dns_records"
    j = await _cf_call("GET", base, query={"name": host, "per_page": 20})
    same = [r for r in j.get("result") or [] if r.get("name") == host]
    tag = "安装代理（节点域名证书）"
    for r in same:
        if r.get("type") not in ("A", "AAAA") or r["type"] != typ:
            raise ApiError(f"{host} 已经有一条 {r.get('type')} 记录，不会去动它。换一个没用过的子域名", 400)
        if r.get("comment") != tag and r.get("content") != ip:       # 不是本功能建的、又指向别处：多半是在用的服务，不能覆盖
            raise ApiError(f"{host} 已经解析到 {r.get('content')}，不是这个节点，不会覆盖它。换一个没用过的子域名，比如 us1.{zone['name']}", 400)
    body = {"type": typ, "name": host, "content": ip, "ttl": 120, "proxied": False, "comment": tag}
    cur = next((r for r in same if r.get("type") == typ), None)
    if cur:
        if cur.get("content") != ip or cur.get("proxied"):
            await _cf_call("PATCH", f"{base}/{cur['id']}", body)
    else:
        await _cf_call("POST", base, body)


PX_ALLOW_DESC = "安装代理放行（订阅 / 安装脚本）"


async def _px_cf_allow(ctx):
    """给订阅域名加一条 Cloudflare 配置规则：/px.php 和 /px.sh 不做 5 秒盾 / 浏览器完整性检查，手机客户端和节点才拿得到内容。
    返回 "ok" / "none"（域名不在 Cloudflare 里或没配 Token）。"""
    host = (urllib.parse.urlsplit(_px_web()).hostname or "").lower()
    if not host or not _cf_ready():
        return "none"
    zone = next((z for z in await _cf_zones() if host == z["name"] or host.endswith("." + z["name"])), None)
    if not zone:
        return "none"
    phase = "http_config_settings"
    base = f"/zones/{zone['id']}/rulesets"
    try:
        j = await _cf_call("GET", f"{base}/phases/{phase}/entrypoint", need=CF_NEED_CFG)
        r = j.get("result") or {}
    except CfNotFound:
        r = None
    expr = f'(http.host eq {_q(host)} and http.request.uri.path in {{"/px.php" "/px.sh"}})'
    rule = {"description": PX_ALLOW_DESC, "expression": expr, "action": "set_config",
            "action_parameters": {"security_level": "essentially_off", "bic": False}, "enabled": True}
    old = next((x for x in (r or {}).get("rules") or [] if x.get("description") == PX_ALLOW_DESC), None)
    if r is None:
        await _cf_call("PUT", f"{base}/phases/{phase}/entrypoint", {"rules": [rule]}, need=CF_NEED_CFG)
    elif old:
        if old.get("expression") == expr and old.get("enabled", True):
            return "ok"
        await _cf_call("PATCH", f"{base}/{r['id']}/rules/{old['id']}", rule, need=CF_NEED_CFG)
    else:
        await _cf_call("POST", f"{base}/{r['id']}/rules", rule, need=CF_NEED_CFG)
    _cf_audit(ctx, "px_allow", zone, old and old.get("expression"), expr)
    return "ok"


@action("px_cf")
async def a_px_cf(ctx, p):
    try:
        st = await _px_cf_allow(ctx)
    except ApiError as e:
        raise ApiError(str(e), 400)
    if st == "none":
        raise ApiError("这个域名不在 Cloudflare Token 管理的域名里（或还没配 Token），没法自动放行", 400)
    return {"ok": True}


@action("px_install")
async def a_px_install(ctx, p):
    proto = str(p.get("proto") or "")
    if proto not in ("hy2", "anytls"):
        raise ApiError("不支持的协议", 400)
    dom = str(p.get("domain") or "").strip().lower().rstrip(".")
    if dom:                                  # 域名证书：先把域名解析到节点（灰云），节点上再用 80 端口向 Let's Encrypt 申请
        if not PX_SNI_RE.match(dom):
            raise ApiError("域名格式不对，比如 us1.example.com", 400)
        with PX_LOCK:
            n0 = _px_node_of(_px_data(), p.get("id"))
            ip0 = (PX_LIVE.get(n0["id"]) or {}).get("ip") or n0.get("ip") or ""
            if not _px_online(n0):
                raise ApiError("节点不在线，请先在节点服务器执行安装命令", 400)
            if int(n0.get("ver") or 0) < 4:
                raise ApiError("节点程序太旧，不支持域名证书：先在节点页「⋯ → 更新节点程序」，等更新完再装", 400)
        if not ip0:
            raise ApiError("还不知道节点的 IP，等它报到一次再试", 400)
        await _px_dns_point(dom, ip0)
    with PX_LOCK:
        d = _px_data()
        n = _px_node_of(d, p.get("id"))
        if not _px_online(n):
            raise ApiError("节点不在线，请先在节点服务器执行安装命令", 400)
        _px_busy(n)
        old = n.get(proto) or {}
        if proto == "hy2":
            port = _px_port(p.get("port"), 443, "hy2 ")
            sni = _px_sni(p.get("sni"), "cdn.jsdelivr.net")
            hop = ""
            if p.get("hop"):
                m = re.fullmatch(r"(\d{1,5})-(\d{1,5})", str(p.get("hop_range") or "20000-30000").strip())
                if not m or not (1 <= int(m.group(1)) < int(m.group(2)) <= 65535):
                    raise ApiError("跳跃端口范围格式不对，比如 20000-30000", 400)
                if int(m.group(1)) <= port <= int(m.group(2)):
                    raise ApiError("跳跃范围不能包含 hy2 主端口", 400)
                if int(m.group(2)) - int(m.group(1)) < 10:
                    raise ApiError("跳跃范围太小，至少 10 个端口", 400)
                hop = f"{int(m.group(1))}-{int(m.group(2))}"
            cfg = {"port": port, "sni": sni, "pass": old.get("pass") or _px_rand(20),
                   "obfs": (old.get("obfs") or _px_rand(16)) if p.get("obfs") else "", "hop": hop}
        else:
            port = _px_port(p.get("port"), 8443, "anytls ")
            cfg = {"port": port, "sni": _px_sni(p.get("sni"), "cdn.jsdelivr.net"), "pass": old.get("pass") or _px_rand(20)}
        if dom:
            cfg["domain"] = dom
            cfg["sni"] = dom
            n["addr"] = dom                  # 订阅 / 导入链接用域名连接，才能和证书对上
        elif old.get("domain") and n.get("addr") == old["domain"]:
            n["addr"] = ""
        j = _px_job(n, proto, **cfg)
        j["cfg"] = cfg
        _px_save(d)
        return {"job": j["id"]}


@action("px_remove")
async def a_px_remove(ctx, p):
    proto = str(p.get("proto") or "")
    if proto not in ("hy2", "anytls"):
        raise ApiError("不支持的协议", 400)
    with PX_LOCK:
        d = _px_data()
        n = _px_node_of(d, p.get("id"))
        if not _px_online(n):
            raise ApiError("节点不在线", 400)
        _px_busy(n)
        _px_job(n, "rm_" + proto)
        _px_save(d)
    return {"ok": True}


@action("px_op")
async def a_px_op(ctx, p):
    op = str(p.get("op") or "")
    if op not in ("update", "restart_hy2", "restart_anytls"):
        raise ApiError("不支持的操作", 400)
    with PX_LOCK:
        d = _px_data()
        n = _px_node_of(d, p.get("id"))
        if not _px_online(n):
            raise ApiError("节点不在线", 400)
        _px_busy(n)
        _px_job(n, op)
        _px_save(d)
    return {"ok": True}


@action("px_sub")
async def a_px_sub(ctx, p):
    with PX_LOCK:
        d = _px_data()
        if str(p.get("op") or "") == "reset":
            d["sub"] = _px_rand(24)
            _px_save(d)
        return _px_sub_urls(_px_web(), d["sub"])


# ---- 导入链接 / 订阅内容

def _px_proxies(d, only=None):
    """所有已安装的协议 → 统一的节点列表（只收装好的，且有地址的）。"""
    out = []
    for n in d["nodes"]:
        if only and n["id"] != only:
            continue
        addr = _px_addr(n)
        if not addr:
            continue
        h = n.get("hy2")
        if h and h.get("ok"):
            out.append({"t": "hy2", "name": n["name"], "addr": addr, **h})
        a = n.get("anytls")
        if a and a.get("ok"):
            out.append({"t": "anytls", "name": n["name"], "addr": addr, **a})
    return out


def _px_host(addr):
    return f"[{addr}]" if ":" in addr and not addr.startswith("[") else addr


def _px_link(x):
    q = urllib.parse.quote
    host = _px_host(x["addr"])
    if x["t"] == "hy2":
        qs = [f"sni={q(x['sni'])}"] + ([] if x.get("domain") else ["insecure=1"])
        if x.get("obfs"):
            qs += ["obfs=salamander", f"obfs-password={q(x['obfs'])}"]
        if x.get("hop"):
            qs += [f"mport={x['hop']}", f"ports={x['hop']}"]         # mport：小火箭 / v2rayN；ports：官方写法
        return f"hysteria2://{q(x['pass'])}@{host}:{x['port']}/?{'&'.join(qs)}#{q(x['name'])}"
    return f"anytls://{q(x['pass'])}@{host}:{x['port']}/?sni={q(x['sni'])}{'' if x.get('domain') else '&insecure=1&allowInsecure=1'}#{q(x['name'])}"


def _px_yq(s):
    return json.dumps(str(s), ensure_ascii=False)       # JSON 字符串同时也是合法的 YAML 字符串


def _px_uniq(items):
    """Clash / sing-box 要求节点名不能重复：同名时才在后面补协议名（小火箭 / v2rayN 的链接用原名）。"""
    seen, cnt, out = set(), {}, []
    for x in items:
        cnt[x["name"]] = cnt.get(x["name"], 0) + 1
    for x in items:
        nm = x["name"] if cnt[x["name"]] == 1 else x["name"] + " " + ("hy2" if x["t"] == "hy2" else "anytls")
        base, i = nm, 2
        while nm in seen:
            nm, i = f"{base} {i}", i + 1
        seen.add(nm)
        out.append({**x, "name": nm})
    return out


def _px_clash(items):
    items = _px_uniq(items)
    L = ["mixed-port: 7890", "allow-lan: false", "mode: rule", "log-level: warning", "ipv6: false", "proxies:"]
    for x in items:
        L += ["  - name: " + _px_yq(x["name"]), "    server: " + _px_yq(x["addr"])]
        if x["t"] == "hy2":
            L += ["    type: hysteria2", f"    port: {x['port']}"]
            if x.get("hop"):
                L.append(f"    ports: {x['hop']}")
            L += ["    password: " + _px_yq(x["pass"]), "    sni: " + _px_yq(x["sni"]), "    skip-cert-verify: " + ("false" if x.get("domain") else "true"), "    udp: true"]
            if x.get("obfs"):
                L += ["    obfs: salamander", "    obfs-password: " + _px_yq(x["obfs"])]
        else:
            L += ["    type: anytls", f"    port: {x['port']}", "    password: " + _px_yq(x["pass"]), "    sni: " + _px_yq(x["sni"]),
                  "    skip-cert-verify: " + ("false" if x.get("domain") else "true"), "    udp: true", "    client-fingerprint: chrome",
                  "    idle-session-check-interval: 30", "    idle-session-timeout: 30", "    min-idle-session: 0"]
    names = [_px_yq(x["name"]) for x in items] or ["DIRECT"]
    L += ["proxy-groups:", '  - name: "节点选择"', "    type: select", "    proxies:", '      - "自动选择"']
    L += ["      - " + s for s in names]
    L += ["      - DIRECT", '  - name: "自动选择"', "    type: url-test", "    url: https://www.gstatic.com/generate_204",
          "    interval: 300", "    tolerance: 50", "    proxies:"]
    L += ["      - " + s for s in names]
    L += ["rules:", "  - GEOIP,LAN,DIRECT,no-resolve", "  - GEOIP,CN,DIRECT", '  - MATCH,节点选择', ""]
    return "\n".join(L)


def _px_singbox(items):
    items = _px_uniq(items)
    outs = []
    for x in items:
        tls = {"enabled": True, "server_name": x["sni"], "insecure": not x.get("domain")}
        if x["t"] == "hy2":
            o = {"type": "hysteria2", "tag": x["name"], "server": x["addr"], "server_port": x["port"], "password": x["pass"], "tls": tls}
            if x.get("hop"):
                o["server_ports"] = [x["hop"].replace("-", ":")]
            if x.get("obfs"):
                o["obfs"] = {"type": "salamander", "password": x["obfs"]}
        else:
            o = {"type": "anytls", "tag": x["name"], "server": x["addr"], "server_port": x["port"], "password": x["pass"], "tls": tls}
        outs.append(o)
    tags = [o["tag"] for o in outs]
    cfg = {
        "log": {"level": "warn"},
        "dns": {"servers": [{"type": "local", "tag": "local"}]},
        "inbounds": [{"type": "tun", "tag": "tun-in", "address": ["172.19.0.1/30"], "auto_route": True, "strict_route": True},
                     {"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": 2080}],
        "outbounds": [{"type": "selector", "tag": "proxy", "outbounds": ["auto"] + tags + ["direct"], "default": "auto"},
                      {"type": "urltest", "tag": "auto", "outbounds": tags or ["direct"], "url": "https://www.gstatic.com/generate_204", "interval": "5m"}]
                     + outs + [{"type": "direct", "tag": "direct"}],
        "route": {"auto_detect_interface": True, "default_domain_resolver": "local", "final": "proxy",
                  "rules": [{"action": "sniff"}, {"protocol": "dns", "action": "hijack-dns"}, {"ip_is_private": True, "outbound": "direct"}]},
    }
    return json.dumps(cfg, ensure_ascii=False, indent=1)


@action("px_links")
async def a_px_links(ctx, p):
    with PX_LOCK:
        d = _px_data()
        n = _px_node_of(d, p.get("id"))
        items = _px_proxies(d, n["id"])
        return {"links": [{"name": x["name"], "t": x["t"], "link": _px_link(x)} for x in items], "addr": _px_addr(n)}


# ---- agent 脚本签名：私钥只在机器人这里；节点装 agent 时把公钥一起带走，以后更新必须验签通过才替换。
#      这样就算网站目录（px.php）被人改了，也没法让节点执行别的脚本。
_PX_SIGNER = None


def _px_signer():
    global _PX_SIGNER
    if _PX_SIGNER:
        return _PX_SIGNER
    d = None
    try:
        with open(PX_KEY_FILE) as f:
            d = int(f.read().strip(), 16)
    except FileNotFoundError:
        pass
    if not d:
        d, _ = webpush.new_key()
        fd = os.open(PX_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("%064x\n" % d)
    pub = webpush._pub_bytes(webpush._mul(d, webpush._G))
    der = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200") + pub
    pem = "-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PUBLIC KEY-----\n"
    _PX_SIGNER = (d, base64.b64encode(pem.encode()).decode())
    return _PX_SIGNER


def _px_der_sig(rs):
    def i(b):
        b = b.lstrip(b"\0") or b"\0"
        if b[0] & 0x80:
            b = b"\0" + b
        return b"\x02" + bytes([len(b)]) + b
    body = i(rs[:32]) + i(rs[32:])
    return b"\x30" + bytes([len(body)]) + body


def px_sh_serve():
    """GET /pub/sh：agent 脚本（填入公钥）+ 签名头 X-Px-Sig。"""
    try:
        with open(PX_SH, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return 404, "echo '机器人目录里没有 px.sh'; exit 1\n", {}
    d, pub = _px_signer()
    text = re.sub(r'(?m)^PX_PUB=""', 'PX_PUB="%s"' % pub, text, count=1)
    sig = base64.b64encode(_px_der_sig(webpush._es256(d, text.encode()))).decode()
    return 200, text, {"X-Px-Sig": sig}


def px_sub_serve(headers, query):
    """GET /pub/sub：订阅内容。返回 (code, content_type, body, extra_headers)。"""
    q = urllib.parse.parse_qs(query)
    tok = (q.get("s") or [""])[0]
    with PX_LOCK:
        d = _px_data()
        if not (tok and hmac.compare_digest(tok, d["sub"])):
            return 403, "text/plain; charset=utf-8", "订阅密钥不正确", {}
        items = _px_proxies(d)
    ua = headers.get("x-px-ua", "").lower()
    f = (q.get("f") or [""])[0]
    if f not in ("clash", "sb", "b64"):
        if re.search(r"clash|mihomo|stash|verge|meta|flclash|clash-?x", ua):
            f = "clash"
        elif re.search(r"sing-?box|sfa|sfi|sfm|hiddify|karing", ua):
            f = "sb"
        else:
            f = "b64"
    exp = {"Profile-Update-Interval": "12", "Subscription-Userinfo": "upload=0; download=0; total=0; expire=0"}
    if f == "clash":
        return 200, "text/yaml; charset=utf-8", _px_clash(items), {**exp, "Content-Disposition": "attachment; filename*=UTF-8''proxy.yaml"}
    if f == "sb":
        return 200, "application/json; charset=utf-8", _px_singbox(items), {**exp, "Content-Disposition": "attachment; filename*=UTF-8''sing-box.json"}
    body = base64.b64encode("\n".join(_px_link(x) for x in items).encode()).decode()
    return 200, "text/plain; charset=utf-8", body, exp


# ---- agent 通信（纯文本 KEY=VALUE）

def _px_kv(raw):
    out = {}
    for line in raw.decode("utf-8", "replace").split("\n"):
        m = re.fullmatch(r"([A-Z][A-Z0-9_]{0,23})=(.{0,8000})", line.rstrip("\r"))
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _px_svc(v):
    return v if v in ("active", "inactive", "failed", "none", "activating") else "unknown"


def px_agent(headers, raw):
    """POST /pub/agent：agent 报到 + 领取任务 + 回报结果。返回 (code, 文本, 节点id)。
    """
    token = headers.get("x-px-token", "")
    ip = _txt(headers.get("x-px-ip", ""), 45)
    kv = _px_kv(raw)
    with PX_LOCK:
        d = _px_load()
        d.setdefault("nodes", [])
        n = None
        for x in d["nodes"]:                    # 逐个做恒定时间比较
            if hmac.compare_digest(x["token"], token):
                n = x
        if not n:
            return 403, "ERR=token\n", ""
        now = time.time()
        dirty = False
        if True:
            st = {"hy2": _px_svc(kv.get("S_HY2")), "anytls": _px_svc(kv.get("S_ANYTLS"))}
            PX_LIVE[n["id"]] = {"ts": now, "ip": ip, "st": st}
            for k, v in (("ip", ip), ("host", _txt(kv.get("HOST"), 60)), ("os", _txt(kv.get("OS"), 60)),
                         ("ver", int(kv["VER"]) if re.fullmatch(r"\d{1,3}", kv.get("VER", "")) else 0), ("st", st), ("seen", 1)):
                if n.get(k) != v:
                    n[k] = v
                    dirty = True
        # 回报任务结果
        jid = kv.get("JOB", "")
        if jid and kv.get("RC") is not None:
            job = next((j for j in n.get("jobs", []) if j["id"] == jid), None)
            if job and job["state"] != "ok" and job["state"] != "fail":
                ok = kv.get("RC") == "0"
                job["state"] = "ok" if ok else "fail"
                job["end"] = int(now)
                try:
                    job["log"] = base64.b64decode(kv.get("LOG", "") or "")[-3000:].decode("utf-8", "replace")
                except Exception:
                    job["log"] = ""
                op = job["op"]
                if ok and op in ("hy2", "anytls") and job.get("cfg"):
                    cfg = dict(job["cfg"])
                    cfg["ok"] = True
                    if kv.get("FP", "").strip():
                        cfg["fp"] = _txt(kv["FP"], 100)
                    n[op] = cfg
                elif ok and op in ("rm_hy2", "rm_anytls"):
                    n.pop(op[3:], None)
                elif ok and op == "uninstall_agent" and n.get("del"):
                    d["nodes"] = [x for x in d["nodes"] if x is not n]
                    PX_LIVE.pop(n["id"], None)
                    _px_save(d)
                    return 200, f"ACK={jid}\n", n["id"]
                dirty = True
            return_ack = f"ACK={jid}\n"
        else:
            return_ack = ""
        # 下发任务
        out = return_ack
        job = next((j for j in n.get("jobs", []) if j["state"] == "queue"), None)
        if job and not any(j["state"] == "run" for j in n.get("jobs", [])):
            job["state"] = "run"
            job["start"] = int(now)
            dirty = True
            a = job.get("args") or {}
            out += f"JOB={job['id']}\nOP={job['op']}\n"
            for key, name in (("port", "PORT"), ("pass", "PASS"), ("obfs", "OBFS"), ("sni", "SNI"), ("hop", "HOP"), ("domain", "DOMAIN")):
                if a.get(key) not in (None, ""):
                    out += f"{name}={a[key]}\n"
        if kv.get("VER", "").isdigit() and int(kv["VER"]) < PX_VER:
            out += "NEWVER=1\n"
        if dirty:
            try:
                _px_save(d)
            except Exception as e:
                print("保存代理节点出错：", e)
        out += "NEXT=%d\n" % (PX_FAST if PX_HOT.get(n["id"], 0) > now else PX_IDLE)
        return 200, out + "OK=1\n", n["id"]


def _px_gc():
    """卡在「执行中」超过 15 分钟的任务标为失败（agent 掉线或被杀）。"""
    now = time.time()
    with PX_LOCK:
        d = _px_load()
        ch = False
        for n in d.get("nodes", []):
            for j in n.get("jobs", []):
                if j["state"] in ("run", "queue") and now - j.get("start", j["t"]) > PX_BUSY:
                    j["state"] = "fail"
                    j["log"] = j.get("log") or "超时：节点没有回报结果（可能掉线了）"
                    ch = True
        if ch:
            _px_save(d)


async def _px_gc_loop():
    while True:
        try:
            await asyncio.to_thread(_px_gc)
        except Exception as e:
            print("清理代理任务出错：", e)
        await asyncio.sleep(60)


async def respond_text(writer, code, body, ctype="text/plain; charset=utf-8", extra=None):
    data = body.encode() if isinstance(body, str) else body
    reason = {200: "OK", 403: "Forbidden", 404: "Not Found"}.get(code, "OK")
    hdr = f"HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(data)}\r\nCache-Control: no-store\r\nConnection: close\r\n"
    for k, v in (extra or {}).items():
        hdr += k + ": " + re.sub(r"[\r\n]", "", str(v)) + "\r\n"
    writer.write((hdr + "\r\n").encode() + data)
    try:
        await writer.drain()
    except Exception:
        pass


# ---------------------------------------------------------------- 网页推送：服务器离线提醒
# 主屏幕 App 里点「开启推送」后，手机把推送订阅交给这里保存（nav_push.json）。
# 后台每 30 秒看一遍：哪台服务器离线满 PUSH_DOWN_AFTER 秒就推送一次，恢复在线再推送一次。
# 只推给服务器的主人（以前添加、没有记录主人的推给所有者）。加密和签名在 webpush.py，纯 Python，不用装库。

import webpush  # noqa: E402

PUSH_FILE = _data_path("nav_push.json")
PUSH_DOWN_AFTER = 300           # 离线多少秒算挂了（5 分钟）
PUSH_CHECK_EVERY = 30
PUSH_MAX_PER_USER = 10
PUSH_HOSTS = ("web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com")
PUSH_BOOT = time.time()
_PUSH_LOCK = threading.Lock()


def _push_load():
    try:
        with open(PUSH_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("subs", {})
            d.setdefault("down", {})
            return d
    except FileNotFoundError:
        pass
    except Exception as e:
        print("推送配置读取失败：", e)
    return {"subs": {}, "down": {}}


def _push_save(d):
    tmp = PUSH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PUSH_FILE)


def _push_keys():
    """VAPID 密钥：第一次用时生成并保存，之后一直用同一对（换了的话手机上的订阅会全部失效）。"""
    with _PUSH_LOCK:
        d = _push_load()
        v = d.get("vapid")
        if not v or not v.get("d") or not v.get("pub"):
            k, pub = webpush.new_key()
            v = d["vapid"] = {"d": format(k, "x"), "pub": webpush.b64u(pub)}
            _push_save(d)
    return int(v["d"], 16), webpush.unb64u(v["pub"])


def _push_contact():
    host = urllib.parse.urlsplit(CONF.get("web_url") or "").hostname or "localhost"
    return "mailto:admin@" + host


def _push_send_sync(uid, payload, topic=None):
    """给某人的全部订阅发推送；失效的订阅自动删掉。返回成功条数。"""
    d_key, pub = _push_keys()
    with _PUSH_LOCK:
        subs = list(_push_load()["subs"].get(str(uid), []))
    ok, dead = 0, []
    for sub in subs:
        try:
            code = webpush.send(sub, payload, d_key, pub, _push_contact(), topic=topic)
        except Exception as e:
            print("推送发送失败：", repr(e))
            continue
        print("推送结果：", code, urllib.parse.urlsplit(sub["endpoint"]).hostname)
        if 200 <= code < 300:
            ok += 1
        elif code in (404, 410):
            dead.append(sub["endpoint"])
        else:
            print("推送被拒绝：", code, urllib.parse.urlsplit(sub["endpoint"]).hostname)
    if dead:
        with _PUSH_LOCK:
            d = _push_load()
            d["subs"][str(uid)] = [x for x in d["subs"].get(str(uid), []) if x["endpoint"] not in dead]
            _push_save(d)
    return ok


def _push_badge(uid, down=None):
    """桌面图标右上角的数字：这个人名下现在离线的服务器有几台。"""
    if down is None:
        with _PUSH_LOCK:
            down = _push_load()["down"]
    by_id = {s["id"]: s for s in SERVERS}
    return sum(1 for sid in down if sid in by_id and uid in _push_owners(by_id[sid]))


async def push_to(uid, title, body, tag=None, badge=None):
    payload = {"title": title, "body": body, "tag": tag or "", "url": "/",
               "badge": _push_badge(uid) if badge is None else badge}
    return await asyncio.to_thread(_push_send_sync, uid, payload, (tag or "")[:32] or None)


def _push_owners(srv):
    owner = srv.get("owner")
    return [owner] if owner else list(dh.ADMIN_IDS)


async def _push_loop():
    await asyncio.sleep(20)
    while True:
        try:
            await _push_check()
        except Exception as e:
            print("离线推送检查出错：", repr(e))
        await asyncio.sleep(PUSH_CHECK_EVERY)


async def _push_check():
    now = time.time()
    with _PUSH_LOCK:
        st = _push_load()
    if not st["subs"]:
        return
    down = st["down"]
    changed = False
    ids = set()
    events = []
    for srv in list(SERVERS):
        sid = srv["id"]
        ids.add(sid)
        if srv.get("uninstall"):
            continue
        live = LIVE.get(sid)
        if live:
            off_for = now - live["ts"]
        elif now - PUSH_BOOT < PUSH_DOWN_AFTER + OFFLINE_AFTER:
            continue                                  # 机器人刚重启，探针还没来得及报到，先不判断
        else:
            off_for = now - PUSH_BOOT
        if off_for >= PUSH_DOWN_AFTER and sid not in down:
            since = now - off_for
            down[sid] = int(since)
            changed = True
            mins = max(1, int(off_for // 60))
            events.append((srv, "🔴 服务器离线", f"「{srv['name']}」已经 {mins} 分钟没有响应"))
        elif off_for < OFFLINE_AFTER and sid in down:
            mins = max(1, int((now - down.pop(sid)) // 60))
            changed = True
            events.append((srv, "✅ 服务器恢复", f"「{srv['name']}」已恢复在线（离线约 {mins} 分钟）"))
    for sid in [k for k in down if k not in ids]:     # 已删除的服务器
        down.pop(sid, None)
        changed = True
    if changed:
        with _PUSH_LOCK:
            d = _push_load()
            d["down"] = down
            _push_save(d)
    for srv, title, body in events:          # 先记好状态再发，图标数字按最新的离线台数算
        for uid in _push_owners(srv):
            await push_to(uid, title, body, tag="srv-" + srv["id"], badge=_push_badge(uid, down))


def _push_endpoint_ok(url):
    u = urllib.parse.urlsplit(str(url or ""))
    host = (u.hostname or "").lower()
    return u.scheme == "https" and len(url) < 1000 and (host in PUSH_HOSTS or host.endswith(".notify.windows.com"))


@action("push_key", admin=True)
async def a_push_key(ctx, p):
    _d, pub = _push_keys()
    return {"key": webpush.b64u(pub)}


@action("push_sub", admin=True)
async def a_push_sub(ctx, p):
    ep = str(p.get("endpoint") or "")
    keys = p.get("keys") if isinstance(p.get("keys"), dict) else {}
    p256, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
    if not _push_endpoint_ok(ep):
        raise ApiError("推送地址不正确")
    try:
        webpush._pub_point(webpush.unb64u(p256))
        if len(webpush.unb64u(auth)) != 16:
            raise ValueError
    except Exception:
        raise ApiError("推送密钥不正确")
    with _PUSH_LOCK:
        d = _push_load()
        lst = [x for x in d["subs"].get(str(ctx["uid"]), []) if x["endpoint"] != ep]
        lst.append({"endpoint": ep, "p256dh": p256, "auth": auth, "dev": ctx.get("dev") or "", "created": int(time.time())})
        d["subs"][str(ctx["uid"])] = lst[-PUSH_MAX_PER_USER:]
        _push_save(d)
    return {"ok": True}


@action("push_unsub", admin=True)
async def a_push_unsub(ctx, p):
    ep = str(p.get("endpoint") or "")
    with _PUSH_LOCK:
        d = _push_load()
        d["subs"][str(ctx["uid"])] = [x for x in d["subs"].get(str(ctx["uid"]), []) if x["endpoint"] != ep]
        _push_save(d)
    return {"ok": True}


@action("push_state", admin=True)
async def a_push_state(ctx, p):
    ep = str(p.get("endpoint") or "")
    with _PUSH_LOCK:
        lst = _push_load()["subs"].get(str(ctx["uid"]), [])
    return {"on": any(x["endpoint"] == ep for x in lst) if ep else False, "count": len(lst), "after": PUSH_DOWN_AFTER}


@action("push_test", admin=True)
async def a_push_test(ctx, p):
    """测试推送：等 8 秒再发，让你有时间回到桌面（App 开在前台时 iPhone 不弹横幅）。"""
    need_rate(ctx["uid"])
    with _PUSH_LOCK:
        if not _push_load()["subs"].get(str(ctx["uid"])):
            raise ApiError("还没有开启推送：请先点「开启推送」")
    uid = ctx["uid"]

    async def later():
        await asyncio.sleep(8)
        n = await push_to(uid, "🔔 测试推送", "收到这条说明服务器离线提醒已经开好了（图标上的数字打开 App 后会恢复）", tag="test", badge=_push_badge(uid) + 1)
        print(f"测试推送：用户 {uid} 发出 {n} 条")
    t = asyncio.get_running_loop().create_task(later())
    LAT_TASKS.add(t)
    t.add_done_callback(LAT_TASKS.discard)
    return {"delay": 8}


def _push_drop_dev(uid, dev):
    """撤销主屏幕设备时，把这台设备的推送订阅一起删掉。"""
    with _PUSH_LOCK:
        d = _push_load()
        lst = d["subs"].get(str(uid), [])
        keep = [x for x in lst if x.get("dev") != dev]
        if len(keep) != len(lst):
            d["subs"][str(uid)] = keep
            _push_save(d)


async def start_server():
    server = await asyncio.start_server(handle, CONF["host"], int(CONF["port"]))
    threading.Thread(target=_ipdb_loop, daemon=True).start()
    for coro in (_dns_loop(), _lat_loop(), _push_loop(), _gh_clean_loop(), _ssh_clean_loop(), _px_gc_loop()):       # 后台循环：保留引用，避免被回收
        LAT_TASKS.add(asyncio.get_running_loop().create_task(coro))
    print(f"Mini App 接口已启动：http://{CONF['host']}:{CONF['port']}")
    return server

# ---------------------------------------------------------------- 多服务器监控（探针）
# 目标服务器上跑一个小脚本（web/probe.sh），每 5 秒把数据发到网站的 probe.php，
# 再转给本接口的 /probe。目标机器不用开任何端口。登记的服务器保存在 SERVERS_FILE。

SERVERS_FILE = CONF.get("servers_file") or _data_path("nav_servers.json")
SERVER_LIMIT = 200
GROUP_LIMIT = 30           # 每个人最多建多少个分组
OFFLINE_AFTER = 30          # 超过这么多秒没收到数据就算离线
SERVERS = []                # [{"id","name","token","created","owner","group"}]
GROUPS = {}                 # 每个人的服务器分组（按顺序）：{"<uid>": ["香港", "日本", ...]}
GROUP_DEF = {}              # 每个人「默认组」的名字（没归类的服务器都在这组，可以改名，不能删）：{"<uid>": "其他"}
GROUP_DEF_NAME = "未分组"
LIVE = {}                   # id -> {"ts", "ip", "m": {...}}


def load_servers():
    global SERVERS, GROUPS, GROUP_DEF
    try:
        with open(SERVERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        SERVERS = [x for x in data.get("servers", []) if x.get("id") and x.get("token")]
        g = data.get("groups")
        GROUPS = {str(k): [str(n) for n in v if n] for k, v in g.items() if isinstance(v, list)} if isinstance(g, dict) else {}
        gd = data.get("group_default")
        GROUP_DEF = {str(k): str(v) for k, v in gd.items() if v} if isinstance(gd, dict) else {}
    except FileNotFoundError:
        SERVERS, GROUPS, GROUP_DEF = [], {}, {}
    except Exception as e:
        print("多服务器列表读取失败：", e)
        SERVERS, GROUPS, GROUP_DEF = [], {}, {}


def save_servers():
    tmp = SERVERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"servers": SERVERS, "groups": GROUPS, "group_default": GROUP_DEF}, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SERVERS_FILE)


load_servers()


def _num(v, lo=0.0, hi=1e18):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if v != v or v in (float("inf"), float("-inf")):
        return 0.0
    return max(lo, min(hi, v))


def _txt(v, n=60):
    return re.sub(r"[\x00-\x1f<>\"\\]", "", str(v or ""))[:n]


def probe_report(headers, raw):
    token = headers.get("x-probe-token", "")
    found = None
    for srv in SERVERS:                    # 逐个做恒定时间比较
        if hmac.compare_digest(srv["token"], token):
            found = srv
    if not found:
        return 403, {"ok": False, "error": "探针密钥不正确"}
    try:
        p = json.loads(raw.decode() or "{}")
        if not isinstance(p, dict):
            raise ValueError
    except Exception:
        return 400, {"ok": False, "error": "数据格式错误"}
    mem, disk, net = p.get("mem") or {}, p.get("disk") or {}, p.get("net") or {}
    load = p.get("load") if isinstance(p.get("load"), list) else []
    load = [round(_num(x, 0, 1e5), 2) for x in (load + [0, 0, 0])[:3]]
    m = {
        "host": _txt(p.get("host")), "os": _txt(p.get("os"), 80), "cores": int(_num(p.get("cores"), 1, 4096)) or 1,
        "cpu": round(_num(p.get("cpu"), 0, 100), 1), "steal": round(_num(p.get("steal"), 0, 100), 1),
        "mem": {"used": _num(mem.get("used")), "total": _num(mem.get("total"))},
        "disk": {"used": _num(disk.get("used")), "total": _num(disk.get("total"))},
        "load": load,
        "net": {"up": _num(net.get("up")), "down": _num(net.get("down")),
                "up_total": _num(net.get("up_total")), "down_total": _num(net.get("down_total"))},
        "uptime": _num(p.get("uptime")),
        "cpu_model": _txt(p.get("cpu_model"), 80), "ver": int(_num(p.get("ver"), 0, 99)),
        "ip6": re.sub(r"[^0-9a-fA-F:]", "", str(p.get("ip6") or ""))[:45],
    }
    ip = headers.get("x-probe-ip", "")
    ip = _txt(ip, 45)
    prev = LIVE.get(found["id"]) or {}
    # 探针大部分时候走默认线路，每隔几次强制走一次 IPv4，这样双栈机器也能记住它的 IPv4
    ip4 = ip if (ip and ":" not in ip) else prev.get("ip4", "")
    # CPU 取最近 3 次上报的平均，避免 3 秒一采样时数字上蹿下跳
    hist = (prev.get("cpus") or [])[-2:] + [m["cpu"]]
    raw_cpu = m["cpu"]
    m["cpu"] = round(sum(hist) / len(hist), 1)
    LIVE[found["id"]] = {"ts": time.time(), "ip": ip, "ip4": ip4, "m": m, "cpus": hist}
    if found.get("uninstall") and m["ver"] >= 2:
        # 已通知探针卸载自己：把这台从列表里移除
        SERVERS.remove(found)
        LIVE.pop(found["id"], None)
        SPEED_RUN.pop(found["id"], None)
        save_servers()
        return 200, {"ok": True, "cmd": "uninstall"}
    return 200, _speed_probe(found, m, p.get("spd"))


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0


def server_view(srv):
    live = LIVE.get(srv["id"])
    age = (time.time() - live["ts"]) if live else None
    out = {"id": srv["id"], "name": srv["name"], "group": srv.get("group") or "", "uninstall": bool(srv.get("uninstall")), "online": bool(live and age < OFFLINE_AFTER),
           "age": int(age) if age is not None else None, "seen": bool(live), "allow_ip": srv.get("allow_ip") or ""}
    if live:
        m = live["m"]
        out.update({
            "cpu_model": m.get("cpu_model", ""), "ver": m.get("ver", 0), "ip4": live.get("ip4", ""), "ip6": m.get("ip6") or (live["ip"] if ":" in live["ip"] else ""), "host": m["host"], "os": m["os"], "cores": m["cores"], "cpu": m["cpu"], "steal": m.get("steal", 0),
            "mem": {**m["mem"], "pct": _pct(m["mem"]["used"], m["mem"]["total"])},
            "disk": {**m["disk"], "pct": _pct(m["disk"]["used"], m["disk"]["total"])},
            "load": m["load"], "net": m["net"], "uptime": dh._fmt_duration(m["uptime"]) if m["uptime"] else "",
        })
    return out


def _owns(uid, srv):
    """谁添加的服务器就是谁的；以前添加、没有记录归属的服务器归所有者。"""
    owner = srv.get("owner")
    return owner == uid if owner else dh.is_owner(uid)


def my_servers(uid):
    return [s for s in SERVERS if _owns(uid, s)]


def servers_summary(uid):
    mine = my_servers(uid)
    online = sum(1 for s in mine if (LIVE.get(s["id"]) and time.time() - LIVE[s["id"]]["ts"] < OFFLINE_AFTER))
    return {"total": len(mine), "online": online}


def install_cmd(srv):
    web = (CONF.get("web_url") or "").strip().rstrip("/")
    if not web.startswith("https://"):
        raise ApiError("还没有设置网站地址：请在 nav_miniapp.json 里填写 web_url（https:// 开头）并重启机器人")
    return f"curl -fsSL {web}/probe.sh | bash -s -- {web} {srv['token']}"


def _find_server(p, uid):
    sid = str(p.get("id") or "")
    for srv in my_servers(uid):
        if srv["id"] == sid:
            return srv
    raise ApiError("这台服务器不存在或已被删除", 404)


def _groups_of(uid):
    """这个人的分组（按他排好的顺序）；服务器上记着、但列表里没有的组名也补进来。"""
    gl = list(GROUPS.get(str(uid), []))
    for s in my_servers(uid):
        g = s.get("group")
        if g and g not in gl:
            gl.append(g)
    return gl


def _group_def(uid):
    return GROUP_DEF.get(str(uid)) or GROUP_DEF_NAME


def _group_name(v):
    name = clean(v, 20).strip()
    if not name:
        raise ApiError("分组名称不能为空")
    return name


@action("servers", admin=True)
async def a_servers(ctx, p):
    items = [server_view(s) for s in my_servers(ctx["uid"])]
    return {"items": items, "groups": _groups_of(ctx["uid"]), "default_group": _group_def(ctx["uid"]),
            "limit": SERVER_LIMIT, **servers_summary(ctx["uid"])}


@action("server_get", admin=True)
async def a_server_get(ctx, p):
    return server_view(_find_server(p, ctx["uid"]))


@action("server_edit", admin=True)
async def a_server_edit(ctx, p):
    op = p.get("op")
    uid, key = ctx["uid"], str(ctx["uid"])
    # ---- 分组：新建 / 改名 / 删除 / 调整顺序（不针对某一台服务器）
    if op == "group_add":
        name, gl = _group_name(p.get("name")), _groups_of(uid)
        if name in gl or name == _group_def(uid):
            raise ApiError("已经有这个分组了")
        if len(gl) >= GROUP_LIMIT:
            raise ApiError(f"最多 {GROUP_LIMIT} 个分组")
        GROUPS[key] = gl + [name]
        save_servers()
        return {"groups": GROUPS[key]}
    if op == "group_rename":
        old, new, gl = str(p.get("old") or ""), _group_name(p.get("name")), _groups_of(uid)
        if old not in gl:
            raise ApiError("这个分组不存在")
        if new != old and (new in gl or new == _group_def(uid)):
            raise ApiError("已经有这个分组了")
        GROUPS[key] = [new if g == old else g for g in gl]
        for s in my_servers(uid):
            if s.get("group") == old:
                s["group"] = new
        save_servers()
        return {"groups": GROUPS[key]}
    if op == "group_default":                           # 默认组（没归类的服务器）改名
        new = _group_name(p.get("name"))
        if new in _groups_of(uid):
            raise ApiError("已经有这个分组了")
        GROUP_DEF[key] = new
        save_servers()
        return {"default_group": new}
    if op == "group_del":
        old, gl = str(p.get("name") or ""), _groups_of(uid)
        GROUPS[key] = [g for g in gl if g != old]
        n = 0
        for s in my_servers(uid):
            if s.get("group") == old:
                s.pop("group", None)
                n += 1
        save_servers()
        return {"groups": GROUPS[key], "moved": n}     # 组里的服务器回到「未分组」，不会被删
    if op == "group_order":
        gl, want = _groups_of(uid), [str(x) for x in (p.get("groups") or []) if isinstance(x, str)]
        GROUPS[key] = [g for g in want if g in gl] + [g for g in gl if g not in want]
        save_servers()
        return {"groups": GROUPS[key]}
    if op == "uam_state":                               # 添加服务器前：探针网址所在的域名是不是开着 5 秒盾（开着就要求填 IP）
        try:
            zone, on = await _uam_probe_zone()
        except ApiError as e:
            return {"uam": None, "error": str(e)}
        return {"uam": on, "zone": zone["name"] if zone else ""}
    if op == "add":
        if len(SERVERS) >= SERVER_LIMIT:
            raise ApiError(f"最多添加 {SERVER_LIMIT} 台服务器")
        name = clean(p.get("name"), 30)
        srv = {"id": secrets.token_hex(4), "name": name, "token": secrets.token_hex(16), "created": int(time.time()), "owner": ctx["uid"]}
        grp = str(p.get("group") or "")
        if grp and grp in _groups_of(uid):
            srv["group"] = grp
        ip = _ip_public(p.get("ip"))
        if ip:
            srv["allow_ip"] = ip
        cmd = install_cmd(srv)                  # 先确认能生成命令，再保存
        SERVERS.append(srv)
        save_servers()
        out = {"id": srv["id"], "cmd": cmd}
        if ip:
            out["uam"] = await _uam_refresh(ctx)
        return out
    srv = _find_server(p, ctx["uid"])
    if op == "rename":
        srv["name"] = clean(p.get("name"), 30)
        save_servers()
        return {"ok": True}
    if op == "allow_ip":                                # 手填这台的公网 IP（网站开着 5 秒盾时用来放行探针）；空 = 清除
        ip = _ip_public(p.get("ip"))
        if ip:
            srv["allow_ip"] = ip
        else:
            srv.pop("allow_ip", None)
        save_servers()
        return {"ok": True, "allow_ip": ip, "uam": await _uam_refresh(ctx)}
    if op == "group":                                   # 把这台放进某个分组；空 = 未分组
        grp = str(p.get("group") or "")
        if grp and grp not in _groups_of(uid):
            raise ApiError("这个分组不存在")
        if grp:
            srv["group"] = grp
        else:
            srv.pop("group", None)
        save_servers()
        return {"ok": True, "group": grp}
    if op == "dns":
        return await _dns_set(ctx, srv, p)
    if op == "del":
        if srv.get("dns_parked"):
            _ok, left = await _dns_unpark(srv, "被删除，先加回临时摘除的解析")
            if left:
                raise ApiError(f"这台还有 {len(left)} 条临时摘除的解析没能加回，请稍后再删")
        SERVERS.remove(srv)
        LIVE.pop(srv["id"], None)
        save_servers()
        return {"ok": True}
    if op == "regen":
        srv["token"] = secrets.token_hex(16)
        LIVE.pop(srv["id"], None)
        save_servers()
        return {"cmd": install_cmd(srv)}
    if op == "cmd":
        return {"cmd": install_cmd(srv)}
    if op == "uninstall":
        live = LIVE.get(srv["id"])
        if not live or live["m"].get("ver", 0) < 2 or time.time() - live["ts"] >= OFFLINE_AFTER:
            raise ApiError("这台探针不在线或版本较旧，不能远程卸载，请在它上面手动执行卸载命令")
        srv["uninstall"] = True
        save_servers()
        return {"ok": True}
    if op == "cancel_uninstall":
        srv.pop("uninstall", None)
        save_servers()
        return {"ok": True}
    raise ApiError("不支持的操作")



# ---------------------------------------------------------------- 服务器带宽测速
# 操作台点「测速」→ 下一次探针上报时回一个 cmd=speedtest → 探针（v4 起）在后台测下载 / 上传各约 10 秒
# （v5 起测速点自动切换：Cloudflare → Ookla Speedtest → 公共测速文件），把进度和结果夹在之后的上报里带回来；收到结果后回 ack，探针就不再带。
# 只记最后一次结果（存在 nav_servers.json 的 speed 字段）。旧版探针 / bash 版探针不支持，需要重装一次。

SPEED_RUN = {}              # id -> {"sid","state":wait|run|done|err,"phase","d","u","ping","node","err","t0","seen"}
SPEED_MIN_VER = 4
SPEED_STALL = 60            # 这么久没收到进度就算失败


def _spd_val(v, hi=1e6):
    if v is None:
        return None
    return round(_num(v, 0, hi), 2)


def _speed_probe(srv, m, sp):
    resp = {"ok": True}
    st = SPEED_RUN.get(srv["id"])
    if isinstance(sp, dict) and sp.get("sid"):
        sid = _txt(sp.get("sid"), 20)
        if st and sid == st["sid"] and st["state"] in ("wait", "run"):
            st.update(phase=_txt(sp.get("phase"), 2), d=_spd_val(sp.get("d")), u=_spd_val(sp.get("u")),
                      ping=_spd_val(sp.get("ping"), 1e5), node=_txt(sp.get("node"), 24), seen=time.time())
            if sp.get("err"):
                st.update(state="err", err=_txt(sp.get("err"), 80))
                resp["ack"] = sid
            elif sp.get("done"):
                st["state"] = "done"
                srv["speed"] = {"d": st["d"], "u": st["u"], "ping": st["ping"], "node": st["node"], "ts": int(time.time())}
                save_servers()
                resp["ack"] = sid
            else:
                st["state"] = "run"
        elif not st or sid != st["sid"] or st["state"] in ("done", "err"):
            resp["ack"] = sid          # 过期的结果：让探针丢掉
    if st and st["state"] == "wait" and m.get("ver", 0) >= SPEED_MIN_VER:
        resp["cmd"] = "speedtest"
        resp["sid"] = st["sid"]
    return resp


def _speed_state(srv):
    st = SPEED_RUN.get(srv["id"])
    if not st:
        return None
    if st["state"] in ("wait", "run") and time.time() - max(st["t0"], st.get("seen") or 0) > SPEED_STALL:
        st.update(state="err", err="探针没有回应，请稍后再试")
    return {k: st.get(k) for k in ("state", "phase", "d", "u", "ping", "node", "err")}


@action("speed", admin=True)
async def a_speed(ctx, p):
    items = []
    for s in my_servers(ctx["uid"]):
        live = LIVE.get(s["id"])
        items.append({"id": s["id"], "name": s["name"],
                      "online": bool(live and time.time() - live["ts"] < OFFLINE_AFTER),
                      "ver": live["m"].get("ver", 0) if live else 0,
                      "last": s.get("speed"), "run": _speed_state(s)})
    return {"items": items, "min_ver": SPEED_MIN_VER}


@action("speed_edit", admin=True)
async def a_speed_edit(ctx, p):
    if p.get("op") != "run":
        raise ApiError("不支持的操作")
    srv = _find_server(p, ctx["uid"])
    live = LIVE.get(srv["id"])
    if not live or time.time() - live["ts"] >= OFFLINE_AFTER:
        raise ApiError("这台服务器不在线")
    if live["m"].get("ver", 0) < SPEED_MIN_VER:
        raise ApiError("这台的探针是旧版，不支持测速：在这台服务器上重新执行一次安装命令就行")
    st = _speed_state(srv)
    if st and st["state"] in ("wait", "run"):
        raise ApiError("这台正在测速")
    running = sum(1 for x in SPEED_RUN.values() if x["state"] in ("wait", "run"))
    if running >= 3:
        raise ApiError("同时最多测 3 台，请等一会儿")
    SPEED_RUN[srv["id"]] = {"sid": secrets.token_hex(4), "state": "wait", "phase": "", "d": None, "u": None,
                            "ping": None, "node": "", "err": "", "t0": time.time(), "seen": 0}
    return {"ok": True}


# ---------------------------------------------------------------- 解析联动（探针 + Cloudflare）
# 每台服务器两个开关（只有所有者能设置）：
#   dns_auto     IP 变了自动改解析：探针从新 IPv4 连续上报 3 分钟，就把所有指向旧 IP 的 A 记录改成新 IP
#   dns_failover 离线自动切换：探针离线 3 分钟，临时删掉指向它的 A 记录（同名还有其他在线服务器时才删）；
#                恢复在线 2 分钟后按原样加回。被删的记录保存在 nav_servers.json 的 dns_parked 里，重启也不会丢。
# 探针本身不用改：IP 和在线状态都来自现有的上报。

DNS_IP_STABLE = 180        # 新 IP 连续出现这么久才算真的换了
DNS_DOWN_AFTER = 180       # 离线这么久才摘除解析
DNS_UP_STABLE = 120        # 恢复在线这么久才加回解析
DNS_CHECK_EVERY = 20
DNS_RETRY_EVERY = 300      # 摘除 / 加回失败后多久再试
DNS_STATE = {}             # id -> 运行中的临时状态（不落盘）
DNS_APP = {"app": None}
DNS_BOOT = time.time()
DNS_CTX = {"uid": "auto"}  # 写操作日志时的操作人


def _ipn(v):
    try:
        return str(ipaddress.ip_address(str(v).strip()))
    except ValueError:
        return ""


def _srv_online(srv):
    live = LIVE.get(srv["id"])
    return bool(live and time.time() - live["ts"] < OFFLINE_AFTER)


def _cf_ready():
    return bool((os.environ.get("CF_API_TOKEN") or CONF.get("cf_token") or "").strip())


async def _dns_notify(text):
    app = DNS_APP["app"]
    print("解析联动：" + text.replace("\n", " | "))
    if not app:
        return
    for uid in dh.ADMIN_IDS:
        try:
            await app.bot.send_message(chat_id=uid, text=text, disable_web_page_preview=True)
        except Exception as e:
            print("解析联动通知发送失败：", e)


def _names(items, limit=12):
    names = [x if isinstance(x, str) else x["name"] for x in items]
    return "、".join(names[:limit]) + (f" 等 {len(names)} 条" if len(names) > limit else "")


async def _dns_records_to(ip):
    """[(zone, record_view)]：所有内容等于 ip 的 A 记录。"""
    return await _cf_matches(ip, "A")


async def _dns_swap(srv, old, new):
    found = await _dns_records_to(old)
    done, failed = [], []
    for z, r in found:
        try:
            j = await _cf_call("PATCH", f"/zones/{z['id']}/dns_records/{r['id']}", {"content": new})
            _cf_audit(DNS_CTX, "auto_swap_ip", z, r, _cf_view(j["result"]))
            done.append(r["name"])
        except ApiError as e:
            failed.append(f"{r['name']}（{e}）")
    msg = f"🔁 服务器「{srv['name']}」IP 变了：{old} → {new}\n"
    if done:
        msg += f"已自动更新 {len(done)} 条解析：{_names(done)}"
    else:
        msg += "Cloudflare 上没有指向旧 IP 的 A 记录，没有改动。"
    if failed:
        msg += f"\n❌ 失败 {len(failed)} 条：{_names(failed, 6)}，请到「解析」里手动修改。"
    await _dns_notify(msg)


async def _dns_ip_check(srv):
    """IP 变化检测：只看 IPv4。"""
    st = DNS_STATE.setdefault(srv["id"], {})
    live = LIVE.get(srv["id"])
    ip = _ipn(live.get("ip4") or "") if live and _srv_online(srv) else ""
    if not ip or ":" in ip:
        st.pop("new_ip", None)
        return
    known = srv.get("dns_ip")
    if not known:
        srv["dns_ip"] = ip
        save_servers()
        return
    if ip == known:
        st.pop("new_ip", None)
        return
    if st.get("new_ip") != ip:
        st["new_ip"], st["since"] = ip, time.time()
        return
    if time.time() - st["since"] < DNS_IP_STABLE:
        return
    st.pop("new_ip", None)
    srv["dns_ip"] = ip
    for x in srv.get("dns_parked") or []:      # 正在摘除中的记录，加回时用新 IP
        if x.get("content") == known:
            x["content"] = ip
    save_servers()
    if srv.get("dns_auto"):
        await _dns_swap(srv, known, ip)


async def _dns_park(srv, ip, minutes):
    offline = {ip}                          # 所有离线服务器的 IP（开没开联动都算）
    for s in SERVERS:
        if not _srv_online(s):
            live = LIVE.get(s["id"]) or {}
            offline |= {x for x in (s.get("dns_ip"), _ipn(live.get("ip4") or "")) if x}
    parked, kept = [], []
    for z in await _cf_zones():
        recs = await _cf_records(z)
        for r in recs:
            if r["type"] != "A" or _ipn(r["content"]) != ip:
                continue
            others = [o for o in recs if o["id"] != r["id"] and o["type"] == "A" and o["name"] == r["name"]
                      and _ipn(o["content"]) not in offline]
            if not others:                 # 同名没有其他可用服务器：删了也没用，保留
                kept.append(r["name"])
                continue
            try:
                await _cf_call("DELETE", f"/zones/{z['id']}/dns_records/{r['id']}")
            except ApiError as e:
                kept.append(f"{r['name']}（删除失败：{e}）")
                continue
            _cf_audit(DNS_CTX, "auto_park", z, r, None)
            parked.append({"zone": z["id"], "zone_name": z["name"], "type": r["type"], "name": r["name"],
                           "content": r["content"], "ttl": r["ttl"], "proxied": r["proxied"], "comment": r["comment"]})
    if parked:
        srv["dns_parked"] = (srv.get("dns_parked") or []) + parked
    if not srv.get("dns_down"):
        srv["dns_down"] = int(time.time() - minutes * 60)   # 记住离线开始时间，恢复时要通知
    save_servers()
    st = DNS_STATE.setdefault(srv["id"], {})
    if not parked and st.get("kept_told") == set(kept):
        return                              # 没有新变化：不重复提醒
    st["kept_told"] = set(kept)
    msg = f"⚠️ 服务器「{srv['name']}」（{ip}）离线超过 {minutes} 分钟"
    if not parked and not kept:
        msg += "\nCloudflare 上没有指向它的 A 记录，不需要切换。恢复后会再通知你。"
    if parked:
        msg += f"\n已临时摘除 {len(parked)} 条解析，流量切到同名的其他服务器：{_names(parked)}\n恢复在线后会自动加回。"
    if kept:
        msg += f"\n以下记录没有其他可用服务器，保留未动：{_names(kept, 6)}"
    if kept and not parked:
        msg += "\n恢复后会再通知你。"
    await _dns_notify(msg)


async def _dns_unpark(srv, why):
    parked = srv.get("dns_parked") or []
    if not parked:
        return [], []
    ip = srv.get("dns_ip")
    ok, left = [], []
    for x in parked:
        body = {"type": x["type"], "name": x["name"], "content": ip or x["content"], "ttl": x.get("ttl", 1),
                "proxied": bool(x.get("proxied")), "comment": x.get("comment") or ""}
        try:
            j = await _cf_call("POST", f"/zones/{x['zone']}/dns_records", body)
            _cf_audit(DNS_CTX, "auto_unpark", {"name": x.get("zone_name", "")}, None, _cf_view(j["result"]))
            ok.append(x["name"])
        except ApiError as e:
            if "already exist" in str(e).lower():      # 你已经手动加回了
                ok.append(x["name"])
            else:
                left.append(x)
    srv["dns_parked"] = left
    save_servers()
    msg = f"✅ 服务器「{srv['name']}」{why}"
    if ok:
        msg += f"\n已加回 {len(ok)} 条解析：{_names(ok)}"
    if left:
        msg += f"\n❌ 还有 {len(left)} 条加回失败，稍后会自动重试：{_names(left, 6)}"
    await _dns_notify(msg)
    return ok, left


async def _dns_failover_check(srv):
    if not srv.get("dns_failover"):
        return
    ip = srv.get("dns_ip")
    if not ip:
        return
    st = DNS_STATE.setdefault(srv["id"], {})
    now = time.time()
    live = LIVE.get(srv["id"])
    if live:
        down_for = now - live["ts"]
    else:
        down_for = now - DNS_BOOT          # 机器人重启后一直没收到这台的数据
    online = down_for < OFFLINE_AFTER
    if online:
        st.pop("park_tried", None)
        st.pop("kept_told", None)
        if srv.get("dns_parked") or srv.get("dns_down"):
            st.setdefault("up_since", now)
            if now - st["up_since"] >= DNS_UP_STABLE and now - st.get("unpark_try", 0) >= DNS_RETRY_EVERY:
                st["unpark_try"] = now
                down = srv.get("dns_down")
                dur = f"（离线约 {max(1, int((st['up_since'] - down) // 60))} 分钟）" if down else ""
                if srv.get("dns_parked"):
                    _ok, left = await _dns_unpark(srv, "已恢复在线" + dur)
                else:
                    left = []
                    await _dns_notify(f"✅ 服务器「{srv['name']}」已恢复在线{dur}")
                if not left:
                    srv.pop("dns_down", None)
                    save_servers()
                    st.pop("up_since", None)
                    st.pop("unpark_try", None)
        return
    st.pop("up_since", None)
    if down_for < DNS_DOWN_AFTER or now - DNS_BOOT < DNS_DOWN_AFTER:
        return
    if now - st.get("park_tried", 0) < DNS_RETRY_EVERY:
        return
    st["park_tried"] = now
    await _dns_park(srv, ip, int(down_for // 60))


async def _dns_loop():
    await asyncio.sleep(15)
    while True:
        try:
            if _cf_ready():
                for srv in list(SERVERS):
                    if not (srv.get("dns_auto") or srv.get("dns_failover")):
                        continue
                    try:
                        await _dns_ip_check(srv)
                        await _dns_failover_check(srv)
                    except ApiError as e:
                        print("解析联动出错：", srv.get("name"), e)
                    except Exception as e:
                        print("解析联动出错：", srv.get("name"), repr(e))
        except Exception as e:
            print("解析联动循环出错：", repr(e))
        await asyncio.sleep(DNS_CHECK_EVERY)


def _dns_need_owner(ctx):
    if not dh.is_owner(ctx["uid"]):
        raise ApiError("只有所有者可以设置解析联动", 403)


async def _dns_info(srv):
    live = LIVE.get(srv["id"])
    ip = srv.get("dns_ip") or (_ipn(live.get("ip4") or "") if live else "")
    recs = [{"zone": z["name"], "name": r["name"], "proxied": r["proxied"]} for z, r in await _dns_records_to(ip)] if ip else []
    st = DNS_STATE.get(srv["id"]) or {}
    return {"auto": bool(srv.get("dns_auto")), "failover": bool(srv.get("dns_failover")),
            "ip": ip, "live_ip": _ipn(live.get("ip4") or "") if live else "", "pending_ip": st.get("new_ip") or "",
            "records": recs, "parked": [{"zone": x.get("zone_name", ""), "name": x["name"]} for x in srv.get("dns_parked") or []],
            "online": _srv_online(srv)}


@action("server_dns", admin=True)
async def a_server_dns(ctx, p):
    _dns_need_owner(ctx)
    _cf_token()
    return await _dns_info(_find_server(p, ctx["uid"]))


async def _dns_set(ctx, srv, p):
    _dns_need_owner(ctx)
    _cf_token()
    auto, fo = bool(p.get("auto")), bool(p.get("failover"))
    if (auto or fo) and not srv.get("dns_ip"):
        live = LIVE.get(srv["id"])
        ip = _ipn(live.get("ip4") or "") if live else ""
        if not ip or ":" in ip:
            raise ApiError("还没收到这台服务器的 IPv4，等探针在线后再开启")
        srv["dns_ip"] = ip
    if not fo and srv.get("dns_parked"):
        _ok, left = await _dns_unpark(srv, "关闭了离线自动切换")
        if left:
            raise ApiError(f"有 {len(left)} 条摘除的解析没能加回，请稍后再试")
    srv["dns_auto"], srv["dns_failover"] = auto, fo
    if not fo:
        srv.pop("dns_down", None)
    if not auto and not fo:
        srv.pop("dns_ip", None)          # 都关了：下次开启时重新记住当时的 IP
        DNS_STATE.pop(srv["id"], None)
    save_servers()
    return await _dns_info(srv)


# ---------------------------------------------------------------- 延迟监控（从国内电信 / 联通 / 移动 ping 各台服务器）
# 每隔 latency_every 秒（默认 900 = 15 分钟，可在 nav_miniapp.json 里改，最少 300）用 Globalping 的国内探针测一轮，
# 每台服务器一次测量、三个运营商各一个探针、每个探针 4 个包。结果存 nav_latency.json，保留 7 天。
LAT_FILE = _data_path("nav_latency.json")
LAT_KEEP = 7 * 86400
LAT_CARRIERS = (
    ("电信", 4134, {4134, 4809, 4811, 4812, 4813, 4816, 23724, 134774}),
    ("联通", 4837, {4837, 4808, 9929, 17621, 17622, 17623, 17816}),
    ("移动", 9808, {9808, 9394, 24400, 24444, 24445, 56040, 56041, 56042, 56044, 56046, 56047, 56048}),
)
LAT_STATE = {"last": 0, "running": False, "manual": 0}
LAT_TASKS = set()
try:
    with open(LAT_FILE, "r", encoding="utf-8") as _f:
        LAT_DATA = json.load(_f)
    if not isinstance(LAT_DATA, dict):
        raise ValueError
except Exception:
    LAT_DATA = {}
LAT_DATA.setdefault("pts", {})      # sid -> [[ts, [ms, 丢包%] 或 None, ×3], ...]；ms 为 None 表示不通
LAT_DATA.setdefault("ip", {})       # sid -> 上次测的 IP（服务器离线时继续测它）


LAT_HOURLY_MAX = 200        # Globalping 免费额度有限：每小时最多发这么多次检测


def _lat_every():
    """检测间隔：默认 15 分钟；服务器多了自动拉长，保证每小时不超过 LAT_HOURLY_MAX 次（200 台时约 1 小时一轮）。"""
    try:
        v = int(CONF.get("latency_every") or 900)
    except (TypeError, ValueError):
        v = 900
    need = len(SERVERS) * 3600 // LAT_HOURLY_MAX
    return max(300, v, need)


def _lat_save():
    try:
        tmp = LAT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(LAT_DATA, f, separators=(",", ":"))
        os.replace(tmp, LAT_FILE)
    except Exception as e:
        print("延迟数据保存失败：", e)


def _lat_ip(srv):
    live = LIVE.get(srv["id"]) or {}
    return _ipn(live.get("ip4") or "") or srv.get("dns_ip") or LAT_DATA["ip"].get(srv["id"], "")


def _lat_measure_sync(ip):
    body = {"type": "ping", "target": ip,
            "locations": [{"country": "CN", "asn": asn, "limit": 1} for _n, asn, _s in LAT_CARRIERS],
            "measurementOptions": {"packets": 4}}
    out = [None, None, None]
    for item in dh._gp_run(body, timeout=30) or []:
        probe = item.get("probe") or {}
        res = item.get("result") or {}
        st = res.get("stats") or {}
        try:
            asn = int(probe.get("asn") or 0)
        except (TypeError, ValueError):
            asn = 0
        for i, (_n, _a, asns) in enumerate(LAT_CARRIERS):
            if asn in asns and out[i] is None:
                avg, loss = st.get("avg"), st.get("loss")
                try:
                    loss = float(loss) if loss is not None else (0.0 if avg is not None else 100.0)
                except (TypeError, ValueError):
                    loss = 100.0
                if avg is None or loss >= 100:
                    out[i] = [None, 100]
                else:
                    out[i] = [round(float(avg), 1), int(round(loss))]
    return out


async def _lat_round():
    if LAT_STATE["running"]:
        return
    LAT_STATE["running"] = True
    LAT_STATE["last"] = time.time()
    try:
        sem = asyncio.Semaphore(2)

        async def one(srv):
            ip = _lat_ip(srv)
            if not ip:
                return
            async with sem:
                try:
                    r = await asyncio.to_thread(_lat_measure_sync, ip)
                except Exception as e:
                    print("延迟检测失败：", srv.get("name"), str(e)[:120])
                    return
            if r == [None, None, None]:
                return
            LAT_DATA["ip"][srv["id"]] = ip
            LAT_DATA["pts"].setdefault(srv["id"], []).append([int(time.time())] + r)

        await asyncio.gather(*(one(s) for s in list(SERVERS)))
        cut = time.time() - LAT_KEEP
        ids = {s["id"] for s in SERVERS}
        LAT_DATA["pts"] = {k: [p for p in v if p[0] >= cut] for k, v in LAT_DATA["pts"].items() if k in ids}
        LAT_DATA["ip"] = {k: v for k, v in LAT_DATA["ip"].items() if k in ids}
        _lat_save()
    finally:
        LAT_STATE["running"] = False


async def _lat_loop():
    await asyncio.sleep(45)
    while True:
        try:
            if SERVERS and time.time() - LAT_STATE["last"] >= _lat_every():
                await _lat_round()
        except Exception as e:
            print("延迟监控出错：", repr(e))
        await asyncio.sleep(30)


def _lat_avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


@action("lat", admin=True)
async def a_lat(ctx, p):
    """首页卡片和详情页用的总览：可用率、三网平均延迟、24 小时走势、每台服务器最新结果。"""
    now, ev = time.time(), _lat_every()
    mine = my_servers(ctx["uid"])
    buckets, checks, ok, last_ts, servers = {}, 0, 0, 0, []
    for s in mine:
        pts = [x for x in LAT_DATA["pts"].get(s["id"], []) if x[0] >= now - 86400]
        for x in pts:
            b = buckets.setdefault(int(x[0] // ev), [[], [], []])
            for i in range(3):
                v = x[i + 1]
                if v is None:
                    continue
                checks += 1
                if v[0] is not None:
                    ok += 1
                    b[i].append(v[0])
        last = pts[-1] if pts else None
        if last:
            last_ts = max(last_ts, last[0])
        servers.append({"id": s["id"], "name": s["name"], "ip": LAT_DATA["ip"].get(s["id"], ""),
                        "ts": last[0] if last else 0,
                        "v": [None if v is None else {"ms": v[0], "loss": v[1]} for v in last[1:]] if last else None})
    keys = sorted(buckets)
    isp = []
    for i, (name, _a, _s) in enumerate(LAT_CARRIERS):
        cur = [sv["v"][i]["ms"] for sv in servers if sv["v"] and sv["v"][i] and sv["v"][i]["ms"] is not None]
        isp.append({"name": name, "avg": _lat_avg(cur), "series": [_lat_avg(buckets[k][i]) for k in keys]})
    return {"every": ev, "last": last_ts, "running": LAT_STATE["running"],
            "avail": round(ok * 100 / checks, 1) if checks else None, "isp": isp, "servers": servers}


@action("lat_server", admin=True)
async def a_lat_server(ctx, p):
    srv = _find_server(p, ctx["uid"])
    span = 7 * 86400 if p.get("range") == "7d" else 86400
    pts = [x for x in LAT_DATA["pts"].get(srv["id"], []) if x[0] >= time.time() - span]
    if span > 86400 and len(pts) > 200:          # 7 天的数据按小时合并，曲线不会太密
        merged, cur = [], None
        for x in pts:
            h = x[0] // 3600
            if not cur or cur[0] != h:
                cur = [h, []]
                merged.append(cur)
            cur[1].append(x)
        pts = []
        for h, xs in merged:
            row = [int(h * 3600)]
            for i in range(3):
                vs = [x[i + 1] for x in xs if x[i + 1] is not None]
                if not vs:
                    row.append(None)
                    continue
                ms = [v[0] for v in vs if v[0] is not None]
                row.append([_lat_avg(ms), int(round(sum(v[1] for v in vs) / len(vs)))])
            pts.append(row)
    return {"id": srv["id"], "name": srv["name"], "pts": pts}


@action("lat_edit", admin=True)
async def a_lat_edit(ctx, p):
    if p.get("op") != "run":
        raise ApiError("未知操作")
    if LAT_STATE["running"]:
        return {"started": False, "msg": "正在检测中"}
    if time.time() - LAT_STATE["manual"] < 120:
        raise ApiError("刚检测过，请 2 分钟后再试")
    LAT_STATE["manual"] = time.time()
    task = asyncio.get_running_loop().create_task(_lat_round())
    LAT_TASKS.add(task)
    task.add_done_callback(LAT_TASKS.discard)
    return {"started": True}


# ---------------------------------------------------------------- 首页按钮 / 欢迎语
# 在 nav_miniapp.json 里填 "web_url"（Mini App 的网址），机器人首页就会多一个「打开操作台」按钮，
# 新用户 /start 的欢迎页也会提示点它。web_url 留空则不改动机器人。

_orig_keyboard = dh.main_menu_keyboard
_orig_home_text = dh.home_text


def _web_url():
    url = (CONF.get("web_url") or "").strip()
    return url if url.startswith("https://") else ""


def _app_url():
    """机器人按钮打开的地址：设置了 entry_key 时自动带上 ?k=密钥（网站 config.php 里要填同一个值）。"""
    url = _web_url()
    key = str(CONF.get("entry_key") or "").strip()
    if url and key:
        url = url.rstrip("/") + "/?k=" + urllib.parse.quote(key, safe="")
    return url


def main_menu_keyboard(user_id=None):
    markup = _orig_keyboard(user_id)
    url = _app_url()
    if not url or user_id is None or not dh.can_use_tools(user_id):
        return markup
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
        style = str(CONF.get("button_style", "success")).strip().lower()   # success 绿 / primary 蓝 / danger 红 / 空=默认
        button = None
        if style in ("success", "primary", "danger"):
            try:   # Telegram 新版机器人接口支持按钮颜色；老版本库不认就退回普通按钮
                button = InlineKeyboardButton("💠 打开操作台", web_app=WebAppInfo(url=url), api_kwargs={"style": style})
            except Exception:
                button = None
        if button is None:
            button = InlineKeyboardButton("💠 打开操作台", web_app=WebAppInfo(url=url))
        rows = [list(row) for row in markup.inline_keyboard]
        if CONF.get("home_compact", True):
            # 首页只留「打开操作台」和（所有者的）「管理后台」，其余按钮只是不显示，功能和指令都还在
            keep = [[b for b in row if getattr(b, "callback_data", None) == "admin|back" and dh.is_owner(user_id)] for row in rows]
            rows = [r for r in keep if r]
        if dh.is_admin(user_id):
            srv_btn = InlineKeyboardButton("🖥 服务器监控", callback_data="srv_home")
            for row in rows:
                if any(getattr(b, "callback_data", None) == "admin|back" for b in row):
                    row.insert(0, srv_btn)
                    break
        return InlineKeyboardMarkup([[button]] + rows)
    except Exception as e:
        print("添加 Mini App 按钮失败：", e)
        return markup


def _compact_home(user_id):
    """首页精简：有 Mini App 按钮、用户有权限、而且放了首页图片时，首页只剩图片 + 按钮（没图片就保留文字，免得消息是空的）。"""
    return bool(CONF.get("home_compact", True) and _web_url() and user_id is not None
                and dh.can_use_tools(user_id) and os.path.exists(dh.HOME_BANNER_FILE))


def home_text(welcome=False, user_id=None):
    if _compact_home(user_id):
        return ""
    text = _orig_home_text(welcome, user_id)
    if welcome and _web_url() and user_id is not None and dh.can_use_tools(user_id):
        text += "\n💠 点下方「打开操作台」，用图形界面操作全部功能"
    return text


dh.main_menu_keyboard = main_menu_keyboard
dh.home_text = home_text

def _fmt_rate(n):
    n = float(n or 0)
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if n < 1024 or unit == "GB/s":
            return f"{n:.0f} {unit}" if unit == "B/s" else f"{n:.1f} {unit}"
        n /= 1024


def servers_text(uid):
    mine = my_servers(uid)
    if not mine:
        return ("🖥 <b>服务器监控</b>\n━━━━━━━━━━━━━━\n"
                "还没有添加服务器。\n打开「操作台」→ 管理 → 服务器监控，点「＋ 添加」，按提示在目标服务器上执行一条命令即可。")
    s = servers_summary(uid)
    lines = [f"🖥 <b>服务器监控</b>　{s['online']}/{s['total']} 在线", "━━━━━━━━━━━━━━"]
    for srv in mine:
        v = server_view(srv)
        name = _html.escape(v["name"])
        if v["online"]:
            lines.append(f"🟢 <b>{name}</b>\n　CPU {v['cpu']:.0f}% · 内存 {v['mem']['pct']:.0f}% · 硬盘 {v['disk']['pct']:.0f}%\n"
                         f"　↑ {_fmt_rate(v['net']['up'])}　↓ {_fmt_rate(v['net']['down'])}")
        elif v["seen"]:
            lines.append(f"🔴 <b>{name}</b>　离线 {dh._fmt_duration(v['age'])}")
        else:
            lines.append(f"⚪ <b>{name}</b>　等待探针连接")
    return "\n".join(lines)[:1000]


async def servers_cb(update, context):
    query = update.callback_query
    uid = query.from_user.id
    if not dh.is_admin(uid):
        await query.answer("只有管理员可以使用", show_alert=True)
        raise dh.ApplicationHandlerStop
    await query.answer()
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        chat_id = query.message.chat_id
        try:
            dh.stop_home_live(chat_id)
        except Exception:
            pass
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 刷新", callback_data="srv_home"),
                                        InlineKeyboardButton("🏠 返回首页", callback_data="home")]])
        await dh.panel_notice(query.get_bot(), chat_id, servers_text(uid), markup)
    except Exception as e:
        print("多服务器页面出错：", repr(e))
    raise dh.ApplicationHandlerStop


# ---------------------------------------------------------------- 挂到机器人启动流程上

_orig_post_init = dh.post_init


async def post_init(application):
    await _orig_post_init(application)
    DNS_APP["app"] = application
    try:
        from telegram.ext import CallbackQueryHandler
        application.add_handler(CallbackQueryHandler(servers_cb, pattern=r"^srv_home$"), group=-1)
    except Exception as e:
        print("多服务器按钮注册失败：", e)
    try:
        application.bot_data["miniapp_server"] = await start_server()
        if _web_url():
            print("Mini App：首页「打开操作台」按钮已启用 →", _web_url())
        else:
            print("Mini App：没有设置 web_url（或不是 https:// 开头），首页不会显示按钮，配置文件：", CONF_FILE)
    except Exception as e:
        print("Mini App 接口启动失败（机器人不受影响）：", e)


dh.post_init = post_init

if __name__ == "__main__":
    dh.run_with_rollback(dh.main)
