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
from telegram_bot import send_all_languages

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

    # 2. Groq tarjima (1 so'rov)
    try:
        d = groq_translate(article["title"], article["description"], article["source"])
    except Exception as e:
        log.error(f"Groq: {e}")
        return

    save_seen_link(article["link"], keywords=d.get("keywords_en", []))

    # 3. Telegram — 3 kanalga 3 tilda
    send_all_languages(d, article)

    # 4. YouTube ga ma'lumot yuborish (alohida servis)
    try:
        import json
        youtube_data = {
            "article":  article,
            "scripts": {
                "uz": d.get("script_uz", ""),
                "ru": d.get("script_ru", ""),
                "en": d.get("script_en", ""),
            },
            "sarlavha": {
                "uz": d.get("sarlavha_uz", ""),
                "ru": d.get("sarlavha_ru", ""),
                "en": d.get("sarlavha_en", ""),
            },
            "keywords_en":    d.get("keywords_en", []),
            "search_queries": d.get("search_queries", []),
            "shot_list":      d.get("shot_list", []),
            "hook": {
                "uz": d.get("hook_uz", ""),
                "ru": d.get("hook_ru", ""),
                "en": d.get("hook_en", ""),
            },
            "location": {
                "uz": d.get("location_uz", ""),
                "ru": d.get("location_ru", ""),
                "en": d.get("location_en", ""),
            },
            "daraja":   d.get("daraja", "xabar"),
        }
        os.makedirs("output/youtube_queue", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(f"output/youtube_queue/{ts}.json", "w", encoding="utf-8") as f:
            json.dump(youtube_data, f, ensure_ascii=False, indent=2)
        log.info(f"📁 YouTube navbat: {ts}.json")
    except Exception as e:
        log.warning(f"YouTube navbat xato: {e}")

    log.info("✅ Pipeline tugadi\n")


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
    log.info(f"⏰ {', '.join(str(h)+':00' for h in SCHEDULE_HOURS)}")
    log.info("Ctrl+C — to'xtatish\n")

    if "--now" in sys.argv:
        run_pipeline()

    scheduler.start()


if __name__ == "__main__":
    main()
