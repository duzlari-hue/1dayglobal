"""
social_poster.py — Ko'p platformali avtomatik postlash moduli

Qo'llab-quvvatlanadigan platformalar:
  ✅ Telegram — video + matn (Bot API sendVideo)
  ✅ Facebook Sahifa (Page) — video yuklash (Graph API)
  ✅ Instagram Reels — 9:16 video (Graph API, ikki bosqich)
  ❌ Facebook shaxsiy profil — 2018 dan API berk (Meta taqiqlagan)

.env da kerakli kalitlar:
  TELEGRAM_BOT_TOKEN       — mavjud
  TELEGRAM_CHANNEL_UZ/RU/EN — mavjud
  FB_PAGE_ID               — Facebook Sahifa ID (raqam)
  FB_PAGE_ACCESS_TOKEN     — Uzun muddatli Sahifa tokeni
  IG_USER_ID               — Instagram Business akkaunt ID
  IG_ACCESS_TOKEN          — Instagram Graph API tokeni (FB Page token ishlaydi)
"""

import os
import sys
import time
import logging
import requests
from datetime import date

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(".env")

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Sozlamalar (.env dan)
# ─────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_UZ   = os.getenv("TELEGRAM_CHANNEL_UZ", "")
TELEGRAM_CHANNEL_RU   = os.getenv("TELEGRAM_CHANNEL_RU", "")
TELEGRAM_CHANNEL_EN   = os.getenv("TELEGRAM_CHANNEL_EN", "")

FB_PAGE_ID            = os.getenv("FB_PAGE_ID", "")
FB_PAGE_ACCESS_TOKEN  = os.getenv("FB_PAGE_ACCESS_TOKEN", "")

IG_USER_ID            = os.getenv("IG_USER_ID", "")
IG_ACCESS_TOKEN       = os.getenv("IG_ACCESS_TOKEN", "")   # FB Page token ishlaydi

FB_GRAPH              = "https://graph.facebook.com/v19.0"
TG_BASE               = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ─────────────────────────────────────────────────────────────
# Kanal xaritasi
# ─────────────────────────────────────────────────────────────
_TG_CHANNELS = {
    "uz": TELEGRAM_CHANNEL_UZ,
    "ru": TELEGRAM_CHANNEL_RU,
    "en": TELEGRAM_CHANNEL_EN,
}

# Telegram kanal to'liq URL lari (Facebook postlar uchun)
TG_LINKS = (
    "📢 Телеграм каналлар:\n"
    "🇺🇿 https://t.me/birkunday\n"
    "🇷🇺 https://t.me/birkunday_ru\n"
    "🌍 https://t.me/birkunday_en"
)

# ─────────────────────────────────────────────────────────────
# O'zbek lotin → kirill transliteratsiya
# ─────────────────────────────────────────────────────────────
def _uz_lat_to_cyr(text: str) -> str:
    """O'zbek lotin yozuvini kirill yozuviga o'tkazish."""
    if not text:
        return text
    # Ko'p belgili almashtirishlar (tartib muhim)
    pairs = [
        ("Sh", "Ш"), ("sh", "ш"),
        ("Ch", "Ч"), ("ch", "ч"),
        ("Ng", "Нг"), ("ng", "нг"),
        ("O'", "Ў"), ("o'", "ў"),
        ("O`", "Ў"), ("o`", "ў"),
        ("G'", "Ғ"), ("g'", "ғ"),
        ("G`", "Ғ"), ("g`", "ғ"),
        ("Yo", "Ё"), ("yo", "ё"),
        ("Yu", "Ю"), ("yu", "ю"),
        ("Ya", "Я"), ("ya", "я"),
        ("'",  "ъ"), ("`",  "ъ"),
        ("A", "А"), ("a", "а"),
        ("B", "Б"), ("b", "б"),
        ("D", "Д"), ("d", "д"),
        ("E", "Е"), ("e", "е"),
        ("F", "Ф"), ("f", "ф"),
        ("G", "Г"), ("g", "г"),
        ("H", "Ҳ"), ("h", "ҳ"),
        ("I", "И"), ("i", "и"),
        ("J", "Ж"), ("j", "ж"),
        ("K", "К"), ("k", "к"),
        ("L", "Л"), ("l", "л"),
        ("M", "М"), ("m", "м"),
        ("N", "Н"), ("n", "н"),
        ("O", "О"), ("o", "о"),
        ("P", "П"), ("p", "п"),
        ("Q", "Қ"), ("q", "қ"),
        ("R", "Р"), ("r", "р"),
        ("S", "С"), ("s", "с"),
        ("T", "Т"), ("t", "т"),
        ("U", "У"), ("u", "у"),
        ("V", "В"), ("v", "в"),
        ("X", "Х"), ("x", "х"),
        ("Y", "Й"), ("y", "й"),
        ("Z", "З"), ("z", "з"),
    ]
    result = text
    for lat, cyr in pairs:
        result = result.replace(lat, cyr)
    return result


def _to_cyrillic(text: str, lang: str) -> str:
    """Matnni kirill yozuviga o'tkazish (faqat UZ uchun)."""
    if lang == "uz":
        return _uz_lat_to_cyr(text)
    return text  # RU allaqachon kirill

# ─────────────────────────────────────────────────────────────
# Yordamchi: hashteglar
# ─────────────────────────────────────────────────────────────
def _hashtags(lang: str, daraja: str = "xabar") -> str:
    base = {
        "uz": "#Янгиликлар #ДунёЯнгиликлари #1КунGlobal #Дунё #Сиёсат",
        "ru": "#Новости #МировыеНовости #1ДеньGlobal #Мир #Политика",
        "en": "#News #WorldNews #1DayGlobal #World #BreakingNews",
    }.get(lang, "#News #WorldNews")
    if daraja == "muhim":
        base += " #MUHIM" if lang == "uz" else (" #СРОЧНО" if lang == "ru" else " #BREAKING")
    return base


def _caption(sarlavha: str, jumla: str, lang: str, daraja: str,
             yt_url: str = "", location: str = "") -> str:
    """Telegram/Facebook uchun post matni — telegram_bot.py bilan bir xil format."""
    from datetime import datetime
    import pytz
    TASHKENT = pytz.timezone("Asia/Tashkent")

    if daraja == "muhim":
        belgi = {"uz": "🔴 MUHIM", "ru": "🔴 ВАЖНО", "en": "🔴 BREAKING"}.get(lang, "🔴")
        j1e = "🔴"
    elif daraja == "tezkor":
        belgi = {"uz": "🟠 TEZKOR", "ru": "🟠 СРОЧНО", "en": "🟠 URGENT"}.get(lang, "🟠")
        j1e = "⚡"
    else:
        belgi = {"uz": "🟢 XABAR", "ru": "🟢 НОВОСТЬ", "en": "🟢 NEWS"}.get(lang, "🟢")
        j1e = "📌"

    kanal = {
        "uz": "@birkunday",
        "ru": "@birkunday_ru",
        "en": "@birkunday_en",
    }.get(lang, "@birkunday")
    vaqt  = datetime.now(TASHKENT).strftime("🕐 %H:%M | %d.%m.%Y")

    post = belgi + "\n\n"
    if sarlavha and len(sarlavha.strip()) >= 4:
        post += f"⚡ <b>{sarlavha.strip()}</b>\n\n"
    if jumla:
        post += f"{j1e} {jumla}\n\n"
    if location:
        post += f"📍 {location}\n"
    post += vaqt + "\n"
    if yt_url:
        post += f"▶️ {yt_url}\n"
    post += f"📰 {kanal}\n\n"
    post += _hashtags(lang, daraja)
    return post


# ═══════════════════════════════════════════════════════════════
# 1. TELEGRAM — video yuborish
# ═══════════════════════════════════════════════════════════════
def post_telegram_video(
        video_path: str,
        sarlavha: str,
        jumla: str,
        lang: str,
        daraja: str = "xabar",
        yt_url: str = "",
        location: str = "",
        channel: str = None,
        caption: str = "",      # Tayyor caption (bo'lsa _caption() chaqirilmaydi)
) -> bool:
    """
    Telegram kanaliga video yuborish.
    video_path — local .mp4 fayl
    channel    — None bo'lsa lang dan avtomatik aniqlanadi
    caption    — tayyor matn (digest uchun); bo'lsa sarlavha/jumla ishlatilmaydi
    """
    if not TELEGRAM_BOT_TOKEN:
        log.warning("  ⚠️  TELEGRAM_BOT_TOKEN yo'q")
        return False

    ch = channel or _TG_CHANNELS.get(lang, "")
    if not ch:
        log.warning(f"  ⚠️  Telegram kanal topilmadi ({lang})")
        return False

    # Tayyor caption bo'lsa → ishlatamiz, bo'lmasa → _caption() bilan yasaymiz
    if caption:
        cap = caption[:1020]
    else:
        cap = _caption(sarlavha, jumla, lang, daraja, yt_url, location)
    # Telegram max caption = 1024 belgi
    cap = cap[:1020]

    if not os.path.exists(video_path):
        log.warning(f"  ⚠️  Video fayl yo'q: {video_path}")
        return False

    file_size_mb = os.path.getsize(video_path) / 1_048_576
    if file_size_mb > 50:
        # Katta fayl: faqat link yuboramiz
        log.info(f"  📦 Fayl {file_size_mb:.0f}MB > 50MB — link yuboriladi")
        return _post_telegram_text(ch, cap)

    try:
        with open(video_path, "rb") as vf:
            r = requests.post(
                f"{TG_BASE}/sendVideo",
                data={
                    "chat_id":              ch,
                    "caption":              cap,
                    "parse_mode":           "HTML",
                    "supports_streaming":   True,
                    "width":                1280,
                    "height":              720,
                },
                files={"video": vf},
                timeout=300,
            )
        resp = r.json()
        if resp.get("ok"):
            msg_id = resp.get("result", {}).get("message_id")
            log.info(f"  ✅ Telegram [{lang.upper()}] → {ch} (msg_id={msg_id})")
            return msg_id   # int yoki None
        else:
            desc = resp.get("description", "")
            log.warning(f"  ⚠️  Telegram [{lang.upper()}] xato: {desc}")
            return None
    except Exception as e:
        log.error(f"  Telegram video xato ({lang}): {e}")
        return None


def _post_telegram_text(channel: str, text: str) -> bool:
    """Faqat matn post (video katta bo'lganda)."""
    try:
        r = requests.post(
            f"{TG_BASE}/sendMessage",
            json={
                "chat_id":                  channel,
                "text":                     text[:4000],
                "parse_mode":               "HTML",
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        return r.json().get("ok", False)
    except Exception:
        return False


def post_telegram_all_langs(
        videos: dict,          # {"uz": path, "ru": path, "en": path}
        sarlavhalar: dict,     # {"uz": str, "ru": str, "en": str}
        jumlalar: dict,        # {"uz": str, "ru": str, "en": str}
        daraja: str = "xabar",
        yt_urls: dict = None,  # {"uz": url, "ru": url, "en": url}
        location: dict = None,
) -> dict:
    """3 tilda Telegram kanallariga video yuborish."""
    results = {}
    for lang in ("uz", "ru", "en"):
        vpath = videos.get(lang, "")
        if not vpath:
            continue
        results[lang] = post_telegram_video(
            video_path = vpath,
            sarlavha   = sarlavhalar.get(lang, ""),
            jumla      = jumlalar.get(lang, ""),
            lang       = lang,
            daraja     = daraja,
            yt_url     = (yt_urls or {}).get(lang, ""),
            location   = (location or {}).get(lang, ""),
        )
    return results


# ═══════════════════════════════════════════════════════════════
# 2. FACEBOOK SAHIFA — YouTube linki elon qilish (feed post)
# ═══════════════════════════════════════════════════════════════
def post_facebook_yt_link(
        yt_url: str,
        title: str,
        description: str,
        lang: str = "uz",
        daraja: str = "xabar",
        location: str = "",
) -> str | None:
    """
    YouTube videosi yuklanganda Facebook Sahifaga link post qilish.
    Facebook yt_url dan thumbnail va preview avtomatik oladi.

    .env da:
      FB_PAGE_ID           — Sahifa raqami
      FB_PAGE_ACCESS_TOKEN — Uzun muddatli token
    """
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        log.warning("  ⚠️  FB_PAGE_ID yoki FB_PAGE_ACCESS_TOKEN yo'q")
        return None
    if not yt_url:
        log.warning("  ⚠️  YouTube URL yo'q, Facebook link post o'tkazildi")
        return None

    emoji = {"muhim": "🔴", "tezkor": "🟠"}.get(daraja, "🟢")
    today = date.today().strftime("%d.%m.%Y")
    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1DAY GLOBAL")
    tags = _hashtags(lang, daraja)

    # Kirill yozuviga o'tkazish
    title_cyr = _to_cyrillic(title, lang)
    desc_cyr  = _to_cyrillic(description, lang)
    loc_cyr   = _to_cyrillic(location, lang)

    message = f"{emoji} {title_cyr}"
    if desc_cyr:
        message += f"\n\n{desc_cyr}"
    if loc_cyr:
        message += f"\n📍 {loc_cyr}"
    message += f"\n\n🗓 {today}\n{tags}\n📡 {brand}"
    message += f"\n\n{TG_LINKS}"

    log.info(f"  🔗 Facebook YT link post ({lang.upper()}): {yt_url[:60]}...")
    try:
        r = requests.post(
            f"{FB_GRAPH}/{FB_PAGE_ID}/feed",
            data={
                "link":         yt_url,
                "message":      message[:5000],
                "access_token": FB_PAGE_ACCESS_TOKEN,
            },
            timeout=30,
        )
        data = r.json()
        if "id" in data:
            post_id = data["id"]
            log.info(f"  ✅ Facebook YT link post: {post_id}")
            return post_id
        elif "error" in data:
            err = data["error"]
            log.error(f"  ❌ Facebook YT link xato: {err.get('message', err)}")
            return None
        else:
            log.warning(f"  ⚠️  Facebook YT link noma'lum javob: {data}")
            return None
    except Exception as e:
        log.error(f"  Facebook YT link post xato: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# 3. FACEBOOK SAHIFA — matnli post (Telegram nusxasi)
# ═══════════════════════════════════════════════════════════════
def post_facebook_text(
        sarlavha: str,
        jumla: str,
        lang: str,
        daraja: str = "xabar",
        yt_url: str = "",
        location: str = "",
        tg_post_url: str = "",   # masalan: https://t.me/birkunday/1097
) -> str | None:
    """
    Facebook Sahifaga oddiy matnli post (Telegram postiga mos).
    Telegram UZ/RU ga post ketganida shu funksiya chaqiriladi.
    tg_post_url — Telegram xabar to'g'ridan havolasi (post ostiga qo'yiladi).
    """
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        log.warning("  ⚠️  FB_PAGE_ID yoki FB_PAGE_ACCESS_TOKEN yo'q")
        return None

    # Kirill yozuviga o'tkazish
    sarlavha_cyr = _to_cyrillic(sarlavha, lang)
    jumla_cyr    = _to_cyrillic(jumla, lang)
    location_cyr = _to_cyrillic(location, lang)

    emoji = {"muhim": "🔴", "tezkor": "🟠"}.get(daraja, "🟢")
    today = date.today().strftime("%d.%m.%Y")
    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1DAY GLOBAL")
    tags  = _hashtags(lang, daraja)

    # Tuzilma: sarlavha + to'liq matn + kanal linki
    lines = [f"{emoji} {sarlavha_cyr}"]
    if jumla_cyr:
        lines.append(f"\n{jumla_cyr}")
    if location_cyr:
        lines.append(f"\n📍 {location_cyr}")
    # 1. Telegram xabar havolasi (matn ostida birinchi)
    if tg_post_url:
        lines.append(f"\n🔗 {tg_post_url}")
    # 2. Kanal havolalari
    lines.append(f"\n\n{TG_LINKS}")
    # 3. Sana va brend (xeshteglar yo'q)
    lines.append(f"\n\n🗓 {today}  |  📡 {brand}")
    message = "\n".join(lines)

    log.info(f"  📝 Facebook matnli post ({lang.upper()})...")
    try:
        r = requests.post(
            f"{FB_GRAPH}/{FB_PAGE_ID}/feed",
            data={
                "message":      message[:63000],
                "access_token": FB_PAGE_ACCESS_TOKEN,
            },
            timeout=30,
        )
        data = r.json()
        if "id" in data:
            log.info(f"  ✅ Facebook matnli post ({lang.upper()}): {data['id']}")
            return data["id"]
        elif "error" in data:
            log.error(f"  ❌ Facebook matn xato: {data['error'].get('message', data['error'])}")
            return None
        else:
            log.warning(f"  ⚠️  Facebook matn noma'lum javob: {data}")
            return None
    except Exception as e:
        log.error(f"  Facebook matnli post xato: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# 4. FACEBOOK SAHIFA — video yuklash (Graph API)
# ═══════════════════════════════════════════════════════════════
def post_facebook_video(
        video_path: str,
        title: str,
        description: str,
        lang: str = "en",
        daraja: str = "xabar",
        location: str = "",
        yt_url: str = "",
) -> str | None:
    """
    Facebook Sahifaga video yuklash.
    Qaytaradi: video ID yoki None.

    .env da:
      FB_PAGE_ID           — Sahifa raqami (masalan: 123456789012345)
      FB_PAGE_ACCESS_TOKEN — Uzun muddatli token (60 kun)
    """
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        log.warning("  ⚠️  FB_PAGE_ID yoki FB_PAGE_ACCESS_TOKEN yo'q (.env ga qo'shing)")
        return None

    if not os.path.exists(video_path):
        log.warning(f"  ⚠️  Video fayl yo'q: {video_path}")
        return None

    # Description matn
    desc_parts = [description]
    if location:
        desc_parts.append(f"📍 {location}")
    if yt_url:
        desc_parts.append(f"▶️ YouTube: {yt_url}")
    desc_parts.append(_hashtags(lang, daraja).replace("<b>", "").replace("</b>", ""))
    full_desc = "\n".join(desc_parts)[:5000]

    file_size_mb = os.path.getsize(video_path) / 1_048_576
    log.info(f"  📤 Facebook Sahifa yuklash: {os.path.basename(video_path)} ({file_size_mb:.1f}MB)...")

    try:
        with open(video_path, "rb") as vf:
            r = requests.post(
                f"{FB_GRAPH}/{FB_PAGE_ID}/videos",
                data={
                    "title":        title[:254],
                    "description":  full_desc,
                    "access_token": FB_PAGE_ACCESS_TOKEN,
                },
                files={"source": vf},
                timeout=600,
            )

        data = r.json()
        if "id" in data:
            vid_id = data["id"]
            log.info(f"  ✅ Facebook Sahifa video: {vid_id}")
            return vid_id
        elif "error" in data:
            err = data["error"]
            log.error(f"  ❌ Facebook xato: {err.get('message', err)}")
            return None
        else:
            log.warning(f"  ⚠️  Facebook noma'lum javob: {data}")
            return None
    except Exception as e:
        log.error(f"  Facebook yuklash xato: {e}")
        return None


def post_facebook_all_langs(
        videos: dict,
        sarlavhalar: dict,
        jumlalar: dict,
        daraja: str = "xabar",
        yt_urls: dict = None,
        location: dict = None,
) -> dict:
    """
    3 tilda Facebook Sahifaga video yuklash.
    Odatda bitta tilda (eng yaxshi tarjima bilan) yuboriladi — Facebook algoritmi.
    Default: EN, so'ngra RU, so'ngra UZ.
    """
    results = {}
    # Facebook uchun bitta til yetarli (content duplication)
    # UZ versiyasi birinchi (asosiy auditoriya o'zbek)
    for lang in ("uz", "ru", "en"):
        vpath = videos.get(lang, "")
        if not vpath or not os.path.exists(vpath):
            continue
        vid_id = post_facebook_video(
            video_path  = vpath,
            title       = sarlavhalar.get(lang, "")[:100],
            description = jumlalar.get(lang, ""),
            lang        = lang,
            daraja      = daraja,
            location    = (location or {}).get(lang, ""),
            yt_url      = (yt_urls or {}).get(lang, ""),
        )
        results[lang] = vid_id
        if vid_id:
            break   # Bir marta yuborildi, to'xtatamiz (duplicate oldini olish)
    return results


# ═══════════════════════════════════════════════════════════════
# 3. INSTAGRAM REELS — ikki bosqichli yuklash (Graph API)
# ═══════════════════════════════════════════════════════════════
def post_instagram_reel(
        video_path: str,
        caption: str,
        lang: str = "en",
        daraja: str = "xabar",
        location: str = "",
        cover_url: str = "",
) -> str | None:
    """
    Instagram Reels yuklash (9:16, max 90s).
    Ikki bosqich:
      1. Container yaratish (upload URL)
      2. Video yuklash → publish

    .env da:
      IG_USER_ID      — Instagram Business akkaunt ID
      IG_ACCESS_TOKEN — FB Page Access Token (bir xil)
    """
    if not IG_USER_ID or not IG_ACCESS_TOKEN:
        log.warning("  ⚠️  IG_USER_ID yoki IG_ACCESS_TOKEN yo'q (.env ga qo'shing)")
        return None

    if not os.path.exists(video_path):
        log.warning(f"  ⚠️  Video fayl yo'q: {video_path}")
        return None

    # Caption + hashtag
    tag_line = _hashtags(lang, daraja).replace("<b>", "").replace("</b>", "")
    full_cap = f"{caption}\n\n{tag_line}"
    if location:
        full_cap = f"{full_cap}\n📍 {location}"
    full_cap = full_cap[:2200]

    # ── Bosqich 1: Container yaratish ────────────────────────
    log.info(f"  📱 Instagram Reel container yaratilmoqda...")
    try:
        cont_data = {
            "media_type":   "REELS",
            "caption":      full_cap,
            "access_token": IG_ACCESS_TOKEN,
        }
        if cover_url:
            cont_data["cover_url"] = cover_url

        # Video faylni multipart upload qilish uchun upload_type=resumable ishlatamiz
        # Yoki oddiy video_url orqali (agar video internetda bo'lsa)
        # Bizda local fayl — shuning uchun upload_type=resumable

        # Resumable upload initiate
        init_r = requests.post(
            f"{FB_GRAPH}/{IG_USER_ID}/media",
            data={
                **cont_data,
                "upload_type": "resumable",
            },
            timeout=30,
        )
        init_json = init_r.json()

        if "error" in init_json:
            log.error(f"  ❌ IG container xato: {init_json['error'].get('message')}")
            return None

        container_id = init_json.get("id")
        upload_url   = init_json.get("uri")   # resumable upload URL

        if not container_id:
            log.warning(f"  ⚠️  IG container ID topilmadi: {init_json}")
            return None

        # ── Bosqich 1b: Faylni yuklash (agar upload_url bo'lsa) ─
        if upload_url:
            file_size = os.path.getsize(video_path)
            log.info(f"  📤 IG video yuklash ({file_size/1024/1024:.1f}MB)...")
            with open(video_path, "rb") as vf:
                upload_r = requests.post(
                    upload_url,
                    headers={
                        "Authorization":    f"OAuth {IG_ACCESS_TOKEN}",
                        "offset":           "0",
                        "file_size":        str(file_size),
                        "Content-Type":     "video/mp4",
                    },
                    data=vf,
                    timeout=600,
                )
            if upload_r.status_code not in (200, 201):
                log.warning(f"  ⚠️  IG upload javob: {upload_r.status_code}")

        # ── Bosqich 2: Status tekshirish ─────────────────────
        log.info(f"  ⏳ IG container tayyorlanmoqda ({container_id})...")
        for attempt in range(20):
            time.sleep(6)
            status_r = requests.get(
                f"{FB_GRAPH}/{container_id}",
                params={
                    "fields":       "status_code,status",
                    "access_token": IG_ACCESS_TOKEN,
                },
                timeout=30,
            )
            status_data = status_r.json()
            sc = status_data.get("status_code", "")
            if sc == "FINISHED":
                break
            elif sc in ("ERROR", "EXPIRED"):
                log.error(f"  ❌ IG container xato: {status_data.get('status')}")
                return None
            log.debug(f"  IG status: {sc} (urinish {attempt+1}/20)")

        # ── Bosqich 3: Publish ────────────────────────────────
        log.info(f"  🚀 IG Reel nashr qilinmoqda...")
        pub_r = requests.post(
            f"{FB_GRAPH}/{IG_USER_ID}/media_publish",
            data={
                "creation_id":  container_id,
                "access_token": IG_ACCESS_TOKEN,
            },
            timeout=60,
        )
        pub_data = pub_r.json()
        if "id" in pub_data:
            reel_id = pub_data["id"]
            log.info(f"  ✅ Instagram Reel nashr: {reel_id}")
            return reel_id
        else:
            log.error(f"  ❌ IG publish xato: {pub_data}")
            return None

    except Exception as e:
        log.error(f"  Instagram yuklash xato: {e}")
        return None


def post_instagram_reel_best_lang(
        videos: dict,
        sarlavhalar: dict,
        daraja: str = "xabar",
        location: dict = None,
) -> str | None:
    """
    Instagram Reels uchun eng yaxshi til videosini yuklash.
    9:16 short_* fayllarni ishlatadi.
    """
    # Instagram uchun bitta til (UZ — asosiy auditoriya)
    for lang in ("uz", "ru", "en"):
        vpath = videos.get(lang, "")
        if not vpath or not os.path.exists(vpath):
            continue
        cap = sarlavhalar.get(lang, "")
        loc = (location or {}).get(lang, "")
        result = post_instagram_reel(vpath, cap, lang, daraja, loc)
        if result:
            return result
    return None


# ═══════════════════════════════════════════════════════════════
# 4. BOSH FUNKSIYA — barcha platformalar
# ═══════════════════════════════════════════════════════════════
def post_all_platforms(
        digest_videos: dict,   # {"uz": path, "ru": path, "en": path}
        short_videos:  dict,   # {"uz": path, "ru": path, "en": path} — 9:16
        sarlavhalar:   dict,   # {"uz": str, "ru": str, "en": str}
        jumlalar:      dict,   # {"uz": str, "ru": str, "en": str}
        daraja:        str = "xabar",
        yt_urls:       dict = None,    # {"uz": yt_url, ...}
        location:      dict = None,    # {"uz": "Феникс", ...}
) -> dict:
    """
    Barcha platformalarga bir vaqtda postlash.

    Qaytaradi: {
      "telegram": {"uz": bool, "ru": bool, "en": bool},
      "facebook": {"en": video_id},
      "instagram": reel_id,
    }
    """
    results = {}

    print("\n  📢 SOCIAL MEDIA POSTLASH:")

    # ── Telegram (har bir tilda digest video) ────────────────
    if any(digest_videos.values()):
        print("  → Telegram (digest vidyolar)...")
        tg_res = post_telegram_all_langs(
            videos     = digest_videos,
            sarlavhalar = sarlavhalar,
            jumlalar    = jumlalar,
            daraja      = daraja,
            yt_urls     = yt_urls,
            location    = location,
        )
        results["telegram_digest"] = tg_res

    # ── Telegram (shorts) ────────────────────────────────────
    if any(short_videos.values()):
        print("  → Telegram (shorts)...")
        tg_short_res = post_telegram_all_langs(
            videos      = short_videos,
            sarlavhalar = sarlavhalar,
            jumlalar    = jumlalar,
            daraja      = daraja,
            yt_urls     = yt_urls,
            location    = location,
        )
        results["telegram_short"] = tg_short_res

    # ── Facebook Sahifa (digest) ─────────────────────────────
    if any(digest_videos.values()):
        print("  → Facebook Sahifa...")
        fb_res = post_facebook_all_langs(
            videos      = digest_videos,
            sarlavhalar = sarlavhalar,
            jumlalar    = jumlalar,
            daraja      = daraja,
            yt_urls     = yt_urls,
            location    = location,
        )
        results["facebook"] = fb_res

    # ── Instagram Reels (short 9:16) ─────────────────────────
    if any(short_videos.values()):
        print("  → Instagram Reels (shorts)...")
        ig_res = post_instagram_reel_best_lang(
            videos      = short_videos,
            sarlavhalar = sarlavhalar,
            daraja      = daraja,
            location    = location,
        )
        results["instagram"] = ig_res

    print("  ✅ Social media postlash tugadi")
    return results


# ─────────────────────────────────────────────────────────────
# Sinov (to'g'ridan ishga tushirish)
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Social Poster sinov ===")
    print(f"Telegram token: {'✅' if TELEGRAM_BOT_TOKEN else '❌'}")
    print(f"FB_PAGE_ID:     {'✅' if FB_PAGE_ID else '❌ (kerak: FB_PAGE_ID)'}")
    print(f"FB Token:       {'✅' if FB_PAGE_ACCESS_TOKEN else '❌ (kerak: FB_PAGE_ACCESS_TOKEN)'}")
    print(f"IG_USER_ID:     {'✅' if IG_USER_ID else '❌ (kerak: IG_USER_ID)'}")
    print(f"IG Token:       {'✅' if IG_ACCESS_TOKEN else '❌ (kerak: IG_ACCESS_TOKEN)'}")
    print()
    print(".env ga qo'shilishi kerak:")
    if not FB_PAGE_ID:
        print("  FB_PAGE_ID=<facebook-page-id>")
    if not FB_PAGE_ACCESS_TOKEN:
        print("  FB_PAGE_ACCESS_TOKEN=<page-access-token>")
    if not IG_USER_ID:
        print("  IG_USER_ID=<instagram-business-account-id>")
    if not IG_ACCESS_TOKEN:
        print("  IG_ACCESS_TOKEN=<instagram-access-token>")
