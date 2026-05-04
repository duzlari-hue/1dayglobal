"""telegram_bot.py — Telegram post yuborish"""
import re
import os
import random
import requests
import logging
import textwrap as _tw
import pathlib as _pl
import tempfile as _tf
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

# ─────────────────────────────────────────────────────────────
# PIL karta generatsiyasi — 1DAY GLOBAL brand style
# ─────────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

# Telegram karta o'lchamlari (1:1 kvadrat, Telegram uchun ideal)
_CW, _CH    = 1080, 1080
_C_CREAM    = (245, 240, 232)   # Krem fon (brand background)
_C_DARK_BG  = (10,  10,  10)   # Deyarli qora
_C_RED_C    = (204,  0,   0)   # Brand qizil
_C_WHITE_C  = (255, 255, 255)
_C_GRAY_C   = (150, 145, 135)
_C_DIV      = (210, 205, 195)  # Divider rang (krem fon uchun)


def _tgf(size: int, bold: bool = True):
    """PIL shrift — Arial/Calibri."""
    if not _PIL_OK:
        return None
    cands = (
        ["C:\\Windows\\Fonts\\arialbd.ttf",
         "C:\\Windows\\Fonts\\calibrib.ttf",
         "C:\\Windows\\Fonts\\verdanab.ttf"]
        if bold else
        ["C:\\Windows\\Fonts\\arial.ttf",
         "C:\\Windows\\Fonts\\calibri.ttf",
         "C:\\Windows\\Fonts\\verdana.ttf"]
    )
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _card_top_bar(draw, W: int, section: str, right_txt: str):
    """Standard top bar — black bg, red '10' logo, section, right info."""
    bar_h = 56
    draw.rectangle([(0, 0), (W, bar_h)], fill=_C_DARK_BG)
    draw.rectangle([(12, 8), (58, 48)], fill=_C_RED_C)
    draw.text((35, 28), "10", font=_tgf(22), fill=_C_WHITE_C, anchor="mm")
    draw.text((72, 28), section, font=_tgf(17, False), fill=(180, 175, 165), anchor="lm")
    draw.text((W - 14, 28), right_txt, font=_tgf(15, False), fill=(180, 175, 165), anchor="rm")


def _card_bottom_bar(draw, W: int, H: int, handle: str = "@birkunday"):
    """Pastki footer — divider + handle + 1DAYGLOBAL.NEWS."""
    draw.line([(40, H - 56), (W - 40, H - 56)], fill=_C_DIV, width=1)
    draw.text((40, H - 28), handle, font=_tgf(19, False), fill=_C_GRAY_C, anchor="lm")
    draw.text((W - 40, H - 28), "1DAYGLOBAL.NEWS", font=_tgf(19, False),
              fill=_C_RED_C, anchor="rm")


def _make_news_card(
        photo_path: "str | None",
        sarlavha: str, jumla1: str,
        daraja: str, source_name: str,
        lang: str, out_path: str) -> str:
    """
    1DAY GLOBAL news card (1080×1080) — Image 3 uslubi:
      · Yuqori 52%: qorong'i foto + diagonal stripe + top bar + category badge
      · Pastki 48%: krem fon + katta sarlavha + subtitle text
      · Footer: divider + @handle + 1DAYGLOBAL.NEWS
    """
    if not _PIL_OK:
        return ""

    W, H      = _CW, _CH
    photo_h   = int(H * 0.52)   # ~562px

    card = Image.new("RGB", (W, H), _C_CREAM)

    # ── YUQORI QISM: Foto + dark overlay + diagonal stripe ───
    if photo_path and os.path.exists(photo_path):
        try:
            ph   = Image.open(photo_path).convert("RGB")
            pw, pph = ph.size
            tgt_r = W / photo_h
            src_r = pw / pph
            if src_r > tgt_r:
                nw = int(pph * tgt_r); x = (pw - nw) // 2
                ph = ph.crop((x, 0, x + nw, pph))
            else:
                nh = int(pw / tgt_r); y = (pph - nh) // 2
                ph = ph.crop((0, y, pw, y + nh))
            ph   = ph.resize((W, photo_h), Image.LANCZOS)
            ph   = ImageEnhance.Brightness(ph).enhance(0.36)
            # Diagonal stripe overlay
            stripe = Image.new("RGBA", (W, photo_h), (0, 0, 0, 0))
            sd = ImageDraw.Draw(stripe)
            for xi in range(-photo_h, W + photo_h, 20):
                sd.line([(xi, 0), (xi + photo_h, photo_h)], fill=(0, 0, 0, 38), width=9)
            ph = ph.convert("RGBA")
            ph.alpha_composite(stripe)
            ph = ph.convert("RGB")
        except Exception:
            ph = None
    else:
        ph = None

    if ph is None:
        # Fallback: qorong'i diagonal texture
        ph = Image.new("RGB", (W, photo_h), (16, 16, 16))
        pd = ImageDraw.Draw(ph)
        for xi in range(-photo_h, W + photo_h, 20):
            pd.line([(xi, 0), (xi + photo_h, photo_h)], fill=(28, 28, 28), width=9)
        # Placeholder
        pr_w, pr_h = 240, 52
        pr_x, pr_y = (W - pr_w) // 2, (photo_h - pr_h) // 2
        for xi in range(pr_x, pr_x + pr_w, 12):
            pd.line([(xi, pr_y), (xi + 6, pr_y)], fill=(50, 50, 50), width=1)
            pd.line([(xi, pr_y + pr_h), (xi + 6, pr_y + pr_h)], fill=(50, 50, 50), width=1)
        for yi in range(pr_y, pr_y + pr_h, 10):
            pd.line([(pr_x, yi), (pr_x, yi + 5)], fill=(50, 50, 50), width=1)
            pd.line([(pr_x + pr_w, yi), (pr_x + pr_w, yi + 5)], fill=(50, 50, 50), width=1)
        pd.text((W // 2, photo_h // 2), "NEWS PHOTO",
                font=_tgf(26, False), fill=(55, 55, 55), anchor="mm")

    card.paste(ph, (0, 0))
    draw = ImageDraw.Draw(card)

    # ── TOP BAR ──────────────────────────────────────────────
    sections = {"uz": "• WORLD", "ru": "• МИРОВЫЕ", "en": "• WORLD"}
    src_str   = (source_name or "1DAY GLOBAL").strip()[:20]
    time_str  = datetime.now().strftime("%H:%M")
    _card_top_bar(draw, W, sections.get(lang, "• WORLD"), f"{src_str}  ·  {time_str}")

    # ── CATEGORY BADGE (foto pastki qismida) ─────────────────
    cat_map = {
        "uz": {"muhim": "• MUHIM",    "tezkor": "• TEZKOR",  "xabar": "• WORLD"},
        "ru": {"muhim": "• ГЛАВНОЕ",  "tezkor": "• СРОЧНО",  "xabar": "• МИРОВЫЕ"},
        "en": {"muhim": "• BREAKING", "tezkor": "• URGENT",  "xabar": "• POLITICS"},
    }
    cat_txt = cat_map.get(lang, cat_map["en"]).get(daraja, "• NEWS")
    cat_y   = photo_h - 52
    bw      = max(len(cat_txt) * 12 + 24, 100)
    draw.rectangle([(12, cat_y), (12 + bw, cat_y + 38)], fill=_C_RED_C)
    draw.text((12 + bw // 2, cat_y + 19), cat_txt,
              font=_tgf(20), fill=_C_WHITE_C, anchor="mm")

    # ── KREM KONTENT QISMI ───────────────────────────────────
    ty          = photo_h + 26
    title_upper = (sarlavha or "").upper()
    for i, line in enumerate(_tw.wrap(title_upper, width=22)[:3]):
        fs = 62 if i == 0 else 56
        draw.text((28, ty), line, font=_tgf(fs), fill=_C_DARK_BG)
        ty += fs + 6

    if jumla1 and ty < H - 130:
        ty += 10
        for line in _tw.wrap(jumla1[:200], width=46)[:2]:
            draw.text((28, ty), line, font=_tgf(24, False), fill=(90, 85, 75))
            ty += 32

    # ── PASTKI BAR ────────────────────────────────────────────
    handles = {"uz": "@birkunday", "ru": "@birkunday_ru", "en": "@birkunday_en"}
    _card_bottom_bar(draw, W, H, handles.get(lang, "@birkunday"))

    card.save(out_path, "JPEG", quality=94)
    return out_path


def _make_digest_card(articles: list, lang: str, out_path: str) -> str:
    """
    1DAY GLOBAL digest card (1080×1080) — Image 1 uslubi:
      · Krem fon, top bar, "DUNYO BUGUN." katta sarlavha
      · Divider chiziq + yangiliklar ro'yxati (01–05)
      · Footer: @handle + 1DAYGLOBAL.NEWS
    """
    if not _PIL_OK:
        return ""

    W, H = _CW, _CH
    card = Image.new("RGB", (W, H), _C_CREAM)
    draw = ImageDraw.Draw(card)

    # ── TOP BAR ──────────────────────────────────────────────
    sections = {"uz": "• KUNLIK DIGEST", "ru": "• ДАЙДЖЕСТ", "en": "• DAILY DIGEST"}
    date_str  = datetime.now().strftime("%d %b").upper()
    n         = min(len(articles), 5)
    _card_top_bar(draw, W, sections.get(lang, "• DAILY DIGEST"),
                  f"{date_str}  ·  {n} MIN")

    # ── KATTA SARLAVHA ────────────────────────────────────────
    hl_map = {
        "uz": ["DUNYO", "BUGUN."],
        "ru": ["МИР", "СЕГОДНЯ."],
        "en": ["THE WORLD", "TODAY."],
    }
    hl_lines = hl_map.get(lang, ["THE WORLD", "TODAY."])
    ty = 74
    for line in hl_lines:
        draw.text((40, ty), line, font=_tgf(112), fill=_C_DARK_BG)
        ty += 122

    # ── DIVIDER ───────────────────────────────────────────────
    div_y = ty + 16
    draw.line([(40, div_y), (W - 40, div_y)], fill=_C_DIV, width=1)

    # ── STORY RO'YXATI ────────────────────────────────────────
    # Kalit so'zdan region kodi olish
    _kw_codes = {
        "g7": "G7", "summit": "G7", "russia": "RU", "ukraine": "UA",
        "usa": "US", "america": "US", "trump": "US", "china": "CN",
        "india": "IN", "europe": "EU", "iran": "IR", "israel": "IL",
        "korea": "KR", "tajikistan": "TJ", "mars": "MA", "space": "SP",
        "climate": "CL", "market": "MK", "tashkent": "UZ", "uzbek": "UZ",
        "pakistan": "PK", "türk": "TR", "africa": "AF",
    }

    list_y = div_y + 26
    for i, art in enumerate(articles[:5], 1):
        sarlavha = (art.get("sarlavha") or "").strip()
        if not sarlavha:
            continue
        if i > 1:
            draw.line([(40, list_y - 10), (W - 40, list_y - 10)],
                      fill=_C_DIV, width=1)

        # Raqam (qizil)
        draw.text((40, list_y + 20), f"{i:02d}",
                  font=_tgf(20), fill=_C_RED_C, anchor="lm")

        # Region kodi (kulrang)
        code = "WD"
        for kw, cd in _kw_codes.items():
            if kw in sarlavha.lower():
                code = cd; break
        draw.text((100, list_y + 20), code,
                  font=_tgf(16, False), fill=_C_GRAY_C, anchor="lm")

        # Sarlavha matni
        title_line = _tw.wrap(sarlavha, width=38)[:1]
        if title_line:
            draw.text((155, list_y + 20), title_line[0],
                      font=_tgf(28), fill=_C_DARK_BG, anchor="lm")

        list_y += 78

    # ── PASTKI BAR ────────────────────────────────────────────
    handles = {"uz": "@birkunday", "ru": "@birkunday_ru", "en": "@birkunday_en"}
    _card_bottom_bar(draw, W, H, handles.get(lang, "@birkunday"))

    card.save(out_path, "JPEG", quality=94)
    return out_path


def _trim_to_sentence(text: str, max_chars: int) -> str:
    """Matnni max_chars belgiga sig'dirish — lekin to'liq gap bilan tugatish.
    Gap oxiri: . ! ? belgisi. Agar gap topilmasa — so'z bilan kesadi."""
    if len(text) <= max_chars:
        return text
    chunk = text[:max_chars]
    # Oxirgi . ! ? ni topamiz
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        idx = chunk.rfind(sep)
        if idx > max_chars // 2:   # Kamida yarmidan keyin bo'lsin
            return chunk[:idx + 1].strip()
    # Gap topilmasa — oxirgi so'z bilan kesish
    idx = chunk.rfind(" ")
    if idx > 0:
        return chunk[:idx].strip()
    return chunk.strip()


def make_post(sarlavha, jumla1, jumla2, daraja, hashtaglar, location, lang="uz"):
    # Har bir paragraf uchun max belgi (to'liq gap bilan tugaydi)
    MAX_J1 = 500
    MAX_J2 = 400

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

    # ── Footer (doim chiqishi kerak) ──────────────────────────
    yt_label = {
        "uz": "🎬 1Kun | Global News",
        "ru": "🎬 1День | Global News",
        "en": "🎬 1Day | Global News",
    }.get(lang, "🎬 1Day | Global News")
    yt_link = "https://www.youtube.com/@1kunnews"
    _cross_lines = {
        "uz": [f"🇷🇺 {TELEGRAM_CHANNEL_RU}", f"🇬🇧 {TELEGRAM_CHANNEL_EN}"],
        "ru": [f"🇺🇿 {TELEGRAM_CHANNEL_UZ}", f"🇬🇧 {TELEGRAM_CHANNEL_EN}"],
        "en": [f"🇺🇿 {TELEGRAM_CHANNEL_UZ}", f"🇷🇺 {TELEGRAM_CHANNEL_RU}"],
    }
    footer = ""
    if location:
        footer += f"📍 {location}\n"
    footer += f"{vaqt}\n"
    footer += f"📰 {kanal}\n"
    footer += f'{yt_label}: <a href="{yt_link}">{yt_link}</a>\n'
    for ch in _cross_lines.get(lang, []):
        footer += f"{ch}\n"
    footer += f"\n{hashtaglar}"

    # ── Header ────────────────────────────────────────────────
    _sv = (sarlavha or "").strip()
    header = belgi + "\n\n"
    if _sv and len(_sv) >= 8:
        header += f"⚡ <b>{_sv}</b>\n\n"

    # ── Body — to'liq gap bilan tugaydi ──────────────────────
    _j1 = (jumla1 or "").strip()
    _j2 = (jumla2 or "").strip()

    # Har bir paragrafni to'liq gap bilan kesish
    _j1 = _trim_to_sentence(_j1, MAX_J1)
    _j2 = _trim_to_sentence(_j2, MAX_J2) if _j2 else ""

    body = ""
    if _j1 and _j1 != _sv:
        body += f"{j1e} {_j1}\n\n"
    if _j2 and _j2 != _j1 and _j2 != _sv:
        body += f"{j2e} {_j2}\n\n"

    return header + body + footer


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

    # 2. Pexels — aniq qidiruv so'rovlari (noto'g'ri odam rasmi chiqmasin)
    queries = []
    title = article.get("title", "")
    # Birinchi urinish: to'liq sarlavha (eng aniq)
    if title and len(title) > 10:
        queries.append(title[:80])
    # Ikkinchi urinish: keywords (shaxs + joylashuv birlashtirish)
    if keywords_en and len(keywords_en) >= 2:
        # Faqat birinchi 2 ta kalit so'z — juda ko'p so'z noto'g'ri rasm beradi
        queries.append(" ".join(keywords_en[:2]))
    # Uchinchi urinish: bitta eng muhim kalit so'z
    if keywords_en:
        queries.append(keywords_en[0])
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

    tl = text.lower()

    # ── 1. Aniq UZ lotin belgilari → darrov kirill ───────────
    _UZ_POSITIVE = (
        "o'", "g'", "o'", "g'",            # apostrof belgilari
        "o'z", "bo'l", "ko'r", "so'z",
        "qildi", "qilmo", "etdi", "etmo",
        "moqda", "yotir", "turip",
        "ning ", "dagi ", "ligi ", "likni",
        "larni", "lardan", "larga",
        "haqida", "uchun", "bilan",
        "yangi ", "davom", "keyin",
        "eronni", "ukraina", "rossiya",     # O'zbek joy nomlari
        "tramp", "zelenskiy", "bayden",     # O'zbek shaxs nomlari
    )
    if any(m in tl for m in _UZ_POSITIVE):
        return lat2cyr(text)

    # ── 2. Aniq ingliz so'zlari → qaytarmaymiz ───────────────
    _EN_WORDS = {
        # Function so'zlar
        "the", "and", "for", "that", "this", "with", "from", "have",
        "will", "not", "are", "was", "were", "has", "its", "their",
        "over", "amid", "into", "about", "after", "before", "between",
        # Ingliz yangilik so'zlari
        "says", "said", "warns", "calls", "hits", "strikes", "kills",
        "deal", "talks", "tells", "vows", "seeks", "urges", "backs",
        "nuclear", "military", "government", "president", "minister",
        "sanctions", "ceasefire", "offensive", "invasion", "attack",
    }
    words = tl.split()
    en_hits = sum(1 for w in words if w.strip(",.!?:;") in _EN_WORDS)
    total_words = max(len(words), 1)
    if en_hits / total_words >= 0.25:
        # Inglizcha matn — o'tkazish
        log.warning(f"_ensure_cyr: inglizcha matn ({en_hits}/{total_words} EN so'z) — o'tkazildi: '{text[:50]}'")
        return ""

    # ── 3. O'zbek qo'shimchalari → kirill ────────────────────
    _UZ_SUFFIXES = (
        "da ", "ga ", "ni ", "ni.", "da.", "ga.",
        "dan ", "dan.", "lar ", "lar.", "ni,", "da,",
    )
    if any(m in tl for m in _UZ_SUFFIXES):
        return lat2cyr(text)

    # ── 4. Noaniql — harflarning yarmi ASCII va lotin → urinib ko'rish ──
    ascii_letters = sum(1 for c in letters if c.isascii())
    if ascii_letters / len(letters) >= 0.70:
        # Asosan lotin — kirill qilib ko'ramiz
        result = lat2cyr(text)
        if result and result != text:
            log.debug(f"_ensure_cyr: fallback lotin→kirill: '{text[:40]}'")
            return result

    log.warning(f"_ensure_cyr: aniqlanmadi — bo'sh: '{text[:60]}'")
    return ""


def _has_body(jumla1: str, jumla2: str = "", min_chars: int = 20) -> bool:
    """Post matni yetarli ekanligini tekshirish.
    Kamida bitta jumla min_chars belgidan uzun bo'lishi kerak.
    min_chars 60 dan 20 ga tushirildi — qisqa tarjimalar ham o'tsin."""
    j1 = (jumla1 or "").strip()
    j2 = (jumla2 or "").strip()
    return len(j1) >= min_chars or len(j2) >= min_chars


def send_all_languages(d, article):
    """3 tilda (UZ/RU/EN) Telegram kanallariga post yuborish.
    Matn (jumla1) bo'sh yoki juda qisqa (<60 harf) bo'lsa — post yuborilmaydi."""
    daraja = d.get("daraja", "xabar")

    # ── Rasm bir marta topib, 3 kanalga ishlatish ─────────────
    _ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    _kws       = d.get("keywords_en", [])
    _raw_photo = _find_article_photo(article, _kws, f"tg_{_ts}")
    _source    = (article.get("source") or article.get("link", "")
                  .replace("https://","").replace("http://","").split("/")[0])[:22]

    def _make_card(sarlavha, jumla1, lang, suffix):
        """Lang uchun brand news card yasash. Muvaffaqiyatsiz bo'lsa — None."""
        if not _PIL_OK:
            return None
        try:
            cp = str(_pl.Path(_tf.gettempdir()) / f"tg_card_{_ts}_{suffix}.jpg")
            if _make_news_card(_raw_photo, sarlavha, jumla1, daraja, _source, lang, cp):
                return cp
        except Exception as _e:
            log.warning(f"News card [{lang}]: {_e}")
        return _raw_photo   # fallback — raw photo

    # Ko'rsatish uchun photo_path (raw uchun eski kod bilan ham mos)
    photo_path = _raw_photo

    # ── O'ZBEK → @birkunday (LOTIN alifbosida) ───────────────
    # UZ kanal FAQAT LOTIN — _ensure_cyr ishlatilmaydi
    _sarlavha_uz = (d.get("sarlavha_uz", "") or "").strip()
    _j1_uz       = (d.get("jumla1_uz",   "") or "").strip()
    _j2_uz       = (d.get("jumla2_uz",   "") or "").strip()

    # Fallback: jumla bo'sh bo'lsa — script_uz dan olish (450-500 so'z)
    if not _j1_uz:
        _script_uz = (d.get("script_uz", "") or d.get("youtube_script_latin", "") or "").strip()
        if _script_uz and len(_script_uz) > 60:
            # Script dan dastlabki 2 gap (Telegram uchun)
            import re as _re
            _sents = _re.split(r'(?<=[.!?])\s+', _script_uz)
            _j1_uz = " ".join(_sents[:3])[:500].strip()
            _j2_uz = _j2_uz or " ".join(_sents[3:6])[:400].strip()
            log.info("  UZ jumla: script_uz dan olinmoqda")

    # Agar lotin UZ bo'sh bo'lsa — inglizcha sarlavhadan fallback
    if not _sarlavha_uz:
        _sarlavha_uz = (d.get("sarlavha_en", "") or "").strip()
        if _sarlavha_uz:
            log.warning("⚠️  sarlavha_uz bo'sh — sarlavha_en ishlatilmoqda")
    if not _sarlavha_uz:
        log.warning("⚠️  sarlavha_uz bo'sh — UZ post o'tkazildi")
    elif not _has_body(_j1_uz, _j2_uz):
        log.warning(f"⚠️  UZ matn juda qisqa ({len(_j1_uz)} harf) — UZ post o'tkazildi")
    else:
        post_uz  = make_post(_sarlavha_uz, _j1_uz, _j2_uz,
                             daraja, d.get("hashtag_uz", "#Yangilik #1KUN"),
                             d.get("location_uz", ""), "uz")
        _card_uz = _make_card(_sarlavha_uz, _j1_uz, "uz", "uz")
        if _send_with_photo(post_uz, TELEGRAM_CHANNEL_UZ, _card_uz):
            log.info(f"✅ Telegram UZ → {TELEGRAM_CHANNEL_UZ}")

    # ── RUS → @birkunday_ru ───────────────────────────────────
    _sarlavha_ru = (d.get("sarlavha_ru", "") or "").strip()
    _j1_ru       = (d.get("jumla1_ru",   "") or "").strip()
    _j2_ru       = (d.get("jumla2_ru",   "") or "").strip()

    if not _j1_ru:
        _script_ru = (d.get("script_ru", "") or "").strip()
        if _script_ru and len(_script_ru) > 60:
            import re as _re2
            _sents_ru = _re2.split(r'(?<=[.!?])\s+', _script_ru)
            _j1_ru = " ".join(_sents_ru[:3])[:500].strip()
            _j2_ru = _j2_ru or " ".join(_sents_ru[3:6])[:400].strip()
            log.info("  RU jumla: script_ru dan olinmoqda")

    # Fallback: sarlavha_ru bo'sh bo'lsa — sarlavha_en ishlatish
    if not _sarlavha_ru:
        _sarlavha_ru = (d.get("sarlavha_en", "") or "").strip()
        if _sarlavha_ru:
            log.warning("⚠️  sarlavha_ru bo'sh — sarlavha_en ishlatilmoqda")
    if not _sarlavha_ru:
        log.warning("⚠️  sarlavha_ru bo'sh — RU post o'tkazildi")
    elif not _has_body(_j1_ru, _j2_ru):
        log.warning(f"⚠️  RU matn juda qisqa ({len(_j1_ru)} harf) — RU post o'tkazildi")
    else:
        post_ru  = make_post(_sarlavha_ru, _j1_ru, _j2_ru,
                             daraja, d.get("hashtag_ru", "#Новости #1День"),
                             d.get("location_ru", ""), "ru")
        _card_ru = _make_card(_sarlavha_ru, _j1_ru, "ru", "ru")
        if _send_with_photo(post_ru, TELEGRAM_CHANNEL_RU, _card_ru):
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
        post_en  = make_post(d["sarlavha_en"], _j1_en, _j2_en,
                             daraja, d["hashtag_en"], d.get("location_en", ""), "en")
        _card_en = _make_card(d.get("sarlavha_en",""), _j1_en, "en", "en")
        if _send_with_photo(post_en, TELEGRAM_CHANNEL_EN, _card_en):
            log.info(f"✅ Telegram EN → {TELEGRAM_CHANNEL_EN}")

    # Vaqtinchalik fayllarni o'chirish
    for _p in [_raw_photo]:
        if _p:
            try: os.remove(_p)
            except Exception: pass


# ══════════════════════════════════════════════════════════════
# EN kanalga RSS DAN BEVOSITA post (tarjima kutilmaydi)
# ══════════════════════════════════════════════════════════════
def send_en_from_rss(article: dict) -> bool:
    """
    Inglizcha kanalga RSS manbadan bevosita post yuborish.
    AI tarjima kutilmaydi — darhol chop etiladi.
    article: {"title": str, "description": str, "link": str, "source": str}
    """
    title = (article.get("title", "") or "").strip()
    desc  = (article.get("description", "") or "").strip()
    source = (article.get("source", "") or "").strip()
    link   = article.get("link", "")

    if not title:
        log.warning("send_en_from_rss: title bo'sh — o'tkazildi")
        return False

    # Matnni 2 qismga bo'lish (jumla1 + jumla2)
    sents = re.split(r'(?<=[.!?…])\s+', desc.strip()) if desc else []
    if len(sents) >= 4:
        mid = len(sents) // 2
        j1 = " ".join(sents[:mid]).strip()
        j2 = " ".join(sents[mid:]).strip()
    else:
        j1 = desc[:500]
        j2 = ""

    # Hashtag — sarlavhadan kalit so'zlar
    kw_words = [w for w in title.split() if len(w) > 4 and w[0].isupper()][:3]
    hashtags = " ".join(f"#{w}" for w in kw_words) + " #News #World #1Day"

    post = make_post(title, j1, j2, "xabar", hashtags, "", "en")

    # Rasm: og:image dan
    _ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    photo = None
    og_p  = _pl.Path(_tf.gettempdir()) / f"tg_en_og_{_ts}.jpg"
    if _fetch_og_image(link, str(og_p)):
        photo = str(og_p)
    elif not photo:
        # Fallback: Pexels sarlavhadan
        px_p = _pl.Path(_tf.gettempdir()) / f"tg_en_px_{_ts}.jpg"
        if _fetch_pexels(title[:60], str(px_p)):
            photo = str(px_p)

    ok = _send_with_photo(post, TELEGRAM_CHANNEL_EN, photo)
    if ok:
        log.info(f"✅ Telegram EN (RSS) → {TELEGRAM_CHANNEL_EN}")
    else:
        log.warning(f"⚠️  Telegram EN (RSS) yuborishda xato")

    # Vaqtinchalik faylni o'chirish
    for p in [photo]:
        if p:
            try: os.remove(p)
            except Exception: pass

    return ok


def send_uz_ru_languages(d: dict, article: dict):
    """
    Faqat UZ va RU kanallariga AI tarjima asosida post yuborish.
    EN kanal allaqachon RSS dan yuborilgan — bu yerda EN chiqarilmaydi.
    """
    daraja = d.get("daraja", "xabar")
    _ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    _kws   = d.get("keywords_en", [])
    _raw_photo = _find_article_photo(article, _kws, f"tg_{_ts}")
    _source = (article.get("source") or article.get("link", "")
               .replace("https://","").replace("http://","").split("/")[0])[:22]

    def _make_card(sarlavha, jumla1, lang, suffix):
        if not _PIL_OK:
            return None
        try:
            cp = str(_pl.Path(_tf.gettempdir()) / f"tg_card_{_ts}_{suffix}.jpg")
            if _make_news_card(_raw_photo, sarlavha, jumla1, daraja, _source, lang, cp):
                return cp
        except Exception as _e:
            log.warning(f"News card [{lang}]: {_e}")
        return _raw_photo

    # ── O'ZBEK ───────────────────────────────────────────────────
    _sarlavha_uz = (d.get("sarlavha_uz", "") or "").strip()
    _j1_uz = (d.get("jumla1_uz", "") or "").strip()
    _j2_uz = (d.get("jumla2_uz", "") or "").strip()

    if not _j1_uz:
        _script_uz = (d.get("script_uz", "") or "").strip()
        if _script_uz and len(_script_uz) > 60:
            _sents = re.split(r'(?<=[.!?])\s+', _script_uz)
            _j1_uz = " ".join(_sents[:3])[:500].strip()
            _j2_uz = _j2_uz or " ".join(_sents[3:6])[:400].strip()

    if not _sarlavha_uz:
        _sarlavha_uz = (d.get("sarlavha_en", "") or "").strip()
    if not _sarlavha_uz:
        log.warning("⚠️  sarlavha_uz bo'sh — UZ post o'tkazildi")
    elif not _has_body(_j1_uz, _j2_uz):
        log.warning(f"⚠️  UZ matn qisqa — UZ post o'tkazildi")
    else:
        post_uz  = make_post(_sarlavha_uz, _j1_uz, _j2_uz,
                             daraja, d.get("hashtag_uz","#Yangilik #1KUN"),
                             d.get("location_uz",""), "uz")
        _card_uz = _make_card(_sarlavha_uz, _j1_uz, "uz", "uz")
        if _send_with_photo(post_uz, TELEGRAM_CHANNEL_UZ, _card_uz):
            log.info(f"✅ Telegram UZ → {TELEGRAM_CHANNEL_UZ}")

    # ── RUS ───────────────────────────────────────────────────────
    _sarlavha_ru = (d.get("sarlavha_ru", "") or "").strip()
    _j1_ru = (d.get("jumla1_ru", "") or "").strip()
    _j2_ru = (d.get("jumla2_ru", "") or "").strip()

    if not _j1_ru:
        _script_ru = (d.get("script_ru", "") or "").strip()
        if _script_ru and len(_script_ru) > 60:
            _sents_ru = re.split(r'(?<=[.!?])\s+', _script_ru)
            _j1_ru = " ".join(_sents_ru[:3])[:500].strip()
            _j2_ru = _j2_ru or " ".join(_sents_ru[3:6])[:400].strip()

    if not _sarlavha_ru:
        _sarlavha_ru = (d.get("sarlavha_en", "") or "").strip()
        if _sarlavha_ru:
            log.warning("⚠️  sarlavha_ru bo'sh — sarlavha_en ishlatilmoqda")
    if not _sarlavha_ru:
        log.warning("⚠️  sarlavha_ru bo'sh — RU post o'tkazildi")
    elif not _has_body(_j1_ru, _j2_ru):
        log.warning(f"⚠️  RU matn qisqa — RU post o'tkazildi")
    else:
        post_ru  = make_post(_sarlavha_ru, _j1_ru, _j2_ru,
                             daraja, d.get("hashtag_ru","#Новости #1День"),
                             d.get("location_ru",""), "ru")
        _card_ru = _make_card(_sarlavha_ru, _j1_ru, "ru", "ru")
        if _send_with_photo(post_ru, TELEGRAM_CHANNEL_RU, _card_ru):
            log.info(f"✅ Telegram RU → {TELEGRAM_CHANNEL_RU}")

    for _p in [_raw_photo]:
        if _p:
            try: os.remove(_p)
            except Exception: pass


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

    # ── Brand karta yasash (Image 1 uslubi) ──────────────────
    _ts_d    = datetime.now().strftime("%Y%m%d_%H%M%S")
    card_path = None
    if _PIL_OK:
        try:
            card_path = str(_pl.Path(_tf.gettempdir()) / f"dg_card_{_ts_d}_{lang}.jpg")
            if not _make_digest_card(articles[:5], lang, card_path):
                card_path = None
        except Exception as _e:
            log.warning(f"Digest card [{lang}]: {_e}")
            card_path = None

    # ── Matn caption (karta tavsifi) ─────────────────────────
    digest_title = {
        "uz": "📋 KUNNING ASOSIY YANGILIKLARI",
        "ru": "📋 ГЛАВНЫЕ НОВОСТИ ДНЯ",
        "en": "📋 TODAY'S TOP NEWS",
    }.get(lang, "📋 TOP NEWS")

    vaqt  = datetime.now(TASHKENT).strftime("🕐 %H:%M | %d.%m.%Y")
    kanal = channel

    post = f"<b>{digest_title}</b>\n"
    post += "━" * 28 + "\n\n"
    for i, art in enumerate(articles[:6], 1):
        sarlavha = art.get("sarlavha", "").strip()
        jumla    = art.get("jumla1", "").strip()
        daraja_a = art.get("daraja", "xabar")
        emoji    = {"muhim": "🔴", "tezkor": "🟠"}.get(daraja_a, "🟢")
        if sarlavha:
            post += f"{i}. {emoji} <b>{sarlavha}</b>\n"
        if jumla:
            post += f"   {jumla[:120]}{'...' if len(jumla) > 120 else ''}\n"
        post += "\n"
    post += "━" * 28 + "\n"
    post += f"{vaqt}\n"
    post += f"📰 {kanal}"

    # ── Yuborish (karta bo'lsa — sendPhoto, bo'lmasa — matn) ─
    if card_path:
        ok = send_telegram_photo(post, card_path, channel)
        try: os.remove(card_path)
        except Exception: pass
        if not ok:
            ok = send_telegram(post, channel)
    else:
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
