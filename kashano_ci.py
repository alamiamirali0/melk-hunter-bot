# -*- coding: utf-8 -*-
"""اجرای در runner گیت‌هاب: ورود به کاشانو + استخراج فایلینگ. نسخهٔ ۴ — دیباگ پیامک."""
import os, re, time, base64, json, subprocess, urllib.request, urllib.error
from pathlib import Path

PHONE = "09201231249"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOK = os.environ.get("GITHUB_TOKEN", "")
OTP_BRANCH = "kashano-otp"
OTP_PATH = "code.txt"
OUT_BRANCH = "kashano-out"
OUT_DIR = Path("kashano_out")
OUT_DIR.mkdir(exist_ok=True)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?98|0)?9\d{9}(?!\d)")
RESEND_RE = re.compile(r"(ارسال|دوباره|مجدد|retry|resend|again)", re.I)
# پروکسی‌های ایرانی که از runner تست شدند (به ترتیب اولویت)
PROXIES = [
    "http://93.118.120.60:8080",
    "http://79.127.30.250:8080",
    "http://185.112.35.184:3128",
    "http://217.219.83.186:2222",
]


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


def push_out(tag=""):
    """کامیتِ کاملِ درخت کاری (شامل kashano_out) + push به شاخهٔ خروجی."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"

    def g(*a):
        r = subprocess.run(["git", *a], check=False, env=env, capture_output=True, text=True)
        return r

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
        log(f"خروجی push شد به شاخهٔ {OUT_BRANCH} ({tag})")


def capture_diag(page, tag):
    """متن + اسکرین‌شات + HTML صفحه را برای عیب‌یابی می‌نویسد."""
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
    log(f"diag{tag} ثبت شد — {len(txt)} کاراکتر متن")
    return txt


def try_resend(page, tag=""):
    """دکمهٔ «ارسال مجدد کد» را پیدا می‌کند و کلیک می‌کند."""
    txt = capture_diag(page, f"_before{tag}")
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
                t = (el.inner_text(timeout=500) or "").strip()
            except Exception:
                continue
            t2 = " ".join(t.split())
            if not (3 <= len(t2) <= 70):
                continue
            if RESEND_RE.search(t2) and ("کد" in t2 or "تأیید" in t2 or "تایید" in t2
                                         or "resend" in t2.lower() or "retry" in t2.lower()
                                         or "دوباره" in t2 or "مجدد" in t2):
                try:
                    el.scroll_into_view_if_needed()
                    el.click(timeout=4000)
                    clicked = True
                    log(f"✅ دکمهٔ resend کلیک شد: «{t2[:50]}»")
                    break
                except Exception as e:
                    log(f"کلیک resend خطا ({t2[:30]}): {type(e).__name__}")
    time.sleep(5)
    after = capture_diag(page, f"_after{tag}")
    if clicked:
        # آیا پیام «ارسال شد» یا شمارش معکوس آمده؟
        tail = after[-400:].replace("\n", " | ")
        log("پس از resend (پایان متن):", tail[:300])
    else:
        log("دکمهٔ resend پیدا نشد — متن صفحه در diag ثبت شد")
    return clicked


def submit_code(page, code, tag=""):
    page.screenshot(path=str(OUT_DIR / f"code_page{tag}.png"))
    (OUT_DIR / f"code_page{tag}.html").write_text(page.content(), encoding="utf-8")
    try:
        page.wait_for_selector("input:visible", timeout=35000)
    except Exception:
        log(f"فیلد کد پیدا نشد{tag}")
        return False
    time.sleep(1)
    ins = page.locator("input:visible")
    n = ins.count()
    log(f"{n} فیلدِ ورودی در صفحهٔ کد{tag}")
    if n == 0:
        return False
    try:
        if n >= 3:
            first = ins.first
            first.click()
            for ch in code:
                first.type(ch, delay=150)
        else:
            target = ins.nth(n - 1)
            target.click()
            target.fill("")
            target.type(code, delay=90)
    except Exception as e:
        log(f"نویسندگی کد خطا: {type(e).__name__}: {str(e)[:100]}")
        return False
    time.sleep(1)
    clicked = False
    for nm in ("تأیید", "ورود", "ادامه", "تأیید کد", "Verify", "Confirm"):
        b = page.get_by_role("button", name=nm)
        if b.count():
            try:
                b.first.click()
                clicked = True
                break
            except Exception:
                pass
    if not clicked:
        page.keyboard.press("Enter")
    time.sleep(15)
    page.screenshot(path=str(OUT_DIR / f"after_code{tag}.png"))
    (OUT_DIR / f"after_code{tag}.html").write_text(page.content(), encoding="utf-8")
    try:
        still = "signin" in page.url.lower() or "sign-in" in page.url.lower()
    except Exception:
        still = True
    log(f"URL بعد از ارسال کد{tag}: {page.url}")
    return not still


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
        """بازکردن signin با یک پروکسی؛ صفحهٔ واقعی را برمی‌گرداند یا None."""
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
                log("  ❌ هنوز پیامِ IP غیرایرانی — پروکسی رد شد")
                c.close()
                return None, None
            if p.locator("input:visible").count() == 0:
                log("  ؟ فیلدی پیدا نشد")
                (OUT_DIR / f"mystery_{(px or 'direct').split(':')[0]}.txt").write_text(
                    p.url + "\n\n" + p.inner_text("body"), encoding="utf-8")
                c.close()
                return None, None
            log("  ✅ صفحهٔ واقعیِ signin باز شد")
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
            log("❌ هیچ پروکسی‌ای عبور نکرد")
            push_out("_allblocked")
            browser.close()
            return

        inp = page.locator("input:visible").first
        inp.click()
        inp.fill("")
        inp.type(PHONE, delay=50)
        time.sleep(1)
        page.screenshot(path=str(OUT_DIR / "1_phone.png"))
        page.get_by_role("button", name="ادامه").click()
        log("شماره فرستاده شد — منتظر صفحهٔ کد")
        time.sleep(15)

        if is_blocked(page):
            (OUT_DIR / "STATUS").write_text("BLOCKED_AFTER_SUBMIT")
            capture_diag(page, "_blocked_after_submit")
            push_out("_blocked")
            browser.close()
            return

        # صفحهٔ کد: دیباگ + دکمهٔ ارسال مجدد + push اولیه
        capture_diag(page, "_codepage")
        try_resend(page, "_1")
        push_out("_early")

        # صبر برای کد از کاربر (با تلاش resend هر ۲/۵ دقیقه)
        code = None
        deadline = time.time() + 900
        last_resend = time.time()
        waited = 0
        while time.time() < deadline:
            st, res = api(f"/repos/{REPO}/contents/{OTP_PATH}?ref={OTP_BRANCH}")
            if st == 200 and res.get("content"):
                code = base64.b64decode(res["content"]).decode().strip()
                log(f"✅ کد از فایل خوانده شد ({len(code)} رقم)")
                break
            waited += 8
            if waited <= 60 and waited % 24 == 0:
                log(f"صبر برای کدِ کاربر... ({waited}s)")
            if time.time() - last_resend > 150:
                last_resend = time.time()
                log("تلاش resend دوباره...")
                try_resend(page, "_loop")
            time.sleep(8)

        if not code:
            (OUT_DIR / "STATUS").write_text("NO_SMS_RECEIVED")
            log("❌ کدی نرسید — STATUS=NO_SMS_RECEIVED")
            push_out("_nosms")
            browser.close()
            return

        ok = submit_code(page, code, tag="")
        log(f"تلاشِ کدِ تازه: {ok}")
        if not ok:
            (OUT_DIR / "STATUS").write_text("BAD_CODE")
            push_out("_badcode")
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
