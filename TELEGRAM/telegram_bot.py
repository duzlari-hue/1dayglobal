"""telegram_bot.py — Telegram post yuborish"""
import re
import requests
import logging
from datetime import datetime

from translator import lat2cyr   # lotin → kirill fallback

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHANNEL_UZ,
    TELEGRAM_CHANNEL_RU,
    TELEGRAM_CHANNEL_EN,
    TASHKENT,
)

log = logging.getLogger(__name__)


def make_post(sarlavha, jumla1, jumla2, daraja, hashtaglar, location, lang="uz"):
    if daraja == "muhim":
        belgi = {"uz": "🔴 MUHIM", "ru": "🔴 ВАЖНО", "en": "🔴 BREAKING"}.get(lang, "🔴")
        j1e, j2e = "🔴", "⚠️"
    elif daraja == "tezkor":
        belgi = {"uz": "🟠 TEZKOR", "ru": "🟠 СРОЧНО", "en": "🟠 URGENT"}.get(lang, "🟠")
        j1e, j2e = "⚡", "📌"
    else:
        belgi = {"uz": "🟢 XABAR", "ru": "🟢 НОВОСТЬ", "en": "🟢 NEWS"}.get(lang, "🟢")
        j1e, j2e = "📌", "💬"

    kanal = {"uz": TELEGRAM_CHANNEL_UZ, "ru": TELEGRAM_CHANNEL_RU, "en": TELEGRAM_CHANNEL_EN}.get(lang, TELEGRAM_CHANNEL_UZ)
    vaqt  = datetime.now(TASHKENT).strftime("🕐 %H:%M | %d.%m.%Y")

    post  = belgi + "\n\n"
    # Sarlavha faqat mavjud va yetarli uzunlikda bo'lsa qo'shiladi
    _sv = sarlavha.strip() if sarlavha else ""
    if _sv and len(_sv) >= 8:
        post += f"⚡ <b>{_sv}</b>\n\n"
    # jumla1: bo'sh bo'lsa yoki sarlavha bilan bir xil bo'lsa — ko'rsatmaymiz
    _j1 = jumla1.strip() if jumla1 else ""
    if _j1 and _j1 != _sv:
        post += f"{j1e} {_j1}\n\n"
    # jumla2
    _j2 = jumla2.strip() if jumla2 else ""
    if _j2 and _j2 != _j1 and _j2 != _sv:
        post += f"{j2e} {_j2}\n\n"
    if location:
        post += f"📍 {location}\n"
    post += vaqt + "\n"
    post += f"📰 {kanal}\n\n"
    post += hashtaglar
    return post


def send_telegram(caption, channel):
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    r = requests.post(
        f"{base}/sendMessage",
        json={
            "chat_id":                  channel,
            "text":                     caption[:4000],
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    ok = r.json().get("ok", False)
    if not ok:
        log.warning(f"Telegram xato ({channel}): {r.json().get('description', '')}")
    return ok


def _ensure_cyr(text: str) -> str:
    """Matn asosan lotin yozuvida bo'lsa — kirillga o'girish.
    Inglizcha matn lat2cyr qilinmaydi (Трумп агаин фумес xatosi oldini olish)."""
    if not text:
        return text
    cyr_chars = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯЎҚҒҲ"
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return text
    cyr_n = sum(1 for c in letters if c in cyr_chars)
    if cyr_n / len(letters) >= 0.60:
        return text   # allaqachon asosan kirill

    # O'zbek lotiniga xos belgilar
    _UZ_MARKERS = ("o'", "g'", "o'", "g'", "sh", "ch",
                   "o'z", "va ", "bu ", "lar", "dan", "ga ")
    tl = text.lower()
    if any(m in tl for m in _UZ_MARKERS):
        return lat2cyr(text)  # O'zbek lotin → kirill

    # Inglizcha yoki noaniq — lat2cyr QILMAYMIZ, bo'sh qaytaramiz
    log.warning(f"_ensure_cyr: inglizcha — bo'sh: '{text[:60]}'")
    return ""  # Bo'sh matn gibberish kiriллdan yaxshi


def send_all_languages(d, article):
    """3 tilda 3 kanalga yuborish"""
    daraja = d.get("daraja", "xabar")

    # O'ZBEK → @birkunday  (barcha UZ matnlar kiriллcha bo'lishi shart)
    _j1_uz = _ensure_cyr(d.get("jumla1_uz", ""))
    _j2_uz = _ensure_cyr(d.get("jumla2_uz", ""))

    # jumla2 bo'sh bo'lsa — jumla1 ni jumlalarga bo'lib ikkinchi qism qilamiz
    if not _j2_uz and _j1_uz:
        _sents = re.split(r'(?<=[.!?…])\s+', _j1_uz.strip())
        if len(_sents) >= 4:
            mid = len(_sents) // 2
            _j1_uz = " ".join(_sents[:mid]).strip()
            _j2_uz = " ".join(_sents[mid:]).strip()

    post_uz = make_post(
        _ensure_cyr(d.get("sarlavha_uz", "")),
        _j1_uz, _j2_uz,
        daraja,
        _ensure_cyr(d.get("hashtag_uz", "")),
        _ensure_cyr(d.get("location_uz", "")),
        "uz"
    )
    if send_telegram(post_uz, TELEGRAM_CHANNEL_UZ):
        log.info(f"✅ Telegram UZ → {TELEGRAM_CHANNEL_UZ}")

    # RUS → @birkunday_ru  (faqat kiriллcha matn yuboriladi)
    _CYR_CHARS  = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
    _ru_text    = d.get("sarlavha_ru", "") + d.get("jumla1_ru", "")
    _ru_letters = [c for c in _ru_text if c.isalpha()]
    _ru_cyr_pct = sum(1 for c in _ru_letters if c in _CYR_CHARS) / max(len(_ru_letters), 1)
    if _ru_cyr_pct < 0.50:
        log.warning(f"⚠️  RU matn kiriллcha emas ({_ru_cyr_pct:.0%}) — o'tkazildi")
    else:
        _j1_ru = d.get("jumla1_ru", "")
        _j2_ru = d.get("jumla2_ru", "")
        # jumla2_ru bo'sh bo'lsa — jumla1_ru ni bo'lamiz
        if not _j2_ru and _j1_ru:
            _sents_ru = re.split(r'(?<=[.!?…])\s+', _j1_ru.strip())
            if len(_sents_ru) >= 4:
                mid_ru = len(_sents_ru) // 2
                _j1_ru = " ".join(_sents_ru[:mid_ru]).strip()
                _j2_ru = " ".join(_sents_ru[mid_ru:]).strip()
        post_ru = make_post(
            d["sarlavha_ru"], _j1_ru, _j2_ru,
            daraja, d["hashtag_ru"], d.get("location_ru", ""), "ru"
        )
        if send_telegram(post_ru, TELEGRAM_CHANNEL_RU):
            log.info(f"✅ Telegram RU → {TELEGRAM_CHANNEL_RU}")

    # INGLIZ → @birkunday_en
    _j1_en = d.get("jumla1_en", "")
    _j2_en = d.get("jumla2_en", "")
    # jumla2_en bo'sh bo'lsa — jumla1_en ni bo'lamiz
    if not _j2_en and _j1_en:
        _sents_en = re.split(r'(?<=[.!?…])\s+', _j1_en.strip())
        if len(_sents_en) >= 4:
            mid_en = len(_sents_en) // 2
            _j1_en = " ".join(_sents_en[:mid_en]).strip()
            _j2_en = " ".join(_sents_en[mid_en:]).strip()
    post_en = make_post(
        d["sarlavha_en"], _j1_en, _j2_en,
        daraja, d["hashtag_en"], d.get("location_en", ""), "en"
    )
    if send_telegram(post_en, TELEGRAM_CHANNEL_EN):
        log.info(f"✅ Telegram EN → {TELEGRAM_CHANNEL_EN}")


# ══════════════════════════════════════════════════════════════
# Kunlik digest — 5-6 ta yangilik bir postda (kechqurun)
# ══════════════════════════════════════════════════════════════
def send_daily_digest(articles: list, lang: str = "uz"):
    """
    5-6 ta yangilikni bitta Telegram postda yuborish.
    articles — [{"sarlavha": str, "jumla1": str, "daraja": str}, ...]
    """
    if not articles:
        return False

    channel = {
        "uz": TELEGRAM_CHANNEL_UZ,
        "ru": TELEGRAM_CHANNEL_RU,
        "en": TELEGRAM_CHANNEL_EN,
    }.get(lang, TELEGRAM_CHANNEL_UZ)

    # Sarlavha
    digest_title = {
        "uz": "📋 KUNNING ASOSIY YANGILIKLARI",
        "ru": "📋 ГЛАВНЫЕ НОВОСТИ ДНЯ",
        "en": "📋 TODAY'S TOP NEWS",
    }.get(lang, "📋 TOP NEWS")

    vaqt = datetime.now(TASHKENT).strftime("🕐 %H:%M | %d.%m.%Y")
    kanal = {
        "uz": TELEGRAM_CHANNEL_UZ,
        "ru": TELEGRAM_CHANNEL_RU,
        "en": TELEGRAM_CHANNEL_EN,
    }.get(lang, TELEGRAM_CHANNEL_UZ)

    post = f"<b>{digest_title}</b>\n"
    post += "━" * 28 + "\n\n"

    for i, art in enumerate(articles[:6], 1):
        sarlavha = art.get("sarlavha", "").strip()
        jumla    = art.get("jumla1", "").strip()
        daraja   = art.get("daraja", "xabar")

        emoji = {"muhim": "🔴", "tezkor": "🟠"}.get(daraja, "🟢")
        if sarlavha:
            post += f"{i}. {emoji} <b>{sarlavha}</b>\n"
        if jumla:
            post += f"   {jumla[:120]}{'...' if len(jumla) > 120 else ''}\n"
        post += "\n"

    post += "━" * 28 + "\n"
    post += f"{vaqt}\n"
    post += f"📰 {kanal}"

    ok = send_telegram(post, channel)
    if ok:
        log.info(f"✅ Kunlik digest [{lang.upper()}] → {channel} ({len(articles)} yangilik)")
    else:
        log.warning(f"⚠️  Kunlik digest [{lang.upper()}] yuborilmadi")
    return ok


def send_daily_digest_all(articles_by_lang: dict):
    """3 tilda kunlik digest yuborish.
    articles_by_lang = {"uz": [...], "ru": [...], "en": [...]}
    """
    for lang in ("uz", "ru", "en"):
        articles = articles_by_lang.get(lang, [])
        if articles:
            send_daily_digest(articles, lang)
