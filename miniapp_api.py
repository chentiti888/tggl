#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
功能导航 Mini App 接口（和机器人同一个进程运行，不用改 dh.py）

用法：把本文件放在 dh.py 旁边，把原来的启动命令
    python3 dh.py
改成
    python3 miniapp_api.py
机器人照常工作；同时在 127.0.0.1:8787 开一个只给本机用的接口，
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
import hashlib
import hmac
import html as _html
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dh  # noqa: E402  复用机器人里已有的全部功能

CONF_FILE = os.environ.get("MINIAPP_CONF", "/root/nav_miniapp.json")
MAX_BODY = 64 * 1024
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
OWNER_ONLY = {"users", "users_edit"}      # 授权用户管理：只有所有者能用


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
    running = sum(1 for j in JOBS.values() if j["uid"] == ctx["uid"] and not j["done"])
    if running >= JOB_PER_USER:
        raise ApiError("你有任务还在运行，请等它完成或先取消")
    jid = secrets.token_urlsafe(9)
    JOBS[jid] = {"id": jid, "uid": ctx["uid"], "kind": kind, "title": title, "created": now,
                 "done": False, "cancel": False, "error": None, "result": None, "progress": {}}
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


@action("job_get")
async def a_job_get(ctx, p):
    job = _job_of(ctx, p)
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
            if length > MAX_BODY:
                return await respond(writer, 413, {"ok": False, "error": "请求太大"})
            raw = await asyncio.wait_for(reader.readexactly(length), 10) if length else b""
        except Exception:
            return await respond(writer, 400, {"ok": False, "error": "请求格式错误"})
        if not hmac.compare_digest(headers.get("x-api-key", ""), CONF["api_key"]):
            return await respond(writer, 403, {"ok": False, "error": "接口密钥不正确"})
        if method == "POST" and path == "/probe":
            code, out = probe_report(headers, raw)
            return await respond(writer, code, out)
        if method != "POST" or not path.startswith("/api/"):
            return await respond(writer, 404, {"ok": False, "error": "接口不存在"})
        name = path[5:].split("?", 1)[0]
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
        try:
            user = verify_init_data(headers.get("x-init-data", ""), dh.BOT_TOKEN, int(CONF["max_age"]))
            uid = user["id"]
            if not dh.can_use_tools(uid):
                raise ApiError(f"你没有使用权限，你的 ID：{uid}", 403)
            admin = dh.is_admin(uid)
            if need_admin and not admin:
                raise ApiError("只有管理员可以操作", 403)
            if name in OWNER_ONLY and not dh.is_owner(uid):
                raise ApiError("只有所有者可以操作", 403)
            ctx = {"uid": uid, "admin": admin,
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


async def start_server():
    server = await asyncio.start_server(handle, CONF["host"], int(CONF["port"]))
    threading.Thread(target=_ipdb_loop, daemon=True).start()
    print(f"Mini App 接口已启动：http://{CONF['host']}:{CONF['port']}")
    return server

# ---------------------------------------------------------------- 多服务器监控（探针）
# 目标服务器上跑一个小脚本（web/probe.sh），每 5 秒把数据发到网站的 probe.php，
# 再转给本接口的 /probe。目标机器不用开任何端口。登记的服务器保存在 SERVERS_FILE。

SERVERS_FILE = CONF.get("servers_file") or "/root/nav_servers.json"
SERVER_LIMIT = 60
OFFLINE_AFTER = 30          # 超过这么多秒没收到数据就算离线
SERVERS = []                # [{"id","name","token","created"}]
LIVE = {}                   # id -> {"ts", "ip", "m": {...}}


def load_servers():
    global SERVERS
    try:
        with open(SERVERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        SERVERS = [x for x in data.get("servers", []) if x.get("id") and x.get("token")]
    except FileNotFoundError:
        SERVERS = []
    except Exception as e:
        print("多服务器列表读取失败：", e)
        SERVERS = []


def save_servers():
    tmp = SERVERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"servers": SERVERS}, f, ensure_ascii=False, indent=1)
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
        save_servers()
        return 200, {"ok": True, "cmd": "uninstall"}
    return 200, {"ok": True}


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0


def server_view(srv):
    live = LIVE.get(srv["id"])
    age = (time.time() - live["ts"]) if live else None
    out = {"id": srv["id"], "name": srv["name"], "uninstall": bool(srv.get("uninstall")), "online": bool(live and age < OFFLINE_AFTER),
           "age": int(age) if age is not None else None, "seen": bool(live)}
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


@action("servers", admin=True)
async def a_servers(ctx, p):
    items = [server_view(s) for s in my_servers(ctx["uid"])]
    return {"items": items, **servers_summary(ctx["uid"])}


@action("server_get", admin=True)
async def a_server_get(ctx, p):
    return server_view(_find_server(p, ctx["uid"]))


@action("server_edit", admin=True)
async def a_server_edit(ctx, p):
    op = p.get("op")
    if op == "add":
        if len(SERVERS) >= SERVER_LIMIT:
            raise ApiError(f"最多添加 {SERVER_LIMIT} 台服务器")
        name = clean(p.get("name"), 30)
        srv = {"id": secrets.token_hex(4), "name": name, "token": secrets.token_hex(16), "created": int(time.time()), "owner": ctx["uid"]}
        cmd = install_cmd(srv)                  # 先确认能生成命令，再保存
        SERVERS.append(srv)
        save_servers()
        return {"id": srv["id"], "cmd": cmd}
    srv = _find_server(p, ctx["uid"])
    if op == "rename":
        srv["name"] = clean(p.get("name"), 30)
        save_servers()
        return {"ok": True}
    if op == "del":
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



# ---------------------------------------------------------------- 首页按钮 / 欢迎语
# 在 nav_miniapp.json 里填 "web_url"（Mini App 的网址），机器人首页就会多一个「打开操作台」按钮，
# 新用户 /start 的欢迎页也会提示点它。web_url 留空则不改动机器人。

_orig_keyboard = dh.main_menu_keyboard
_orig_home_text = dh.home_text


def _web_url():
    url = (CONF.get("web_url") or "").strip()
    return url if url.startswith("https://") else ""


def main_menu_keyboard(user_id=None):
    markup = _orig_keyboard(user_id)
    url = _web_url()
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
