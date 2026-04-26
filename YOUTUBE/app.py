"""app.py — YouTube video pipeline"""
import os
import sys
import re
import json
import glob
import logging
from datetime import datetime
from dotenv import load_dotenv

# Windows terminalda UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

load_dotenv(dotenv_path=".env")

from config import TASHKENT, QUEUE_DIR, YOUTUBE_ENABLED, YOUTUBE_LOCAL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("output/youtube.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

SEEN_FILE = "output/seen_articles.json"

# Rossiya axborot manbalari — o'tkazib yuboriladi
RUSSIAN_DOMAINS = {
    "tass.ru", "ria.ru", "rt.com", "sputniknews.com", "regnum.ru",
    "interfax.ru", "kommersant.ru", "rbc.ru", "gazeta.ru", "lenta.ru",
    "iz.ru", "tvzvezda.ru", "vesti.ru", "1tv.ru", "ntv.ru", "mk.ru",
    "kp.ru", "aif.ru", "riafan.ru", "life.ru", "pravda.ru", "tvc.ru",
    "rg.ru", "rtr-vesti.ru", "russia.tv", "ren.tv",
}


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


def is_russian_source(url: str) -> bool:
    url = url.lower()
    return any(f"/{d}" in url or f".{d}" in url or url.startswith(f"https://{d}")
               or url.startswith(f"http://{d}") for d in RUSSIAN_DOMAINS)


# ── Muhim yangiliklar filtri ──────────────────────────────────
# Bu kalit so'zlardan kamida biri sarlavhada bo'lishi shart
IMPORTANT_KW = {
    "war", "attack", "killed", "kill", "dead", "death", "strike",
    "missile", "bomb", "explosion", "fire", "troops", "army", "military",
    "election", "vote", "president", "minister", "prime minister", "parliament",
    "sanctions", "treaty", "agreement", "deal", "summit", "nato", "un ", "eu ",
    "earthquake", "flood", "hurricane", "disaster", "crisis", "emergency",
    "protest", "coup", "arrest", "detained", "sentenced", "trial", "court",
    "nuclear", "economy", "inflation", "tariff", "trade", "gdp", "recession",
    "refugee", "ceasefire", "offensive", "invasion", "occupation",
    "shooting", "hostage", "terror", "assassination",
}

# Bu kalit so'zlardan biri sarlavhada bo'lsa — o'tkazib yuboriladi (soft news)
SOFT_NEWS_KW = {
    "chimpanzee", "monkey", "gorilla", "elephant", "animal", "wildlife",
    "spider", "snake", "shark", "bear", "lion", "tiger", "dog", "cat",
    "viral", "funny", "weird", "bizarre", "unusual", "stunning", "amazing",
    "celebrity", "oscar", "grammy", "emmy", "award", "fashion", "beauty",
    "recipe", "cooking", "diet", "lifestyle", "travel", "tourism",
    "football", "soccer", "basketball", "cricket", "tennis", "nba", "nfl",
    "album", "concert", "singer", "actor", "actress", "movie", "film",
}

def _is_important_news(title: str) -> bool:
    t = title.lower()
    # Soft news bo'lsa — rad et
    if any(k in t for k in SOFT_NEWS_KW):
        return False
    # Muhim kalit so'z bo'lsa — qabul qil
    if any(k in t for k in IMPORTANT_KW):
        return True
    # Aniqlab bo'lmasa — qabul qil (tarjima daraja'ga ishontiramiz)
    return True


# ── Sarlavhadan kalit so'z olish ─────────────────────────────
_TITLE_STOP = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "as", "is", "are", "was",
    "were", "be", "been", "has", "have", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "shall",
    "not", "no", "this", "that", "these", "those", "its", "their",
    "over", "after", "before", "between", "into", "through", "about",
    "says", "said", "after", "amid", "amid", "amid", "amid",
}

def _title_keywords(title: str, count: int = 6) -> list:
    """Asl inglizcha sarlavhadan muhim so'zlarni olish."""
    words = re.findall(r"[A-Za-z']{3,}", title)
    seen  = set()
    result = []
    for w in words:
        wl = w.lower()
        if wl in _TITLE_STOP or wl in seen:
            continue
        seen.add(wl)
        result.append(w)
        if len(result) >= count:
            break
    return result


_CYR = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯЎҚҒҲ"

def _title_ok(text: str, lang: str) -> bool:
    """YouTube sarlavhasi yaroqli ekanligini tekshirish."""
    if not text or not text.strip():
        return False
    t = text.strip()
    if len(t) < 8 or len(t) > 150:
        return False
    if len(t.split()) < 3:
        return False
    bad = {"none", "n/a", "sarlavha", "заголовок", "headline", "title", "null", ""}
    if t.lower() in bad:
        return False
    if lang == "uz" and not any(c in t for c in _CYR):
        return False
    # ALL CAPS tekshiruv: ≥70% harflar katta bo'lsa — noto'g'ri
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 6 and sum(1 for c in letters if c.isupper()) / len(letters) >= 0.70:
        return False
    return True


def _repair_title(bad_title: str, original_en: str, lang: str) -> str:
    """Sarlavhani alohida AI so'rov bilan tuzatish."""
    # EN uchun: asl inglizcha sarlavha yaxshi bo'lsa — uni qaytarish (AI so'rovsiz)
    if lang == "en" and original_en and len(original_en.strip()) >= 8:
        log.info(f"  🔧 EN sarlavha: asl sarlavhani qaytarmoqda: '{original_en[:60]}'")
        return original_en.strip()[:100]
    try:
        import sys
        sys.path.insert(0, "../TELEGRAM")
        from translator import _fix_title_only, _is_valid_title
        fixed = _fix_title_only(original_en, lang)
        if fixed and len(fixed.strip()) >= 8:
            log.info(f"  🔧 {lang.upper()} sarlavha tuzatildi: '{fixed[:60]}'")
            return fixed
    except Exception as e:
        log.warning(f"  sarlavha tuzatish xato ({lang}): {e}")
    # Fallback — asl inglizcha
    return original_en[:80]


_STATUS_SUFFIXES = (
    "_error.json", "_seen.json", "_skipped.json",
    "_no_video.json", "_soft.json",
)

DIGEST_BATCH = 5      # Bir digestda nechta yangilik
_CYR_UZ = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯЎҚҒҲ"
_GENERIC_QW = {"specific","location","event","country","conflict","raw","footage",
               "ground","scene","aftermath","video","personname","countryname",
               "organizationname","eventtopic","keyterm"}


def _parse_queue_item(qfile: str, seen: set) -> dict | None:
    """JSON fayldan yangilik ma'lumotlarini olish. Filtrlardan o'tmasa None."""
    try:
        with open(qfile, "r", encoding="utf-8") as f:
            data = json.load(f)

        article     = data["article"]
        article_url = article.get("link", "")
        title       = article.get("title", "")

        if article_url and article_url in seen:
            os.rename(qfile, qfile.replace(".json", "_seen.json"))
            return None
        if article_url and is_russian_source(article_url):
            os.rename(qfile, qfile.replace(".json", "_skipped.json"))
            return None
        if not _is_important_news(title):
            os.rename(qfile, qfile.replace(".json", "_soft.json"))
            return None

        scripts     = data.get("scripts", {})
        sarlavhalar = data.get("sarlavha", {})
        keywords_en = data.get("keywords_en", [])
        search_q    = data.get("search_queries", [])
        location    = data.get("location", {})
        daraja      = data.get("daraja", "xabar")
        jumlalar    = data.get("jumla", {})

        # Sarlavhalarni tuzatish
        try:
            sys.path.insert(0, "../TELEGRAM")
            from translator import _apply_uz_places, _fix_title_only

            def _is_corrupt(text, lc):
                if not text or len(text.strip()) < 5:
                    return True
                alpha = [c for c in text if c.isalpha()]
                if not alpha:
                    return True
                if lc in ("uz", "ru"):
                    if sum(1 for c in alpha if c.isascii()) / len(alpha) > 0.15:
                        return True
                    if sum(1 for c in alpha if c in _CYR_UZ) / len(alpha) < 0.60:
                        return True
                elif lc == "en":
                    if sum(1 for c in alpha if c in _CYR_UZ) / len(alpha) > 0.30:
                        return True
                return False

            for fix_lang in ("uz", "ru", "en"):
                val = sarlavhalar.get(fix_lang, "")
                if not _is_corrupt(val, fix_lang):
                    continue
                if fix_lang == "en" and not _is_corrupt(title, "en"):
                    sarlavhalar["en"] = title[:100]
                    continue
                fixed = _fix_title_only(title, fix_lang)
                sarlavhalar[fix_lang] = fixed if fixed and not _is_corrupt(fixed, fix_lang) else ""

            uz_s = sarlavhalar.get("uz", "")
            if uz_s:
                sarlavhalar["uz"] = _apply_uz_places(uz_s)
        except Exception as _e:
            log.debug(f"Sarlavha tuzatish: {_e}")

        # Kalit so'zlar
        title_kw    = _title_keywords(title, count=6)
        extra_kw    = [k for k in keywords_en if k not in title_kw
                       and len(k.split()) <= 3 and k and k[0].isupper()
                       and k.lower() not in _TITLE_STOP]
        combined_kw = title_kw + extra_kw[:4]

        return {
            "qfile":       qfile,
            "article_url": article_url,
            "en_title":    sarlavhalar.get("en") or title,
            "sarlavhalar": sarlavhalar,
            "scripts":     scripts,
            "jumlalar":    jumlalar,
            "location":    location,
            "daraja":      daraja,
            "keywords_en": combined_kw,
            "title":       title,
        }
    except Exception as e:
        log.error(f"Parse xato {qfile}: {e}")
        return None


def _build_digest_item(raw: dict, lang: str) -> dict | None:
    """Bitta yangilikni digest uchun formatlash."""
    sarlavhalar = raw["sarlavhalar"]
    scripts     = raw["scripts"]
    jumlalar    = raw["jumlalar"]
    location    = raw["location"]
    title       = raw["title"]

    sarlavha = sarlavhalar.get(lang, "")
    if not _title_ok(sarlavha, lang):
        sarlavha = _repair_title(sarlavha, title, lang)
    if not sarlavha:
        return None

    script_raw = scripts.get(lang, "")
    script_raw = re.sub(
        r"^(Efirda\s+1KUN\s+Global\.?|В\s+эфире\s+1ДЕНЬ\s+Global\.?|"
        r"This\s+is\s+1DAY\s+Global\.?)\s*",
        "", script_raw, flags=re.IGNORECASE
    ).strip()

    jumla1 = jumlalar.get(lang, "") or script_raw[:200]

    return {
        "sarlavha":    sarlavha,
        "jumla1":      jumla1,
        "script":      script_raw,
        "location":    location.get(lang, ""),
        "daraja":      raw["daraja"],
        "article_url": raw["article_url"],
        "en_title":    raw["en_title"],
        "keywords_en": raw["keywords_en"],
    }


def process_queue():
    """
    Yangiliklar digest formatida qayta ishlash.

    Har DIGEST_BATCH ta yangilik → bitta digest video (faqat EN).
    Bir chaqiruvda FAQAT 1 ta batch — takrorlanishni oldini oladi.
    """
    # ── Lock fayl — paralel ishga tushishni oldini olish ────────
    _lock = f"{QUEUE_DIR}/.lock"
    if os.path.exists(_lock):
        log.info("⏳ process_queue allaqachon ishlayapti — o'tkazildi")
        return
    try:
        open(_lock, "w").close()   # Lock yaratish
    except Exception:
        pass

    try:
        _process_queue_inner()
    finally:
        try:
            os.remove(_lock)       # Lock o'chirish (har doim)
        except Exception:
            pass


def _process_queue_inner():
    from digest_maker import digest_pipeline

    seen = load_seen()

    queue_files = sorted([
        f for f in glob.glob(f"{QUEUE_DIR}/*.json")
        if not any(f.endswith(suf) for suf in _STATUS_SUFFIXES)
    ])

    if not queue_files:
        log.info("Navbat bo'sh")
        return

    log.info(f"Navbatda: {len(queue_files)} ta fayl")

    # ── Barcha fayllarni parse qilamiz ────────────────────────
    parsed = []
    skip_files = []
    for qfile in queue_files:
        raw = _parse_queue_item(qfile, seen)
        if raw:
            parsed.append(raw)
        else:
            skip_files.append(qfile)

    log.info(f"  ✓ Yaroqli: {len(parsed)} ta | O'tkazildi: {len(skip_files)} ta")

    if not parsed:
        log.info("Yaroqli yangilik yo'q")
        return

    # ── FAQAT 1 ta batch — eng so'nggi DIGEST_BATCH ta yangilik ──
    # (Keyingi chaqiruvda qolgan fayllar qayta ishlanadi)
    done_dir = f"{QUEUE_DIR}/done"
    os.makedirs(done_dir, exist_ok=True)

    if True:   # Faqat 1 iteratsiya
        batch_start = 0
        batch = parsed[:DIGEST_BATCH]
        log.info(f"\n📺 Digest batch: 1–{len(batch)}/{len(parsed)}")

        any_lang_ok = False
        # Social media uchun natijalarni to'playmiz
        digest_videos = {}   # lang → video_path
        short_videos  = {}   # lang → short_path
        yt_urls       = {}   # lang → yt_url
        sarlavhalar   = {}   # lang → sarlavha (FB/IG uchun)
        jumlalar      = {}   # lang → jumla
        location_map  = {}   # lang → location
        daraja_main   = "xabar"

        for lang in ["en"]:  # ⏸️ UZ/RU vaqtincha o'chirilgan — faqat EN
            # Har bir yangilik uchun digest item yasaymiz
            items = []
            for raw in batch:
                item = _build_digest_item(raw, lang)
                if item:
                    items.append(item)

            if len(items) < 1:
                log.warning(f"  [{lang.upper()}] Hech item yo'q, o'tkazildi")
                continue

            # Sarlavha/jumla/location ni social media uchun saqlaymiz
            if items:
                sarlavhalar[lang] = items[0].get("sarlavha", "")
                jumlalar[lang]    = items[0].get("jumla1", "")
                location_map[lang]= items[0].get("location", "")
                daraja_main       = items[0].get("daraja", "xabar")

            log.info(f"  [{lang.upper()}] {len(items)} ta yangilik digest yaratilmoqda...")
            try:
                result = digest_pipeline(items, lang)
                # digest_pipeline endi (video_path, yt_url, short_path) qaytaradi
                if isinstance(result, tuple):
                    video_path, yt_url, short_path = result
                else:
                    video_path, yt_url, short_path = result, "", None

                if video_path:
                    any_lang_ok = True
                    digest_videos[lang] = video_path
                    if yt_url:
                        yt_urls[lang] = yt_url
                    if short_path:
                        short_videos[lang] = short_path
                    log.info(f"  ✅ [{lang.upper()}] Digest tayyor: {video_path}")
                else:
                    log.warning(f"  ⚠️  [{lang.upper()}] Digest yaratilmadi")
            except Exception as e:
                log.error(f"  [{lang.upper()}] Digest xato: {e}", exc_info=True)

        # ── INSTAGRAM postlash (barcha tillar tugagandan keyin) ─
        # Facebook postlash digest_maker.py ichida Telegram bilan birga bajariladi
        # (UZ va RU uchun)
        if any_lang_ok and short_videos:
            try:
                from social_poster import post_instagram_reel_best_lang
                # Instagram Reels → short (9:16)
                log.info("  📸 Instagram Reels postlash...")
                ig_id = post_instagram_reel_best_lang(
                    videos      = short_videos,
                    sarlavhalar = sarlavhalar,
                    daraja      = daraja_main,
                    location    = location_map,
                )
                if ig_id:
                    log.info(f"  ✅ Instagram Reel: {ig_id}")
            except Exception as _soc_e:
                log.warning(f"  ⚠️  Instagram post xato: {_soc_e}")

        # ── Fayllarni arxivlash ───────────────────────────────
        for raw in batch:
            qfile       = raw["qfile"]
            article_url = raw["article_url"]
            try:
                if any_lang_ok:
                    if article_url:
                        seen.add(article_url)
                    os.rename(qfile, f"{done_dir}/{os.path.basename(qfile)}")
                else:
                    error_path = qfile.replace(".json", "_error.json")
                    if os.path.exists(error_path):
                        os.remove(error_path)
                    os.rename(qfile, error_path)
            except Exception as _e:
                log.debug(f"Fayl ko'chirish xato: {_e}")

        if any_lang_ok and seen:
            save_seen(seen)

        log.info("")


def run_daily_shorts_all():
    """
    Barcha 3 tilda (uz, ru, en) Daily Shorts yaratish va YouTube ga yuklash.
    Bu funksiya APScheduler tomonidan kuniga 4 mahal chaqiriladi.
    """
    log.info("📰 Daily Shorts (uz/ru/en) ishga tushdi...")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from daily_shorts import make_daily_shorts
        for lg in ["en"]:  # ⏸️ UZ/RU vaqtincha o'chirilgan — faqat EN
            try:
                log.info(f"  → Daily Shorts ({lg.upper()}) yaratilmoqda...")
                result = make_daily_shorts(lg)
                if result:
                    log.info(f"  ✅ Daily Shorts ({lg.upper()}) tayyor: {result}")
                else:
                    log.warning(f"  ⚠️  Daily Shorts ({lg.upper()}) yaratilmadi")
            except Exception as e:
                log.error(f"  Daily Shorts ({lg.upper()}) xato: {e}", exc_info=True)
    except ImportError as e:
        log.error(f"  daily_shorts import xato: {e}")


def run_analysis_all():
    """
    Navbatdagi so'nggi DIGEST_BATCH ta yangilikdan tahlil video yaratish.
    Kunda 3 marta chaqiriladi (08:00, 14:00, 20:00 Toshkent).
    Tahlil video = uzunroq naratsiya + 2 rasm/yangilik + ob-havo ticker + timestamps.
    """
    log.info("🎙 Tahlil pipeline (uz/ru/en) ishga tushdi...")
    from analysis_maker import analysis_pipeline

    seen = load_seen()
    done_dir = f"{QUEUE_DIR}/done"

    # Queue + done papkalardan so'nggi DIGEST_BATCH ta fayl (modified time bo'yicha)
    queue_files = [
        f for f in glob.glob(f"{QUEUE_DIR}/*.json")
        if not any(f.endswith(suf) for suf in _STATUS_SUFFIXES)
    ]
    done_files  = glob.glob(f"{done_dir}/*.json")
    all_cands   = sorted(
        queue_files + done_files,
        key=os.path.getmtime, reverse=True
    )
    recent_files = all_cands[:DIGEST_BATCH]

    if not recent_files:
        log.info("Tahlil uchun yangilik yo'q")
        return

    log.info(f"  Tahlil uchun {len(recent_files)} ta yangilik")

    parsed = []
    for qfile in recent_files:
        try:
            with open(qfile, "r", encoding="utf-8") as f:
                data = json.load(f)
            article = data.get("article", {})
            url     = article.get("link", "")
            title   = article.get("title", "")
            if url and is_russian_source(url):
                continue

            scripts     = data.get("scripts", {})
            sarlavhalar = data.get("sarlavha", {})
            keywords_en = data.get("keywords_en", [])
            location    = data.get("location", {})
            daraja      = data.get("daraja", "xabar")
            jumlalar    = data.get("jumla", {})
            en_title    = sarlavhalar.get("en") or title

            parsed.append({
                "title": title, "en_title": en_title,
                "sarlavhalar": sarlavhalar,
                "scripts": scripts, "jumlalar": jumlalar,
                "location": location, "daraja": daraja,
                "article_url": url, "keywords_en": keywords_en,
            })
        except Exception as e:
            log.debug(f"Parse xato {qfile}: {e}")

    if not parsed:
        log.info("  Yaroqli yangilik yo'q")
        return

    def _is_cyr_text(text: str, threshold: float = 0.35) -> bool:
        """Matn kamida threshold% Kirill harfdan iboratmi?"""
        alpha = [c for c in text if c.isalpha()]
        if not alpha:
            return False
        return sum(1 for c in alpha if c in _CYR) / len(alpha) >= threshold

    def _clean_field(text: str, lang: str) -> str:
        """RU/UZ uchun inglizcha matnni o'tkazib yuborish."""
        if not text or not text.strip():
            return ""
        if lang in ("uz", "ru") and not _is_cyr_text(text):
            return ""   # Inglizcha matn — bo'sh qaytarish
        return text.strip()

    for lang in ["en"]:  # ⏸️ UZ/RU vaqtincha o'chirilgan — faqat EN
        items = []
        for raw in parsed:
            raw_sarlavha = raw["sarlavhalar"].get(lang, "")
            sarlavha = _clean_field(raw_sarlavha, lang)
            # EN uchun inglizcha fallback OK — RU/UZ uchun EMAS
            if not sarlavha and lang == "en":
                sarlavha = raw.get("title", "")

            # ── Script olish ──────────────────────────────────────
            # UZ: script LOTIN (TTS uchun) — Kirill tekshiruvi KERAK EMAS
            # RU: script Kirill bo'lishi kerak (_clean_field)
            # EN: script inglizcha — Kirill tekshiruvi KERAK EMAS
            raw_script = raw["scripts"].get(lang, "").strip()
            if lang == "uz":
                # UZ Latin skriptini to'g'ridan filtrsiz olish
                script = raw_script
            elif lang == "en":
                # EN: script bo'sh bo'lsa — article description ni ishlatish
                script = raw_script or raw.get("article", {}).get("description", "")
                script = script.strip()
            else:
                # RU: Kirill tekshiruvi (inglizcha content kirmasin)
                script = _clean_field(raw_script, lang)

            jumla_raw = raw["jumlalar"].get(lang, "")
            jumla1 = _clean_field(jumla_raw, lang) or (script[:200] if script else "")

            if not sarlavha or len(sarlavha.strip()) < 5:
                continue
            items.append({
                "sarlavha":    sarlavha,
                "jumla1":      jumla1,
                "script":      script,
                "location":    raw["location"].get(lang, ""),
                "daraja":      raw["daraja"],
                "article_url": raw["article_url"],
                "en_title":    raw["en_title"],
                "keywords_en": raw["keywords_en"],
            })

        if len(items) < 2:
            log.warning(f"  [{lang.upper()}] Yetarli tahlil matni yo'q ({len(items)} ta)")
            continue

        log.info(f"  [{lang.upper()}] {len(items)} ta yangilik tahlil qilinmoqda...")
        try:
            result = analysis_pipeline(items, lang)
            # analysis_pipeline endi (video_path, yt_url) qaytaradi
            if isinstance(result, tuple):
                video_path, yt_url = result
            else:
                video_path, yt_url = result, ""

            if video_path:
                log.info(f"  ✅ [{lang.upper()}] Tahlil tayyor: {video_path}")

                # ── Faqat Facebook YT link post (Telegram YO'Q) ──
                if yt_url:
                    try:
                        from social_poster import post_facebook_yt_link
                        top_sarlavha = items[0].get("sarlavha", "")
                        top_jumla    = items[0].get("jumla1",   "")
                        top_loc      = items[0].get("location",  "")
                        daraja_val   = items[0].get("daraja", "xabar")

                        fb_ok = post_facebook_yt_link(
                            yt_url      = yt_url,
                            title       = top_sarlavha,
                            description = top_jumla,
                            lang        = lang,
                            daraja      = daraja_val,
                            location    = top_loc,
                        )
                        log.info(f"  {'✅' if fb_ok else '⚠️ '} Facebook Tahlil [{lang.upper()}]")
                    except Exception as _sp_e:
                        log.error(f"  Tahlil social post xato [{lang.upper()}]: {_sp_e}", exc_info=True)
            else:
                log.warning(f"  ⚠️  [{lang.upper()}] Tahlil yaratilmadi")
        except Exception as e:
            log.error(f"  [{lang.upper()}] Tahlil xato: {e}", exc_info=True)


def main():
    log.info("🎬 YouTube pipeline ishga tushdi")

    if "--now" in sys.argv or "--once" in sys.argv:
        process_queue()
        return

    if "--shorts" in sys.argv:
        run_daily_shorts_all()
        return

    if "--analysis" in sys.argv:
        run_analysis_all()
        return

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone=TASHKENT)

    # ── YouTube video queue — har 30 daqiqada ─────────────────
    scheduler.add_job(
        process_queue,
        IntervalTrigger(minutes=30),
        id="youtube_queue",
        misfire_grace_time=300,
    )

    # ── Daily Shorts — kuniga 12 mahal (har toq soatda) ───────
    # 01,03,05,07,09,11,13,15,17,19,21,23 (Toshkent)
    shorts_hours = [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23]
    for shot_id, hour in enumerate(shorts_hours, start=1):
        scheduler.add_job(
            run_daily_shorts_all,
            CronTrigger(hour=hour, minute=30, timezone=TASHKENT),
            id=f"daily_shorts_{shot_id}",
            misfire_grace_time=600,
        )
    log.info(f"⏰ Daily Shorts: {', '.join(f'{h:02d}:30' for h in shorts_hours)} (Toshkent) — 12/kun")

    # ── Tahlil video — kuniga 1 marta (20:00 Toshkent) ──────────
    scheduler.add_job(
        run_analysis_all,
        CronTrigger(hour=20, minute=0, timezone=TASHKENT),
        id="analysis_daily",
        misfire_grace_time=600,
    )
    log.info("⏰ Tahlil (kunlik digest): 20:00 (Toshkent) — 1/kun")

    log.info("⏰ Har 30 daqiqada navbat tekshiriladi")
    log.info("Ctrl+C — to'xtatish\n")
    process_queue()
    scheduler.start()


if __name__ == "__main__":
    main()
