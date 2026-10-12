#!/usr/bin/env python3
"""
Telegram API callback receiver.
Configure the three values in the CONFIG section below, or provide them through
environment variables. This is a callback receiver, not a polling Telegram bot.
"""
import hmac
import json
import logging
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ===================== CONFIG =====================
# Fill these in here if you do not want to configure systemd EnvironmentFile.
BOT_TOKEN = "PUT_NEW_BOT_TOKEN_HERE"
CHAT_ID = "PUT_CHAT_ID_HERE"
WEBHOOK_SECRET = "PUT_48_CHAR_RANDOM_SECRET_HERE"

# Environment variables, if present, override the values above.
BOT_TOKEN = os.environ.get("BOT_TOKEN", BOT_TOKEN)
CHAT_ID = os.environ.get("CHAT_ID", CHAT_ID)
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", WEBHOOK_SECRET)

HOST = "127.0.0.1"
PORT = 8787
MAX_BODY = 64 * 1024
# ==================================================

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

def validate_config():
    missing = []
    if not BOT_TOKEN or BOT_TOKEN == "PUT_NEW_BOT_TOKEN_HERE":
        missing.append("BOT_TOKEN")
    if not CHAT_ID or CHAT_ID == "PUT_CHAT_ID_HERE":
        missing.append("CHAT_ID")
    if not WEBHOOK_SECRET or WEBHOOK_SECRET == "PUT_48_CHAR_RANDOM_SECRET_HERE":
        missing.append("WEBHOOK_SECRET")
    if missing:
        raise SystemExit(
            "Configuration missing: " + ", ".join(missing)
            + ". Set these values in the CONFIG section of app.py "
              "or provide them as environment variables."
        )

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message[:4000],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError("Telegram API rejected the message")

class CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Do not log request bodies or secret-bearing query strings.
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
        expected_path = "/callback/" + WEBHOOK_SECRET
        actual_path = urllib.parse.urlsplit(self.path).path
        if not hmac.compare_digest(actual_path, expected_path):
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

            # Only test/non-sensitive payloads should be used until the
            # provider's signature and sensitive-field rules are configured.
            message = "收到 API 回调：\n" + json.dumps(
                payload, ensure_ascii=False, indent=2
            )
            send_telegram(message)
            self.reply(200, "Accepted")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.reply(400, "Invalid payload encoding or JSON")
        except Exception:
            logging.exception("Callback processing failed")
            self.reply(502, "Forwarding failed")

if __name__ == "__main__":
    validate_config()
    server = ThreadingHTTPServer((HOST, PORT), CallbackHandler)
    logging.info("Callback receiver listening on %s:%s", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
