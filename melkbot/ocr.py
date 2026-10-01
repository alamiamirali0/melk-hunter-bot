# -*- coding: utf-8 -*-
"""
خواندن شماره‌ها از عکس (OCR) برای بخش «📷 افزودن از عکس».

موتور: tesseract با زبان فارسی + انگلیسی (خود tesseract v5 موتور شبکهٔ
عصبی است — سبک‌ترین «هوش مصنوعی» که روی CPU رایگان گیت‌هاب به‌خوبی کار می‌کند).

نکتهٔ مهم: برای متن چاپی (اسکرین‌شات، فهرست تایپی، کارت ویزیت) خوب است.
برای دست‌خطِ دفترچه «حالت قوی» فعال است: همهٔ نسخه‌های تصویر خوانده شده و
نتایج *با رأی‌گیری بر پایهٔ شماره* ادغام می‌شوند — شماره‌ای که در چند خوانش
تکرار شود «مطمئن» است؛ موارد یک‌بار-دیده‌شده ⚠️ می‌خورند. چون هرچه از عکس
درآمد *قبل از افزودن* به کاربر نشان داده می‌شود، خطاها قابل‌رفع‌اند.

طرح استخراج (سریع + دقیق):
  ۱) پیش‌پردازش: خاکستری + کنتراست + سقف ۱۷۰۰px + تیزکردن
  ۲) خوانش‌های متعدد (هر نسخه × PSM 6/11): اصلی، دوعدام، نویز‌لندازی‌شده،
     پرزِ مرکبِ وصل‌شده، معکوس (حالت تیره)، بزرگ‌نمایی ۲x، نوارها (عکس بلند)،
     چرخش (عکس کج) — داخل سقف زمان/فراخوانی
  ۳) چیدن کلمات در خط‌ها و جفت‌کردن خودکار «نام — شماره — یادداشت»
     (نامِ خطِ قبل، اگر خطِ شماره نام ندارد — مثل کارت ویزیت)
  ۴) ادغام: رأی بر پایهٔ شماره + نامِ پرتکرار + cross-check با «eng»
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
# دو خانوادهٔ ارقامِ جدا: فارسی (۰-۹) و عربی (٠-٩) — هرکدام جداگانه نگاشت می‌شوند
DIGIT_MAP = {ord(a): str(i) for i, a in enumerate(FA_DIGITS)}
DIGIT_MAP.update({ord(a): str(i) for i, a in enumerate(AR_DIGITS)})
# کاراکترهای بی‌اثر فارسی (نیم‌فاصله، کشیده، اعراب)
CLEAN = {ord("\u200c"): " ", ord("\u200f"): " ", ord("\u200e"): " ",
         ord("\u0640"): "", ord("\u064b"): "", ord("\u064c"): "", ord("\u064d"): "",
         ord("\u064e"): "", ord("\u064f"): "", ord("\u0650"): "", ord("\u0651"): "",
         ord("\u0652"): ""}

MOBILE_RE = re.compile(r"(?:\+?98|0098|0)?9\d{9}")
LAND_RE = re.compile(r"0\d{2,3}\d{7,8}")
DIGITS_RUN = re.compile(r"[\d\s\-().+]{8,}")


def available() -> bool:
    """آیا موتور OCR روی این سرور نصب است؟"""
    return shutil.which("tesseract") is not None


def to_en(s: str) -> str:
    return s.translate(DIGIT_MAP)


def clean_line(s: str) -> str:
    s = s.translate(CLEAN)
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


def read_image(path: str | Path, langs: str = "fas+eng", psm: int = 6) -> str:
    """متن عکس را یک بار می‌خواند (سبک و سریع)."""
    return read_image_single(path, langs, psm)


def norm_phone(p: str) -> str:
    """شمارهٔ خام را به قالب ۰۹xxxxxxxxx یا ۰xxx… تبدیل می‌کند."""
    d = re.sub(r"\D", "", to_en(p))
    if d.startswith("0098"):
        d = "0" + d[4:]
    elif d.startswith("98") and len(d) >= 12:
        d = "0" + d[2:]
    if len(d) == 10 and d.startswith("9"):
        d = "0" + d
    return d


def is_phone(d: str) -> bool:
    return (len(d) == 11 and d.startswith("09")) or (len(d) in (10, 11) and d.startswith("0") and not d.startswith("09"))


def extract_items(text: str) -> list[dict]:
    """از متن خوانده‌شده، لیست «نام — شماره» می‌سازد (بی‌تکرار)."""
    items: list[dict] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = clean_line(to_en(raw_line))
        if not line:
            continue
        # هر شمارهٔ ممکن در خط
        candidates: list[str] = []
        for m in MOBILE_RE.findall(line.replace(" ", "")):
            candidates.append(m)
        for m in LAND_RE.findall(line.replace(" ", "")):
            candidates.append(m)
        if not candidates:
            continue
        # نام = همان خط، بدون شماره‌ها و علائم
        name = line
        for c in candidates:
            name = name.replace(c, " ")
        name = DIGITS_RUN.sub(" ", name)
        name = re.sub(r"[+_=|<>#*]+", " ", name)
        name = re.sub(r"\s{2,}", " ", name).strip(" -–—:.,؛،")
        for c in dict.fromkeys(candidates):
            phone = norm_phone(c)
            if not is_phone(phone) or phone in seen:
                continue
            seen.add(phone)
            items.append({"name": name or "بدون نام", "phone": phone,
                          "tags": "عکس", "source": "عکس"})
    return items


def read_image_votes(path: str | Path) -> dict[str, int]:
    """
    عکس را با چند تنظیم متفاوت می‌خواند و می‌شمارد هر شماره چند بار دیده شده.

    چرا: یک بار خواندن ممکن است یک رقم را غلط ببیند. اگر یک شماره در هر دو/سه
    خوانش تکرار شود، تقریباً قطعی است؛ اگر فقط در یکی بیاید، ⚠️ می‌خورد.
    """
    votes: dict[str, int] = {}
    for langs, psm in (("fas+eng", 6), ("eng", 6)):
        text = read_image_single(path, langs, psm)
        for it in extract_items(text):
            votes[it["phone"]] = votes.get(it["phone"], 0) + 1
        # شماره‌های خام این خوانش هم رأی می‌گیرند (برای تشخیص اختلاف رقم)
    return votes


def read_image_single(path: str | Path, langs: str, psm: int) -> str:
    if not available():
        return ""
    try:
        r = subprocess.run(["tesseract", str(path), "stdout", "-l", langs, "--psm", str(psm)],
                           capture_output=True, text=True, timeout=20)
        return r.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def preprocess(path: str | Path, out: str | Path | None = None, scale: float = 1.5) -> Path:
    """
    عکس را برای خواندن آماده می‌کند. ملایم تا حد ممکن، چون هدف خواندن دقیقِ نام است:
      • عکس خیلی بزرگ → کوچک تا ۱۷۰۰px (سرعت)
      • عکس ریز/کم‌نسبت → بزرگ‌نمایی ملایم ۱.۵x
      • عکس با کیفیت → دست نمی‌خورد (فقط خاکستری + کنتراست ملایم)
    """
    try:
        from PIL import Image, ImageFilter, ImageOps
    except Exception:  # noqa: BLE001
        return Path(path)
    try:
        im = Image.open(path).convert("L")
        im = ImageOps.autocontrast(im, cutoff=1)
        resized = False
        if im.height > 2600:      # اسکرین‌شات/عکس خیلی بلند → کوچک تا ۲۶۰۰px
            im = im.resize((max(480, int(im.width * 2600 / im.height)), 2600),
                           Image.LANCZOS)
            resized = True
        if im.width > 1700:
            im = im.resize((1700, int(im.height * 1700 / im.width)), Image.LANCZOS)
            resized = True
        elif im.width < 1200 and scale > 1.0 and im.height <= 2600:
            im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
            resized = True
        if resized:
            im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))
        out = Path(out or (str(path) + "_clean.png"))
        im.save(out)
        return out
    except Exception:  # noqa: BLE001
        return Path(path)


# -------------------------------------------------- استخراج هوشمند خط‌به‌خط ----
LABEL_WORDS = {
    "موبایل", "تلفن", "تلفنی", "شماره", "محمول", "محموله", "ثابت", "فکس",
    "سرشماره", "شمارهٔ", "پلاک", "tel", "phone", "mobile", "cell", "fax",
    "number", "no", "no.", "landline",
}
PERSIAN_LATIN = re.compile(r"[\u0600-\u06FFA-Za-z]")
DIGIT_WORD = re.compile(r"^[\d+\-()/. ]{2,}$")
PHONE_PATTERNS = [
    re.compile(r"(?<!\d)00989\d{9}(?!\d)"),
    re.compile(r"(?<!\d)09\d{9}(?!\d)"),
    re.compile(r"(?<!\d)989\d{9}(?!\d)"),
    re.compile(r"(?<!\d)9\d{9}(?!\d)"),
    re.compile(r"(?<!\d)0\d{2,3}\d{7,8}(?!\d)"),   # تلفن ثابت
]


def find_phones_in_digits(d: str) -> list[str]:
    """شماره‌های تلفنی موجود در یک زنجیرهٔ ارقام (موبایل اول، سپس ثابت)."""
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    for pat in PHONE_PATTERNS:
        for m in pat.finditer(d):
            if any(not (m.end() <= s or m.start() >= e) for s, e in spans):
                continue
            out.append(m.group())
            spans.append((m.start(), m.end()))
    return out


def find_phones_in_chunks(chunks: list[str]) -> list[str]:
    """
    ارقامِ چند تکهٔ کنار هم (مثلاً «0912 477 5412» یا «021-2245889») را شماره می‌سازد.

    نکتهٔ مهم (RTL): در متون راست‌به‌چپ، ترتیب تکه‌ها — چه کلمه‌های جدا
    («5412 477 0912») چه تکه‌های داخل یک کلمهٔ خط‌دار («2245889-021») — ممکن
    است وارونه خوانده شود. پس ترتیب طبیعی را می‌سازیم؛ اگر شماره‌ای نساخت،
    ترتیب وارونهٔ تکه‌ها را امتحان می‌کنیم.
    """
    subs: list[str] = []
    for c in chunks:
        subs.extend(p for p in re.split(r"[\s\-().+/]+", c) if p)
    if not subs:
        return []
    orders = [subs]
    if len(subs) > 1:
        orders.append(list(reversed(subs)))
    for order in orders:
        ph = find_phones_in_digits("".join(order))
        if ph:
            return ph
    return []


def _meaningful(s: str) -> bool:
    return sum(1 for c in s if PERSIAN_LATIN.match(c)) >= 2


def _clean_text(s: str, maxlen: int) -> str:
    words = []
    for w in s.split():
        w2 = w.strip("|–—-:.,؛،()[]{}«»\"'/ \u200c\u200f")
        if not w2 or w2.lower() in LABEL_WORDS:
            continue
        words.append(w2)
    out = re.sub(r"\s{2,}", " ", " ".join(words)).strip()
    if not _meaningful(out):
        return ""
    return out[:maxlen]


def read_lines_tsv(path: str | Path, langs: str = "fas+eng", psm: int = 6,
                   timeout: float = 12.0) -> list[list[tuple[str, float]]]:
    """
    یک خوانش tesseract به‌صورت TSV: کلمه‌به‌کلمه با درجهٔ اعتماد، گروه‌بندی‌شده
    در خط‌ها (به ترتیب صفحه). فقط یک فراخوانی موتور = سریع.
    """
    if not available():
        return []
    try:
        r = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", langs, "--psm", str(psm), "tsv"],
            capture_output=True, text=True, timeout=timeout)
    except Exception:  # noqa: BLE001
        return []
    lines: dict[tuple, list[tuple[str, float]]] = {}
    order: list[tuple] = []
    for row in r.stdout.splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12:
            continue
        try:
            if int(f[0]) != 5:      # فقط سطح کلمه
                continue
            conf = float(f[10])
        except ValueError:
            continue
        key = (f[1], f[2], f[3], f[4])   # page, block, par, line
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append((f[11], conf))
    return [lines[k] for k in order]


def _rotations(path: str | Path) -> list[Path]:
    """سه چرخش دقیق (۹۰/۱۸۰/۲۷۰) برای عکس‌هایی که کج گرفته شده‌اند."""
    try:
        from PIL import Image
        im = Image.open(path)
    except Exception:  # noqa: BLE001
        return []
    out: list[Path] = []
    for angle, op in (("90", Image.ROTATE_90), ("180", Image.ROTATE_180),
                      ("270", Image.ROTATE_270)):
        p = Path(f"{path}.rot{angle}.png")
        try:
            im.transpose(op).save(p)
            out.append(p)
        except Exception:  # noqa: BLE001
            pass
    return out


# ------------------------------------------------ استراتژی‌های چندگانه ----
# عکس‌های واقعی انواع‌انواخیست: اسکرین‌شات حالت تیره، کاغذ کم‌کنتراست،
# اسکرین‌شات بلند، عکس کج، دفترچهٔ دست‌خط. به‌جای «اولین راهِ برنده»،
# چند نسخه از تصویر می‌سازیم، همه را (تا سقف زمان/فراخوانی) می‌خوانیم و
# نتایج را با رأی‌گیری بر پایهٔ شماره ادغام می‌کنیم.
_BUDGET_CALLS = 15    # سقف فراخوانی‌های tesseract برای هر عکس
_BUDGET_TIME = 11.0   # سقف شروعِ خوانش‌های تازه (ثانیه) — کلِ بخش عکس قطعاً < ۲۵s


def _otsu_threshold(im) -> int:
    """آستانهٔ دوعدام (Otsu) با هیستوگرام PIL — بدون وابستگی اضافی."""
    hist = im.histogram()
    total = sum(hist)
    if not total:
        return 128
    sum_all = float(sum(i * c for i, c in enumerate(hist)))
    sum_b, w_b, best, thr = 0.0, 0, 0.0, 128
    for i, c in enumerate(hist):
        w_b += c
        if not w_b:
            continue
        w_f = total - w_b
        if not w_f:
            break
        sum_b += i * c
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best:
            best, thr = var, i
    return thr


def _variant(base: Path, kind: str) -> Path | None:
    """
    نسخه‌های جایگزین تصویر:
      invert → متن روشن روی زمینهٔ تیره (اسکرین‌شات واتس‌اپ/تلگرام حالت تیره)
      bin    → دوعدام (عکس کاغذ در نور ضعیف / کم‌کنتراست)
      invbin → ترکیب هر دو
    """
    try:
        from PIL import Image, ImageFilter, ImageOps
        im = Image.open(base).convert("L")
    except Exception:  # noqa: BLE001
        return None
    try:
        if kind == "invert":
            im = ImageOps.autocontrast(ImageOps.invert(im))
        elif kind == "bin":
            thr = _otsu_threshold(im)
            im = im.point(lambda p: 255 if p > thr else 0)
        elif kind == "invbin":
            im = ImageOps.invert(im)
            thr = _otsu_threshold(im)
            im = im.point(lambda p: 255 if p > thr else 0)
        elif kind == "denoise":
            # نویزِ دوربین/کاغذ را می‌کَنَد و پرزِ مرکبِ نازک را حفظ می‌کند (دست‌خط)
            im = im.filter(ImageFilter.MedianFilter(3))
            thr = _otsu_threshold(im)
            im = im.point(lambda p: 255 if p > thr else 0)
        elif kind == "close":
            # پرزهای شکستهٔ دست‌خط را به هم وصل می‌کند (dilate ← erode)
            im = im.filter(ImageFilter.MaxFilter(3))
            im = im.filter(ImageFilter.MinFilter(3))
            thr = _otsu_threshold(im)
            im = im.point(lambda p: 255 if p > thr else 0)
        elif kind == "up2":
            # دست‌خطِ ریز را بزرگ می‌کند و دوعضمی می‌کند
            if im.width < 1100:
                im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
            thr = _otsu_threshold(im)
            im = im.point(lambda p: 255 if p > thr else 0)
        else:
            return None
        out = Path(f"{base}.{kind}.png")
        im.save(out)
        return out
    except Exception:  # noqa: BLE001
        return None


def make_strips(base: Path, max_h: int = 2000, step: int = 1700,
                overlap: int = 250, max_strips: int = 8) -> list[Path]:
    """
    اگر تصویر بلند است (اسکرین‌شات طولانی)، نوارهای افقیِ همپوشان می‌سازد؛
    وگرنه خود تصویر را برمی‌گرداند.
    """
    try:
        from PIL import Image
        im = Image.open(base)
    except Exception:  # noqa: BLE001
        return [base]
    if im.height <= max_h:
        return [base]
    out: list[Path] = []
    h, w = im.height, im.width
    y = 0
    while len(out) < max_strips:
        top = 0 if y == 0 else max(0, y - overlap)
        bot = min(y + step, h)
        p = Path(f"{base}.strip{len(out)}.png")
        try:
            im.crop((0, top, w, bot)).save(p)
            out.append(p)
        except Exception:  # noqa: BLE001
            break
        if bot >= h:
            break
        y = bot
    return out


def pair_items(lines: list[list[tuple[str, float]]]) -> list[dict]:
    """
    جفت‌کردن خودکار «نام — شماره — یادداشت» از خط‌های خوانده‌شده.

    قوانین ساده و قابل‌پیش‌بینی:
      • ارقامِ کنار هم در یک خط → شماره (چه فاصله داشته باشد چه نه)
      • نام = کلماتِ فارسی/انگلیسیِ قبل از شماره در همان خط
      • اگر شماره بدون نام باشد، نام از خطِ قبل گرفته می‌شود (کارت ویزیت)
      • کلماتِ بعد از شماره → یادداشت (مثلاً محله) که برچسب می‌شود
    """
    items: list[dict] = []
    seen: set[str] = set()
    pending = ""
    for words in lines:
        cw: list[tuple[str, float]] = []
        for text, conf in words:
            t = clean_line(to_en(text)).strip()
            if t and t not in {"|", "-", "—", "–", ":", "؛", ",", "."}:
                cw.append((t, float(conf)))
        if not cw:
            continue

        # ── شناسایی کلماتِ عددی و شماره‌های داخل کلمه ──
        digit_idx: set[int] = set()
        inline_phones: dict[int, list[str]] = {}
        for i, (t, _conf) in enumerate(cw):
            if DIGIT_WORD.match(t):
                digit_idx.add(i)
                continue
            found = find_phones_in_digits(re.sub(r"\D", "", t))
            if found:
                inline_phones[i] = found
                digit_idx.add(i)

        # ── گروه‌های عددیِ متوالی را به شماره تبدیل می‌کنیم ──
        phones: list[tuple[int, int, str, float]] = []   # (از، تا، شماره، اعتماد)
        used: set[int] = set()
        i = 0
        while i < len(cw):
            if i in inline_phones:
                for ph in inline_phones[i]:
                    phones.append((i, i, norm_phone(ph), cw[i][1]))
                used.add(i)
                i += 1
                continue
            if i in digit_idx and i not in used:
                j = i
                while j + 1 in digit_idx and j + 1 not in used:
                    j += 1
                chunks = [t for t, _c in cw[i:j + 1]]   # خام: جداکننده‌ها می‌مانند
                for ph in find_phones_in_chunks(chunks):
                    phones.append((i, j, norm_phone(ph),
                                   min(c2 for _t, c2 in cw[i:j + 1])))
                used.update(range(i, j + 1))
                i = j + 1
                continue
            i += 1
        phones = [(a, b, p, c) for a, b, p, c in phones if is_phone(p)]

        if phones:
            first = min(a for a, _b, _p, _c in phones)
            last = max(b for _a, b, _p, _c in phones)
            pre = [t for i, (t, _c) in enumerate(cw) if i < first and i not in used]
            post = [t for i, (t, _c) in enumerate(cw) if i > last and i not in used]
            own_name = _clean_text(" ".join(pre), 80)
            if own_name:
                name = own_name
                pending = ""          # خطِ خودِ دارد؛ نامِ خطِ قبل را رها کن
            else:
                name = pending        # چند شماره برای یک نفر (کارت ویزیت)
            note = _clean_text(" ".join(post), 40)
            for _a, _b, ph, conf in phones:
                if ph in seen:
                    continue
                seen.add(ph)
                items.append({
                    "name": name or "بدون نام",
                    "phone": ph,
                    "tags": "عکس" + (f" {note}" if note else ""),
                    "note": note,
                    "source": "عکس",
                    "min_conf": conf,
                })
        else:
            name = _clean_text(" ".join(t for t, _c in cw), 120)
            if name:
                pending = name   # نامِ خطِ بعد (بدون شماره) — مثلِ سرِ کارت ویزیت
    return items


def _edit1(a: str, b: str) -> bool:
    """آیا a و b فقط یک رقم تفاوت دارند (جایگزینی/افزودن/کوتاه‌شدن یک رقم)?"""
    la, lb = len(a), len(b)
    if la == lb:
        if la == 0:
            return False
        return sum(x != y for x, y in zip(a, b)) <= 1
    if abs(la - lb) != 1:
        return False
    if la < lb:
        a, b, la, lb = b, a, lb, la
    i = j = 0
    skipped = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            if skipped:
                return False
            skipped = True
            i += 1
    return True


def extract_with_reliability(path: str | Path) -> list[dict]:
    """
    نتیجهٔ نهایی: لیست «نام — شماره» (با یادداشت/برچسب محله و درجهٔ اطمینان).

    حالت قوی (چنداستراتژی + رأی‌گیری) — برای اسکرین‌شات، فهرست تایپی،
    کارت ویزیت و دفترچهٔ دست‌خط:
      به‌جای «اولین استراتژیِ برنده»، نسخه‌های تصویر داخلِ سقف
      زمان/فراخوانی خوانده می‌شوند و نتایج بر پایهٔ شماره ادغام می‌شوند:
        ۱) تصویر اصلی با PSM ۶ (بلوک) / ۳ (خودکار — حتی عکسِ کج) / ۱۱ (پراکنده)
        ۲) چرخش‌های ۹۰/۱۸۰/۲۷۰ — فقط وقتی تقریباً چیزی درنیامد
        ۳) نوارهای همپوشان — اسکرین‌شات بلند
        ۴) نسخه‌های جایگزین: دوعظمی / نویز‌لندازی / وصل‌پرز / معکوس /
           بزرگ‌نمایی — برای عکسِ تیره و دست‌خطِ ریز
      رأی: شماره‌ای که در ≥۲ خوانش تکرار شود «مطمئن»؛ یک‌بار → ⚠️
      نام: پرتکرارترین نامِ همان شماره در همهٔ خوانش‌ها.
      توقفِ زودهنگام: خوانشِ تمیز (≥۵ شماره با اعتماد ≥۹۰) → بقیهٔ نسخه‌ها
      خوانده نمی‌شوند (عکس‌های چاپی همان سرعتِ قبل).
      سقف: ۱۵ فراخوانی یا ۱۱ ثانیه (کلِ بخش عکس < ۲۵s).
    """
    t0 = time.monotonic()
    calls = {"n": 0}
    base = preprocess(path)
    temps: list[Path] = []
    found: list[dict] = []

    def budget_ok() -> bool:
        return calls["n"] < _BUDGET_CALLS and (time.monotonic() - t0) <= _BUDGET_TIME

    def distinct() -> int:
        return len({it["phone"] for it in found})

    def mean_conf() -> float:
        if not found:
            return 0.0
        return sum(float(it.get("min_conf", 0)) for it in found) / len(found)

    def strong_enough() -> bool:
        """خوانشِ تمیز: چند شمارهٔ بااعتماد → نیازی به نسخه‌های دیگر نیست."""
        return distinct() >= 5 and mean_conf() >= 90

    def read_img(img: Path, psm: str) -> int:
        """یک خوانش؛ نتایجِ تازه به found اضافه می‌شوند. تعدادِ این بار."""
        if not budget_ok():
            return 0
        calls["n"] += 1
        items = pair_items(read_lines_tsv(img, "fas+eng", psm))
        known = {it["phone"] for it in found}
        found.extend(it for it in items if it["phone"] not in known)
        return len(items)

    def cleanup() -> None:
        for pt in temps:
            try:
                pt.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

    try:
        # ۱) تصویر اصلی: PSM ۶ (بلوک یکنواخت)؛ اگر کم ماند، ۳ (خودکار) و ۱۱ (پراکنده)
        read_img(base, "6")
        if distinct() < 5:
            read_img(base, "3")
        if distinct() < 5:
            read_img(base, "11")

        # ۲) چرخش — فقط وقتی تقریباً چیزی درنیامده (عکس کج/چند)
        if distinct() <= 1:
            for rot in _rotations(base):
                temps.append(rot)
                read_img(rot, "6")
                if distinct() >= 3 or strong_enough():
                    break

        # ۳) اسکرین‌شات بلند → نوارهای همپوشان
        if distinct() < 8:
            bin_v = _variant(base, "bin")
            if bin_v:
                temps.append(bin_v)
            for src in ([base, bin_v] if bin_v else [base]):
                for s in make_strips(src)[1:]:
                    temps.append(s)
                    read_img(s, "6")
                    if strong_enough():
                        break
                if strong_enough():
                    break

        # ۴) نسخه‌های جایگزین — رأی‌گیری (عکس تیره/کم‌نور، دست‌خط ریز، کاغذ)
        if not strong_enough():
            for k in ("bin", "denoise", "close", "invert", "up2", "invbin"):
                v = _variant(base, k)
                if not v:
                    continue
                temps.append(v)
                read_img(v, "6")
                if k in ("bin", "denoise"):     # PSM ۱۱ هم برای این‌ها
                    read_img(v, "11")
                if strong_enough():
                    break

        if not found:
            return []

        # ۵) cross-check با eng (یک خوانش اضافه — فقط ارقام انگلیسی)
        eng_phones: set[str] = set()
        if calls["n"] < _BUDGET_CALLS + 2 and (time.monotonic() - t0) <= _BUDGET_TIME:
            calls["n"] += 1
            for it in pair_items(read_lines_tsv(base, "eng")):
                eng_phones.add(it["phone"])

        # ۶) ادغام بر پایهٔ شماره: رأی + نامِ پرتکرار + بلندترین یادداشت
        recs: dict[str, dict] = {}
        order: list[str] = []
        for it in found:
            ph = it["phone"]
            r = recs.get(ph)
            if r is None:
                r = {"names": Counter(), "votes": 0, "conf": 0.0, "note": ""}
                recs[ph] = r
                order.append(ph)
            r["votes"] += 1
            r["conf"] = max(r["conf"], float(it.get("min_conf", 0)))
            nm = it.get("name", "")
            if nm and nm != "بدون نام":
                r["names"][nm] += 1
            note = it.get("note", "")
            if len(note) > len(r["note"]):
                r["note"] = note

        # ۷) ادغامِ شکلی: همان شمارهٔ واقعی که در یک خوانش یک رقمش افتاده/اضافه
        #    شده (خطای رایج OCR) → شماره‌ای با رأی بیشتر، کمتر را می‌بلعد.
        for a in order:
            if a not in recs:
                continue
            for b in order:
                if b == a or b not in recs or b == a:
                    continue
                ra, rb = recs[a], recs[b]
                same_len = len(a) == len(b)
                prefix = a.startswith(b) or b.startswith(a)
                if not prefix or abs(len(a) - len(b)) > 1 or same_len:
                    continue
                if ra["votes"] > rb["votes"]:
                    ra["votes"] += rb["votes"]
                    ra["conf"] = max(ra["conf"], rb["conf"])
                    for nm, c in rb["names"].items():
                        ra["names"][nm] += c
                    if len(rb["note"]) > len(ra["note"]):
                        ra["note"] = rb["note"]
                    del recs[b]
                elif rb["votes"] > ra["votes"]:
                    rb["votes"] += ra["votes"]
                    rb["conf"] = max(rb["conf"], ra["conf"])
                    for nm, c in ra["names"].items():
                        rb["names"][nm] += c
                    if len(ra["note"]) > len(rb["note"]):
                        rb["note"] = ra["note"]
                    del recs[a]
                    break

        # ۸) حذفِ «دوقلویِ ضعیف»: شمارهٔ تک‌رأیِ کم‌اعتماد که فقط یک رقم با
        #    شمارهٔ مطمئن تفاوت دارد → تقریباً قطعاً همان شمارهٔ واقعی است.
        anchors = [ph for ph in order
                   if ph in recs and (recs[ph]["votes"] >= 2 or recs[ph]["conf"] >= 85)]
        for b in order:
            if b not in recs:
                continue
            rb = recs[b]
            if rb["votes"] >= 2 or rb["conf"] >= 85:
                continue
            for a in anchors:
                if a != b and _edit1(a, b):
                    del recs[b]
                    break

        out: list[dict] = []
        for ph in order:
            if ph not in recs:
                continue
            r = recs[ph]
            name = r["names"].most_common(1)[0][0] if r["names"] else "بدون نام"
            sure = r["votes"] >= 2 or ph in eng_phones or r["conf"] >= 90
            out.append({
                "name": name,
                "phone": ph,
                "tags": "عکس" + (f" {r['note']}" if r["note"] else ""),
                "note": r["note"],
                "source": "عکس",
                "votes": r["votes"],
                "sure": bool(sure),
                "min_conf": r["conf"],
            })
            if len(out) >= 300:
                break
        return out
    finally:
        cleanup()


def preview_lines(items: list[dict], limit: int = 25) -> str:
    """پیش‌نمایش خوانا برای تأیید کاربر؛ موارد نامطمئن با ⚠️ علامت می‌خورند."""
    out = []
    for i, it in enumerate(items[:limit], 1):
        mark = "" if it.get("sure", True) else " ⚠️"
        note = f" ({it['note']})" if it.get("note") else ""
        addr = f" ({it['address'][:80]})" if it.get("address") else ""
        out.append(f"{i}. {it['name']}{addr} — {it['phone']}{note}{mark}")
    if len(items) > limit:
        out.append(f"… و {len(items) - limit} مورد دیگر")
    return "\n".join(out)


def counts(items: list[dict]) -> tuple[int, int]:
    sure = sum(1 for it in items if it.get("sure", True))
    return sure, len(items) - sure
