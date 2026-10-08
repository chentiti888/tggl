#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import unicodedata
import io
import json
import math
import os
import re
import errno
import shutil
import socket
import subprocess
import time
import html
import ipaddress
import platform
from datetime import datetime, timezone, timedelta
import urllib.error
import urllib.request
import urllib.parse
from urllib.parse import quote
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except Exception:
    PIL_OK = False
from telegram import (
    Update,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    TypeHandler,
    ApplicationHandlerStop,
    ContextTypes,
    filters,
)
CONFIG_FILE = os.environ.get("NAV_BOT_CONFIG", "/root/nav_bot_config.json")
CONFIG_TEMPLATE = {
    "bot_token": "",
    "admin_ids": [1715835996],
    "globalping_token": "",
    "divider_len": 19,
    "abuseipdb_key": "",
    "ipapi_is_key": "",
    "proxycheck_key": "",
}

def _load_config():
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(CONFIG_TEMPLATE, f, ensure_ascii=False, indent=2)
            os.chmod(CONFIG_FILE, 0o600)
        except Exception as e:
            print("生成配置文件模板失败：", e)
        return dict(CONFIG_TEMPLATE)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return dict(CONFIG_TEMPLATE, **json.load(f))
    except Exception as e:
        print("读取配置文件失败：", e)
        return dict(CONFIG_TEMPLATE)
CONFIG = _load_config()
BOT_TOKEN = os.environ.get("BOT_TOKEN") or CONFIG.get("bot_token") or ""
ADMIN_IDS = [
    int(x) for x in re.split(
        r"[,\s]+",
        os.environ.get("ADMIN_IDS")
        or ",".join(str(i) for i in CONFIG.get("admin_ids") or [])
    )
    if x.strip().isdigit()
]
DATA_FILE = "/root/nav_data.json"
DEFAULT_DATA = {
    "categories": {
        "AI工具": [
            {
                "name": "ChatGPT",
                "url": "https://chatgpt.com/"
            },
            {
                "name": "Gemini",
                "url": "https://gemini.google.com/"
            },
            {
                "name": "OpenRouter",
                "url": "https://openrouter.ai/"
            }
        ],
        "服务器": [
            {
                "name": "GitHub",
                "url": "https://github.com/"
            },
            {
                "name": "Vultr",
                "url": "https://www.vultr.com/"
            },
            {
                "name": "Linode",
                "url": "https://www.linode.com/"
            }
        ],
        "支付 / 交易": [
            {
                "name": "OKX",
                "url": "https://www.okx.com/"
            }
        ],
        "其他": [
            {
                "name": "Google",
                "url": "https://www.google.com/"
            }
        ]
    }
}

def load_data():
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA
    try:
        with open(
            DATA_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            result = json.load(f)
            if not isinstance(result, dict):
                raise ValueError("数据文件不是 JSON 对象")
            if not isinstance(result.get("categories"), dict):
                result["categories"] = {}
            return result
    except Exception as e:
        print("读取数据失败：", e)
        try:
            broken = f"{DATA_FILE}.corrupt-{time.strftime('%Y%m%d_%H%M%S')}"
            os.replace(DATA_FILE, broken)
            print("已把损坏的数据文件另存为：", broken)
        except Exception as e2:
            print("保存损坏数据文件失败：", e2)
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA

def save_data(data):
    temp_file = DATA_FILE + ".tmp"
    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )
        f.flush()
        os.fsync(f.fileno())
    os.replace(
        temp_file,
        DATA_FILE
    )
data = load_data()
if any([data.pop(k, None) is not None for k in ("monitors", "site_status", "site_check_last")]):
    save_data(data)

def is_owner(user_id):
    return user_id in ADMIN_IDS

def is_admin(user_id):
    # 授权用户和所有者权限完全一样（包括主机状态、管理后台）
    if user_id in ADMIN_IDS:
        return True
    users = data.get("allowed_users") if isinstance(data, dict) else None
    return isinstance(users, dict) and str(user_id) in users
PANELS = {}

async def _try_delete(message):
    try:
        await message.delete()
    except Exception as e:
        print("删除旧消息失败：", e)
try:
    DIVIDER = "━" * max(8, min(40, int(CONFIG.get("divider_len") or 19)))
except (TypeError, ValueError):
    DIVIDER = "━" * 19
_DIVIDER_SRC = "━" * 14

def _wide(text):
    return text.replace(_DIVIDER_SRC, DIVIDER) if isinstance(text, str) else text

async def send_page(
    message,
    text,
    reply_markup=None,
    parse_mode=ParseMode.HTML
):
    text = _wide(text)
    chat_id = message.chat_id
    old_id = PANELS.get(chat_id)
    sent = await message.reply_text(
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup
    )
    PANELS[chat_id] = sent.message_id
    if old_id and old_id != sent.message_id:
        try:
            await message.get_bot().delete_message(
                chat_id=chat_id,
                message_id=old_id
            )
        except Exception as e:
            print("删除旧面板失败：", e)
    return sent
ADMIN_STATES = {
    "add_category",
    "confirm_add_category",
    "add_site_name",
    "add_site_url",
    "confirm_add_site",
    "edit_name",
    "edit_url",
    "rename_category",
    "allow_user",
    "import_backup",
}

async def reply_panel(
    update,
    context,
    text,
    parse_mode=None,
    reply_markup=None
):
    text = _wide(text)
    message = update.message
    chat_id = message.chat_id
    bot = message.get_bot()
    if reply_markup is None:
        if context.user_data.get("state") in ADMIN_STATES:
            label, target = "❌ 取消", "admin|cancel"
        else:
            label, target = "🏠 返回首页", "home"
        reply_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    label,
                    callback_data=target
                )
            ]
        ])
    panel_id = PANELS.get(chat_id)
    if panel_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=panel_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            return
        except Exception as e:
            if "not modified" in str(e).lower():
                return
            print("更新面板失败，改发新消息：", e)
    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup
    )
    if panel_id:
        try:
            await bot.delete_message(
                chat_id=chat_id,
                message_id=panel_id
            )
        except Exception:
            pass
    PANELS[chat_id] = sent.message_id

async def edit_page(
    query,
    text=None,
    parse_mode=None,
    reply_markup=None
):
    text = _wide(text)
    message = query.message
    PANELS[message.chat_id] = message.message_id
    try:
        if message.photo:
            sent = await message.get_bot().send_message(
                chat_id=message.chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            PANELS[message.chat_id] = sent.message_id
            await _try_delete(message)
            return
        await query.edit_message_text(
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
    except Exception as e:
        if "not modified" in str(e).lower():
            return
        raise

def _rows(buttons, per_row=2):
    return [
        buttons[i:i + per_row]
        for i in range(0, len(buttons), per_row)
    ]

def main_menu_keyboard(user_id=None):
    """
    首页按钮：先是网站分类，再是网络工具（两两一排），最后是个人工具和管理。
    按钮名字统一成 4 个字左右，两列看起来整齐。
    """
    categories = data.get("categories", {})
    category_buttons = [
        InlineKeyboardButton(f"🪐 {category}", callback_data=f"cat|{category}")
        for category in categories
    ]
    keyboard = _rows(category_buttons, 2)
    admin = user_id is not None and is_admin(user_id)
    tools = user_id is not None and can_use_tools(user_id)
    if tools:
        keyboard.append([
            InlineKeyboardButton("🛰️ 全球 Ping", callback_data="global_ping"),
            InlineKeyboardButton("🧑‍💻 路由追踪", callback_data="route_trace"),
        ])
        keyboard.append([
            InlineKeyboardButton("🔍 端口扫描", callback_data="port_scan"),
            InlineKeyboardButton("🔬 I P 质量", callback_data="ipq"),
        ])
    if admin:
        keyboard.append([
            InlineKeyboardButton("🎫 记事本本", callback_data="notes"),
            InlineKeyboardButton("📟 主机状态", callback_data="status"),
        ])
        keyboard.append([InlineKeyboardButton("🗄️ 管理后台", callback_data="admin|back")])
    elif tools:
        keyboard.append([InlineKeyboardButton("🎫 记事本本", callback_data="notes")])
    return InlineKeyboardMarkup(keyboard)

def _greeting():
    hour = datetime.now(DISPLAY_TZ).hour
    if 5 <= hour < 11:
        return "☀️ 早上好"
    if 11 <= hour < 13:
        return "🌤 中午好"
    if 13 <= hour < 18:
        return "⛅ 下午好"
    return "🌙 晚上好"

# ===== 实时汇率（首页显示 USDT-USD-CNY）=====
# USDT/USD：Kraken 公共行情（取不到用 CoinGecko），首页停留时每 5 秒取一次；
# USD/CNY：CoinGecko 换算（取不到用 open.er-api.com），5 分钟取一次就够（外汇变化慢）。
# 后台每 60 秒也会更新一次，首页打开时直接读缓存，不会拖慢首页。
FX_REFRESH = 60
FX_USDT_TTL = 4
FX_CNY_TTL = 300
FX_CACHE = {}

def _fx_get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=6) as response:
        return json.loads(response.read().decode("utf-8"))

def _fx_fetch_usdt_usd():
    try:
        d = _fx_get_json("https://api.kraken.com/0/public/Ticker?pair=USDTUSD")
        ticker = next(iter(d["result"].values()))
        return float(ticker["c"][0])
    except Exception as e:
        print("USDT 价格（Kraken）获取失败：", e)
    try:
        return float(_fx_get_json(
            "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=usd"
        )["tether"]["usd"])
    except Exception as e:
        print("USDT 价格（CoinGecko）获取失败：", e)
    return None

def _fx_fetch_usd_cny():
    try:
        d = _fx_get_json("https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=usd,cny")["tether"]
        return float(d["cny"]) / float(d["usd"])
    except Exception as e:
        print("美元汇率（CoinGecko）获取失败：", e)
    try:
        return float(_fx_get_json("https://open.er-api.com/v6/latest/USD")["rates"]["CNY"])
    except Exception as e:
        print("美元汇率（备用）获取失败：", e)
    return None

def fx_refresh():
    """按需更新缓存（各自过期了才去取），多个人同时停在首页也只取一次。"""
    now = time.time()
    if now - FX_CACHE.get("usdt_time", 0) >= FX_USDT_TTL:
        FX_CACHE["usdt_time"] = now
        value = _fx_fetch_usdt_usd()
        if value:
            FX_CACHE["usdt_usd"] = value
            FX_CACHE["time"] = time.time()
    if now - FX_CACHE.get("cny_time", 0) >= FX_CNY_TTL:
        FX_CACHE["cny_time"] = now
        value = _fx_fetch_usd_cny()
        if value:
            FX_CACHE["usd_cny"] = value
            FX_CACHE["time"] = time.time()

async def fx_loop():
    while True:
        await asyncio.to_thread(fx_refresh)
        await asyncio.sleep(FX_REFRESH)

def fx_rate_line():
    """首页汇率那一行；还没取到数据（或超过 30 分钟没更新）就不显示。"""
    usd_cny = FX_CACHE.get("usd_cny")
    if not usd_cny or time.time() - FX_CACHE.get("time", 0) > 1800:
        return None
    usdt_usd = FX_CACHE.get("usdt_usd")
    if usdt_usd:
        return f"🪙 实时汇率　USDT = {usdt_usd * usd_cny:.4f} CNY"
    return f"🪙 实时汇率　USD = {usd_cny:.4f} CNY"

def home_text(welcome=False, user_id=None):
    """
    首页文字：标题 + 问候，下面三行概览（网站、工具、服务器），最后一句操作提示。
    文字尽量短，图片下方一屏就能看完。
    """
    categories = data.get("categories", {})
    category_count = len(categories)
    site_count = sum(len(sites) for sites in categories.values())
    title = "👋 <b>欢迎使用功能导航</b>" if welcome else "🧰 <b>功能导航</b>"
    lines = [
        f"{title}　{_greeting()}",
        "━━━━━━━━━━━━━━",
    ]
    if user_id is not None and can_use_tools(user_id):
        lines.append("⚙️ 网络工具　Ping · 路由 · 端口 · IP 质量")
    if user_id is not None and is_admin(user_id):
        try:
            with open("/proc/uptime") as f:
                uptime = float(f.read().split()[0])
            lines.append(f"🟢 服务器在线　已运行 {_fmt_duration(uptime)}")
        except Exception:
            pass
    return "\n".join(lines)

HOME_BANNER_FILE = "/root/nav_home_banner.jpg"

def home_banner():
    """首页顶部图片：放在服务器 /root/nav_home_banner.jpg；没有这个文件就返回 None。"""
    try:
        with open(HOME_BANNER_FILE, "rb") as f:
            return f.read()
    except Exception:
        return None

HOME_FRESH_REUSE = 20   # 秒：这段时间内重复 /start、主菜单，只改文字不重发图片
_HOME_FRESH = {}

HOME_LIVE = 5          # 停在首页时，汇率每 5 秒刷新一次
HOME_LIVE_MAX = 600    # 最多连续刷新 10 分钟，之后停下（重新进首页会继续）
_HOME_LIVE_TASKS = {}

def stop_home_live(chat_id):
    """离开首页（点了别的按钮 / 发了消息 / 命令）时调用，停止汇率刷新。"""
    task = _HOME_LIVE_TASKS.pop(chat_id, None)
    if task and not task.done() and task is not asyncio.current_task():
        task.cancel()

async def _home_live_loop(bot, chat_id, message_id, user_id, welcome, is_photo):
    began = time.time()
    last_text = _wide(home_text(welcome, user_id))  # 当前首页上显示的文字
    try:
        while time.time() - began < HOME_LIVE_MAX:
            await asyncio.sleep(HOME_LIVE)
            if PANELS.get(chat_id) != message_id:
                return  # 首页已经换成别的消息了
            await asyncio.to_thread(fx_refresh)
            if PANELS.get(chat_id) != message_id or _HOME_LIVE_TASKS.get(chat_id) is not asyncio.current_task():
                return
            text = _wide(home_text(welcome, user_id))
            if text == last_text:
                continue  # 汇率（和其他文字）都没变，不发请求
            markup = main_menu_keyboard(user_id)
            try:
                if is_photo:
                    await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=text,
                                                   parse_mode=ParseMode.HTML, reply_markup=markup)
                else:
                    await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                                                parse_mode=ParseMode.HTML, reply_markup=markup)
                last_text = text
            except Exception as e:
                error = str(e).lower()
                if "not modified" in error:
                    last_text = text
                    continue
                if "retry" in error or "flood" in error:
                    await asyncio.sleep(5)
                    continue
                print("首页汇率刷新停止：", e)
                return
    except asyncio.CancelledError:
        pass
    finally:
        if _HOME_LIVE_TASKS.get(chat_id) is asyncio.current_task():
            _HOME_LIVE_TASKS.pop(chat_id, None)

def start_home_live(bot, chat_id, user_id, welcome, is_photo):
    stop_home_live(chat_id)
    message_id = PANELS.get(chat_id)
    if message_id:
        _HOME_LIVE_TASKS[chat_id] = asyncio.create_task(
            _home_live_loop(bot, chat_id, message_id, user_id, welcome, is_photo)
        )

async def show_home(bot, chat_id, user_id, welcome=False, fresh=False):
    """
    首页：顶部「工具箱」图片 + 下方文字和菜单按钮。
    fresh=True：一定发一条新消息（再删掉旧面板），不在旧消息上改。
    用在 /start、/menu、随手发文字这些“重新进入”的场合：
    用户清空过聊天记录时，旧面板在用户这边已经看不到了，但机器人那边还能改它，
    如果还去改旧消息，用户就什么都看不到（看起来像消息被自动删除了）。
    """
    recent = _HOME_FRESH.get(chat_id)
    if (
        fresh and recent and PANELS.get(chat_id) == recent[0]
        and time.time() - recent[1] < HOME_FRESH_REUSE
    ):
        # 刚用 /start、主菜单发过首页图（几秒内又点）：旧首页肯定还看得见，
        # 只改文字和按钮，不再重发图片（连续重发图片 + 删旧消息，手机端会卡在转圈）
        if await panel_caption(bot, chat_id, home_text(welcome, user_id), main_menu_keyboard(user_id)):
            return
    old_id = PANELS.pop(chat_id, None) if fresh else None
    banner = home_banner()
    if banner:
        await panel_photo(bot, chat_id, banner, home_text(welcome, user_id), main_menu_keyboard(user_id))
        if fresh:
            _HOME_FRESH[chat_id] = (PANELS.get(chat_id), time.time())
    else:
        # 没放图片：首页用纯文字显示，功能不受影响
        await _panel_show(bot, chat_id, home_text(welcome, user_id), main_menu_keyboard(user_id))
    if old_id and old_id != PANELS.get(chat_id):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_id)
        except Exception:
            pass

async def show_main_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if update.callback_query:
        message = update.callback_query.message
        PANELS[message.chat_id] = message.message_id
    else:
        message = update.message
    await show_home(message.get_bot(), message.chat_id, update.effective_user.id,
                    fresh=update.callback_query is None)

# 按钮字体（iOS 苹方/SF）各字符大致宽度，单位 1/1000 em
_SITE_CHAR_W = {}
for _chars, _w in (
    ("ijl'|", 222), (":;", 173), ("ftI!.,/ ", 278), ("r-()[]", 333),
    # 数字按三张实机截图逐个校准：iOS 的数字不等宽（1 最窄，8、9 最宽）
    ("0", 576), ("1", 346), ("2", 585), ("3", 548), ("4", 514), ("5", 553), ("6", 597), ("7", 473), ("8", 642), ("9", 621),
    ("cksvxyzJ", 500), ("abdeghnopqu_?$#", 556),
    ("FTZ", 611), ("ABEKPSVXY&", 667), ("CDHNRUw", 722),
    ("GOQ", 778), ("mM", 833), ("%", 889), ("W@", 944),
):
    for _c in _chars:
        _SITE_CHAR_W[_c] = _w

# 以下系数按 iOS 实机截图反推（英文实际比上表宽约 12.8%，半角空格≈0.485em，1/6 空格≈0.154em）
_SITE_ASCII_SCALE = 1.128
_SITE_PAD = (("\u3000", 1000), ("\u2002", 485), ("\u2006", 154))

def _site_name_width(name):
    total = 0
    for ch in name:
        if unicodedata.east_asian_width(ch) in "WF":
            total += 1000
        else:
            total += _SITE_CHAR_W.get(ch, 556) * _SITE_ASCII_SCALE
    return total

def _site_left_labels(names):
    # Telegram 按钮文字只能居中：把每个名字后面补空白到同样宽度，图标就固定在左边对齐
    widths = [_site_name_width(n) for n in names]
    target = max(widths, default=0)
    labels = []
    for name, width in zip(names, widths):
        gap = target - width
        pad = ""
        for ch, w in _SITE_PAD[:-1]:
            n = int(gap // w)
            pad += ch * n
            gap -= n * w
        ch, w = _SITE_PAD[-1]
        pad += ch * round(gap / w)
        labels.append(f"🌐 {name}{pad}\u2800")
    return labels

def _pad_best(gap):
    """用 全角 / 半角 / 1/6 三种空格拼出最接近 gap 的宽度，返回 (误差, 空格串)。"""
    (c3, w3), (c2, w2), (c6, w6) = _SITE_PAD
    best = (abs(gap), "")
    base = max(0, int(gap // w3) - 2)
    for n3 in range(base, base + 3):
        for n2 in range(0, 5):
            for n6 in range(0, 7):  # 小空格最多 6 个，用多了误差会累积
                err = abs(gap - n3 * w3 - n2 * w2 - n6 * w6)
                if err < best[0]:
                    best = (err, c3 * n3 + c2 * n2 + c6 * n6)
    return best

def _pad_fill(gap):
    return _pad_best(gap)[1]

def _fit_width(text, cap):
    """名字太长就截断加「…」，保证不超过按钮能放下的宽度（超出会被 Telegram 截断，就对不齐了）。"""
    if _site_name_width(text) <= cap:
        return text
    while text and _site_name_width(text + "…") > cap:
        text = text[:-1]
    return text + "…"

def _left_labels(texts, prefix="▪ ", cap=8800):
    """
    按钮文字补空白到同样宽度，让前面的图标固定靠左对齐（和网站按钮同一套宽度表）。
    cap：每个名字最多占多宽（单位 1/1000 字宽）；两个一行的按钮约 8800（再长会被 Telegram 截断），一行一个约 20000。
    """
    texts = [_fit_width(t, cap) for t in texts]
    widths = [_site_name_width(t) for t in texts]
    top = max(widths, default=0)
    # 统一宽度不一定取最长那个：在稍宽一点的范围里找一个让所有按钮误差都最小的宽度
    target = min(
        range(int(top), int(top) + 400, 5),
        key=lambda t: max((_pad_best(t - w)[0] for w in widths), default=0)
    ) if widths else 0
    return [f"{prefix}{text}{_pad_fill(target - width)}\u2800" for text, width in zip(texts, widths)]

def _left_rows(buttons, tail):
    """两个一行；数量为单数时最后一个和 tail 按钮放同一行，免得单独一行变宽对不齐。"""
    rows = _rows(buttons, 2)
    if rows and len(rows[-1]) == 1:
        rows[-1].append(tail)
    else:
        rows.append([tail])
    return rows

def category_keyboard(category):
    websites = data["categories"].get(
        category,
        []
    )
    labels = _site_left_labels([site['name'] for site in websites])
    site_buttons = [
        InlineKeyboardButton(
            labels[index],
            callback_data=f"site|{category}|{index}"
        )
        for index in range(len(websites))
    ]
    keyboard = _rows(site_buttons, 2)
    home_button = InlineKeyboardButton(
        "⬅️ 返回首页",
        callback_data="home"
    )
    if keyboard and len(keyboard[-1]) == 1:
        # 网站数量为单数时，最后一个网站和返回首页放同一行，保持左对齐
        keyboard[-1].append(home_button)
    else:
        keyboard.append([home_button])
    return InlineKeyboardMarkup(keyboard)

async def show_category(
    query,
    category
):
    websites = data["categories"].get(
        category,
        []
    )
    if not websites:
        text = (
            f"📂 <b>{html.escape(category)}</b>\n"
            "━━━━━━━━━━━━━━\n"
            "📭 暂无网址"
        )
    else:
        text = (
            f"📂 <b>{html.escape(category)}</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"🔗 共 <b>{len(websites)}</b> 个网站\n\n"
            "👇 请选择要访问的网站"
        )
    await edit_page(
        query,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=category_keyboard(category)
    )

def site_keyboard(
    category,
    index
):
    site = data["categories"][category][index]
    url = site["url"]
    translate_url = (
        "https://translate.google.com/translate"
        "?sl=auto"
        "&tl=zh-CN"
        "&u=" + quote(
            url,
            safe=""
        )
    )
    keyboard = [
        [
            InlineKeyboardButton(
                "🌐 打开网站",
                url=url
            )
        ],
        [
            InlineKeyboardButton(
                "🇨🇳 中文翻译",
                url=translate_url
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ 返回分类",
                callback_data=f"cat|{category}"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 返回首页",
                callback_data="home"
            )
        ]
    ]
    return InlineKeyboardMarkup(
        keyboard
    )

async def show_site(
    query,
    category,
    index
):
    websites = data["categories"].get(
        category,
        []
    )
    if (
        index < 0
        or index >= len(websites)
    ):
        await query.answer(
            "网址不存在",
            show_alert=True
        )
        return
    site = websites[index]
    text = (
        f"🌐 <b>{html.escape(site['name'])}</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"📂 分类：{html.escape(category)}\n"
        f"🔗 {html.escape(site['url'])}\n"
        + "\n👇 请选择操作"
    )
    await edit_page(query,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=site_keyboard(
            category,
            index
        )
    )

def admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ 添加分类",
                callback_data="admin|add_category"
            )
        ],
        [
            InlineKeyboardButton(
                "➕ 添加网址",
                callback_data="admin|add_site"
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ 编辑网址",
                callback_data="admin|edit_site"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 删除网址",
                callback_data="admin|delete_site"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 查看网址",
                callback_data="admin|list"
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ 修改分类",
                callback_data="admin|rename_category"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 删除分类",
                callback_data="admin|delete_category"
            )
        ],
        [
            InlineKeyboardButton("🗂 旧版本管理", callback_data="upd|olds"),
            InlineKeyboardButton("👥 授权用户", callback_data="admin|users"),
        ],
        [
            InlineKeyboardButton("💾 导出备份", callback_data="admin|export"),
            InlineKeyboardButton("📥 导入备份", callback_data="admin|import"),
        ],
        [
            InlineKeyboardButton("🖥 主机状态", callback_data="status"),
            InlineKeyboardButton("⬆️ 版本更新", callback_data="upd|home"),
        ],
        [
            InlineKeyboardButton("📊 探针额度", callback_data="admin|quota"),
        ],
        [
            InlineKeyboardButton(
                "🏠 返回首页",
                callback_data="home"
            )
        ]
    ])

GP_PER_TEST = 20  # 一次全球 Ping 大约消耗的测试次数（18 个节点 + 偶尔换探针）

def gp_quota_text():
    """后台「探针额度」页：查 Globalping /v1/limits（这个接口本身不扣额度）。"""
    head = "📊 <b>探针额度</b>\n━━━━━━━━━━━━━━\n"
    headers = {"User-Agent": "Mozilla/5.0"}
    if GLOBALPING_TOKEN:
        headers["Authorization"] = "Bearer " + GLOBALPING_TOKEN
    try:
        request = urllib.request.Request("https://api.globalping.io/v1/limits", headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            d = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return head + f"❌ 查询失败：<code>{html.escape(str(e)[:120])}</code>"
    create = ((d.get("rateLimit") or {}).get("measurements") or {}).get("create") or {}
    limit = create.get("limit")
    remaining = create.get("remaining")
    reset = create.get("reset")
    credits = (d.get("credits") or {}).get("remaining")
    account = "已登录（token）" if create.get("type") == "user" else "未登录（匿名额度）"
    lines = [head.rstrip("\n"), f"🔑 账号　{account}"]
    if limit is not None and remaining is not None:
        lines.append(f"⏳ 本小时　剩余 <b>{remaining}</b> / {limit} 次")
    if reset:
        minutes = max(1, round(int(reset) / 60))
        lines.append(f"🔄 恢复　约 {minutes} 分钟后重置")
    if credits is not None:
        lines.append(f"💳 积分　<b>{credits}</b>")
    total = (remaining or 0) + (credits or 0)
    lines.append(f"🛰️ 现在约可测全球 Ping <b>{total // GP_PER_TEST}</b> 次")
    lines.append("\n▪ 每次全球 Ping 约消耗 18~25 次，三网路由约 9 次")
    lines.append("▪ 积分只在本小时额度用完后才扣")
    return "\n".join(lines)

async def show_admin_menu(
    query=None,
    message=None
):
    text = (
        "⚙️ <b>管理员后台</b>\n"
        "━━━━━━━━━━━━━━\n"
        "👇 请选择操作"
    )
    if query:
        await edit_page(query,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu_keyboard()
        )
    elif message:
        await send_page(
            message,
            text=text,
            reply_markup=admin_menu_keyboard()
        )

async def admin_add_category(
    query,
    context
):
    context.user_data["state"] = "add_category"
    await edit_page(query,
        "➕ <b>添加分类</b>\n\n"
        "请直接输入新的分类名称。\n\n"
        "例如：\n"
        "影视\n"
        "机场\n"
        "AI工具\n"
        "服务器\n\n"
        "输入后会进入确认页面。",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ 取消",
                    callback_data="admin|cancel"
                )
            ]
        ])
    )

async def admin_add_site(
    query,
    context
):
    categories = data["categories"]
    if not categories:
        await edit_page(query,
            "❌ 当前没有任何分类。\n\n"
            "请先添加分类。",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "➕ 添加分类",
                        callback_data="admin|add_category"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ 返回",
                        callback_data="admin|back"
                    )
                ]
            ])
        )
        return
    keyboard = []
    for category in categories:
        keyboard.append([
            InlineKeyboardButton(
                f"📂 {category}",
                callback_data=f"addsitecat|{category}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton(
            "❌ 取消",
            callback_data="admin|back"
        )
    ])
    await edit_page(query,
        "➕ <b>添加网址</b>\n\n"
        "第 1 步：请选择网址所属分类。",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

async def add_site_category(
    query,
    context
):
    category = query.data.split(
        "|",
        1
    )[1]
    context.user_data["state"] = "add_site_name"
    context.user_data["new_category"] = category
    await edit_page(query,
        f"➕ <b>添加网址</b>\n\n"
        f"📂 分类：<b>{html.escape(category)}</b>\n\n"
        "第 2 步：请输入网站名称。\n\n"
        "例如：\n"
        "GitHub\n"
        "ChatGPT\n"
        "Google",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ 取消",
                    callback_data="admin|cancel"
                )
            ]
        ])
    )

async def add_site_name(
    update,
    context
):
    name = update.message.text.strip()
    if not name:
        await reply_panel(
            update, context,
            "❌ 网站名称不能为空，请重新输入。"
        )
        return
    context.user_data["new_name"] = name
    context.user_data["state"] = "add_site_url"
    await reply_panel(
        update, context,
        "🔗 <b>第 3 步：请输入网站地址</b>\n\n"
        "例如：\n"
        "<code>https://github.com/</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ 取消",
                    callback_data="admin|cancel"
                )
            ]
        ])
    )

async def add_site_url(
    update,
    context
):
    url = update.message.text.strip()
    if not (
        url.startswith("http://")
        or url.startswith("https://")
    ):
        await reply_panel(
            update, context,
            "❌ 地址格式不正确。\n\n"
            "网址必须以 http:// 或 https:// 开头。\n\n"
            "请重新输入："
        )
        return
    context.user_data["new_url"] = url
    category = context.user_data["new_category"]
    name = context.user_data["new_name"]
    text = (
        "✅ <b>请确认添加</b>\n\n"
        f"📂 分类：{html.escape(category)}\n"
        f"🌐 名称：{html.escape(name)}\n"
        f"🔗 地址：{html.escape(url)}\n\n"
        "确定添加吗？"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ 确认添加",
                callback_data="confirm|add_site"
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ 重新输入",
                callback_data="admin|add_site"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ 取消",
                callback_data="admin|cancel"
            )
        ]
    ])
    await reply_panel(
        update, context,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    context.user_data["state"] = "confirm_add_site"

async def confirm_add_site(
    query,
    context
):
    category = context.user_data.get(
        "new_category"
    )
    name = context.user_data.get(
        "new_name"
    )
    url = context.user_data.get(
        "new_url"
    )
    if not category or not name or not url:
        await edit_page(query,
            "❌ 添加信息已经失效，请重新操作。"
        )
        return
    data["categories"][category].append({
        "name": name,
        "url": url
    })
    save_data(data)
    context.user_data.clear()
    await edit_page(query,
        "✅ <b>网址添加成功！</b>\n\n"
        f"📂 分类：{html.escape(category)}\n"
        f"🌐 名称：{html.escape(name)}\n"
        f"🔗 地址：{html.escape(url)}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➕ 继续添加",
                    callback_data="admin|add_site"
                )
            ],
            [
                InlineKeyboardButton(
                    "⚙️ 管理后台",
                    callback_data="admin|back"
                )
            ]
        ])
    )

async def admin_edit_site(
    query
):
    categories = data["categories"]
    keyboard = []
    for category in categories:
        if categories[category]:
            keyboard.append([
                InlineKeyboardButton(
                    f"📂 {category}",
                    callback_data=f"editcat|{category}"
                )
            ])
    keyboard.append([
        InlineKeyboardButton(
            "⬅️ 返回",
            callback_data="admin|back"
        )
    ])
    if not keyboard or (
        len(keyboard) == 1
    ):
        await edit_page(query,
            "❌ 当前没有可编辑的网址。",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ 返回",
                        callback_data="admin|back"
                    )
                ]
            ])
        )
        return
    await edit_page(query,
        "✏️ <b>编辑网址</b>\n\n"
        "第 1 步：请选择分类。",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

async def edit_category(
    query
):
    category = query.data.split(
        "|",
        1
    )[1]
    websites = data["categories"].get(
        category,
        []
    )
    keyboard = []
    for index, site in enumerate(websites):
        keyboard.append([
            InlineKeyboardButton(
                f"🌐 {site['name']}",
                callback_data=f"edit|{category}|{index}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton(
            "⬅️ 返回",
            callback_data="admin|edit_site"
        )
    ])
    await edit_page(query,
        f"✏️ <b>{html.escape(category)}</b>\n\n"
        "第 2 步：选择要编辑的网址。",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

async def edit_site_menu(
    query,
    context
):
    parts = query.data.split("|")
    category = parts[1]
    index = int(parts[2])
    site = data["categories"][category][index]
    context.user_data["edit_category"] = category
    context.user_data["edit_index"] = index
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✏️ 修改名称",
                callback_data="editaction|name"
            )
        ],
        [
            InlineKeyboardButton(
                "🔗 修改网址",
                callback_data="editaction|url"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ 返回",
                callback_data=f"editcat|{category}"
            )
        ]
    ])
    await edit_page(query,
        "✏️ <b>编辑网址</b>\n\n"
        f"📂 分类：{html.escape(category)}\n"
        f"🌐 名称：{html.escape(site['name'])}\n"
        f"🔗 地址：{html.escape(site['url'])}\n\n"
        "请选择修改项目：",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

async def edit_action(
    query,
    context
):
    action = query.data.split(
        "|",
        1
    )[1]
    category = context.user_data.get(
        "edit_category"
    )
    index = context.user_data.get(
        "edit_index"
    )
    if category is None or index is None:
        await query.answer(
            "编辑状态已失效，请重新操作。",
            show_alert=True
        )
        return
    if action == "name":
        context.user_data["state"] = "edit_name"
        await edit_page(query,
            "✏️ <b>修改网站名称</b>\n\n"
            "请输入新的名称：",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ 取消",
                        callback_data="admin|cancel"
                    )
                ]
            ])
        )
    elif action == "url":
        context.user_data["state"] = "edit_url"
        await edit_page(query,
            "🔗 <b>修改网站地址</b>\n\n"
            "请输入新的网址：",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ 取消",
                        callback_data="admin|cancel"
                    )
                ]
            ])
        )

async def handle_edit_name(
    update,
    context
):
    name = update.message.text.strip()
    if not name:
        await reply_panel(
            update, context,
            "❌ 名称不能为空，请重新输入。"
        )
        return
    category = context.user_data.get(
        "edit_category"
    )
    index = context.user_data.get(
        "edit_index"
    )
    data["categories"][category][index]["name"] = name
    save_data(data)
    context.user_data.clear()
    await reply_panel(
        update, context,
        "✅ 网站名称修改成功！",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚙️ 管理后台",
                    callback_data="admin|back"
                )
            ]
        ])
    )

async def handle_edit_url(
    update,
    context
):
    url = update.message.text.strip()
    if not (
        url.startswith("http://")
        or url.startswith("https://")
    ):
        await reply_panel(
            update, context,
            "❌ 地址格式不正确。\n\n"
            "必须以 http:// 或 https:// 开头。\n\n"
            "请重新输入："
        )
        return
    category = context.user_data.get(
        "edit_category"
    )
    index = context.user_data.get(
        "edit_index"
    )
    data["categories"][category][index]["url"] = url
    save_data(data)
    context.user_data.clear()
    await reply_panel(
        update, context,
        "✅ 网站地址修改成功！",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚙️ 管理后台",
                    callback_data="admin|back"
                )
            ]
        ])
    )

async def admin_delete_site(
    query
):
    keyboard = []
    for category in data["categories"]:
        if data["categories"][category]:
            keyboard.append([
                InlineKeyboardButton(
                    f"📂 {category}",
                    callback_data=f"delcat|{category}"
                )
            ])
    keyboard.append([
        InlineKeyboardButton(
            "⬅️ 返回",
            callback_data="admin|back"
        )
    ])
    await edit_page(query,
        "🗑 <b>删除网址</b>\n\n"
        "第 1 步：请选择分类。",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

async def delete_category_select(
    query
):
    category = query.data.split(
        "|",
        1
    )[1]
    websites = data["categories"].get(
        category,
        []
    )
    keyboard = []
    for index, site in enumerate(websites):
        keyboard.append([
            InlineKeyboardButton(
                f"🗑 {site['name']}",
                callback_data=f"delete|{category}|{index}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton(
            "⬅️ 返回",
            callback_data="admin|delete_site"
        )
    ])
    await edit_page(query,
        f"🗑 <b>{html.escape(category)}</b>\n\n"
        "第 2 步：选择要删除的网址。",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

async def delete_site_confirm(
    query,
    context
):
    parts = query.data.split("|")
    category = parts[1]
    index = int(parts[2])
    websites = data["categories"].get(
        category,
        []
    )
    if index >= len(websites):
        await query.answer(
            "网址不存在。",
            show_alert=True
        )
        return
    site = websites[index]
    context.user_data["delete_category"] = category
    context.user_data["delete_index"] = index
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🗑 确认删除",
                callback_data="confirm|delete_site"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ 取消",
                callback_data=f"delcat|{category}"
            )
        ]
    ])
    await edit_page(query,
        "⚠️ <b>确认删除？</b>\n\n"
        f"📂 分类：{html.escape(category)}\n"
        f"🌐 网站：{html.escape(site['name'])}\n"
        f"🔗 地址：{html.escape(site['url'])}\n\n"
        "删除后无法恢复。",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

async def confirm_delete_site(
    query,
    context
):
    category = context.user_data.get(
        "delete_category"
    )
    index = context.user_data.get(
        "delete_index"
    )
    if category is None or index is None:
        await query.answer(
            "操作已经失效。",
            show_alert=True
        )
        return
    websites = data["categories"].get(
        category,
        []
    )
    if index >= len(websites):
        await query.answer(
            "网址不存在。",
            show_alert=True
        )
        return
    site = websites.pop(index)
    save_data(data)
    context.user_data.clear()
    await edit_page(query,
        "✅ <b>删除成功！</b>\n\n"
        f"已删除：{html.escape(site['name'])}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚙️ 管理后台",
                    callback_data="admin|back"
                )
            ]
        ])
    )

async def admin_list(
    query
):
    text = "📋 <b>当前网址</b>\n\n"
    total = 0
    for category, websites in data["categories"].items():
        text += (
            f"📂 <b>{html.escape(category)}</b>"
            f"（{len(websites)}）\n"
        )
        for site in websites:
            text += (
                f"  • {html.escape(site['name'])}\n"
            )
            total += 1
        text += "\n"
    text += f"共 {total} 个网址"
    await edit_page(query,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚙️ 管理后台",
                    callback_data="admin|back"
                )
            ]
        ])
    )

async def admin_delete_category(
    query
):
    categories = data["categories"]
    keyboard = []
    for category in categories:
        keyboard.append([
            InlineKeyboardButton(
                f"🗑 {category}",
                callback_data=f"delcategory|{category}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton(
            "⬅️ 返回",
            callback_data="admin|back"
        )
    ])
    await edit_page(query,
        "🗑 <b>删除分类</b>\n\n"
        "⚠️ 删除分类会同时删除该分类下面的所有网址。\n\n"
        "请选择分类：",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

async def delete_category_confirm(
    query
):
    category = query.data.split(
        "|",
        1
    )[1]
    if category not in data["categories"]:
        await query.answer(
            "分类不存在。",
            show_alert=True
        )
        return
    count = len(
        data["categories"][category]
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🗑 确认删除",
                callback_data=f"confirmcategory|{category}"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ 取消",
                callback_data="admin|delete_category"
            )
        ]
    ])
    await edit_page(query,
        "⚠️ <b>确认删除分类？</b>\n\n"
        f"📂 分类：{html.escape(category)}\n"
        f"🌐 包含网址：{count} 个\n\n"
        "删除分类后，该分类和里面的网址都会被删除。",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

async def confirm_delete_category(
    query
):
    category = query.data.split(
        "|",
        1
    )[1]
    if category not in data["categories"]:
        await query.answer(
            "分类不存在。",
            show_alert=True
        )
        return
    del data["categories"][category]
    save_data(data)
    await edit_page(query,
        "✅ <b>分类删除成功！</b>\n\n"
        f"已删除：{html.escape(category)}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚙️ 管理后台",
                    callback_data="admin|back"
                )
            ]
        ])
    )

async def admin_rename_category(
    query
):
    categories = data["categories"]
    keyboard = []
    for category in categories:
        keyboard.append([
            InlineKeyboardButton(
                f"✏️ {category}",
                callback_data=f"renamecat|{category}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton(
            "⬅️ 返回",
            callback_data="admin|back"
        )
    ])
    await edit_page(query,
        "✏️ <b>修改分类</b>\n\n"
        "修改名称后，该分类下的网址会保持不变。\n\n"
        "请选择要修改的分类：",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

async def rename_category_select(
    query,
    context
):
    category = query.data.split(
        "|",
        1
    )[1]
    if category not in data["categories"]:
        await query.answer(
            "分类不存在。",
            show_alert=True
        )
        return
    context.user_data["state"] = "rename_category"
    context.user_data["rename_old_category"] = category
    await edit_page(query,
        "✏️ <b>修改分类名称</b>\n\n"
        f"当前名称：<b>{html.escape(category)}</b>\n\n"
        "请直接输入新的分类名称。",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ 取消",
                    callback_data="admin|cancel"
                )
            ]
        ])
    )

async def handle_rename_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    old_name = context.user_data.get(
        "rename_old_category"
    )
    if not old_name or old_name not in data["categories"]:
        context.user_data.clear()
        await reply_panel(
            update, context,
            "❌ 操作已经失效，请重新进入管理后台。"
        )
        return
    new_name = update.message.text.strip()
    if not new_name:
        await reply_panel(
            update, context,
            "❌ 分类名称不能为空，请重新输入。"
        )
        return
    bad = _category_name_error(new_name)
    if bad:
        await reply_panel(update, context, bad)
        return
    if new_name == old_name:
        await reply_panel(
            update, context,
            "❌ 新名称和原名称相同，请重新输入。"
        )
        return
    if new_name in data["categories"]:
        await reply_panel(
            update, context,
            "❌ 这个分类已经存在，请换一个名称。"
        )
        return
    data["categories"] = {
        (new_name if name == old_name else name): sites
        for name, sites in data["categories"].items()
    }
    save_data(data)
    context.user_data.clear()
    await reply_panel(
        update, context,
        "✅ <b>分类修改成功！</b>\n\n"
        f"📂 {html.escape(old_name)} ➜ {html.escape(new_name)}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✏️ 继续修改分类",
                    callback_data="admin|rename_category"
                )
            ],
            [
                InlineKeyboardButton(
                    "⚙️ 管理后台",
                    callback_data="admin|back"
                )
            ]
        ])
    )
COMMON_PORTS = [
    21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 443, 445, 465,
    587, 993, 995, 1080, 1433, 1521, 2049, 2082, 2083, 3000, 3306,
    3389, 5432, 5900, 5984, 6379, 7001, 8000, 8008, 8080, 8081, 8443,
    8888, 9000, 9090, 9200, 10000, 11211, 27017,
]
PORT_SERVICES = {
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
    80: "HTTP", 110: "POP3", 111: "RPC", 135: "MSRPC",
    139: "NetBIOS", 143: "IMAP", 443: "HTTPS", 445: "SMB",
    465: "SMTPS", 587: "SMTP 提交", 993: "IMAPS", 995: "POP3S",
    1080: "SOCKS", 1433: "MSSQL", 1521: "Oracle", 2049: "NFS",
    2082: "cPanel", 2083: "cPanel SSL", 3000: "开发服务",
    3306: "MySQL", 3389: "RDP 远程桌面", 5432: "PostgreSQL",
    5900: "VNC", 5984: "CouchDB", 6379: "Redis", 7001: "WebLogic",
    8000: "HTTP 备用", 8008: "HTTP 备用", 8080: "HTTP 代理",
    8081: "HTTP 备用", 8443: "HTTPS 备用", 8888: "HTTP 备用",
    9000: "常见管理面板", 9090: "常见管理面板", 9200: "Elasticsearch",
    10000: "Webmin", 11211: "Memcached", 27017: "MongoDB",
}
MAX_SCAN_PORTS = 65535
SCAN_CONCURRENCY = 500  # 同时扫描的连接数（原来 1000，太快容易被对方防火墙限速漏扫）
SCAN_TIMEOUT = 1.5
SCAN_TIMEOUT_LARGE = 1.0
SCAN_JOBS = {}

def _parse_port_spec(text):
    text = text.replace("，", ",").replace("、", ",").strip()
    if text.lower() in ("常用", "common"):
        return list(COMMON_PORTS), None
    if text.lower() in ("全部", "全端口", "all"):
        return list(range(1, 65536)), None
    ports = set()
    for token in re.split(r"[,\s]+", text):
        if not token:
            continue
        match = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?", token)
        if not match:
            return None, f"端口格式不正确：{token}"
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end > 65535 or start > end:
            return None, f"端口范围不正确：{token}"
        ports.update(range(start, end + 1))
        if len(ports) > MAX_SCAN_PORTS:
            return None, f"一次最多扫描 {MAX_SCAN_PORTS} 个端口"
    if not ports:
        return None, "没有可扫描的端口"
    return sorted(ports), None

def _scan_concurrency():
    limit = 1024
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        wanted = 4096
        if hard != resource.RLIM_INFINITY:
            wanted = min(wanted, hard)
        if soft < wanted:
            resource.setrlimit(
                resource.RLIMIT_NOFILE,
                (wanted, hard)
            )
            soft = wanted
        limit = soft
    except Exception:
        pass
    return max(50, min(SCAN_CONCURRENCY, limit - 300))
_SCAN_SLOTS = {}

def _scan_slots():
    """全局共用的扫描连接名额（第一次用时按系统句柄上限创建）。"""
    if "sem" not in _SCAN_SLOTS:
        _SCAN_SLOTS["sem"] = asyncio.Semaphore(_scan_concurrency())
    return _SCAN_SLOTS["sem"]

def _scan_bar(done, total, width=10):
    """所有进度条统一样式：10 格，■ 表示已完成、░ 表示未完成（和主机状态一样）。"""
    full, empty = STATUS_BAR_FULL, STATUS_BAR_EMPTY
    if total <= 0:
        return full * width, 100
    filled = int(width * done / total)  # 向下取整：没完成前不会显示满格
    filled = max(0, min(width, filled))
    return (
        full * filled + empty * (width - filled),
        int(done * 100 / total)
    )

def _scan_open_lines(open_ports, limit=60):
    """开放端口列表：每行 🟢 + 等宽的「端口号 服务名」，端口号补齐到同样宽度，服务名对齐。"""
    lines = []
    for port in sorted(open_ports)[:limit]:
        service = PORT_SERVICES.get(port, "")
        text = f"{port:<6}{service}".rstrip()
        lines.append(f"🟢 <code>{html.escape(text)}</code>")
    if len(open_ports) > limit:
        lines.append(f"▪ 另有 {len(open_ports) - limit} 个未列出")
    return "\n".join(lines)

# 端口扫描专用进度条：蓝色填充 + 小白方块底（其他地方的进度条仍是 ■░）
SCAN_BAR_FULL = "🟦"
SCAN_BAR_EMPTY = "▫️"

def _port_scan_bar(done, total, width=10):
    if total <= 0:
        return SCAN_BAR_FULL * width, 100
    filled = max(0, min(width, int(width * done / total)))
    return SCAN_BAR_FULL * filled + SCAN_BAR_EMPTY * (width - filled), int(done * 100 / total)

def _scan_progress_text(label, done, total, open_ports, elapsed):
    bar, percent = _port_scan_bar(done, total)
    if open_ports:
        shown = sorted(open_ports)[:25]
        found = " ".join(str(p) for p in shown)
        if len(open_ports) > 25:
            found += f" …共 {len(open_ports)} 个"
        found_line = f"🟢 已发现：<code>{html.escape(found)}</code>"
    else:
        found_line = "▪ 暂未发现开放端口"
    return (
        "📡 <b>正在扫描端口……</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"🎯 目标：<code>{html.escape(label)}</code>\n\n"
        f"{bar} {percent}%\n"
        f"🧩 {done}/{total} 个端口　⏱ {elapsed:.0f}s\n\n"
        f"{found_line}"
    )

def _scan_result_text(
    label,
    done,
    total,
    open_ports,
    elapsed,
    cancelled
):
    bar, percent = _port_scan_bar(done, total)
    title = "⏹ <b>端口扫描已取消</b>" if cancelled else "📡 <b>端口扫描结果</b>"
    text = (
        f"{title}\n"
        "━━━━━━━━━━━━━━\n"
        f"🎯 目标：<code>{html.escape(label)}</code>\n"
        f"🧩 {done}/{total} 个端口　⏱ 用时 {elapsed:.1f}s\n"
        f"{bar} {percent}%\n\n"
    )
    if open_ports:
        text += (
            f"<b>开放端口 {len(open_ports)} 个</b>\n"
            f"{_scan_open_lines(open_ports)}"
        )
    else:
        text += "🔒 <b>未发现开放端口</b>\n🧱 端口都已关闭，或被防火墙拦截"
    return text

def _scan_finish_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📡 继续扫描",
                callback_data="port_scan"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 返回首页",
                callback_data="home"
            )
        ]
    ])

def _scan_running_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⏹ 取消扫描",
                callback_data="portscan_cancel"
            )
        ]
    ])

async def _edit_scan_panel(bot, chat_id, text, markup):
    text = _wide(text)
    panel_id = PANELS.get(chat_id)
    if not panel_id:
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=panel_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            print("更新扫描进度失败：", e)

async def _run_port_scan(
    bot,
    chat_id,
    user_data,
    label,
    ip,
    ports,
    job
):
    total = len(ports)
    started = time.time()
    open_ports = []
    counter = {"scanned": 0}
    concurrency = max(1, min(_scan_concurrency(), total))
    timeout = SCAN_TIMEOUT if total <= 2000 else SCAN_TIMEOUT_LARGE
    port_iter = iter(ports)
    async def worker():
        while not job["cancel"]:
            port = next(port_iter, None)
            if port is None:
                return
            try:
                for attempt in range(3):
                    try:
                        async with _scan_slots():
                            _reader, writer = await asyncio.wait_for(
                                asyncio.open_connection(ip, port),
                                timeout=timeout
                            )
                            open_ports.append(port)
                            writer.close()
                            try:
                                await writer.wait_closed()
                            except Exception:
                                pass
                        break
                    except OSError as e:
                        if e.errno == errno.EMFILE and attempt < 2:
                            await asyncio.sleep(0.2)
                            continue
                        break
                    except Exception:
                        break
            finally:
                counter["scanned"] += 1
    workers = [
        asyncio.create_task(worker())
        for _ in range(concurrency)
    ]
    try:
        while True:
            _done, pending = await asyncio.wait(
                workers,
                timeout=1.0
            )
            if not pending:
                break
            if user_data.get("state") != "port_scan":
                job["cancel"] = True
                job["silent"] = True
            if job["cancel"]:
                break
            await _edit_scan_panel(
                bot,
                chat_id,
                _scan_progress_text(
                    label,
                    counter["scanned"],
                    total,
                    open_ports,
                    time.time() - started
                ),
                _scan_running_keyboard()
            )
        await asyncio.gather(*workers, return_exceptions=True)
        if job.get("silent"):
            return
        await _edit_scan_panel(
            bot,
            chat_id,
            _scan_result_text(
                label,
                counter["scanned"],
                total,
                open_ports,
                time.time() - started,
                job["cancel"]
            ),
            _scan_finish_keyboard()
        )
    except Exception as e:
        print("端口扫描出错：", e)
        await _edit_scan_panel(
            bot,
            chat_id,
            "❌ 端口扫描出错，请稍后重试。",
            _scan_finish_keyboard()
        )

async def port_scan(
    query,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["state"] = "port_scan"
    await edit_page(query,
        "📡 <b>端口扫描</b>\n"
        "━━━━━━━━━━━━━━\n"
        "⌨️ 请发送要扫描的IP或域名\n\n"
        # 示例整行用等宽字体，左边格式补空格到同样宽度，右边说明对齐
        "▪ <code>IP             常用端口</code>\n"
        "▪ <code>IP 22,80,443   指定端口</code>\n"
        "▪ <code>IP 1-1000      端口范围</code>\n"
        "▪ <code>IP ALL         全部端口</code>\n"
        "\n⏱ 全端口扫描约需 1~3 分钟，可随时取消",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            recent_entry_rows(query.from_user.id, "ps") + [[_btn("🏠 返回首页", "home")]]
        )
    )

async def handle_port_scan(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    chat_id = update.message.chat_id
    bot = update.message.get_bot()
    running = SCAN_JOBS.get(chat_id)
    if running and not running["task"].done():
        return
    parts = update.message.text.strip().split(None, 1)
    if not parts:
        return
    limited = rate_check(update.effective_user.id, 1)
    if limited:
        await reply_panel(update, context, "⏳ " + limited)
        return
    target = _parse_ping_target(parts[0])
    if target is None:
        await reply_panel(
            update, context,
            "❌ 地址格式不正确，请重新发送 IP 地址或域名。"
        )
        return
    host, single_port = target
    if len(parts) > 1:
        ports, error = _parse_port_spec(parts[1])
        if error:
            await reply_panel(
                update, context,
                "❌ " + html.escape(error) + "，请重新发送。",
                parse_mode=ParseMode.HTML
            )
            return
    elif single_port:
        ports = [int(single_port)]
    else:
        ports = list(COMMON_PORTS)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host,
            None,
            type=socket.SOCK_STREAM
        )
        ip = infos[0][4][0]
    except Exception:
        await reply_panel(
            update, context,
            "❌ 无法解析这个地址，请检查后重新发送。"
        )
        return
    recent_target_add(update.effective_user.id, host)
    scan_recent_add(
        update.effective_user.id, host,
        parts[1].strip() if len(parts) > 1 else (str(single_port) if single_port else "")
    )
    label = host if host == ip else f"{host} ({ip})"
    await _panel_show(
        bot,
        chat_id,
        _scan_progress_text(label, 0, len(ports), [], 0),
        _scan_running_keyboard()
    )
    job = {"cancel": False}
    job["task"] = asyncio.create_task(
        _run_port_scan(
            bot,
            chat_id,
            context.user_data,
            label,
            ip,
            ports,
            job
        )
    )
    SCAN_JOBS[chat_id] = job

def _parse_ping_target(text):
    text = text.strip()
    for prefix in ("https://", "http://"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    text = text.split("/", 1)[0].strip()
    if not text:
        return None
    try:
        ipaddress.ip_address(text)
        return text, None
    except ValueError:
        pass
    host, port = text, None
    if text.count(":") == 1:
        host, port = text.split(":")
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            return None
    try:
        ipaddress.ip_address(host)
        return host, port
    except ValueError:
        pass
    if re.fullmatch(
        r"([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}",
        host
    ):
        return host, port
    return None

async def global_ping(
    query,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["state"] = "global_ping"
    await edit_page(query,
        "🌍 <b>全球 PING</b>\n"
        "━━━━━━━━━━━━━━\n"
        "⌨️ 请发送要检测的IP或域名，可加端口号",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            ([[_btn("🗃 最近记录", "gp|HLIST")]] if gping_recent_list(query.from_user.id) else [])
            + [[_btn("🏠 返回首页", "home")]]
        )
    )

async def gping_recent_page(query, context):
    """全球 PING 的最近记录页：点一下直接重测（保留当时的端口 / 协议）。"""
    context.user_data["state"] = "global_ping"
    rows = gping_recent_rows(query.from_user.id)
    if not rows:
        await global_ping(query, context)
        return
    await edit_page(query,
        "🗃 <b>最近记录</b>\n"
        "━━━━━━━━━━━━━━\n"
        "▪ 点一下直接重测",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows + [[_btn("⬅️ 返回", "global_ping")]])
    )

def _lookup_ip_info(host):
    url = (
        "http://ip-api.com/json/"
        + urllib.parse.quote(host, safe="")
        + "?lang=zh-CN&fields=status,message,country,countryCode,regionName,city,lat,lon,as,query"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(
        request,
        timeout=8
    ) as response:
        return json.loads(
            response.read().decode("utf-8")
        )
GLOBAL_PING_NODES = [
    # 国内 14 个、海外 4 个，结果两列排版正好左右对称
    {"flag": "🇨🇳", "name": "北京", "lat": 39.90, "lon": 116.40,
     "province": "北京", "cities": ["Beijing"]},
    {"flag": "🇨🇳", "name": "上海", "lat": 31.23, "lon": 121.47,
     "province": "上海", "cities": ["Shanghai"]},
    {"flag": "🇨🇳", "name": "江苏", "lat": 32.06, "lon": 118.80,
     "province": "江苏", "cities": ["Nanjing", "Suzhou", "Wuxi", "Changzhou",
                                   "Nantong", "Xuzhou", "Yangzhou", "Kunshan"]},
    {"flag": "🇨🇳", "name": "浙江", "lat": 30.27, "lon": 120.15,
     "province": "浙江", "cities": ["Hangzhou", "Ningbo", "Wenzhou", "Jiaxing",
                                   "Shaoxing", "Jinhua", "Taizhou", "Huzhou"]},
    {"flag": "🇨🇳", "name": "安徽", "lat": 31.82, "lon": 117.23,
     "province": "安徽", "cities": ["Hefei", "Wuhu", "Bengbu", "Anqing", "Maanshan", "Fuyang", "Huainan", "Chuzhou"]},
    {"flag": "🇨🇳", "name": "福建", "lat": 26.08, "lon": 119.30,
     "province": "福建", "cities": ["Fuzhou", "Xiamen", "Quanzhou", "Zhangzhou",
                                   "Putian", "Longyan", "Sanming", "Ningde"]},
    {"flag": "🇨🇳", "name": "广州", "lat": 23.13, "lon": 113.26,
     "province": "广东", "cities": ["Guangzhou", "Shenzhen", "Dongguan", "Foshan",
                                   "Zhuhai", "Huizhou", "Zhongshan", "Shantou"]},
    {"flag": "🇨🇳", "name": "广西", "lat": 22.82, "lon": 108.37,
     "province": "广西", "cities": ["Nanning", "Guilin", "Liuzhou", "Wuzhou", "Beihai",
                                   "Yulin", "Qinzhou", "Guigang", "Baise"]},
    {"flag": "🇨🇳", "name": "湖北", "lat": 30.59, "lon": 114.31,
     "province": "湖北", "cities": ["Wuhan", "Yichang", "Xiangyang", "Jingzhou",
                                   "Huangshi", "Shiyan", "Xiaogan", "Jingmen"]},
    {"flag": "🇨🇳", "name": "湖南", "lat": 28.23, "lon": 112.94,
     "province": "湖南", "cities": ["Changsha", "Zhuzhou", "Xiangtan", "Hengyang",
                                   "Yueyang", "Changde", "Chenzhou", "Yiyang"]},
    {"flag": "🇨🇳", "name": "重庆", "lat": 29.56, "lon": 106.55,
     "province": "重庆", "cities": ["Chongqing"]},
    {"flag": "🇨🇳", "name": "贵州", "lat": 26.65, "lon": 106.63,
     "province": "贵州", "cities": ["Guiyang", "Zunyi", "Liupanshui", "Anshun", "Bijie", "Tongren", "Kaili", "Duyun"]},
    {"flag": "🇨🇳", "name": "云南", "lat": 25.04, "lon": 102.71,
     "province": "云南", "cities": ["Kunming", "Qujing", "Yuxi", "Dali", "Lijiang", "Baoshan", "Zhaotong", "Puer"]},
    {"flag": "🇨🇳", "name": "陕西", "lat": 34.34, "lon": 108.94,
     "province": "陕西", "cities": ["Xi'an", "Xianyang", "Baoji", "Weinan", "Hanzhong", "Yan'an", "Ankang", "Shangluo"]},
    {"flag": "🇭🇰", "name": "香港", "lat": 22.32, "lon": 114.17,
     # 海外节点：第一个不行（无探针 / 掉线 / 卡住）就按顺序换下一个；
     # 同一地区重复写，Globalping 每次会重新随机挑一台在线探针
     "locations": [{"country": "HK"}, {"country": "HK"}, {"country": "HK"}]},
    {"flag": "🇨🇳", "name": "台湾", "lat": 25.03, "lon": 121.57,
     "locations": [{"country": "TW", "city": "Taipei"}, {"country": "TW"}, {"country": "TW"}]},
    {"flag": "🇯🇵", "name": "东京", "lat": 35.68, "lon": 139.69,
     "locations": [{"country": "JP", "city": "Tokyo"}, {"country": "JP", "city": "Tokyo"},
                   {"country": "JP", "city": "Osaka"}, {"country": "JP"}]},
    {"flag": "🇺🇸", "name": "美国", "lat": 34.05, "lon": -118.24,
     "locations": [{"country": "US", "city": "Los Angeles"}, {"country": "US", "city": "Los Angeles"},
                   {"country": "US", "city": "San Jose"}, {"country": "US", "city": "San Francisco"}]},
]
GLOBAL_PING_NEAR_KM = 900
GLOBAL_PING_NODE_MAX = 20
CN_CITY_ZH = {
    "hangzhou": "杭州", "ningbo": "宁波", "wenzhou": "温州", "jiaxing": "嘉兴",
    "shaoxing": "绍兴", "jinhua": "金华", "taizhou": "台州", "huzhou": "湖州",
    "nanjing": "南京", "suzhou": "苏州", "wuxi": "无锡", "changzhou": "常州",
    "nantong": "南通", "xuzhou": "徐州", "yangzhou": "扬州", "kunshan": "昆山",
    "fuzhou": "福州", "xiamen": "厦门", "quanzhou": "泉州", "zhangzhou": "漳州",
    "putian": "莆田", "longyan": "龙岩", "sanming": "三明", "ningde": "宁德",
    "dongguan": "东莞", "foshan": "佛山", "zhuhai": "珠海", "huizhou": "惠州",
    "zhongshan": "中山", "shantou": "汕头",
    "chengdu": "成都", "chongqing": "重庆", "wuhan": "武汉", "xian": "西安",
    "guilin": "桂林", "nanning": "南宁", "changsha": "长沙", "tianjin": "天津",
    "qingdao": "青岛", "jinan": "济南", "zhengzhou": "郑州", "hefei": "合肥",
    "kunming": "昆明", "shenyang": "沈阳", "harbin": "哈尔滨", "nanchang": "南昌",
    "shijiazhuang": "石家庄", "taiyuan": "太原", "dalian": "大连", "guiyang": "贵阳",
    "wuhu": "芜湖", "ganzhou": "赣州", "jiujiang": "九江", "shangrao": "上饶",
    "yingtan": "鹰潭", "huangshan": "黄山", "anqing": "安庆", "bengbu": "蚌埠",
    "lanzhou": "兰州", "xining": "西宁", "yinchuan": "银川", "urumqi": "乌鲁木齐",
    "hohhot": "呼和浩特", "changchun": "长春", "lhasa": "拉萨", "haikou": "海口",
    "sanya": "三亚", "zhuzhou": "株洲", "yantai": "烟台", "weifang": "潍坊",
    "linyi": "临沂", "baoding": "保定", "tangshan": "唐山", "luoyang": "洛阳",
    "yichang": "宜昌", "xiangyang": "襄阳", "mianyang": "绵阳", "liuzhou": "柳州", "baise": "百色", "guigang": "贵港", "qinzhou": "钦州", "beihai": "北海", "wuzhou": "梧州",
    "zhanjiang": "湛江", "jiangmen": "江门", "shaoguan": "韶关", "meizhou": "梅州",
    "zhoushan": "舟山", "lishui": "丽水", "quzhou": "衢州", "yancheng": "盐城",
    "huaian": "淮安", "lianyungang": "连云港", "zhenjiang": "镇江", "taizhoujs": "泰州",
    "suqian": "宿迁", "nanping": "南平",
    "hefei": "合肥", "maanshan": "马鞍山", "fuyang": "阜阳", "huainan": "淮南", "chuzhou": "滁州",
    "zunyi": "遵义", "liupanshui": "六盘水", "anshun": "安顺", "bijie": "毕节", "tongren": "铜仁", "kaili": "凯里", "duyun": "都匀",
    "qujing": "曲靖", "yuxi": "玉溪", "dali": "大理", "lijiang": "丽江", "baoshan": "保山", "zhaotong": "昭通", "puer": "普洱",
    "xianyang": "咸阳", "baoji": "宝鸡", "weinan": "渭南", "hanzhong": "汉中", "yanan": "延安", "ankang": "安康", "shangluo": "商洛",
    "jingzhou": "荆州", "huangshi": "黄石", "shiyan": "十堰", "xiaogan": "孝感", "jingmen": "荆门",
    "xiangtan": "湘潭", "hengyang": "衡阳", "yueyang": "岳阳", "changde": "常德", "chenzhou": "郴州", "yiyang": "益阳",
    "deyang": "德阳", "yibin": "宜宾", "nanchong": "南充", "leshan": "乐山",
    "luzhou": "泸州", "zigong": "自贡", "karamay": "克拉玛依", "kashgar": "喀什",
    "kashi": "喀什", "yining": "伊宁", "korla": "库尔勒", "shihezi": "石河子",
    "hami": "哈密", "changji": "昌吉",
    "taichung": "台中", "kaohsiung": "高雄", "taoyuan": "桃园", "hsinchu": "新竹",
    "tainan": "台南", "newtaipei": "新北",
}
GLOBAL_PING_PACKETS = 3
from concurrent.futures import ThreadPoolExecutor as _GPExecutor
import threading as _gp_threading
_GP_POOL = _GPExecutor(max_workers=32, thread_name_prefix="gping")
GLOBALPING_PROBES_API = "https://api.globalping.io/v1/probes"
_PROBE_CACHE = {"t": 0, "list": None}

def _city_key(city):
    return re.sub(r"[^a-z]", "", (city or "").lower())

def _city_zh(city):
    key = _city_key(city)
    if key in CN_CITY_ZH:
        return CN_CITY_ZH[key]
    return CITY_TABLE.get(key, (city,))[0]

def _online_cn_probes():
    """Globalping 当前在线的国内探针（缓存 10 分钟）。拿不到返回 None。"""
    if _PROBE_CACHE["list"] is not None:
        if time.time() - _PROBE_CACHE["t"] >= 600 and not _PROBE_CACHE.get("refreshing"):
            _PROBE_CACHE["refreshing"] = True
            _gp_threading.Thread(target=_refresh_cn_probes, daemon=True).start()
        return _PROBE_CACHE["list"]
    if time.time() - _PROBE_CACHE.get("failed", 0) < 60:
        return None
    return _refresh_cn_probes()

def _refresh_cn_probes():
    try:
        return _fetch_cn_probes()
    finally:
        _PROBE_CACHE["refreshing"] = False

def _fetch_cn_probes():
    try:
        rows = _http_json(GLOBALPING_PROBES_API, timeout=15)
        if not isinstance(rows, list):
            raise ValueError("探针列表格式不对")
    except Exception as e:
        print("获取在线探针列表失败：", e)
        _PROBE_CACHE["failed"] = time.time()
        return _PROBE_CACHE["list"]
    probes = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        loc = row.get("location") or {}
        if (loc.get("country") or "").upper() != "CN" or not loc.get("city"):
            continue
        key = (_city_key(loc["city"]), loc.get("asn"))
        if key in seen:
            continue
        seen.add(key)
        probes.append({
            "city": loc["city"],
            "asn": loc.get("asn"),
            "lat": loc.get("latitude"),
            "lon": loc.get("longitude"),
        })
    _PROBE_CACHE.update(t=time.time(), list=probes)
    return probes

def _probe_location(probe):
    location = {"country": "CN", "city": probe["city"]}
    if probe.get("asn"):
        location["asn"] = int(probe["asn"])
    return location

def _plan_global_ping():
    """
    给每个节点排好候选探针：[(位置条件, 类型), ...]
      own      本地区的在线探针
      near     本地区没有，借用最近的在线探针
      fallback 国外节点的备选城市
    """
    probes = _online_cn_probes()
    plans = []
    own_keys = {
        _city_key(c)
        for node in GLOBAL_PING_NODES
        for c in node.get("cities", [])
    }
    for node in GLOBAL_PING_NODES:
        if "locations" in node:
            plans.append([
                (loc, "static" if i == 0 else "fallback")
                for i, loc in enumerate(node["locations"])
            ])
            continue
        keys = [_city_key(c) for c in node["cities"]]
        if probes is None:
            plans.append([({"country": "CN", "city": c}, "own") for c in node["cities"]])
            continue
        own = sorted(
            (p for p in probes if _city_key(p["city"]) in keys),
            key=lambda p: keys.index(_city_key(p["city"]))
        )
        plan = [(_probe_location(p), "own") for p in own[:3]]
        near, spare = [], []
        for p in probes:
            if p.get("lat") is None or _city_key(p["city"]) in keys:
                continue
            km = _haversine_km(node["lat"], node["lon"], float(p["lat"]), float(p["lon"]))
            if km > node.get("near_km", GLOBAL_PING_NEAR_KM):
                continue
            (spare if _city_key(p["city"]) in own_keys else near).append((km, p))
        borrow = sorted(near + [(km * 1.3, p) for km, p in spare], key=lambda item: item[0])
        plan += [(_probe_location(p), "near") for _km, p in borrow[:3]]
        plan = plan[:6]
        plans.append(plan)
    return plans

def _probe_resolve(host, measurement_id):
    """用上一次测量的同一个探针解析域名，返回它拿到的第一个 IPv4（失败返回 None）。"""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if not measurement_id:
        return None
    try:
        results = _gp_run({
            "type": "dns",
            "target": host,
            "locations": measurement_id,
            "measurementOptions": {"query": {"type": "A"}},
        }, timeout=8, first_wait=0.5, interval=0.5)
    except Exception as e:
        print("探针解析域名失败：", e)
        return None
    for item in results:
        for answer in (item.get("result") or {}).get("answers") or []:
            if answer.get("type") == "A" and answer.get("value"):
                return answer["value"]
    return None

def _ip_in_raw(raw):
    """
    从探针原始输出里找目标 IP：优先取开头 “PING 域名 (1.2.3.4)” 这一行；
    没有这一行才在全文里找第一个公网 IPv4（避免拿到中途路由器回的报错 IP）。
    """
    head = re.search(r"^\s*\S*\s*PING\s+\S+\s+\((\d{1,3}(?:\.\d{1,3}){3})\)", raw or "", re.M | re.I)
    if head:
        try:
            return head.group(1) if ipaddress.ip_address(head.group(1)).is_global else None
        except ValueError:
            return None
    for text in re.findall(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])", raw or ""):
        try:
            addr = ipaddress.ip_address(text)
        except ValueError:
            continue
        if addr.is_global:
            return text
    return None
GLOBAL_PING_STUCK = {"ICMP": 3.0, "TCP": 5.0}
GLOBAL_PING_STUCK_SWITCH = 2
GLOBAL_PING_GIVE_UP = 2.5
GLOBAL_PING_MEASURE_MAX = 8
_PING_REPLY = re.compile(r"time[=<]\s*[\d.]+\s*ms|bytes from|^\s*reply from", re.I | re.M)
_PING_RTT = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.I)
_PING_MISS = re.compile(
    r"no answer yet|request time ?out|timed? ?out|no reply|unreachable", re.I
)
_DNS_FAIL = re.compile(
    r"name or service not known|temporary failure in name resolution|unknown host|"
    r"could not resolve|no address associated|nxdomain|servfail",
    re.I
)

def _ping_give_up(proto="ICMP", switch_if_stuck=True):
    """
    每次测量新建一个判断函数，交给 _gp_run 每次查结果时调用。
    返回 True 表示不用再等；为什么不等记在 check.reason 里：
      "stuck"   探针卡住（一直没有输出）
      "timeout" 目标不通（有“没回应”的证据）
    """
    first_miss = {}
    stuck_after = GLOBAL_PING_STUCK.get(proto, 5.0)
    def check(result, waited):
        items = result.get("results") or []
        if not items:
            return False
        now = time.time()
        stuck = 0
        dead = 0
        running = 0
        for index, item in enumerate(items):
            detail = item.get("result") or {}
            if detail.get("status") != "in-progress":
                continue
            running += 1
            raw = detail.get("rawOutput") or ""
            if not raw.strip():
                if switch_if_stuck and waited >= stuck_after:
                    stuck += 1
                continue
            misses = sum(1 for line in raw.splitlines()[1:] if _PING_MISS.search(line))
            if _PING_REPLY.search(raw) or misses < 2:
                continue
            started = first_miss.setdefault(index, now)
            if now - started >= GLOBAL_PING_GIVE_UP:
                dead += 1
        if not running or stuck + dead < running:
            return False
        check.reason = "stuck" if stuck else "timeout"
        return True
    check.reason = None
    return check

def _rtt_from_raw(raw):
    """测量没跑完时，用已经收到的回包算延迟（取最低值，和正常结果一致；没有回包返回 None）。"""
    values = [float(v) for v in _PING_RTT.findall(raw or "")]
    return min(values) if values else None

def _raw_brief(raw):
    """日志用：原始输出的第一行和最后一行。"""
    lines = [l.strip() for l in (raw or "").splitlines() if l.strip()]
    if not lines:
        return "（无输出）"
    return lines[0][:80] + ("" if len(lines) == 1 else " … " + lines[-1][:80])

def _global_ping_one(host, port, candidates, proto="ICMP", label=""):
    """按候选依次测试，探针不存在、掉线或卡住就换下一个。返回结果 dict。"""
    measure_type = "ping"
    options = {"packets": GLOBAL_PING_PACKETS}
    if proto == "TCP":
        options.update(protocol="TCP", port=int(port))
    last_error = "无探针"
    stuck_switches = 0
    # 有没有探针真正接过这个测试（卡住 / 无回包也算）。
    # 接过的话，最后就算换完候选也没结果，也应该显示“超时”，不能显示“无探针”：
    # 目标本身不通时，探针常常一直没输出（被判“卡住”），换到最后一个候选又碰上
    # 探针暂时不可用，以前就会错误地落到“无探针”。
    ran_any = False
    log = []
    began = time.time()
    def done(res):
        print(
            f"全球Ping 节点 {label or '?'}（{proto}）{time.time() - began:.1f}s："
            + "；".join(log)
        )
        return res
    for number, (location, tag) in enumerate(candidates):
        remaining = len(candidates) - number - 1
        where = location.get("city") or location.get("magic") or location.get("country") or "?"
        tried = time.time()
        body = {
            "type": measure_type,
            "target": host,
            "locations": [dict(location, limit=1)],
            "measurementOptions": options,
        }
        can_switch = bool(remaining) and stuck_switches < GLOBAL_PING_STUCK_SWITCH
        checker = _ping_give_up(proto, switch_if_stuck=can_switch)
        try:
            results, measurement_id = _gp_run(
                body, timeout=GLOBAL_PING_MEASURE_MAX, first_wait=0.6, interval=0.5,
                return_id=True, early_stop=checker
            )
        except ProbeUnavailable as e:
            if "probe" not in str(e).lower():
                log.append(f"{where} 参数错误")
                return done({"error": "参数错误"})
            last_error = "无探针"
            log.append(f"{where} 无探针")
            if tag in ("own", "near"):
                _PROBE_CACHE["t"] = 0
            continue
        except Exception as e:
            text = str(e)
            log.append(f"{where} 请求失败 {text[:60]}")
            if "429" in text or "额度" in text or "频繁" in text:
                return done({"error": "额度用完"})
            if "timed out" in text.lower() or "超时" in text:
                return done({"error": "请求超时"})
            return done({"error": "请求失败"})
        spent = f"{time.time() - tried:.1f}s"
        if not results:
            log.append(f"{where} 无结果 {spent}")
            continue
        probe = results[0].get("probe") or {}
        result = results[0].get("result") or {}
        stats = result.get("stats") or {}
        raw = result.get("rawOutput") or ""
        status = result.get("status")
        who = f"{probe.get('city') or where}/AS{probe.get('asn') or '?'}"
        if status != "offline":
            ran_any = True  # 探针在线并且接了测试（失败、卡住都算测过）
        if status == "offline" or (status == "failed" and not raw):
            last_error = "探针失效"
            log.append(f"{who} 探针失效 {spent}")
            continue
        stuck = status == "in-progress" and not raw.strip()
        if stuck and can_switch:
            stuck_switches += 1
            log.append(f"{who} 卡住{spent}无输出→换探针")
            continue
        rtt = stats.get("min") if isinstance(stats.get("min"), (int, float)) else stats.get("avg")
        loss = stats.get("loss")
        if status == "in-progress":
            partial = _rtt_from_raw(raw)
            if partial is not None:
                rtt, loss, status = partial, None, "finished"
                log.append(f"{who} 到点用已收回包 {partial:.0f}ms {spent}")
            elif checker.reason == "timeout":
                log.append(f"{who} 目标不通提前判超时 {spent} [{_raw_brief(raw)}]")
            elif stuck:
                log.append(f"{who} 卡住{spent}无输出→判超时")
            else:
                log.append(f"{who} 到点无回包判超时 {spent} [{_raw_brief(raw)}]")
        else:
            log.append(
                f"{who} {status} "
                + (f"{rtt:.0f}ms" if isinstance(rtt, (int, float)) else "无延迟")
                + f" {spent}"
                + ("" if proto == "ICMP" else f" [{_raw_brief(raw)}]")
            )
        ip = result.get("resolvedAddress") or _ip_in_raw(raw)
        if not ip and status == "finished" and not _DNS_FAIL.search(raw):
            # 只给测通的节点补查 IP（用来发现其他地区解析到的 IP）；超时 / 卡住的不再多等一次
            ip = _probe_resolve(host, measurement_id)
        return done({
            "ip": ip,
            "rtt": rtt,
            "loss": loss,
            "city": probe.get("city") or "",
            "country": probe.get("country") or "",
            "lat": probe.get("latitude"),
            "lon": probe.get("longitude"),
            "tag": tag,
            "status": status,
            "raw": raw,
        })
    if ran_any and last_error in ("无探针", "探针失效"):
        log.append("有探针测过但都没结果 → 判超时")
        return done({"error": "超时"})
    return done({"error": last_error})

def _global_ping_cell(res):
    """返回延迟列的文字：“25ms” 或 “超时 / 失败 / 无探针” 等状态。"""
    if res.get("error"):
        return res["error"]
    if res.get("status") != "finished" or res.get("rtt") is None:
        return "超时"
    if res.get("loss") is not None and res["loss"] >= 100:
        return "超时"
    return f"{res['rtt']:.0f}ms"

def _global_ping_label(node):
    """
    只显示省 / 地区名，不再标注具体用了哪个城市的探针：
    探针由脚本自动挑（本省没有就自动借邻省），用户不需要关心。
    广州节点显示为“广东”（探针可能在深圳、东莞等地）。
    """
    return node.get("province") or node["name"]
IP_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"
IP_MARKS_SELECTED = "❶❷❸❹❺❻❼❽❾❿"
DNS_ECS_SUBNETS = [
    ("电信", "202.96.209.0/24"),
    ("电信", "219.141.136.0/24"),
    ("电信", "113.108.209.0/24"),
    ("联通", "202.106.0.0/24"),
    ("联通", "210.22.97.0/24"),
    ("联通", "210.21.196.0/24"),
    ("移动", "211.136.112.0/24"),
    ("移动", "221.179.155.0/24"),
    ("移动", "120.196.165.0/24"),
    ("海外", "8.8.8.0/24"),
    ("海外", "103.4.200.0/24"),
]
DNS_ECS_ROUNDS = 2
DOH_SERVERS = [
    "https://dns.google/resolve?name={name}&type={qtype}&edns_client_subnet={subnet}",
    "https://dns.alidns.com/resolve?name={name}&type={qtype}&edns_client_subnet={subnet}",
]

def resolve_all_ips(name):
    """返回 {ip: [来源标签...]}，按发现顺序。只取 A 记录（IPv4）。"""
    from concurrent.futures import ThreadPoolExecutor, wait
    jobs = [(label, subnet) for _ in range(DNS_ECS_ROUNDS) for label, subnet in DNS_ECS_SUBNETS]
    def query(job):
        label, subnet = job
        for server in DOH_SERVERS:
            url = server.format(name=urllib.parse.quote(name), qtype=1, subnet=subnet)
            try:
                reply = _http_json(url, headers={"Accept": "application/dns-json"}, timeout=3)
            except Exception:
                continue
            return label, [
                a.get("data") for a in reply.get("Answer") or []
                if a.get("type") == 1 and a.get("data")
            ]
        return label, []
    found = {}
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    futures = [pool.submit(query, job) for job in jobs]
    wait(futures, timeout=5)
    pool.shutdown(wait=False)
    for future in futures:
        if not future.done() or future.exception():
            continue
        label, ips = future.result()
        for ip in ips:
            labels = found.setdefault(ip, [])
            if label not in labels:
                labels.append(label)
    return found

def _fw(text):
    """转成全角：数字、字母、符号、空格都变成和汉字一样宽。"""
    out = []
    for ch in text:
        code = ord(ch)
        if ch == " ":
            out.append("　")
        elif 0x21 <= code <= 0x7E:
            out.append(chr(code + 0xFEE0))
        elif ch == "·":
            out.append("・")
        else:
            out.append(ch)
    return "".join(out)

def _global_ping_table(results, port, proto="ICMP", ip_marks=None):
    """
    两列排版，国内、海外分开排：
        🟢 北京 49ms    🟢 天津 47ms
        🔴 香港 超时    🟢 台湾 21ms
    「地名 延迟」放在 <code> 里用等宽字体显示。
    对齐办法：让左边每一格里“汉字宽的字符”和“字母宽的字符”数量完全一样，
    不管手机字体里汉字到底多宽，每格宽度都相同，第二列一定对齐：
        40ms   → "40ms" + 2 个空格 + 3 个全角空格
        超时   → "超时" + 1 个全角空格 + 6 个空格
        无探针 → "无探针" + 6 个空格
    """
    status_short = {"请求失败": "失败", "请求超时": "超时", "额度用完": "无额度",
                    "探针失效": "失效", "参数错误": "错误"}
    ASCII_SLOTS = 6   # 每格固定 6 个字母宽的字符（数字、ms、空格）
    CJK_SLOTS = 3     # 每格固定 3 个汉字宽的字符（汉字、全角空格）

    def cell(node, res, last):
        label = _global_ping_label(node)
        latency = _global_ping_cell(res)
        m = re.fullmatch(r"(\d+)ms", latency)
        if m:
            ms = int(m.group(1))
            value = f"{ms}ms" if ms < 1000 else f"{ms / 1000:.1f}s"
            dot = "🟢"
        elif latency == "无探针":
            value, dot = "无探针", "🟡"
        else:
            value, dot = status_short.get(latency, latency), "🔴"
        cjk = sum(1 for ch in value if ord(ch) > 0x2E80)
        ascii_n = len(value) - cjk
        text = f"{label} {value}"
        if not last:
            text += " " * max(0, ASCII_SLOTS - ascii_n) + "\u3000" * max(0, CJK_SLOTS - cjk)
        return f"{dot} <code>{html.escape(text)}</code>"

    home, abroad = [], []
    for node, res in zip(GLOBAL_PING_NODES, results):
        (home if "cities" in node else abroad).append((node, res))
    rows = []
    for group in (home, abroad):
        for i in range(0, len(group), 2):
            pair = group[i:i + 2]
            rows.append("".join(
                cell(node, res, last=(j == len(pair) - 1)) for j, (node, res) in enumerate(pair)
            ))
    how = proto if proto == "ICMP" else f"{proto} {port}"
    title = f"🌎 <b>全球延迟</b>（{how}）"
    return title + "\n" + "\n".join(rows)
_TILE_CACHE = {}

def _tile_cached(zoom, x, y):
    key = (zoom, x % (2 ** zoom), y)
    tile = _TILE_CACHE.get(key)
    if tile is None:
        tile = _fetch_tile(zoom, x, y)
        if tile is None:
            return None
        if len(_TILE_CACHE) > 400:
            _TILE_CACHE.clear()
        _TILE_CACHE[key] = tile
    return tile
# 省份上色：颜色更深、更不透明（0~255，越大越实）
PING_FILL_COLORS = {"ok": (76, 175, 50), "fail": (215, 45, 35), "none": (140, 145, 150)}  # 取自 🟢🔴 的颜色
PING_EDGE_COLORS = {"ok": (52, 125, 32), "fail": (155, 28, 20), "none": (100, 105, 110)}
PING_FILL_ALPHA = 215
PING_MAP_LABELS = False  # 全球 Ping 地图上是否显示地区名标签（True 显示）
PING_COLORS = {
    "ok": (76, 175, 50),
    "fail": (215, 45, 35),
    "none": (140, 150, 160),
}
PING_LABEL_COLORS = {
    "ok": (0, 160, 70),
    "fail": (220, 20, 50),
    "none": (105, 115, 125),
}

def _load_cjk_bold(size):
    for name in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "NotoSansCJK-Bold.ttc",
        "msyhbd.ttc",
    ):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return _load_cjk_font(size)

def _draw_marker(image, draw, x, y, r, color):
    """醒目的标记点：半透明光晕 + 深色外圈 + 白圈 + 亮色实心。"""
    halo = Image.new("RGBA", image.size, (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse((x - r - 9, y - r - 9, x + r + 9, y + r + 9), fill=color + (70,))
    hd.ellipse((x - r - 5, y - r - 5, x + r + 5, y + r + 5), fill=color + (110,))
    image.alpha_composite(halo)
    draw.ellipse((x - r - 3, y - r - 3, x + r + 3, y + r + 3), fill=(25, 25, 25))
    draw.ellipse((x - r - 1, y - r - 1, x + r + 1, y + r + 1), fill=(255, 255, 255))
    draw.ellipse((x - r + 1, y - r + 1, x + r - 1, y + r - 1), fill=color)

def _ping_points(results):
    points = []
    for node, res in zip(GLOBAL_PING_NODES, results):
        latency = _global_ping_cell(res)
        if res.get("pending") or res.get("error") in ("无探针", "探针失效"):
            state = "none"
        elif latency.endswith("ms"):
            state = "ok"
        else:
            state = "fail"
        if res.get("tag") in ("near", "fallback") or res.get("lat") is None:
            lat, lon = node["lat"], node["lon"]
        else:
            lat, lon = res["lat"], res["lon"]
        points.append({
            "label": _global_ping_label(node),
            "latency": latency,
            "lat": float(lat),
            "lon": float(lon),
            "state": state,
            "china": (node["flag"] in ("🇨🇳", "🇭🇰")),
        })
    return points

PROVINCE_GEO_FILE = "/root/china_provinces.json"
PROVINCE_GEO_URLS = [
    # 阿里云 DataV 的全国省级边界（标准 GeoJSON）
    "https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json",
    # 备用：ECharts 自带的中国地图（坐标是压缩编码，下面会自动解码）
    "https://cdn.jsdelivr.net/npm/echarts@4.9.0/map/json/china.json",
]
_PROVINCE_GEO = {"shapes": None, "tried": 0}

def _echarts_decode(ring, offsets, scale=1024):
    """ECharts 压缩坐标解码，返回 [(lon, lat), ...]。"""
    out = []
    prev_x, prev_y = offsets[0], offsets[1]
    for i in range(0, len(ring) - 1, 2):
        x = ord(ring[i]) - 64
        y = ord(ring[i + 1]) - 64
        x = (x >> 1) ^ (-(x & 1))
        y = (y >> 1) ^ (-(y & 1))
        x += prev_x
        y += prev_y
        prev_x, prev_y = x, y
        out.append((x / scale, y / scale))
    return out

def _province_shapes_parse(geo):
    """返回 {省份名: [外圈坐标列表, ...]}，坐标为 (lon, lat)。"""
    shapes = {}
    encoded = bool(geo.get("UTF8Encoding"))
    for feature in geo.get("features") or []:
        props = feature.get("properties") or {}
        name = str(props.get("name") or "").strip()
        geom = feature.get("geometry") or {}
        if not name or not geom:
            continue
        kind = geom.get("type")
        coords = geom.get("coordinates") or []
        polygons = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
        offsets = geom.get("encodeOffsets") or []
        if kind == "Polygon":
            offsets = [offsets]
        rings = []
        for pi, polygon in enumerate(polygons):
            if not polygon:
                continue
            outer = polygon[0]
            if encoded and isinstance(outer, str):
                try:
                    outer = _echarts_decode(outer, offsets[pi][0])
                except Exception:
                    continue
            else:
                outer = [(float(pt[0]), float(pt[1])) for pt in outer]
            if len(outer) >= 3:
                rings.append(outer)
        if rings:
            shapes.setdefault(name, []).extend(rings)
    return shapes

def _province_shapes():
    """加载省级边界；本地没有就下载一次存起来。失败时 1 小时内不再重试，地图退回只画圆点。"""
    if _PROVINCE_GEO["shapes"] is not None:
        return _PROVINCE_GEO["shapes"]
    if time.time() - _PROVINCE_GEO["tried"] < 3600:
        return None
    _PROVINCE_GEO["tried"] = time.time()
    def read_local():
        try:
            with open(PROVINCE_GEO_FILE, "r", encoding="utf-8") as f:
                return _province_shapes_parse(json.load(f))
        except Exception:
            return None
    shapes = read_local()
    if not shapes:
        for url in PROVINCE_GEO_URLS:
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = response.read()
                parsed = _province_shapes_parse(json.loads(payload.decode("utf-8")))
                if len(parsed) < 20:
                    continue
                temp = PROVINCE_GEO_FILE + ".tmp"
                with open(temp, "wb") as f:
                    f.write(payload)
                os.replace(temp, PROVINCE_GEO_FILE)
                print("省级边界下载完成：", url)
                shapes = parsed
                break
            except Exception as e:
                print("下载省级边界失败：", url, e)
    _PROVINCE_GEO["shapes"] = shapes or None
    return _PROVINCE_GEO["shapes"]

def _province_rings(label):
    """按节点名（北京 / 广东 / 香港 / 台湾…）找对应省份的边界。"""
    shapes = _province_shapes()
    if not shapes or not label:
        return None
    for name, rings in shapes.items():
        if name.startswith(label):
            return rings
    return None

def _map_panel(points, width, height, title, max_zoom, label_filter, font, small, extra=None):
    pad = 55
    zoom = 1
    # 取景时把上色省份的整块范围也算进去（比如四川西部），不只是探针那一个点
    extent = [(p["lat"], p["lon"]) for p in points]
    for p in points:
        try:
            rings = _province_rings(p.get("label"))
        except Exception:
            rings = None
        for ring in rings or []:
            lons = [pt[0] for pt in ring]
            lats = [pt[1] for pt in ring]
            extent += [(min(lats), min(lons)), (max(lats), max(lons))]
    for candidate in range(max_zoom, 0, -1):
        xs, ys = zip(*[_world_xy(lat, lon, candidate) for lat, lon in extent])
        if (
            max(xs) - min(xs) <= width - 2 * pad
            and max(ys) - min(ys) <= height - 2 * pad
        ):
            zoom = candidate
            break
    coords = [_world_xy(p["lat"], p["lon"], zoom) for p in points]
    frame = [_world_xy(lat, lon, zoom) for lat, lon in extent]
    left = (min(c[0] for c in frame) + max(c[0] for c in frame)) / 2 - width / 2
    top = (min(c[1] for c in frame) + max(c[1] for c in frame)) / 2 - height / 2
    image = Image.new("RGB", (width, height), (170, 211, 223))
    from concurrent.futures import ThreadPoolExecutor
    tiles = [
        (tx, ty)
        for tx in range(int(math.floor(left / 256)), int(math.floor((left + width) / 256)) + 1)
        for ty in range(int(math.floor(top / 256)), int(math.floor((top + height) / 256)) + 1)
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(lambda t: _tile_cached(zoom, t[0], t[1]), tiles))
    for (tx, ty), tile in zip(tiles, fetched):
        if tile is not None:
            image.paste(tile, (int(tx * 256 - left), int(ty * 256 - top)))
    draw = ImageDraw.Draw(image)
    placed = []
    def overlaps(box):
        return any(
            not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3])
            for b in placed
        )
    image = image.convert("RGBA")
    # 整个省份按结果上色：绿色测通、红色失败 / 超时、灰色无探针（半透明，底图照样看得清）
    filled = set()
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shade = ImageDraw.Draw(overlay)
    for index, p in enumerate(points):
        try:
            rings = _province_rings(p.get("label"))
        except Exception as e:
            print("省份上色失败：", e)
            rings = None
        if not rings:
            continue
        color = PING_FILL_COLORS[p["state"]]
        for ring in rings:
            xy = []
            for lon, lat in ring:
                wx, wy = _world_xy(lat, lon, zoom)
                xy.append((wx - left, wy - top))
            if len(xy) < 3:
                continue
            shade.polygon(xy, fill=color + (PING_FILL_ALPHA,))
            shade.line(xy + [xy[0]], fill=PING_EDGE_COLORS[p["state"]] + (255,), width=2)
        filled.add(index)
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)
    bold = _load_cjk_bold(17) if font else None
    for index, (p, (wx, wy)) in enumerate(zip(points, coords)):
        x, y = wx - left, wy - top
        # 省份已经整块上色的不再画圆点；没有边界数据（没上色）的才用圆点标出来
        if index in filled:
            continue
        r = 11 if label_filter(p) else 7
        _draw_marker(image, draw, x, y, r, PING_COLORS[p["state"]])
        placed.append((x - r - 4, y - r - 4, x + r + 4, y + r + 4))
    for p, (wx, wy) in zip(points, coords):
        if not label_filter(p):
            continue
        x, y = wx - left, wy - top
        if not PING_MAP_LABELS or not font:
            continue  # 不画文字标签：通不通看省份颜色，地区和延迟看下方文字结果
        text = p["label"]
        use_font = bold or small
        tw = draw.textlength(text, font=use_font)
        th = 24
        gap = 17
        options = [
            (x + gap, y - th / 2),
            (x - gap - tw - 12, y - th / 2),
            (x - (tw + 12) / 2, y + gap),
            (x - (tw + 12) / 2, y - gap - th),
        ]
        for step in (1, 2, 3):
            dy = step * (th + 4)
            for bx in (x + gap, x - gap - tw - 12):
                options.append((bx, y - th / 2 - dy))
                options.append((bx, y - th / 2 + dy))
        box = None
        for bx, by in options:
            candidate = (bx, by, bx + tw + 12, by + th)
            if candidate[0] >= 2 and candidate[2] <= width - 2 and candidate[1] >= 2 \
                    and candidate[3] <= height - 2 and not overlaps(candidate):
                box = candidate
                break
        if box is None:
            inside = [
                (bx, by, bx + tw + 12, by + th) for bx, by in options
                if bx >= 2 and bx + tw + 12 <= width - 2 and by >= 2 and by + th <= height - 2
            ]
            if inside:
                box = inside[0]
            else:
                bx = min(max(2, options[0][0]), width - tw - 14)
                by = min(max(2, options[0][1]), height - th - 2)
                box = (bx, by, bx + tw + 12, by + th)
        placed.append(box)
        cy = (box[1] + box[3]) / 2
        cx = box[0] if box[0] > x else box[2]
        if abs(cy - y) > th:
            draw.line([(x, y), (cx, cy)], fill=(40, 40, 40), width=3)
            draw.line([(x, y), (cx, cy)], fill=PING_COLORS[p["state"]], width=1)
        shadow = (box[0] + 2, box[1] + 2, box[2] + 2, box[3] + 2)
        draw.rounded_rectangle(shadow, radius=7, fill=(0, 0, 0, 90))
        draw.rounded_rectangle(box, radius=7, fill=PING_LABEL_COLORS[p["state"]],
                               outline=(255, 255, 255), width=2)
        draw.text((box[0] + 6, box[1] + th / 2), text, font=use_font,
                  fill=(255, 255, 255), anchor="lm")
    # 海外节点（东京等）不参与取景，但正好落在画面里的，也用圆点标出结果
    for p in extra or []:
        wx, wy = _world_xy(p["lat"], p["lon"], zoom)
        x, y = wx - left, wy - top
        if 16 <= x <= width - 16 and 50 <= y <= height - 16:
            _draw_marker(image, draw, x, y, 9, PING_COLORS[p["state"]])
    title_font = font or small
    tw = draw.textlength(title, font=title_font)
    draw.rounded_rectangle((10, 10, 10 + tw + 20, 40), radius=8, fill=(33, 37, 41))
    draw.text((20, 25), title, font=title_font, fill=(255, 255, 255), anchor="lm")
    return image.convert("RGB")
_PING_PLACEHOLDER = {}

def ping_placeholder_map():
    """测试中的占位图：所有测试点灰色，标“测试中”。只生成一次。"""
    if _PING_PLACEHOLDER.get("img") is None:
        try:
            _PING_PLACEHOLDER["img"] = _render_ping_map(
                [{"error": "测试中", "pending": True} for _node in GLOBAL_PING_NODES]
            )
        except Exception as e:
            print("生成占位地图失败：", e)
    return _PING_PLACEHOLDER.get("img")

def _render_ping_map(results):
    if not PIL_OK:
        return None
    points = _ping_points(results)
    font = _load_cjk_font(16)
    small = _load_font(14)
    width = 800
    china = [p for p in points if p["china"]]
    if not china:
        return None
    others = [p for p in points if not p["china"]]
    panels = [_map_panel(
        china, width, 560, "中国" if font else "China", 6,
        lambda p: True, font, small, extra=others
    )]
    legend_h = 40
    total = sum(p.height for p in panels) + legend_h
    canvas = Image.new("RGB", (width, total), (255, 255, 255))
    y = 0
    for panel in panels:
        canvas.paste(panel, (0, y))
        y += panel.height
    draw = ImageDraw.Draw(canvas)
    legend = [("ok", "成功" if font else "OK"), ("fail", "失败/超时" if font else "Failed"),
              ("none", "无探针" if font else "No probe")]
    x = 16
    for state, text in legend:
        draw.ellipse((x - 1, y + 9, x + 19, y + 29), fill=(25, 25, 25))
        draw.ellipse((x + 1, y + 11, x + 17, y + 27), fill=PING_COLORS[state])
        draw.text((x + 24, y + 20), text, font=font or small, fill=(40, 40, 40), anchor="lm")
        x += 46 + draw.textlength(text, font=font or small)
    buffer = io.BytesIO()
    canvas.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()
IP2REGION_FILE = "/root/ip2region_v4.xdb"
IP2REGION_URLS = [
    "https://raw.githubusercontent.com/lionsoul2014/ip2region/master/data/ip2region_v4.xdb",
    "https://cdn.jsdelivr.net/gh/lionsoul2014/ip2region@master/data/ip2region_v4.xdb",
]
_IP2REGION = {"buf": None, "tried": 0}

def _ip2region_load():
    """加载离线库；文件不存在就下载。失败时 1 小时内不再重试。"""
    if _IP2REGION["buf"] is not None:
        return _IP2REGION["buf"]
    if time.time() - _IP2REGION["tried"] < 3600:
        return None
    _IP2REGION["tried"] = time.time()
    if not os.path.exists(IP2REGION_FILE) or os.path.getsize(IP2REGION_FILE) < 1_000_000:
        for url in IP2REGION_URLS:
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = response.read()
                if len(payload) < 1_000_000:
                    continue
                temp = IP2REGION_FILE + ".tmp"
                with open(temp, "wb") as f:
                    f.write(payload)
                os.replace(temp, IP2REGION_FILE)
                print("ip2region 离线库下载完成：", url)
                break
            except Exception as e:
                print("下载 ip2region 失败：", url, e)
    try:
        with open(IP2REGION_FILE, "rb") as f:
            _IP2REGION["buf"] = f.read()
    except Exception as e:
        print("读取 ip2region 失败：", e)
    return _IP2REGION["buf"]

def ip2region_search(ip):
    """返回 dict(country, province, city, isp, code) 或 None（仅 IPv4）。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.version != 4:
        return None
    buf = _ip2region_load()
    if not buf:
        return None
    packed = addr.packed
    value = int(addr)
    idx = 256 + (packed[0] * 256 + packed[1]) * 8
    s_ptr = int.from_bytes(buf[idx:idx + 4], "little")
    e_ptr = int.from_bytes(buf[idx + 4:idx + 8], "little")
    if not s_ptr or not e_ptr:
        return None
    low, high = 0, (e_ptr - s_ptr) // 14
    while low <= high:
        mid = (low + high) >> 1
        p = s_ptr + mid * 14
        start = int.from_bytes(buf[p:p + 4], "little")
        end = int.from_bytes(buf[p + 4:p + 8], "little")
        if value < start:
            high = mid - 1
        elif value > end:
            low = mid + 1
        else:
            length = int.from_bytes(buf[p + 8:p + 10], "little")
            ptr = int.from_bytes(buf[p + 10:p + 14], "little")
            parts = buf[ptr:ptr + length].decode("utf-8", "replace").split("|")
            parts += [""] * (5 - len(parts))
            clean = lambda x: "" if x in ("0", "Reserved") else x
            return {
                "country": clean(parts[0]),
                "province": clean(parts[1]),
                "city": clean(parts[2]),
                "isp": clean(parts[3]),
                "code": clean(parts[4]),
            }
    return None
CARRIER_BY_ASN = {
    4134: "电信", 4809: "电信", 23764: "电信", 4811: "电信", 4812: "电信",
    4813: "电信", 4816: "电信", 23724: "电信", 4835: "电信",
    4837: "联通", 9929: "联通", 10099: "联通", 4808: "联通", 17621: "联通",
    17622: "联通", 17623: "联通", 17816: "联通",
    9808: "移动", 58453: "移动", 58807: "移动", 9394: "移动", 24400: "移动",
    56040: "移动", 56041: "移动", 56042: "移动", 56044: "移动", 56046: "移动",
    56047: "移动", 56048: "移动", 132525: "移动",
}
RIPESTAT_NEIGHBOURS = "https://stat.ripe.net/data/asn-neighbours/data.json?resource=AS{asn}"
_UPSTREAM_CACHE = {}

def _has_cjk(text):
    return bool(text) and any("一" <= ch <= "鿿" for ch in text)

def _carrier_of_asn(asn, name=""):
    if asn in CARRIER_BY_ASN:
        return CARRIER_BY_ASN[asn]
    return _carrier_of(asn, name)

def _asn_upstreams(asn):
    """公开 BGP 数据里这个 AS 的上游（RIPEstat，缓存 1 天）。"""
    cached = _UPSTREAM_CACHE.get(asn)
    if cached and time.time() - cached[0] < 86400:
        return cached[1]
    try:
        reply = _http_json(RIPESTAT_NEIGHBOURS.format(asn=asn), timeout=8)
        ups = [
            n.get("asn") for n in ((reply or {}).get("data") or {}).get("neighbours") or []
            if n.get("type") == "left"
        ]
    except Exception as e:
        print("查询上游 AS 失败：", e)
        return None
    _UPSTREAM_CACHE[asn] = (time.time(), ups)
    return ups

def ip_extra_info(ip, api):
    """在线程里跑：离线库 + 上游 AS。"""
    extra = {"r2": ip2region_search(ip) if ip else None, "asn": None, "own": None, "ups": None}
    match = re.match(r"AS(\d+)", (api or {}).get("as") or "")
    if match:
        extra["asn"] = int(match.group(1))
    cn = (api or {}).get("countryCode") == "CN" or (extra["r2"] or {}).get("country") == "中国"
    if cn and extra["asn"]:
        extra["own"] = _carrier_of_asn(extra["asn"], (api or {}).get("as") or "")
        if not extra["own"]:
            ups = _asn_upstreams(extra["asn"])
            if ups is not None:
                extra["ups"] = list(dict.fromkeys(
                    c for c in (_carrier_of_asn(u) for u in ups) if c
                ))
    return extra
_IPINFO_CACHE = {}
IPAPI_FIELDS = "status,message,country,countryCode,regionName,city,lat,lon,as,query"

def _ip_info_bundle(ips):
    """
    一次查齐多个 IP 的信息，返回 {ip: (ip-api 结果, 附加信息)}：
      ip-api 用批量接口，一个请求查完所有 IP（不再一个 IP 一个请求）；
      离线库 + 上游 AS 各 IP 同时查。
    """
    from concurrent.futures import ThreadPoolExecutor
    now = time.time()
    out = {}
    need = []
    for ip in dict.fromkeys(i for i in ips if i):
        cached = _IPINFO_CACHE.get(ip)
        if cached and now - cached[0] < 600:
            out[ip] = (cached[1], cached[2])
        else:
            need.append(ip)
    if not need:
        return out
    apis = {}
    try:
        rows = _http_json(
            "http://ip-api.com/batch?lang=zh-CN&fields=" + IPAPI_FIELDS,
            payload=[{"query": ip} for ip in need], timeout=8
        )
        for ip, row in zip(need, rows or []):
            apis[ip] = row or {}
    except Exception as e:
        print("批量查询 IP 信息失败，改为逐个查询：", e)
        with ThreadPoolExecutor(max_workers=min(10, len(need))) as pool:
            for ip, row in zip(need, pool.map(lambda i: _safe(_lookup_ip_info, i, {}), need)):
                apis[ip] = row
    with ThreadPoolExecutor(max_workers=min(10, len(need))) as pool:
        extras = list(pool.map(lambda i: _safe(ip_extra_info, i, {}, apis.get(i)), need))
    for ip, extra in zip(need, extras):
        api = apis.get(ip) or {}
        out[ip] = (api, extra)
        if api.get("status") == "success":
            _IPINFO_CACHE[ip] = (now, api, extra)
    if len(_IPINFO_CACHE) > 500:
        _IPINFO_CACHE.clear()
    return out

def _safe(fn, arg, default, *more):
    """调用出错时返回默认值（并发查询时一个失败不影响其他）。"""
    try:
        return fn(arg, *more)
    except Exception as e:
        print(f"{getattr(fn, '__name__', fn)} 失败：", arg, e)
        return default
CN_PROVINCE_ZH = {
    "beijing": "北京", "shanghai": "上海", "tianjin": "天津", "chongqing": "重庆",
    "guangdong": "广东", "zhejiang": "浙江", "jiangsu": "江苏", "fujian": "福建",
    "shandong": "山东", "henan": "河南", "hebei": "河北", "hubei": "湖北", "hunan": "湖南",
    "anhui": "安徽", "jiangxi": "江西", "sichuan": "四川", "shaanxi": "陕西", "shanxi": "山西",
    "liaoning": "辽宁", "jilin": "吉林", "heilongjiang": "黑龙江", "yunnan": "云南",
    "guizhou": "贵州", "guangxi": "广西", "hainan": "海南", "gansu": "甘肃", "qinghai": "青海",
    "ningxia": "宁夏", "xinjiang": "新疆", "xizang": "西藏", "tibet": "西藏",
    "neimenggu": "内蒙古", "mongolia": "内蒙古",
}

def _as_name_zh(as_info, location=""):
    """
    AS 名称里的拼音地名附上中文，例如 AS133776 Quanzhou（泉州）。
    AS 名称是公司注册地，和实际位置不一致时不加中文，免得看起来像另一个位置。
    """
    name = as_info.split(" ", 1)[1] if " " in as_info else ""
    found = []
    for token in re.findall(r"[A-Za-z]+", name):
        zh = CN_CITY_ZH.get(token.lower()) or CN_PROVINCE_ZH.get(token.lower())
        if zh and zh not in found:
            found.append(zh)
    if location and any(zh not in location for zh in found):
        return as_info
    return f"{as_info}（{'·'.join(found)}）" if found else as_info

def _short_place(name):
    for suffix in ("特别行政区", "自治区", "省", "市"):
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            return name[:-len(suffix)]
    return name
LATENCY_ANCHOR_MS = 5

def _line_text(place, own, ups):
    if own:
        return f"{place} {own}"
    if not ups:
        return f"{place} BGP"
    if len(ups) >= 3:
        return f"{place} 三线BGP（电信 / 联通 / 移动）"
    if len(ups) == 2:
        return f"{place} 双线BGP（{' / '.join(ups)}）"
    return f"{place} BGP（上游：{ups[0]}）"

def _latency_anchor(ping_results):
    """本次测试延迟最低、且 ≤ 5ms 的探针：服务器一定就在它附近。"""
    best = None
    for res in ping_results or []:
        rtt = res.get("rtt")
        if rtt is None or res.get("lat") is None or (res.get("loss") or 0) >= 100:
            continue
        if best is None or rtt < best["rtt"]:
            best = res
    if best and best["rtt"] <= LATENCY_ANCHOR_MS:
        return best
    return None

def ip_profile(api, extra, ping_results):
    """整理一个 IP 的信息：国家、位置、运营商、线路、AS。拿不到返回 None。"""
    api = api or {}
    if api.get("status") != "success":
        return None
    r2 = (extra or {}).get("r2") or {}
    as_info = api.get("as") or "未知"
    anchor = _latency_anchor(ping_results)
    if anchor:
        anchor_cc = (anchor.get("country") or "").upper()
        db_cc = (api.get("countryCode") or "").upper()
        far = False
        if api.get("lat") is not None:
            far = _haversine_km(
                float(anchor["lat"]), float(anchor["lon"]),
                float(api["lat"]), float(api["lon"])
            ) > 500
        if far or (anchor_cc and db_cc and anchor_cc != db_cc):
            place = _city_zh(anchor.get("city") or "")
            measured = f"（实测 {anchor['rtt']:.0f}ms）"
            if anchor_cc != "CN":
                return {
                    "cn": False,
                    "country": COUNTRY_ZH.get(anchor_cc, anchor_cc or "未知"),
                    "location": (place or "未知") + measured,
                    "note": "",
                    "isp": "",
                    "line": "",
                    "as": as_info,
                }
            own = (extra or {}).get("own")
            ups = (extra or {}).get("ups")
            return {
                "cn": True,
                "country": "中国",
                "location": (place or "未知") + measured,
                "note": "",
                "isp": own or (" / ".join(ups) if ups else ""),
                "line": _line_text(place, own, ups),
                "as": as_info,
            }
    special = (api.get("countryCode") or "").upper() in ("HK", "MO", "TW") or any(
        (r2.get("province") or "").startswith(x) for x in ("香港", "澳门", "台湾")
    )
    cn = not special and (api.get("countryCode") == "CN" or r2.get("country") == "中国")
    if not cn:
        return {
            "cn": False,
            "country": api.get("country") or "未知",
            "location": " ".join(p for p in (api.get("regionName"), api.get("city")) if p) or "未知",
            "note": "",
            "isp": "",
            "line": "",
            "as": as_info,
        }
    r2_prov = r2.get("province") or ""
    r2_city = r2.get("city") if _has_cjk(r2.get("city")) else ""
    api_prov = api.get("regionName") or ""
    api_city = api.get("city") if _has_cjk(api.get("city")) else ""
    as_provs = []
    for token in re.findall(r"[A-Za-z]+", as_info.split(" ", 1)[1] if " " in as_info else ""):
        zh = CN_PROVINCE_ZH.get(token.lower())
        if zh and zh not in as_provs:
            as_provs.append(zh)
    votes = {}
    for prov in [r2_prov, api_prov] + as_provs:
        if prov:
            votes[prov[:2]] = votes.get(prov[:2], 0) + 1
    winner = None
    if votes:
        top = max(votes.values())
        leaders = [k for k, v in votes.items() if v == top]
        winner = r2_prov[:2] if r2_prov[:2] in leaders else leaders[0]
    note = ""
    use_r2 = bool(r2_prov) and r2_prov[:2] == winner
    if use_r2:
        province, city = r2_prov, r2_city
    elif api_prov and api_prov[:2] == winner:
        province, city = api_prov, api_city
    elif winner:
        province, city = next(p for p in as_provs if p[:2] == winner), ""
    else:
        province, city = r2_prov or api_prov, r2_city or api_city
    if city and _short_place(city) == _short_place(province):
        city = ""
    location = " ".join(p for p in (province, city) if p) or "未知"
    isp = ""
    if use_r2 and _has_cjk(r2.get("isp")):
        isp = r2["isp"].replace("中国", "") or r2["isp"]
    own = (extra or {}).get("own")
    ups = (extra or {}).get("ups")
    if not isp and own:
        isp = own
    if not isp and ups:
        isp = " / ".join(ups)
    line = _line_text(_short_place(city or province), own, ups)
    return {
        "cn": True,
        "country": r2.get("country") or api.get("country") or "中国",
        "location": location,
        "note": note,
        "isp": isp,
        "line": line,
        "as": _as_name_zh(as_info, location),
    }

def ip_info_text(api, extra, ping_results):
    """单个 IP：多行详细信息。"""
    prof = ip_profile(api, extra, ping_results)
    if not prof:
        return "⚠️ 暂时无法获取 IP 信息"
    # 紧凑排版：国家和位置合成一行，运营商和线路合成一行，AS 名称太长就截短，避免换行
    country = prof["country"] or ""
    location = prof["location"] or ""
    place = location if (not country or country in location) else f"{country} {location}"
    lines = [f"📌 {html.escape(place.strip())}{html.escape(prof['note'])}"]
    extra_bits = [x for x in (prof["isp"], prof["line"]) if x]
    if extra_bits:
        lines.append("🏢 " + html.escape(" · ".join(extra_bits)))
    as_text = (prof["as"] or "").split(",")[0].strip()
    if len(as_text) > 30:
        as_text = as_text[:29] + "…"
    lines.append(f"🕸️ {html.escape(as_text)}")
    return "\n".join(lines)

def ip_place(api, extra, ping_results):
    """
    多个 IP 时每行用的位置信息，返回 (国家 或 None, 地点)：
      国内 / 港澳台  → (None, "广东省 深圳市") / (None, "香港 Kowloon")
      其他国家       → ("英国", "英格兰 伦敦")，括号里写国家，后面只写地区城市
    不带运营商、AS（运营商已经写在括号里）。
    """
    prof = ip_profile(api, extra, ping_results)
    if not prof:
        return None, ""
    location = re.sub(r"（实测 \d+ms）$", "", prof.get("location") or "")
    location = "" if location == "未知" else location
    location = " ".join(w for w in location.split() if _has_cjk(w))
    if prof["cn"]:
        return None, location
    country = (prof.get("country") or "").replace("中国", "") or "未知"
    if country in ("香港", "澳门", "台湾"):
        if location.startswith(country):
            location = location[len(country):].strip()
        return None, f"{country} {location}".strip()
    if location.startswith(country):
        location = location[len(country):].strip()
    return country, location
GLOBAL_PING_DEFAULT_PORT = 443

def _parse_global_ping_input(text):
    """
    解析输入，返回 (host, port, 协议) 或 None：
      1.2.3.4 / example.com        -> ICMP
      1.2.3.4:443                  -> TCP 443
      tcp 1.2.3.4:22 / 1.2.3.4:22 tcp          -> TCP
    """
    # 中文输入法打出来的全角冒号 / 逗号也认
    text = text.strip().replace("：", ":").replace("，", " ")
    proto = None
    lowered = text.lower()
    if lowered.startswith("tcp://"):
        proto, text = "TCP", text[len("tcp://"):]
    parts = re.split(r"[\s/]+", text)
    words = [w for w in parts if w.lower() in ("tcp", "icmp")]
    if words:
        proto = words[0].upper()
    rest = [w for w in parts if w and w.lower() not in ("tcp", "icmp")]
    if not rest:
        return None
    target = _parse_ping_target(rest[0])
    if target is None:
        return None
    host, port = target
    # 「IP 端口」中间用空格隔开也行（和端口扫描的写法一样）
    if not port and len(rest) > 1 and rest[1].isdigit() and 1 <= int(rest[1]) <= 65535:
        port = rest[1]
    if proto is None:
        proto = "TCP" if port else "ICMP"
    if proto == "ICMP":
        port = None
    elif not port:
        port = GLOBAL_PING_DEFAULT_PORT
    return host, (int(port) if port else None), proto
GPING_RECENT_FILE = "/root/nav_gping_recent.json"
GPING_RECENT_MAX = 10  # 最近记录最多保存 10 条（四个功能共用）

def _load_gping_recent():
    try:
        with open(GPING_RECENT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
GPING_RECENT = _load_gping_recent()

def _save_gping_recent():
    try:
        temp = GPING_RECENT_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(GPING_RECENT, f, ensure_ascii=False)
        os.replace(temp, GPING_RECENT_FILE)
    except Exception as e:
        print("保存全球Ping记录失败：", e)

def _gping_label(item):
    """记录的显示文字：1.2.3.4 / example.com:443"""
    host, port = item["host"], item.get("port")
    if not port:
        return host
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"

def gping_recent_add(user_id, host, port, proto):
    """测过的目标放到最前面（重复的去掉），最多保留 GPING_RECENT_MAX 条。"""
    key = str(user_id)
    item = {"host": host, "port": port, "proto": proto, "t": time.time()}
    items = [
        i for i in GPING_RECENT.get(key, [])
        if (i["host"], i.get("port")) != (host, port)
    ]
    GPING_RECENT[key] = ([item] + items)[:GPING_RECENT_MAX]
    _save_gping_recent()

def gping_recent_list(user_id):
    return GPING_RECENT.get(str(user_id), [])

def gping_recent_rows(user_id):
    """记录按钮：两个一行，最后一行是清空。没有记录返回 []。"""
    items = gping_recent_list(user_id)
    if not items:
        return []
    labels = _left_labels([_gping_label(item) for item in items])
    buttons = [InlineKeyboardButton(label, callback_data=f"gp|H|{i}") for i, label in enumerate(labels)]
    return _left_rows(buttons, InlineKeyboardButton("🧹 清空记录", callback_data="gp|HCLR"))

async def handle_global_ping(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    parsed = _parse_global_ping_input(update.message.text)
    if parsed is None:
        await panel_notice(
            update.message.get_bot(), update.message.chat_id,
            "❌ 地址格式不正确，请重新发送 IP 或域名。", _home_markup()
        )
        return
    host, port, proto = parsed
    await run_global_ping(
        update.message.get_bot(),
        update.message.chat_id,
        update.effective_user.id,
        context,
        host, port, proto
    )

async def global_ping_switch(query, context):
    """全球Ping 结果 / 页面上的按钮：测试记录、只测某个 IP、自定义端口。"""
    action = query.data.split("|", 1)[1]
    if action.startswith("H|"):
        items = gping_recent_list(query.from_user.id)
        try:
            item = items[int(action.split("|", 1)[1])]
        except (ValueError, IndexError):
            await global_ping(query, context)
            return
        context.user_data["state"] = "global_ping"
        await run_global_ping(
            query.message.get_bot(),
            query.message.chat_id,
            query.from_user.id,
            context,
            item["host"], item.get("port"), item.get("proto") or "ICMP"
        )
        return
    if action == "HCLR":
        if GPING_RECENT.pop(str(query.from_user.id), None) is not None:
            _save_gping_recent()
        try:
            await query.answer("已清空测试记录")
        except Exception:
            pass
        await global_ping(query, context)
        return
    if action in ("HIST", "HLIST"):
        await gping_recent_page(query, context)
        return
    last = context.user_data.get("gping_last")
    if not last:
        await edit_page(query, "❌ 请先发送要测试的 IP 或域名。", reply_markup=_home_markup())
        return
    proto = action
    host, _port, last_proto = last
    if proto.startswith("IP|"):
        ip = proto.split("|", 1)[1]
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return
        context.user_data["state"] = "global_ping"
        group = context.user_data.get("gping_group")
        if group and ip in group["ips"]:
            _port, last_proto = group["port"], group["proto"]
        else:
            group = None
        await run_global_ping(
            query.message.get_bot(),
            query.message.chat_id,
            query.from_user.id,
            context,
            ip, _port, last_proto,
            remember=False,
            group=group
        )
        return
    if proto == "ALL":
        group = context.user_data.get("gping_group")
        if not group:
            await global_ping(query, context)
            return
        context.user_data["state"] = "global_ping"
        await run_global_ping(
            query.message.get_bot(),
            query.message.chat_id,
            query.from_user.id,
            context,
            group["domain"], group["port"], group["proto"],
            remember=False
        )
        return
    if proto == "PORT":
        context.user_data["state"] = "gping_port"
        await panel_notice(
            query.message.get_bot(),
            query.message.chat_id,
            "⚙️ <b>自定义端口</b>\n\n"
            f"目标：<code>{html.escape(host)}</code>\n"
            "请发送端口号，例如 <code>8443</code>\n"
            "发送后用 TCP 测这个端口",
            InlineKeyboardMarkup([[_btn("❌ 取消", "home")]])
        )
        return

async def handle_global_ping_port(update, context):
    last = context.user_data.get("gping_last")
    text = update.message.text.strip().lower()
    match = re.fullmatch(r"(?:tcp\s*)?(\d{1,5})(?:\s*tcp)?", text)
    if not last or not match or not 1 <= int(match.group(1)) <= 65535:
        await panel_notice(
            update.message.get_bot(), update.message.chat_id,
            "❌ 请发送 1～65535 之间的端口号，例如 8443", _home_markup()
        )
        return
    host, _port, _proto = last
    proto = "TCP"
    context.user_data["state"] = "global_ping"
    await run_global_ping(
        update.message.get_bot(),
        update.message.chat_id,
        update.effective_user.id,
        context,
        host, int(match.group(1)), proto
    )

async def run_global_ping(bot, chat_id, user_id, context, host, port, proto, remember=True, group=None):
    """
    group：从域名结果里点“测试N”单测某个 IP 时，传入这个域名的 IP 列表，
    结果页继续显示 测试1~N 按钮（当前这个打 ✅），还能点“全部”回到整个域名。
    """
    limited = rate_check(user_id, 1)
    if limited:
        await panel_notice(bot, chat_id, "⏳ " + html.escape(limited), _home_markup())
        return
    context.user_data["gping_last"] = (host, port, proto)
    t0 = time.time()
    timing = {}
    if remember:
        gping_recent_add(user_id, host, port, proto)
    placeholder = await asyncio.to_thread(ping_placeholder_map)
    progress = await Progress(
        bot, chat_id,
        f"正在全球 {proto} 测试……", host if not port else f"{host}:{port}",
        [f"{n['flag']} {_global_ping_label(n)}" for n in GLOBAL_PING_NODES], expected=8,
        photo=placeholder, icon="🌍"
    ).start()
    plans = await asyncio.to_thread(_plan_global_ping)
    async def ping_node(index, candidates):
        node = GLOBAL_PING_NODES[index]
        if not candidates:
            res = {"error": "无探针"}
        else:
            progress.set(index, "run", "测试中")
            try:
                res = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        _GP_POOL, _global_ping_one, host, port, candidates, proto,
                        _global_ping_label(node)
                    ),
                    GLOBAL_PING_NODE_MAX
                )
            except asyncio.TimeoutError:
                print("全球Ping 节点等待超时：", node["name"])
                res = {"error": "超时"}
        latency = _global_ping_cell(res)
        progress.items[index]["name"] = f"{node['flag']} {_global_ping_label(node)}"
        progress.set(index, "fail" if res.get("error") else "done", latency)
        return res
    ping_task = asyncio.gather(*[
        ping_node(i, candidates)
        for i, candidates in enumerate(plans)
    ])
    ping_task.add_done_callback(lambda _f: timing.__setitem__("测速", time.time() - t0))
    tcp_target = host
    if port:
        tcp_target = f"{host}:{port}"
    is_domain = True
    try:
        ipaddress.ip_address(host)
        is_domain = False
    except ValueError:
        pass
    resolved = []
    if is_domain:
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    host, None, type=socket.SOCK_STREAM
                ),
                timeout=5
            )
            resolved = list(dict.fromkeys(info[4][0] for info in infos))
            resolved.sort(key=lambda ip: ":" in ip)
        except Exception as e:
            print("全球Ping解析域名失败：", e)
    ip_sources = {}
    if is_domain:
        try:
            ip_sources = await asyncio.to_thread(resolve_all_ips, host)
        except Exception as e:
            print("多地解析失败：", e)
        for ip in resolved:
            ip_sources.setdefault(ip, []).append("本机")
        order = {"电信": 0, "联通": 1, "移动": 2, "海外": 3, "本机": 4}
        resolved = sorted(
            ip_sources,
            key=lambda ip: (":" in ip, min(order.get(l, 5) for l in ip_sources[ip]))
        )
    timing["解析"] = time.time() - t0
    if is_domain and not resolved:
        try:
            fallback = await asyncio.to_thread(_lookup_ip_info, host)
            ipaddress.ip_address(fallback.get("query") or "")
            resolved = [fallback["query"]]
        except Exception:
            pass
    first_ip = resolved[0] if resolved else (host if not is_domain else None)
    info_task = asyncio.create_task(asyncio.to_thread(
        _ip_info_bundle, (resolved[:len(IP_MARKS)] if is_domain else [host])
    ))
    if is_domain:
        if resolved:
            ip_text = "、".join(
                f"<code>{html.escape(ip)}</code>" for ip in resolved[:4]
            )
            more = f"（共 {len(resolved)} 个）" if len(resolved) > 4 else ""
            address = (
                f"🏁 域名：<code>{html.escape(tcp_target)}</code>\n"
                f"🧩 解析 IP：{ip_text}{more}\n"
            )
        else:
            address = (
                f"🏁 域名：<code>{html.escape(tcp_target)}</code>\n"
                "🧩 解析 IP：❌ 解析失败（域名不存在或 DNS 未配置）\n"
            )
    else:
        address = f"🏁 IP：<code>{html.escape(tcp_target)}</code>\n"
        if group and host in group["ips"]:
            index = group["ips"].index(host)
            address += f"🔗 来自 <code>{html.escape(group['domain'])}</code> 的 {IP_MARKS[index]} 号 IP\n"
    ping_results = None
    try:
        ping_results = await ping_task
    except Exception as e:
        print("全球Ping测试失败：", e)
    all_ips = list(resolved)
    for res in ping_results or []:
        if res.get("ip") and res["ip"] not in all_ips:
            all_ips.append(res["ip"])
            ip_sources.setdefault(res["ip"], []).append("探针")
    total_ips = len(all_ips)
    if total_ips > len(IP_MARKS):
        hit = {r.get("ip") for r in ping_results or [] if r.get("ip")}
        all_ips = [ip for ip in all_ips if ip in hit] + [ip for ip in all_ips if ip not in hit]
    all_ips = all_ips[:len(IP_MARKS)]
    multi = is_domain and len(all_ips) > 1
    ip_marks = {ip: IP_MARKS[i] for i, ip in enumerate(all_ips[:len(IP_MARKS)])} if multi else None
    if ping_results is not None:
        latency = _global_ping_table(ping_results, port, proto, ip_marks)
    else:
        latency = "🌎 全球延迟：❌ 测试失败"
    t_info = time.time()
    try:
        infos = await info_task
    except Exception as e:
        print("查询 IP 信息失败：", e)
        infos = {}
    result, extra = infos.get(first_ip) or ({}, {})
    timing["测速后再等IP信息"] = time.time() - t_info
    info = ""
    if multi:
        missing = [ip for ip in all_ips if ip not in infos]
        if missing:
            infos.update(await asyncio.to_thread(_ip_info_bundle, missing))
        def brief(ip):
            try:
                api, ext = infos.get(ip) or ({}, {})
                hits = [r for r in ping_results or [] if r.get("ip") == ip]
                return ip_place(api, ext, hits)
            except Exception as e:
                print("查询 IP 信息失败：", ip, e)
                return None, ""
        briefs = [brief(ip) for ip in all_ips]
        ip_lines, ip_lines_short = [], []
        for i, ip in enumerate(all_ips):
            country, detail = briefs[i]
            if country:
                where = f"（{html.escape(country)}）"
            else:
                sources = [l for l in ip_sources.get(ip, []) if l != "本机"] or ip_sources.get(ip, [])
                where = f"（{html.escape('·'.join(sources))}）" if sources else ""
            line = f"{IP_MARKS[i]} <code>{html.escape(ip)}</code>{where}"
            ip_lines_short.append(line)
            ip_lines.append(line + (html.escape(detail) if detail else ""))
        more = f"（显示前 {len(all_ips)} 个）" if total_ips > len(all_ips) else ""
        def ip_block(lines):
            return (
                f"🏁 域名：<code>{html.escape(tcp_target)}</code>\n"
                f"🧩 解析到 {total_ips} 个 IP{more}\n"
                + "\n".join(lines) + "\n"
            )
        address = ip_block(ip_lines)
        address_short = ip_block(ip_lines_short)
    else:
        try:
            info = ip_info_text(result, extra, ping_results)
        except Exception as e:
            print("生成 IP 信息失败：", e)
            info = "⚠️ 暂时无法获取 IP 信息"
    image = None
    t_map = time.time()
    if ping_results:
        try:
            image = await asyncio.to_thread(_render_ping_map, ping_results)
        except Exception as e:
            print("生成全球Ping地图失败：", e)
    timing["画地图"] = time.time() - t_map
    await progress.stop()
    print(
        f"全球Ping {host} 用时 {time.time() - t0:.1f}s："
        + "，".join(f"{k} {v:.1f}s" for k, v in timing.items())
    )
    text = (
        "🌍 <b>全球 Ping</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"{address}"
        + (f"{info}\n" if info else "")
        + "\n"
        f"{latency}\n\n"
        + ("👇 点下方 ①②③… 只测对应编号的 IP\n" if multi else "")
        + "💡 发送其他 IP 可直接更新本消息"
    )
    if _visible_len(text) > 1000 and multi:
        text = text.replace(address, address_short, 1)
    ip_rows = []
    if multi:
        group = {"domain": host, "port": port, "proto": proto, "ips": list(all_ips)}
        context.user_data["gping_group"] = group
    if group and (multi or host in group["ips"]):
        # 按钮直接用圈号 ①②③…，一排最多 8 个（Telegram 一行最多 8 个按钮）
        ip_buttons = [
            InlineKeyboardButton(
                # 当前正在看的那个 IP 用实心圈号 ❶❷❸… 表示选中，不再在前面加 ✅（按钮太窄会挤在一起）
                (IP_MARKS_SELECTED[i] if not multi and ip == host else IP_MARKS[i]),
                callback_data=f"gp|IP|{ip}"
            )
            for i, ip in enumerate(group["ips"])
        ]
        ip_rows = _rows(ip_buttons, 8)
    markup = InlineKeyboardMarkup(ip_rows + [
        [
            InlineKeyboardButton("⚙️ 自定义端口", callback_data="gp|PORT"),
            InlineKeyboardButton("🗃 测试记录", callback_data="gp|HIST"),
        ],
        [
            InlineKeyboardButton(
                "🏠 返回首页",
                callback_data="home"
            )
        ]
    ])
    if image is not None:
        await panel_photo(bot, chat_id, image, text, markup)
    elif placeholder is not None:
        await panel_caption(bot, chat_id, text, markup)
    else:
        await _panel_show(bot, chat_id, text, markup)
GLOBALPING_API = "https://api.globalping.io/v1/measurements"

class ProbeUnavailable(RuntimeError):
    """该位置没有可用探针 / 参数不被接受。"""
GLOBALPING_TOKEN = (
    os.environ.get("GLOBALPING_TOKEN") or CONFIG.get("globalping_token") or ""
)
MAP_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_USER_AGENT = "TelegramNavBot/1.0 (personal route map)"

def _carrier_fallbacks(name, shanghai_asns, national_asns,
                       cities=("Shanghai", "Beijing", "Guangzhou", "Shenzhen")):
    """
    探针备选顺序：按 cities 的顺序逐个城市找该运营商的探针，
    上海还会再按 ASN 精确找一次，最后全国范围按 ASN 兜底。
    """
    chain = []
    for city in cities:
        chain.append({"magic": f"{city}+{name}"})
        if city == "Shanghai":
            chain += [{"city": "Shanghai", "asn": asn} for asn in shanghai_asns]
    chain += [{"country": "CN", "asn": asn} for asn in national_asns]
    return chain
# 运营商圆点颜色和地图上的线条颜色一致：电信蓝、联通红、移动绿
CARRIER_DOTS = {"电信": "🔵", "联通": "🔴", "移动": "🟢"}
TRACE_SOURCES = [
    ("🔵 电信", "SH_CT", _carrier_fallbacks("China Telecom", [4812, 4134], [4134, 4812, 4811, 4813])),
    ("🔴 联通", "SH_CU", _carrier_fallbacks("China Unicom", [17621, 4837], [4837, 9929, 17621])),
    ("🟢 移动", "SH_CM", _carrier_fallbacks("China Mobile", [24400, 9808], [9808, 56040, 56046, 24400])),
]

def _expected_carrier(locations):
    """从备选条件里的 magic（China Telecom / Unicom / Mobile）推出应有的运营商。"""
    for location in locations or []:
        text = str(location.get("magic") or "").lower()
        for key, name in (("telecom", "电信"), ("unicom", "联通"), ("mobile", "移动")):
            if key in text:
                return name
    return None

def _probe_carrier(source):
    """探针自身所属运营商；认不出来返回 None（不据此丢弃）。"""
    asn = source.get("asn")
    try:
        asn = int(asn) if asn is not None else None
    except (TypeError, ValueError):
        asn = None
    if asn in CARRIER_BY_ASN:
        return CARRIER_BY_ASN[asn]
    return _carrier_of(asn, source.get("network"))

def _trace_with_fallback(target, locations):
    """按顺序尝试备选探针条件（上海 → 北京 → 广州 → 深圳 → 全国），返回 runs。"""
    if isinstance(locations, dict):
        return _trace_globalping(target, locations)
    last_error = None
    expected = _expected_carrier(locations)
    for location in locations:
        try:
            runs = _trace_globalping(target, location)
        except ProbeUnavailable as e:
            last_error = e
            print("该条件没有可用探针，尝试下一个：", location, e)
            continue
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RuntimeError("Globalping 额度用完或请求太频繁，请稍后再试。")
            last_error = e
            print("该条件追踪出错，尝试下一个：", location, e)
            continue
        except (RuntimeError, OSError) as e:
            if "429" in str(e) or "频繁" in str(e) or "额度" in str(e):
                raise
            last_error = e
            print("该条件追踪失败，尝试下一个：", location, e)
            continue
        if expected:
            matched = [
                (s, h) for s, h in runs
                if _probe_carrier(s) in (None, expected)
            ]
            if not matched:
                print("探针运营商不符，尝试下一个：", location,
                      [_probe_carrier(s) for s, _h in runs])
                continue
            runs = matched
        return runs
    raise ProbeUnavailable(
        "Globalping 目前没有该运营商的在线探针（国内探针由志愿者运行，"
        "数量很少且经常上下线），请稍后再试或换一个起点。"
        + (f"\n{last_error}" if last_error else "")
    )

def _http_json(url, payload=None, headers=None, timeout=10):
    request_headers = {"User-Agent": "Mozilla/5.0"}
    if headers:
        request_headers.update(headers)
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers
    )
    with urllib.request.urlopen(
        request,
        timeout=timeout
    ) as response:
        return json.loads(
            response.read().decode("utf-8")
        )

def _http_error_text(error):
    try:
        detail = json.loads(
            error.read().decode("utf-8")
        )
        message = (detail.get("error") or {}).get("message")
        if message:
            return f"HTTP {error.code}：{message}"
    except Exception:
        pass
    if error.code == 429:
        return "请求过于频繁（免费额度用完），请稍后再试。"
    return f"HTTP {error.code}"
TRACE_PROBES = 3

def _responding(hops):
    return sum(1 for h in hops if h.get("ip"))

def _gp_parse_item(item):
    probe = item.get("probe") or {}
    detail = item.get("result") or {}
    if detail.get("status") != "finished":
        return None
    hops = []
    for index, hop in enumerate(detail.get("hops") or [], 1):
        rtts = [
            t.get("rtt")
            for t in (hop.get("timings") or [])
            if isinstance(t.get("rtt"), (int, float))
        ]
        hops.append({
            "n": index,
            "ip": hop.get("resolvedAddress"),
            "rtt": (sum(rtts) / len(rtts)) if rtts else None,
        })
    if not hops:
        return None
    while (
        len(hops) > 1
        and hops[-1]["ip"]
        and hops[-1]["ip"] == hops[-2]["ip"]
    ):
        hops.pop()
    target_ip = detail.get("resolvedAddress")
    label_parts = [
        probe.get("city") or "",
        probe.get("country") or "",
    ]
    label = " ".join(p for p in label_parts if p)
    if probe.get("network"):
        label += f" · {probe['network']}"
    source = {
        "label": label or "检测节点",
        "lat": probe.get("latitude"),
        "lon": probe.get("longitude"),
        "country": probe.get("country"),
        "network": probe.get("network") or "",
        "asn": probe.get("asn"),
        "target_ip": target_ip,
        "reached": bool(target_ip) and any(
            h["ip"] == target_ip for h in hops
        ),
    }
    return source, hops

def _gp_measure(target, location, options=None, measure_type="mtr", limit=None):
    headers = {}
    if GLOBALPING_TOKEN:
        headers["Authorization"] = "Bearer " + GLOBALPING_TOKEN
    body = {
        "type": measure_type,
        "target": target,
        "locations": [location],
        "limit": limit or TRACE_PROBES
    }
    if options:
        body["measurementOptions"] = options
    try:
        created = _http_json(
            GLOBALPING_API,
            body,
            headers
        )
    except urllib.error.HTTPError as e:
        if e.code in (400, 422):
            raise ProbeUnavailable(_http_error_text(e))
        raise RuntimeError(_http_error_text(e))
    measurement_id = created.get("id")
    if not measurement_id:
        raise RuntimeError("创建检测任务失败。")
    result = None
    deadline = time.time() + 40
    while time.time() < deadline:
        time.sleep(1.5)
        result = _http_json(
            GLOBALPING_API + "/" + measurement_id,
            headers=headers
        )
        if result.get("status") != "in-progress":
            break
    if not result or result.get("status") == "in-progress":
        raise RuntimeError("检测超时，请稍后重试。")
    items = result.get("results") or []
    if not items:
        raise ProbeUnavailable("该地区暂时没有可用的检测节点。")
    runs = []
    for item in items:
        parsed = _gp_parse_item(item)
        if parsed:
            runs.append(parsed)
    if not runs:
        raw = ((items[0].get("result") or {}).get("rawOutput") or "")
        raise RuntimeError(raw[:200] or "检测失败。")
    return runs

def _trace_globalping(target, location):
    try:
        tcp_runs = _gp_measure(
            target,
            location,
            {"protocol": "TCP", "port": 443, "packets": 3}
        )
    except ProbeUnavailable:
        raise
    except Exception as e:
        print("TCP 443 追踪失败，改用 ICMP：", e)
        tcp_runs = []
    if any(s.get("reached") for s, _h in tcp_runs):
        return tcp_runs
    try:
        icmp_runs = _gp_measure(target, location)
    except Exception:
        if tcp_runs:
            return tcp_runs
        raise
    def score(runs):
        return max(
            ((1 if s.get("reached") else 0, _responding(h)) for s, h in runs),
            default=(0, 0)
        )
    tcp_score = score(tcp_runs)
    icmp_score = score(icmp_runs)
    if icmp_score > tcp_score:
        return icmp_runs
    return tcp_runs or icmp_runs

def _geo_batch(ips):
    valid = []
    for ip in dict.fromkeys(ips):
        try:
            if ipaddress.ip_address(ip).is_global:
                valid.append(ip)
        except ValueError:
            continue
    result = {}
    for i in range(0, len(valid), 100):
        try:
            rows = _http_json(
                "http://ip-api.com/batch"
                "?lang=zh-CN"
                "&fields=status,country,countryCode,regionName,city,lat,lon,as,query",
                valid[i:i + 100]
            )
        except Exception as e:
            print("批量查询IP位置失败：", e)
            continue
        for row in rows:
            if row.get("status") == "success":
                result[row.get("query")] = row
    return result

def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    )
    return 6371 * 2 * math.asin(min(1.0, math.sqrt(a)))
IPMAP_API = "https://ipmap-api.ripe.net/v1/locate/all?resources="
IPMAP_MEASURED_ENGINES = {"latency", "single-radius", "simple-anycast", "ixp"}
TRUSTED_GEO = ("ipmap", "ping", "route")  # 只有实测结果算"已确认"：IPmap 实测引擎、多地 ping
CITY_TABLE = {
    "losangeles": ("洛杉矶", 34.05, -118.24, "US"),
    "sanjose": ("圣何塞", 37.34, -121.89, "US"),
    "santaclara": ("圣克拉拉", 37.35, -121.95, "US"),
    "paloalto": ("帕洛阿尔托", 37.44, -122.14, "US"),
    "sanfrancisco": ("旧金山", 37.77, -122.42, "US"),
    "fremont": ("弗里蒙特", 37.55, -121.99, "US"),
    "seattle": ("西雅图", 47.61, -122.33, "US"),
    "portland": ("波特兰", 45.52, -122.68, "US"),
    "lasvegas": ("拉斯维加斯", 36.17, -115.14, "US"),
    "phoenix": ("凤凰城", 33.45, -112.07, "US"),
    "denver": ("丹佛", 39.74, -104.99, "US"),
    "dallas": ("达拉斯", 32.78, -96.80, "US"),
    "houston": ("休斯顿", 29.76, -95.37, "US"),
    "chicago": ("芝加哥", 41.88, -87.63, "US"),
    "atlanta": ("亚特兰大", 33.75, -84.39, "US"),
    "miami": ("迈阿密", 25.76, -80.19, "US"),
    "ashburn": ("阿什本", 39.04, -77.49, "US"),
    "washington": ("华盛顿", 38.91, -77.04, "US"),
    "newyork": ("纽约", 40.71, -74.01, "US"),
    "newark": ("纽瓦克", 40.74, -74.17, "US"),
    "boston": ("波士顿", 42.36, -71.06, "US"),
    "toronto": ("多伦多", 43.65, -79.38, "CA"),
    "vancouver": ("温哥华", 49.28, -123.12, "CA"),
    "london": ("伦敦", 51.51, -0.13, "GB"),
    "amsterdam": ("阿姆斯特丹", 52.37, 4.90, "NL"),
    "frankfurt": ("法兰克福", 50.11, 8.68, "DE"),
    "paris": ("巴黎", 48.86, 2.35, "FR"),
    "marseille": ("马赛", 43.30, 5.37, "FR"),
    "madrid": ("马德里", 40.42, -3.70, "ES"),
    "milan": ("米兰", 45.46, 9.19, "IT"),
    "vienna": ("维也纳", 48.21, 16.37, "AT"),
    "stockholm": ("斯德哥尔摩", 59.33, 18.07, "SE"),
    "zurich": ("苏黎世", 47.38, 8.54, "CH"),
    "hongkong": ("香港", 22.32, 114.17, "HK"),
    "tokyo": ("东京", 35.68, 139.69, "JP"),
    "osaka": ("大阪", 34.69, 135.50, "JP"),
    "singapore": ("新加坡", 1.35, 103.82, "SG"),
    "taipei": ("台北", 25.03, 121.57, "TW"),
    "seoul": ("首尔", 37.57, 126.98, "KR"),
    "sydney": ("悉尼", -33.87, 151.21, "AU"),
    "mumbai": ("孟买", 19.08, 72.88, "IN"),
    "dubai": ("迪拜", 25.20, 55.27, "AE"),
    "saopaulo": ("圣保罗", -23.55, -46.63, "BR"),
    "shanghai": ("上海", 31.23, 121.47, "CN"),
    "beijing": ("北京", 39.90, 116.40, "CN"),
    "guangzhou": ("广州", 23.13, 113.26, "CN"),
    "shenzhen": ("深圳", 22.54, 114.06, "CN"),
}
CITY_CODES = {
    "lax": "losangeles", "lsanca": "losangeles",
    "sjc": "sanjose", "snjsca": "sanjose",
    "sfo": "sanfrancisco", "pao": "paloalto", "plalca": "paloalto",
    "sea": "seattle", "sttlwa": "seattle", "pdx": "portland",
    "phx": "phoenix", "den": "denver", "dnvrco": "denver",
    "dfw": "dallas", "dal": "dallas", "dllstx": "dallas",
    "iah": "houston", "hou": "houston", "hstntx": "houston",
    "ord": "chicago", "chi": "chicago", "chcgil": "chicago",
    "atl": "atlanta", "atlnga": "atlanta",
    "mia": "miami", "miamfl": "miami",
    "iad": "ashburn", "ash": "ashburn", "asbnva": "ashburn",
    "dca": "washington", "wdc": "washington",
    "nyc": "newyork", "jfk": "newyork", "nycmny": "newyork",
    "ewr": "newark", "nwrknj": "newark", "bos": "boston",
    "yyz": "toronto", "yvr": "vancouver",
    "lon": "london", "lhr": "london", "ldn": "london",
    "ams": "amsterdam", "fra": "frankfurt", "ffm": "frankfurt",
    "par": "paris", "cdg": "paris", "mrs": "marseille",
    "mad": "madrid", "mxp": "milan", "vie": "vienna",
    "arn": "stockholm", "zrh": "zurich",
    "hkg": "hongkong", "tyo": "tokyo", "nrt": "tokyo", "hnd": "tokyo",
    "tokyjp": "tokyo", "osa": "osaka", "kix": "osaka",
    "sin": "singapore", "sgp": "singapore", "sng": "singapore",
    "tpe": "taipei", "icn": "seoul", "sel": "seoul",
    "syd": "sydney", "bom": "mumbai", "dxb": "dubai",
    "gru": "saopaulo", "sao": "saopaulo",
}
DOMAIN_CITY_CODES = {
    "twelve99.net": {
        "las": "losangeles", "sjo": "sanjose", "nyk": "newyork",
        "dls": "dallas", "hnk": "hongkong",
    },
}
COUNTRY_ZH = {
    "US": "美国", "CN": "中国", "HK": "中国香港", "TW": "中国台湾",
    "MO": "中国澳门", "JP": "日本", "KR": "韩国", "SG": "新加坡",
    "GB": "英国", "DE": "德国", "FR": "法国", "NL": "荷兰",
    "CA": "加拿大", "AU": "澳大利亚", "IT": "意大利", "ES": "西班牙",
    "AT": "奥地利", "SE": "瑞典", "CH": "瑞士", "IN": "印度",
    "AE": "阿联酋", "BR": "巴西", "RU": "俄罗斯", "MY": "马来西亚",
    "TH": "泰国", "VN": "越南", "PH": "菲律宾", "ID": "印度尼西亚",
    "TR": "土耳其",
    "IL": "以色列",
    "FI": "芬兰",
    "PL": "波兰",
    "UA": "乌克兰",
    "AR": "阿根廷",
    "MX": "墨西哥",
    "ZA": "南非",
    "LU": "卢森堡",
    "IE": "爱尔兰",
    "NO": "挪威",
    "DK": "丹麦",
    "CZ": "捷克",
    "RO": "罗马尼亚",
    "BG": "保加利亚",
    "SC": "塞舌尔",
    "PA": "巴拿马",
    "KH": "柬埔寨",
}

def _country_zh(code, ipdb_info=None):
    code = (code or "").upper()
    if ipdb_info and (ipdb_info.get("countryCode") or "").upper() == code:
        return ipdb_info.get("country") or code
    return COUNTRY_ZH.get(code, code)

def _ipmap_batch(ips):
    result = {}
    for i in range(0, len(ips), 40):
        chunk = ips[i:i + 40]
        try:
            reply = _http_json(
                IPMAP_API + ",".join(chunk),
                timeout=8
            )
        except Exception as e:
            print("IPmap 查询失败：", e)
            continue
        for ip, row in ((reply or {}).get("data") or {}).items():
            if not isinstance(row, dict):
                continue
            if isinstance(row.get("location"), dict):
                row = row["location"]
            if row.get("latitude") is None or row.get("longitude") is None:
                continue
            # 只用 IPmap 实测引擎（延迟测量 / 单点半径 / 任播检测 / IXP）给出的位置；
            # 只靠反向域名字面匹配或人口权重"猜"出来的位置一律不用
            engines = set((row.get("contributions") or {}).keys())
            if not engines & IPMAP_MEASURED_ENGINES:
                continue
            result[ip] = row
    return result

def _rdns_batch(ips, budget=4.0):
    from concurrent.futures import ThreadPoolExecutor, wait
    def lookup(ip):
        try:
            return socket.gethostbyaddr(ip)[0]
        except Exception:
            return None
    pool = ThreadPoolExecutor(max_workers=16)
    futures = {pool.submit(lookup, ip): ip for ip in ips}
    done, _pending = wait(futures, timeout=budget)
    pool.shutdown(wait=False)
    result = {}
    for future in done:
        name = future.result()
        if name:
            result[futures[future]] = name
    return result

def _too_fast(rtt, km):
    factor = 1.25 if km < 3000 else 1.05
    return rtt < km / 100 * factor - 2

def _refine_geo(runs, raw_geo):
    """
    每一跳的位置：有 IPmap 实测结果就用实测，没有就用 IP 库（ip-api，和全球 ping 同一个库）。
    不根据反向域名、延迟去猜位置，也不改动、不丢弃任何一跳。
    反向域名只作为路由器名称显示在表格里。
    """
    ips = []
    for _source, hops in runs:
        for hop in hops:
            ip = hop.get("ip")
            if ip and ip not in ips:
                try:
                    if ipaddress.ip_address(ip).is_global:
                        ips.append(ip)
                except ValueError:
                    pass
    ipmap = _ipmap_batch(ips) if ips else {}
    names = _rdns_batch(ips) if ips else {}
    geo = {}
    for ip in ips:
        base = dict(raw_geo.get(ip) or {})
        if base.get("lat") is not None and not (base["lat"] == 0 and base["lon"] == 0):
            base["src"] = "ipdb"
        else:
            base.update(lat=None, lon=None, src=None)
        row = ipmap.get(ip)
        if row:
            city_key = re.sub(r"[^a-z]", "", (row.get("cityNameAscii") or row.get("cityName") or "").lower())
            base.update(
                src="ipmap",
                lat=float(row["latitude"]),
                lon=float(row["longitude"]),
                city=CITY_TABLE.get(city_key, (row.get("cityNameAscii") or row.get("cityName") or "",))[0],
                country=_country_zh(row.get("countryCodeAlpha2"), base),
            )
        if names.get(ip):
            base["rdns"] = names[ip]
        geo[ip] = base
    return geo

def _sanitize_geo(source, hops, geo):
    """
    只核对终点：IP 库给的终点位置如果和这次实测的路由在物理上矛盾，就按路由实际到达的地方定位。
    依据（全是实测数据，不是推测）：
      光在光纤里 1ms 往返最多走 100km 单程距离；
      包已经实测经过了某一跳，再从那一跳到终点，总路程不可能超过终点延迟允许的距离。
    例：经过洛杉矶的节点、终点延迟 142ms，IP 库却说终点在香港——从洛杉矶回香港至少还要 110ms，不可能。
    中间跳的位置不改，原样画。
    """
    slat, slon = source.get("lat"), source.get("lon")
    last = hops[-1] if hops else None
    if (
        slat is None or slon is None
        or not source.get("reached")
        or not last or not last.get("ip") or last.get("rtt") is None
    ):
        return geo
    tinfo = geo.get(last["ip"])
    if not tinfo or tinfo.get("lat") is None:
        return geo
    slat, slon = float(slat), float(slon)
    reach = last["rtt"] * 100 + 500  # 终点延迟允许的最大单程距离（含 500km 余量）

    def km_from_src(info):
        return _haversine_km(slat, slon, float(info["lat"]), float(info["lon"]))

    passed = []  # 实测经过、且自身位置和自己的延迟不矛盾的跳点
    for hop in hops[:-1]:
        info = geo.get(hop["ip"]) if hop.get("ip") else None
        if not info or info.get("lat") is None or hop.get("rtt") is None:
            continue
        if info["lat"] == 0 and info["lon"] == 0:
            continue
        d = km_from_src(info)
        if d <= hop["rtt"] * 100 + 500 and d <= reach:
            passed.append((hop, info))
    tlat, tlon = float(tinfo["lat"]), float(tinfo["lon"])
    conflict = km_from_src(tinfo) > reach or any(
        km_from_src(info)
        + _haversine_km(float(info["lat"]), float(info["lon"]), tlat, tlon) > reach
        for _hop, info in passed
    )
    if not conflict:
        return geo
    old_place = tinfo.get("city") or tinfo.get("country") or "其它地方"
    if passed:
        hop, info = passed[-1]
        new = dict(
            tinfo, src="route",
            lat=float(info["lat"]), lon=float(info["lon"]),
            city=info.get("city") or "", country=info.get("country") or "",
            route_note=f"IP库定位为{old_place}，与实测路由矛盾，按最后经过的第 {hop['n']} 跳定位",
        )
    else:
        new = dict(
            tinfo, src=None, lat=None, lon=None, suspect=True,
            route_note=f"IP库定位为{old_place}，与实测延迟矛盾",
        )
    return dict(geo, **{last["ip"]: new})

def _build_points(source, hops, geo):
    raw = []
    if source.get("lat") is not None and source.get("lon") is not None:
        raw.append({
            "first": "S", "last": "S",
            "lat": float(source["lat"]), "lon": float(source["lon"]),
            "sure": True, "start": True,
        })
    last_hop = hops[-1] if hops else None
    for hop in hops:
        info = geo.get(hop["ip"]) if hop["ip"] else None
        if not info or info.get("lat") is None:
            continue
        if info["lat"] == 0 and info["lon"] == 0:
            continue
        raw.append({
            "first": str(hop["n"]), "last": str(hop["n"]),
            "lat": float(info["lat"]), "lon": float(info["lon"]),
            "sure": True,
            "dest": bool(
                source.get("reached") and hop is last_hop
            ),
        })
    merged = []
    for point in raw:
        prev = merged[-1] if merged else None
        if prev and _haversine_km(
            prev["lat"], prev["lon"], point["lat"], point["lon"]
        ) < 30:
            if not prev.get("start"):
                prev["last"] = point["last"]
            prev["sure"] = prev["sure"] or point["sure"]
            prev["dest"] = prev.get("dest") or point.get("dest")
            continue
        merged.append(dict(point))
    previous = None
    for point in merged:
        lon = point["lon"]
        if previous is not None:
            while lon - previous > 180:
                lon -= 360
            while lon - previous < -180:
                lon += 360
        point["lon"] = lon
        previous = lon
    for point in merged:
        point["tag"] = (
            point["first"] if point["first"] == point["last"]
            else f"{point['first']}-{point['last']}"
        )
    return merged

def _world_xy(lat, lon, zoom):
    size = 256 * (2 ** zoom)
    lat = max(min(lat, 85.05), -85.05)
    x = (lon + 180.0) / 360.0 * size
    rad = math.radians(lat)
    y = (
        1.0
        - math.log(math.tan(rad) + 1.0 / math.cos(rad)) / math.pi
    ) / 2.0 * size
    return x, y

def _load_font(size):
    for name in (
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "arialbd.ttf",
        "arial.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()

def _fetch_tile(zoom, x, y):
    count = 2 ** zoom
    if y < 0 or y >= count:
        return None
    url = MAP_TILE_URL.format(
        z=zoom,
        x=x % count,
        y=y
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": MAP_USER_AGENT}
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=6
        ) as response:
            return Image.open(
                io.BytesIO(response.read())
            ).convert("RGB")
    except Exception:
        return None

def _load_cjk_font(size):
    for name in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "NotoSansCJK-Regular.ttc",
        "msyh.ttc",
    ):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return None

def _dashed_line(draw, a, b, fill, width, dash=14, gap=9):
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length == 0:
        return
    dx = (b[0] - a[0]) / length
    dy = (b[1] - a[1]) / length
    pos = 0.0
    while pos < length:
        end = min(pos + dash, length)
        draw.line(
            [(a[0] + dx * pos, a[1] + dy * pos),
             (a[0] + dx * end, a[1] + dy * end)],
            fill=fill,
            width=width
        )
        pos = end + gap

ROUTE_MAP_SCALE = 2      # 路由地图按 2 倍分辨率输出（1600×1200），线条在手机上更清楚
ROUTE_MAP_SUPERSAMPLE = 4  # 线条、文字先按 4 倍画再缩小，边缘抗锯齿，不再发毛

SATELLITE_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
SATELLITE_LABEL_URL = (
    # 透明的地名 + 国界 / 省界图层，叠在卫星图上
    "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
)
_SAT_CACHE = {}

def _fetch_sat_tile(zoom, x, y):
    """卫星图瓦片 + 地名图层合成一块（地名拿不到时只有卫星图）。带内存缓存。"""
    count = 2 ** zoom
    if y < 0 or y >= count:
        return None
    key = (zoom, x % count, y)
    if key in _SAT_CACHE:
        return _SAT_CACHE[key]
    def get(url):
        request = urllib.request.Request(url, headers={"User-Agent": MAP_USER_AGENT})
        with urllib.request.urlopen(request, timeout=8) as response:
            return Image.open(io.BytesIO(response.read()))
    try:
        tile = get(SATELLITE_TILE_URL.format(z=zoom, x=x % count, y=y)).convert("RGB")
    except Exception:
        return None
    try:
        labels = get(SATELLITE_LABEL_URL.format(z=zoom, x=x % count, y=y)).convert("RGBA")
        merged = tile.convert("RGBA")
        merged.alpha_composite(labels)
        tile = merged.convert("RGB")
    except Exception:
        pass
    if len(_SAT_CACHE) > 600:
        _SAT_CACHE.clear()
    _SAT_CACHE[key] = tile
    return tile

def _route_base_map(zoom, left, top, width, height):
    """
    路由地图底图：卫星图 + 地名 / 边界，按 2 倍分辨率拼（用高一级的瓦片，清晰、地名多）。
    卫星图拿到不到一半时，整张退回原来的 OpenStreetMap 街道图（同一级瓦片放大 2 倍）。
    返回 (图片, 版权文字)。全球 Ping 地图不走这里，仍是街道图。
    """
    from concurrent.futures import ThreadPoolExecutor
    scale = ROUTE_MAP_SCALE
    def tile_list(l, t, w, h):
        return [
            (tx, ty)
            for tx in range(int(math.floor(l / 256)), int(math.floor((l + w) / 256)) + 1)
            for ty in range(int(math.floor(t / 256)), int(math.floor((t + h) / 256)) + 1)
        ]
    # 卫星图：高一级瓦片，直接拼成 2 倍大小
    z = zoom + int(round(math.log2(scale)))
    sl, st, sw, sh = left * scale, top * scale, width * scale, height * scale
    tiles = tile_list(sl, st, sw, sh)
    with ThreadPoolExecutor(max_workers=12) as pool:
        fetched = list(pool.map(lambda t: _fetch_sat_tile(z, t[0], t[1]), tiles))
    if sum(1 for t in fetched if t is not None) * 2 >= len(tiles):
        image = Image.new("RGB", (sw, sh), (12, 20, 32))
        for (tx, ty), tile in zip(tiles, fetched):
            if tile is not None:
                image.paste(tile, (int(tx * 256 - sl), int(ty * 256 - st)))
        return image, ""
    print("卫星底图拿不到，改用街道图")
    # 退回街道图：和全球 Ping 同一种地图、同一级瓦片，拼好后放大 2 倍
    tiles = tile_list(left, top, width, height)
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(lambda t: _tile_cached(zoom, t[0], t[1]), tiles))
    image = Image.new("RGB", (width, height), (170, 211, 223))
    for (tx, ty), tile in zip(tiles, fetched):
        if tile is not None:
            image.paste(tile, (int(tx * 256 - left), int(ty * 256 - top)))
    return image.resize((width * scale, height * scale), Image.LANCZOS), ""

def _route_credit(draw, credit, width, height):
    if not credit:
        return
    small = _load_font(11)
    credit_width = draw.textlength(credit, font=small)
    draw.rectangle((width - credit_width - 14, height - 18, width, height), fill=(255, 255, 255))
    draw.text((width - 7, height - 9), credit, font=small, fill=(60, 60, 60), anchor="rm")

class _HDDraw:
    """
    代替 ImageDraw 的画笔：外面照旧按 800×600 的坐标调用，
    实际在 4 倍大小的透明图层上画，最后缩小叠到底图上，线条和文字边缘都是平滑的。
    """
    def __init__(self, size):
        self.f = ROUTE_MAP_SUPERSAMPLE
        self.size = size
        self.layer = Image.new("RGBA", (size[0] * self.f, size[1] * self.f), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.layer)
        self.measure = ImageDraw.Draw(Image.new("L", (1, 1)))
        self.fonts = {}

    def _s(self, v):
        if isinstance(v, (int, float)):
            return v * self.f
        return type(v)(self._s(i) for i in v) if isinstance(v, tuple) else [self._s(i) for i in v]

    def _w(self, width):
        return max(1, int(round((width or 1) * self.f)))

    def _font(self, font):
        key = id(font)
        if key not in self.fonts:
            big = None
            try:
                if font is not None and hasattr(font, "font_variant"):
                    big = font.font_variant(size=int(round(font.size * self.f)))
                else:
                    big = ImageFont.load_default(size=11 * self.f)
            except Exception:
                big = font
            self.fonts[key] = big
        return self.fonts[key]

    def line(self, xy, fill=None, width=1, **kw):
        pts = [tuple(p) for p in xy]
        w = self._w(width)
        self.d.line(self._s(pts), fill=fill, width=w, joint="curve")
        if w >= 3 and fill is not None:
            r = w / 2
            for x, y in (pts[0], pts[-1]):
                x, y = x * self.f, y * self.f
                self.d.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    def ellipse(self, xy, fill=None, outline=None, width=1):
        self.d.ellipse(self._s(xy), fill=fill, outline=outline, width=self._w(width))

    def rectangle(self, xy, fill=None, outline=None, width=1):
        self.d.rectangle(self._s(xy), fill=fill, outline=outline, width=self._w(width))

    def rounded_rectangle(self, xy, radius=0, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(self._s(xy), radius=radius * self.f, fill=fill,
                                 outline=outline, width=self._w(width))

    def polygon(self, xy, fill=None, outline=None, width=1):
        self.d.polygon(self._s([tuple(p) for p in xy]), fill=fill, outline=outline, width=self._w(width))

    def text(self, xy, text, font=None, fill=None, anchor=None, **kw):
        self.d.text(self._s(tuple(xy)), text, font=self._font(font), fill=fill, anchor=anchor)

    def textlength(self, text, font=None):
        return self.measure.textlength(text, font=font)

    def compose(self, base):
        """缩小图层（抗锯齿）后叠到底图上，返回 RGB 图。"""
        layer = self.layer.convert("RGBa").resize(base.size, Image.LANCZOS).convert("RGBA")
        out = base.convert("RGBA")
        out.alpha_composite(layer)
        return out.convert("RGB")

def _render_route_map(points):
    if not PIL_OK or not points:
        return None
    from concurrent.futures import ThreadPoolExecutor
    width, height, pad = 800, 600, 70
    if len(points) == 1:
        zoom = 4
    else:
        zoom = 1
        for candidate in range(6, 0, -1):
            xs, ys = zip(*[
                _world_xy(p["lat"], p["lon"], candidate)
                for p in points
            ])
            if (
                max(xs) - min(xs) <= width - 2 * pad
                and max(ys) - min(ys) <= height - 2 * pad
            ):
                zoom = candidate
                break
    coords = [
        _world_xy(p["lat"], p["lon"], zoom)
        for p in points
    ]
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    left = (min(xs) + max(xs)) / 2 - width / 2
    top = (min(ys) + max(ys)) / 2 - height / 2
    # 缩到世界地图那一级时，整个地球的高度可能比图片还矮：把图片裁成地球的高度，
    # 上下就不会出现没有地图的黑边；否则把画面限制在地图范围内，不露出地图外面
    world = 256 * 2 ** zoom
    if world < height:
        height, top = int(world), 0
    else:
        top = max(0, min(top, world - height))
    image, credit = _route_base_map(zoom, left, top, width, height)
    draw = _HDDraw((width, height))
    items = []
    for point, (wx, wy) in zip(points, coords):
        x, y = wx - left, wy - top
        if items and math.hypot(items[-1]["x"] - x, items[-1]["y"] - y) < 30:
            last = items[-1]
            if not last["start"]:
                last["last"] = point["last"]
            last["sure"] = last["sure"] or point["sure"]
            last["dest"] = last["dest"] or bool(point.get("dest"))
            continue
        items.append({
            "first": point["first"],
            "last": point["last"],
            "x": x,
            "y": y,
            "start": bool(point.get("start")),
            "dest": bool(point.get("dest")),
            "sure": bool(point.get("sure")),
        })
    for item in items:
        item["tag"] = (
            item["first"] if item["first"] == item["last"]
            else f"{item['first']}-{item['last']}"
        )
    def solid(item):
        return item["start"] or item["sure"] or item["dest"]
    for a, b in zip(items, items[1:]):
        pa, pb = (a["x"], a["y"]), (b["x"], b["y"])
        if solid(a) and solid(b):
            draw.line([pa, pb], fill=(20, 20, 20), width=8)
            draw.line([pa, pb], fill=(255, 140, 0), width=5)
        else:
            _dashed_line(draw, pa, pb, (20, 20, 20), 7)
            _dashed_line(draw, pa, pb, (255, 190, 90), 4)
    font = _load_font(15)
    groups = []
    for item in items:
        for group in groups:
            if math.hypot(group["x"] - item["x"], group["y"] - item["y"]) < 26:
                group["tags"].append(item["tag"])
                group["start"] = group["start"] or item["start"]
                group["dest"] = group["dest"] or item["dest"]
                group["sure"] = group["sure"] or item["sure"]
                break
        else:
            groups.append(dict(item, tags=[item["tag"]]))
    for item in groups:
        x, y = item["x"], item["y"]
        if item["start"]:
            color = (46, 204, 113)
        elif item["dest"]:
            color = (231, 76, 60)
        elif item["sure"]:
            color = (52, 152, 219)
        else:
            color = (140, 148, 156)
        text = "·".join(item["tags"])
        try:
            text_w = draw.textlength(text, font=font)
        except Exception:
            text_w = 8 * len(text)
        half_w = max(13, text_w / 2 + 8)
        draw.rounded_rectangle(
            (x - half_w, y - 13, x + half_w, y + 13),
            radius=13,
            fill=color,
            outline=(255, 255, 255),
            width=2
        )
        try:
            draw.text((x, y), text, font=font, fill=(255, 255, 255), anchor="mm")
        except Exception:
            draw.text((x - text_w / 2, y - 7), text, fill=(255, 255, 255))
    cjk = _load_cjk_font(15)
    if cjk is not None:
        legend = [
            ("line", "位置已确认"),
            ("dash", "IP库估计，未确认"),
        ]
        legend_font = cjk
    else:
        legend = [
            ("line", "verified"),
            ("dash", "estimated (unverified)"),
        ]
        legend_font = _load_font(13)
    box_w = 60 + max(draw.textlength(t, font=legend_font) for _k, t in legend)
    draw.rectangle(
        (10, height - 20 - 26 * len(legend), 10 + box_w, height - 10),
        fill=(255, 255, 255),
        outline=(180, 180, 180)
    )
    for i, (kind, label) in enumerate(legend):
        ly = height - 10 - 26 * (len(legend) - i) + 13
        if kind == "line":
            draw.line([(20, ly), (56, ly)], fill=(255, 140, 0), width=5)
        else:
            _dashed_line(draw, (20, ly), (56, ly), (230, 150, 40), 4, dash=9, gap=5)
        draw.text((64, ly), label, font=legend_font, fill=(40, 40, 40), anchor="lm")
    _route_credit(draw, credit, width, height)
    image = draw.compose(image)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()
CARRIER_COLORS = {
    "电信": (41, 128, 255),
    "联通": (235, 64, 52),
    "移动": (30, 190, 90),
}

def _compare_route_points(points, anchor=None):
    """
    三网地图按路由实际经过的每一跳画线，不挑点、不补点；
    从起点开始重新做经度连续化，三条线在同一坐标系里。
    """
    kept = [dict(p) for p in points]
    previous = anchor
    for point in kept:
        lon = point["lon"]
        if previous is not None:
            while lon - previous > 180:
                lon -= 360
            while lon - previous < -180:
                lon += 360
        point["lon"] = lon
        previous = lon
    return kept

def _render_compare_map(routes, target=None, reached=None):
    """
    routes：[(运营商名, 地图点列表)]，地图点来自 _build_points。
    target：目标的位置 {"lat", "lon"}（三网测的是同一个目标），可以为 None。
    reached：{运营商名: 是否到达终点}。到达了但这条线自己没定位到终点的，用实线连到目标。
    三网各一种颜色；线条稍微错开几个像素，路线重合时三条都看得见。
    没到达终点的线：用虚线从最后看得到的位置连到目标，表示后面的路由看不到。
    """
    anchor = next((pts[0]["lon"] for _name, pts in routes if pts), None)
    routes = [(name, _compare_route_points(pts, anchor)) for name, pts in routes]
    routes = [(name, pts) for name, pts in routes if pts]
    if target and target.get("lat") is not None and target.get("lon") is not None:
        for _name, pts in routes:
            if pts[-1].get("dest"):
                continue
            lon = float(target["lon"])
            while lon - pts[-1]["lon"] > 180:
                lon -= 360
            while lon - pts[-1]["lon"] < -180:
                lon += 360
            if _haversine_km(pts[-1]["lat"], pts[-1]["lon"], float(target["lat"]), lon) < 30:
                continue
            pts.append({
                "lat": float(target["lat"]), "lon": lon,
                "tag": "T", "ghost": True,
                "dest": bool((reached or {}).get(_name)),
            })
    if not PIL_OK or not routes:
        return None
    from concurrent.futures import ThreadPoolExecutor
    width, height, pad = 800, 600, 70
    all_points = [p for _name, pts in routes for p in pts]
    zoom = 4
    if len(all_points) > 1:
        zoom = 1
        for candidate in range(6, 0, -1):
            xs, ys = zip(*[_world_xy(p["lat"], p["lon"], candidate) for p in all_points])
            if max(xs) - min(xs) <= width - 2 * pad and max(ys) - min(ys) <= height - 2 * pad:
                zoom = candidate
                break
    xs, ys = zip(*[_world_xy(p["lat"], p["lon"], zoom) for p in all_points])
    left = (min(xs) + max(xs)) / 2 - width / 2
    top = (min(ys) + max(ys)) / 2 - height / 2
    # 缩到世界地图那一级时，整个地球的高度可能比图片还矮：把图片裁成地球的高度，
    # 上下就不会出现没有地图的黑边；否则把画面限制在地图范围内，不露出地图外面
    world = 256 * 2 ** zoom
    if world < height:
        height, top = int(world), 0
    else:
        top = max(0, min(top, world - height))
    image, credit = _route_base_map(zoom, left, top, width, height)
    draw = _HDDraw((width, height))
    def screen(p, shift):
        wx, wy = _world_xy(p["lat"], p["lon"], zoom)
        return wx - left + shift, wy - top + shift
    shifts = {0: -4, 1: 0, 2: 4}
    ends = []
    for index, (name, pts) in enumerate(routes):
        color = CARRIER_COLORS.get(name, (255, 140, 0))
        shift = shifts.get(index, 0)
        xy = [screen(p, shift) for p in pts]
        arrived = bool(pts[-1].get("dest"))
        segments = list(zip(xy, xy[1:], pts, pts[1:]))
        for number, (a, b, pa, pb) in enumerate(segments):
            sure = (pa.get("sure") or pa.get("start")) and (pb.get("sure") or pb.get("dest"))
            if not arrived and number == len(segments) - 1:
                sure = False
            if sure:
                draw.line([a, b], fill=(20, 20, 20), width=7)
                draw.line([a, b], fill=color, width=4)
            else:
                _dashed_line(draw, a, b, (20, 20, 20), 6)
                _dashed_line(draw, a, b, color, 3)
        for (x, y), p in zip(xy, pts):
            if not p.get("start") and not p.get("dest") and not p.get("ghost"):
                draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color, outline=(255, 255, 255))
        ends.append((xy[0], xy[-1], pts[-1].get("dest") or pts[-1].get("ghost"), color, bool(pts[0].get("start"))))
    cjk = _load_cjk_bold(15)
    font = cjk or _load_font(15)
    names_en = {"电信": "China Telecom", "联通": "China Unicom", "移动": "China Mobile"}
    def say(zh, en):
        return zh if cjk else en
    def badge(x, y, text, fill):
        try:
            w = draw.textlength(text, font=font)
        except Exception:
            w = 9 * len(text)
        half = max(14, w / 2 + 8)
        draw.rounded_rectangle((x - half, y - 14, x + half, y + 14), radius=14,
                               fill=fill, outline=(255, 255, 255), width=2)
        draw.text((x, y), text, font=font, fill=(255, 255, 255), anchor="mm")
    starts = []
    for first, _last, _dest, color, is_start in ends:
        if not is_start:
            continue
        x, y = first
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline=(255, 255, 255), width=2)
        if not any(math.hypot(x - sx, y - sy) < 40 for sx, sy in starts):
            starts.append((x, y))
    for x, y in starts:
        badge(x, y - 24, say("起点", "Start"), (60, 60, 60))
    dest = next(((e[1]) for e in ends if e[2]), None)
    if dest:
        draw.ellipse((dest[0] - 7, dest[1] - 7, dest[0] + 7, dest[1] + 7),
                     fill=(40, 40, 40), outline=(255, 255, 255), width=2)
        badge(dest[0], dest[1] + 24, say("终点", "Target"), (60, 60, 60))
    legend = [
        (say(name, names_en.get(name, name)), CARRIER_COLORS.get(name, (255, 140, 0)))
        for name, _pts in routes
    ]
    box_w = 64 + max(draw.textlength(n, font=font) for n, _c in legend)
    draw.rectangle((10, height - 20 - 26 * len(legend), 10 + box_w, height - 10),
                   fill=(255, 255, 255), outline=(180, 180, 180))
    for i, (name, color) in enumerate(legend):
        ly = height - 10 - 26 * (len(legend) - i) + 13
        draw.line([(20, ly), (56, ly)], fill=color, width=5)
        draw.text((64, ly), name, font=font, fill=(40, 40, 40), anchor="lm")
    _route_credit(draw, credit, width, height)
    image = draw.compose(image)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()

def _fmt_rtt(value):
    if value is None:
        return ""
    return f"{value:.0f}ms" if value >= 10 else f"{value:.1f}ms"
ROUTE_ASN_RULES = {
    4809: ("电信 CN2", True),
    23764: ("电信 CTGNet（国际网络）", True),
    9929: ("联通 9929（CUII）", True),
    10099: ("联通 CUG（国际网络）", False),
    58807: ("移动 CMIN2", True),
    58453: ("移动 CMI", True),
    4134: ("电信 163 骨干", False),
    4837: ("联通 4837 骨干", False),
    9808: ("移动 9808 骨干", False),
    23724: ("电信 IDC 网络", False),
}
CARRIER_ASNS = {
    4811: "电信", 4812: "电信", 4813: "电信",
    4808: "联通", 17621: "联通", 17622: "联通",
    17623: "联通", 17816: "联通",
    9394: "移动", 24400: "移动", 56040: "移动", 56041: "移动",
    56042: "移动", 56046: "移动", 56047: "移动", 56048: "移动",
    132525: "移动",
}

def _carrier_of(asn, as_text):
    if asn in CARRIER_ASNS:
        return CARRIER_ASNS[asn]
    lowered = (as_text or "").lower()
    if "china telecom" in lowered or "chinanet" in lowered:
        return "电信"
    if "china unicom" in lowered or "china169" in lowered:
        return "联通"
    if "china mobile" in lowered or "cmnet" in lowered:
        return "移动"
    return None

def _hop_asns(hops, geo):
    """按 traceroute 中实际出现的跳点顺序提取 ASN，去掉连续重复。"""
    result = []
    for hop in hops:
        ip = hop.get("ip")
        if not ip:
            continue
        info = geo.get(ip) or {}
        as_text = info.get("as") or ""
        match = re.search(r"(?:^|\s)AS(\d+)", as_text, re.I)
        if not match:
            continue
        asn = int(match.group(1))
        if not result or result[-1] != asn:
            result.append(asn)
    return result

def _route_profile(hops, geo):
    """返回标准化线路分类 + ASN 证据。"""
    asns = _hop_asns(hops, geo)
    asn_set = set(asns)
    if 4809 in asn_set:
        if 4134 in asn_set:
            route_class = "ct_cn2_gt_mixed"
            label = "电信 CN2 GT+163 混合"
        else:
            route_class = "ct_cn2_gia"
            label = "电信 CN2 GIA 路径（未见 163）"
    elif 4134 in asn_set:
        route_class = "ct_163"
        label = "电信 163"
    elif 23764 in asn_set:
        route_class = "ct_ctgnet"
        label = "电信 CTGNet 国际网络"
    else:
        route_class = None
        label = None
    if 9929 in asn_set:
        if 4837 in asn_set:
            u_class = "cu_9929_mixed"
            u_label = "联通 9929+4837 混合"
        else:
            u_class = "cu_9929"
            u_label = "联通 9929"
    elif 4837 in asn_set:
        u_class = "cu_4837"
        u_label = "联通 4837"
    elif 10099 in asn_set:
        u_class = "cu_10099"
        u_label = "联通 CUG 国际网络"
    else:
        u_class = None
        u_label = None
    if 58807 in asn_set:
        if 9808 in asn_set:
            m_class = "cm_cmin2_mixed"
            m_label = "移动 CMIN2+9808 混合"
        else:
            m_class = "cm_cmin2"
            m_label = "移动 CMIN2"
    elif 58453 in asn_set:
        if 9808 in asn_set:
            m_class = "cm_cmi_mixed"
            m_label = "移动 CMI+9808 混合"
        else:
            m_class = "cm_cmi"
            m_label = "移动 CMI"
    elif 9808 in asn_set:
        m_class = "cm_9808"
        m_label = "移动 9808"
    else:
        m_class = None
        m_label = None
    families = [(route_class, label), (u_class, u_label), (m_class, m_label)]
    families = [(c, l) for c, l in families if c]
    if len(families) > 1:
        route_class = "multi_carrier_mixed"
        label = "多运营商线路混合"
    elif families:
        route_class, label = families[0]
    else:
        route_class = "unknown"
        label = None
    return {
        "class": route_class,
        "label": label,
        "asns": asns,
        "asn_set": asn_set,
    }

def _classify_route(hops, geo, source=None):
    profile = _route_profile(hops, geo)
    asns = profile["asns"]
    label = profile["label"]
    evidence = []
    for asn in asns:
        if asn in ROUTE_ASN_RULES:
            name, _ = ROUTE_ASN_RULES[asn]
            evidence.append(f"{name} AS{asn}")
    if label:
        text = label
        if profile["class"] == "ct_cn2_gia":
            text += "（按可见路径判定）"
        ordinary_classes = {
            "ct_163",
            "cu_4837",
            "cm_9808",
        }
        if profile["class"] in ordinary_classes:
            prefix = "🟡 普通"
        else:
            prefix = "🟢 优化"
        return html.escape(prefix + " · " + text)
    carriers = []
    for hop in hops:
        info = geo.get(hop["ip"]) if hop.get("ip") else None
        if not info:
            continue
        as_text = info.get("as") or ""
        match = re.search(r"(?:^|\s)AS(\d+)", as_text, re.I)
        if not match:
            continue
        asn = int(match.group(1))
        carrier = _carrier_of(asn, as_text)
        item = f"{carrier}网络 AS{asn}" if carrier else None
        if item and item not in carriers:
            carriers.append(item)
    if carriers:
        return html.escape("🟡 普通 · " + "、".join(carriers))
    country = ((source or {}).get("country") or "").upper()
    if country == "CN":
        return html.escape(
            "⚪ 未识别到已知三网骨干（多跳无回应或 ASN 数据不足）"
        )
    return html.escape("⚪ 未识别到国内三网骨干（起点可能在境外）")

def _place_name(info):
    if not info or info.get("lat") is None:
        return ""
    name = info.get("city") or info.get("country") or ""
    if name.endswith("市") and len(name) > 2:
        name = name[:-1]
    return name
GEO_SOURCE_TEXT = {
    "ipmap": "RIPE IPmap 实测",
    "rdns": "路由器名称",
    "ping": "多地 ping 实测",
    "route": "按实测路由定位",
    "ipdb": "IP库估计，未确认",
}

def _hop_place(hop, geo):
    """表格里的位置列：✓已确认 / ?IP库估计 / ×存疑 / 内网 / 未知。"""
    try:
        private = not ipaddress.ip_address(hop["ip"]).is_global
    except ValueError:
        private = False
    if private:
        return "内网"
    info = geo.get(hop["ip"]) or {}
    if info.get("suspect"):
        return "×存疑"
    name = _place_name(info)
    if not name:
        return "未知"
    if info.get("src") in TRUSTED_GEO:
        return "✓" + name
    return "?" + name

def _hop_as(hop, geo):
    info = geo.get(hop["ip"]) or {}
    as_text = (info.get("as") or "").strip()
    match = re.match(r"AS(\d+)\s*(.*)", as_text, re.I)
    if not match:
        return None, ""
    return int(match.group(1)), match.group(2)

def _hop_table(hops, geo):
    """返回 [(是否分隔行, 纯文本行)]；AS 变化处插入一行分隔，显示 AS 路径。"""
    rows = []
    index = 0
    current_asn = None
    while index < len(hops):
        hop = hops[index]
        if not hop["ip"]:
            end = index
            while end + 1 < len(hops) and not hops[end + 1]["ip"]:
                end += 1
            number = (
                f"{hop['n']}" if end == index
                else f"{hop['n']}-{hops[end]['n']}"
            )
            rows.append(("hop", number, "*", "", "超时"))
            index = end + 1
            continue
        asn, name = _hop_as(hop, geo)
        if asn is not None and asn != current_asn:
            carrier = ROUTE_ASN_RULES.get(asn, (None,))[0]
            label = carrier or name[:20]
            rows.append(("as", f"AS{asn} {label}".strip()))
            current_asn = asn
        rows.append((
            "hop",
            str(hop["n"]),
            hop["ip"],
            _fmt_rtt(hop["rtt"]),
            _hop_place(hop, geo),
        ))
        index += 1
    hop_rows = [r for r in rows if r[0] == "hop"]
    n_w = max((len(r[1]) for r in hop_rows), default=2)
    ip_w = max((len(r[2]) for r in hop_rows), default=15)
    rtt_w = max((len(r[3]) for r in hop_rows), default=5)
    lines = []
    for row in rows:
        if row[0] == "as":
            lines.append((True, f"── {row[1]}"))
            continue
        _k, n, ip, rtt, place = row
        lines.append((
            False,
            f"{n:>{n_w}} {ip:<{ip_w}} {rtt:>{rtt_w}} {place}"
        ))
    return lines

def _route_summary(source, hops, geo):
    """绕路判断 + 出境段。绕路按实测延迟与直线理想延迟的差距判断。"""
    result = {"detour": None, "exit": None}
    responding = [
        h for h in hops
        if h.get("ip") and h.get("rtt") is not None
    ]
    last = hops[-1] if hops else None
    target_info = geo.get(last["ip"]) if last and last.get("ip") else None
    if (
        source.get("reached")
        and last and last.get("rtt") is not None
        and source.get("lat") is not None
    ):
        if target_info and target_info.get("lat") is not None:
            km = _haversine_km(
                float(source["lat"]), float(source["lon"]),
                float(target_info["lat"]), float(target_info["lon"])
            )
            ideal = km / 100 * 1.25 + 10
            rtt = last["rtt"]
            # 绕路看实测延迟比直线理想值多出多少。
            # 路由器的 IP 库位置经常是运营商注册地，不能拿来算路程，否则会把正常线路判成绕路
            if rtt <= ideal * 1.3 + 15:
                judge = "✅ 未见明显绕路"
            elif rtt <= ideal * 1.8 + 30:
                judge = "🟡 可能略有绕路"
            else:
                judge = "🔴 明显绕路"
            result["detour"] = (
                judge,
                f"实测 {rtt:.0f}ms　理想 {ideal:.0f}ms · 直线 {km:,.0f} km"
            )
        else:
            result["detour"] = ("⚪ 无法判断", "目标位置存疑")
    floors = []
    lowest = None
    for hop in reversed(responding):
        lowest = hop["rtt"] if lowest is None else min(lowest, hop["rtt"])
        floors.append((hop, lowest))
    floors.reverse()
    best = None
    previous = (None, 0.0)
    for hop, floor in floors:
        jump = floor - previous[1]
        if best is None or jump > best[2]:
            best = (previous[0], hop, jump)
        previous = (hop, floor)
    if best and best[2] >= 40:
        a, b, jump = best
        where = "起点"
        if a is not None:
            info = geo.get(a["ip"]) or {}
            name = _place_name(info)
            if info.get("suspect"):
                where = "位置存疑"
            elif name:
                prefix = "✓" if info.get("src") in TRUSTED_GEO else "?"
                where = f"{prefix}{name}（{GEO_SOURCE_TEXT.get(info.get('src'), '')}）"
            else:
                where = "未知"
            note = info.get("ping_note")
            if note:
                where += f"\n　　　 {note}"
        result["exit"] = (
            a,
            b,
            jump,
            where,
        )
    return result

def _visible_len(text):
    """
    Telegram 实际计算的长度：去掉 HTML 标签、按 UTF-16 计数（emoji 算 2），
    并且按加宽分割线之后的文字算（发送前会把分割线加宽）。
    """
    plain = html.unescape(re.sub(r"<[^>]+>", "", _wide(text)))
    return len(plain.encode("utf-16-le")) // 2

def _carrier_block(name, verdict, detour):
    """
    单个运营商的结论块（三网测试和单线追踪共用）：
        📡 联通　同一网络多探针线路不一致
        🟡 普通 · 联通 4837
        🟢 优化 · 联通 9929
        ✅ 未见明显绕路　实测 152ms　理想 140ms
    返回转义好的 HTML 行列表。
    """
    verdict_lines = [v.strip() for v in (verdict or "").split("\n") if v.strip()]
    header = f"{CARRIER_DOTS.get(name, '📡')} <b>{html.escape(name)}</b>　"
    if verdict_lines and verdict_lines[0].startswith("⚠️"):
        header += html.escape(verdict_lines[0].replace("⚠️", "").strip())
        verdict_lines = verdict_lines[1:]
    lines = [header]
    lines += [html.escape(re.sub(r"^\d+/\d+ 个探针：", "", v)) for v in verdict_lines]
    if detour and not detour[0].startswith("⚪"):
        lines.append(f"{html.escape(detour[0])}　{html.escape(detour[1].split(' · 直线')[0])}")
    return lines

def _build_route_text(
    target,
    carrier,
    hops,
    geo,
    limit,
    verdict="",
    source=None
):
    e = html.escape
    head = [
        f"🧭 <b>路由追踪</b>　<code>{e(target)}</code>",
        "━━━━━━━━━━━━━━",
    ]
    detour = _route_summary(source, hops, geo)["detour"] if source is not None else None
    head += _carrier_block(carrier, verdict, detour)
    if source is not None and not source.get("reached"):
        head.append("⚠️ 未到达目标（目标屏蔽探测或超出最大跳数）")
    head.append("")
    head.append(f"🔀 共 {len(hops)} 跳")
    table = _hop_table(hops, geo)
    foot = "✓实测定位 ?IP库定位　地图按实际经过的跳点画线"
    def compose(lines):
        body = "\n".join(e(text) for _sep, text in lines)
        return (
            "\n".join(head)
            + "\n<pre>" + body + "</pre>\n"
            + e(foot)
        )
    text = compose(table)
    if _visible_len(text) <= limit:
        return text
    for keep_tail in (4, 3, 2):
        tail = table[-keep_tail:]
        for keep in range(len(table) - keep_tail - 1, 0, -1):
            text = compose(table[:keep] + [(False, "  ……")] + tail)
            if _visible_len(text) <= limit:
                return text
    return compose(table[-3:])
VERIFY_EXIT = True
EXIT_CITIES = ["Shanghai", "Guangzhou", "Beijing", "Shenzhen"]
CARRIER_EN = {"电信": "China Telecom", "联通": "China Unicom", "移动": "China Mobile"}

def _gp_ping(target, locations):
    headers = {}
    if GLOBALPING_TOKEN:
        headers["Authorization"] = "Bearer " + GLOBALPING_TOKEN
    created = _http_json(
        GLOBALPING_API,
        {
            "type": "ping",
            "target": target,
            "locations": locations,
            "measurementOptions": {"packets": 3},
        },
        headers
    )
    measurement_id = created.get("id")
    if not measurement_id:
        return []
    deadline = time.time() + 20
    result = None
    while time.time() < deadline:
        time.sleep(1.2)
        result = _http_json(GLOBALPING_API + "/" + measurement_id, headers=headers)
        if result.get("status") != "in-progress":
            break
    samples = []
    for item in (result or {}).get("results") or []:
        probe = item.get("probe") or {}
        stats = (item.get("result") or {}).get("stats") or {}
        if probe.get("latitude") is None or stats.get("min") is None:
            continue
        samples.append({
            "city": probe.get("city") or "",
            "lat": float(probe["latitude"]),
            "lon": float(probe["longitude"]),
            "rtt": float(stats["min"]),
        })
    return samples

def _verify_exit(source, hops, geo):
    """定位出境前最后一跳；结果直接写回 geo（返回新的 dict）。"""
    if not VERIFY_EXIT or (source.get("country") or "").upper() != "CN":
        return geo
    summary = _route_summary(source, hops, geo)
    if not summary["exit"] or summary["exit"][0] is None:
        return geo
    hop = summary["exit"][0]
    ip = hop["ip"]
    info = dict(geo.get(ip) or {})
    if info.get("src") in TRUSTED_GEO:
        return geo
    asn, _name = _hop_as(hop, geo)
    carrier = _carrier_of(asn, info.get("as")) if asn else None
    suffix = f"+{CARRIER_EN[carrier]}" if carrier in CARRIER_EN else ""
    carrier_asns = {
        "电信": [4134, 4812], "联通": [4837, 17621], "移动": [9808, 24400],
    }.get(carrier, [])
    attempts = [
        [{"magic": city + suffix, "limit": 2} for city in EXIT_CITIES],
        [
            {"city": city, "asn": a, "limit": 1}
            for city in EXIT_CITIES for a in carrier_asns
        ],
    ]
    samples = []
    for locations in attempts:
        if not locations:
            continue
        try:
            samples = _gp_ping(ip, locations)
        except Exception as e:
            print("出口定位失败，尝试下一组探针：", e)
            continue
        if samples:
            break
    if not samples:
        info["ping_note"] = "多地 ping 无回应，未能验证"
        return dict(geo, **{ip: info})
    def zh(city):
        key = re.sub(r"[^a-z]", "", city.lower())
        return CITY_TABLE.get(key, (city,))[0]
    best = min(samples, key=lambda s: s["rtt"])
    measured = "、".join(
        f"{zh(s['city'])} {s['rtt']:.0f}ms"
        for s in sorted(samples, key=lambda s: s["rtt"])[:4]
    )
    if best["rtt"] <= 4:
        info.update(
            src="ping",
            lat=best["lat"],
            lon=best["lon"],
            city=zh(best["city"]),
            country="中国",
            suspect=False,
        )
        info["ping_note"] = f"ping 实测：{measured}"
        return dict(geo, **{ip: info})
    # 实测没落在任何一个城市附近：保留 IP 库位置，只在备注里写出实测数据
    info["ping_note"] = f"ping 实测：{measured}，均不在附近"
    return dict(geo, **{ip: info})

def route_keyboard():
    buttons = [
        InlineKeyboardButton(
            label,
            callback_data=f"rt|{code}"
        )
        for label, code, _location in TRACE_SOURCES
    ]
    keyboard = _rows(buttons, 3)
    keyboard.append([InlineKeyboardButton("📶 三网测试", callback_data="rtx|cmp")])
    keyboard.append([InlineKeyboardButton("🏠 返回首页", callback_data="home")])
    return InlineKeyboardMarkup(keyboard)

async def _panel_show(
    bot,
    chat_id,
    text,
    reply_markup=None,
    photo=None
):
    text = _wide(text)
    old_id = PANELS.get(chat_id)
    if photo is not None:
        try:
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
            PANELS[chat_id] = sent.message_id
            if old_id:
                try:
                    await bot.delete_message(
                        chat_id=chat_id,
                        message_id=old_id
                    )
                except Exception:
                    pass
            return
        except Exception as e:
            print("发送地图失败，改用文字：", e)
    if old_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=old_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
            return
        except Exception as e:
            if "not modified" in str(e).lower():
                return
    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    PANELS[chat_id] = sent.message_id
    if old_id:
        try:
            await bot.delete_message(
                chat_id=chat_id,
                message_id=old_id
            )
        except Exception:
            pass

async def route_trace(
    query,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["state"] = "route_trace"
    await edit_page(query,
        "🧭 <b>全球路由追踪</b>\n"
        "━━━━━━━━━━━━━━\n"
        "⌨️ 请发送要追踪的IP或域名",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            recent_entry_rows(query.from_user.id, "rt") + [[_btn("🏠 返回首页", "home")]]
        )
    )

async def handle_route_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    target = _parse_ping_target(
        update.message.text
    )
    if target is None:
        await reply_panel(
            update, context,
            "❌ 地址格式不正确，请重新发送 IP 地址或域名。"
        )
        return
    host = target[0]
    context.user_data["route_target"] = host
    recent_target_add(update.effective_user.id, host)
    await reply_panel(
        update, context,
        route_select_text(host),
        parse_mode=ParseMode.HTML,
        reply_markup=route_keyboard()
    )

def route_select_text(host):
    return (
        "🧭 <b>全球路由追踪</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"🎯 目标：<code>{html.escape(host)}</code>\n\n"
        "🔽 请选择检测起点\n"
        "🔵 电信　🔴 联通　🟢 移动\n"
        "📶 三网测试：三网同时追踪并汇总\n"
        "🔁 也可以继续发送其他 IP 更换目标"
    )

def _known_runs(runs):
    known = [
        r for r in runs
        if r.get("route_profile", {}).get("class") not in (None, "unknown")
    ]
    return known or runs

def _pick_run(runs):
    pool = _known_runs(runs)
    counts = {}
    for run in pool:
        route_class = run.get("route_profile", {}).get("class") or "unknown"
        counts[route_class] = counts.get(route_class, 0) + 1
    top = max(counts.values())
    candidates = [
        r for r in pool
        if counts.get(r.get("route_profile", {}).get("class") or "unknown", 0) == top
    ]
    return max(
        candidates,
        key=lambda r: (
            1 if r["source"].get("reached") else 0,
            _responding(r["hops"])
        )
    )

def _verdict_summary(runs):
    if len(runs) == 1:
        return runs[0]["verdict"]
    total = len(runs)
    pool = _known_runs(runs)
    counts = {}
    for run in pool:
        route_class = run.get("route_profile", {}).get("class") or "unknown"
        counts[route_class] = counts.get(route_class, 0) + 1
    if len(counts) == 1:
        verdict = pool[0]["verdict"]
        count = next(iter(counts.values()))
        return verdict  # 不再显示「（N 个探针一致）」「（x/N 个探针有效）」
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    class_to_verdict = {}
    for run in pool:
        cls = run.get("route_profile", {}).get("class") or "unknown"
        class_to_verdict.setdefault(cls, run["verdict"])
    lines = [
        f"　{count}/{total} 个探针：{class_to_verdict[cls]}"
        for cls, count in ordered
    ]
    return "⚠️ 同一网络多探针线路不一致\n" + "\n".join(lines)
ROUTE_STAGES = [
    "节点正在追踪路由",
    "查询 IP 位置",
    "生成路由地图",
]

def _route_percent(stage, elapsed):
    if stage == 0:
        return 70 * (1 - math.exp(-elapsed / 12))
    if stage == 1:
        return 72 + 12 * (1 - math.exp(-elapsed / 3))
    return 86 + 12 * (1 - math.exp(-elapsed / 4))

def _route_progress_text(target, source_name, progress):
    now = time.time()
    stage = progress["stage"]
    percent = int(
        _route_percent(stage, now - progress["stage_start"])
    )
    bar, _ = _scan_bar(percent, 100)
    return (
        "🧭 <b>正在追踪路由……</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"🎯 目标：{html.escape(target)}\n"
        f"🚩 起点：{html.escape(source_name)}\n\n"
        f"{bar} {percent}%\n"
        f"🔹 {ROUTE_STAGES[stage]}…　⏱ {now - progress['started']:.0f}s"
    )

async def _route_progress_ticker(
    bot,
    chat_id,
    target,
    source_name,
    progress
):
    while True:
        await asyncio.sleep(1.2)
        await _edit_scan_panel(
            bot,
            chat_id,
            _route_progress_text(target, source_name, progress),
            None
        )

async def route_run(
    query,
    context: ContextTypes.DEFAULT_TYPE
):
    code = query.data.split("|", 1)[1]
    bot = query.message.get_bot()
    chat_id = query.message.chat_id
    PANELS[chat_id] = query.message.message_id
    target = context.user_data.get("route_target")
    if not target:
        await _panel_show(
            bot,
            chat_id,
            "❌ 操作已经失效，请重新发送 IP 地址。",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🧭 重新开始",
                        callback_data="route_trace"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 返回首页",
                        callback_data="home"
                    )
                ]
            ])
        )
        return
    context.user_data["state"] = "route_trace"
    limited = rate_check(query.from_user.id, 1)
    if limited:
        await _panel_show(bot, chat_id, "⏳ " + html.escape(limited), route_keyboard())
        return
    source_name = None
    location = None
    for label, source_code, source_location in TRACE_SOURCES:
        if source_code == code:
            source_name = label
            location = source_location
    if location is None:
        await _panel_show(
            bot,
            chat_id,
            "❌ 这个检测起点已经不存在，请重新选择。",
            route_keyboard()
        )
        return
    progress = {
        "stage": 0,
        "started": time.time(),
        "stage_start": time.time(),
    }
    await _panel_show(
        bot,
        chat_id,
        _route_progress_text(target, source_name, progress)
    )
    ticker = asyncio.create_task(
        _route_progress_ticker(
            bot,
            chat_id,
            target,
            source_name,
            progress
        )
    )
    async def stop_ticker():
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
    try:
        runs = await asyncio.to_thread(
            _trace_with_fallback,
            target,
            location
        )
        progress["stage"] = 1
        progress["stage_start"] = time.time()
        raw_geo = await asyncio.to_thread(
            _geo_batch,
            [h["ip"] for _s, hops in runs for h in hops if h["ip"]]
        )
        try:
            raw_geo = await asyncio.to_thread(_refine_geo, runs, raw_geo)
        except Exception as e:
            print("精确定位失败，使用 IP 库位置：", e)
        analysed = []
        for run_source, run_hops in runs:
            run_geo = _sanitize_geo(run_source, run_hops, raw_geo)
            profile = _route_profile(run_hops, run_geo)
            analysed.append({
                "source": run_source,
                "hops": run_hops,
                "geo": run_geo,
                "route_profile": profile,
                "verdict": _classify_route(run_hops, run_geo, run_source),
            })
        best = _pick_run(analysed)
        source = best["source"]
        hops = best["hops"]
        geo = best["geo"]
        verdict = _verdict_summary(analysed)
        try:
            geo = await asyncio.to_thread(_verify_exit, source, hops, geo)
        except Exception as e:
            print("出口定位失败：", e)
        points = _build_points(source, hops, geo)
        image = None
        progress["stage"] = 2
        progress["stage_start"] = time.time()
        if points:
            try:
                image = await asyncio.to_thread(
                    _render_route_map,
                    points
                )
            except Exception as e:
                print("生成路由地图失败：", e)
    except Exception as e:
        await stop_ticker()
        print("路由追踪失败：", e)
        await _panel_show(
            bot,
            chat_id,
            "❌ <b>路由追踪失败</b>\n\n"
            f"{html.escape(str(e) or '未知错误')}\n\n"
            "可以换一个起点再试。",
            route_keyboard()
        )
        return
    await stop_ticker()
    carrier = source_name.split(" ", 1)[-1]
    if image is not None:
        await _panel_show(
            bot,
            chat_id,
            _build_route_text(target, carrier, hops, geo, 1000, verdict, source),
            route_keyboard(),
            photo=image
        )
    else:
        await _panel_show(
            bot,
            chat_id,
            _build_route_text(target, carrier, hops, geo, 4000, verdict, source),
            route_keyboard()
        )
BOT_STARTED = time.time()
DISPLAY_TZ = timezone(timedelta(hours=8))

def _fmt_time(ts, fmt="%m-%d %H:%M"):
    return datetime.fromtimestamp(ts, DISPLAY_TZ).strftime(fmt)

def _dw(text):
    """等宽字体下的显示宽度：中文算 2 格。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)

def _ljust(text, width):
    return text + " " * max(0, width - _dw(text))

def _rjust(text, width):
    return " " * max(0, width - _dw(text)) + text

def _btn(text, data=None, url=None):
    if url:
        return InlineKeyboardButton(text, url=url)
    return InlineKeyboardButton(text, callback_data=data)

def _home_markup():
    return InlineKeyboardMarkup([[_btn("🏠 返回首页", "home")]])

async def panel_photo(bot, chat_id, photo, caption, markup):
    """
    用图片消息当面板：当前面板是图片就直接换图 + 换文字（不发新消息）；
    是文字消息的话（Telegram 不能把文字消息改成图片）才发一条新的并删掉旧的。
    """
    caption = _wide(caption)
    old_id = PANELS.get(chat_id)
    if old_id:
        try:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=old_id,
                media=InputMediaPhoto(photo, caption=caption, parse_mode=ParseMode.HTML),
                reply_markup=markup
            )
            return
        except Exception as e:
            if "not modified" in str(e).lower():
                return
    sent = await bot.send_photo(
        chat_id=chat_id,
        photo=photo,
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=markup
    )
    PANELS[chat_id] = sent.message_id
    if old_id and old_id != sent.message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_id)
        except Exception:
            pass

async def panel_caption(bot, chat_id, caption, markup):
    """只改图片面板下方的文字。面板不是图片时返回 False。"""
    caption = _wide(caption)
    panel_id = PANELS.get(chat_id)
    if not panel_id:
        return False
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=panel_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=markup
        )
    except Exception as e:
        if "not modified" in str(e).lower():
            return True
        return False
    return True

async def panel_notice(bot, chat_id, text, markup):
    """面板是图片就改图片下方文字，否则按文字面板显示（尽量不发新消息）。"""
    if not await panel_caption(bot, chat_id, text, markup):
        await _panel_show(bot, chat_id, text, markup)

PING_BLINK_RATE = 0.35        # 每次刷新大约有多少比例的节点小方块跳成 ░■（0~1）
PING_BLINK = ("■░", "░■")  # 测试中：延迟位置显示两格小进度条 ■░ ⇄ ░■ 来回跳

class Progress:
    """
    多个子任务的进度面板，每 1.5 秒刷新一次。
    百分比 = 已完成的子任务 + 进行中的子任务按用时估算（最多算到 90%）。
    """
    ICONS = {"wait": "▪", "run": "▪", "done": "✅", "fail": "❌"}  # 等待 / 测试中用 ▪，和历史 IP 按钮统一
    def __init__(self, bot, chat_id, title, target, items, expected=30, photo=None, icon="⏳"):
        self.icon = icon
        self.photo = photo
        self.bot = bot
        self.chat_id = chat_id
        self.title = title
        self.target = target
        self.expected = expected
        self.started = time.time()
        self.task = None
        self.items = [
            {"name": n, "state": "wait", "text": "等待", "since": None}
            for n in items
        ]
    def set(self, index, state, text=None):
        item = self.items[index]
        if state == "run" and item["state"] != "run":
            item["since"] = time.time()
        item["state"] = state
        item["text"] = text or {"run": "进行中", "done": "完成", "fail": "失败"}.get(state, "等待")
    def percent(self):
        now = time.time()
        total = 0.0
        for item in self.items:
            if item["state"] in ("done", "fail"):
                total += 1
            elif item["state"] == "run":
                elapsed = now - (item["since"] or now)
                total += 0.9 * (1 - math.exp(-elapsed / self.expected))
        return int(100 * total / len(self.items))
    def text(self):
        percent = min(99, self.percent())
        bar, _ = _scan_bar(percent, 100)
        lines = [f"{self.icon} <b>{html.escape(self.title)}</b>", "━━━━━━━━━━━━━━"]
        if self.target:
            lines.append(f"🎯 目标：<code>{html.escape(self.target)}</code>")
        lines.append(f"{bar} {percent}%　⏱ {time.time() - self.started:.0f}s")  # 已用时间放在进度条右边
        cells = [
            f"{self.ICONS[item['state']]} {html.escape(item['name'])}　{html.escape(item['text'])}"
            for item in self.items
        ]
        if len(cells) > 8:
            # 节点多（全球 Ping）时两个一行，边测边出结果（和结果页同一种排版）：
            #   测试中 ⚪ 北京 ...   成功 🟢 北京 47ms   失败 🔴 北京 超时   无探针 🟡 北京 无探针
            # 左边每格补成同样多的“字母宽 + 汉字宽”字符，第二列一定对齐。
            short = {"请求失败": "失败", "请求超时": "超时", "额度用完": "无额度",
                     "探针失效": "失效", "参数错误": "错误"}
            cells = []
            for n, item in enumerate(self.items):
                name = item["name"].split(" ", 1)[-1]
                if item["state"] == "done" and re.fullmatch(r"\d+(\.\d+)?m?s", item["text"] or ""):
                    dot, value = "🟢", item["text"]
                elif item["state"] in ("done", "fail"):
                    value = short.get(item["text"], item["text"])
                    dot = "🟡" if value == "无探针" else "🔴"
                else:
                    # 测试中的图标每次刷新（约 1.5 秒）按 PING_BLINK 轮流变化，相邻节点错开，
                    # 看起来是一排在动的图标；颜色避开结果用的绿 / 红 / 黄，不会混淆
                    tick = int((time.time() - self.started) / 1.5)
                    # 刚开始全部是白点；之后每次刷新随机挑一部分节点闪成蓝色，
                    # 看起来像在随机缓冲。同一次刷新里结果固定（用刷新序号 + 节点序号做随机种子）。
                    import random as _random
                    lit = tick > 0 and _random.Random(f"{tick}-{n}").random() < PING_BLINK_RATE
                    dot = "⚪"  # 圆点固定白色，只有后面的小方块随机跳动
                    value = PING_BLINK[1] if lit else PING_BLINK[0]
                cjk = sum(1 for ch in value if ord(ch) > 0x2E80)
                text = f"{name} {value}"
                if n % 2 == 0:  # 左边一列补齐
                    text += " " * max(0, 6 - (len(value) - cjk)) + "\u3000" * max(0, 3 - cjk)
                cells.append(f"{dot} <code>{html.escape(text)}</code>")
            for i in range(0, len(cells), 2):
                lines.append("".join(cells[i:i + 2]))
        else:
            lines += cells
        return "\n".join(lines)
    async def start(self):
        if self.photo is not None:
            await panel_photo(self.bot, self.chat_id, self.photo, self.text(), None)
        else:
            await _panel_show(self.bot, self.chat_id, self.text())
        async def tick():
            while True:
                await asyncio.sleep(1.5)
                if self.photo is not None:
                    await panel_caption(self.bot, self.chat_id, self.text(), None)
                else:
                    await _edit_scan_panel(self.bot, self.chat_id, self.text(), None)
        self.task = asyncio.create_task(tick())
        return self
    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
TOOL_COOLDOWN = 15
TOOL_HOURLY = 30
_RATE = {}
TOOL_CALLBACKS = ("global_ping", "route_trace", "port_scan", "ipq")
TOOL_PREFIXES = ("rt|", "rtx|", "gp|", "iq|", "rec|")
TOOL_STATES = {"port_scan", "route_trace", "global_ping", "gping_port", "ipq", "note_add", "note_cat_new", "note_cat_rename"}

def _allowed_users():
    users = data.get("allowed_users")
    if not isinstance(users, dict):
        users = data["allowed_users"] = {}
    return users

def can_use_tools(user_id):
    return is_admin(user_id) or str(user_id) in _allowed_users()

def is_tool_callback(value):
    return value in TOOL_CALLBACKS or value.startswith(TOOL_PREFIXES)

def rate_check(user_id, cost=1):
    """通过返回 None，否则返回提示文字。管理员不受限制。"""
    if is_admin(user_id):
        return None
    now = time.time()
    stamps = [t for t in _RATE.get(user_id, []) if now - t < 3600]
    if stamps and now - stamps[-1] < TOOL_COOLDOWN:
        wait = int(TOOL_COOLDOWN - (now - stamps[-1])) + 1
        return f"操作太频繁，请 {wait} 秒后再试。"
    if len(stamps) + cost > TOOL_HOURLY:
        return "本小时的检测次数已用完，请稍后再试。"
    stamps.extend([now] * cost)
    _RATE[user_id] = stamps
    return None

def no_permission_text(user_id):
    return (
        "🔒 <b>此功能仅限授权用户使用</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"你的 ID：<code>{user_id}</code>\n"
        "把这个 ID 发给管理员即可申请授权。"
    )
_DENY_NOTICE = {}

def _category_name_error(name):
    """
    分类名会放进按钮数据（Telegram 限制 64 字节），太长或带“|”会让整个首页按钮报错。
    合法返回 None，否则返回提示文字。
    """
    if "|" in name:
        return "❌ 分类名称不能包含“|”，请重新输入。"
    if len(name.encode("utf-8")) > 45:
        return "❌ 分类名称太长（最多约 15 个汉字或 45 个英文字母），请重新输入。"
    return None

async def access_gate(update, context):
    """所有消息 / 按钮最先经过这里；未授权直接拦下，后面的处理器都不会执行。"""
    user = update.effective_user
    if user is None:
        raise ApplicationHandlerStop
    try:
        allowed = can_use_tools(user.id)
    except Exception as e:
        print("权限检查出错，按未授权处理：", e)
        allowed = False
    if allowed:
        # 用户有任何操作（点按钮、发消息、命令）都先停掉首页汇率刷新；
        # 如果操作结果还是首页，show_home 会重新开始刷新
        if update.effective_chat:
            stop_home_live(update.effective_chat.id)
        return
    try:
        if update.callback_query:
            await update.callback_query.answer("🔒 你没有使用权限", show_alert=True)
        elif update.inline_query:
            await update.inline_query.answer([], cache_time=60, is_personal=True)
        elif update.effective_chat and update.effective_chat.type == "private" and update.effective_message:
            now = time.time()
            if now - _DENY_NOTICE.get(user.id, 0) > 30:
                _DENY_NOTICE[user.id] = now
                await update.effective_message.reply_text(
                    "🔒 <b>这是私人机器人，未授权无法使用</b>\n"
                    "━━━━━━━━━━━━━━\n"
                    f"你的 ID：<code>{user.id}</code>\n"
                    "如需使用，请把这个 ID 发给管理员申请授权。",
                    parse_mode=ParseMode.HTML
                )
    except Exception as e:
        print("拦截提示发送失败：", e)
    raise ApplicationHandlerStop

async def myid_command(update, context):
    user = update.effective_user
    await update.message.reply_text(
        f"🆔 你的 Telegram ID：<code>{user.id}</code>",
        parse_mode=ParseMode.HTML
    )

def _users_page():
    users = _allowed_users()
    lines = [
        "👥 <b>授权用户</b>",
        "━━━━━━━━━━━━━━",
        "🔒 授权用户和所有者权限相同，所有功能都能使用（含管理后台）；",
        "陌生人搜到机器人也无法查看任何内容。",
        "",
    ]
    if users:
        for uid, note in users.items():
            lines.append(f"• <code>{html.escape(uid)}</code> {html.escape(note or '')}")
    else:
        lines.append("📭 还没有授权用户（管理员不需要授权）")
    keyboard = [
        [_btn(f"❌ 移除 {uid} {note or ''}".strip(), f"users|del|{uid}")]
        for uid, note in users.items()
    ]
    keyboard.append([_btn("➕ 添加用户", "admin|adduser")])
    keyboard.append([_btn("⚙️ 管理后台", "admin|back")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)

async def handle_allow_user(update, context):
    parts = update.message.text.strip().split(None, 1)
    if not parts or not parts[0].isdigit():
        await reply_panel(
            update, context,
            "❌ 格式不对。请发送：<code>用户ID 备注</code>，例如 <code>123456789 小王</code>",
            parse_mode=ParseMode.HTML
        )
        return
    _allowed_users()[parts[0]] = parts[1][:20] if len(parts) > 1 else ""
    save_data(data)
    context.user_data.clear()
    text, markup = _users_page()
    await reply_panel(
        update, context,
        "✅ 已授权\n\n" + text,
        parse_mode=ParseMode.HTML,
        reply_markup=markup
    )
SHORT_CLASS = {
    "ct_cn2_gia": "CN2 GIA",
    "ct_cn2_gt_mixed": "CN2 GT",
    "ct_163": "163",
    "ct_ctgnet": "CTGNet",
    "cu_9929": "9929",
    "cu_9929_mixed": "9929+4837",
    "cu_4837": "4837",
    "cu_10099": "CUG",
    "cm_cmin2": "CMIN2",
    "cm_cmin2_mixed": "CMIN2+9808",
    "cm_cmi": "CMI",
    "cm_cmi_mixed": "CMI+9808",
    "cm_9808": "9808",
    "multi_carrier_mixed": "多网混合",
    "unknown": "未识别",
}
CARRIER_CODES = [("电信", "SH_CT"), ("联通", "SH_CU"), ("移动", "SH_CM")]

def _source_locations(code):
    for _label, source_code, location in TRACE_SOURCES:
        if source_code == code:
            return location
    return None

def _analyse_runs(runs):
    """定位 + 线路识别，返回最有代表性的一组结果。"""
    raw_geo = _geo_batch([h["ip"] for _s, hops in runs for h in hops if h["ip"]])
    try:
        raw_geo = _refine_geo(runs, raw_geo)
    except Exception as e:
        print("精确定位失败：", e)
    analysed = []
    for run_source, run_hops in runs:
        run_geo = _sanitize_geo(run_source, run_hops, raw_geo)
        analysed.append({
            "source": run_source,
            "hops": run_hops,
            "geo": run_geo,
            "route_profile": _route_profile(run_hops, run_geo),
            "verdict": _classify_route(run_hops, run_geo, run_source),
        })
    best = _pick_run(analysed)
    return {
        "best": best,
        "verdict": _verdict_summary(analysed),
        "summary": _route_summary(best["source"], best["hops"], best["geo"]),
    }

def _target_rtt(source, hops):
    if source.get("reached") and hops and hops[-1].get("rtt") is not None:
        return hops[-1]["rtt"]
    return None

async def _route_busy(query, text):
    await _panel_show(
        query.message.get_bot(),
        query.message.chat_id,
        text
    )

async def route_compare(query, context):
    target = context.user_data.get("route_target")
    if not target:
        await _route_busy(query, "❌ 请先发送要测试的 IP 或域名。")
        return
    limited = rate_check(query.from_user.id, 3)
    if limited:
        await _panel_show(query.message.get_bot(), query.message.chat_id,
                          "⏳ " + html.escape(limited), route_keyboard())
        return
    progress = await Progress(
        query.message.get_bot(), query.message.chat_id,
        "正在三网测试……", target,
        [name for name, _code in CARRIER_CODES], expected=25
    ).start()
    async def one(index, code):
        progress.set(index, "run", "追踪路由中")
        try:
            runs = await asyncio.to_thread(
                _trace_with_fallback, target, _source_locations(code)
            )
            progress.set(index, "run", "定位、识别线路中")
            result = await asyncio.to_thread(_analyse_runs, runs)
        except Exception:
            progress.set(index, "fail", "失败")
            raise
        best = result["best"]
        rtt = _target_rtt(best["source"], best["hops"])
        progress.set(
            index, "done",
            SHORT_CLASS.get(best["route_profile"]["class"], "")
            + (f" · {rtt:.0f}ms" if rtt is not None else " · 未到达")
        )
        return result
    try:
        results = await asyncio.gather(
            *[one(i, code) for i, (_name, code) in enumerate(CARRIER_CODES)],
            return_exceptions=True
        )
    finally:
        await progress.stop()
    blocks = []
    table = []
    for (name, code), result in zip(CARRIER_CODES, results):
        if isinstance(result, Exception):
            table.append(f"{_ljust(name, 6)}  {_rjust('失败', 6)}  -")
            blocks.append(f"{CARRIER_DOTS.get(name, '📡')} <b>{name}</b>　❌ {html.escape(str(result)[:120])}")
            continue
        best = result["best"]
        source, hops = best["source"], best["hops"]
        route_class = best["route_profile"]["class"]
        rtt = _target_rtt(source, hops)
        detour = result["summary"]["detour"]
        rtt_text = f"{rtt:.0f}ms" if rtt is not None else "未到达"
        table.append(
            f"{_ljust(name, 6)}  {_rjust(rtt_text, 6)}  {SHORT_CLASS.get(route_class, route_class)}"
        )
        blocks.append("\n".join(_carrier_block(name, result["verdict"], detour)))
    routes = []
    for (name, _code), result in zip(CARRIER_CODES, results):
        if isinstance(result, Exception):
            continue
        best = result["best"]
        try:
            routes.append((name, _build_points(best["source"], best["hops"], best["geo"])))
        except Exception as e:
            print("三网测试地图取点失败：", name, e)
    image = None
    try:
        target_geo = None  # 不画推测的终点连线，只画路由实际经过的跳点
        reached = {
            name: bool(r["best"]["source"].get("reached"))
            for (name, _code), r in zip(CARRIER_CODES, results)
            if not isinstance(r, Exception)
        }
        image = await asyncio.to_thread(_render_compare_map, routes, target_geo, reached)
    except Exception as e:
        print("生成三网测试地图失败：", e)
    text = (
        f"📊 <b>三网测试</b>　<code>{html.escape(target)}</code>\n"
        "━━━━━━━━━━━━━━\n"
        "<pre>" + html.escape(f"{_ljust('运营商', 6)}  {_rjust('延迟', 6)}  线路\n" + "\n".join(table)) + "</pre>\n\n"
        + "\n\n".join(blocks)
    )
    use_photo = image is not None and _visible_len(text) <= 1020
    await _panel_show(
        query.message.get_bot(),
        query.message.chat_id,
        text if use_photo else text[:4000],
        route_keyboard(),
        photo=image if use_photo else None
    )

def _gp_run(body, timeout=30, first_wait=1.5, interval=1.5, return_id=False, early_stop=None):
    """
    创建一次 Globalping 测量并等待结果，返回 results 列表（return_id=True 时连测量 ID 一起返回）。
    early_stop(result, 已等秒数) 返回 True 时不再等探针跑完，直接用当前（未完成的）结果。
    """
    headers = {}
    if GLOBALPING_TOKEN:
        headers["Authorization"] = "Bearer " + GLOBALPING_TOKEN
    try:
        created = _http_json(GLOBALPING_API, body, headers)
    except urllib.error.HTTPError as e:
        if e.code in (400, 422):
            raise ProbeUnavailable(_http_error_text(e))
        raise RuntimeError(_http_error_text(e))
    measurement_id = created.get("id")
    if not measurement_id:
        raise RuntimeError("创建检测任务失败。")
    deadline = time.time() + timeout
    result = None
    wait = first_wait
    while time.time() < deadline:
        time.sleep(wait)
        wait = interval
        result = _http_json(GLOBALPING_API + "/" + measurement_id, headers=headers)
        if result.get("status") != "in-progress":
            break
        if early_stop and early_stop(result, time.time() - (deadline - timeout)):
            break
    results = (result or {}).get("results") or []
    return (results, measurement_id) if return_id else results

async def admin_export(query):
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"nav_backup_{_fmt_time(time.time(), '%Y%m%d_%H%M')}.json"
    await query.message.get_bot().send_document(
        chat_id=query.message.chat_id,
        document=io.BytesIO(payload),
        filename=filename,
        caption=(
            f"💾 功能导航备份（{_fmt_time(time.time(), '%Y-%m-%d %H:%M')}）\n"
            "包含分类、网址和授权用户，不含 Bot Token。"
        )
    )
    await edit_page(
        query,
        "✅ 备份文件已发送。\n\n需要恢复时，在管理后台点「📥 导入备份」再把文件发回来即可。",
        reply_markup=InlineKeyboardMarkup([[_btn("⚙️ 管理后台", "admin|back")]])
    )

async def admin_import_prompt(query, context):
    context.user_data["state"] = "import_backup"
    await edit_page(
        query,
        "📥 <b>导入备份</b>\n\n"
        "请直接发送之前导出的 <code>.json</code> 备份文件。\n"
        "⚠️ 导入会覆盖当前的分类和网址（导入前会自动在服务器上保留一份旧数据）。",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[_btn("❌ 取消", "admin|cancel")]])
    )

def _validate_backup(new):
    if not isinstance(new, dict) or not isinstance(new.get("categories"), dict):
        return "文件里没有 categories（分类）数据"
    for category, sites in new["categories"].items():
        if not isinstance(sites, list):
            return f"分类「{category}」格式不正确"
        if _category_name_error(category):
            return f"分类「{category}」名称太长或含“|”，无法做成按钮"
        for site in sites:
            if not isinstance(site, dict) or "name" not in site or "url" not in site:
                return f"分类「{category}」里有网址缺少 name 或 url"
            if not isinstance(site["name"], str) or not isinstance(site["url"], str):
                return f"分类「{category}」里有网址的 name / url 不是文字"
    users = new.get("allowed_users", {})
    if not isinstance(users, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in users.items()
    ):
        return "授权用户（allowed_users）格式不正确"
    return None

async def document_handler(update, context):
    message = update.message
    if context.user_data.get("state") == "update_script" and is_admin(update.effective_user.id):
        await update_handle_document(update, context)
        return
    if not is_admin(update.effective_user.id) or context.user_data.get("state") != "import_backup":
        return
    document = message.document
    if document.file_size and document.file_size > 5 * 1024 * 1024:
        await reply_panel(update, context, "❌ 文件太大（超过 5MB），请确认是导出的备份文件。")
        return
    try:
        telegram_file = await document.get_file()
        raw = await telegram_file.download_as_bytearray()
        new = json.loads(bytes(raw).decode("utf-8"))
    except Exception as e:
        await reply_panel(update, context, f"❌ 读取文件失败：{e}")
        return
    error = _validate_backup(new)
    if error:
        await reply_panel(update, context, f"❌ 这不是有效的备份文件：{error}")
        return
    backup_path = DATA_FILE + ".bak-" + _fmt_time(time.time(), "%Y%m%d%H%M%S")
    try:
        shutil.copyfile(DATA_FILE, backup_path)
    except Exception as e:
        print("保存旧数据失败：", e)
    if "allowed_users" not in new and "allowed_users" in data:
        new["allowed_users"] = data["allowed_users"]
    new.pop("monitors", None)
    data.clear()
    data.update(new)
    save_data(data)
    context.user_data.clear()
    await _try_delete(message)
    categories = len(data["categories"])
    sites = sum(len(s) for s in data["categories"].values())
    await reply_panel(
        update, context,
        f"✅ <b>导入成功</b>\n\n📚 分类 {categories}　🔗 网址 {sites}\n"
        f"🗂 旧数据已保存为 <code>{html.escape(backup_path)}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[_btn("⚙️ 管理后台", "admin|back")]])
    )

def _read_cpu():
    with open("/proc/stat") as f:
        values = [int(v) for v in f.readline().split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle

def _read_net():
    rx = tx = 0
    with open("/proc/net/dev") as f:
        for line in f.readlines()[2:]:
            name, stats = line.split(":", 1)
            if name.strip() == "lo":
                continue
            fields = stats.split()
            rx += int(fields[0])
            tx += int(fields[8])
    return rx, tx

def _fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} B"
        n /= 1024

def _fmt_duration(seconds):
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}天{hours}小时"
    if hours:
        return f"{hours}小时{minutes}分"
    return f"{minutes}分钟"

# CPU 后面的分隔：半角冒号 + 半角宽空格（U+2002）。
# 如果还偏右，可以换成 ":\u2005"（更窄）；偏左了换成 ":\u2002\u200a"（再宽一丝）
STATUS_CPU_SEP = ":\u2002\u200a\u200a\u200a"   # 半角冒号 + 半字宽空格 + 三个极窄空格（每个约往右挪一丝）
# 进度条：有进度的格子 / 空格子。想换样式只改这两个
STATUS_BAR_FULL = "■"
STATUS_BAR_EMPTY = "░"

def _bar(percent, width=10):
    filled = int(round(percent / 100 * width))
    return STATUS_BAR_FULL * filled + STATUS_BAR_EMPTY * (width - filled)

def server_status_text():
    lines = ["🖥 <b>主机状态</b>", "━━━━━━━━━━━━━━"]
    try:
        cpu1, idle1 = _read_cpu()
        rx1, tx1 = _read_net()
        time.sleep(1)
        cpu2, idle2 = _read_cpu()
        rx2, tx2 = _read_net()
        cpu = 100 * (1 - (idle2 - idle1) / max(1, cpu2 - cpu1))
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, value = line.split(":", 1)
                mem[key] = int(value.split()[0]) * 1024
        mem_total = mem.get("MemTotal", 0)
        mem_used = mem_total - mem.get("MemAvailable", mem.get("MemFree", 0))
        mem_pct = 100 * mem_used / max(1, mem_total)
        disk = shutil.disk_usage("/")
        disk_pct = 100 * disk.used / max(1, disk.total)
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        load = os.getloadavg()
        # 普通文字、左对齐（不用代码块）
        lines += [
            f"🏷 {html.escape(socket.gethostname())} · {html.escape(platform.system())} {html.escape(platform.release())}",
            f"⏱ 已运行 {_fmt_duration(uptime)}　🤖 机器人 {_fmt_duration(time.time() - BOT_STARTED)}",
            "",
            # “CPU”比“内存”略宽，所以 CPU 这一行把全角冒号“：”换成半角冒号 + 半个字宽的空格，
            # 整体窄一点点，进度条往左挪，和下面内存、硬盘的进度条对齐
            f"💻 CPU{STATUS_CPU_SEP}{_bar(cpu)} {cpu:.0f}%",
            f"🧠 内存：{_bar(mem_pct)} {mem_pct:.0f}%（{_fmt_bytes(mem_used)} / {_fmt_bytes(mem_total)}）",
            f"🗄 硬盘：{_bar(disk_pct)} {disk_pct:.0f}%（{_fmt_bytes(disk.used)} / {_fmt_bytes(disk.total)}）",
            f"⚙️ 负载：{load[0]:.2f} {load[1]:.2f} {load[2]:.2f}（{os.cpu_count()} 核）",
            f"📶 网速：↑ {_fmt_bytes(tx2 - tx1)}/s　↓ {_fmt_bytes(rx2 - rx1)}/s",
            f"📊 流量：↑ {_fmt_bytes(tx2)}　↓ {_fmt_bytes(rx2)}（开机以来）",
        ]
    except Exception as e:
        lines.append(f"❌ 读取失败：{html.escape(str(e))}（只支持 Linux）")
    lines.append(f"\n🕘 {_fmt_time(time.time(), '%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)

STATUS_REFRESH = 3        # 主机状态每 3 秒自动刷新一次
STATUS_REFRESH_MAX = 600  # 最多自动刷新 10 分钟，之后停下，点「刷新」再继续
_STATUS_TASKS = {}

def _status_markup():
    return InlineKeyboardMarkup([
        [_btn("🔄 刷新", "status"), _btn("🏠 返回首页", "home")],
    ])

def stop_status_refresh(chat_id):
    """离开主机状态页面（点了别的按钮 / 发了消息）时调用，停止自动刷新。"""
    task = _STATUS_TASKS.pop(chat_id, None)
    if task and not task.done():
        task.cancel()

async def _status_refresh_loop(bot, chat_id, message_id):
    began = time.time()
    try:
        while True:
            await asyncio.sleep(max(0.5, STATUS_REFRESH - 1))  # 取数本身要 1 秒，合起来约 3 秒一次
            if PANELS.get(chat_id) != message_id:
                return  # 面板已经换成别的页面了
            text = await asyncio.to_thread(server_status_text)
            if PANELS.get(chat_id) != message_id:
                return
            finished = time.time() - began >= STATUS_REFRESH_MAX
            if finished:
                text += "\n⏸ 已停止自动刷新，点「🔄 刷新」继续"
            else:
                text += f"\n🔁 每 {STATUS_REFRESH} 秒自动刷新"
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=_wide(text),
                    parse_mode=ParseMode.HTML, reply_markup=_status_markup()
                )
            except Exception as e:
                error = str(e).lower()
                if "not modified" not in error:
                    if "retry" in error or "flood" in error:
                        await asyncio.sleep(5)
                        continue
                    print("主机状态自动刷新停止：", e)
                    return
            if finished:
                return
    except asyncio.CancelledError:
        pass
    finally:
        if _STATUS_TASKS.get(chat_id) is asyncio.current_task():
            _STATUS_TASKS.pop(chat_id, None)

async def show_server_status(query):
    chat_id = query.message.chat_id
    stop_status_refresh(chat_id)
    text = await asyncio.to_thread(server_status_text)
    await edit_page(
        query,
        text + f"\n🔁 每 {STATUS_REFRESH} 秒自动刷新",
        parse_mode=ParseMode.HTML,
        reply_markup=_status_markup()
    )
    message_id = PANELS.get(chat_id)
    if message_id:
        _STATUS_TASKS[chat_id] = asyncio.create_task(
            _status_refresh_loop(query.message.get_bot(), chat_id, message_id)
        )
import sys as _sys
import difflib as _difflib
SCRIPT_PATH = os.path.abspath(__file__)
UPDATE_BACKUP_DIR = "/root/nav_bot_backups"
UPDATE_STATE_FILE = "/root/nav_update_state.json"
UPDATE_KEEP = 5
_RESTART = {"go": False}

def _upd_state_load():
    try:
        with open(UPDATE_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _upd_state_save(state):
    try:
        with open(UPDATE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        print("保存更新状态失败：", e)

def _upd_state_clear():
    try:
        os.remove(UPDATE_STATE_FILE)
    except Exception:
        pass

def _upd_backups():
    """旧版本列表（新的在前）：[(路径, 时间戳)]"""
    try:
        names = [n for n in os.listdir(UPDATE_BACKUP_DIR) if n.endswith(".py") and not n.startswith("_")]
    except Exception:
        return []
    items = [(os.path.join(UPDATE_BACKUP_DIR, n), os.path.getmtime(os.path.join(UPDATE_BACKUP_DIR, n))) for n in names]
    return sorted(items, key=lambda x: -x[1])

def _upd_label(path, mtime):
    """旧版本按钮文字：时间 · 更新前 / 回滚前 · 大小"""
    kind = "回滚前" if "rollback" in os.path.basename(path) else "更新前"
    try:
        size = f"{os.path.getsize(path) / 1024:.0f}KB"
    except OSError:
        size = ""
    return " · ".join(x for x in (_fmt_time(mtime), kind, size) if x)

def _upd_install(src):
    """
    用 src 替换正在运行的脚本：先写到同目录的临时文件，再一次性替换。
    中途失败（比如磁盘满）原脚本完好无损，不会留下半截文件。
    """
    temp = SCRIPT_PATH + ".installing"
    try:
        shutil.copyfile(src, temp)
        try:
            shutil.copymode(SCRIPT_PATH, temp)
        except OSError:
            pass
        os.replace(temp, SCRIPT_PATH)
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise

def _upd_discard(path):
    """删掉没用上的上传文件（检查没通过 / 和当前一样 / 放弃更新）。"""
    if path:
        try:
            os.remove(path)
        except OSError:
            pass

def _file_sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def _upd_backup_current(tag="before-update", keep=None):
    """
    把正在运行的脚本备份一份，返回备份路径；只保留最近 UPDATE_KEEP 份。
    keep：这次要用到的旧版本（回滚目标），清理时不能删掉它。
    """
    os.makedirs(UPDATE_BACKUP_DIR, exist_ok=True)
    name = f"{os.path.splitext(os.path.basename(SCRIPT_PATH))[0]}_{_fmt_time(time.time(), '%Y%m%d_%H%M%S')}_{tag}.py"
    path = os.path.join(UPDATE_BACKUP_DIR, name)
    shutil.copy2(SCRIPT_PATH, path)
    for old, _t in _upd_backups()[UPDATE_KEEP:]:
        if keep and os.path.abspath(old) == os.path.abspath(keep):
            continue
        try:
            os.remove(old)
        except Exception:
            pass
    return path

def _upd_check(path):
    """
    检查新脚本：1. 语法  2. 在独立进程里试加载一次（不会启动机器人），
    能发现缺少模块、写错变量名这类启动就会报错的问题。返回错误文字，没问题返回 None。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
    except UnicodeDecodeError:
        return "不是 UTF-8 文本文件"
    try:
        compile(source, os.path.basename(path), "exec")
    except SyntaxError as e:
        return f"语法错误：第 {e.lineno} 行 {e.msg}\n{(e.text or '').strip()}"
    if "def main(" not in source or "Application" not in source:
        return "这看起来不是机器人脚本（找不到 main / Application）"
    probe = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('navbot_check', {path!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "assert callable(getattr(m, 'main', None)), '缺少 main()'\n"
        "print('LOAD_OK')\n"
    )
    try:
        run = subprocess.run(
            [_sys.executable, "-c", probe],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.dirname(SCRIPT_PATH),
        )
    except subprocess.TimeoutExpired:
        return "试加载超时（60 秒），脚本加载时可能卡住了"
    if "LOAD_OK" not in run.stdout:
        err = (run.stderr or run.stdout or "未知错误").strip().splitlines()
        return "试加载失败：\n" + "\n".join(err[-6:])
    return None

def _upd_diff_summary(new_path):
    try:
        with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
            old = f.read().splitlines()
        with open(new_path, "r", encoding="utf-8") as f:
            new = f.read().splitlines()
    except Exception:
        return None, 0, 0, 0
    added = removed = 0
    for line in _difflib.unified_diff(old, new, lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return old == new, len(new), added, removed

def request_restart(application):
    """让机器人停止轮询后重新启动自己（main() 末尾执行 os.execv）。"""
    _RESTART["go"] = True
    try:
        application.stop_running()
    except AttributeError:
        import signal
        os.kill(os.getpid(), signal.SIGINT)

def _restart_process():
    print("正在重启机器人……")
    os.execv(_sys.executable, [_sys.executable] + _sys.argv)
UPD_STEPS = [
    "接收文件", "检查语法", "试加载新脚本", "对比改动",
    "备份旧版本", "替换脚本", "重启机器人", "新版本启动",
]

def _upd_progress_text(title, done, current=None, pct=None, note="", first=0, last=7, failed=False):
    """
    更新进度面板：进度条 + 步骤清单。
    done：已完成的步骤数（从 first 算起的绝对序号）；current：正在进行的步骤。
    """
    total = last - first + 1
    if pct is None:
        pct = int((done - first) * 100 / total)
    pct = max(0, min(100, pct))
    bar, _ = _scan_bar(pct, 100)
    lines = [title, "━━━━━━━━━━━━━━", f"{bar} {pct}%", ""]
    for i in range(first, last + 1):
        if i < done:
            icon = "✅"
        elif i == current:
            icon = "❌" if failed else "🔄"
        else:
            icon = "⏸"
        lines.append(f"{icon} {UPD_STEPS[i]}")
    if note:
        lines.append("\n" + note)
    return "\n".join(lines)

async def update_notify_after_boot(application):
    """启动完成后：如果刚更新 / 回滚过，通知管理员结果。在 post_init 里调用。"""
    state = _upd_state_load()
    if not state:
        return
    _upd_state_clear()
    if time.time() - state.get("time", 0) > 600:
        return
    chat_id = state.get("chat_id")
    if not chat_id:
        return
    if state.get("stage") == "trial":
        text = _upd_progress_text(
            "✅ <b>更新成功，新版本已运行</b>", 8, first=4, pct=100,
            note=f"📄 {html.escape(state.get('desc', ''))}\n"
                 "有问题可以在 管理后台 → ⬆️ 版本更新 → ⏪ 回滚"
        )
    elif state.get("stage") == "restart":
        text = "✅ <b>机器人已重启</b>"
    elif state.get("stage") == "rolled_back":
        text = _upd_progress_text(
            "❌ <b>新版本启动失败，已自动恢复旧版本</b>", 7, current=7, first=4, pct=100, failed=True,
            note=f"<pre>{html.escape(state.get('error', '')[:800])}</pre>"
        )
    else:
        return
    markup = InlineKeyboardMarkup([[_btn("⬆️ 版本更新", "upd|home"), _btn("🏠 返回首页", "home")]])
    if state.get("msg_id"):
        try:
            await application.bot.edit_message_text(
                chat_id=chat_id, message_id=state["msg_id"], text=text,
                parse_mode=ParseMode.HTML, reply_markup=markup
            )
            PANELS[chat_id] = state["msg_id"]
            return
        except Exception as e:
            print("更新进度面板失败，改发新消息：", e)
    try:
        sent = await application.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=markup)
        PANELS[chat_id] = sent.message_id
    except Exception as e:
        print("发送更新通知失败：", e)
        return
    if state.get("msg_id"):
        try:
            await application.bot.delete_message(chat_id=chat_id, message_id=state["msg_id"])
        except Exception:
            pass

def run_with_rollback(main_func):
    """
    启动入口：新版本试运行期间（stage=trial）如果启动报错，
    自动把备份的旧版本放回去并重启，旧版本启动后会通知你失败原因。
    """
    try:
        main_func()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:
        state = _upd_state_load()
        network_error = False
        try:
            from telegram.error import NetworkError
            network_error = isinstance(e, NetworkError)
        except Exception:
            pass
        if (
            state and state.get("stage") == "trial" and state.get("backup")
            and os.path.exists(state["backup"])
            and not network_error
            and time.time() - float(state.get("time") or 0) < 600
        ):
            print("新版本启动失败，自动回滚：", e)
            _upd_install(state["backup"])
            state.update(stage="rolled_back", error=f"{type(e).__name__}: {e}", time=time.time())
            _upd_state_save(state)
            _restart_process()
        raise
    if _RESTART["go"]:
        _restart_process()

def _upd_home_view():
    backups = _upd_backups()
    lines = [
        "⬆️ <b>版本更新</b>",
        "━━━━━━━━━━━━━━",
        f"📄 当前脚本：<code>{html.escape(os.path.basename(SCRIPT_PATH))}</code>",
        f"🕘 更新时间：{_fmt_time(os.path.getmtime(SCRIPT_PATH), '%Y-%m-%d %H:%M')}",
        f"💾 旧版本：{len(backups)} 个" + ("，可一键回滚" if backups else ""),
        "",
        "📤 <b>把新的 .py 文件直接发给我</b>",
        "▪ 检查语法并试加载",
        "▪ 显示改动行数",
        "▪ 确认后备份旧版、替换并自动重启",
        "",
        "🛡 新版本启动失败会自动恢复旧版本",
    ]
    keyboard = []
    if backups:
        keyboard.append([_btn("⏪ 回滚旧版本", "upd|list"), _btn("🗂 旧版本管理", "upd|olds")])
    keyboard.append([_btn("🔁 重启机器人", "upd|restart")])
    keyboard.append([_btn("🎛 管理后台", "admin|back")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)

async def update_entry(query, context):
    context.user_data["state"] = "update_script"
    text, markup = _upd_home_view()
    await edit_page(query, text, parse_mode=ParseMode.HTML, reply_markup=markup)

async def update_handle_document(update, context):
    """update_script 状态下收到 .py 文件。"""
    message = update.message
    bot = message.get_bot()
    chat_id = message.chat_id
    document = message.document
    await _try_delete(message)
    old_pending = context.user_data.pop("update_pending", None)
    if old_pending:
        try:
            os.remove(old_pending["path"])
        except Exception:
            pass
    back = InlineKeyboardMarkup([[_btn("⬆️ 版本更新", "upd|home"), _btn("⚙️ 管理后台", "admin|back")]])
    if not (document.file_name or "").endswith(".py"):
        await _panel_show(bot, chat_id, "❌ 请发送 <b>.py</b> 脚本文件。", back)
        return
    if (document.file_size or 0) > 5 * 1024 * 1024:
        await _panel_show(bot, chat_id, "❌ 文件太大（超过 5MB），不像是机器人脚本。", back)
        return
    title = f"🔍 <b>检查新脚本</b>　<code>{html.escape(document.file_name)}</code>"
    async def show(done, current, pct=None, failed=False, note=""):
        await _panel_show(bot, chat_id, _upd_progress_text(
            title, done, current, pct, note, first=0, last=3, failed=failed
        ))
    await show(0, 0)
    os.makedirs(UPDATE_BACKUP_DIR, exist_ok=True)
    new_path = os.path.join(UPDATE_BACKUP_DIR, f"_incoming_{chat_id}_{int(time.time() * 1000)}.py")
    try:
        tg_file = await document.get_file()
        await tg_file.download_to_drive(new_path)
    except Exception as e:
        await _panel_show(bot, chat_id, f"❌ 接收文件失败：{html.escape(str(e))}", back)
        return
    await show(1, 1)
    check = asyncio.create_task(asyncio.to_thread(_upd_check, new_path))
    started = time.time()
    await asyncio.sleep(0.3)
    while not check.done():
        pct = 50 + min(20, int((time.time() - started) * 2.5))
        await show(2, 2, pct)
        await asyncio.wait({check}, timeout=1.5)
    error = check.result()
    if error:
        _upd_discard(new_path)
        await _panel_show(
            bot, chat_id,
            "❌ <b>检查没通过，没有替换</b>\n"
            f"📄 {html.escape(document.file_name)}\n\n"
            f"<pre>{html.escape(error[:1500])}</pre>\n"
            "当前运行的版本不受影响。",
            back
        )
        return
    await show(3, 3, 85)
    same, lines, added, removed = _upd_diff_summary(new_path)
    if same:
        _upd_discard(new_path)
        await _panel_show(bot, chat_id, "ℹ️ 这个文件和正在运行的脚本完全一样，不需要更新。", back)
        return
    context.user_data["update_pending"] = {
        "path": new_path, "name": document.file_name, "sha": _file_sha256(new_path),
    }
    try:
        with open(new_path, "r", encoding="utf-8") as f:
            has_updater = "def update_notify_after_boot" in f.read()
    except Exception:
        has_updater = True
    warn = "" if has_updater else (
        "\n⚠️ <b>注意：这个脚本没有在线更新功能</b>\n"
        "换上后收不到更新结果通知，也不能再在机器人里更新 / 回滚，\n"
        "之后要恢复只能登服务器手动替换。\n"
    )
    await _panel_show(
        bot, chat_id,
        _upd_progress_text("✅ <b>检查通过</b>", 4, first=0, last=3) + "\n\n"
        f"📄 新脚本：<code>{html.escape(document.file_name)}</code>\n"
        f"📏 共 {lines} 行　✏️ <b>+{added}</b> / <b>-{removed}</b> 行\n"
        + warn + "\n"
        "▪ 确认后：备份 → 替换 → 重启（约 5~10 秒）\n"
        "🛡 新版本启动失败会自动恢复旧版本",
        InlineKeyboardMarkup([
            [_btn("✅ 确认更新并重启", "upd|go")],
            [_btn("↩️ 取消", "upd|home")],
        ])
    )

async def _upd_olds_page(query, context, notice=""):
    """旧版本管理页：列出备份的旧脚本，可以逐个删除或全部清空。"""
    backups = _upd_backups()
    context.user_data["update_olds"] = [p for p, _t in backups]
    lines = ["🗂 <b>旧版本管理</b>", "━━━━━━━━━━━━━━"]
    if notice:
        lines += [html.escape(notice), ""]
    if backups:
        total = sum(os.path.getsize(p) for p, _t in backups if os.path.exists(p))
        lines.append(f"💾 共 <b>{len(backups)}</b> 个旧版本 · 占用 {total / 1024:.0f} KB")
        lines.append("")
        lines.append("▪ 时间 = 被替换下来的时间，新的在上")
        lines.append("▪ 更新前 = 在线更新时备份")
        lines.append("▪ 回滚前 = 回滚时备份")
        lines.append("▪ 点 🗑 删除对应版本")
    else:
        lines.append("📭 没有旧版本")
    keyboard = [
        [_btn(f"🗑 {_upd_label(p, t)}", f"upd|odel|{i}")]
        for i, (p, t) in enumerate(backups)
    ]
    if backups:
        keyboard.append([_btn("🧹 全部删除", "upd|oclr")])
    keyboard.append([_btn("⬆️ 版本更新", "upd|home"), _btn("🎛 管理后台", "admin|back")])
    await edit_page(query, "\n".join(lines), parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(keyboard))

async def update_callback(query, context):
    if not is_admin(query.from_user.id):
        return
    ud = context.user_data
    parts = query.data.split("|")
    action = parts[1] if len(parts) > 1 else "home"
    chat_id = query.message.chat_id
    if action == "home":
        _upd_discard((ud.pop("update_pending", None) or {}).get("path"))
        await update_entry(query, context)
        return
    if action == "go":
        pending = ud.pop("update_pending", None)
        def _pending_ok(p):
            try:
                return bool(p) and os.path.exists(p["path"]) and (
                    not p.get("sha") or _file_sha256(p["path"]) == p["sha"]
                )
            except Exception:
                return False
        if not _pending_ok(pending):
            await edit_page(query, "⌛ 这次更新已失效，请重新发送脚本。",
                            reply_markup=InlineKeyboardMarkup([[_btn("⬆️ 版本更新", "upd|home")]]))
            return
        title = "⬆️ <b>正在更新</b>"
        async def show(done, current, pct=None):
            await edit_page(query, _upd_progress_text(title, done, current, pct, first=4, last=7),
                            parse_mode=ParseMode.HTML)
        try:
            await show(4, 4)
            backup = _upd_backup_current("before-update")
            await show(5, 5)
            _upd_install(pending["path"])
        except Exception as e:
            await edit_page(query, f"❌ 替换失败：{html.escape(str(e))}\n当前版本不受影响。",
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup([[_btn("⬆️ 版本更新", "upd|home")]]))
            return
        try:
            os.remove(pending["path"])
        except Exception:
            pass
        _upd_state_save({
            "stage": "trial", "backup": backup, "chat_id": chat_id,
            "desc": pending["name"], "time": time.time(),
            "msg_id": query.message.message_id,
        })
        await show(6, 6, 75)
        await edit_page(
            query,
            _upd_progress_text(title, 6, 6, 80, first=4, last=7,
                               note="🔁 机器人正在重启，新版本启动后这里会自动更新到 100%"),
            parse_mode=ParseMode.HTML
        )
        request_restart(context.application)
        return
    if action == "restart":
        await edit_page(
            query, "🔁 确定重启机器人？",
            reply_markup=InlineKeyboardMarkup([[_btn("✅ 重启", "upd|restartok"), _btn("❌ 取消", "upd|home")]])
        )
        return
    if action == "restartok":
        _upd_state_save({"stage": "restart", "chat_id": chat_id, "time": time.time(),
                         "msg_id": query.message.message_id})
        await edit_page(query, "🔁 机器人正在重启……")
        request_restart(context.application)
        return
    if action == "olds":
        await _upd_olds_page(query, context)
        return
    if action == "odel":
        paths = ud.get("update_olds", [])
        try:
            path = paths[int(parts[2])]
        except (IndexError, ValueError):
            path = None
        if path and os.path.dirname(os.path.abspath(path)) == os.path.abspath(UPDATE_BACKUP_DIR):
            try:
                os.remove(path)
                notice = f"✅ 已删除 {os.path.basename(path)}"
            except Exception as e:
                notice = f"❌ 删除失败：{e}"
        else:
            notice = "⌛ 列表已变化，请重新选择"
        await _upd_olds_page(query, context, notice)
        return
    if action == "oclr":
        count = len(_upd_backups())
        await edit_page(
            query,
            f"🗑 确定删除全部 {count} 个旧版本？\n\n删除后就不能再回滚到这些版本了，当前运行的脚本不受影响。",
            reply_markup=InlineKeyboardMarkup([
                [_btn("✅ 确定全部删除", "upd|oclrok"), _btn("❌ 取消", "upd|olds")]
            ])
        )
        return
    if action == "oclrok":
        removed = 0
        for path, _t in _upd_backups():
            try:
                os.remove(path)
                removed += 1
            except Exception as e:
                print("删除旧版本失败：", path, e)
        await _upd_olds_page(query, context, f"✅ 已删除 {removed} 个旧版本")
        return
    if action == "list":
        backups = _upd_backups()
        ud["update_backups"] = [p for p, _t in backups]
        keyboard = [
            [_btn(f"⏪ {_upd_label(p, t)}", f"upd|rb|{i}")]
            for i, (p, t) in enumerate(backups)
        ]
        keyboard.append([_btn("⬆️ 版本更新", "upd|home")])
        await edit_page(
            query,
            "⏪ <b>回滚到旧版本</b>\n━━━━━━━━━━━━━━\n"
            "时间是该版本被替换下来的时间（新的在上）。\n选一个恢复：",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    if action in ("rb", "rbok"):
        try:
            path = ud.get("update_backups", [])[int(parts[2])]
        except (IndexError, ValueError):
            return
        if action == "rb":
            await edit_page(
                query,
                f"⏪ 确定恢复到这个版本并重启？\n<code>{html.escape(os.path.basename(path))}</code>\n\n"
                "当前版本也会先备份一份。",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [_btn("✅ 确定回滚", f"upd|rbok|{parts[2]}"), _btn("❌ 取消", "upd|list")]
                ])
            )
            return
        error = await asyncio.to_thread(_upd_check, path)
        if error:
            await edit_page(query, f"❌ 这个旧版本检查不通过：\n<pre>{html.escape(error[:1000])}</pre>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup([[_btn("⏪ 返回", "upd|list")]]))
            return
        try:
            backup = _upd_backup_current("before-rollback", keep=path)
            _upd_install(path)
        except Exception as e:
            await edit_page(query, f"❌ 回滚失败：{html.escape(str(e))}\n当前版本不受影响。",
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup([[_btn("⏪ 返回", "upd|list")]]))
            return
        _upd_state_save({
            "stage": "trial", "backup": backup, "chat_id": chat_id,
            "desc": "回滚到 " + os.path.basename(path), "time": time.time(),
            "msg_id": query.message.message_id,
        })
        await edit_page(query, "⏪ 已恢复旧版本，机器人正在重启……")
        request_restart(context.application)
        return
ABUSEIPDB_BUILTIN_KEY = "7576f4ce98b3c39e1a2e135a6deca0f65f300de033aee6ec411bf5096c0f008eb73028ff2e377435"
IPQ_KEYS = {
    "ipapi_is": os.environ.get("IPAPI_IS_KEY") or CONFIG.get("ipapi_is_key") or "",
    "proxycheck": os.environ.get("PROXYCHECK_KEY") or CONFIG.get("proxycheck_key") or "",
    "abuseipdb": os.environ.get("ABUSEIPDB_KEY") or CONFIG.get("abuseipdb_key") or ABUSEIPDB_BUILTIN_KEY,
}
IPQ_DNSBL = [
    ("Spamhaus", "zen.spamhaus.org"),
    ("SpamCop", "bl.spamcop.net"),
    ("PSBL", "psbl.surriel.com"),
    ("UCEPROTECT", "dnsbl-1.uceprotect.net"),
    ("DroneBL", "dnsbl.dronebl.org"),
    ("Mailspike", "bl.mailspike.net"),
    ("Manitu", "ix.dnsbl.manitu.net"),
]
IPQ_CACHE = {}
IPQ_CACHE_SECONDS = 600

def _ipq_ipapi_com(ip):
    url = (
        "http://ip-api.com/json/" + ip
        + "?lang=zh-CN&fields=status,message,country,countryCode,regionName,city,isp,org,as,mobile,proxy,hosting,query"
    )
    return _http_json(url, timeout=8)

def _ipq_ipapi_is(ip):
    url = "https://api.ipapi.is/?q=" + ip
    if IPQ_KEYS["ipapi_is"]:
        url += "&key=" + urllib.parse.quote(IPQ_KEYS["ipapi_is"])
    return _http_json(url, timeout=8)

def _ipq_proxycheck(ip):
    url = f"https://proxycheck.io/v2/{ip}?vpn=1&risk=1"
    if IPQ_KEYS["proxycheck"]:
        url += "&key=" + urllib.parse.quote(IPQ_KEYS["proxycheck"])
    data = _http_json(url, timeout=8)
    if data.get("status") not in ("ok", "warning"):
        raise RuntimeError(data.get("message") or data.get("status") or "查询失败")
    return data.get(ip) or {}

def _ipq_rir(ip):
    """IP 段在地区互联网注册机构（RIR）登记的国家，比如 154.80.0.0/12 登记在塞舌尔。"""
    data = _http_json(
        "https://stat.ripe.net/data/rir-stats-country/data.json?resource=" + ip, timeout=8
    )
    located = (data.get("data") or {}).get("located_resources") or []
    return (located[0].get("location") or "").upper() if located else None

def _ipq_abuseipdb(ip):
    if not IPQ_KEYS["abuseipdb"]:
        return None
    url = "https://api.abuseipdb.com/api/v2/check?maxAgeInDays=90&ipAddress=" + ip
    data = _http_json(url, headers={"Key": IPQ_KEYS["abuseipdb"], "Accept": "application/json"}, timeout=8)
    return data.get("data") or {}

def _ipq_dnsbl(ip):
    """返回 (命中的列表名, 查询成功的列表数)。只支持 IPv4。"""
    from concurrent.futures import ThreadPoolExecutor, wait
    reversed_ip = ".".join(reversed(ip.split(".")))
    def query(zone):
        try:
            answer = socket.gethostbyname(f"{reversed_ip}.{zone}")
        except socket.gaierror as e:
            no_record = {getattr(socket, n) for n in ("EAI_NONAME", "EAI_NODATA") if hasattr(socket, n)}
            return "clean" if e.errno in no_record else "error"
        except Exception:
            return "error"
        if answer.startswith("127.255.255.") or not answer.startswith("127."):
            return "error"
        return "listed"
    pool = ThreadPoolExecutor(max_workers=len(IPQ_DNSBL))
    futures = {pool.submit(query, zone): name for name, zone in IPQ_DNSBL}
    done, _pending = wait(futures, timeout=6)
    pool.shutdown(wait=False)
    hits, ok = [], 0
    for future in done:
        status = future.result()
        if status == "error":
            continue
        ok += 1
        if status == "listed":
            hits.append(futures[future])
    return sorted(hits), ok

IPQ_SOURCE_NAMES = {
    "ipapi": "位置 / 网络",
    "ipapi_is": "类型 / 代理",
    "proxycheck": "风险评分",
    "abuseipdb": "滥用举报",
    "rir": "注册地",
    "dnsbl": "黑名单库",
}

def ip_quality(ip, progress=None):
    """
    并发查各个来源，返回汇总 dict（查失败的来源为 None）。
    progress：可选的 dict，查询过程中写入 names（所有来源）和 done（已完成的来源），给进度条用。
    """
    cached = IPQ_CACHE.get(ip)
    if cached and time.time() - cached[0] < IPQ_CACHE_SECONDS:
        if progress is not None:
            progress["names"] = list(cached[1].keys())
            progress["done"] = set(cached[1].keys())
        return cached[1]
    from concurrent.futures import ThreadPoolExecutor, wait
    jobs = {
        "ipapi": _ipq_ipapi_com,
        "ipapi_is": _ipq_ipapi_is,
        "proxycheck": _ipq_proxycheck,
        "abuseipdb": _ipq_abuseipdb,
        "rir": _ipq_rir,
    }
    if ":" not in ip:
        jobs["dnsbl"] = _ipq_dnsbl
    result = {}
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    futures = {name: pool.submit(fn, ip) for name, fn in jobs.items()}
    if progress is not None:
        progress["names"] = list(jobs.keys())
        for name, future in futures.items():
            future.add_done_callback(lambda _f, n=name: progress["done"].add(n))
    wait(futures.values(), timeout=12)
    pool.shutdown(wait=False)
    for name, future in futures.items():
        if not future.done():
            print(f"IP 质量：{name} 超时")
            result[name] = None
            continue
        try:
            result[name] = future.result()
        except Exception as e:
            print(f"IP 质量：{name} 查询失败：", e)
            result[name] = None
    failed = sum(1 for v in result.values() if v is None)
    if failed <= len(result) // 2:
        if len(IPQ_CACHE) > 300:
            IPQ_CACHE.clear()
        IPQ_CACHE[ip] = (time.time(), result)
    return result

def _as_dict(value):
    """接口返回的字段不是字典（比如免费版返回精简数据）时当成空字典。"""
    return value if isinstance(value, dict) else {}

def _flag_emoji(code):
    code = (code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return "🌐"
    return chr(0x1F1E6 + ord(code[0]) - 65) + chr(0x1F1E6 + ord(code[1]) - 65)

def ip_quality_text(target, ip, q):
    """
    排版：先给结论，再分「基本信息」「风险检测」两组；
    每行最前面的图标就是这一项的状态（✅ 正常 / ⚠️ 有问题 / ❔ 未知），一眼能看出哪里有问题。
    评分规则和原来完全一样。
    """
    e = html.escape
    api = _as_dict(q.get("ipapi"))
    iis = _as_dict(q.get("ipapi_is"))
    pc = _as_dict(q.get("proxycheck"))
    ab = q.get("abuseipdb")
    ab = None if ab is None else _as_dict(ab)
    bl = q.get("dnsbl")
    score = 0
    warnings = 0
    place = " ".join(x for x in (api.get("country"), api.get("regionName"), api.get("city")) if x) or "未知"
    network = api.get("as") or _as_dict(iis.get("asn")).get("org") or "未知"
    ctype = _as_dict(iis.get("company")).get("type")
    usage = (ab or {}).get("usageType") or ""
    if iis.get("is_datacenter") or ctype == "hosting" or api.get("hosting") or "Data Center" in usage:
        type_icon, ip_type = "🏢", "机房（IDC）"
    elif iis.get("is_mobile") or api.get("mobile") or "Mobile" in usage:
        type_icon, ip_type = "📱", "移动网络"
    elif ctype in ("isp",) or "ISP" in usage:
        type_icon, ip_type = "🏠", "家庭宽带 / ISP"
    elif ctype in ("business", "education", "government", "banking"):
        type_icon, ip_type = {"business": ("🏬", "企业"), "education": ("🎓", "教育网"),
                              "government": ("🏛", "政府"), "banking": ("🏦", "金融")}[ctype]
    else:
        type_icon = "❔"
        ip_type = "未知（配置 abuseipdb_key 可识别）" if ab is None else "未知"
    reg = (q.get("rir") or _as_dict(iis.get("asn")).get("country") or "").upper()
    loc = (_as_dict(iis.get("location")).get("country_code") or api.get("countryCode") or "").upper()
    if reg and loc:
        if reg == loc:
            native_icon, native = "✅", f"原生 · 注册地和使用地都是{_country_zh(loc)}"
        else:
            native_icon, native = "❌", f"非原生 · 注册地 {_country_zh(reg)} → 使用地 {_country_zh(loc)}"
    else:
        native_icon, native = "❔", "未知"
    flags = []
    if iis.get("is_tor") or (ab or {}).get("isTor") or pc.get("type") == "TOR":
        flags.append("Tor")
    if iis.get("is_vpn") or pc.get("type") == "VPN":
        flags.append("VPN")
    if iis.get("is_proxy") or api.get("proxy") or (pc.get("proxy") == "yes" and pc.get("type") not in ("VPN", "TOR")):
        flags.append("代理")
    if "Tor" in flags:
        score += 3
    elif flags:
        score += 1
    if flags:
        warnings += 1
        proxy_icon, proxy_text = "⚠️", "发现 " + " / ".join(flags)
    else:
        proxy_icon, proxy_text = "✅", "未发现"
    risk = pc.get("risk")
    try:
        risk = float(risk) if risk is not None else None
    except (TypeError, ValueError):
        risk = None
    if risk is not None:
        level = "低" if risk <= 33 else ("中" if risk <= 66 else "高")
        risk_icon = "🟢" if level == "低" else "🟡" if level == "中" else "🔴"
        filled = int(round(min(100, max(0, risk)) / 10))
        bar = STATUS_BAR_FULL * filled + STATUS_BAR_EMPTY * (10 - filled)
        risk_text = f"{level} {bar} {risk:.0f}/100"
        score += 0 if level == "低" else (1 if level == "中" else 2)
        if level != "低":
            warnings += 1
    else:
        risk_icon, risk_text = "❔", "未知"
    if iis.get("is_abuser"):
        risk_text += " · 有滥用记录"
        risk_icon = "⚠️" if risk_icon in ("🟢", "❔") else risk_icon
        score += 1
        warnings += 1
    if ab is None and not IPQ_KEYS.get("abuseipdb"):
        abuse_icon, abuse_text = "➖", "未启用（配置 abuseipdb_key 后显示）"
    elif ab is None or not ab:
        abuse_icon, abuse_text = "❔", "查询失败"
    else:
        reports = int(ab.get("totalReports") or 0)
        confidence = int(ab.get("abuseConfidenceScore") or 0)
        abuse_icon = "✅" if reports == 0 else "⚠️"
        abuse_text = f"90 天 {reports} 次 · 可信度 {confidence}%"
        score += 0 if confidence < 25 else (1 if confidence < 75 else 3)
        if reports:
            warnings += 1
    if ":" in ip:
        bl_icon, bl_text = "➖", "仅支持 IPv4"
    elif bl is None:
        bl_icon, bl_text = "❔", "查询失败"
    else:
        hits, ok = bl
        if not ok:
            bl_icon, bl_text = "❔", "查询失败"
        elif hits:
            bl_icon, bl_text = "⚠️", f"{len(hits)} / {ok} 命中（{'、'.join(hits)}）"
            score += 1 if len(hits) == 1 else 2
            warnings += 1
        else:
            bl_icon, bl_text = "✅", f"0 / {ok} 命中"
    if score == 0:
        verdict = "🟢 <b>干净</b>　未发现风险标记"
    elif score <= 3:
        verdict = f"🟡 <b>一般</b>　发现 {warnings or 1} 项风险标记"
    else:
        verdict = f"🔴 <b>高风险</b>　发现 {warnings or 1} 项风险标记"
    head = f"<code>{e(target)}</code>"
    if target != ip:
        head += f"\n↳ <code>{e(ip)}</code>"
    lines = [
        "🔬 <b>IP 质量检测</b>",
        head,
        "━━━━━━━━━━━━━━",
        verdict,
        "",
        "<b>📋 基本信息</b>",
        f"{_flag_emoji(loc)} 位置：{e(place)}",
        f"🕸️ 网络：{e(network)}",
        f"{type_icon} 类型：{e(ip_type)}",
        f"{native_icon} 原生：{e(native)}",
        "",
        "<b>🛡 风险检测</b>",
        f"{proxy_icon} 代理：{e(proxy_text)}",
        f"{risk_icon} 风险：{e(risk_text)}",
        f"{abuse_icon} 举报：{e(abuse_text)}",
        f"{bl_icon} 黑名单：{e(bl_text)}",
        "━━━━━━━━━━━━━━",
        "💡 发送其他 IP 或域名，本消息直接更新",
    ]
    return "\n".join(lines)

def _ipq_markup():
    return InlineKeyboardMarkup([
        [_btn("🔄 重新检测", "iq|again"), _btn("🏠 返回首页", "home")],
    ])

async def ip_quality_prompt(query, context):
    context.user_data["state"] = "ipq"
    await edit_page(
        query,
        "🔐 <b>IP 质量检测</b>\n"
        "━━━━━━━━━━━━━━\n"
        "⌨️ 请发送要检测的IP或域名",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            recent_entry_rows(query.from_user.id, "iq") + [[_btn("🏠 返回首页", "home")]]
        )
    )

def _ipq_resolve(host):
    """域名解析成 IP（优先 IPv4）。"""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    ips = [i[4][0] for i in infos]
    return next((ip for ip in ips if ":" not in ip), ips[0] if ips else None)

def _ipq_progress_text(target, ip, progress=None, step=None):
    """检测中的进度页：进度条 + 每个数据源的完成情况。"""
    e = html.escape
    lines = ["🔬 <b>IP 质量检测</b>", f"<code>{e(target)}</code>"]
    if ip and ip != target:
        lines.append(f"↳ <code>{e(ip)}</code>")
    lines.append("━━━━━━━━━━━━━━")
    names = (progress or {}).get("names") or []
    done = set((progress or {}).get("done") or ())
    if not names:
        lines.append(f"{STATUS_BAR_EMPTY * 10} 0%")
        lines.append(f"⏳ {step or '准备中…'}")
        return "\n".join(lines)
    finished = sum(1 for n in names if n in done)
    pct = int(round(100 * finished / len(names)))
    filled = int(round(pct / 10))
    lines.append(f"{STATUS_BAR_FULL * filled}{STATUS_BAR_EMPTY * (10 - filled)} {pct}%（{finished}/{len(names)}）")
    lines.append("")
    for n in names:
        lines.append(f"{'✅' if n in done else '⏳'} {IPQ_SOURCE_NAMES.get(n, n)}")
    return "\n".join(lines)

async def run_ip_quality(bot, chat_id, user_id, context, target, fresh=False):
    limited = rate_check(user_id, 1)
    if limited:
        await _panel_show(bot, chat_id, "⏳ " + html.escape(limited), _ipq_markup())
        return
    context.user_data["ipq_last"] = target
    recent_target_add(user_id, target)
    is_domain = True
    try:
        ipaddress.ip_address(target)
        is_domain = False
    except ValueError:
        pass
    await _panel_show(bot, chat_id, _ipq_progress_text(target, None, step="解析域名…" if is_domain else "准备中…"))
    try:
        ip = await asyncio.wait_for(asyncio.to_thread(_ipq_resolve, target), 10)
    except Exception:
        ip = None
    if not ip:
        await _panel_show(bot, chat_id, "❌ 域名解析失败，请检查后重新发送。", _ipq_markup())
        return
    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
            await _panel_show(bot, chat_id, "❌ 这是内网 / 保留地址，没有公网档案可查。", _ipq_markup())
            return
    except ValueError:
        pass
    if fresh:
        IPQ_CACHE.pop(ip, None)
    try:
        progress = {"names": [], "done": set()}
        task = asyncio.ensure_future(asyncio.to_thread(ip_quality, ip, progress))
        began = time.time()
        shown = None
        while not task.done():
            await asyncio.wait({task}, timeout=1.0)
            if task.done():
                break
            if time.time() - began > 20:
                task.cancel()
                raise asyncio.TimeoutError
            view = _ipq_progress_text(target, ip, progress)
            if view != shown:
                shown = view
                try:
                    await _panel_show(bot, chat_id, view)
                except Exception as err:
                    print("IP 质量进度更新失败：", err)
        q = task.result()
        text = ip_quality_text(target, ip, q)
    except asyncio.TimeoutError:
        text = "❌ 检测超时（数据源都没有响应），请稍后点「重新检测」。"
    except Exception as e:
        import traceback
        traceback.print_exc()
        text = f"❌ 检测出错：{html.escape(type(e).__name__ + ': ' + str(e))[:300]}\n可以把这条报错截图发给管理员排查。"
    await _panel_show(bot, chat_id, text, _ipq_markup())

async def handle_ip_quality(update, context):
    parsed = _parse_ping_target(update.message.text or "")
    if parsed is None:
        await _panel_show(
            update.message.get_bot(), update.message.chat_id,
            "❌ 地址格式不正确，请重新发送 IP 或域名。", _ipq_markup()
        )
        return
    await run_ip_quality(
        update.message.get_bot(), update.message.chat_id,
        update.effective_user.id, context, parsed[0]
    )

async def ip_quality_callback(query, context):
    if query.data == "iq|again":
        target = context.user_data.get("ipq_last")
        if not target:
            await ip_quality_prompt(query, context)
            return
        context.user_data["state"] = "ipq"
        PANELS[query.message.chat_id] = query.message.message_id
        await run_ip_quality(
            query.message.get_bot(), query.message.chat_id,
            query.from_user.id, context, target, fresh=True
        )

NOTE_MAX_LEN = 1000
NOTE_PAGE_CHARS = 3300
NOTE_PAGE_MAX = 10
NOTE_CAT_MAX_LEN = 20
NOTE_CAT_LIMIT = 50
NOTE_TEXT_STATES = {"note_add", "note_cat_new", "note_cat_rename"}

def _note_book(user_id):
    """
    每个用户自己的记事本（存在 nav_data.json 里，备份导出时一起带上）。
    结构：{"cats": [{"name": 分类名, "items": [{"text": ..., "time": ...}, ...]}, ...]}
    旧版本是单页列表，第一次打开时自动迁移到「默认分类」，原有内容不会丢。
    """
    book = data.get("notes")
    if not isinstance(book, dict):
        book = data["notes"] = {}
    mine = book.get(str(user_id))
    if isinstance(mine, list):
        cats = [{"name": "默认分类", "items": mine}] if mine else []
        mine = book[str(user_id)] = {"cats": cats}
        save_data(data)
    elif not isinstance(mine, dict) or not isinstance(mine.get("cats"), list):
        mine = book[str(user_id)] = {"cats": []}
    return mine

def _note_cats(user_id):
    return _note_book(user_id)["cats"]

def _note_cat(user_id, ci):
    cats = _note_cats(user_id)
    if 0 <= ci < len(cats):
        cat = cats[ci]
        if not isinstance(cat.get("items"), list):
            cat["items"] = []
        return cat
    return None

def _note_html(text):
    # 单行用 <code>，点一下就复制；多行用 <pre>，点一下也能复制整段
    if "\n" in text:
        return f"<pre>{html.escape(text)}</pre>"
    return f"<code>{html.escape(text)}</code>"

def _note_pages(notes):
    """按字数分页：每页最多 NOTE_PAGE_MAX 条，文字不超过 NOTE_PAGE_CHARS 字。"""
    pages, page, size = [], [], 0
    for index, note in enumerate(notes):
        length = len(note.get("text", "")) + 20
        if page and (len(page) >= NOTE_PAGE_MAX or size + length > NOTE_PAGE_CHARS):
            pages.append(page)
            page, size = [], 0
        page.append(index)
        size += length
    if page:
        pages.append(page)
    return pages or [[]]

def _note_cat_name_error(user_id, name, skip=None):
    if not name:
        return "❌ 分类名称不能为空，请重新输入。"
    if "\n" in name:
        return "❌ 分类名称只能写一行，请重新输入。"
    if len(name) > NOTE_CAT_MAX_LEN:
        return f"❌ 分类名称太长（{len(name)} 字），最多 {NOTE_CAT_MAX_LEN} 字，请重新输入。"
    for i, cat in enumerate(_note_cats(user_id)):
        if i != skip and cat.get("name") == name:
            return "❌ 已经有同名分类了，换个名字吧。"
    return None

def notes_home_view(user_id):
    """记事本首页：列出自己建的分类。"""
    cats = _note_cats(user_id)
    lines = ["🗒 <b>记事本本</b>", "━━━━━━━━━━━━━━"]
    if not cats:
        lines += [
            "▪ 还没有分类",
            "🆕 先新建一个分类，再进去添加文字",
        ]
    else:
        total = sum(len(c.get("items") or []) for c in cats)
        lines += [
            f"🗂 <b>{len(cats)}</b> 个分类 · <b>{total}</b> 条记录",
            "▪ 点分类进入，点文字即可复制",
        ]
    keyboard = _rows([
        _btn(f"🗂 {c.get('name', '')} · {len(c.get('items') or [])}", f"nt|c|{i}|0")
        for i, c in enumerate(cats)
    ], 2)
    row = [_btn("🆕 新建分类", "nt|newcat")]
    if cats:
        row.append(_btn("🛠 管理分类", "nt|cm"))
    keyboard.append(row)
    keyboard.append([_btn("🏠 返回首页", "home")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)

def notes_manage_view(user_id):
    cats = _note_cats(user_id)
    lines = ["⚙️ <b>管理分类</b>", "━━━━━━━━━━━━━━",
             "✏️ 改名　🗑 删除（会连同分类里的文字一起删掉）"]
    keyboard = []
    for i, c in enumerate(cats):
        keyboard.append([
            _btn(f"✏️ {c.get('name', '')}", f"nt|ren|{i}"),
            _btn("🗑", f"nt|cdel|{i}"),
        ])
    keyboard.append([_btn("⬅ 返回分类列表", "nt|home")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)

def notes_view(user_id, ci, page=0, deleting=False):
    """某个分类里面的文字列表。"""
    cat = _note_cat(user_id, ci)
    if cat is None:
        return notes_home_view(user_id)
    notes = cat["items"]
    pages = _note_pages(notes)
    page = max(0, min(page, len(pages) - 1))
    lines = [f"📁 <b>{html.escape(cat.get('name', ''))}</b>", "━━━━━━━━━━━━━━"]
    if not notes:
        lines.append("这个分类还没有内容，点「➕ 添加」记一条。")
    else:
        tip = "👇 点要删除的编号" if deleting else "👆 点文字即可复制"
        lines.append(f"共 {len(notes)} 条　{tip}\n")
        for index in pages[page]:
            lines.append(f"<b>{index + 1}.</b> {_note_html(notes[index].get('text', ''))}")
    keyboard = []
    if deleting:
        keyboard += _rows([_btn(f"🗑 {i + 1}", f"nt|d|{ci}|{i}|{page}") for i in pages[page]], 5)
        keyboard.append([_btn("✅ 完成", f"nt|c|{ci}|{page}")])
    else:
        row = [_btn("➕ 添加", f"nt|add|{ci}")]
        if notes:
            row.append(_btn("🗑 删除", f"nt|del|{ci}|{page}"))
        keyboard.append(row)
    if len(pages) > 1:
        mode = "del" if deleting else "c"
        nav = []
        if page > 0:
            nav.append(_btn("◀ 上一页", f"nt|{mode}|{ci}|{page - 1}"))
        nav.append(_btn(f"{page + 1}/{len(pages)}", f"nt|{mode}|{ci}|{page}"))
        if page < len(pages) - 1:
            nav.append(_btn("下一页 ▶", f"nt|{mode}|{ci}|{page + 1}"))
        keyboard.append(nav)
    keyboard.append([_btn("⬅ 返回分类列表", "nt|home"), _btn("🏠 首页", "home")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)

async def notes_callback(query, context):
    user_id = query.from_user.id
    parts = (query.data or "").split("|")
    action = parts[1] if len(parts) > 1 else "home"

    def num(i, default=0):
        try:
            return int(parts[i])
        except (IndexError, ValueError):
            return default

    ci = num(2, -1)
    if action == "newcat":
        if len(_note_cats(user_id)) >= NOTE_CAT_LIMIT:
            text, markup = notes_home_view(user_id)
            await edit_page(query, f"❌ 最多只能建 {NOTE_CAT_LIMIT} 个分类。\n\n" + text,
                            parse_mode=ParseMode.HTML, reply_markup=markup)
            return
        context.user_data["state"] = "note_cat_new"
        await edit_page(
            query,
            "📁 <b>新建分类</b>\n━━━━━━━━━━━━━━\n"
            f"直接发送分类名称（一行，最多 {NOTE_CAT_MAX_LEN} 字）。",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[_btn("❌ 取消", "nt|home")]])
        )
        return
    if action == "ren" and _note_cat(user_id, ci) is not None:
        context.user_data["state"] = "note_cat_rename"
        context.user_data["note_cat"] = ci
        name = _note_cat(user_id, ci).get("name", "")
        await edit_page(
            query,
            "✏️ <b>分类改名</b>\n━━━━━━━━━━━━━━\n"
            f"当前名称：<code>{html.escape(name)}</code>\n"
            f"直接发送新名称（一行，最多 {NOTE_CAT_MAX_LEN} 字）。",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[_btn("❌ 取消", "nt|cm")]])
        )
        return
    if action == "add" and _note_cat(user_id, ci) is not None:
        context.user_data["state"] = "note_add"
        context.user_data["note_cat"] = ci
        name = _note_cat(user_id, ci).get("name", "")
        await edit_page(
            query,
            f"📝 <b>添加到「{html.escape(name)}」</b>\n━━━━━━━━━━━━━━\n"
            f"直接发送要保存的文字（可以多行，最多 {NOTE_MAX_LEN} 字）。\n"
            "以 / 开头的命令行也能直接保存。",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[_btn("❌ 取消", f"nt|c|{ci}|0")]])
        )
        return
    context.user_data.pop("state", None)
    context.user_data.pop("note_cat", None)
    if action == "c":
        text, markup = notes_view(user_id, ci, num(3))
    elif action == "del":
        text, markup = notes_view(user_id, ci, num(3), deleting=True)
    elif action == "d":
        cat = _note_cat(user_id, ci)
        if cat is not None:
            index = num(3, -1)
            if 0 <= index < len(cat["items"]):
                cat["items"].pop(index)
                save_data(data)
            text, markup = notes_view(user_id, ci, num(4), deleting=bool(cat["items"]))
        else:
            text, markup = notes_home_view(user_id)
    elif action == "cm":
        text, markup = notes_manage_view(user_id)
    elif action == "cdel" and _note_cat(user_id, ci) is not None:
        cat = _note_cat(user_id, ci)
        text = (
            "🗑 <b>删除分类</b>\n━━━━━━━━━━━━━━\n"
            f"确定删除「{html.escape(cat.get('name', ''))}」吗？\n"
            f"里面的 {len(cat['items'])} 条文字会一起删除，无法恢复。"
        )
        markup = InlineKeyboardMarkup([[
            _btn("✅ 确定删除", f"nt|cdelok|{ci}"),
            _btn("❌ 取消", "nt|cm"),
        ]])
    elif action == "cdelok":
        cats = _note_cats(user_id)
        if 0 <= ci < len(cats):
            cats.pop(ci)
            save_data(data)
        text, markup = notes_manage_view(user_id) if cats else notes_home_view(user_id)
    else:
        text, markup = notes_home_view(user_id)
    await edit_page(query, text, parse_mode=ParseMode.HTML, reply_markup=markup)

async def handle_note_add(update, context):
    user_id = update.effective_user.id
    bot = update.message.get_bot()
    chat_id = update.message.chat_id
    state = context.user_data.get("state")
    text = (update.message.text or "").strip()
    if not text:
        return

    if state in ("note_cat_new", "note_cat_rename"):
        renaming = state == "note_cat_rename"
        ci = context.user_data.get("note_cat", -1)
        if renaming and _note_cat(user_id, ci) is None:
            context.user_data.pop("state", None)
            view, markup = notes_home_view(user_id)
            await _panel_show(bot, chat_id, view, markup)
            return
        error = _note_cat_name_error(user_id, text, skip=ci if renaming else None)
        if error:
            back = "nt|cm" if renaming else "nt|home"
            await _panel_show(bot, chat_id, error, InlineKeyboardMarkup([[_btn("❌ 取消", back)]]))
            return
        context.user_data.pop("state", None)
        context.user_data.pop("note_cat", None)
        if renaming:
            _note_cat(user_id, ci)["name"] = text
            save_data(data)
            view, markup = notes_manage_view(user_id)
            await _panel_show(bot, chat_id, "✅ 已改名\n\n" + view, markup)
        else:
            cats = _note_cats(user_id)
            cats.append({"name": text, "items": []})
            save_data(data)
            view, markup = notes_view(user_id, len(cats) - 1)
            await _panel_show(bot, chat_id, "✅ 分类已创建\n\n" + view, markup)
        return

    ci = context.user_data.get("note_cat", -1)
    cat = _note_cat(user_id, ci)
    if cat is None:
        context.user_data.pop("state", None)
        view, markup = notes_home_view(user_id)
        await _panel_show(bot, chat_id, "❌ 分类不存在了，请重新选择。\n\n" + view, markup)
        return
    if len(text) > NOTE_MAX_LEN:
        await _panel_show(
            bot, chat_id,
            f"❌ 太长了（{len(text)} 字），每条最多 {NOTE_MAX_LEN} 字，请分开发送。",
            InlineKeyboardMarkup([[_btn("❌ 取消", f"nt|c|{ci}|0")]])
        )
        return
    cat["items"].append({"text": text, "time": int(time.time())})
    save_data(data)
    context.user_data.pop("state", None)
    context.user_data.pop("note_cat", None)
    pages = _note_pages(cat["items"])
    view, markup = notes_view(user_id, ci, len(pages) - 1)
    await _panel_show(bot, chat_id, "✅ 已保存\n\n" + view, markup)

async def note_command_capture(update, context):
    """
    以 / 开头的文字会被 Telegram 当成命令，原来的文字处理器收不到，所以存不进记事本。
    这里在命令处理器之前先看一眼：正在等待记事本输入时，就把它当普通文字保存，
    并拦住后面的 /start 等命令处理器；其他时候不管，命令照常执行。
    """
    if context.user_data.get("state") in NOTE_TEXT_STATES:
        await text_handler(update, context)
        raise ApplicationHandlerStop

# ===== 最近记录（四个功能共用一份）=====
# 记录存在全球 PING 的测试记录里；路由追踪 / 端口扫描 / IP 质量检测测过的目标也会加进去。
# 这三个功能的记录页只显示 IP / 域名（去掉端口、去重），最多 RECENT_SHOW 个。
RECENT_SHOW = 10
RECENT_TOOLS = {
    "rt": ("🧭", "点一下直接追踪"),
    "ps": ("📡", "点一下按原来的端口重新扫描"),
    "iq": ("🔐", "点一下直接检测"),
}

def recent_target_add(user_id, host):
    """记一条（已有的会移到最前面）。"""
    if not host:
        return
    try:
        gping_recent_add(user_id, host, None, "ICMP")
    except Exception as e:
        print("保存最近记录失败：", e)

def recent_targets(user_id):
    hosts = []
    for item in gping_recent_list(user_id):
        host = item.get("host")
        if host and host not in hosts:
            hosts.append(host)
        if len(hosts) >= RECENT_SHOW:
            break
    return hosts

# ----- 端口扫描单独的记录：连同输入的端口（范围 / 指定端口 / ALL）一起记 -----
SCAN_RECENT_MAX = 10

def _scan_recent_book():
    book = data.get("scan_recent")
    if not isinstance(book, dict):
        book = data["scan_recent"] = {}
    return book

def scan_recent_list(user_id):
    items = _scan_recent_book().get(str(user_id))
    return [i for i in items if isinstance(i, dict) and i.get("host")] if isinstance(items, list) else []

def scan_recent_add(user_id, host, spec):
    """记一条端口扫描（同一个地址 + 同样的端口只留一条，移到最前面）。spec 为空表示常用端口。"""
    if not host:
        return
    try:
        spec = re.sub(r"\s+", " ", (spec or "").strip())
        items = [i for i in scan_recent_list(user_id)
                 if not (i["host"] == host and (i.get("spec") or "") == spec)]
        items.insert(0, {"host": host, "spec": spec})
        _scan_recent_book()[str(user_id)] = items[:SCAN_RECENT_MAX]
        save_data(data)
    except Exception as e:
        print("保存端口扫描记录失败：", e)

def _scan_spec_label(spec):
    if not spec:
        return "常用"
    if spec.lower() in ("all", "全部", "全端口"):
        return "ALL"
    return spec

def recent_entry_rows(user_id, tool):
    """输入页上的「🗃 最近记录」按钮（没有记录时不显示）。"""
    has = scan_recent_list(user_id) if tool == "ps" else recent_targets(user_id)
    return [[_btn("🗃 最近记录", f"rec|{tool}")]] if has else []

def recent_page(user_id, tool):
    _icon, tip = RECENT_TOOLS[tool]
    buttons = []
    if tool == "ps":
        hosts = scan_recent_list(user_id)
        labels = _left_labels(
            [f"{item['host']} · {_scan_spec_label(item.get('spec'))}" for item in hosts], cap=20000
        )
        for i, label in enumerate(labels):
            buttons.append(_btn(label, f"rec|{tool}|{i}"))
        keyboard = [[b] for b in buttons] + [
            [_btn("🧹 清空记录", f"rec|{tool}|clr")],
            [_btn("⬅️ 返回", "port_scan")],
        ]
        text = "🗃 <b>最近记录</b>\n━━━━━━━━━━━━━━\n" + (f"▪ {tip}" if hosts else "▪ 还没有记录")
        return text, InlineKeyboardMarkup(keyboard)
    hosts = recent_targets(user_id)
    for i, label in enumerate(_left_labels(hosts)):
        buttons.append(_btn(label, f"rec|{tool}|{i}"))
    back = {"rt": "route_trace", "ps": "port_scan", "iq": "ipq"}[tool]
    keyboard = _left_rows(buttons, _btn("🧹 清空记录", f"rec|{tool}|clr")) + [
        [_btn("⬅️ 返回", back)],
    ]
    text = "🗃 <b>最近记录</b>\n━━━━━━━━━━━━━━\n" + (f"▪ {tip}" if hosts else "▪ 还没有记录")
    return text, InlineKeyboardMarkup(keyboard)

async def recent_callback(query, context):
    parts = query.data.split("|")
    tool = parts[1] if len(parts) > 1 else ""
    if tool not in RECENT_TOOLS:
        return
    user_id = query.from_user.id
    bot = query.message.get_bot()
    chat_id = query.message.chat_id
    if len(parts) == 2:
        context.user_data["state"] = {"rt": "route_trace", "ps": "port_scan", "iq": "ipq"}[tool]
        text, markup = recent_page(user_id, tool)
        await edit_page(query, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return
    if parts[2] == "clr":
        if tool == "ps":
            # 端口扫描只清自己的记录，不影响其他功能
            if _scan_recent_book().pop(str(user_id), None) is not None:
                save_data(data)
        elif GPING_RECENT.pop(str(user_id), None) is not None:
            _save_gping_recent()
        try:
            await query.answer("已清空最近记录")
        except Exception:
            pass
        text, markup = recent_page(user_id, tool)
        await edit_page(query, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return
    if tool == "ps":
        try:
            item = scan_recent_list(user_id)[int(parts[2])]
        except (ValueError, IndexError):
            text, markup = recent_page(user_id, tool)
            await edit_page(query, text, parse_mode=ParseMode.HTML, reply_markup=markup)
            return
        spec = item.get("spec") or ""
        ports, error = _parse_port_spec(spec) if spec else (list(COMMON_PORTS), None)
        if error:
            ports = list(COMMON_PORTS)
        PANELS[chat_id] = query.message.message_id
        context.user_data["state"] = "port_scan"
        await start_port_scan(bot, chat_id, user_id, context, item["host"], ports, spec)
        return
    hosts = recent_targets(user_id)
    try:
        host = hosts[int(parts[2])]
    except (ValueError, IndexError):
        text, markup = recent_page(user_id, tool)
        await edit_page(query, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return
    PANELS[chat_id] = query.message.message_id
    if tool == "rt":
        context.user_data["state"] = "route_trace"
        context.user_data["route_target"] = host
        recent_target_add(user_id, host)
        await edit_page(query, route_select_text(host), parse_mode=ParseMode.HTML,
                        reply_markup=route_keyboard())
    elif tool == "iq":
        context.user_data["state"] = "ipq"
        await run_ip_quality(bot, chat_id, user_id, context, host)
    elif tool == "ps":
        context.user_data["state"] = "port_scan"
        await start_port_scan(bot, chat_id, user_id, context, host, list(COMMON_PORTS))

async def start_port_scan(bot, chat_id, user_id, context, host, ports, spec=""):
    """从最近记录直接开始扫描（不经过发消息）。"""
    running = SCAN_JOBS.get(chat_id)
    if running and not running["task"].done():
        return
    limited = rate_check(user_id, 1)
    if limited:
        await _panel_show(bot, chat_id, "⏳ " + html.escape(limited), _scan_finish_keyboard())
        return
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        ip = infos[0][4][0]
    except Exception:
        await _panel_show(bot, chat_id, "❌ 无法解析这个地址，请检查后重新发送。", _scan_finish_keyboard())
        return
    recent_target_add(user_id, host)
    scan_recent_add(user_id, host, spec)
    label = host if host == ip else f"{host} ({ip})"
    await _panel_show(bot, chat_id, _scan_progress_text(label, 0, len(ports), [], 0),
                      _scan_running_keyboard())
    job = {"cancel": False}
    job["task"] = asyncio.create_task(
        _run_port_scan(bot, chat_id, context.user_data, label, ip, ports, job)
    )
    SCAN_JOBS[chat_id] = job

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    user_id = query.from_user.id
    try:
        await query.answer()
    except Exception:
        pass
    value = query.data
    if value != "status":
        stop_status_refresh(query.message.chat_id)  # 离开主机状态页面就停止自动刷新
    if is_tool_callback(value) and not can_use_tools(user_id):
        context.user_data.clear()
        await edit_page(
            query,
            no_permission_text(user_id),
            parse_mode=ParseMode.HTML,
            reply_markup=_home_markup()
        )
        return
    if value == "status":
        if is_admin(user_id):
            await show_server_status(query)
        return
    if value == "ipq":
        context.user_data.clear()
        await ip_quality_prompt(query, context)
        return
    if value == "notes" or value.startswith("nt|"):
        if not can_use_tools(user_id):
            await edit_page(query, no_permission_text(user_id),
                            parse_mode=ParseMode.HTML, reply_markup=_home_markup())
            return
        await notes_callback(query, context)
        return
    if value.startswith("rec|"):
        await recent_callback(query, context)
        return
    if value.startswith("iq|"):
        await ip_quality_callback(query, context)
        return
    if value.startswith("upd|"):
        await update_callback(query, context)
        return
    if value.startswith("gp|"):
        await global_ping_switch(query, context)
        return
    if value.startswith("rtx|"):
        action = value.split("|", 1)[1]
        if action == "cmp":
            await route_compare(query, context)
        return
    if value.startswith("users|del|"):
        if is_admin(user_id):
            _allowed_users().pop(value.split("|", 2)[2], None)
            save_data(data)
            text, markup = _users_page()
            await edit_page(query, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return
    if value == "home":
        context.user_data.clear()
        await show_main_menu(
            update,
            context
        )
        return
    if value == "global_ping":
        context.user_data.clear()
        await global_ping(
            query,
            context
        )
        return
    if value == "route_trace":
        context.user_data.clear()
        await route_trace(
            query,
            context
        )
        return
    if value.startswith("rt|"):
        await route_run(
            query,
            context
        )
        return
    if value == "port_scan":
        context.user_data.clear()
        await port_scan(
            query,
            context
        )
        return
    if value == "portscan_cancel":
        job = SCAN_JOBS.get(query.message.chat_id)
        if job:
            job["cancel"] = True
        return
    if value.startswith("admin|"):
        if not is_admin(user_id):
            await query.answer(
                "❌ 没有管理员权限。",
                show_alert=True
            )
            return
        action = value.split(
            "|",
            1
        )[1]
        if action == "back":
            context.user_data.clear()
            await show_admin_menu(
                query=query
            )
        elif action == "cancel":
            context.user_data.clear()
            await show_admin_menu(
                query=query
            )
        elif action == "add_category":
            await admin_add_category(
                query,
                context
            )
        elif action == "add_site":
            context.user_data.clear()
            await admin_add_site(
                query,
                context
            )
        elif action == "edit_site":
            context.user_data.clear()
            await admin_edit_site(
                query
            )
        elif action == "delete_site":
            context.user_data.clear()
            await admin_delete_site(
                query
            )
        elif action == "delete_category":
            context.user_data.clear()
            await admin_delete_category(
                query
            )
        elif action == "rename_category":
            context.user_data.clear()
            await admin_rename_category(
                query
            )
        elif action == "list":
            await admin_list(
                query
            )
        elif action == "quota":
            context.user_data.clear()
            text = await asyncio.to_thread(gp_quota_text)
            await edit_page(query, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
                [_btn("🔄 刷新", "admin|quota"), _btn("⬅️ 返回后台", "admin|back")],
            ]))
        elif action == "users":
            context.user_data.clear()
            text, markup = _users_page()
            await edit_page(query, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        elif action == "adduser":
            context.user_data["state"] = "allow_user"
            await edit_page(
                query,
                "➕ <b>添加授权用户</b>\n\n"
                "请发送：<code>用户ID 备注</code>，例如 <code>123456789 小王</code>\n"
                "对方可以给机器人发送 /myid 查看自己的 ID。",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[_btn("❌ 取消", "admin|users")]])
            )
        elif action == "export":
            await admin_export(query)
        elif action == "import":
            await admin_import_prompt(query, context)
        return
    if value.startswith("addsitecat|"):
        if not is_admin(user_id):
            return
        await add_site_category(
            query,
            context
        )
        return
    if value.startswith("editcat|"):
        if not is_admin(user_id):
            return
        await edit_category(
            query
        )
        return
    if value.startswith("edit|"):
        if not is_admin(user_id):
            return
        await edit_site_menu(
            query,
            context
        )
        return
    if value.startswith("editaction|"):
        if not is_admin(user_id):
            return
        await edit_action(
            query,
            context
        )
        return
    if value.startswith("delcat|"):
        if not is_admin(user_id):
            return
        await delete_category_select(
            query
        )
        return
    if value.startswith("delete|"):
        if not is_admin(user_id):
            return
        await delete_site_confirm(
            query,
            context
        )
        return
    if value.startswith("renamecat|"):
        if not is_admin(user_id):
            return
        await rename_category_select(
            query,
            context
        )
        return
    if value.startswith("delcategory|"):
        if not is_admin(user_id):
            return
        await delete_category_confirm(
            query
        )
        return
    if value == "confirm|add_site":
        if not is_admin(user_id):
            return
        await confirm_add_site(
            query,
            context
        )
        return
    if value == "confirm|delete_site":
        if not is_admin(user_id):
            return
        await confirm_delete_site(
            query,
            context
        )
        return
    if value.startswith("confirmcategory|"):
        if not is_admin(user_id):
            return
        await confirm_delete_category(
            query
        )
        return
    if value.startswith("cat|"):
        category = value.split(
            "|",
            1
        )[1]
        if category not in data["categories"]:
            await query.answer(
                "分类不存在。",
                show_alert=True
            )
            return
        await show_category(
            query,
            category
        )
        return
    if value.startswith("site|"):
        parts = value.split("|")
        if len(parts) != 3:
            return
        category = parts[1]
        try:
            index = int(parts[2])
        except ValueError:
            return
        await show_site(
            query,
            category,
            index
        )
        return

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data.clear()
    await show_home(
        update.message.get_bot(), update.message.chat_id,
        update.effective_user.id, welcome=True, fresh=True
    )
    await _try_delete(update.message)

async def menu_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data.clear()
    await show_main_menu(
        update,
        context
    )
    await _try_delete(update.message)

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(
        update.effective_user.id
    ):
        await update.message.reply_text(
            "❌ 你没有管理员权限。"
        )
        return
    context.user_data.clear()
    await show_admin_menu(
        message=update.message
    )
    await _try_delete(update.message)

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await _try_delete(update.message)
    stop_status_refresh(update.message.chat_id)
    user_id = update.effective_user.id
    state = context.user_data.get(
        "state"
    )
    if not state:
        await show_home(update.message.get_bot(), update.message.chat_id, user_id, fresh=True)
        return
    if state in TOOL_STATES:
        if not can_use_tools(user_id):
            return
    elif not is_admin(user_id):
        return
    if state == "ipq":
        await handle_ip_quality(update, context)
        return
    if state in NOTE_TEXT_STATES:
        await handle_note_add(update, context)
        return
    if state == "gping_port":
        await handle_global_ping_port(update, context)
        return
    if state == "allow_user":
        await handle_allow_user(update, context)
        return
    if state == "port_scan":
        await handle_port_scan(
            update,
            context
        )
        return
    if state == "route_trace":
        await handle_route_target(
            update,
            context
        )
        return
    if state == "global_ping":
        await handle_global_ping(
            update,
            context
        )
        return
    if state == "add_category":
        category = update.message.text.strip()
        if not category:
            await reply_panel(
                update, context,
                "❌ 分类名称不能为空，请重新输入。"
            )
            return
        bad = _category_name_error(category)
        if bad:
            await reply_panel(update, context, bad)
            return
        if category in data["categories"]:
            await reply_panel(
                update, context,
                "❌ 这个分类已经存在，请换一个名称。"
            )
            return
        context.user_data["new_category_name"] = category
        await reply_panel(
            update, context,
            "📂 <b>确认添加分类？</b>\n\n"
            f"分类名称：<b>{html.escape(category)}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ 确认添加",
                        callback_data="confirmcategoryadd"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ 取消",
                        callback_data="admin|cancel"
                    )
                ]
            ])
        )
        context.user_data["state"] = "confirm_add_category"
        return
    if state == "rename_category":
        await handle_rename_category(
            update,
            context
        )
        return
    if state == "add_site_name":
        await add_site_name(
            update,
            context
        )
        return
    if state == "add_site_url":
        await add_site_url(
            update,
            context
        )
        return
    if state == "edit_name":
        await handle_edit_name(
            update,
            context
        )
        return
    if state == "edit_url":
        await handle_edit_url(
            update,
            context
        )
        return

async def confirm_add_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    if not is_admin(
        query.from_user.id
    ):
        await query.answer(
            "没有权限。",
            show_alert=True
        )
        return
    category = context.user_data.get(
        "new_category_name"
    )
    if not category:
        await query.answer(
            "操作已经失效。",
            show_alert=True
        )
        return
    data["categories"][category] = []
    save_data(data)
    context.user_data.clear()
    await query.answer(
        "添加成功！"
    )
    await edit_page(query,
        "✅ <b>分类添加成功！</b>\n\n"
        f"📂 {html.escape(category)}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➕ 继续添加分类",
                    callback_data="admin|add_category"
                )
            ],
            [
                InlineKeyboardButton(
                    "➕ 添加网址",
                    callback_data="admin|add_site"
                )
            ],
            [
                InlineKeyboardButton(
                    "⚙️ 管理后台",
                    callback_data="admin|back"
                )
            ]
        ])
    )

async def error_handler(
    update,
    context
):
    text = str(context.error)
    if "Query is too old" in text or "query id is invalid" in text or "not modified" in text:
        return
    print(
        "ERROR:",
        context.error
    )
BUSY_TOAST = "⏳ 上一个测试还没完成，请稍等"

def _update_is_long(update):
    """这条更新会不会跑比较久（检测类按钮、发送的 IP / 域名等文字）。"""
    query = getattr(update, "callback_query", None)
    if query is not None:
        return is_tool_callback(query.data or "")
    message = getattr(update, "message", None)
    if message is not None and message.text:
        return not message.text.startswith("/")
    return False

def _make_update_processor():
    """
    python-telegram-bot 20.4 以上才有 BaseUpdateProcessor；
    版本太旧就返回 None，退回原来的排队模式（功能不受影响）。
    """
    try:
        from telegram.ext import BaseUpdateProcessor
    except ImportError:
        print("python-telegram-bot 版本较旧，不开并发，按顺序处理。")
        return None
    class ChatUpdateProcessor(BaseUpdateProcessor):
        """
        不同聊天（不同的人）同时处理，互不等待；
        同一聊天还是一条一条来，面板、输入状态不会被打乱。
        同一聊天的检测还没跑完时再点按钮：直接弹提示，不排队、不重复花额度。
        """
        def __init__(self):
            super().__init__(max_concurrent_updates=100000)
            self.chats = {}
            self.slots = asyncio.Semaphore(64)
        async def do_process_update(self, update, coroutine):
            chat = getattr(update, "effective_chat", None)
            if chat is None:
                await coroutine
                return
            state = self.chats.setdefault(chat.id, {"lock": asyncio.Lock(), "long": False, "n": 0})
            query = getattr(update, "callback_query", None)
            if query is not None and state["lock"].locked() and state["long"]:
                close = getattr(coroutine, "close", None)
                if close:
                    close()
                try:
                    await query.answer(BUSY_TOAST)
                except Exception as e:
                    print("回复忙碌提示失败：", e)
                return
            state["n"] += 1
            try:
                async with state["lock"]:
                    state["long"] = _update_is_long(update)
                    try:
                        async with self.slots:
                            await coroutine
                    finally:
                        state["long"] = False
            finally:
                state["n"] -= 1
                if state["n"] <= 0 and self.chats.get(chat.id) is state:
                    del self.chats[chat.id]
        async def initialize(self):
            pass
        async def shutdown(self):
            pass
    return ChatUpdateProcessor()

async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "开始使用"),
        BotCommand("menu", "主菜单"),
        BotCommand("myid", "查看我的 ID"),
        BotCommand("admin", "管理后台"),
    ])
    from concurrent.futures import ThreadPoolExecutor
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=64))
    application.bot_data["gping_warm"] = asyncio.create_task(asyncio.to_thread(
        lambda: (_online_cn_probes(), ping_placeholder_map())
    ))
    application.bot_data["ip2region"] = asyncio.create_task(asyncio.to_thread(_ip2region_load))
    await update_notify_after_boot(application)

def main():
    if not BOT_TOKEN:
        print("")
        print("========================================")
        print("错误：没有找到 Bot Token")
        print(f"请编辑 {CONFIG_FILE}，填写 bot_token（和 admin_ids），")
        print("或者设置环境变量 BOT_TOKEN 后再启动。")
        print("========================================")
        print("")
        return
    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
    )
    processor = _make_update_processor()
    if processor is not None:
        builder = builder.concurrent_updates(processor)
    application = builder.build()
    application.add_handler(TypeHandler(Update, access_gate), group=-2)
    # 记事本：以 / 开头的文字（命令行等）也能保存，必须排在命令处理器前面
    application.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE & filters.TEXT & filters.COMMAND, note_command_capture),
        group=-1
    )
    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )
    application.add_handler(
        CommandHandler(
            "menu",
            menu_command
        )
    )
    application.add_handler(
        CommandHandler(
            "admin",
            admin_command
        )
    )
    application.add_handler(CommandHandler("myid", myid_command))
    application.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.Document.ALL, document_handler))
    application.add_handler(
        CallbackQueryHandler(
            confirm_add_category,
            pattern=r"^confirmcategoryadd$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )
    application.add_error_handler(
        error_handler
    )
    print("")
    print("========================================")
    print("       功能导航 Telegram Bot")
    print("========================================")
    print("机器人启动成功")
    print("========================================")
    print("")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )
if __name__ == "__main__":
    run_with_rollback(main)
