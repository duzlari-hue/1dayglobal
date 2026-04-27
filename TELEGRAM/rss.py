"""rss.py — RSS yangiliklar va seen_links"""
import os
import re
import time
import calendar
import random
import feedparser
import logging
from datetime import datetime

from config import RSS_FEEDS

log = logging.getLogger(__name__)

SEEN_LINKS_FILE = "output/seen_links.txt"

# ── Yangilik ahamiyatlilik reytingi ──────────────────────────
# Yuqori ball = muhimroq yangilik
_MUHIM_WORDS = {
    # Urush / inqiroz (10 ball)
    "war":10, "attack":10, "strike":10, "invasion":10, "bomb":10,
    "missile":10, "troops":10, "killed":10, "casualties":10,
    "ceasefire":10, "genocide":10, "massacre":10, "hostage":10,
    "nuclear":10, "weapons":10, "explosion":10, "earthquake":10,
    "tsunami":10, "hurricane":10, "flood":10, "crisis":8,
    # Siyosat / diplomatiya (8 ball)
    "sanctions":8, "treaty":8, "summit":8, "election":8, "coup":8,
    "president":7, "minister":7, "parliament":7, "congress":7,
    "referendum":8, "protest":7, "riot":8, "arrested":7,
    "sentenced":7, "charged":7, "accused":7, "resigned":7,
    "assassination":10, "impeach":8,
    # Iqtisodiyot (6 ball)
    "tariff":6, "trade":5, "economy":5, "inflation":6, "recession":7,
    "gdp":5, "oil":6, "energy":5, "bank":5, "market":4, "stock":4,
    "deal":5, "agreement":5, "accord":5, "bilateral":5,
    # Munosabatlar / diplomatiya (5 ball)
    "diplomatic":5, "relations":4, "alliance":5, "nato":6, "un":5,
    "united nations":6, "eu":5, "imf":5, "g7":6, "g20":6, "brics":5,
    # Taniqli shaxslar (4 ball)
    "trump":5, "biden":4, "putin":5, "xi":4, "zelensky":5,
    "macron":4, "modi":4, "netanyahu":5, "erdogan":4,
}

_SOFT_WORDS = {
    # Muxbir sayohati / shaxsiy hikoyalar — xabar emas
    "reporter", "journalist", "correspondent", "our reporter", "i visited",
    "i spent", "i met", "i talked", "i asked", "i went to", "a day in",
    "a week in", "a year in", "behind the scenes", "inside story",
    "personal story", "first person", "memoir",
    # Feature / tahlil — shoshilinch emas
    "history of", "how it works", "explainer", "explained", "what is",
    "what are", "why does", "opinion", "analysis", "commentary",
    "in pictures", "photo essay", "gallery",
    # Madaniyat / sport — yuqori ahamiyatli emas
    "oscars", "emmys", "grammys", "fashion week", "film festival",
    "celebrity", "actor", "singer", "musician", "pop star",
}

def _score_article(article: dict) -> float:
    """Maqolaning ahamiyatlilik ballini hisoblash."""
    title = article.get("title","").lower()
    desc  = article.get("description","").lower()
    text  = title + " " + desc
    score = 0.0

    # Kalit so'zlar bo'yicha ball
    for word, pts in _MUHIM_WORDS.items():
        if word in text:
            score += pts

    # Soft news jarima: -8 ball
    for sw in _SOFT_WORDS:
        if sw in text:
            score -= 8
            break  # bitta topildi — yetarli

    # Yangilik yangiligi: so'nggi 6 soat = +5 ball, 6-24 soat = 0, 24-48 soat = -5
    pub = article.get("published_ts")
    if pub:
        age_h = (time.time() - pub) / 3600
        if age_h <= 6:
            score += 5
        elif age_h > 24:
            score -= 5

    return score


def load_seen_links():
    if os.path.exists(SEEN_LINKS_FILE):
        with open(SEEN_LINKS_FILE, "r", encoding="utf-8") as f:
            links = [l.strip() for l in f.readlines() if l.strip()]
        return set(links[-1000:])
    return set()


_TOPIC_STOP = {
    "the", "and", "for", "are", "was", "has", "had", "not", "but", "this",
    "that", "with", "from", "they", "have", "been", "will", "said", "more",
    "than", "when", "also", "some", "would", "about", "their", "other",
    "into", "its", "all", "can", "who", "what", "were", "one", "year",
    "says", "amid", "claim", "report", "after", "over", "calls", "then",
    "there", "both", "very", "just", "still", "while", "under", "each",
}


def _title_stems(title: str) -> set:
    words = re.findall(r'[A-Za-z]{4,}', title.lower())
    return {w[:5] for w in words if w not in _TOPIC_STOP}


def save_seen_link(link, title="", keywords=None):
    with open(SEEN_LINKS_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")
        if title:
            # Sarlavhaning stem'larini saqlash (cross-run topic dedup uchun)
            stems = _title_stems(title)
            if stems:
                f.write(f"__title__{','.join(stems)}\n")
        if keywords:
            for kw in keywords[:3]:
                f.write(f"__kw__{kw.lower()}\n")


def is_topic_seen(title: str, threshold: int = 2) -> bool:
    """Sarlavha so'nggi yangiliklardagi mavzu bilan mos kelsa True qaytaradi."""
    if not title or not os.path.exists(SEEN_LINKS_FILE):
        return False
    new_stems = _title_stems(title)
    if not new_stems:
        return False
    with open(SEEN_LINKS_FILE, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip().startswith("__title__")]
    # Faqat so'nggi 200 ta yozuvni tekshirish
    for line in lines[-200:]:
        saved_stems = set(line.replace("__title__", "").split(","))
        if len(new_stems & saved_stems) >= threshold:
            return True
    return False


def fetch_rss_news(count=10):
    """Barcha RSS kanallardan yangi maqolalarni olish va ahamiyat bo'yicha saralash."""
    articles     = []
    seen_links   = load_seen_links()
    session_seen = set()
    feeds        = RSS_FEEDS.copy()
    random.shuffle(feeds)

    for feed_info in feeds:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:8]:   # har kanaldan ko'proq olish
                title = entry.get("title", "").strip()
                link  = entry.get("link",  "").strip()
                desc  = entry.get("summary", entry.get("description", "")).strip()

                if not title or not link:
                    continue
                if link in seen_links or link in session_seen:
                    continue

                # ── Sana tekshiruvi: 48 soatdan eski yangilikni o'tkazib yuborish ──
                pub_ts = None
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    try:
                        pub_ts = calendar.timegm(pub)
                        age_h  = (time.time() - pub_ts) / 3600
                        if age_h > 48:
                            log.debug(f"Eski yangilik ({age_h:.0f}h): {title[:50]}")
                            continue
                    except Exception:
                        pass

                if is_topic_seen(title):
                    log.debug(f"Mavzu allaqachon ko'rilgan: {title[:60]}")
                    continue

                session_seen.add(link)
                articles.append({
                    "title":        title,
                    "description":  re.sub(r"<[^>]+>", "", desc)[:500],
                    "link":         link,
                    "source":       feed_info["name"],
                    "published_ts": pub_ts,   # reyting uchun
                })
        except Exception as e:
            log.warning(f"RSS ({feed_info['name']}): {e}")

    # ── Ahamiyatlilik bo'yicha saralash ──────────────────────
    articles.sort(key=_score_article, reverse=True)

    if articles:
        top = articles[0]
        score = _score_article(top)
        log.info(f"RSS: {len(articles)} ta yangi maqola — eng muhim (ball={score:.0f}): {top['title'][:60]}")
    else:
        log.info("RSS: yangilik topilmadi")

    return articles[:count]
