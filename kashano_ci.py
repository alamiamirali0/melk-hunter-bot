# -*- coding: utf-8 -*-
"""اجرای در runner گیت‌هاب: ورود به کاشانو + استخراج فایلینگ."""
import os, re, time, base64, json, subprocess, urllib.request, urllib.error
from pathlib import Path

PHONE = "09201231249"
PIN = "88047108"          # PIN احتمالی حساب (اول امتحان می‌شود)
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOK = os.environ.get("GITHUB_TOKEN", "")
OTP_BRANCH = "kashano-otp"
OTP_PATH = "code.txt"
OUT_BRANCH = "kashano-out"
OUT_DIR = Path("kashano_out")
OUT_DIR.mkdir(exist_ok=True)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?98|0)?9\d{9}(?!\d)")


def log(m):
    print(f"[kashano] {m}", flush=True)


def norm_phone(s):
    d = re.sub(r"[\s\-().\u200c]", "", s)
    d = d.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    m = PHONE_RE.search(d)
    if not m:
        return None
    ph = m.group(0)
    if ph.startswith("98") and len(ph) == 12:
        ph = "0" + ph[2:]
    if len(ph) == 10 and ph.startswith("9"):
        ph = "0" + ph
    return ph


def api(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request("https://api.github.com" + url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOK}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "kashano-ci")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def read_otp_file(timeout_s=720, first_wait=20):
    """کدِ تازه را از فایلِ مخزن می‌خواند (تا timeout)."""
    deadline = time.time() + timeout_s
    waited = 0
    while time.time() < deadline:
        st, res = api(f"/repos/{REPO}/contents/{OTP_PATH}?ref={OTP_BRANCH}")
        if st == 200 and res.get("content"):
            code = base64.b64decode(res["content"]).decode().strip()
            log(f"کد از فایل خوانده شد ({len(code)} رقم)")
            return code
        waited += 8
        if waited <= first_wait:
            log(f"صبر برای کدِ تازه از کاربر... ({waited}s)")
        time.sleep(8)
    return None


def collect_rows(page):
    rows = []
    trs = page.locator("table tr:visible")
    n = trs.count()
    log(f"  {n} ردیف جدول")
    for i in range(n):
        tds = trs.nth(i).locator("td:visible")
        if tds.count() == 0:
            continue
        cells = [tds.nth(j).inner_text().strip() for j in range(tds.count())]
        text = " | ".join(c for c in cells if c)
        ph = norm_phone(text)
        if not ph:
            continue
        name, addr_parts = "", []
        for c in cells:
            if not c.strip():
                continue
            c2 = c.strip()
            if not name and not PHONE_RE.search(c2.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))):
                name = c2
            elif name:
                addr_parts.append(c2)
        rows.append({"name": name or "بدون نام", "phone": ph, "address": "، ".join(addr_parts)})
    if rows:
        return rows
    log("  بدون جدول — از متن صفحه می‌خوانم")
    body = page.inner_text("body")
    flat = body.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    for m in PHONE_RE.finditer(flat):
        lo, hi = max(0, m.start() - 60), min(len(flat), m.end() + 120)
        chunk = " ".join(flat[lo:hi].split())
        rows.append({"name": " ".join(chunk.split()[:4]), "phone": norm_phone(m.group(0)), "address": chunk})
    return rows


def submit_code(page, code):
    page.screenshot(path=str(OUT_DIR / "code_page.png"))
    (OUT_DIR / "code_page.html").write_text(page.content(), encoding="utf-8")
    ins = page.locator("input:visible")
    n = ins.count()
    target = ins.nth(n - 1) if n else page.locator("input").last
    target.click(); target.fill("")
    target.type(code, delay=80)
    time.sleep(1)
    clicked = False
    for nm in ("تأیید", "ورود", "ادامه", "تأیید کد", "Verify", "Confirm"):
        b = page.get_by_role("button", name=nm)
        if b.count():
            b.first.click(); clicked = True; break
    if not clicked:
        page.keyboard.press("Enter")
    time.sleep(15)
    page.screenshot(path=str(OUT_DIR / "after_code.png"))
    (OUT_DIR / "after_code.html").write_text(page.content(), encoding="utf-8")
    return "signin" not in page.url.lower() and "sign-in" not in page.url.lower()


def push_out():
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    def g(*a):
        subprocess.run(["git", *a], check=False, env=env, capture_output=True)
    g("config", "user.email", "ci@k.local"); g("config", "user.name", "ci")
    url = f"https://x-access-token:{TOK}@github.com/{REPO}.git"
    g("push", "-f", url, f"HEAD:{OUT_BRANCH}")
    log(f"خروجی push شد به شاخهٔ {OUT_BRANCH}")


def main():
    from playwright.sync_api import sync_playwright
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, locale="fa-IR", viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        log("بازکردن signin...")
        page.goto("https://kashano.ir/signin", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("input", timeout=30000)
        inp = page.locator("input").first
        inp.click(); inp.fill("")
        inp.type(PHONE, delay=50)
        time.sleep(1)
        page.screenshot(path=str(OUT_DIR / "1_phone.png"))
        page.get_by_role("button", name="ادامه").click()
        log("شماره فرستاده شد — منتظر کادرِ کد")
        time.sleep(12)

        ok = submit_code(page, PIN)
        log(f"تلاشِ PIN: {ok}")
        if not ok:
            code = read_otp_file()
            if code:
                ok = submit_code(page, code)
                log(f"تلاشِ کدِ تازه: {ok}")
            if not ok:
                (OUT_DIR / "STATUS").write_text("NEEDS_CODE_RETRY")
                log("❌ ورود موفق نبود — STATUS=NEEDS_CODE_RETRY")
                push_out(); browser.close(); return
        log("✅ وارد شدیم!")

        # فایلینگ
        for sel in ("فایلینگ", "فایلینگ املاک", "فایل‌های من"):
            loc = page.get_by_text(sel, exact=False)
            if loc.count():
                loc.first.click(); time.sleep(6); break
        else:
            for u in ("https://kashano.ir/filings", "https://kashano.ir/filing",
                      "https://kashano.ir/my-filings", "https://kashano.ir/dashboard"):
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=30000)
                    if page.locator("table, [class*=fil], [class*=list]").count():
                        break
                except Exception:
                    continue
        page.screenshot(path=str(OUT_DIR / "4_filing.png"))
        (OUT_DIR / "4_filing.html").write_text(page.content(), encoding="utf-8")
        log("صفحهٔ فایلینگ:", page.url)

        seen, order = {}, []
        for pn in range(1, 41):
            log(f"صفحهٔ {pn}...")
            for r in collect_rows(page):
                if r["phone"] and r["phone"] not in seen:
                    seen[r["phone"]] = r; order.append(r["phone"])
            nxt = None
            for nm in ("بعدی", "Next"):
                b = page.get_by_role("button", name=nm)
                if b.count() and b.first.is_enabled():
                    nxt = b.first; break
            if not nxt:
                break
            try:
                nxt.click()
            except Exception:
                break
            time.sleep(5)
            page.screenshot(path=str(OUT_DIR / f"4_filing_p{pn}.png"))
            (OUT_DIR / f"4_filing_p{pn}.html").write_text(page.content(), encoding="utf-8")

        items = [seen[p] for p in order]
        import csv
        with open(OUT_DIR / "kashano_owners.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["name", "phone", "address"])
            for it in items:
                w.writerow([it["name"], it["phone"], it["address"]])
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(["نام", "شماره", "آدرس"])
        for it in items:
            ws.append([it["name"], it["phone"], it["address"]])
        wb.save(OUT_DIR / "kashano_owners.xlsx")
        (OUT_DIR / "STATUS").write_text(f"DONE:{len(items)}")
        log(f"✅ DONE: {len(items)} مالک")
        push_out()
        browser.close()


if __name__ == "__main__":
    main()
