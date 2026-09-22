# -*- coding: utf-8 -*-
"""
مغز هوشمند Melk Hunter — نسخهٔ هیولا

۱) مترادف‌های محاوره‌ای بازار ملک: «خونه» ↔ «خانه»، «آپار» ↔ «آپارتمان»، …
   وقتی نتیجه کم است، پرس‌وجو خودش گشادتر می‌شود (فقط در صورت نیاز، تا دقت حفظ شود).
۲) اصلاح خودکار غلط املایی در سطح «کلمه» با واژگان واقعی دفترچه.
۳) حذف کلمات پرکننده: «پیدا کن»، «بگرد»، «می‌خوام»، «لطفا» …
۴) کارت مخاطب استاندارد (vCard) برای ذخیرهٔ یک‌لمسی در گوشی.
۵) بینش‌های آماری برای داشبورد.
"""
from __future__ import annotations

import re
from collections import Counter

from rapidfuzz import fuzz, process

from melkbot.normalize import digits_only, normalize_text, parse_phone

# ---------------------------------------------------------------- مترادف‌ها --
SYNONYMS: dict[str, tuple[str, ...]] = {
    # نوع ملک
    "خونه": ("خانه", "مسکونی"),
    "خانه": ("خونه",),
    "اپار": ("آپارتمان", "اپارتمان"),
    "اپارتمان": ("آپارتمان",),
    "اپارتمانی": ("آپارتمان",),
    "اپارتمانی ": ("آپارتمان",),
    "مغازه": ("تجاری", "پاساژ", "حجره"),
    "دکان": ("مغازه", "تجاری"),
    "دفتر": ("اداری", "دفترکار"),
    "دفترکار": ("اداری", "دفتر"),
    "اداری": ("دفتر", "آفیس"),
    "تجاری": ("مغازه", "پاساژ", "حجره"),
    "سوئیت": ("سویت",),
    "ویلا": ("ویلایی", "ویلا"),
    "زمین": ("قطعه", "زمین"),
    "کلنگی": ("تخریب", "کلنگی"),
    "تخریب": ("کلنگی",),
    "نوساز": ("نو", "ساز"),
    "پیش": ("فروش",),
    # معامله
    "رهن": ("ودیعه", "رهن"),
    "ودیعه": ("رهن",),
    "اجاره": ("مستاجر", "اجاره"),
    "مستاجر": ("اجاره", "رهن"),
    "خریدار": ("خواستار", "خریدار"),
    "خواستار": ("خریدار",),
    "فروشنده": ("فروش", "مالک"),
    "فروشی": ("فروش",),
    "مالک": ("صاحب", "مالک"),
    "صاحب": ("مالک",),
    # شغل‌ها و جایگاه
    "بنگاه": ("املاک", "مشاور", "آژانس"),
    "املاک": ("بنگاه", "مشاور"),
    "مشاور": ("بنگاه", "املاک", "همکار"),
    "همکار": ("مشاور", "بنگاه", "املاک"),
    "آژانس": ("بنگاه", "املاک"),
    "واسطه": ("مشاور", "بنگاه"),
    # محاوره
    "جنوب": ("جنوبی",),
    "شمال": ("شمالی",),
    "شرق": ("شرقی",),
    "غرب": ("غربی",),
    "مرکز": ("مرکزی",),
    "حیاط": ("باغ", "حیاط"),
    "باغ": ("حیاط", "باغ"),
}

# کلماتی که کاربر می‌نویسد ولی در دفترچه معنا ندارند
# کلماتی که همیشه حذف می‌شوند (بی‌ابهام)
FILLERS_STRONG = {
    "پیدا", "کن", "کنی", "کنید", "بگرد", "بگردی", "بگردید", "جستجو", "جستوجو", "سرچ",
    "میخوام", "میخواستم", "میخواهم", "خواستم", "لطفا", "لطفاً", "ممنون", "دنبال",
    "بده", "نشون", "نشان", "شمارهی", "همراهش", "رو", "را", "واسه", "بگو", "بگیر",
}
# حروف اضافهٔ کوتاه — فقط وقتی عبارت ۳ کلمه یا بیشتر باشد حذف می‌شوند
# (تا معنای عبارت‌های کوتاه مثل «خانه در تهران» عوض نشود)
FILLERS_WEAK = {
    "شماره", "تلفن", "موبایل", "مال", "کی", "به", "از", "با", "این", "آن", "هم",
    "و", "در", "برای", "یه", "یکی", "هست", "داری", "داریم", "دارید", "کجاست", "کیه",
}

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa(n) -> str:
    """عدد با رقم‌های فارسی (برای متن‌های آماری)."""
    if isinstance(n, float):
        s = f"{n:.1f}".rstrip("0").rstrip(".")
    elif isinstance(n, int):
        s = f"{n:,}"
    else:
        s = str(n)
    return s.translate(_FA_DIGITS)

_TOKEN_SPLIT = re.compile(r"\s+")
_OPERATORISH = re.compile(r'^([#"-]|متر:|متراژ:|نوع:|تکراری:|area:)')


def strip_fillers(q: str) -> str:
    """
    کلمات پرکننده را حذف می‌کند ولی عملگرها و برچسب‌ها را دست نمی‌زند.
    حروف اضافهٔ کوتاه فقط در عبارت‌های ۳کلمه‌ای و بیشتر حذف می‌شوند.
    """
    tokens = [t for t in _TOKEN_SPLIT.split(q.strip()) if t]
    drop_weak = len(tokens) >= 3
    out = []
    for tok in tokens:
        if _OPERATORISH.match(tok) or tok.startswith('"'):
            out.append(tok)
            continue
        nt = normalize_text(tok)
        if nt in FILLERS_STRONG or (drop_weak and nt in FILLERS_WEAK):
            continue
        out.append(tok)
    cleaned = " ".join(out).strip()
    return cleaned if cleaned and cleaned != q else q


def synonyms_for(token: str) -> tuple[str, ...]:
    return SYNONYMS.get(normalize_text(token), ())


def expand_queries(q: str, max_n: int = 6) -> list[str]:
    """
    چند نسخهٔ گشادتر از پرس‌وجو می‌سازد: اول تک‌تک کلمات با مترادف جایگزین می‌شوند،
    بعد همه با هم. ترتیب از «کم‌تغییر» به «پرتغییر» است تا دقت حفظ شود.
    """
    tokens = [t for t in _TOKEN_SPLIT.split(q.strip()) if t]
    if not tokens:
        return []
    variants: list[str] = []
    singles: list[str] = []
    for i, tok in enumerate(tokens):
        if _OPERATORISH.match(tok) or tok.startswith('"') or any(ch.isdigit() for ch in tok):
            continue
        syns = synonyms_for(tok)
        for s in syns[:2]:
            cand = " ".join(tokens[:i] + [s] + tokens[i + 1:])
            if cand != q and cand not in variants:
                singles.append(cand)
    variants.extend(singles)
    # نسخهٔ «همه با هم»
    allrep = []
    for tok in tokens:
        syns = synonyms_for(tok)
        allrep.append(syns[0] if syns else tok)
    combo = " ".join(allrep)
    if combo != q and combo not in variants:
        variants.append(combo)
    return variants[:max_n]


# ------------------------------------------------------- اصلاح املایی کلمه‌ای
def build_vocab(contacts: list[dict], min_len: int = 2) -> dict[str, int]:
    """واژگان + بسامد، از کلیدهای واقعی دفترچه (برای اصلاح خودکار هوشمند)."""
    vocab: dict[str, int] = {}
    for c in contacts:
        for k in c.get("_keys", []):
            for w in _TOKEN_SPLIT.split(k):
                if len(w) < min_len:
                    continue
                vocab[w] = vocab.get(w, 0) + 1
                for part in w.split("\u200c"):        # شکستن نیم‌فاصله
                    if len(part) >= min_len:
                        vocab[part] = vocab.get(part, 0) + 1
    return vocab


def correct_query(q: str, vocab: dict[str, int], cutoff: float = 85.0,
                  min_len: int = 4) -> tuple[str, list[tuple[str, str]]]:
    """
    برای هر کلمهٔ پرس‌وجو نزدیک‌ترین واژهٔ واقعی دفترچه را پیدا می‌کند
    (در حالت تساویِ امتیاز، کلمهٔ پرتکرارتر برنده است).
    خروجی: (پرس‌وجوی اصلاح‌شده، فهرست (غلط → درست))
    """
    if not vocab:
        return q, []
    tokens = [t for t in _TOKEN_SPLIT.split(q.strip()) if t]
    changes: list[tuple[str, str]] = []
    out: list[str] = []
    for tok in tokens:
        nt = normalize_text(tok)
        if (_OPERATORISH.match(tok) or tok.startswith('"') or any(ch.isdigit() for ch in tok)
                or len(tok) < min_len or nt in vocab):
            out.append(tok)
            continue
        hits = process.extract(nt, list(vocab), scorer=fuzz.WRatio, limit=8,
                               score_cutoff=cutoff)
        # طول کلمهٔ پیشنهادی باید نزدیک باشد (تا «کلنکی» به «کی» تبدیل نشود)
        hits = [h for h in hits if abs(len(h[0]) - len(nt)) <= 2] or []
        if hits:
            best = max(hits, key=lambda h: (h[1], vocab.get(h[0], 0)))
            if best[0] != nt:
                changes.append((tok, best[0]))
                out.append(best[0])
                continue
        out.append(tok)
    return (" ".join(out), changes) if changes else (q, [])


# ------------------------------------------------------------ vCard -------
def _intl_phone(c: dict) -> str:
    phone = c.get("phone") or ""
    if not phone:
        p = parse_phone(c.get("phone_raw", ""))
        phone = p.get("e164") or ""
    d = digits_only(phone)
    if not d:
        return ""
    if d.startswith("98"):
        return "+" + d
    if d.startswith("0"):
        return "+98" + d[1:]
    if d.startswith("9") and len(d) == 10:
        return "+98" + d
    return "+" + d


def vcf(c: dict) -> str:
    """کارت مخاطب استاندارد (vCard 3.0) برای ذخیرهٔ یک‌لمسی در گوشی."""
    tel = _intl_phone(c)
    name = (c.get("name") or "مخاطب").strip()
    org = " · ".join(x for x in [c.get("operator"), c.get("region")] if x)
    tags = ", ".join(c.get("tags", [])[:8])
    note = " · ".join(x for x in [org, (f"برچسب‌ها: {tags}" if tags else "")] if x)
    lines = [
        "BEGIN:VCARD", "VERSION:3.0",
        f"N:{name};{name};;;", f"FN:{name}",
        "TEL;TYPE=CELL:" + tel if tel else "TEL;TYPE=CELL:",
    ]
    if org:
        lines.append("ORG:" + org)
    if note:
        lines.append("NOTE:" + note)
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def vcf_filename(c: dict) -> str:
    safe = re.sub(r"[^\w\u0600-\u06FF\- ]+", "", c.get("name") or "contact").strip()
    return (safe[:40] or "contact") + ".vcf"


# ------------------------------------------------------------ بینش‌ها ------
def insights(E, meta: dict | None = None, top_regions: int = 3) -> list[str]:
    """خطوط «نگاه یک‌نگاههٔ» داشبورد آمار."""
    meta = meta or {}
    n = max(1, len(E.contacts))
    out: list[str] = []

    def pct(x: int) -> str:
        p = x / n * 100
        return _fa(p) if p >= 1 else "<۱"

    tags = E.top_tags(1)
    if tags:
        out.append(f"🔥 داغ‌ترین برچسب: <b>#{tags[0][0]}</b> با {_fa(tags[0][1])} مخاطب")

    intl = sum(1 for c in E.contacts if c.get("kind") == "intl")
    if intl:
        out.append(f"🌍 مخاطبین خارج از کشور: <b>{_fa(intl)}</b> نفر ({pct(intl)}٪)")

    with_area = [c for c in E.contacts if c.get("area")]
    if with_area:
        avg = round(sum(int(c["area"]) for c in with_area) / len(with_area))
        out.append(f"📐 میانگین متراژ ذکرشده: <b>{_fa(avg)}</b> متر "
                   f"(روی {_fa(len(with_area))} رکورد)")

    regions = Counter(c["region"] for c in E.contacts if c.get("region"))
    if regions:
        top = " · ".join(f"{r} ({_fa(k)})" for r, k in regions.most_common(top_regions))
        out.append(f"📍 پرترددترین مناطق: {top}")

    ops = Counter(c["operator"] for c in E.contacts if c.get("operator"))
    if ops:
        top = " · ".join(f"{o} ({_fa(c)})" for o, c in ops.most_common(3))
        out.append(f"📶 اپراتورهای برتر: {top}")

    multi = meta.get("multi_phone", 0)
    if multi:
        out.append(f"📞 رکوردهای چندشماره‌ای: <b>{_fa(multi)}</b>")

    dup = sum(1 for c in E.contacts if c.get("is_dup"))
    if dup:
        out.append(f"🔁 سهم رکوردهای تکراری: {pct(dup)}٪ — با /dups ببینشان")

    return out


# ------------------------------------------------------------ خودآزمایی -----
if __name__ == "__main__":
    tests = [
        ("خونه جنت آباد", ["خانه جنت آباد"]),
        ("مغازه", ["تجاری"]),
        ("آپار ارزان", ["آپارتمان ارزان"]),
        ("اسپهبد یرم", ["اسپهبد", "یرم"]),
    ]
    print("=== گشادسازی پرس‌وجو ===")
    for q, _ in tests:
        print(f"  «{q}» → {expand_queries(q)[:3]}")
    print("\n=== حذف پرکننده‌ها ===")
    print("  »,لطفا شماره ارش جنت رو پیدا کن« →", strip_fillers("لطفا شماره ارش جنت رو پیدا کن"))
    print("\n=== اصلاح املایی ===")
    vocab = {"آرش", "جنتآباد", "اسپهبد", "برج", "ماری", "کلنگی", "دیوار"}
    print("  ", correct_query("ارشس جنت اباد", vocab))
    print("\n=== vCard ===")
    print(vcf({"name": "آقای آرش جنت", "phone": "+989121234567",
               "operator": "همراه اول", "region": "تهران", "tags": ["کلنگی", "دیوار"]}))
