#!/usr/bin/env python3
"""
Callback receiver designed for tg-bot-gl.sh installer.

Installer integration:
- Reads BOT_TOKEN from the install directory's .env (systemd EnvironmentFile).
- Reads ADMIN_IDS from the same .env as the notification recipient.
- Does not require CHAT_ID or WEBHOOK_SECRET to be configured by the installer.
- Generates and persists a callback secret beside this script.

Expected systemd setup from installer:
    WorkingDirectory=${INSTALL_DIR}
    EnvironmentFile=-${INSTALL_DIR}/.env
    ExecStart=/usr/bin/python3 -u app.py
"""
import hmac
import json
import logging
import os
import secrets
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_IDS_RAW = (
    os.environ.get("ADMIN_IDS")
    or os.environ.get("ADMIN_ID")
    or os.environ.get("CHAT_ID")
    or ""
)
HOST = "127.0.0.1"
PORT = int(os.environ.get("CALLBACK_PORT", "8787"))
MAX_BODY = 64 * 1024
APP_DIR = Path(__file__).resolve().parent
SECRET_FILE = APP_DIR / ".webhook_secret"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

def parse_recipients(raw):
    # Accept comma/space/semicolon separated IDs, and Python-list-like text.
    cleaned = raw.replace("[", " ").replace("]", " ").replace("'", " ").replace('"', " ")
    values = cleaned.replace(",", " ").replace(";", " ").split()
    return list(dict.fromkeys(v for v in values if v.lstrip("-").isdigit()))

def telegram_api(method, fields=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(fields or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API {method} failed")
    return result["result"]

def load_or_create_secret():
    try:
        value = SECRET_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    value = secrets.token_hex(24)
    SECRET_FILE.write_text(value + "\n", encoding="utf-8")
    try:
        SECRET_FILE.chmod(0o600)
    except OSError:
        pass
    return value

def redact_sensitive(value):
    sensitive = (
        "password", "passwd", "token", "secret", "otp", "verification",
        "验证码", "密码", "令牌", "口令",
    )
    if isinstance(value, dict):
        return {
            str(k): (
                "[已隐藏]"
                if any(word in str(k).lower() for word in sensitive)
                else redact_sensitive(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(v) for v in value[:50]]
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:200]

def send_telegram(message):
    for chat_id in RECIPIENTS:
        telegram_api("sendMessage", {"chat_id": chat_id, "text": message[:4000]})

class CallbackHandler(BaseHTTPRequestHandler):
    webhook_secret = None

    def log_message(self, fmt, *args):
        logging.info("HTTP %s %s", self.command, urllib.parse.urlsplit(self.path).path)

    def reply(self, status, message):
        body = json.dumps(
            {"ok": status == 200, "message": message},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(404, "Not found")

    def do_POST(self):
        expected = "/callback/" + self.webhook_secret
        actual = urllib.parse.urlsplit(self.path).path
        if not hmac.compare_digest(actual, expected):
            self.reply(404, "Not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.reply(400, "Invalid content length")
            return
        if length <= 0:
            self.reply(400, "Empty request body")
            return
        if length > MAX_BODY:
            self.reply(413, "Request body too large")
            return

        try:
            raw = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "").lower()
            if "application/json" in content_type:
                payload = json.loads(raw.decode("utf-8"))
            elif "application/x-www-form-urlencoded" in content_type:
                payload = urllib.parse.parse_qs(
                    raw.decode("utf-8"), keep_blank_values=True
                )
            else:
                self.reply(415, "Unsupported content type")
                return
            if not isinstance(payload, (dict, list)):
                self.reply(400, "Expected JSON object, array, or form")
                return

            safe_payload = redact_sensitive(payload)
            message = "收到 API 回调：\n" + json.dumps(
                safe_payload, ensure_ascii=False, indent=2
            )
            send_telegram(message)
            self.reply(200, "Accepted")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.reply(400, "Invalid payload encoding or JSON")
        except Exception:
            logging.exception("Callback processing failed")
            self.reply(502, "Forwarding failed")

def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "缺少 BOT_TOKEN：请通过安装脚本把 BOT_TOKEN 写入项目 .env。"
        )
    global RECIPIENTS
    RECIPIENTS = parse_recipients(ADMIN_IDS_RAW)
    if not RECIPIENTS:
        raise SystemExit(
            "缺少有效的 ADMIN_IDS：请通过安装脚本把管理员 ID 写入项目 .env。"
        )

    telegram_api("getMe")
    secret = load_or_create_secret()
    CallbackHandler.webhook_secret = secret
    server = ThreadingHTTPServer((HOST, PORT), CallbackHandler)
    logging.info("Callback receiver listening on %s:%s", HOST, PORT)
    logging.info("Callback path: /callback/%s", secret)
    try:
        server.serve_forever()
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
