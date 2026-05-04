"""app.py — YouTube video pipeline"""
import os
import sys
import re
import json
import glob
import time
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

SEEN_FILE        = "output/seen_articles.json"
SEEN_TOPICS_FILE = "output/seen_topics.json"   # Cross-run topic dedup

MIN_DIGEST_ITEMS = 1   # 1 ta yangilik bo'lsa ham post qilamiz; 3+ bo'lsa to'liq digest

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


def load_seen_topics() -> list:
    """Cross-run duplikat topiclarni saqlash — so'nggi 24 soat uchun."""
    if os.path.exists(SEEN_TOPICS_FILE):
        try:
            with open(SEEN_TOPICS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Faqat so'nggi 24 soatdagi mavzularni saqla
            cutoff = time.time() - 86400
            return [e for e in data if e.get("ts", 0) > cutoff]
        except Exception:
            pass
    return []


def save_seen_topics(entries: list):
    os.makedirs("output", exist_ok=True)
    with open(SEEN_TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


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
    # UZ endi LOTIN alifbosida — kirill talabi olib tashlandi
    if lang == "uz":
        # Lotin UZ: asosan ASCII harflar bo'lishi kerak
        alpha = [c for c in t if c.isalpha()]
        if alpha:
            cyr_ratio = sum(1 for c in alpha if c in _CYR) / len(alpha)
            # Agar >80% kirill → bu RU matn, UZ emas
            if cyr_ratio > 0.80:
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
                if lc == "ru":
                    # Rus matni: asosan kirill bo'lishi kerak
                    if sum(1 for c in alpha if c.isascii()) / len(alpha) > 0.15:
                        return True
                    if sum(1 for c in alpha if c in _CYR_UZ) / len(alpha) < 0.60:
                        return True
                elif lc == "uz":
                    # UZ LOTIN: ASCII dominant bo'lishi normal
                    # Faqat agar >85% kirill bo'lsa — bu RU matn, UZ emas
                    cyr_r = sum(1 for c in alpha if c in _CYR_UZ) / len(alpha)
                    if cyr_r > 0.85:
                        return True  # Kirill UZ → buzuq
                elif lc == "en":
                    if sum(1 for c in alpha if c in _CYR_UZ) / len(alpha) > 0.30:
                        return True
                return False

            def _is_corrupt_fixed(text, lc):
                """Fixed versiya tuzatilgan sarlavha uchun."""
                return _is_corrupt(text, lc)

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
        # Eski lock (30 daqiqadan ko'p) — avtomatik o'chirish
        try:
            age_min = (time.time() - os.path.getmtime(_lock)) / 60
            if age_min > 30:
                os.remove(_lock)
                log.warning(f"⚠️  Eski lock fayl o'chirildi ({age_min:.0f} daqiqa eski)")
            else:
                log.info(f"⏳ process_queue ishlayapti ({age_min:.0f} daq) — o'tkazildi")
                return
        except Exception:
            pass
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

    # ── DUPLIKAT MAVZULARNI OLIB TASHLASH ─────────────────────
    def _topics_overlap(t1: str, t2: str, threshold: int = 2) -> bool:
        stop = _TITLE_STOP | {"says", "report", "amid", "claim", "calls", "over", "after"}
        words1 = [w for w in re.findall(r'[A-Za-z]{4,}', t1.lower()) if w not in stop]
        words2 = [w for w in re.findall(r'[A-Za-z]{4,}', t2.lower()) if w not in stop]
        stems1 = {w[:5] for w in words1}
        stems2 = {w[:5] for w in words2}
        return len(stems1 & stems2) >= threshold

    done_dir = f"{QUEUE_DIR}/done"
    os.makedirs(done_dir, exist_ok=True)

    # 1. Cross-run dedup: so'nggi 24 soatda qayta ishlangan mavzular
    prior_topic_entries = load_seen_topics()
    prior_topic_titles  = [e["title"] for e in prior_topic_entries]

    deduped     = []
    seen_titles = list(prior_topic_titles)   # starts with yesterday's topics
    dup_count   = 0
    for raw in parsed:
        title = raw.get("title", "")
        if any(_topics_overlap(title, st) for st in seen_titles):
            log.info(f"  Duplikat mavzu o'tkazildi: {title[:60]}")
            # Fayl ham ko'chirilsin (queue dan tozalansin)
            try:
                os.rename(raw["qfile"], f"{done_dir}/{os.path.basename(raw['qfile'])}")
            except Exception:
                pass
            dup_count += 1
            continue
        seen_titles.append(title)
        deduped.append(raw)
    if dup_count:
        log.info(f"  Duplikat olib tashlandi: {dup_count} ta | Qoldi: {len(deduped)} ta")
    parsed = deduped

    if not parsed:
        log.info("Duplikat filtrlashdan keyin yangilik qolmadi")
        return

    # 2. Minimum yangilik soni tekshiruvi
    if len(parsed) < MIN_DIGEST_ITEMS:
        log.info(f"  Kam yangilik ({len(parsed)} ta < {MIN_DIGEST_ITEMS}) — digest o'tkazildi")
        return

    # 3. Topiclarni DARROV seen_topics ga yozamiz — crash bo'lsa ham qaytib ishlamaydi
    _early_entries = [
        {"title": raw["title"], "ts": time.time()}
        for raw in parsed[:DIGEST_BATCH] if raw.get("title")
    ]
    save_seen_topics(prior_topic_entries + _early_entries)
    log.info(f"  Topic ro'yxatga olindi: {len(_early_entries)} ta (24 soatlik dedup)")

    # ── FAQAT 1 ta batch — eng so'nggi DIGEST_BATCH ta yangilik ──
    if True:   # Faqat 1 iteratsiya
        batch_start = 0
        batch = parsed[:DIGEST_BATCH]
        log.info(f"\nDigest batch: 1-{len(batch)}/{len(parsed)}")

        any_lang_ok = False
        # Social media uchun natijalarni to'playmiz
        digest_videos = {}   # lang → video_path
        short_videos  = {}   # lang → short_path
        yt_urls       = {}   # lang → yt_url
        sarlavhalar   = {}   # lang → sarlavha (FB/IG uchun)
        jumlalar      = {}   # lang → jumla
        location_map  = {}   # lang → location
        daraja_main   = "xabar"

        for lang in ["uz", "ru", "en"]:
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

        # Qayta ishlangan mavzularni saqlash (cross-run dedup uchun)
        if any_lang_ok:
            new_entries = [
                {"title": raw["title"], "ts": time.time()}
                for raw in batch if raw.get("title")
            ]
            save_seen_topics(prior_topic_entries + new_entries)

        log.info("")


def run_daily_shorts_all():
    """
    Barcha 3 tilda (uz, ru, en) Daily Shorts yaratish — KUNIGA BIR MARTA.
    Har til uchun lock fayl tekshiriladi — bugun allaqachon yaratilgan bo'lsa o'tkaziladi.
    Bu funksiya APScheduler tomonidan kuniga 12 mahal chaqiriladi,
    lekin har til uchun faqat BIRINCHI muvaffaqiyatli ishga tushishda video yaratiladi.
    """
    log.info("📰 Daily Shorts (uz/ru/en) ishga tushdi...")
    today_str  = datetime.now(TASHKENT).strftime("%Y-%m-%d")
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(output_dir, exist_ok=True)

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from daily_shorts import make_daily_shorts
        for lg in ["uz", "ru", "en"]:
            # ── Kunlik lock tekshiruvi ────────────────────────────
            lock_file = os.path.join(output_dir, f"daily_shorts_{lg}_lock.txt")
            if os.path.exists(lock_file):
                try:
                    if open(lock_file, encoding="utf-8").read().strip() == today_str:
                        log.info(f"  ⏭️  Daily Shorts ({lg.upper()}) bugun allaqachon yaratilgan — o'tkazildi")
                        continue
                except Exception:
                    pass  # Lock fayl o'qilmadi — davom etish

            try:
                log.info(f"  → Daily Shorts ({lg.upper()}) yaratilmoqda...")
                result = make_daily_shorts(lg)
                if result:
                    log.info(f"  ✅ Daily Shorts ({lg.upper()}) tayyor: {result}")
                    # Lock fayl yozish — bugun qayta yaratilmasin
                    try:
                        with open(lock_file, "w", encoding="utf-8") as lf:
                            lf.write(today_str)
                    except Exception:
                        pass
                else:
                    log.warning(f"  ⚠️  Daily Shorts ({lg.upper()}) yaratilmadi")
            except Exception as e:
                log.error(f"  Daily Shorts ({lg.upper()}) xato: {e}", exc_info=True)
    except ImportError as e:
        log.error(f"  daily_shorts import xato: {e}")


def run_analysis_all():
    """
    OLIB TASHLANDI: Tahlil video pipeline o'chirildi (foydalanuvchi buyrug'i).
    Tahlil videolar sifatsiz edi — 5 ta yangilik jamlangan, faqat birida audio bor,
    qolganlari 20-40 soniyalik jim video. Shu sababli butunlay o'chirildi.
    """
    log.info("🎙 Tahlil pipeline o'chirilgan — o'tkazildi (disabled by user request)")


def run_upload_pending():
    """
    output/videos/ dagi yuklnmagan videolarni YouTube ga yuklash.
    Kvota-aware: MAX_UPLOADS (default=6) ta videogacha, kvota tugasa to'xtatish.
    Har upload uploaded.json ga yoziladi — qayta yuklanmaydi.
    """
    log.info("📤 YouTube upload pending...")
    try:
        from upload_pending import _select_files, upload_video, QuotaError, \
                                   _load_uploaded, _save_uploaded, _detect_lang, \
                                   _detect_type, VIDEOS_DIR, MAX_UPLOADS, TODAY
        import glob as _gl
        all_mp4  = sorted(_gl.glob(str(VIDEOS_DIR / "*.mp4")))
        uploaded = _load_uploaded()
        selected = _select_files(all_mp4, uploaded, today_only=True)

        if not selected:
            log.info("  Yuklanishi kerak bo'lgan yangi video yo'q")
            return

        log.info(f"  Yuklash uchun: {len(selected)} ta (max {MAX_UPLOADS} ta yuklanadi)")
        ok = failed = 0
        for prio, fname, fpath, vtype in selected[:MAX_UPLOADS]:
            lang = _detect_lang(fname)
            try:
                vid_id = upload_video(fpath, lang, vtype)
                if vid_id:
                    uploaded.add(fname)
                    _save_uploaded(uploaded)
                    ok += 1
                else:
                    failed += 1
            except QuotaError:
                log.warning(f"  ⏸️  Kvota tugadi. {ok} ta yuklandi.")
                break
        log.info(f"  ✅ Yuklandi: {ok} ta | Xato: {failed} ta")
    except Exception as e:
        log.error(f"  run_upload_pending xato: {e}", exc_info=True)


def main():
    log.info("🎬 YouTube pipeline ishga tushdi")

    if "--now" in sys.argv or "--once" in sys.argv:
        process_queue()
        return

    if "--shorts" in sys.argv:
        run_daily_shorts_all()
        return

    if "--upload" in sys.argv:
        run_upload_pending()
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

    # ── YouTube upload — kuniga 6 mahal (har 4 soatda) ───────────
    # (Tahlil pipeline o'chirildi — sifatsiz video edi)
    # Kvota: 10,000 unit/kun, upload = 1,600 unit → max 6/kun
    # Strategiya: biriktirilgan videni uploaddan keyin tracking-file ga yozish
    scheduler.add_job(
        run_upload_pending,
        CronTrigger(hour="0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23", minute=15, timezone=TASHKENT),
        id="upload_pending",
        misfire_grace_time=600,
    )
    log.info("⏰ YouTube upload: har soat :15 da (0:15–23:15, Toshkent) — kun bo'yi avtomatik")

    log.info("⏰ Har 15 aqiqada navbat tekshiriladi")
    log.info("Ctrl+C — to'xtatish\n")
    process_queue()
    scheduler.start()


if __name__ == "__main__":
    main()
