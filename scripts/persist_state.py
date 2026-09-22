# -*- coding: utf-8 -*-
"""
ذخیرهٔ وضعیت کاربر (علاقه‌مندی‌ها، تاریخچه، دیده‌بان‌ها، تنظیمات) در مخزن.

بدون این کار، هر بار که اجرای ۶ ساعته تمام و اجرای تازه شروع می‌شود،
دیتابیس کوچک ربات از صفر ساخته می‌شود و علاقه‌مندی‌های کاربر می‌پرد.
اینجا دیتابیس را رمزنگاری می‌کنیم و به مخزن برمی‌گردانیم.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tarfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
STATE_ENC = DATA / "state.enc"
STATE_TAR = Path("/tmp/state.tar")
KEEP = ["bot.sqlite3", "extra.json", "heartbeat.json"]


def sh(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(BASE), capture_output=True, text=True,
                          check=False if not check else True)


def main() -> int:
    key = os.environ.get("DATA_KEY", "").strip()
    if not key:
        print("سکرت DATA_KEY نیست — ذخیرهٔ وضعیت انجام نشد")
        return 0
    have = [DATA / n for n in KEEP if (DATA / n).exists()]
    if not have:
        print("چیزی برای ذخیره نبود")
        return 0

    with tarfile.open(STATE_TAR, "w") as t:
        for p in have:
            t.add(p, arcname=p.name)
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt",
                        "-in", str(STATE_TAR), "-out", str(STATE_ENC),
                        "-pass", f"pass:{key}"], capture_output=True, text=True)
    STATE_TAR.unlink(missing_ok=True)
    if r.returncode != 0:
        print("رمزنگاری وضعیت ناموفق:", r.stderr[-200:])
        return 0
    print("وضعیت رمزنگاری شد:", STATE_ENC.name,
          f"({STATE_ENC.stat().st_size // 1024}KB)")

    # برگرداندن به مخزن (با توکن داخلی گیت‌هاب؛ اجراهای تازه را trigger نمی‌کند)
    try:
        sh(["git", "config", "user.email", "bot@melkhunter.local"])
        sh(["git", "config", "user.name", "Melk Hunter"])
        sh(["git", "add", "data/state.enc"])
        diff = sh(["git", "diff", "--cached", "--quiet"], check=False)
        if diff.returncode == 0:
            print("وضعیت تغییری نداشت")
            return 0
        sh(["git", "commit", "-q", "-m", "state: ذخیرهٔ خودکار وضعیت کاربر"])
        push = sh(["git", "push", "-q", "origin", "HEAD:main"], check=False)
        if push.returncode == 0:
            print("وضعیت به مخزن برگشت داده شد ✅")
        else:
            # شاید اجرای دیگری هم‌زمان push کرده باشد → یک بار rebase و تلاش دوباره
            sh(["git", "pull", "--rebase", "-q", "origin", "main"], check=False)
            push2 = sh(["git", "push", "-q", "origin", "HEAD:main"], check=False)
            print("تلاش دوم push:", "موفق ✅" if push2.returncode == 0
                  else "ناموفق: " + (push2.stderr or push2.stdout)[-150:])
    except Exception as e:  # noqa: BLE001
        print("ذخیرهٔ وضعیت در گیت ممکن نشد (ربات بدون وقفه ادامه می‌دهد):", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
