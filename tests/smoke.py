# -*- coding: utf-8 -*-
"""
تست دودی: همهٔ مسیرهای اصلی ربات را بدون اتصال به تلگرام اجرا می‌کند.
اجرا:  python3 tests/smoke.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import melkbot.bot as B  # noqa: E402
from telegram import Chat  # noqa: E402

UID = 998877
SENT: list[dict] = []
OK, FAIL = [], []


class FakeBot:
    def __init__(self):
        self.username = "MelkHunter_bot"
        self.id = 8200155638

    async def send_chat_action(self, *a, **k):
        return True

    async def send_message(self, chat_id, text, **k):
        SENT.append({"chat_id": chat_id, "text": text, **k})
        return SimpleNamespace(message_id=len(SENT), chat_id=chat_id)

    async def send_document(self, chat_id, doc, **k):
        SENT.append({"chat_id": chat_id, "doc": k.get("filename"), "caption": k.get("caption")})
        return SimpleNamespace(message_id=len(SENT))

    def __getattr__(self, item):
        return AsyncMock()


class FakeMessage:
    """پیام جعلی با همان رابطی که هندلرها استفاده می‌کنند."""

    def __init__(self, text: str = ""):
        self.message_id = 1
        self.text = text
        self.document = None
        self.caption = None
        self.reply_to_message = None
        self.chat = Chat(id=UID, type="private")
        self.chat_id = UID
        self.from_user = FakeUser()

    async def reply_text(self, text, **k):
        SENT.append({"chat_id": UID, "text": text, **k})
        return self

    async def edit_text(self, text, **k):
        SENT.append({"chat_id": UID, "text": text, "edited": True, **k})
        return SimpleNamespace(message_id=1)

    async def edit_reply_markup(self, **k):
        return True

    async def delete(self):
        return True


class FakeUser:
    id = UID
    first_name = "تست"
    full_name = "تست کاربر"
    username = "tester"
    is_bot = False


class FakeChat:
    id = UID
    type = "private"


def make_update(text: str = "", data: str = "", doc=None) -> SimpleNamespace:
    user = FakeUser()
    chat = FakeChat()
    msg = FakeMessage(text)
    msg.document = doc
    cq = None
    if data:
        cq = SimpleNamespace(
            data=data, from_user=user, message=msg,
            answer=AsyncMock(), edit_message_text=msg.edit_text,
            edit_message_reply_markup=AsyncMock(),
        )
    return SimpleNamespace(
        effective_user=user, effective_chat=chat, effective_message=msg,
        message=msg, callback_query=cq, inline_query=None,
    )


def ctx() -> SimpleNamespace:
    return SimpleNamespace(bot=FakeBot(), args=[], job_queue=None)


async def check(name: str, coro):
    try:
        await coro
        OK.append(name)
    except Exception as e:  # noqa: BLE001
        FAIL.append((name, f"{type(e).__name__}: {e}"))


async def main() -> int:
    B.load_engine()
    print(f"ایندکس: {B.filtered_count()} مخاطب")
    c = ctx()

    await check("/start", B.cmd_start(make_update("hi"), c))
    await check("/help", B.cmd_help(make_update(), c))
    await check("/id", B.cmd_id(make_update(), c))
    await check("/stats", B.cmd_stats(make_update(), c))
    await check("/quality", B.cmd_quality(make_update(), c))
    await check("/dups", B.cmd_dups(make_update(), c))
    await check("/tags", B.cmd_tags(make_update(), c))
    await check("/favs (خالی)", B.cmd_favs(make_update(), c))
    await check("/rand 3", B.cmd_rand(make_update(), SimpleNamespace(bot=FakeBot(), args=["3"])))
    await check("/filters", B.cmd_filters(make_update(), c))
    await check("/history", B.cmd_history(make_update(), c))
    await check("/export", B.cmd_export(make_update(), c))
    await check("/add", B.cmd_add(make_update(), c))
    await check("/watch", B.cmd_watch(make_update(), SimpleNamespace(bot=FakeBot(), args=["#کلنگی"])))
    await check("/watches", B.cmd_watches(make_update(), c))

    for q in ["آرش جنت", "ارش جنت", "0912 477", "+989122206255", "#کلنگی", "تکراری=بله",
              "کلنگی ۳۰۰ متر", "دیوار -همکار نوع:موبایل", '"خانم شریف"', "zzzz", "متر:5000-9000"]:
        await check(f"جست‌وجو: {q}", B.do_search(make_update(q), c, q))

    # حالت‌های انتظار
    B.SESS[UID] = {"await": "add"}
    await check("افزودن دستی", B.on_text(make_update("آقای تستی | 09120000000 | کلنگی,تست"), c))
    B.SESS[UID] = {"await": "note", "note_cid": 5}
    await check("ثبت یادداشت", B.on_text(make_update("یادداشت تست"), c))

    # دکمه‌ها
    for data in ["pg:1", "pg:0", "f:1", "info:1", "sim:1", "dup:1", "share:1", "ctags:1",
                 "tg:کلنگی", "q:آرش", "set:kind:mobile", "set:mode:name", "set:dup:only",
                 "set:sort:name", "re:1", "menu:filters", "menu:sort", "menu:export",
                 "list:dup", "list:invalid", "exp:csv", "exp:xlsx", "exp:vcf",
                 "exp:allxlsx", "exp:backup", "exp:issues", "wdel:1", "noop", "stats:1"]:
        await check(f"دکمه: {data}", B.on_callback(make_update(data=data), c))

    # دیده‌بان — اجرای زمان‌بندی‌شده
    await check("job دیده‌بان", B.watch_job(SimpleNamespace(bot=FakeBot())))

    # تست پویش فایل متنی و خواندن vcf
    vcf = ("BEGIN:VCARD\nVERSION:3.0\nFN:مخاطب تستی\nTEL;TYPE=CELL:+989121110000\nEND:VCARD\n"
           "BEGIN:VCARD\nVERSION:3.0\nFN:مخاطب دوم\nTEL;TYPE=CELL:09122223333\nEND:VCARD\n")
    items = B.parse_vcf(vcf)
    assert len(items) == 2, items
    txt = "5. شخص آزمون — 09123456789\n6. یک آگهی دیگر — +989120000000\n"
    assert len(B.parse_txt_list(txt)) == 2
    OK.append("پارس VCF و متن")

    # 👤 دکمهٔ «اطلاعات کامل» باید برای *هر نامِ* صفحه وجود داشته باشد
    UID_FULL = 998879   # شناسهٔ تازه تا تنظیمات ذخیره‌شدهٔ تست‌های قبلی اثر نگذارد
    res = B.run_search(UID_FULL, "کلنگی", {"mode": "smart", "kind": "all", "dup": "show"})
    ids = B.sort_ids(res, "score")
    assert len(ids) >= 3, "برای تست به چند نتیجه نیاز است"
    sess = {"uid": UID_FULL, "query": "کلنگی", "ids": ids, "total": res["total"], "page": 0,
            "relaxed": False, "fuzzy": False, "kind": "all", "dup": "show", "title": ""}
    text, kb = B.render_page(sess, 0, "کلنگی")
    per_card = [b for row in kb.inline_keyboard for b in row
                if (b.callback_data or "").startswith("info:")]
    on_page = min(B.PAGE_SIZE, len(ids))
    assert len(per_card) >= on_page, f"دکمهٔ اطلاعات کامل کم است: {len(per_card)} < {on_page}"
    targets = {b.callback_data for b in per_card}
    for cid in ids[:on_page]:
        assert f"info:{cid}" in targets, f"برای مخاطب {cid} دکمهٔ اطلاعات کامل نیست"
    full = B.full_info_text(B.ENGINE.get(ids[0]), UID_FULL)
    for needle in ("اطلاعات کامل", "آدرس ثبت‌شده", B.e164(B.ENGINE.get(ids[0]))):
        assert needle in full, f"در صفحهٔ اطلاعات کامل «{needle}» نبود"
    assert len(full) < 4000, "متن اطلاعات کامل برای تلگرام بلند است"
    assert "tel:" not in repr(kb) and "tel:" not in full, "لینک tel: ممنوع است"
    # نتیجهٔ جست‌وجو با متنی که واقعاً وجود دارد
    hit = B.run_search(UID_FULL, "برج ماری", {"mode": "smart", "kind": "all", "dup": "show"})
    assert hit["total"] > 0, "سرچ «برج ماری» نتیجه نداشت"
    OK.append(f"دکمهٔ اطلاعات کامل برای هر نتیجه ({on_page} دکمه روی صفحه)")

    # 📥 افزودن گروهی + 📷 بخش عکس
    await check("/bulk", B.cmd_bulk(make_update(), c))
    await check("/photo", B.cmd_photo(make_update(), c))
    await check("افزودن گروهی (۳ خط)",
                B.handle_await(make_update("x"), c, {"await": "bulk"},
                               "حسین کوکیان | 09127814246\nحسین صمدی — 09122481270\n02188723241"))
    checks = {
        "حسین کوکیان | 09127814246": ("حسین کوکیان", "09127814246"),
        "حسین صمدی — 09122481270": ("حسین صمدی", "09122481270"),
        "رضا , 02188723241": ("رضا", "02188723241"),
        "آقای رضایی | 09121112222 | کلنگی": ("آقای رضایی", "09121112222"),
    }
    for line, (nm, ph) in checks.items():
        got = B.parse_txt_list(line)[0]
        assert got["name"] == nm and ph in got["phone"], f"{line} → {got}"
    from melkbot import ocr
    oitems = ocr.extract_items("Ali Karimi 09123456789\nSara 09352713495")
    assert len(oitems) == 2, oitems
    assert ocr.norm_phone("+989352713495") == "09352713495"
    assert ocr.preview_lines(oitems)
    OK.append("افزودن گروهی و بخش عکس")

    print("\n✅ موفق:", len(OK))
    for n in OK:
        print("   •", n)
    if FAIL:
        print("\n❌ خطاها:", len(FAIL))
        for n, e in FAIL:
            print(f"   • {n} → {e}")
        return 1
    print("\n🎉 همهٔ مسیرها بدون خطا اجرا شد.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
