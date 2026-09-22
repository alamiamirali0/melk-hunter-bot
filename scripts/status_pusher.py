# -*- coding: utf-8 -*-
"""
گزارش زندهٔ وضعیت — هر ۹۰ ثانیه وضعیت ربات را در شاخهٔ «status» مخزن می‌گذارد
تا از بیرون (بدون نیاز به لاگ محرمانهٔ گیت‌هاب) بتوان سلامت را دید.

این فایل هیچ دادهٔ مخاطبی ندارد: فقط ضربان، تعداد پیام و ۲۰ خط آخر لاگ ربات.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WORK = Path("/tmp/status_repo")
DATA = BASE / "data"
LOGS = BASE / "logs"
INTERVAL = int(os.environ.get("STATUS_INTERVAL", "90"))


def git(*args, check=False):
    return subprocess.run(["git", *args], cwd=str(WORK), capture_output=True,
                          text=True, check=check)


def remote_with_token() -> str:
    """آدرس مخزن با توکن داخلی گیت‌هاب (برای push از داخل اجرا)."""
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("MELKBOT_GH_TOKEN") or ""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if tok and repo:
        return f"https://x-access-token:{tok}@github.com/{repo}.git"
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(BASE),
                       capture_output=True, text=True)
    return (r.stdout or "").strip()


def snapshot() -> dict:
    hb = {}
    hb_path = DATA / "heartbeat.json"
    if hb_path.exists():
        try:
            hb = json.loads(hb_path.read_text(encoding="utf-8"))
        except Exception:
            hb = {}
    log_tail = ""
    lg = LOGS / "bot.log"
    if lg.exists():
        raw = lg.read_text(encoding="utf-8", errors="ignore").splitlines()
        # خطوط مهم (پیام کاربر، نتیجهٔ سرچ، خطا) را نگه می‌داریم و نویز را می‌اندازیم
        noise = ("apscheduler.executors", "Running job \"heartbeat", "executed successfully")
        keep = [l for l in raw if l.strip() and not any(n in l for n in noise)]
        log_tail = "\n".join(keep[-25:]) if keep else "\n".join(raw[-3:])
    return {
        "time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "run": os.environ.get("GITHUB_RUN_ID", ""),
        "log_age_sec": int(time.time() - lg.stat().st_mtime) if lg.exists() else -1,
        "heartbeat_file": hb_path.exists(),
        "heartbeat": hb,
        "bot_log_tail": log_tail,
    }


def main() -> None:
    url = remote_with_token()
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    git("init", "-q", "-b", "status")
    git("config", "user.email", "status@melkhunter.local")
    git("config", "user.name", "Melk Hunter Status")
    git("remote", "add", "origin", url)

    while True:
        try:
            snap = snapshot()
            (WORK / "status.json").write_text(
                json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
            git("add", "-A")
            git("commit", "-q", "-m", f"status {snap['time_utc']}")
            p = git("push", "-q", "--force", "origin", "status:status")
            if p.returncode != 0:
                print("push نشد:", (p.stderr or "")[-200:], flush=True)
        except Exception as e:  # noqa: BLE001
            print("خطای گزارش:", e, flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
