"""app.py — Asosiy pipeline va scheduler"""
import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv

RUSSIAN_DOMAINS = {
    "tass.ru", "ria.ru", "rt.com", "sputniknews.com", "regnum.ru",
    "interfax.ru", "kommersant.ru", "rbc.ru", "gazeta.ru", "lenta.ru",
    "iz.ru", "tvzvezda.ru", "vesti.ru", "1tv.ru", "ntv.ru", "mk.ru",
    "kp.ru", "aif.ru", "riafan.ru", "life.ru", "pravda.ru",
    "rg.ru", "russia.tv", "ren.tv",
}

def _is_russian(url: str) -> bool:
    url = url.lower()
    return any(d in url for d in RUSSIAN_DOMAINS)

load_dotenv(dotenv_path=".env")

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config import TASHKENT, SCHEDULE_HOURS, TELEGRAM_CHANNEL_UZ
from rss import fetch_rss_news, save_seen_link
from translator import groq_translate
from telegram_bot import send_all_languages, send_daily_digest_all, \
                         send_en_from_rss, send_uz_ru_languages
from photo_of_day import run_photo_of_day, run_hayrat_nigoh, run_kun_fotosi

# ── YouTube special shorts (YOUTUBE papkasidan import) ─────────
import pathlib as _pl_app
import sys as _sys_app
import importlib.util as _ilu
_YT_DIR = str(_pl_app.Path(__file__).parent.parent / "YOUTUBE")
if _YT_DIR not in _sys_app.path:
    _sys_app.path.insert(0, _YT_DIR)
try:
    # sys.modules['config'] da TELEGRAM config cached — YOUTUBE config bilan vaqtincha almashtirish
    _tg_config_cached = _sys_app.modules.get("config")
    _yt_config_path   = str(_pl_app.Path(__file__).parent.parent / "YOUTUBE" / "config.py")
    _yt_config_spec   = _ilu.spec_from_file_location("config", _yt_config_path)
    _yt_config_mod    = _ilu.module_from_spec(_yt_config_spec)
    _sys_app.modules["config"] = _yt_config_mod
    _yt_config_spec.loader.exec_module(_yt_config_mod)

    from special_shorts import (
        run_numbers_short, run_history_short,
        run_fakt_short, run_breaking_short, run_top5_short,
    )
    _SHORTS_OK = True
except Exception as _e:
    log_pre = logging.getLogger(__name__)
    log_pre.warning(f"⚠️  special_shorts import muvaffaqiyatsiz: {_e}")
    _SHORTS_OK = False
    def run_numbers_short(*a, **kw): pass
    def run_history_short(*a, **kw): pass
    def run_fakt_short(*a, **kw): pass
    def run_breaking_short(*a, **kw): pass
    def run_top5_short(*a, **kw): pass
finally:
    # TELEGRAM config ni qayta tiklash
    if _tg_config_cached is not None:
        _sys_app.modules["config"] = _tg_config_cached
    else:
        _sys_app.modules.pop("config", None)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("output/bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


def run_pipeline():
    # ── Lock: parallel bot instansiyalari bir vaqtda ishlamasin ──
    import time as _time
    _lock_path = "output/.pipeline.lock"
    os.makedirs("output", exist_ok=True)
    if os.path.exists(_lock_path):
        try:
            age = _time.time() - os.path.getmtime(_lock_path)
            if age < 600:   # 10 daqiqadan kam — boshqa instansiya ishlayapti
                log.warning(f"⏳ Pipeline lock band ({age:.0f}s) — o'tkazildi")
                return
        except Exception:
            pass
    try:
        open(_lock_path, "w").close()
    except Exception:
        pass

    try:
        _run_pipeline_inner()
    finally:
        try:
            os.remove(_lock_path)
        except Exception:
            pass


def _run_pipeline_inner():
    log.info(f"🚀 Pipeline — {datetime.now(TASHKENT).strftime('%H:%M')} (Toshkent)")

    # 1. RSS
    try:
        articles = fetch_rss_news(count=10)
    except Exception as e:
        log.error(f"RSS: {e}")
        return

    if not articles:
        log.warning("Yangilik topilmadi")
        return

    # Rossiya manbalarini o'tkazib yuborish
    articles = [a for a in articles if not _is_russian(a.get("link", ""))]
    if not articles:
        log.warning("Barcha yangiliklar Rossiya manbasi — o'tkazildi")
        return

    # Eng muhim yangilik — rss.py allaqachon ball bo'yicha saralagan
    # Agar birinchi yangilik juda oddiy bo'lsa (ball < 5) — keyingisiga o'tish
    from rss import _score_article

    # ── Barcha maqolalar ro'yxati (ball bo'yicha) ────────────
    log.info("─" * 60)
    log.info(f"{'№':>2}  {'Ball':>4}  Sarlavha")
    log.info("─" * 60)
    for i, a in enumerate(articles, 1):
        sc = _score_article(a)
        marker = " ◀ TANLANDI" if i == 1 else ""
        log.info(f"{i:>2}. [{sc:>4.0f}]  {a['title'][:65]}{marker}")
    log.info("─" * 60)

    article = articles[0]
    if len(articles) > 1:
        top_score = _score_article(article)
        # 2-3-chi yangilik birinchidan 2x muhimroq bo'lsa — uni tanlash
        for candidate in articles[1:3]:
            cand_score = _score_article(candidate)
            if cand_score > top_score * 1.5 and cand_score > 10:
                log.info(f"  📊 Muhimroq yangilik tanlandi (ball={cand_score:.0f} vs {top_score:.0f})")
                article = candidate
                break

    log.info(f"📰 {article['title'][:70]}...")

    # ── YANGI 2 BOSQICHLI PIPELINE ──────────────────────────────
    #
    # BOSQICH 1: EN kanalga RSS DAN DARHOL post (tarjima kutilmaydi)
    #   → inglizcha material har doim to'liq va tez chop etiladi
    #
    # BOSQICH 2: AI tarjima → UZ + RU kanallariga post
    #   → agar tarjima muvaffaqiyatli bo'lsa — UZ/RU ham chop etiladi
    #   → agar tarjima muvaffaqiyatsiz bo'lsa — faqat EN chop etilgan bo'ladi
    # ──────────────────────────────────────────────────────────────

    # 2. BOSQICH 1: EN darhol chop etish (RSS dan, AI yo'q)
    log.info("📤 EN kanal: RSS dan darhol chop etilmoqda...")
    send_en_from_rss(article)
    save_seen_link(article["link"], title=article.get("title", ""), keywords=[])

    # 3. BOSQICH 2: AI tarjima (UZ + RU uchun)
    d = None
    try:
        log.info("🌐 AI tarjima: UZ + RU uchun...")
        d = groq_translate(article["title"], article["description"], article["source"])
        log.info("✅ AI tarjima muvaffaqiyatli")
    except Exception as e:
        log.error(f"⚠️  AI tarjima muvaffaqiyatsiz: {e}")
        log.warning("   EN allaqachon chop etildi. UZ/RU o'tkazildi.")

    # 4. UZ + RU chop etish (agar tarjima muvaffaqiyatli bo'lsa)
    if d:
        send_uz_ru_languages(d, article)
        # keywords ni seen_links ga yangilash
        if d.get("keywords_en"):
            save_seen_link(article["link"],
                          title=article.get("title",""),
                          keywords=d.get("keywords_en", []))
    else:
        # Tarjima yo'q — minimal d tuzamiz (YouTube queue uchun)
        _title = article.get("title", "")
        _desc  = (article.get("description", "") or _title).strip()
        d = {
            "sarlavha_uz": "", "jumla1_uz": "", "jumla2_uz": "",
            "sarlavha_ru": "", "jumla1_ru": "", "jumla2_ru": "",
            "sarlavha_en": _title[:120], "jumla1_en": _desc[:500], "jumla2_en": "",
            "script_uz": "", "script_ru": "", "script_en": _desc,
            "daraja": "xabar",
            "hashtag_uz": "#Yangilik #1KUN",
            "hashtag_ru": "#Новости #1День",
            "hashtag_en": "#News #World #1Day",
            "keywords_en": _title.split()[:5],
            "search_queries": [_title[:50]],
            "location_uz": "", "location_ru": "", "location_en": "",
            "shot_list": [], "hook_uz": "", "hook_ru": "", "hook_en": _title[:50],
        }

    # 5. Kunlik digest buferiga qo'shish
    _DAILY_BUFFER.append(d)
    log.info(f"📝 Digest buffer: {len(_DAILY_BUFFER)} ta yangilik")

    # 6. YouTube queue — yangilikni qo'shish
    _save_to_youtube_queue(d, article)

    # 6. YouTube Shorts — har bir yangilikdan keyin (soatga qarab navbat bilan)
    if _SHORTS_OK:
        _art_for_short = {
            "title":       article.get("title", ""),
            "description": article.get("description", ""),
        }
        _cur_hour = datetime.now(TASHKENT).hour
        # Soat bo'yicha navbatma-navbat:  08→fakt, 11→breaking, 14→numbers, 17→fakt, 20→breaking
        _short_rotation = {
            8:  ("fakt",     run_fakt_short),
            11: ("breaking", run_breaking_short),
            14: ("numbers",  run_numbers_short),
            17: ("fakt",     run_fakt_short),
            20: ("breaking", run_breaking_short),
        }
        _short_fn_name, _short_fn = _short_rotation.get(
            _cur_hour, ("numbers", run_numbers_short)
        )
        try:
            log.info(f"▶ Short format: {_short_fn_name.upper()} [{_cur_hour}:00]")
            _short_fn(article=_art_for_short)
        except Exception as _se:
            log.warning(f"⚠️  Short [{_short_fn_name}] xato: {_se}")

    log.info("✅ Pipeline tugadi\n")


# ── YouTube queue ga yozish ────────────────────────────────────
import json
import pathlib

_YOUTUBE_QUEUE = pathlib.Path(__file__).parent.parent / "YOUTUBE" / "queue"

def _save_to_youtube_queue(d: dict, article: dict):
    """Yangilikni YouTube queue papkasiga saqlash (analysis_maker uchun)."""
    try:
        _YOUTUBE_QUEUE.mkdir(exist_ok=True)
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        fname = _YOUTUBE_QUEUE / f"{ts}.json"

        scripts = {
            "uz": d.get("script_uz", "") or d.get("hook_uz", ""),
            "ru": d.get("script_ru", "") or d.get("hook_ru", ""),
            "en": d.get("script_en", "") or d.get("hook_en", ""),
        }
        sarlavha = {
            "uz": d.get("sarlavha_uz", ""),
            "ru": d.get("sarlavha_ru", ""),
            "en": d.get("sarlavha_en", ""),
        }
        jumla = {
            "uz": d.get("jumla1_uz", ""),
            "ru": d.get("jumla1_ru", ""),
            "en": d.get("jumla1_en", ""),
        }
        location = {
            "uz": d.get("location_uz", ""),
            "ru": d.get("location_ru", ""),
            "en": d.get("location_en", ""),
        }

        queue_item = {
            "article":      article,
            "scripts":      scripts,
            "sarlavha":     sarlavha,
            "jumla":        jumla,
            "location":     location,
            "daraja":       d.get("daraja", "xabar"),
            "keywords_en":  d.get("keywords_en", []),
            "search_queries": d.get("search_queries", []),
        }
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(queue_item, f, ensure_ascii=False, indent=2)
        log.info(f"📥 YouTube queue: {fname.name}")
    except Exception as e:
        log.warning(f"YouTube queue xato: {e}")


# ── Kunlik digest pipeline ─────────────────────────────────────
_DAILY_BUFFER = []   # kun davomida to'plangan yangiliklar


def run_daily_digest():
    """Kun bo'yi to'plangan 5-6 yangilikni kechqurun bitta postda yuborish."""
    global _DAILY_BUFFER

    if not _DAILY_BUFFER:
        log.info("📋 Kunlik digest: bufer bo'sh, o'tkazildi")
        return

    log.info(f"📋 Kunlik digest: {len(_DAILY_BUFFER)} ta yangilik yuborilmoqda...")

    articles_by_lang = {"uz": [], "ru": [], "en": []}
    for d in _DAILY_BUFFER[-6:]:   # so'nggi 6 ta
        for lang in ("uz", "ru", "en"):
            sarlavha = d.get(f"sarlavha_{lang}", "")
            jumla    = d.get(f"jumla1_{lang}", "")
            if sarlavha or jumla:
                articles_by_lang[lang].append({
                    "sarlavha": sarlavha,
                    "jumla1":   jumla,
                    "daraja":   d.get("daraja", "xabar"),
                })

    send_daily_digest_all(articles_by_lang)

    # TOP-5 TEZKOR shorti — digest bilan bir vaqtda (raw article titles)
    if _SHORTS_OK:
        try:
            _arts_for_top5 = [
                {
                    "title":       d.get("sarlavha_en") or d.get("sarlavha_uz", ""),
                    "description": d.get("jumla1_en")   or d.get("jumla1_uz", ""),
                }
                for d in _DAILY_BUFFER[-5:]
            ]
            run_top5_short(articles=_arts_for_top5)
        except Exception as _te:
            log.warning(f"⚠️  Top-5 short xato: {_te}")

    _DAILY_BUFFER.clear()
    log.info("✅ Kunlik digest yuborildi, bufer tozalandi")


def main():
    # Tarjimon: GEMINI_API_KEY yoki OPENROUTER_API_KEY — kamida biri bo'lishi kerak
    if not os.getenv("GEMINI_API_KEY") and not os.getenv("OPENROUTER_API_KEY"):
        log.error("❌ .env: GEMINI_API_KEY yoki OPENROUTER_API_KEY kerak!")
        return
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        log.error("❌ .env: TELEGRAM_BOT_TOKEN topilmadi!")
        return

    log.info(f"1Kun Global News | {TELEGRAM_CHANNEL_UZ} | {SCHEDULE_HOURS}")
    scheduler = BlockingScheduler(timezone=TASHKENT)
    for hour in SCHEDULE_HOURS:
        scheduler.add_job(
            run_pipeline,
            CronTrigger(hour=hour, minute=0, timezone=TASHKENT),
            id=f"post_{hour}",
            misfire_grace_time=300,
        )
    # Kunlik digest — kechqurun 21:00 (barcha yangiliklar yig'ilgandan keyin)
    scheduler.add_job(
        run_daily_digest,
        CronTrigger(hour=21, minute=0, timezone=TASHKENT),
        id="daily_digest",
        misfire_grace_time=300,
    )
    # Dunyoga Hayrat Nigohi — 12:00
    scheduler.add_job(
        run_hayrat_nigoh,
        CronTrigger(hour=12, minute=0, timezone=TASHKENT),
        id="hayrat_nigoh",
        misfire_grace_time=600,
    )
    # Kun Fotosi — kechki 20:00
    scheduler.add_job(
        run_kun_fotosi,
        CronTrigger(hour=20, minute=0, timezone=TASHKENT),
        id="kun_fotosi",
        misfire_grace_time=600,
    )
    # Bugun Tarixda shorti — 10:00
    if _SHORTS_OK:
        scheduler.add_job(
            run_history_short,
            CronTrigger(hour=10, minute=0, timezone=TASHKENT),
            id="history_short",
            misfire_grace_time=600,
        )
    log.info(f"⏰ Yangiliklar: {', '.join(str(h)+':00' for h in SCHEDULE_HOURS)}")
    log.info("⏰ Kunlik digest: 21:00 (Toshkent)")
    log.info("⏰ Dunyoga Hayrat Nigohi: 12:00 (Toshkent)")
    log.info("⏰ Kun Fotosi: 20:00 (Toshkent)")
    if _SHORTS_OK:
        log.info("⏰ Bugun Tarixda shorti: 10:00 (Toshkent)")
    log.info("Ctrl+C — to'xtatish\n")

    if "--now" in sys.argv or "--once" in sys.argv:
        run_pipeline()
        return
    if "--photo" in sys.argv:
        run_hayrat_nigoh(force=True)
        return
    if "--kunfoto" in sys.argv:
        run_kun_fotosi(force=True)
        return
    if "--history" in sys.argv:
        run_history_short()
        return
    if "--fakt" in sys.argv:
        run_fakt_short(article={"title": "World news today", "description": ""})
        return
    if "--breaking" in sys.argv:
        run_breaking_short(article={"title": "World news today", "description": ""})
        return
    if "--top5" in sys.argv:
        run_top5_short(articles=[{"title": f"News story #{i}", "description": ""} for i in range(1, 6)])
        return
    if "--numbers" in sys.argv:
        # test uchun namunaviy yangilik
        run_numbers_short(article={
            "title": sys.argv[sys.argv.index("--numbers")+1]
                     if "--numbers" in sys.argv and sys.argv.index("--numbers")+1 < len(sys.argv)
                        and not sys.argv[sys.argv.index("--numbers")+1].startswith("--")
                     else "World economic summit 2026",
            "description": ""
        })
        return

    scheduler.start()


if __name__ == "__main__":
    main()
