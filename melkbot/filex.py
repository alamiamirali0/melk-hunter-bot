# -*- coding: utf-8 -*-
"""
ورود مخاطبین از فایل: PDF (متنی یا اسکن‌شده) و Word (docx).

• PDF متنی   → pdftotext → پارسر خط‌به‌خط (همان /bulk)
• PDF اسکنی  → رندر صفحات با pdftoppm → همان موتور OCR بخش «افزودن از عکس»
• DOCX       → متن پاراگراف‌ها و جدول‌ها → پارسر خط‌به‌خط

همهٔ خروجی‌ها یک لیست «نام/شماره/برچسب» برمی‌گردانند که ربات
*قبل از افزودن* به کاربر نشان می‌دهد.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from melkbot import ocr
from melkbot.normalize import digits_only

MAX_OCR_PAGES = 5      # برای PDF اسکن‌شده، حداکثر چند صفحه را OCR کنیم
MAX_ITEMS = 600        # سقف مخاطبینِ یک بارِ ورود (فایل‌های بزرگ ۱۰+ صفحه‌ای)


# ---------------------------------------------------------------- پارسر ----
_BIDI_MARKS = re.compile(r"[\u200e\u200f\u2066-\u2069\u202a-\u202e]")
_DIGITISH = r"[0-9۰-۹٠-٩+\-().\u200c ]+"


def _fix_rtl_line(line: str) -> str:
    """
    خروجی pdftotext (-raw) از متون فارسی: ترتیب کلمات منطقی است ولی حروف
    داخل هر کلمهٔ فارسی وارونه نوشته‌شده‌اند (با نشان‌های bidi). اگر این
    نشان‌ها هست، حروفِ هر تکهٔ فارسی را بازمی‌گردانیم؛ ارقام (لاتین/فارسی)
    و کلمه‌های انگلیسی دست نمی‌خورند.
    """
    if not _BIDI_MARKS.search(line):
        return line
    line = _BIDI_MARKS.sub(" ", line)
    fixed = []
    for tok in line.split():
        if re.fullmatch(_DIGITISH, tok):
            fixed.append(tok)
            continue
        parts = re.split(r"(" + _DIGITISH + r")", tok)
        fixed.append("".join(
            p[::-1] if re.search(r"[\u0600-\u06FF]", p) else p
            for p in parts if p))
    return " ".join(fixed)


def parse_txt_list(text: str, source: str = "bulk") -> list[dict]:
    """خط‌به‌خط: «نام | شماره | برچسب» · «نام — شماره» · «1) نام — شماره» · فقط شماره."""
    out: list[dict] = []
    for raw_line in text.splitlines():
        line = _fix_rtl_line(raw_line).strip()
        if not line:
            continue
        m = re.match(r"^\d+[.)]\s*(.+?)\s*[—–-]{1,2}\s*(.+)$", line)
        if m:
            out.append({"name": m.group(1).strip(), "phone": m.group(2).strip(),
                        "source": source})
            continue
        m = re.match(r"^(.+?)\s*[|,;،\t]\s*(\+?[\d\s\-()]{6,})\s*(?:[|]\s*(.*))?$", line)
        if not m:
            m = re.match(r"^(.+?)\s*[—–]{1,2}\s*(\+?[\d\s\-()]{6,})\s*(?:[|]\s*(.*))?$", line)
        if m:
            out.append({"name": m.group(1).strip(), "phone": m.group(2).strip(),
                        "tags": (m.group(3) or "").strip(), "source": source})
            continue
        d = digits_only(line)
        if len(d) >= 8:
            nm = re.sub(r"[\d+()\-\s۰-۹٠-٩]{6,}", " ", line).strip(" ,;-")
            out.append({"name": nm or "بدون نام", "phone": line, "source": source})
    return out


def _dedupe(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        d = ocr.to_en(re.sub(r"\D", "", it.get("phone", "")))
        if len(d) < 6 or d in seen:
            continue
        seen.add(d)
        out.append(it)
        if len(out) >= MAX_ITEMS:
            break
    return out


# ------------------------------------------------------------------ DOCX ----
def extract_docx(path: str | Path) -> list[dict]:
    """متن پاراگراف‌ها و جدول‌های Word را می‌گیرد و پارس می‌کند."""
    try:
        from docx import Document
    except ImportError:
        return []
    try:
        doc = Document(str(path))
    except Exception:  # noqa: BLE001
        return []
    lines: list[str] = []
    for p in doc.paragraphs:
        if p.text.strip():
            lines.append(p.text.strip())
    for tbl in doc.tables:
        for row in tbl.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return _dedupe(parse_txt_list("\n".join(lines), source="فایل"))


# -------------------------------------------------------------------- PDF ----
def has_pdftools() -> bool:
    return shutil.which("pdftotext") is not None and shutil.which("pdftoppm") is not None


def _pdftotext(path: Path, mode: str) -> str:
    """mode: "-raw" (ترتیب جریان) یا "-layout" (حفظ ستون‌ها و خطوط)."""
    try:
        r = subprocess.run(["pdftotext", mode, "-enc", "UTF-8", str(path), "-"],
                           capture_output=True, text=True, timeout=60)
        return r.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def _strip_bidi(text: str) -> str:
    """نشان‌های جهتی را پاک می‌کند (زونجِ داخل کلمه می‌ماند)."""
    return _BIDI_MARKS.sub(" ", text)


def _rev_line(line: str) -> str:
    """حروفِ تکه‌های فارسی را وارونه می‌کند (برای PDFهایی که fpdf2 و
    بعضی ابزارها حروف را به ترتیب نمایش می‌نویسند). ارقام دست نمی‌خورند."""
    fixed = []
    for tok in line.split():
        if re.fullmatch(_DIGITISH, tok):
            fixed.append(tok)
            continue
        parts = re.split(r"(" + _DIGITISH + r")", tok)
        fixed.append("".join(
            p[::-1] if re.search(r"[\u0600-\u06FF]", p) else p
            for p in parts if p))
    return " ".join(fixed)


def _rev_text(text: str) -> str:
    return "\n".join(_rev_line(l) for l in text.splitlines())


# کلمات پرتکرار فارسی — برای انتخاب «تنوع درست» متن (املا درست = نمرهٔ بالا)
_COMMON_FA = (
    # محل / خیابان
    "کوچه", "پالک", "پلاک", "بزرگراه", "بلوار", "میدان", "نبش", "تقاطع", "خیابان",
    "مجتمع", "بلوک", "واحد", "طبقه", "ساختمان", "مسکونی", "تجاری", "اداری",
    "ویلا", "آپارتمان", "مغازه", "ملک", "زمین", "پارکینگ", "انباری", "پشت‌بام",
    "شهرک", "منطقه", "فاز", "قطعه", "پروژه", "تهران",
    # ملک / قرارداد
    "سند", "قولنامه", "تصرف", "املاک", "مشاور", "مشاوره", "مشاوران", "پیمانکار",
    "سازنده", "مجری", "شرکت", "بازاریابی", "فروش", "اجاره", "خرید", "کلنگی",
    "متری", "متر", "متراژ", "ملیارد", "هزار", "رهن", "همکار", "همکاران",
    # ربط / جهت
    "و", "از", "بین", "نزدیک", "مقابل", "کنار", "پشت", "سمت", "پایین‌تر", "بالاتر",
    "شمالی", "جنوبی", "شرقی", "غربی", "شمال", "جنوب", "شرق", "غرب", "اول", "دوم",
    "سوم", "نوبخت", "دیوار", "کارت",
    # نام‌های خانوادگی پرتکرار
    "حسینی", "محمدی", "احمدی", "رضایی", "کریمی", "موسوی", "جعفری", "حسنی",
    "توکلی", "ابراهیمی", "فراهانی", "معصومی", "مقدم", "شهبازی", "سعادتی",
    "فضلی", "علمدار", "بزرگی", "قربانی", "امینیان", "همدانی", "فکوری",
    "جمالزاده", "نظامی", "قریب", "طوسی", "مظفری", "کوهک", "وفا", "نیک‌پور",
    "صادقی", "قاسمی", "رحیمی", "نجفی", "کاظمی", "غلامی", "عزت‌الله", "زارعی",
)


def _fa_score(items: list[dict]) -> int:
    """نمرهٔ «فارسی درست بودن» نام‌ها: هر بار که کلمهٔ پرتکرار درست دیده شود
    (یعنی حروف وارونه نشده باشند) نمره می‌گیرد."""
    blob = " ".join(it.get("name", "") for it in items)
    return sum(blob.count(w) for w in _COMMON_FA)


_MOBILE_RE = re.compile(r"(?<!\d)(09\d{9,10})(?!\d)")


def _clean_name(name: str, limit: int = 60) -> str:
    name = re.sub(r"[\u200c]+$", "", name)          # زونجِ آویزهٔ آخر
    name = re.sub(r"^[^0-9A-Za-z\u0600-\u06FF]+", "", name)  # نشانه‌های اول
    name = name.replace("(", " ").replace(")", " ")
    name = re.sub(r"\s+", " ", name).strip(" -–,;،.")
    return name[:limit]


def _name_near_phone(line: str, phone: str) -> str:
    """نامِ همراهِ یک شماره را از همان خط می‌گیرد (الگوهای رایج)."""
    pe = re.escape(phone)

    def _g(m) -> str:
        # نامِ پیدا‌شده باید حداقل یک حرفِ فارسی داشته باشد
        if m and _HAS_FA.search(m.group(1)):
            return m.group(1)
        return ""

    # A) «) نام ( شماره» — رایج‌ترین الگوی جدول‌های اکسل
    n = _g(re.search(r"\)\s*([^()|,;،]{2,40}?)\s*\(\s*" + pe + r"\b", line))
    if n:
        return n
    # B) «نام ( شماره» — پرانتز بستهٔ خالی هم ممکن است: «نام ( ) شماره»
    n = _g(re.search(r"([\u0600-\u06FF][\u0600-\u06FF\u200c\- ]{1,39})\s*\(\s*\)?\s*" + pe + r"\b", line))
    if n:
        return n
    # J) «( نام ) شماره» — نام داخل پرانتز، شماره بیرون
    n = _g(re.search(r"\(\s*([\u0600-\u06FF][\u0600-\u06FF\u200c\- ]{1,39}?)\s*\)\s*\)?\s*" + pe + r"\b", line))
    if n:
        return n
    # C) «شماره ( نام» — شماره اول خط
    n = _g(re.match(r"\s*" + pe + r"\s*\(\s*([^()|,;،]{2,40}?)\s*\)?\s*$", line))
    if n:
        return n
    # D) «شماره نام» — ساده
    n = _g(re.match(r"\s*" + pe + r"\s+([\u0600-\u06FF][\u0600-\u06FF\u200c\- ]{1,39})\s*$", line))
    if n:
        return n
    # E) «شماره ... نام ردیف» — ستونِ نامِ جدول (پایین خط، پیش از شمارهٔ ردیف)
    n = _g(re.search(r"\b" + pe + r"[^\u0600-\u06FF]{0,6}([\u0600-\u06FF][\u0600-\u06FF\u200c\- ]{0,28})\s*\d{1,3}\s*$", line))
    if n:
        return n
    # G) «نام ) شماره» یا «نام شماره» — شماره آخر خط، پرانتز ممکن است تک و بسته باشد
    n = _g(re.search(r"([\u0600-\u06FF][\u0600-\u06FF\u200c\- ]{1,39})\s*\)?\s*" + pe + r"\s*$", line))
    if n:
        return n
    # H) «( شماره نام» — نام بعد از شماره
    n = _g(re.search(r"\(\s*" + pe + r"\s+([\u0600-\u06FF][\u0600-\u06FF\u200c\- ]{1,39})\s*$", line))
    if n:
        return n
    return ""


def _cells(line: str) -> list[tuple[int, str]]:
    """تکه‌های خط را بر اساس فاصلهٔ بزرگ (۲+ فاصله = مرز ستون) با ستونِ شروع می‌دهد."""
    return [(m.start(), m.group(0).strip()) for m in re.finditer(r"\S+(?: \S+)*", line)]


_HAS_FA = re.compile(r"[\u0600-\u06FF]")
_PURE_NUM = re.compile(r"[\d۰-۹٠-٩+\-().\u200c ]+$")
_NAME_STOP = {
    "تلفن همراه", "تلفن ثابت", "تلفن", "همراه", "ثابت", "شماره", "آدرس",
    "نام", "ردیف", "سازنده", "نام سازنده", "عنوان", "شرح",
}


def _in_name_cluster(tt: str, name: str) -> bool:
    """آیا این تکه، بخشی از خوشهٔ نام/شماره است (نه آدرس)؟"""
    if not name or len(tt) < 2:
        return False
    if tt == name or tt in name or name in tt:
        return True
    return name.startswith(tt) or tt.startswith(name)


def parse_table_text(text: str, source: str = "فایل") -> list[dict]:
    """
    پارسر سِریع برای متن PDF (جدول و لیست):
      • شمارهٔ موبایل لنگر است؛ نام از همان خط (الگوهای A–F)
      • در جدول‌ها، سطرهای بسته‌شده (wrap) به سطرِ شمارهٔشان پیوست می‌شوند
        و متنِ ستون‌های چپ (آدرس) برمی‌دارد و در item["address"] می‌گذارد
      • خطوط مستقلِ «نام | شماره» / «نام — شماره» / فقط-شماره هم می‌خورند
    """
    text = _strip_bidi(text)
    lines = [ln.replace("\t", " ") for ln in text.splitlines()]
    cell_lines = [(ln, _cells(ln)) for ln in lines]

    # گروه‌بندی سطر‌ها: خطی که موبایل دارد سطرِ تازه است
    rows: list[dict] = []
    cur: dict | None = None
    for idx, (line, cells) in enumerate(cell_lines):
        if _MOBILE_RE.search(line):
            cur = {"idxs": [idx], "phones": _MOBILE_RE.findall(line), "thresh": None, "borrow": None}
            for col, t in cells:
                if _MOBILE_RE.search(t) and (cur["thresh"] is None or col < cur["thresh"]):
                    cur["thresh"] = col
            rows.append(cur)
        elif cur is not None and line.strip():
            cur["idxs"].append(idx)

    # مشخصات هر سطر: آیا خطِ شماره، آدرسِ چپِ واقعی دارد؟
    for row in rows:
        th = (row["thresh"] if row["thresh"] is not None else 10 ** 9) - 2
        row["has_left"] = any(
            col < th and _HAS_FA.search(t) and not _PURE_NUM.match(t)
            and not re.search(r"[()]", t)  # تکه‌های پرانتزی = خوشهٔ نام/شماره
            for col, t in cell_lines[row["idxs"][0]][1])

    def _left_farsi_any(li: int) -> bool:
        return any(
            _HAS_FA.search(t) and not _PURE_NUM.match(t)
            for _, t in cell_lines[li][1])

    LONG_LINE = 35  # آستانهٔ «خطِ پُر» — سرریزِ بالایِ آدرس
    # فازِ ۱: مالکیتِ «خطِ بالایِ سطر» (قبل از جمع‌آوری آدرس)
    # خطِ پُرِ بالایِ سطر = سرریزِ بالایِ آدرسِ همین سطر؛ خطِ کوتاه = ادامهٔ سطرِ قبل
    for ri, row in enumerate(rows):
        row["borrow_above"] = None
        if row["has_left"]:
            continue
        L = row["idxs"][0] - 1
        if L < 0 or not cell_lines[L][1] or _MOBILE_RE.search(cell_lines[L][0]):
            continue
        in_prev = ri > 0 and L in rows[ri - 1]["idxs"]
        if in_prev and rows[ri - 1]["has_left"]:
            take = True  # خطِ واغل از سطرِ قبلی
        else:
            take = len(cell_lines[L][0].strip()) >= LONG_LINE
        if take and _left_farsi_any(L):
            row["borrow_above"] = L
            if in_prev:
                rows[ri - 1]["idxs"].remove(L)  # آن خط مالِ این سطر است

    # خطوطِ مستقل (بدون موبایل و جزء هیچ سطر نبوده‌اند)
    row_line_ids = {li for r in rows for li in r["idxs"]}

    out: list[dict] = []
    seen_phones: set[str] = set()

    prev_row_name = ""

    for ri, row in enumerate(rows):
        idxs = row["idxs"]
        thresh = (row["thresh"] if row["thresh"] is not None else 10 ** 9) - 2
        li0 = idxs[0]
        first_line = cell_lines[li0][0]
        phones = list(dict.fromkeys(row["phones"]))

        # ۱) نامِ هر شماره + نامِ سطحِ سطر (برای سطرهای چندشماره‌ای مثل تلفن‌های دوگانه)
        names = {ph: _clean_name(_name_near_phone(first_line, ph)) for ph in phones}
        row_name = next((n for n in names.values() if n and n not in _NAME_STOP), "")
        if not row_name and ri > 0 and li0 - max(rows[ri - 1]["idxs"]) <= 1:
            row_name = prev_row_name  # سطرِ چسبیدهٔ بالایِ همین شخص (مثلاً شمارهٔ دوم)
        for ph in phones:
            if not names[ph] or names[ph] in _NAME_STOP:
                names[ph] = row_name
        prev_row_name = row_name

        # ۲) آدرس = تکه‌های فارسیِ ستون‌های چپ
        # متونِ ستون‌های راست (نام/ردیف/شماره) برای فیلتر تکه‌های آویزه
        cluster_blob = " ".join(
            _clean_name(t, 100)
            for li in idxs for col, t in cell_lines[li][1] if col >= thresh)

        def _left_parts(li: int) -> list[str]:
            parts: list[str] = []
            for col, t in cell_lines[li][1]:
                if col >= thresh:
                    continue
                if re.search(r"[()]", t) and _MOBILE_RE.search(t):
                    break
                tt = _clean_name(t, limit=100)
                if not tt or not _HAS_FA.search(tt) or _PURE_NUM.match(tt):
                    continue
                if tt in _NAME_STOP:
                    continue
                if row_name and _in_name_cluster(tt, row_name):
                    break
                if len(tt) >= 2 and tt in cluster_blob:
                    continue  # تکه‌ای از نام/شماره که به ستون آدرس ریخته
                parts.append(tt)
            return parts

        # ۲ب) ادامه‌های پایینِ سطر
        addr_parts = []
        if row.get("borrow_above") is not None:
            addr_parts.extend(_left_parts(row["borrow_above"]))
        addr_parts.extend(_left_parts(li0))
        landlines: list[str] = []
        for li in idxs[1:]:
            if _left_farsi_any(li):
                addr_parts.extend(_left_parts(li))
            # شمارهٔ ثابتِ داخلِ ادامه (مثلاً «تلفن ثابت  021...»)
            if row_name and not _MOBILE_RE.search(cell_lines[li][0]):
                for m in re.finditer(r"(?<!\d)(\d{7,})(?!\d)", cell_lines[li][0]):
                    landlines.append(m.group(1))
        address = re.sub(r"\s+", " ", " ".join(addr_parts)).strip(" -")[:120]
        for lp in landlines:
            if ocr.to_en(lp) not in seen_phones:
                seen_phones.add(ocr.to_en(lp))
                it = {"name": row_name, "phone": lp, "source": source}
                if address:
                    it["address"] = address
                out.append(it)

        # ۳) خروجی
        for ph in phones:
            name = names[ph]
            if not name or name in _NAME_STOP:
                if not addr_parts and not row["has_left"]:
                    fm = re.search(r"[\u0600-\u06FF][\u0600-\u06FF\u200c\- ]{2,39}", first_line)
                    name = _clean_name(fm.group(0)) if fm else ""
                if not name or name in _NAME_STOP:
                    continue
            d = ocr.to_en(ph)
            if d in seen_phones:
                continue
            seen_phones.add(d)
            item = {"name": name, "phone": ph, "source": source}
            if address:
                item["address"] = address
            out.append(item)

    # خطوط مستقل (مثلاً لیست ساده‌ای که خطی موبایل ندارد)
    for idx, line in enumerate(lines):
        if idx in row_line_ids or not line.strip() or _MOBILE_RE.search(line):
            continue
        for it in parse_txt_list(line, source=source):
            ph = re.sub(r"[^\d+\-() ]", "", it["phone"]).strip()
            d = re.sub(r"\D", "", ph)
            if len(d) < 6 or d in seen_phones:
                continue
            seen_phones.add(d)
            it = dict(it)
            it["phone"] = ph
            out.append(it)
    return out


def _pdf_pages(path: Path, limit: int = MAX_OCR_PAGES) -> list[Path]:
    """صفحات PDF را به PNG رندر می‌کند (برای PDF اسکن‌شده)."""
    out_dir = Path(f"{path}.pages")
    out_dir.mkdir(exist_ok=True)
    try:
        subprocess.run(
            ["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", str(limit),
             str(path), str(out_dir / "p")],
            capture_output=True, text=True, timeout=120, check=True)
    except Exception:  # noqa: BLE001
        return []
    return sorted(out_dir.glob("p-*.png"))


def extract_pdf(path: str | Path) -> list[dict]:
    """
    PDF را می‌خواند:
      ۱) متن دارد؟ → چند «تنوع» متن (layout/raw × راست/وارونه) را می‌سازد،
         هرکدام را پارس می‌کند و آن تنوعی را برمی‌گرداند که نام‌هایش
         «فارسی درست»تر هستند (نمرهٔ واژگانی).
      ۲) اسکن‌شده است؟ → صفحات را رندر و با موتور OCR می‌خواند
    """
    path = Path(path)
    raw_text = _pdftotext(path, "-raw")
    layout_text = _pdftotext(path, "-layout")

    variants = [
        (_strip_bidi(layout_text),),
        (_strip_bidi(raw_text),),
        (_rev_text(_strip_bidi(layout_text)),),
        (_rev_text(_strip_bidi(raw_text)),),
    ]
    best_items: list[dict] = []
    best_score = -1
    for (text,) in variants:
        if not text.strip():
            continue
        items = _dedupe(parse_table_text(text, source="فایل"))
        if not items:
            continue
        score = _fa_score(items)
        if score > best_score or (score == best_score and len(items) > len(best_items)):
            best_items, best_score = items, score
    if best_items:
        return best_items

    # اسکن‌شده: رندر + OCR (فقط تا زمانی که نتیجه بدهد)
    pages = _pdf_pages(path)
    all_items: list[dict] = []
    for i, page in enumerate(pages):
        got = ocr.extract_with_reliability(page)
        all_items.extend(got)
        # اگر صفحهٔ اول چیز نداد، احتمالاً لیست مخاطبین نیست — وقت را هدر نده
        if i == 0 and not got:
            break
    return _dedupe(all_items)


# -------------------------------------------------------------------- همه ----
def extract_file(path: str | Path, ext: str) -> list[dict]:
    """dispatch بر اساس پسوند: pdf / docx (با یا بدون نقطه)"""
    path = Path(path)
    ext = (ext or "").lstrip(".").lower()
    if ext == "pdf":
        return extract_pdf(path)
    if ext == "docx":
        return extract_docx(path)
    return []
