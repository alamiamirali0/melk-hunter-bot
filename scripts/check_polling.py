# -*- coding: utf-8 -*-
"""بررسی می‌کند که ربات واقعاً روی سرور در حال poll کردن است."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ.get("MELKBOT_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("MELKBOT_TOKEN نیست")


def call(method, **params):
    d = urllib.parse.urlencode(params).encode() if params else None
    return json.load(urllib.request.urlopen(
        f"https://api.telegram.org/bot{TOKEN}/{method}", data=d, timeout=20))


info = call("getWebhookInfo")["result"]
print(f"📬 صف تلگرام: {info['pending_update_count']} پیام | "
      f"خطای آخر: {info.get('last_error_message') or '—'}")

try:
    call("getUpdates", timeout=0)
    print("❌ هیچ نسخه‌ای در حال poll نیست — ربات خوابیده!")
except urllib.error.HTTPError as e:
    body = e.read().decode(errors="ignore")
    if "Conflict" in body:
        print("✅ ربات در حال poll کردن است (تداخل مورد انتظار با همین بررسی)")
    else:
        print(f"⚠️ پاسخ غیرمنتظره: {e.code} {body[:200]}")
print("🔚 بررسی تمام")
