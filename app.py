#!/usr/bin/env python3
"""
Telegram API callback receiver.

- Receives JSON or application/x-www-form-urlencoded POST requests.
- Forwards callback payloads to a dedicated Telegram bot/chat.
- Listens only on 127.0.0.1:8787; expose it through your HTTPS reverse proxy.
- Configure via /etc/tg-callback.env (see README instructions in chat).

Security notes:
- Protect the callback URL with a long random secret.
- This generic receiver does not validate provider-specific webhook signatures.
- Do not send passwords, one-time codes, access tokens, or other sensitive data to Telegram.
"""
import hmac
import json
import logging
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
MAX_BODY = 64 * 1024


def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message[:4000],
    }).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError("Telegram API rejected the message")


class CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Avoid logging request bodies or secret-bearing query strings.
        logging.info("HTTP %s %s", self.command, urllib.parse.urlsplit(self.path).path)

    def reply(self, status: int, message: str) -> None:
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
                    raw.decode("utf-8"),
                    keep_blank_values=True,
                )
            else:
                self.reply(415, "Unsupported content type")
                return

            if not isinstance(payload, (dict, list)):
                self.reply(400, "Expected a JSON object, array, or form")
                return

            # This generic receiver forwards the supplied payload as-is.
            # Use only with test/non-sensitive callback data until provider
            # signature validation and field filtering are configured.
            message = "收到 API 回调：\n" + json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
            send_telegram(message)
            self.reply(200, "Accepted")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.reply(400, "Invalid payload encoding or JSON")
        except Exception:
            logging.exception("Callback processing failed")
            self.reply(502, "Forwarding failed")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8787), CallbackHandler)
    logging.info("Telegram callback receiver listening on 127.0.0.1:8787")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
