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
# MUHIM: faqat SARLAVHA da bo'lsa qo'shimcha 1.5x ko'paytiruvchi
_MUHIM_WORDS = {
    # ── Urush / halokatlar (12 ball) ─────────────────────────
    "war":12, "attack":12, "airstrike":12, "strike":10, "invasion":12,
    "bomb":11, "bombing":12, "missile":11, "rockets":10,
    "troops":10, "soldiers":9, "military":8,
    "killed":12, "dead":10, "deaths":11, "casualties":11,
    "wounded":10, "injured":9,
    "ceasefire":11, "genocide":13, "massacre":13, "hostage":12,
    "nuclear":12, "chemical weapon":14, "biological weapon":14,
    "explosion":11, "earthquake":12, "tsunami":13,
    "hurricane":11, "flood":10, "wildfire":9, "disaster":9,
    # ── Qatl / hibsga olish (o'ta muhim) ─────────────────────
    "executed":13, "execution":13, "executes":13,
    "hanged":12, "death penalty":13, "death sentence":13,
    "sentenced to death":14,
    "assassinated":13, "assassination":13,
    # ── Geopolitik inqirozlar ─────────────────────────────────
    "crisis":9, "conflict":9, "offensive":10, "siege":11,
    "blockade":10, "occupation":10, "annexed":10, "annexed":10,
    "coup":12, "uprising":10, "civil war":13, "ethnic cleansing":14,
    "famine":11, "starvation":11, "humanitarian":8,
    # ── Siyosat (8 ball) ──────────────────────────────────────
    "sanctions":8, "treaty":8, "summit":8, "election":8,
    "president":6, "parliament":6, "congress":6,
    "referendum":8, "protest":7, "riot":9,
    "arrested":7, "detained":7, "imprisoned":8,
    "sentenced":7, "charged":7, "accused":6, "resigned":8,
    "impeach":9, "overthrow":10,
    # ── Diplomatiya (6 ball) ──────────────────────────────────
    "sanctions":8, "diplomatic":5, "relations":4,
    "alliance":5, "nato":7, "un":5,
    "united nations":7, "eu":5, "imf":5, "g7":7, "g20":7,
    # ── Iqtisodiyot (5 ball) ──────────────────────────────────
    "tariff":6, "trade war":9, "economy":5, "inflation":6,
    "recession":8, "gdp":5, "oil":6, "energy":5,
    "bankruptcy":7, "default":7, "collapse":8,
    # ── Taniqli shaxslar (5 ball, faqat sarlavhada bo'lsa) ────
    "trump":5, "putin":5, "zelensky":5, "netanyahu":5,
    "xi jinping":6, "xi":4, "biden":4, "macron":4,
    "modi":4, "erdogan":4, "khamenei":6,
}

# ── Mamlakat/mintaqa bonusi (sarlavhada bo'lsa +ball) ─────────
# Faol urush/inqiroz hududlari — yuqori ustuvorlik
_GEO_BONUS = {
    "gaza":8, "israel":6, "palestine":7, "west bank":7,
    "ukraine":7, "russia":5, "kyiv":6, "moscow":5,
    "iran":7, "tehran":6, "syria":6, "damascus":5,
    "lebanon":6, "beirut":5, "hezbollah":7, "hamas":7,
    "sudan":7, "khartoum":6, "sahel":6, "mali":6,
    "north korea":7, "taiwan":7, "china sea":7,
    "kashmir":6, "pakistan":5, "afghanistan":6, "kabul":5,
    "somalia":6, "ethiopia":5, "niger":6, "burkina":6,
    "venezuela":5, "haiti":6, "myanmar":7,
    "yemen":7, "houthi":7, "saudi":5,
}

# ── Mahalliy siyosat / unchalik muhim emas ─────────────────────
# Bu so'zlar sarlavhada bo'lsa jarima
_LOCAL_POLITICS_PENALTY = {
    "crackdown on", "promises to", "pledges to", "vows to",
    "plans to", "proposes", "considering", "weighing",
    "rave", "nightclub", "party ban", "festival ban",
    "drug law", "cannabis", "marijuana law",
    "local election", "municipal", "city council",
    "regional", "provincial", "county",
}

_SOFT_WORDS = {
    # Muxbir sayohati / shaxsiy hikoyalar — xabar emas
    "reporter", "journalist", "correspondent", "our reporter",
    "i visited", "i spent", "i met", "i talked",
    "behind the scenes", "inside story", "personal story",
    "first person", "memoir",
    # Feature / tahlil — shoshilinch emas
    "history of", "how it works", "explainer", "explained",
    "what is", "what are", "why does", "opinion",
    "commentary", "in pictures", "photo essay", "gallery",
    # Madaniyat / sport — yuqori ahamiyatli emas
    "oscars", "emmys", "grammys", "fashion week", "film festival",
    "celebrity", "actor", "singer", "musician", "pop star",
    # Maishiy / kundalik hayot — siyosiy emas
    "recipe", "cooking", "food review", "restaurant",
    "travel guide", "holiday", "vacation", "tourism",
    "wildlife", "nature walk", "gardening",
    # Rave/party/concert — past ustuvorlik
    "rave party", "rave scene", "illegal rave",
    "music festival", "concert ban", "nightlife",
}


def _score_article(article: dict) -> float:
    """Maqolaning ahamiyatlilik ballini hisoblash.

    Strategiya:
    - SARLAVHA da kalit so'z → 1.5x ko'paytiruvchi (sarlavha muhimroq)
    - Tavsif da kalit so'z → 1x
    - Geopolitik mintaqa bonusi (faqat sarlavha)
    - Mahalliy siyosat / soft news jarima
    """
    title = article.get("title", "").lower()
    desc  = article.get("description", "").lower()

    score = 0.0

    # ── Kalit so'zlar: sarlavhada 1.5x, tavsifda 1x ──────────
    for word, pts in _MUHIM_WORDS.items():
        in_title = word in title
        in_desc  = word in desc
        if in_title:
            score += pts * 1.5   # sarlavha muhimroq
        elif in_desc:
            score += pts * 1.0

    # ── Geopolitik mintaqa bonusi (faqat sarlavha) ────────────
    for geo, bonus in _GEO_BONUS.items():
        if geo in title:
            score += bonus

    # ── Mahalliy siyosat jarima (sarlavhada) ──────────────────
    for phrase in _LOCAL_POLITICS_PENALTY:
        if phrase in title:
            score -= 10
            break

    # ── Soft news jarima ──────────────────────────────────────
    for sw in _SOFT_WORDS:
        if sw in title or sw in desc:
            score -= 10
            break

    # ── Yangilik yangiligi ────────────────────────────────────
    pub = article.get("published_ts")
    if pub:
        age_h = (time.time() - pub) / 3600
        if age_h <= 3:
            score += 7    # juda yangi
        elif age_h <= 6:
            score += 4
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
