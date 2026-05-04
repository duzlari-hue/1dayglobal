"""photo_of_day.py — Ikkita kunlik foto ruknlari

1. run_hayrat_nigoh()  → soat 12:00
   "HAYRAT / NIGOH" — dunyo haqida hayratlanarli fakt + infografik karta (Image 2 uslubi)
   To'liq qizil fon, ulkan stat raqam, oq matn, brand style

2. run_kun_fotosi()    → soat 20:00
   "KUN FOTOSI" — bugun dunyoda sodir bo'lgan eng muhim voqeaning
   eng chiroyli va esda qolarli fotosi
"""
import os, sys, re, json, random, logging, pathlib, tempfile, subprocess, requests, textwrap
from datetime import datetime

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=True)

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHANNEL_UZ,
    TELEGRAM_CHANNEL_RU,
    TELEGRAM_CHANNEL_EN,
    TASHKENT,
)
from translator import _ask_anthropic, ANTHROPIC_API_KEY, groq_ask

log = logging.getLogger(__name__)
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

# ─────────────────────────────────────────────────────────────
# Sarlavhalar (FIXED — o'zgarmas, lotin UZ)
# ─────────────────────────────────────────────────────────────
_HAYRAT_TITLE = {
    "uz": "🔴 HAYRAT / NIGOH",
    "ru": "🔴 УДИВИТЕЛЬНЫЙ ФАКТ",
    "en": "🔴 AMAZING FACT",
}
_KUN_FOTOSI_TITLE = {
    "uz": "📸 KUN FOTOSI",
    "ru": "📸 ФОТО ДНЯ",
    "en": "📸 PHOTO OF THE DAY",
}

# ─────────────────────────────────────────────────────────────
# Shrift yordamchisi
# ─────────────────────────────────────────────────────────────
def _hf(size: int, bold: bool = True):
    """PIL shrift (Arial Bold/Regular)."""
    if not _PIL_OK:
        return None
    cands = (
        ["C:\\Windows\\Fonts\\arialbd.ttf", "C:\\Windows\\Fonts\\calibrib.ttf"]
        if bold else
        ["C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\calibri.ttf"]
    )
    for p in cands:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except Exception: pass
    return ImageFont.load_default()


# ─────────────────────────────────────────────────────────────
# HAYRAT NIGOH: Infografik karta generatsiyasi (Image 2 uslubi)
# ─────────────────────────────────────────────────────────────
def _make_hayrat_card(stat: str, text: str, source: str, lang: str, out_path: str) -> bool:
    """
    1DAY GLOBAL infografik karta (1080×1080) — Image 2 uslubi:
      · To'liq qizil (#CC0000) fon
      · Qora top bar: '10' logo + 'HAYRAT / NIGOH' + 'WORLD CURIOSITY'
      · Ulkan oq stat raqam (~280px)
      · Quyida katta oq UPPERCASE matn (3-4 qator)
      · Pastda: manba + divider + @handle + 1DAYGLOBAL.NEWS
    """
    if not _PIL_OK:
        return False
    try:
        W, H     = 1080, 1080
        C_RED    = (204, 0, 0)
        C_BLACK  = (10, 10, 10)
        C_WHITE  = (255, 255, 255)
        C_LGRAY  = (200, 195, 185)

        card = Image.new("RGB", (W, H), C_RED)
        draw = ImageDraw.Draw(card)

        # ── TOP BAR (qora) ─────────────────────────────────────
        bar_h = 58
        draw.rectangle([(0, 0), (W, bar_h)], fill=C_BLACK)
        # '10' qizil logo
        draw.rectangle([(12, 8), (58, 50)], fill=C_RED)
        draw.text((35, 29), "10", font=_hf(22), fill=C_WHITE, anchor="mm")
        # Section (har til uchun o'z nomi)
        sections = {
            "uz": "• HAYRAT / NIGOH",
            "ru": "• УДИВИТЕЛЬНЫЙ ФАКТ",
            "en": "• AMAZING FACT",
        }
        right_labels = {
            "uz": "WORLD CURIOSITY",
            "ru": "ИНТЕРЕСНО О МИРЕ",
            "en": "WORLD CURIOSITY",
        }
        draw.text((72, 29), sections.get(lang, "• AMAZING FACT"),
                  font=_hf(17, False), fill=(180, 175, 165), anchor="lm")
        draw.text((W - 14, 29), right_labels.get(lang, "WORLD CURIOSITY"),
                  font=_hf(15, False), fill=(180, 175, 165), anchor="rm")

        # ── ULKAN STAT RAQAMI ──────────────────────────────────
        stat_clean = (stat or "").strip()
        # Raqam qanchalik katta ekanligiga qarab font o'lchami
        if len(stat_clean) <= 4:
            stat_fs = 260
        elif len(stat_clean) <= 6:
            stat_fs = 200
        else:
            stat_fs = 160

        stat_y = bar_h + 30
        draw.text((40, stat_y), stat_clean,
                  font=_hf(stat_fs), fill=C_WHITE)

        # Raqam pastini aniqlash (taxminan)
        stat_bottom = stat_y + stat_fs + 20

        # ── MATN (katta oq, UPPERCASE) ─────────────────────────
        text_clean = (text or "").upper().strip()
        # Satrga bo'lish
        wrapped = textwrap.wrap(text_clean, width=24)[:4]
        ty = max(stat_bottom, bar_h + 360)
        for line in wrapped:
            draw.text((40, ty), line, font=_hf(52), fill=C_WHITE)
            ty += 64

        # ── MANBA ──────────────────────────────────────────────
        if source:
            src_y = H - 130
            # Ingichka gorizontal chiziq
            draw.line([(40, src_y - 16), (W - 40, src_y - 16)],
                      fill=(255, 255, 255, 80), width=1)
            # Manba matni
            src_label = {"uz": "MANBA", "ru": "ИСТОЧНИК", "en": "SOURCE"}.get(lang, "SOURCE")
            draw.text((40, src_y), f"{src_label}  ·  {source.upper()[:40]}",
                      font=_hf(18, False), fill=(220, 200, 200), anchor="lm")

        # ── PASTKI BAR ─────────────────────────────────────────
        bot_y = H - 58
        draw.rectangle([(0, bot_y), (W, H)], fill=C_BLACK)
        draw.line([(0, bot_y), (W, bot_y)], fill=(180, 0, 0), width=2)
        handles = {"uz": "@birkunday", "ru": "@birkunday_ru", "en": "@birkunday_en"}
        draw.text((40, bot_y + 28), handles.get(lang, "@birkunday"),
                  font=_hf(19, False), fill=(150, 145, 135), anchor="lm")
        draw.text((W - 40, bot_y + 28), "1DAYGLOBAL.NEWS",
                  font=_hf(19, False), fill=C_RED, anchor="rm")

        card.save(out_path, "JPEG", quality=94)
        return True
    except Exception as e:
        log.warning(f"_make_hayrat_card xato: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# HAYRAT NIGOH: AI fakt generatsiyasi
# ─────────────────────────────────────────────────────────────
def _gen_hayrat_factoid() -> dict:
    """
    AI dan hayratlanarli dunyo fakti generatsiyasi.
    Qaytaradi: {"stat": "73%", "uz": "...", "ru": "...", "en": "...",
                "source": "UN Water 2026",
                "ht_uz": "#...", "ht_ru": "#...", "ht_en": "#..."}
    """
    categories = [
        "science", "space", "ocean", "human body", "animals", "history",
        "technology", "climate", "food", "economy", "population", "energy",
    ]
    cat = random.choice(categories)

    prompt = (
        f"Generate ONE amazing, mind-blowing fact about '{cat}' that includes a specific number/statistic.\n\n"
        "Rules:\n"
        "- The stat must be a specific number, percentage, or measurement (like '73%', '8.6 billion', '299,792 km/s')\n"
        "- The fact must be true and verifiable\n"
        "- Text should be short and impactful (max 15 words per language)\n"
        "- uz: ONLY Latin Uzbek script. NO Cyrillic. Example: 'Yer yuzidagi barcha suv okeanlarida'\n"
        "- ru: Russian Cyrillic\n"
        "- en: English\n\n"
        "Return ONLY valid JSON (no markdown):\n"
        '{"stat":"73%","uz":"Yer yuzidagi barcha suv okeanlarida — bizga atigi 1% ichimlik suv qolgan.",'
        '"ru":"73% воды Земли — в океанах. Питьевой воды осталось лишь 1%.",'
        '"en":"73% of Earth\'s water is in oceans. Only 1% is drinkable fresh water.",'
        '"source":"UN Water 2026",'
        '"ht_uz":"#HayratNigoh #Dunyo #Fakt #1KUN",'
        '"ht_ru":"#HayratNigoh #Мир #Факт #1День",'
        '"ht_en":"#WorldCuriosity #Fact #Amazing #1Day"}'
    )

    fallbacks = [
        {"stat": "99.9%", "uz": "Kosmosda tovush tarqalmaydi — chunki vakuumda molekulalar yo'q.",
         "ru": "В космосе нет звука — там нет молекул для передачи волн.",
         "en": "In space, no one can hear you scream — there are no molecules to carry sound.",
         "source": "NASA 2026",
         "ht_uz": "#HayratNigoh #Kosmoos #Fakt #1KUN",
         "ht_ru": "#HayratNigoh #Космос #Факт #1День",
         "ht_en": "#WorldCuriosity #Space #Fact #1Day"},
        {"stat": "8 min", "uz": "Quyosh nuri yerga 8 daqiqada yetib keladi — uni ko'rsak kechikkan bo'lamiz.",
         "ru": "Солнечный свет достигает Земли за 8 минут — мы всегда видим прошлое.",
         "en": "Sunlight takes 8 minutes to reach Earth — you're always seeing the past.",
         "source": "NASA / ESA 2026",
         "ht_uz": "#HayratNigoh #Quyosh #Fakt #1KUN",
         "ht_ru": "#HayratNigoh #Солнце #Факт #1День",
         "ht_en": "#WorldCuriosity #Sun #Science #1Day"},
        {"stat": "37B", "uz": "Insonning tanasida 37 milliard hujayra bor — kosmosda yulduzdan ko'p.",
         "ru": "В теле человека 37 миллиардов клеток — больше, чем звёзд в Млечном Пути.",
         "en": "The human body has 37 billion cells — more than stars in the Milky Way.",
         "source": "Cell Biology Research 2026",
         "ht_uz": "#HayratNigoh #Inson #Fakt #1KUN",
         "ht_ru": "#HayratNigoh #Человек #Факт #1День",
         "ht_en": "#WorldCuriosity #HumanBody #Science #1Day"},
    ]

    raw = ""
    try:
        if ANTHROPIC_API_KEY:
            raw = _ask_anthropic(prompt, max_tokens=400)
    except Exception as e:
        log.warning(f"Anthropic xato: {e}")
    if not raw:
        try:
            raw = groq_ask(prompt, max_tokens=400)
        except Exception:
            pass
    if raw:
        try:
            raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                data = json.loads(m.group())
                if data.get("stat") and data.get("en"):
                    return data
        except Exception as e:
            log.warning(f"Factoid JSON parse xato: {e}")

    return random.choice(fallbacks)


# ─────────────────────────────────────────────────────────────
# Pexels yordamchi funksiyalar
# ─────────────────────────────────────────────────────────────
def _pexels_curated(out_path: str) -> dict | None:
    """Pexels Curated — trending landshaft/hujjatli foto."""
    if not PEXELS_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/curated?per_page=30",
            headers={"Authorization": PEXELS_API_KEY}, timeout=15,
        )
        if r.status_code != 200:
            return None
        photos = r.json().get("photos", [])
        landscape = [p for p in photos if p.get("width", 0) > p.get("height", 1)]
        candidates = landscape or photos
        random.shuffle(candidates)
        for ph in candidates:
            src = ph.get("src", {})
            img_url = src.get("large2x") or src.get("large") or src.get("original", "")
            if not img_url:
                continue
            ir = requests.get(img_url, timeout=20)
            if ir.status_code == 200 and len(ir.content) >= 30_000:
                with open(out_path, "wb") as f:
                    f.write(ir.content)
                return {
                    "photographer": ph.get("photographer", ""),
                    "alt":          ph.get("alt", ""),
                    "url":          ph.get("url", ""),
                }
    except Exception as e:
        log.warning(f"Pexels curated xato: {e}")
    return None


def _pexels_search(query: str, out_path: str, orientation: str = "landscape") -> dict | None:
    """Pexels qidiruvi."""
    if not PEXELS_API_KEY:
        return None
    try:
        r = requests.get(
            f"https://api.pexels.com/v1/search",
            params={"query": query, "per_page": 20, "orientation": orientation},
            headers={"Authorization": PEXELS_API_KEY}, timeout=15,
        )
        if r.status_code != 200:
            return None
        photos = r.json().get("photos", [])
        random.shuffle(photos)
        for ph in photos:
            src = ph.get("src", {})
            img_url = src.get("large2x") or src.get("large", "")
            if not img_url:
                continue
            ir = requests.get(img_url, timeout=20)
            if ir.status_code == 200 and len(ir.content) >= 30_000:
                with open(out_path, "wb") as f:
                    f.write(ir.content)
                return {
                    "photographer": ph.get("photographer", ""),
                    "alt":          ph.get("alt", ""),
                    "url":          ph.get("url", ""),
                }
    except Exception as e:
        log.warning(f"Pexels search '{query}' xato: {e}")
    return None


# ─────────────────────────────────────────────────────────────
# Bugungi eng muhim yangilik mavzusini aniqlash
# ─────────────────────────────────────────────────────────────
def _get_todays_top_news_topic() -> str:
    """Bugungi eng muhim yangilik mavzusini aniqlash (RSS yoki TELEGRAM queue dan)."""
    try:
        # TELEGRAM queue dan bugungi so'nggi yangilikni olish
        import pathlib as _pl, glob as _gl, json as _js
        queue_dir = _pl.Path(__file__).parent.parent / "TELEGRAM" / "queue"
        today = datetime.now().strftime("%Y%m%d")
        today_files = sorted([
            f for f in _gl.glob(str(queue_dir / "done" / "*.json"))
            if _pl.Path(f).name.startswith(today)
        ], reverse=True)
        if not today_files:
            # queue/ da ham tekshir
            today_files = sorted([
                f for f in _gl.glob(str(queue_dir / "*.json"))
                if _pl.Path(f).name.startswith(today)
            ], reverse=True)
        if today_files:
            with open(today_files[0], encoding="utf-8") as f:
                data = _js.load(f)
            article = data.get("article", {})
            title = article.get("title", "")
            if title:
                log.info(f"  📰 Bugungi top yangilik: {title[:60]}")
                return title
    except Exception as e:
        log.warning(f"  Queue topilmadi: {e}")

    # Fallback — RSS dan
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        from rss import fetch_rss_news
        articles = fetch_rss_news(count=3)
        if articles:
            return articles[0].get("title", "world news today")
    except Exception:
        pass
    return "world news today"


# ─────────────────────────────────────────────────────────────
# AI bilan caption yaratish
# ─────────────────────────────────────────────────────────────
def _gen_hayrat_caption(alt: str, photographer: str) -> dict:
    """DUNYOGA HAYRAT NIGOHI uchun she'riy tavsif (lotin UZ, kirill RU, EN)."""
    if not alt or len(alt.strip()) < 10:
        return {
            "uz": "Dunyoning har bir burchagida go'zallik mavjud. Bu lahza — shunday bir on.",
            "ru": "В каждом уголке мира есть своя красота. Этот момент — тому подтверждение.",
            "en": "Beauty exists in every corner of the world. This moment captures it all.",
            "ht_uz": "#DunyogaHayratNigohi #Foto #Dunyo #1KUN",
            "ht_ru": "#ВзглядНаМир #Фото #Мир #1День",
            "ht_en": "#WindowToTheWorld #Photo #World #1Day",
        }

    prompt = (
        f'Photo description: "{alt}"\n'
        f'Photographer: {photographer}\n\n'
        "Write a short poetic caption (2-3 sentences) in 3 languages.\n"
        "IMPORTANT RULES:\n"
        "- uz: ONLY Latin Uzbek (lotin o'zbek). NO Cyrillic. Example: 'Dunyo go'zalligini ko'ring.'\n"
        "- ru: Russian Cyrillic only\n"
        "- en: English\n"
        "Return ONLY valid JSON:\n"
        '{"uz":"...","ru":"...","en":"...","ht_uz":"#DunyogaHayratNigohi #Foto #1KUN",'
        '"ht_ru":"#ВзглядНаМир #Фото #1День","ht_en":"#WindowToTheWorld #Photo #1Day"}'
    )
    return _ai_json(prompt, {
        "uz": "Dunyoning go'zal bir lahzasi.",
        "ru": "Прекрасный момент нашего мира.",
        "en": "A beautiful moment from our world.",
        "ht_uz": "#DunyogaHayratNigohi #Foto #1KUN",
        "ht_ru": "#ВзглядНаМир #Фото #1День",
        "ht_en": "#WindowToTheWorld #Photo #1Day",
    })


def _gen_kun_fotosi_caption(news_topic: str, alt: str, photographer: str) -> dict:
    """KUN FOTOSI uchun tavsif — bugungi eng muhim voqea kontekstida."""
    prompt = (
        f'Today\'s top news: "{news_topic}"\n'
        f'Photo description: "{alt}"\n'
        f'Photographer: {photographer}\n\n'
        "Write a compelling news photo caption (2-3 sentences) connecting the photo to today's event.\n"
        "IMPORTANT RULES:\n"
        "- uz: ONLY Latin Uzbek (lotin). NO Cyrillic at all. "
        "Example: 'Bugun dunyoda sodir bo`lgan voqea...'\n"
        "- ru: Russian Cyrillic only\n"
        "- en: English\n"
        "Return ONLY valid JSON:\n"
        '{"uz":"...","ru":"...","en":"...","ht_uz":"#KunFotosi #Yangilik #1KUN",'
        '"ht_ru":"#ФотоДня #Новости #1День","ht_en":"#PhotoOfTheDay #News #1Day"}'
    )
    return _ai_json(prompt, {
        "uz": f"Bugungi kun eng muhim voqeasi. {news_topic[:50]}",
        "ru": f"Главное событие дня. {news_topic[:50]}",
        "en": f"Photo of the day. {news_topic[:50]}",
        "ht_uz": "#KunFotosi #Yangilik #1KUN",
        "ht_ru": "#ФотоДня #Новости #1День",
        "ht_en": "#PhotoOfTheDay #News #1Day",
    })


def _ai_json(prompt: str, fallback: dict) -> dict:
    """AI dan JSON javob olish (Anthropic → Groq)."""
    raw = ""
    try:
        if ANTHROPIC_API_KEY:
            raw = _ask_anthropic(prompt, max_tokens=500)
    except Exception as e:
        log.warning(f"Anthropic xato: {e}")
    if not raw:
        try:
            raw = groq_ask(prompt, max_tokens=500)
        except Exception:
            pass
    if raw:
        try:
            raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
            # JSON qismini ajratib olish
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception as e:
            log.warning(f"JSON parse xato: {e} | raw: {raw[:100]}")
    return fallback


# ─────────────────────────────────────────────────────────────
# Telegram yuborish
# ─────────────────────────────────────────────────────────────
def _send_photo(photo_path: str, caption: str, channel: str) -> bool:
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    try:
        with open(photo_path, "rb") as f:
            r = requests.post(
                f"{base}/sendPhoto",
                data={"chat_id": channel, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"photo": f},
                timeout=30,
            )
        ok = r.json().get("ok", False)
        if not ok:
            log.warning(f"sendPhoto xato ({channel}): {r.json().get('description','')}")
        return ok
    except Exception as e:
        log.warning(f"sendPhoto exception ({channel}): {e}")
        return False


def _build_caption(title: str, cap_text: str, hashtags: str,
                   photographer: str, channel: str, lang: str) -> str:
    """Telegram caption yasash."""
    vaqt = datetime.now(TASHKENT).strftime("🕐 %H:%M | %d.%m.%Y")
    yt_labels = {"uz": "🎬 1Kun", "ru": "🎬 1День", "en": "🎬 1Day"}
    cross = {
        "uz": [f"🇷🇺 {TELEGRAM_CHANNEL_RU}", f"🇬🇧 {TELEGRAM_CHANNEL_EN}"],
        "ru": [f"🇺🇿 {TELEGRAM_CHANNEL_UZ}", f"🇬🇧 {TELEGRAM_CHANNEL_EN}"],
        "en": [f"🇺🇿 {TELEGRAM_CHANNEL_UZ}", f"🇷🇺 {TELEGRAM_CHANNEL_RU}"],
    }
    credit_label = {"uz": "📷 Fotograf", "ru": "📷 Фотограф", "en": "📷 Photo by"}.get(lang, "📷")

    text  = f"<b>{title}</b>\n\n"
    text += f"{cap_text}\n\n"
    if photographer:
        text += f"{credit_label}: {photographer}\n"
    text += f"{vaqt}\n"
    text += f"📰 {channel}\n"
    text += f'{yt_labels.get(lang,"🎬")} | Global News: <a href="https://www.youtube.com/@1kunnews">youtube.com/@1kunnews</a>\n'
    for ch in cross.get(lang, []):
        text += f"{ch}\n"
    text += f"\n{hashtags}"
    return text


# ─────────────────────────────────────────────────────────────
# Lock yordamchisi
# ─────────────────────────────────────────────────────────────
def _check_lock(lock_name: str, force: bool = False) -> tuple[bool, pathlib.Path]:
    """Lock faylni tekshirish. (ok, lock_path) qaytaradi."""
    out_dir   = pathlib.Path(__file__).parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    lock_file = out_dir / lock_name
    today_str = datetime.now(TASHKENT).strftime("%Y-%m-%d")
    if not force and lock_file.exists():
        try:
            if lock_file.read_text(encoding="utf-8").strip() == today_str:
                return False, lock_file
        except Exception:
            pass
    return True, lock_file


def _write_lock(lock_path: pathlib.Path):
    today_str = datetime.now(TASHKENT).strftime("%Y-%m-%d")
    try:
        lock_path.write_text(today_str, encoding="utf-8")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 1. HAYRAT / NIGOH — soat 12:00 (yangi infografik format)
# ─────────────────────────────────────────────────────────────
def run_hayrat_nigoh(force: bool = False):
    """
    Soat 12:00 — dunyo haqida hayratlanarli fakt.
    Yangi dizayn (Image 2): to'liq qizil fon, ulkan stat raqam, oq matn.
    AI fakt generatsiya → infografik karta → 3 kanalga yuborish.
    """
    ok, lock_path = _check_lock("hayrat_nigoh_lock.txt", force)
    if not ok:
        log.info("🔴 Hayrat Nigohi: bugun allaqachon yuborilgan — o'tkazildi")
        return

    log.info("🔴 HAYRAT / NIGOH — boshlanmoqda...")

    # ── AI dan fakt olish ─────────────────────────────────────
    factoid = _gen_hayrat_factoid()
    stat    = factoid.get("stat", "?")
    source  = factoid.get("source", "")
    log.info(f"  Fakt: {stat} | {factoid.get('en','')[:60]}")

    # ── Har til uchun karta yasash va yuborish ────────────────
    tmp = pathlib.Path(tempfile.gettempdir())
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")

    channels = [
        ("uz", TELEGRAM_CHANNEL_UZ),
        ("ru", TELEGRAM_CHANNEL_RU),
        ("en", TELEGRAM_CHANNEL_EN),
    ]
    vaqt = datetime.now(TASHKENT).strftime("🕐 %H:%M | %d.%m.%Y")

    for lang, channel in channels:
        card_path = str(tmp / f"hayrat_{ts}_{lang}.jpg")
        text      = factoid.get(lang, factoid.get("en", ""))
        hashtags  = factoid.get(f"ht_{lang}", "#HayratNigoh #Dunyo #1KUN")

        # Karta yasash
        card_ok = _make_hayrat_card(stat, text, source, lang, card_path)

        # Caption (karta tavsifi Telegram uchun)
        caption  = f"<b>{_HAYRAT_TITLE[lang]}</b>\n\n"
        caption += f"<b>{stat}</b>  —  {text}\n\n"
        if source:
            src_lbl = {"uz": "Manba", "ru": "Источник", "en": "Source"}.get(lang, "Source")
            caption += f"📊 {src_lbl}: {source}\n"
        caption += f"{vaqt}\n"
        caption += f"📰 {channel}\n"
        caption += f"\n{hashtags}"

        if card_ok and os.path.exists(card_path):
            sent = _send_photo(card_path, caption, channel)
            try: os.remove(card_path)
            except Exception: pass
        else:
            # Fallback — faqat matn
            from telegram_bot import send_telegram as _st
            sent = _st(caption, channel)

        if sent:
            log.info(f"  ✅ Hayrat Nigohi [{lang.upper()}] → {channel}")
        else:
            log.warning(f"  ⚠️  Hayrat Nigohi [{lang.upper()}] muvaffaqiyatsiz")

    _write_lock(lock_path)
    log.info("🔴 HAYRAT / NIGOH — tugadi")


# ─────────────────────────────────────────────────────────────
# 2. KUN FOTOSI — soat 20:00
# ─────────────────────────────────────────────────────────────
def run_kun_fotosi(force: bool = False):
    """Soat 20:00 — bugungi eng muhim voqeaning chiroyli fotosi."""
    ok, lock_path = _check_lock("kun_fotosi_lock.txt", force)
    if not ok:
        log.info("📸 Kun Fotosi: bugun allaqachon yuborilgan — o'tkazildi")
        return

    log.info("📸 KUN FOTOSI — boshlanmoqda...")
    tmp      = pathlib.Path(tempfile.gettempdir())
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    img_path = str(tmp / f"kunfoto_{ts}.jpg")

    # Bugungi top yangilik mavzusini aniqlash
    news_topic = _get_todays_top_news_topic()
    log.info(f"  Mavzu: {news_topic[:60]}")

    # Mavzu bo'yicha eng chiroyli foto qidirish
    # Avval yangilik mavzusida, so'ng umumiy "photo of the day" da
    meta = _pexels_search(news_topic[:50], img_path)
    if not meta:
        # Yangilik kalit so'zlari bilan
        words = [w for w in news_topic.split() if len(w) > 4][:3]
        if words:
            meta = _pexels_search(" ".join(words), img_path)
    if not meta:
        meta = _pexels_search("breaking news world today", img_path)
    if not meta:
        meta = _pexels_curated(img_path)

    if not meta:
        log.warning("📸 Kun Fotosi: foto topilmadi")
        return

    log.info(f"  Foto: {meta.get('alt','')[:50]} (by {meta.get('photographer','')})")
    captions = _gen_kun_fotosi_caption(
        news_topic, meta.get("alt", ""), meta.get("photographer", "")
    )

    channels = [
        ("uz", TELEGRAM_CHANNEL_UZ),
        ("ru", TELEGRAM_CHANNEL_RU),
        ("en", TELEGRAM_CHANNEL_EN),
    ]
    for lang, channel in channels:
        title    = _KUN_FOTOSI_TITLE[lang]
        cap_text = captions.get(lang, "")
        hashtags = captions.get(f"ht_{lang}", "#Photo #News")
        caption  = _build_caption(title, cap_text, hashtags,
                                  meta.get("photographer", ""), channel, lang)
        if _send_photo(img_path, caption, channel):
            log.info(f"  ✅ Kun Fotosi [{lang.upper()}] → {channel}")
        else:
            log.warning(f"  ⚠️  Kun Fotosi [{lang.upper()}] muvaffaqiyatsiz")

    _write_lock(lock_path)
    try:
        os.remove(img_path)
    except Exception:
        pass
    log.info("📸 KUN FOTOSI — tugadi")


# ─────────────────────────────────────────────────────────────
# Eski nom bilan muvofiqlashish (app.py da ishlatiladi)
# ─────────────────────────────────────────────────────────────
def run_photo_of_day(force: bool = False):
    """Orqaga muvofiqlashish — run_hayrat_nigoh ga yo'naltiradi."""
    run_hayrat_nigoh(force=force)


if __name__ == "__main__":
    if "--kun" in sys.argv:
        run_kun_fotosi(force=True)
    elif "--hayrat" in sys.argv:
        run_hayrat_nigoh(force=True)
    else:
        run_hayrat_nigoh(force=True)
