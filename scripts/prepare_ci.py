# -*- coding: utf-8 -*-
"""
آماده‌سازی محیط اجرا در CI (GitHub Actions):
  - رمزگشایی دفترچهٔ مخاطبین از فایل رمزنگاری‌شده
  - ساخت config.json از روی متغیرهای محیطی (سکرت‌ها)
  - ساخت پوشه‌های لازم
هیچ دادهٔ حساسی داخل مخزن ذخیره نمی‌شود.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"


def sh(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("خطا در اجرای:", " ".join(cmd[:3]), "...\n", r.stderr[-500:])
        sys.exit(1)


def main() -> int:
    key = os.environ.get("DATA_KEY", "").strip()
    token = os.environ.get("MELKBOT_TOKEN", "").strip()
    owner = os.environ.get("OWNER_ID", "").strip()

    if not key:
        print("❌ سکرت DATA_KEY تنظیم نشده"); return 1
    if not token:
        print("❌ سکرت MELKBOT_TOKEN تنظیم نشده"); return 1

    for d in (DATA, BASE / "logs", BASE / "exports"):
        d.mkdir(parents=True, exist_ok=True)

    # ۲) دفترچهٔ مخاطبین از بستهٔ رمزی
    enc = DATA / "privdata.tar.enc"
    tar_path = Path("/tmp/privdata.tar")
    if enc.exists():
        sh(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2",
            "-in", str(enc), "-out", str(tar_path), "-pass", f"pass:{key}"])
        with tarfile.open(tar_path) as t:
            members = [m for m in t.getmembers()
                       if not m.name.startswith(("/", "..")) and ".." not in m.name]
            try:
                t.extractall(DATA, members=members, filter="data")
            except TypeError:          # پایتون‌های قدیمی‌تر
                t.extractall(DATA, members=members)
        tar_path.unlink(missing_ok=True)
        got = sorted(p.name for p in DATA.iterdir() if p.is_file())
        print("✅ داده‌ها رمزگشایی شد:", ", ".join(got))
    else:
        print("⚠️ فایل رمزنگاری‌شده پیدا نشد — ربات با دفترچهٔ خالی بالا می‌آید")

    # ۳) وضعیت تازه‌تر کاربر — *بعد* از بستهٔ اصلی، تا دفترچهٔ قدیمی رویش را نگیرد
    state = DATA / "state.enc"
    if state.exists():
        st_tar = Path("/tmp/state.tar")
        r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2",
                            "-in", str(state), "-out", str(st_tar),
                            "-pass", f"pass:{key}"], capture_output=True, text=True)
        if r.returncode == 0:
            with tarfile.open(st_tar) as t:
                try:
                    t.extractall(DATA, filter="data")
                except TypeError:
                    t.extractall(DATA)
            st_tar.unlink(missing_ok=True)
            print("✅ وضعیت تازهٔ کاربر بازیابی شد (علاقه‌مندی/تاریخچه/دیده‌بان)")
        else:
            print("⚠️ وضعیت قبلی باز نشد — همان بستهٔ اصلی معتبر است")

    cfg = {
        "token": token,
        "admin_ids": [int(owner)] if owner.isdigit() else [],
        "allowed_users": [int(owner)] if owner.isdigit() else [],
        "watch_hour": 10,
        "max_results": 2000,
    }
    (BASE / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ config.json ساخته شد (قفل روی شناسه: {owner or 'همه'} — فایلش داخل مخزن نیست)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
