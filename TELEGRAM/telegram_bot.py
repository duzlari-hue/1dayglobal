"""telegram_bot.py — Telegram post yuborish"""
import re
import os
import random
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
    post += f"📰 {kanal}\n"

    # ── YouTube kanal havolasi (har post pastida) ─────────────
    yt_label = {
        "uz": "🎬 1Kun | Global News",
        "ru": "🎬 1День | Global News",
        "en": "🎬 1Day | Global News",
    }.get(lang, "🎬 1Day | Global News")
    yt_link = "https://www.youtube.com/@1kunnews"
    post += f'{yt_label}: <a href="{yt_link}">{yt_link}</a>\n\n'

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


def _fetch_og_image(article_url: str, out_path: str) -> bool:
    """Maqola sahifasidan og:image meta-tegini olish."""
    if not article_url or not article_url.startswith("http"):
        return False
    try:
        hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(article_url, headers=hdrs, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return False
        for pat in [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ]:
            m = re.search(pat, resp.text, re.IGNORECASE)
            if m:
                img_url = m.group(1).strip()
                if img_url.startswith("http"):
                    ir = requests.get(img_url, headers=hdrs, timeout=12)
                    if ir.status_code == 200 and len(ir.content) >= 10_000:
                        with open(out_path, "wb") as fh:
                            fh.write(ir.content)
                        return True
    except Exception:
        pass
    return False


_PEXELS_SEEN = set()

def _fetch_pexels(query: str, out_path: str) -> bool:
    """Pexels orqali rasm qidirish."""
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key or not query.strip():
        return False
    if not all(c.isascii() or not c.isalpha() for c in query):
        return False
    try:
        hdrs = {"Authorization": api_key}
        url  = (f"https://api.pexels.com/v1/search"
                f"?query={requests.utils.quote(query[:80])}"
                f"&per_page=15&orientation=landscape")
        resp = requests.get(url, headers=hdrs, timeout=10)
        if resp.status_code != 200:
            return False
        photos = resp.json().get("photos", [])
        random.shuffle(photos)
        for ph in photos:
            ph_id = ph.get("id")
            if ph_id in _PEXELS_SEEN:
                continue
            _PEXELS_SEEN.add(ph_id)
            src = ph.get("src", {})
            img_url = src.get("large2x") or src.get("large") or src.get("medium", "")
            if not img_url:
                continue
            ir = requests.get(img_url, timeout=15)
            if ir.status_code == 200 and len(ir.content) >= 20_000:
                with open(out_path, "wb") as f:
                    f.write(ir.content)
                return True
    except Exception:
        pass
    return False


def _find_article_photo(article: dict, keywords_en: list, tmp_prefix: str) -> str | None:
    """Yangilik uchun rasm topish: og:image → Pexels. None = topilmadi."""
    import tempfile, pathlib
    tmp_dir = pathlib.Path(tempfile.gettempdir())

    # 1. og:image maqola sahifasidan
    og_path = str(tmp_dir / f"{tmp_prefix}_og.jpg")
    if _fetch_og_image(article.get("link", ""), og_path):
        log.info("  📷 og:image topildi")
        return og_path

    # 2. Pexels — keywords_en bilan qidirish
    queries = []
    if keywords_en:
        queries.append(" ".join(keywords_en[:3]))
    title = article.get("title", "")
    if title:
        # Birinchi 3-5 ta so'z
        words = [w for w in title.split() if len(w) > 3][:4]
        if words:
            queries.append(" ".join(words))
    for q in queries:
        px_path = str(tmp_dir / f"{tmp_prefix}_px.jpg")
        if _fetch_pexels(q, px_path):
            log.info(f"  📷 Pexels: '{q[:40]}'")
            return px_path

    log.info("  📷 Rasm topilmadi — rasmsiz yuboriladi")
    return None


def send_telegram_photo(caption: str, photo_path: str, channel: str) -> bool:
    """Rasmli Telegram post yuborish."""
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    try:
        with open(photo_path, "rb") as f:
            r = requests.post(
                f"{base}/sendPhoto",
                data={
                    "chat_id":    channel,
                    "caption":    caption[:1024],
                    "parse_mode": "HTML",
                },
                files={"photo": f},
                timeout=30,
            )
        ok = r.json().get("ok", False)
        if not ok:
            log.warning(f"sendPhoto xato ({channel}): {r.json().get('description', '')}")
        return ok
    except Exception as e:
        log.warning(f"sendPhoto exception ({channel}): {e}")
        return False


def _send_with_photo(caption: str, channel: str, photo_path: str | None) -> bool:
    """Rasm bo'lsa sendPhoto, yo'qsa sendMessage."""
    if photo_path:
        ok = send_telegram_photo(caption, photo_path, channel)
        if ok:
            return True
        log.warning("  sendPhoto muvaffaqiyatsiz — matn bilan qayta urinish")
    return send_telegram(caption, channel)


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


def _has_body(jumla1: str, jumla2: str = "", min_chars: int = 60) -> bool:
    """Post matni yetarli ekanligini tekshirish.
    Kamida bitta jumla min_chars belgidan uzun bo'lishi kerak."""
    j1 = (jumla1 or "").strip()
    j2 = (jumla2 or "").strip()
    return len(j1) >= min_chars or len(j2) >= min_chars


def send_all_languages(d, article):
    """3 tilda (UZ/RU/EN) Telegram kanallariga post yuborish.
    Matn (jumla1) bo'sh yoki juda qisqa (<60 harf) bo'lsa — post yuborilmaydi."""
    import tempfile, pathlib
    daraja = d.get("daraja", "xabar")

    # ── Rasm bir marta topib, 3 kanalga ishlatish ─────────────
    _ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    _kws = d.get("keywords_en", [])
    photo_path = _find_article_photo(article, _kws, f"tg_{_ts}")

    # ── O'ZBEK → @birkunday ───────────────────────────────────
    _sarlavha_uz = _ensure_cyr(d.get("sarlavha_uz", ""))
    _j1_uz       = _ensure_cyr(d.get("jumla1_uz", ""))
    _j2_uz       = _ensure_cyr(d.get("jumla2_uz", ""))
    if not _sarlavha_uz:
        log.warning("⚠️  sarlavha_uz bo'sh — UZ post o'tkazildi")
    elif not _has_body(_j1_uz, _j2_uz):
        log.warning(f"⚠️  UZ matn juda qisqa ({len(_j1_uz)} harf) — UZ post o'tkazildi")
    else:
        post_uz = make_post(
            _sarlavha_uz, _j1_uz, _j2_uz,
            daraja, d.get("hashtag_uz", "#Янгилик #1КУН"),
            d.get("location_uz", ""), "uz"
        )
        if _send_with_photo(post_uz, TELEGRAM_CHANNEL_UZ, photo_path):
            log.info(f"✅ Telegram UZ → {TELEGRAM_CHANNEL_UZ}")

    # ── RUS → @birkunday_ru ───────────────────────────────────
    _sarlavha_ru = d.get("sarlavha_ru", "").strip()
    _j1_ru       = d.get("jumla1_ru", "").strip()
    _j2_ru       = d.get("jumla2_ru", "").strip()
    if not _sarlavha_ru:
        log.warning("⚠️  sarlavha_ru bo'sh — RU post o'tkazildi")
    elif not _has_body(_j1_ru, _j2_ru):
        log.warning(f"⚠️  RU matn juda qisqa ({len(_j1_ru)} harf) — RU post o'tkazildi")
    else:
        post_ru = make_post(
            _sarlavha_ru, _j1_ru, _j2_ru,
            daraja, d.get("hashtag_ru", "#Новости #1День"),
            d.get("location_ru", ""), "ru"
        )
        if _send_with_photo(post_ru, TELEGRAM_CHANNEL_RU, photo_path):
            log.info(f"✅ Telegram RU → {TELEGRAM_CHANNEL_RU}")

    # ── INGLIZ → @birkunday_en ────────────────────────────────
    _j1_en = d.get("jumla1_en", "")
    _j2_en = d.get("jumla2_en", "")
    if not _has_body(_j1_en, _j2_en):
        log.warning(f"⚠️  EN matn juda qisqa ({len(_j1_en)} harf) — EN post o'tkazildi")
    else:
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
        if _send_with_photo(post_en, TELEGRAM_CHANNEL_EN, photo_path):
            log.info(f"✅ Telegram EN → {TELEGRAM_CHANNEL_EN}")

    # Vaqtinchalik faylni o'chirish
    if photo_path:
        try:
            import os as _os
            _os.remove(photo_path)
        except Exception:
            pass


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
