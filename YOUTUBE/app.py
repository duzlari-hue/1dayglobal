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


def process_queue():
    """youtube_queue papkasidagi JSON fayllarni qayta ishlash"""
    from youtube_maker import fetch_youtube_clips, fetch_web_clips, fetch_clips_per_shot, youtube_pipeline, extract_keywords

    seen = load_seen()

    # Faqat "toza" queue fayllarni olish — status suffixli fayllarni o'tkazib yuborish
    # (_error, _seen, _skipped, _no_video, _soft suffikslari bilan tugagan fayllar
    #  qayta ishlanmaydi — aks holda har safar yangi suffiks qo'shiladi)
    _STATUS_SUFFIXES = (
        "_error.json", "_seen.json", "_skipped.json",
        "_no_video.json", "_soft.json",
    )
    queue_files = sorted([
        f for f in glob.glob(f"{QUEUE_DIR}/*.json")
        if not any(f.endswith(suf) for suf in _STATUS_SUFFIXES)
    ])

    if not queue_files:
        log.info("Navbat bo'sh")
        return

    log.info(f"Navbatda: {len(queue_files)} ta fayl")

    for qfile in queue_files:
        try:
            with open(qfile, "r", encoding="utf-8") as f:
                data = json.load(f)

            article        = data["article"]
            scripts        = data["scripts"]
            sarlavhalar    = data["sarlavha"]
            keywords_en    = data.get("keywords_en", [])
            search_queries = data.get("search_queries", [])
            shot_list      = data.get("shot_list", [])
            location       = data.get("location", {})
            daraja         = data.get("daraja", "xabar")
            article_url    = article.get("link", "")

            # ── Ko'rilgan yangilik — o'tkazish ────────────────
            if article_url and article_url in seen:
                log.info(f"⏭ Ko'rilgan: {article_url[:70]}")
                os.rename(qfile, qfile.replace(".json", "_seen.json"))
                continue

            # ── Rossiya manbasi — o'tkazish ───────────────────
            if article_url and is_russian_source(article_url):
                log.warning(f"⛔ Rossiya manbasi: {article_url[:70]}")
                os.rename(qfile, qfile.replace(".json", "_skipped.json"))
                continue

            title = article.get("title", "")

            # ── Muhim yangilik filtri — hayvon/sport/soft news rad ──
            if not _is_important_news(title):
                log.warning(f"⏭ Soft news: {title[:70]}")
                os.rename(qfile, qfile.replace(".json", "_soft.json"))
                continue

            log.info(f"🎬 {title[:65]}...")

            # ── Kalit so'zlar: AYN INGLIZCHA SARLAVHADAN (asosiy) ──
            # AI skriptidan emas — sarlavha eng aniq va toza manba
            title_kw = _title_keywords(title, count=6)
            # AI taklif qilgan keywords_en ni ham qo'shamiz (nom, joy, tashkilot)
            extra_kw = [k for k in keywords_en
                        if k not in title_kw
                        and len(k.split()) <= 3
                        and k[0].isupper()
                        and k.lower() not in _TITLE_STOP]
            combined_kw = title_kw + extra_kw[:4]

            # Qidiruv so'rovlari: sarlavha BIRINCHI, qolgan support rolida
            GENERIC = {"specific", "location", "event", "country", "conflict",
                       "raw", "footage", "ground", "scene", "aftermath", "video",
                       "personname", "countryname", "organizationname", "eventtopic", "keyterm"}
            clean_sq = [title]  # Asl sarlavha — eng muhim qidiruv
            for q in search_queries:
                if (not any(w.lower() in GENERIC for w in q.split())
                        and len(q.split()) >= 3
                        and q != title):
                    clean_sq.append(q)

            log.info(f"  🔑 Sarlavha kalit so'zlari: {combined_kw}")
            log.info(f"  🔍 Asosiy qidiruv: {clean_sq[0][:70]}")

            # ── 1. YouTube kliplar — shot_list yoki oddiy qidiruv ──
            log.info("  🎬 YouTube kliplar qidirilmoqda...")
            if shot_list and len(shot_list) >= 3:
                log.info(f"  🎬 Shot list rejimi: {len(shot_list)} ta kadr...")
                yt_clips = fetch_clips_per_shot(shot_list)
                log.info(f"  🎬 Shot list: {len(yt_clips)} ta klip olindi")
            else:
                yt_clips = fetch_youtube_clips(
                    combined_kw, count=5, search_queries=clean_sq)

            # ── 2. Dailymotion + Vimeo — fallback ────────────
            if len(yt_clips) < 2:
                log.info("  🌐 Web kliplar (Dailymotion/Vimeo)...")
                web_clips = fetch_web_clips(
                    combined_kw, count=3, search_queries=clean_sq)
                yt_clips = yt_clips + web_clips

            if not yt_clips:
                log.warning("  ⚠️  Video klip topilmadi — o'tkazildi")
                os.rename(qfile, qfile.replace(".json", "_no_video.json"))
                continue

            # ── Sarlavhalarni tekshirish va tuzatish ─────────────
            try:
                import sys as _sys
                _sys.path.insert(0, "../TELEGRAM")
                from translator import _apply_uz_places, lat2cyr, _fix_title_only
                _CYR_UZ = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯЎҚҒҲ"

                def _is_corrupt(text, lang_code):
                    """Sarlavha buzilganmi: noto'g'ri alifbo yoki bo'sh."""
                    if not text or len(text.strip()) < 5:
                        return True
                    alpha = [c for c in text if c.isalpha()]
                    if not alpha:
                        return True
                    if lang_code in ("uz", "ru"):
                        # Kirill majburiy: 15%+ lotin = buzilgan
                        latin_n = sum(1 for c in alpha if c.isascii())
                        if latin_n / len(alpha) > 0.15:
                            return True
                        cyr_n = sum(1 for c in alpha if c in _CYR_UZ)
                        if cyr_n / len(alpha) < 0.60:
                            return True
                    elif lang_code == "en":
                        # Inglizcha: kirill ko'p bo'lsa — buzilgan
                        cyr_n = sum(1 for c in alpha if c in _CYR_UZ)
                        if cyr_n / len(alpha) > 0.30:
                            return True
                    return False

                for fix_lang in ("uz", "ru", "en"):
                    val = sarlavhalar.get(fix_lang, "")
                    if not _is_corrupt(val, fix_lang):
                        continue  # Yaxshi — o'tkazib yuborish
                    # EN: agar asl sarlavha o'zi inglizcha bo'lsa — uni ishlatish
                    if fix_lang == "en" and title and not _is_corrupt(title, "en"):
                        log.info(f"  ℹ️  EN sarlavha buzilgan, asl sarlavhani ishlatmoqda: '{title[:50]}'")
                        sarlavhalar["en"] = title[:100]
                        continue
                    log.warning(f"  🔧 {fix_lang.upper()} sarlavha buzilgan: '{val[:50]}' — qayta tarjima...")
                    fixed = _fix_title_only(title, fix_lang)
                    if fixed and not _is_corrupt(fixed, fix_lang):
                        sarlavhalar[fix_lang] = fixed
                        log.info(f"  ✓ {fix_lang.upper()} tuzatildi: '{fixed[:50]}'")
                    else:
                        log.warning(f"  ✗ {fix_lang.upper()} tuzatilmadi — bo'sh qoldirildi")
                        sarlavhalar[fix_lang] = ""

                # UZ joy nomlarini tuzatish
                uz_s = sarlavhalar.get("uz", "")
                if uz_s:
                    sarlavhalar["uz"] = _apply_uz_places(uz_s)

            except Exception as _e:
                log.debug(f"Sarlavha tuzatish xato: {_e}")

            # jumla — tavsif uchun (script emas, intro yo'q)
            jumlalar = data.get("jumla", {})

            any_success = False
            for lang in ["en", "ru", "uz"]:
                sarlavha = sarlavhalar.get(lang, "")

                # ── Sarlavha majburiy tekshiruv ───────────────
                if not _title_ok(sarlavha, lang):
                    log.warning(f"  ⚠️  {lang.upper()} sarlavha yaroqsiz: '{sarlavha[:60]}' — tuzatilmoqda...")
                    sarlavha = _repair_title(sarlavha, title, lang)
                    # Hali ham yomon bo'lsa — bu tilni o'tkazib yuborish
                    if not _title_ok(sarlavha, lang):
                        log.warning(f"  ⛔ {lang.upper()} sarlavha tuzatilmadi — o'tkazildi")
                        continue

                # YouTube tavsifi: jumla (intro yo'q) yoki article description
                jumla_desc = jumlalar.get(lang, "")
                if not jumla_desc:
                    # Eski queue fayllar uchun: scriptdan intro tozalab olish
                    raw_script = scripts.get(lang, "")
                    # Oddiy regex bilan intro tozalash (youtube_maker import qilmasdan)
                    import re as _re
                    raw_script = _re.sub(
                        r"^(Efirda\s+1KUN\s+Global\.?|В\s+эфире\s+1ДЕНЬ\s+Global\.?|This\s+is\s+1DAY\s+Global\.?)\s*",
                        "", raw_script, flags=_re.IGNORECASE).strip()
                    jumla_desc = raw_script[:300]

                video_data = {
                    "lang":                lang,
                    "sarlavha":            sarlavha,
                    "youtube_script_latin": scripts.get(lang, ""),
                    "location":            location.get(lang, ""),
                    "daraja":              daraja,
                    "article_url":         article_url,
                    "keywords_en":         combined_kw,
                    "search_queries":      combined_kw,
                    "yt_clips":            yt_clips,
                    "jumla1":              jumla_desc,
                    "jumla2":              "",   # jumla_desc ichida bor
                    "hook":                data.get("hook", {}),
                    "hashtaglar":          data.get("sarlavha", {}).get(lang, ""),
                }
                log.info(f"  🎬 {lang.upper()} video: '{sarlavha[:55]}'")
                result = youtube_pipeline(video_data)
                if result:
                    any_success = True

            if not any_success:
                log.warning("  ⚠️  Hech bir video yaratilmadi — error ga o'tkazildi")
                error_path = qfile.replace(".json", "_error.json")
                if os.path.exists(error_path):
                    os.remove(error_path)
                os.rename(qfile, error_path)
                continue

            # ── Ko'rilganlarga qo'shish ───────────────────────
            if article_url:
                seen.add(article_url)
                save_seen(seen)

            # ── Arxivga ko'chirish ────────────────────────────
            done_dir = f"{QUEUE_DIR}/done"
            os.makedirs(done_dir, exist_ok=True)
            os.rename(qfile, f"{done_dir}/{os.path.basename(qfile)}")
            log.info("✅ Video tayyor\n")

        except Exception as e:
            log.error(f"Video xato: {e}", exc_info=True)
            if os.path.exists(qfile):
                error_path = qfile.replace(".json", "_error.json")
                try:
                    if os.path.exists(error_path):
                        os.remove(error_path)
                    os.rename(qfile, error_path)
                except Exception:
                    pass


def main():
    log.info("🎬 YouTube pipeline ishga tushdi")

    if "--now" in sys.argv:
        process_queue()
        return

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BlockingScheduler(timezone=TASHKENT)
    scheduler.add_job(
        process_queue,
        IntervalTrigger(minutes=30),
        id="youtube_queue",
        misfire_grace_time=300,
    )
    log.info("⏰ Har 30 daqiqada navbat tekshiriladi")
    log.info("Ctrl+C — to'xtatish\n")
    process_queue()
    scheduler.start()


if __name__ == "__main__":
    main()
