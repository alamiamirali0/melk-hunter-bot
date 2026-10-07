# -*- coding: utf-8 -*-
"""کاشانو v7: ورود + احراز IP دفتر املاک (کد امنیتی + کد پیامکی) + استخراج فایلینگ."""
import os, re, time, base64, json, random, subprocess, urllib.request, urllib.error
from pathlib import Path

PHONE = "09201231249"
PIN = "88047108"          # رمزِ حساب (در احراز دفتر به‌عنوان کد امنیتی هم امتحان می‌شود)
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOK = os.environ.get("GITHUB_TOKEN", "")
OTP_BRANCH = "kashano-otp"
OTP_PATH = "code.txt"
OUT_BRANCH = "kashano-out"
OUT_DIR = Path("kashano_out")
OUT_DIR.mkdir(exist_ok=True)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?98|0)?9\d{9}(?!\d)")
RESEND_RE = re.compile(r"(ارسال|دوباره|مجدد|درخواست|retry|resend|again)", re.I)
PROXIES = [
    "http://93.118.120.60:8080",
    "http://79.127.30.250:8080",
    "http://185.112.35.184:3128",
    "http://217.219.83.186:2222",
]


def log(*args):
    print("[kashano] " + " ".join(str(a) for a in args), flush=True)


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


def push_out(tag=""):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"

    def g(*a):
        return subprocess.run(["git", *a], check=False, env=env, capture_output=True, text=True)

    g("config", "user.email", "ci@k.local")
    g("config", "user.name", "ci")
    g("add", "-A")
    c = g("commit", "-m", f"kashano out {tag}", "--allow-empty", "-q")
    if c.returncode != 0:
        log("خطای commit:", c.stderr[:200])
    url = f"https://x-access-token:{TOK}@github.com/{REPO}.git"
    p = g("push", "-f", url, f"HEAD:{OUT_BRANCH}")
    if p.returncode != 0:
        log("خطای push:", p.stderr[:300])
    else:
        log(f"خروجی push شد ({tag})")


def capture_diag(page, tag):
    try:
        txt = page.inner_text("body")
    except Exception:
        txt = "(خواندن متن ممکن نبود)"
    try:
        url = page.url
    except Exception:
        url = "?"
    (OUT_DIR / f"diag{tag}.txt").write_text(f"URL: {url}\n\n{txt}", encoding="utf-8")
    try:
        page.screenshot(path=str(OUT_DIR / f"diag{tag}.png"), full_page=True)
    except Exception:
        pass
    try:
        (OUT_DIR / f"diag{tag}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    log(f"diag{tag} — {len(txt)} کاراکتر")
    return txt


def try_resend(page, tag=""):
    """دکمهٔ «درخواست/ارسال دوبارهٔ کد» را پیدا و کلیک می‌کند."""
    capture_diag(page, f"_before{tag}")
    clicked = False
    for sel in ("button", "a", "span", "div"):
        if clicked:
            break
        try:
            els = page.locator(f"{sel}:visible")
            n = els.count()
        except Exception:
            continue
        for i in range(n):
            el = els.nth(i)
            try:
                t = " ".join((el.inner_text(timeout=500) or "").split())
            except Exception:
                continue
            if not (3 <= len(t) <= 70):
                continue
            if RESEND_RE.search(t) and ("کد" in t or "پیامک" in t or "resend" in t.lower()):
                try:
                    el.scroll_into_view_if_needed()
                    el.click(timeout=4000)
                    clicked = True
                    log(f"✅ دکمهٔ resend: «{t[:50]}»")
                    break
                except Exception as e:
                    log(f"کلیک resend خطا ({t[:30]}): {type(e).__name__}")
    time.sleep(6)
    capture_diag(page, f"_after{tag}")
    return clicked


def wait_user_codes(page, timeout_s=900):
    """از فایل code.txt می‌خواند: ۲ خط = (کد امنیتی, کد پیامکی)؛ ۱ خط = (PIN, کد)."""
    deadline = time.time() + timeout_s
    last_resend = time.time()
    waited = 0
    while time.time() < deadline:
        st, res = api(f"/repos/{REPO}/contents/{OTP_PATH}?ref={OTP_BRANCH}")
        if st == 200 and res.get("content"):
            raw = base64.b64decode(res["content"]).decode().strip()
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            if len(lines) >= 2:
                sec, sms = lines[0], lines[-1]
            elif len(lines) == 1:
                sec, sms = PIN, lines[0]
            else:
                time.sleep(5)
                continue
            log(f"کدها دریافت شد: sec={sec or '—'} sms={sms}")
            try:
                api(f"/repos/{REPO}/contents/{OTP_PATH}?ref={OTP_BRANCH}", method="DELETE",
                    payload={"sha": res["sha"], "message": "consumed"})
            except Exception:
                pass
            return sec, sms
        waited += 8
        if waited <= 120 and waited % 24 == 0:
            log(f"صبر برای کدها از کاربر... ({waited}s)")
        if time.time() - last_resend > 150:
            last_resend = time.time()
            try_resend(page, "_loop")
        time.sleep(8)
    return None


def enter_office_codes(page, sec, sms):
    """فیلدهای احراز دفتر: ۱) کد امنیتی ۲) کد تایید پیامکی."""
    ins = page.locator("input:visible")
    n = ins.count()
    log(f"{n} فیلد در صفحهٔ احراز دفتر")
    if n >= 2:
        for i, val in ((0, sec), (1, sms)):
            if val:
                ins.nth(i).click()
                ins.nth(i).fill("")
                ins.nth(i).type(val, delay=90)
                time.sleep(0.5)
    elif n == 1 and sms:
        ins.nth(0).click()
        ins.nth(0).fill("")
        ins.nth(0).type(sms, delay=90)
    time.sleep(1)
    for nm in ("ادامه", "تأیید", "ورود", "Confirm"):
        b = page.get_by_role("button", name=nm)
        if b.count():
            try:
                b.first.click()
                break
            except Exception:
                pass
    else:
        page.keyboard.press("Enter")
    time.sleep(12)


def enter_password(page, pw):
    ptype = page.locator("input[type=password]:visible")
    if ptype.count():
        target = ptype.first
    else:
        ins = page.locator("input:visible")
        target = ins.nth(ins.count() - 1)
    target.click()
    target.fill("")
    target.type(pw, delay=80)
    time.sleep(1)
    for nm in ("ادامه", "ورود", "تأیید", "Login"):
        b = page.get_by_role("button", name=nm)
        if b.count():
            try:
                b.first.click()
                break
            except Exception:
                pass
    else:
        page.keyboard.press("Enter")
    time.sleep(12)


def login_ok(page):
    try:
        t = page.inner_text("body")
        u = page.url.lower()
    except Exception:
        return False
    return "signin" not in u and "ورود" not in t and "رمز عبور" not in t and "احراز IP" not in t


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


def main():
    from playwright.sync_api import sync_playwright
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    def is_blocked(p):
        try:
            t = p.inner_text("body")
        except Exception:
            return False
        return ("داخل کشور" in t) or ("آی پی های داخل" in t) or ("متعلق به ایران نیست" in t)

    def try_signin(browser, px):
        log(f"تلاش با پروکسی: {px or 'مستقیم'}")
        c = None
        try:
            kw = dict(user_agent=UA, locale="fa-IR", viewport={"width": 1366, "height": 900})
            if px:
                kw["proxy"] = {"server": px}
            c = browser.new_context(**kw)
            p = c.new_page()
            p.goto("https://kashano.ir/signin", wait_until="domcontentloaded", timeout=60000)
            time.sleep(10)
            if is_blocked(p):
                log("  ❌ پیام IP غیرایرانی — پروکسی رد شد")
                c.close()
                return None, None
            if p.locator("input:visible").count() == 0:
                log("  ؟ فیلدی پیدا نشد")
                (OUT_DIR / "mystery.txt").write_text(p.url + "\n\n" + p.inner_text("body"), encoding="utf-8")
                c.close()
                return None, None
            log("  ✅ صفحهٔ واقعیِ signin")
            return c, p
        except Exception as e:
            log(f"  خطا: {type(e).__name__}: {str(e)[:120]}")
            if c:
                try:
                    c.close()
                except Exception:
                    pass
            return None, None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = page = None
        for px in PROXIES + [None]:
            ctx, page = try_signin(browser, px)
            if ctx:
                break
        if not ctx:
            (OUT_DIR / "STATUS").write_text("ALL_PROXIES_BLOCKED")
            push_out("_allblocked")
            browser.close()
            return

        inp = page.locator("input:visible").first
        inp.click()
        inp.fill("")
        inp.type(PHONE, delay=50)
        time.sleep(1)
        page.get_by_role("button", name="ادامه").click()
        log("شماره فرستاده شد")
        time.sleep(15)

        if is_blocked(page):
            (OUT_DIR / "STATUS").write_text("BLOCKED_AFTER_SUBMIT")
            capture_diag(page, "_blocked")
            push_out("_blocked")
            browser.close()
            return

        capture_diag(page, "_step2")
        push_out("_step2")
        body2 = page.inner_text("body")
        log("مرحلهٔ ۲:", " ".join(body2.split())[:150])

        logged = False
        if "رمز عبور" in body2:
            log(f"مرحلهٔ رمز عبور — {PIN} را وارد می‌کنم")
            enter_password(page, PIN)
            capture_diag(page, "_afterpw")
            push_out("_afterpw")
            body3 = page.inner_text("body")

            if "احراز IP" in body3 or "دفتر املاک" in body3:
                # --- احراز IP دفتر املاک ---
                log("صفحهٔ احراز IP دفتر — درخواست پیامک کد تایید")
                clicked_req = False
                for loc in (page.get_by_role("button", name="درخواست پیامک"),
                            page.get_by_text("درخواست پیامک"),
                            page.get_by_text("درخواست کد")):
                    if loc.count():
                        try:
                            loc.first.click()
                            clicked_req = True
                            break
                        except Exception:
                            pass
                if not clicked_req:
                    for sel in ("button", "a", "span", "div"):
                        for el in page.locator(f"{sel}:visible").all():
                            try:
                                t = " ".join((el.inner_text(timeout=300) or "").split())
                            except Exception:
                                continue
                            if "درخواست" in t and ("کد" in t or "پیامک" in t):
                                try:
                                    el.click()
                                    clicked_req = True
                                    break
                                except Exception:
                                    pass
                        if clicked_req:
                            break
                log("دکمهٔ درخواست پیامک:", "کلیک شد" if clicked_req else "پیدا نشد!")
                time.sleep(10)
                capture_diag(page, "_sms_requested")
                push_out("_sms_requested")
                log("پیامک در راه است — منتظر کدها (خط اول=کد امنیتی، خط دوم=کد پیامک)")
                codes = wait_user_codes(page, timeout_s=1200)
                if not codes:
                    (OUT_DIR / "STATUS").write_text("NO_SMS_RECEIVED")
                    push_out("_nosms")
                    browser.close()
                    return
                sec, sms = codes
                enter_office_codes(page, sec, sms)
                capture_diag(page, "_afteroffice")
                push_out("_afteroffice")
                logged = login_ok(page)
                if not logged:
                    log("تلاش اول احراز دفتر جواب نداد — ۵ دقیقه منتظر کد تازه")
                    codes2 = wait_user_codes(page, timeout_s=300)
                    if codes2:
                        sec, sms = codes2
                        enter_office_codes(page, sec, sms)
                        capture_diag(page, "_afteroffice2")
                        push_out("_afteroffice2")
                        logged = login_ok(page)
            elif "رمز عبور" in body3:
                # رمز اشتباه بود — مسیر فراموشی رمز
                log("رمز قبلی اشتباه — مسیر فراموشی رمز")
                f = page.get_by_text("فراموش کرده", exact=False)
                if f.count():
                    f.first.click()
                    time.sleep(10)
                capture_diag(page, "_forgot")
                push_out("_forgot")
                codes = wait_user_codes(page, timeout_s=900)
                if not codes:
                    (OUT_DIR / "STATUS").write_text("NO_SMS_RECEIVED")
                    push_out("_nosms")
                    browser.close()
                    return
                _, sms = codes
                enter_office_codes(page, "", sms)
                capture_diag(page, "_afterreset")
                push_out("_afterreset")
                body_r = page.inner_text("body")
                if "رمز جدید" in body_r or "تکرار" in body_r:
                    newpass = "Kashano" + "".join(random.choice("23456789") for _ in range(6)) + "!"
                    ins = page.locator("input:visible")
                    for i in range(min(ins.count(), 2)):
                        ins.nth(i).click()
                        ins.nth(i).fill("")
                        ins.nth(i).type(newpass, delay=60)
                    for nm in ("ثبت", "ذخیره", "ادامه", "تأیید"):
                        b = page.get_by_role("button", name=nm)
                        if b.count():
                            try:
                                b.first.click()
                                break
                            except Exception:
                                pass
                    else:
                        page.keyboard.press("Enter")
                    time.sleep(12)
                    capture_diag(page, "_afternewpw")
                    push_out("_afternewpw")
                    if not login_ok(page):
                        enter_password(page, newpass)
                        time.sleep(8)
                    (OUT_DIR / "NEW_PASSWORD.txt").write_text(newpass, encoding="utf-8")
                    logged = login_ok(page)
                else:
                    logged = login_ok(page)
            else:
                logged = login_ok(page)
        else:
            logged = login_ok(page)

        if not logged:
            (OUT_DIR / "STATUS").write_text("LOGIN_FAILED")
            capture_diag(page, "_finalfail")
            push_out("_loginfail")
            browser.close()
            return

        log("✅ وارد شدیم!")
        capture_diag(page, "_loggedin")

        # فایلینگ
        for sel in ("فایلینگ", "فایلینگ املاک", "فایل‌های من"):
            loc = page.get_by_text(sel, exact=False)
            if loc.count():
                loc.first.click()
                time.sleep(6)
                break
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
        push_out("_filing")

        seen, order = {}, []
        for pn in range(1, 41):
            log(f"صفحهٔ {pn}...")
            for r in collect_rows(page):
                if r["phone"] and r["phone"] not in seen:
                    seen[r["phone"]] = r
                    order.append(r["phone"])
            nxt = None
            for nm in ("بعدی", "Next"):
                b = page.get_by_role("button", name=nm)
                if b.count() and b.first.is_enabled():
                    nxt = b.first
                    break
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
        wb = Workbook()
        ws = wb.active
        ws.append(["نام", "شماره", "آدرس"])
        for it in items:
            ws.append([it["name"], it["phone"], it["address"]])
        wb.save(OUT_DIR / "kashano_owners.xlsx")
        (OUT_DIR / "STATUS").write_text(f"DONE:{len(items)}")
        log(f"✅ DONE: {len(items)} مالک")
        push_out("_done")
        browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"❌ خطای غیرمنتظره: {type(e).__name__}: {e}")
        traceback.print_exc()
        try:
            (OUT_DIR / "STATUS").write_text(f"CRASH:{type(e).__name__}:{str(e)[:120]}")
            push_out("_crash")
        except Exception as e2:
            log("push خطا هم نشد:", e2)
        raise
