# -*- coding: utf-8 -*-
"""تشخیص وضعیت سرور: دسترسی تلگرام، داده‌ها، و پاسخ API."""
from __future__ import annotations

import json
import os
import platform
import socket
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def line(*parts):
    print(" ".join(str(x) for x in parts), flush=True)


line("=" * 56)
line("🐍 پایتون:", sys.version.split()[0], "| معماری:", platform.machine())
line("📁 مسیر:", BASE)
line("🧩 فایل‌های data:")
for p in sorted((BASE / "data").iterdir()):
    line(f"    {p.name:22} {p.stat().st_size:>9,} بایت")

# دسترسی شبکه به تلگرام
line("\n🌐 تست شبکه:")
for host, port in (("api.telegram.org", 443), ("github.com", 443)):
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=10)
        s.close()
        line(f"   ✅ {host} وصل شد ({int((time.time()-t0)*1000)}ms)")
    except OSError as e:
        line(f"   ❌ {host} وصل نشد: {e}")

# توکن و API
try:
    import melkbot.bot as B
    TOKEN = B.TOKEN
    line(f"\n🔑 توکن: …{TOKEN[-6:]} | config: admin_ids={B.CFG.get('admin_ids')} "
         f"allowed={B.CFG.get('allowed_users')} mode={B.CFG.get('mode', 'polling')}")
except Exception as e:
    line(f"\n❌ خطا در import ربات: {type(e).__name__}: {e}")
    raise SystemExit(1)

for method in ("getMe", "getWebhookInfo"):
    t0 = time.time()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/{method}",
                                    timeout=15) as r:
            d = json.load(r)["result"]
        ms = int((time.time() - t0) * 1000)
        if method == "getMe":
            line(f"   ✅ getMe: @{d['username']} (id {d['id']}) — {ms}ms")
        else:
            line(f"   ✅ webhook: url={d.get('url') or '—'} | pending={d.get('pending_update_count')} "
                 f"| last_error={(d.get('last_error_message') or '—')[:80]}")
    except Exception as e:
        line(f"   ❌ {method} خطا: {type(e).__name__}: {e}")

# داده و موتور
line("\n📚 بارگذاری موتور:")
t0 = time.time()
B.load_engine()
line(f"   ✅ {B.filtered_count()} مخاطب در {int((time.time()-t0)*1000)}ms")
for q in ("برج ماری", "کلنگی", "آرش جنت", "#دیوار متر:۳۰۰"):
    try:
        res = B.run_search(840822911, q)
        line(f"   🔎 «{q}» → {res['total']} نتیجه")
    except Exception as e:
        line(f"   ❌ «{q}» خطا: {type(e).__name__}: {e}")

# ساخت اپلیکیشن (همان کاری که استارت می‌کند)
line("\n⚙️ ساخت اپلیکیشن:")
try:
    app = B.build_app()
    line(f"   ✅ اپ ساخته شد | handlerها: "
         f"{sum(len(v) for v in app.handlers.values())} | job_queue: {bool(app.job_queue)}")
except Exception as e:
    line(f"   ❌ ساخت اپ خطا: {type(e).__name__}: {e}")

line("\n🧠 واژگان و مغز:")
try:
    v = B.vocab()
    line(f"   ✅ {len(v)} کلمه")
    from melkbot import smart as sm
    line(f"   ✅ گشادسازی «خونه» → {sm.expand_queries('خونه')[:2]}")
except Exception as e:
    line(f"   ❌ خطا: {type(e).__name__}: {e}")

line("=" * 56)
line("✅ تشخیص تمام شد")
