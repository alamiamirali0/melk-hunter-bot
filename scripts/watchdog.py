# -*- coding: utf-8 -*-
"""
نگهبان ربات ملک هانتر — اگر ربات به هر دلیلی خاموش شده باشد، روشنش می‌کند.

هر ۱۰ دقیقه بیدار می‌شود، «ضربان» ربات را از برنچ status می‌خواند:
  • اگر ضربان تازه باشد و polling سالم باشد → هیچ کاری نمی‌کند.
  • اگر ربات خاموش/گیرکرده باشد و اجرایی هم در صف نباشد → اجرای تازه می‌فرستد
    و به مالک در تلگرام خبر می‌دهد.
این لایه، مستقل از زمان‌بندی گیت‌هاب است؛ همان چیزی که باعث شد امروز ربات خاموش بماند.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
WORKFLOW = "relay.yml"
STALE_SEC = int(os.environ.get("STALE_SEC", "600"))   # بیش از این مدت بی‌خبری = خاموش


def call(url: str, tok: str, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "melk-hunter-watchdog",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:  # noqa: BLE001
            return e.code, {"message": body[:200]}
    except Exception as e:  # noqa: BLE001
        return 0, {"message": str(e)}


def notify(text: str, tok: str) -> None:
    bt = (os.environ.get("MELKBOT_TOKEN") or "").strip()
    owner = (os.environ.get("OWNER_ID") or "").strip()
    if not bt or not owner.isdigit():
        return
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bt}/sendMessage",
            data=json.dumps({"chat_id": int(owner), "text": text, "parse_mode": "HTML"}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:  # noqa: BLE001
        print("خبر تلگرام نرفت:", e)


def heartbeat(tok: str, rp: str) -> tuple[float, bool, str]:
    """(سن ضربان به ثانیه، polling سالم؟، توضیح) — از Contents API تا کش CDN اثر نگذارد."""
    st, res = call(f"{API}/repos/{rp}/contents/status.json?ref=status", tok)
    if st != 200 or "content" not in res:
        return 10 ** 6, False, f"status.json خوانده نشد ({st}: {res.get('message')})"
    try:
        snap = json.loads(base64.b64decode(res["content"]).decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return 10 ** 6, False, f"status.json ناخوانا: {e}"
    hb = snap.get("heartbeat") or {}
    age = time.time() - float(hb.get("ts") or 0)
    return age, bool(hb.get("polling")), f"ضربان {int(age)}s پیش، polling={hb.get('polling')}"


def running_already(tok: str, rp: str) -> tuple[bool, str]:
    st, runs = call(f"{API}/repos/{rp}/actions/runs?per_page=15", tok)
    if st != 200:
        return False, f"خواندن اجراها نشد ({st})"
    for r in runs.get("workflow_runs", []):
        if r.get("path", "").endswith(WORKFLOW) and r["status"] in (
                "in_progress", "queued", "pending", "requested", "waiting"):
            return True, f"اجرای #{r['run_number']} ({r['status']}) مشغول است"
    return False, "اجرایی در صف نیست"


def main() -> int:
    tok = (os.environ.get("GITHUB_TOKEN") or "").strip()
    rp = os.environ.get("GITHUB_REPOSITORY", "").strip()
    force = (os.environ.get("FORCE") or "").lower() in ("1", "true", "yes")
    if not tok or not rp:
        print("توکن/مخزن نیست")
        return 1

    age, polling, why = heartbeat(tok, rp)
    print(f"وضعیت ضربان → {why}")
    busy, busy_why = running_already(tok, rp)
    print(f"اجراها → {busy_why}")

    need = force or (not busy and (age > STALE_SEC or not polling))
    if force:
        print("🧪 حالت تست: جدا از سلامت، روشن‌سازی امتحان می‌شود (بدون پیام به کاربر)")
    if not need:
        print("✅ ربات سالم است — نگهبان کاری نکرد")
        return 0

    st, res = call(f"{API}/repos/{rp}/actions/workflows/{WORKFLOW}/dispatches", tok,
                   "POST", {"ref": "main"})
    if st in (200, 204):
        mins = int(age / 60) if age < 10 ** 6 else -1
        note = f"حدود {mins} دقیقه" if mins >= 0 else "نامعلوم"
        print("✅ ربات دوباره روشن شد")
        if not force:
            notify(("🟡 <b>ربات ملک هانتر خاموش شده بود</b>\n"
                f"مدت خاموشی: {note}\n"
                "خودکار روشنش کردم — همین حالا دوباره در دسترس است.\n"
                "<i>پیام‌هایی که در این فاصله فرستادی در صف تلگرام مانده‌اند و جواب می‌گیرند.</i>"), tok)
    else:
        print(f"❌ روشن کردن ناموفق ({st}): {res.get('message')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
