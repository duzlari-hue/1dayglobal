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
from telegram_bot import send_all_languages, send_daily_digest_all

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

    # 2. Tarjima (Gemini → OpenRouter zanjiri)
    try:
        d = groq_translate(article["title"], article["description"], article["source"])
    except Exception as e:
        log.error(f"Barcha tarjimon servislari muvaffaqiyatsiz: {e}")
        # Tarjima to'liq muvaffaqiyatsiz — faqat EN kanalga inglizcha post yuboramiz
        _desc = (article.get("description", "") or article.get("title", "")).strip()
        log.warning("⚠️  Faqat EN kanalga inglizcha post yuborilmoqda (tarjima yo'q)...")
        d = {
            "sarlavha_uz": "", "jumla1_uz": "", "jumla2_uz": "",
            "sarlavha_ru": "", "jumla1_ru": "", "jumla2_ru": "",
            "sarlavha_en": article.get("title", "")[:80],
            "jumla1_en":   _desc[:500],
            "jumla2_en":   "",
            "script_uz":   "", "script_ru": "",
            "script_en":   _desc,
            "daraja":      "xabar",
            "hashtag_uz":  "#Янгилик #1КУН",
            "hashtag_ru":  "#Новости #1День",
            "hashtag_en":  "#News #World #1Day",
            "keywords_en": article.get("title", "").split()[:5],
            "search_queries": [article.get("title", "")[:50]],
            "location_uz": "", "location_ru": "", "location_en": "",
            "shot_list":   [], "hook_uz": "", "hook_ru": "",
            "hook_en":     article.get("title", "")[:50],
        }

    save_seen_link(article["link"], keywords=d.get("keywords_en", []))

    # 3. Telegram — 3 kanalga 3 tilda
    send_all_languages(d, article)

    # 4. Kunlik digest buferiga qo'shish
    _DAILY_BUFFER.append(d)
    log.info(f"📝 Digest buffer: {len(_DAILY_BUFFER)} ta yangilik")

    # 5. YouTube queue — yangilikni qo'shish
    _save_to_youtube_queue(d, article)

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
    log.info(f"⏰ Yangiliklar: {', '.join(str(h)+':00' for h in SCHEDULE_HOURS)}")
    log.info("⏰ Kunlik digest: 21:00 (Toshkent)")
    log.info("Ctrl+C — to'xtatish\n")

    if "--now" in sys.argv or "--once" in sys.argv:
        run_pipeline()
        return

    scheduler.start()


if __name__ == "__main__":
    main()
