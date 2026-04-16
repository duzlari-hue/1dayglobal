"""telegram_bot.py — Telegram post yuborish"""
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
    if sarlavha and len(sarlavha.strip()) >= 8:
        post += f"⚡ <b>{sarlavha.strip()}</b>\n\n"
    post += f"{j1e} {jumla1}\n\n"
    if jumla2:
        post += f"{j2e} {jumla2}\n\n"
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

    # Inglizcha yoki noaniq — lat2cyr QILMAYMIZ, xato chiqarish
    log.warning(f"_ensure_cyr: inglizcha matn keldi, o'zgartirilmadi: '{text[:60]}'")
    return text  # Inglizcha holida qoldirish (bo'sh bo'lganidan yaxshi)


def send_all_languages(d, article):
    """3 tilda 3 kanalga yuborish"""
    daraja = d.get("daraja", "xabar")

    # O'ZBEK → @birkunday  (barcha UZ matnlar kiriллcha bo'lishi shart)
    post_uz = make_post(
        _ensure_cyr(d.get("sarlavha_uz", "")),
        _ensure_cyr(d.get("jumla1_uz", "")),
        _ensure_cyr(d.get("jumla2_uz", "")),
        daraja,
        _ensure_cyr(d.get("hashtag_uz", "")),
        _ensure_cyr(d.get("location_uz", "")),
        "uz"
    )
    if send_telegram(post_uz, TELEGRAM_CHANNEL_UZ):
        log.info(f"✅ Telegram UZ → {TELEGRAM_CHANNEL_UZ}")

    # RUS → @birkunday_ru
    post_ru = make_post(
        d["sarlavha_ru"], d["jumla1_ru"], d["jumla2_ru"],
        daraja, d["hashtag_ru"], d.get("location_ru", ""), "ru"
    )
    if send_telegram(post_ru, TELEGRAM_CHANNEL_RU):
        log.info(f"✅ Telegram RU → {TELEGRAM_CHANNEL_RU}")

    # INGLIZ → @birkunday_en
    post_en = make_post(
        d["sarlavha_en"], d["jumla1_en"], d["jumla2_en"],
        daraja, d["hashtag_en"], d.get("location_en", ""), "en"
    )
    if send_telegram(post_en, TELEGRAM_CHANNEL_EN):
        log.info(f"✅ Telegram EN → {TELEGRAM_CHANNEL_EN}")
