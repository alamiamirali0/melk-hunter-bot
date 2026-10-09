# -*- coding: utf-8 -*-
"""کاشانو v7: ورود + احراز IP دفتر املاک (کد امنیتی + کد پیامکی) + استخراج فایلینگ."""
import os, re, sys, time, base64, json, random, subprocess, urllib.request, urllib.error
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
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


def fetch_fresh_proxies():
    """فهرش تازهٔ پروکسی‌های ایرانی از چند منبع (geonode + proxyscrape + spys.one)."""
    proxs = []

    def add(ip, port, proto):
        try:
            if ip and str(port) and proto in ("http", "socks5", "socks4"):
                proxs.append((ip, str(port), proto))
        except Exception:
            pass

    # ۱) geonode (http + socks)
    for socks in ("false", "true"):
        for pageno in (1, 2, 3):
            try:
                req = urllib.request.Request(
                    f"https://proxylist.geonode.com/api/proxy-list?country=IR&limit=100"
                    f"&page={pageno}&socks={socks}",
                    headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    d = json.loads(r.read().decode())
                for it in d.get("data", []):
                    protos = it.get("protocols") or ["http"]
                    proto = "socks5" if "socks5" in protos else ("socks4" if "socks4" in protos else "http")
                    add(it.get("ip"), it.get("port"), proto)
            except Exception:
                pass
    # ۲) proxyscrape (دو مجموعهٔ پورت)
    for portset in ("8080,3128,1080,80,8118,9050", "8888,8081,8889,10809,3129"):
        try:
            req = urllib.request.Request(
                "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&country=ir"
                f"&proxy_format=protocolipport&format=text&ports={portset}",
                headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                for line in r.read().decode().splitlines():
                    line = line.strip()
                    if "://" in line:
                        proto, rest = line.split("://", 1)
                        ip, port = rest.rsplit(":", 1)
                        add(ip, port, proto)
        except Exception:
            pass
    # ۳) spys.one (HTML)
    try:
        req = urllib.request.Request("https://spys.one/en/proxy-list/4/countryiran/",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            html = r.read().decode(errors="replace")
        for m in re.finditer(r'(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})\D{0,120}?S(\d)[^0-9]', html):
            add(m.group(1), m.group(2), "socks5" if m.group(3) == "5" else "socks4")
        for m in re.finditer(r'(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})\D{0,120}?HTTP', html):
            add(m.group(1), m.group(2), "http")
    except Exception:
        pass
    seen, uniq = set(), []
    for ip, port, proto in proxs:
        k = (ip, port, proto)
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    log(f"{len(uniq)} پروکسیِ تازه جمع شد")
    return uniq


def proxy_url(ip, port, proto):
    if proto == "socks5":
        return f"socks5h://{ip}:{port}"
    if proto == "socks4":
        return f"socks4a://{ip}:{port}"
    return f"http://{ip}:{port}"


def test_proxy(ux, timeout=10):
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout),
         "-x", ux, "https://kashano.ir/signin"],
        capture_output=True, text=True)
    return r.stdout.strip() == "200"


def pick_working_proxies(limit=15):
    """فهرش تازه + تست موازی → پروکسی‌های زنده."""
    uniq = fetch_fresh_proxies()
    urls = [proxy_url(ip, port, proto) for ip, port, proto in uniq]
    for px in PROXIES:
        if px not in urls:
            urls.append(px)
    alive = []
    with ThreadPoolExecutor(max_workers=15) as ex:
        for ux, ok in zip(urls, ex.map(test_proxy, urls)):
            if ok:
                alive.append(ux)
                log(f"  زنده: {ux}")
    log(f"{len(alive)} پروکسی زنده از {len(urls)}")
    return alive[:limit]


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


def busy_check():
    """اگر اجرای دیگری فعال است، تکرار نکنیم."""
    try:
        cur_id = int(os.environ.get("GITHUB_RUN_ID", "0") or "0")
    except ValueError:
        cur_id = 0
    st, res = api(f"/repos/{REPO}/actions/runs?status=in_progress&per_page=20")
    if st == 200:
        for r in res.get("workflow_runs", []):
            if r["name"] == "kashano-grab" and r.get("id") != cur_id:
                return True
    return False


def try_support_form(p):
    """درخواست ریست کد احراز IP از طریق فرم تماس کاشانو.
    ساختار واقعی فرم: نام / موبایل / ایمیل(اختیاری) / پیام(textarea) / کد امنیتی (SVG 150x36 + دکمهٔ تازه‌سازی) + ارسال
    کد SVG را اسکرین‌شات می‌گیریم، می‌فرستیم branch، کد خوانده‌شده در code.txt می‌آید."""
    def _read_flag(name):
        try:
            st, f = api(f"/repos/{REPO}/contents/kashano_out/{name}?ref={OUT_BRANCH}")
            if st == 200 and f.get("content"):
                return base64.b64decode(f["content"]).decode().strip()
        except Exception:
            pass
        return None
    if _read_flag("support_done.txt"):
        return
    failed = _read_flag("support_failed.txt")
    if failed:
        try:
            from datetime import datetime as _dt
            if time.time() - _dt.fromisoformat(failed.split(" ")[0].replace("Z", "+00:00")).timestamp() < 7200:
                return  # کمتر از ۲ ساعت پیش شکست خورده — اسپم نکن
        except Exception:
            pass
    try:
        p.goto("https://kashano.ir/contact-us", wait_until="domcontentloaded", timeout=45000)
        time.sleep(6)
        ta = p.locator("textarea:visible")
        if not ta.count():
            log("  فرم تماس: textarea پیدا نشد")
            return
        msg = ("سلام. من مالک حساب کاشانو با شمارهٔ 09201231249 هستم. برای ورود از بیرون دفتر، "
               "سایت کد «احراز IP دفتر املاک» می‌خواهد که در دسترس من نیست. "
               "لطفاً این کد را برای همین شماره پیامک کنید یا آن را ریست/غیرفعال کنید. ممنون")
        inp = p.locator("input:visible")
        for i in range(inp.count()):
            el = inp.nth(i)
            try:
                ph = (el.get_attribute("placeholder") or "")
                if "فارسی" in ph:
                    el.fill("مالک حساب کاشانو")
                elif "09" in ph:
                    el.fill(PHONE)
            except Exception:
                pass
        try:
            ta.first.fill(msg)
        except Exception:
            pass
        cap_svg = None
        for sel in ('span[class*="bg-[#e0e0e0]"] svg', 'svg[width="150"][height="36"]'):
            try:
                loc = p.locator(sel)
                if loc.count():
                    cap_svg = loc.first
                    break
            except Exception:
                pass
        done = False
        for rnd in range(3):
            if cap_svg is None or not cap_svg.count():
                log("  تصویر کد امنیتی پیدا نشد")
                break
            try:
                cap_svg.screenshot(path=str(OUT_DIR / "captcha.png"))
            except Exception:
                p.screenshot(path=str(OUT_DIR / "captcha.png"), full_page=True)
            push_out("_captcha")
            log(f"  [فرم] تصویر کد امنیتی ثبت شد (دور {rnd+1}) — منتظر خواندن کد")
            code = None
            deadline = time.time() + 600
            while time.time() < deadline:
                try:
                    st, res = api(f"/repos/{REPO}/contents/{OTP_PATH}?ref={OTP_BRANCH}")
                    if st == 200 and res.get("content"):
                        code = base64.b64decode(res["content"]).decode().strip()
                        try:
                            api(f"/repos/{REPO}/contents/{OTP_PATH}?ref={OTP_BRANCH}",
                                method="DELETE", payload={"sha": res["sha"], "message": "consumed"})
                        except Exception:
                            pass
                        break
                except Exception:
                    pass
                time.sleep(8)
            if not code:
                log("  کدی برای کد امنیتی نرسید — ارسال نمی‌کنم")
                break
            code = code.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")) if any(c.isdigit() for c in code) else code
            before = ""
            try:
                before = p.inner_text("body")
            except Exception:
                pass
            # فیلد کد = آخرین input
            try:
                cap_inp = inp.nth(inp.count() - 1)
                cap_inp.fill("")
                cap_inp.type(code, delay=50)
            except Exception as e:
                log("  خطا در پر کردن کد:", type(e).__name__)
            time.sleep(1)
            p.screenshot(path=str(OUT_DIR / "support_form.png"), full_page=True)
            b = p.get_by_role("button", name="ارسال")
            if b.count():
                try:
                    b.first.click()
                except Exception:
                    pass
            time.sleep(8)
            after = ""
            try:
                after = p.inner_text("body")
            except Exception:
                pass
            p.screenshot(path=str(OUT_DIR / "support_after.png"), full_page=True)
            diff = ""
            if before and after:
                # کلمات تازه‌ای که بعد از ارسال ظاهر شده‌اند
                before_words = set(before.split())
                diff = " ".join(w for w in after.split() if w not in before_words)
            err_hit = any(k in diff for k in ("اجباری", "صحیح نیست", "نادرست", "اشتباه", "دوباره", "نامعتبر", "خطا"))
            ok_hit = any(k in diff for k in ("ثبت شد", "موفقیت", "ارسال شد", "پیام شما"))
            # پاک شدن فیلد نام = نشانهٔ ثبت موفق
            name_cleared = False
            try:
                for i in range(inp.count()):
                    ph = (inp.nth(i).get_attribute("placeholder") or "")
                    if "فارسی" in ph:
                        name_cleared = (inp.nth(i).input_value() or "") == ""
                        break
            except Exception:
                pass
            log(f"  نتیجهٔ دور {rnd+1}: diff={diff[:80]!r} err={err_hit} ok={ok_hit} name_cleared={name_cleared}")
            if err_hit:
                log("  خطا از سایت — کد را تازه می‌کنم")
                try:
                    ref = p.locator('span[class*="cursor-pointer"] svg, svg[class*="cursor-pointer"]')
                    if ref.count():
                        ref.first.click()
                        time.sleep(3)
                except Exception:
                    p.reload(wait_until="domcontentloaded", timeout=45000)
                    time.sleep(6)
                    ta = p.locator("textarea:visible")
                    inp = p.locator("input:visible")
                    if ta.count():
                        ta.first.fill(msg)
                    for i in range(inp.count()):
                        el = inp.nth(i)
                        try:
                            ph = (el.get_attribute("placeholder") or "")
                            if "فارسی" in ph:
                                el.fill("مالک حساب کاشانو")
                            elif "09" in ph:
                                el.fill(PHONE)
                        except Exception:
                            pass
                    for sel in ('span[class*="bg-[#e0e0e0]"] svg', 'svg[width="150"][height="36"]'):
                        try:
                            loc = p.locator(sel)
                            if loc.count():
                                cap_svg = loc.first
                                break
                        except Exception:
                            pass
                continue
            if ok_hit or name_cleared:
                log("  ✅✅ فرم پشتیبانی ثبت شد")
                (OUT_DIR / "support_done.txt").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                push_out("_support")
                done = True
                break
            # نه خطا، نه موفقیت — به‌عنوان نامشخص ثبت می‌کنیم (با مدرک) ولی اسپم نمی‌کنیم
            log("  نتیجهٔ نامشخص — مدرک ثبت شد")
            (OUT_DIR / "support_failed.txt").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            push_out("_support")
            break
        if not done:
            (OUT_DIR / "STATUS").write_text("SUPPORT_TRIED")
            push_out("_support")
        try:
            p.goto("https://kashano.ir/signin", wait_until="domcontentloaded", timeout=45000)
            time.sleep(8)
        except Exception:
            pass
    except Exception as e:
        log("  خطای فرم پشتیبانی:", type(e).__name__, str(e)[:100])
        try:
            p.goto("https://kashano.ir/signin", wait_until="domcontentloaded", timeout=45000)
            time.sleep(8)
        except Exception:
            pass


def _pause_if_sec_window():
    """اگر همهٔ کاندیدهای کد امنیتی تازه (کمتر از ۳ ساعت) رد شده‌اند،
    تا پایان پنجره در همین اجرای خودکار صبر می‌کنیم — و در همین فاصله
    اگر کاربر کد تازه بفرستد (sec_code.txt) فوراً ادامه می‌دهیم."""
    try:
        st_s, res_s = api(f"/repos/{REPO}/contents/kashano_out/STATUS?ref={OUT_BRANCH}")
        if st_s != 200 or not res_s.get("content"):
            return
        prev = base64.b64decode(res_s["content"]).decode().strip()
        if prev.split(" ")[0] != "SEC_CODE_REJECTED_ALL":
            return
        t0 = None
        parts = prev.split(" ")
        if len(parts) > 1:
            try:
                t0 = datetime.fromisoformat(parts[1].replace("Z", "+00:00")).timestamp()
            except Exception:
                t0 = None
        if t0 is None:
            t0 = time.time() - (3 * 3600 - 20 * 60)  # بدون زمان دقیق: ۲۰ دقیقه صبر
        end_s = t0 + 3 * 3600
        if end_s <= time.time():
            log("پنجرهٔ ۳ ساعتهٔ کد امنیتی تمام شده — ادامهٔ عادی")
            return
        log(f"پنجرهٔ کد امنیتی تا {time.strftime('%H:%M:%S', time.gmtime(end_s))}Z فعال — "
            f"منتظر می‌مانم (کد تازهٔ کاربر را هر ۳۰ ثانیه چک می‌کنم)")
        seen = set()
        while time.time() < end_s:
            try:
                st_k, f_k = api(f"/repos/{REPO}/contents/sec_code.txt?ref={OTP_BRANCH}")
                if st_k == 200 and f_k.get("content"):
                    code = base64.b64decode(f_k["content"]).decode().strip()
                    if code and code not in seen:
                        seen.add(code)
                        log(f"کد تازه از کاربر رسید: {code} — ادامهٔ فوری")
                        return
            except Exception:
                pass
            time.sleep(30)
        log("پنجره تمام شد — ادامهٔ عادی")
    except Exception as e:
        log("خطا در چک پنجرهٔ کد امنیتی:", e)


def main():
    from playwright.sync_api import sync_playwright
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    if busy_check():
        log("اجرای دیگری فعال است — این نوبت را رها می‌کنم (و زنجیره را تحریک نمی‌کنم)")
        sys.exit(3)

    _pause_if_sec_window()

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

    def attempt_login(browser, px, tag):
        """یک تلاش کاملِ ورود با یک پروکسی. (ctx, page) برمی‌گرداند یا None."""
        log(f"── تلاش {tag} با پروکسی: {px or 'مستقیم'}")
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
                log("  ❌ بلاک IP در صفحهٔ ورود")
                c.close()
                return None
            if p.locator("input:visible").count() == 0:
                log("  ؟ فیلدی پیدا نشد")
                c.close()
                return None
            log("  ✅ صفحهٔ واقعیِ signin")
            try_support_form(p)
            if is_blocked(p):
                log("  ❌ بعد از فرم تماس بلاک شد")
                c.close()
                return None
            inp = p.locator("input:visible").first
            inp.click()
            inp.fill("")
            inp.type(PHONE, delay=50)
            time.sleep(1)
            p.get_by_role("button", name="ادامه").click()
            log("  شماره فرستاده شد")
            time.sleep(15)
            if is_blocked(p):
                log("  ❌ بلاک IP پس از ارسال شماره (پروکسی بیرون افتاد)")
                capture_diag(p, f"_blocked{tag}")
                c.close()
                return None
            capture_diag(p, f"_step2{tag}")
            body2 = p.inner_text("body")
            if "رمز عبور" not in body2:
                log("  ؟ مرحلهٔ ۲ رمز عبور نبود")
                capture_diag(p, f"_unexpected{tag}")
                c.close()
                return None
            log(f"  مرحلهٔ رمز — {PIN} را وارد می‌کنم")
            enter_password(p, PIN)
            body3 = p.inner_text("body")
            capture_diag(p, f"_afterpw{tag}")
            if "احراز IP" not in body3 and "دفتر املاک" not in body3:
                log("  ❌ صفحهٔ احراز دفتر نیامد")
                c.close()
                return None
            log("  صفحهٔ احراز IP دفتر — مرحلهٔ ۱: کد امنیتی")
            # کاندیداهای کد امنیتی
            sec_cands = []
            st_sec, res_sec = api(f"/repos/{REPO}/contents/sec_code.txt?ref={OTP_BRANCH}")
            if st_sec == 200 and res_sec.get("content"):
                v = base64.b64decode(res_sec["content"]).decode().strip()
                sec_cands.extend(l.strip() for l in v.splitlines() if l.strip())
                try:
                    api(f"/repos/{REPO}/contents/sec_code.txt?ref={OTP_BRANCH}",
                        method="DELETE", payload={"sha": res_sec["sha"], "message": "consumed"})
                except Exception:
                    pass
            for cand in ("98920123124", "989201231249", "9201231249", "09201231249", PIN):
                if cand not in sec_cands:
                    sec_cands.append(cand)
            log(f"  {len(sec_cands)} کاندید برای کد امنیتی")
            ins = p.locator("input:visible")
            accepted = None
            for cand in sec_cands[:8]:
                if ins.count() >= 1:
                    ins.nth(0).click()
                    ins.nth(0).fill("")
                    ins.nth(0).type(cand, delay=60)
                time.sleep(1)
                for loc in (p.get_by_role("button", name="درخواست پیامک"),
                            p.get_by_text("درخواست پیامک"),
                            p.get_by_text("درخواست کد")):
                    if loc.count():
                        try:
                            loc.first.click()
                            break
                        except Exception:
                            pass
                time.sleep(9)
                errt = p.inner_text("body")
                if (("صحیح نیست" in errt) or ("نادرست" in errt) or ("غلط" in errt)
                        or ("نامعتبر" in errt) or ("شکست" in errt) or ("اجباری" in errt)):
                    log(f"  کد {cand} رد شد")
                    continue
                accepted = cand
                log(f"  ✅ کد امنیتی پذیرفته شد: {cand}")
                break
            capture_diag(p, f"_sec_tried{tag}")
            push_out(f"_sec{tag}")
            if not accepted:
                (OUT_DIR / "STATUS").write_text(
                    "SEC_CODE_REJECTED_ALL " + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                log("  ❌ هیچ کاندیدی پذیرفته نشد")
                push_out(f"_secbad{tag}")
                c.close()
                return "STOP"
            log("  مرحلهٔ ۲: منتظر کدِ پیامک از کاربر")
            codes = wait_user_codes(p, timeout_s=1200)
            if not codes:
                log("  کدی نرسید")
                c.close()
                return None
            _, sms = codes
            enter_office_codes(p, "", sms)
            capture_diag(p, f"_afteroffice{tag}")
            push_out(f"_office{tag}")
            if login_ok(p):
                log(f"  ✅✅ وارد شدیم (تلاش {tag})")
                return (c, p)
            log("  تلاش اول احراز جواب نداد — ۴ دقیقه منتظر کد تازه")
            codes2 = wait_user_codes(p, timeout_s=240)
            if codes2:
                _, sms = codes2
                enter_office_codes(p, "", sms)
                capture_diag(p, f"_afteroffice2{tag}")
                push_out(f"_office2{tag}")
                if login_ok(p):
                    log(f"  ✅✅ وارد شدیم (تلاش {tag}، کد دوم)")
                    return (c, p)
            log("  ❌ ورود با این پروکسی به نتیجه نرسید")
            c.close()
            return None
        except Exception as e:
            log(f"  خطا در تلاش {tag}: {type(e).__name__}: {str(e)[:150]}")
            if c:
                try:
                    c.close()
                except Exception:
                    pass
            return None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = page = None
        fresh = pick_working_proxies(limit=12)
        pool = fresh if fresh else PROXIES
        log(f"استخر پروکسی: {len(pool)}")
        attempts = []
        for rnd in range(3):
            for i, px in enumerate(pool):
                attempts.append((px, f"r{rnd}p{i}"))
        for px, tag in attempts:
            res = attempt_login(browser, px, tag)
            if res == "STOP":
                log("تلاش‌های بیشتر بی‌فایده است — توقف")
                break
            if res:
                ctx, page = res
                break
            time.sleep(8)
        if not ctx:
            if not (OUT_DIR / "STATUS").exists():
                (OUT_DIR / "STATUS").write_text("ALL_PROXIES_FAILED")
            log("❌ به نتیجه نرسید")
            push_out("_allfailed")
            browser.close()
            return

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
