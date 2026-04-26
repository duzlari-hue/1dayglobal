"""config.py — Telegram bot sozlamalari"""
import os
import pytz
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

# API kalitlar
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Telegram kanallar
TELEGRAM_CHANNEL_UZ = os.getenv("TELEGRAM_CHANNEL_UZ", "@birkunday")
TELEGRAM_CHANNEL_RU = os.getenv("TELEGRAM_CHANNEL_RU", "@birkunday_ru")
TELEGRAM_CHANNEL_EN = os.getenv("TELEGRAM_CHANNEL_EN", "@birkunday_en")

# Vaqt va jadval
TASHKENT       = pytz.timezone("Asia/Tashkent")
SCHEDULE_HOURS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]

# Papkalar
os.makedirs("output", exist_ok=True)

# RSS manbalar
RSS_FEEDS = [
    {"name": "BBC World",   "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Reuters",     "url": "https://feeds.reuters.com/reuters/worldNews"},
    {"name": "Al Jazeera",  "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "CNN",         "url": "http://rss.cnn.com/rss/edition_world.rss"},
    {"name": "AP News",     "url": "https://apnews.com/rss"},
    {"name": "France24",    "url": "https://www.france24.com/en/rss"},
    {"name": "DW",          "url": "https://rss.dw.com/xml/rss-en-world"},
    {"name": "Guardian",    "url": "https://www.theguardian.com/world/rss"},
    {"name": "NPR",         "url": "https://feeds.npr.org/1004/rss.xml"},
    {"name": "Sky News",    "url": "https://feeds.skynews.com/feeds/rss/world.xml"},
    {"name": "Euronews",    "url": "https://www.euronews.com/rss?level=theme&name=news"},
    {"name": "ABC News",    "url": "https://abcnews.go.com/abcnews/internationalheadlines"},
    {"name": "Eurasianet",  "url": "https://eurasianet.org/rss.xml"},
    {"name": "VOA News",    "url": "https://www.voanews.com/api/zkouvmqit_"},
    {"name": "Independent", "url": "https://www.independent.co.uk/news/world/rss"},
    {"name": "Time",        "url": "https://time.com/feed/"},
    {"name": "Ozodlik",     "url": "https://www.ozodlik.org/api/zpouvqpqmit"},
    {"name": "Kun.uz",      "url": "https://kun.uz/rss"},
]
