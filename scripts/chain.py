# -*- coding: utf-8 -*-
"""
زنجیره‌سازی: اجرای بعدی ربات را در صف می‌گذارد تا هیچ‌وقت خاموش نماند.

در پایان هر اجرای سالم، این اسکریپت یک اجرای تازه از relay را dispatch می‌کند.
آن اجرای تازه در حالت «در انتظار» می‌ماند (concurrency گروه ربات) و *دقیقاً* وقتی
اجرای فعلی تمام شد، بی‌درنگ بالا می‌آید. پس دیگر به زمان‌بندی گیت‌هاب وابسته نیستیم.

محافظ‌ها:
  • فقط وقتی زنجیره می‌سازد که ربات واقعاً سالم بوده (نشانهٔ سلامت با ۱۰ دقیقه کارکرد).
  • اگر اجرایی از قبل در صف/در حال اجرا باشد، دوباره dispatch نمی‌کند.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MARKER = BASE / "logs" / "healthy.marker"
WORKFLOW = "relay.yml"
API = "https://api.github.com"


def token() -> str:
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("MELKBOT_GH_TOKEN") or "").strip()


def repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY", "").strip()


def call(url: str, tok: str, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "melk-hunter-chain",
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


def bot_is_alive(tok: str, rp: str) -> tuple[bool, str]:
    """آیا همین حالا اجرای دیگری از ربات در صف یا در حال اجراست؟"""
    st, runs = call(f"{API}/repos/{rp}/actions/runs?per_page=15", tok)
    if st != 200:
        return False, f"خواندن اجراها نشد ({st})"
    mine = str(os.environ.get("GITHUB_RUN_ID", ""))
    for r in runs.get("workflow_runs", []):
        if r.get("path", "").endswith(WORKFLOW) and r["status"] in ("in_progress", "queued", "pending", "requested", "waiting"):
            if str(r["id"]) != mine:
                return True, f"اجرای #{r['run_number']} ({r['status']}) از قبل هست"
    return False, "اجرای دیگری در صف نیست"


def notify(text: str, tok: str) -> None:
    """خبر کوتاه به مالک ربات در تلگرام (اگر توکن ربات در دسترس باشد)."""
    bt = (os.environ.get("MELKBOT_TOKEN") or "").strip()
    owner = (os.environ.get("OWNER_ID") or "").strip()
    if not bt or not owner.isdigit():
        return
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bt}/sendMessage",
            data=json.dumps({"chat_id": int(owner), "text": text, "parse_mode": "HTML",
                             "disable_notification": False}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:  # noqa: BLE001
        print("خبر تلگرام نرفت:", e)


def main() -> int:
    tok, rp = token(), repo()
    if not tok or not rp:
        print("توکن/مخزن در دسترس نیست — زنجیره‌سازی رد شد")
        return 0

    healthy = MARKER.exists()
    if not healthy:
        print("این اجرا نشانهٔ سلامت ندارد (ربات کامل بالا نیامده) — زنجیره ساخته نشد")
        return 0

    busy, why = bot_is_alive(tok, rp)
    if busy:
        print("زنجیره لازم نیست:", why)
        return 0

    st, res = call(f"{API}/repos/{rp}/actions/workflows/{WORKFLOW}/dispatches", tok,
                   "POST", {"ref": "main"})
    if st in (200, 204):
        print("✅ اجرای بعدی در صف گذاشته شد — ربات بی‌وقفه ادامه می‌دهد")
    else:
        print(f"❌ dispatch نشد ({st}): {res.get('message')}")
        notify("⚠️ <b>ربات ملک هانتر</b>\nزنجیره‌سازی خودکار کار نکرد؛ نگهبان تا چند دقیقه دیگر روشنش می‌کند.", tok)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
