"""
digest_maker.py — Yangiliklar digest video generatori (yangi format)

Format (bir videoda 4-6 ta yangilik):
  [OCHILISH 3s] → [Yangilik-1 sarlavha 4s] → [Yangilik-1 foto 10s] →
  [Yangilik-2 sarlavha 4s] → [Yangilik-2 foto 10s] → ... → [YAKUNLASH 3s]

Dizayn:
  ✓ Katta, yorqin sarlavhalar
  ✓ Geo-marker (qizil nuqta + joy nomi)
  ✓ Statistika overlay (raqamlar ekranda katta)
  ✓ Pastki ticker "KEYINGI: ..."
  ✓ Tezkor fon musiqasi
  ✓ Xarita uslubidagi joy grafika
  ✓ Kanal brendi har doim
"""

import os, sys, re, json, glob, hashlib, shutil, textwrap, math
import subprocess, requests, asyncio, random, logging
from datetime import date, datetime

log = logging.getLogger(__name__)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(".env")

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import edge_tts

from config import OUTPUT_DIR, TEMP_DIR, VOICES, AUDIO_FX, YOUTUBE_PLAYLIST

# ─────────────────────────────────────────────────────────────
# Konstantlar
# ─────────────────────────────────────────────────────────────
VW, VH        = 1280, 720
FPS           = 25
OPEN_DUR      = 4       # Ochilish kartasi
TITLE_DUR     = 4       # Har bir yangilik sarlavha kartasi
PHOTO_DUR     = 90      # Har bir yangilik foto — TTS bilan ~1-2 daqiqa
OUTRO_DUR     = 8       # Yakunlash kartasi (musiqa outro uchun kengaytirilgan)
TRANS_DUR     = 0.5     # Crossfade
MAX_ITEMS     = 6       # Bir videoda maksimal yangilik soni
MIN_ITEMS     = 1       # Minimum yangilik
WORDS_PER_STORY = 220   # Har bir yangilik naratsiya so'zlari (~90s speech)
MUSIC_VOL     = 0.28    # Fon musiqasi balandligi (eshitilarli bo'lsin)

_HERE = os.path.dirname(os.path.abspath(__file__))

# 1DAY GLOBAL brand colors: qora / oq / qizil
C_BG      = (0,   0,   0)   # Pure black
C_DARK    = (12,  12,  12)
C_NAVY    = (18,  18,  18)
C_RED     = (204,  0,   0)   # Brand red
C_WHITE   = (255, 255, 255)
C_LGRAY   = (150, 150, 150)
C_ACCENT  = (204,  0,   0)   # Red (ko'k o'rniga)
C_GREEN   = (200, 200, 200)  # Oq-kulrang
C_TICKER  = (15,  15,  15)   # Ticker foni (deyarli qora)
C_GOLD    = (255, 255, 255)  # Oltin → oq (legacy alias)


# ─────────────────────────────────────────────────────────────
# Shrift
# ─────────────────────────────────────────────────────────────
def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    cands = (
        ["C:\\Windows\\Fonts\\arialbd.ttf",
         "C:\\Windows\\Fonts\\calibrib.ttf",
         "C:\\Windows\\Fonts\\verdanab.ttf"]
        if bold else
        ["C:\\Windows\\Fonts\\arial.ttf",
         "C:\\Windows\\Fonts\\calibri.ttf",
         "C:\\Windows\\Fonts\\verdana.ttf"]
    )
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ─────────────────────────────────────────────────────────────
# Yordamchi: matn chizish (soya bilan)
# ─────────────────────────────────────────────────────────────
def _text_shadow(draw, xy, text, font, fill, shadow=(0, 0, 0), offset=2, anchor=None):
    x, y = xy
    kw = {"anchor": anchor} if anchor else {}
    draw.text((x + offset, y + offset), text, font=font, fill=(*shadow, 160), **kw)
    draw.text((x, y), text, font=font, fill=fill, **kw)


# ─────────────────────────────────────────────────────────────
# Yordamchi: gradient jadval
# ─────────────────────────────────────────────────────────────
def _gradient_rect(draw, x0, y0, x1, y1, color_top, color_bot, alpha=255):
    h = y1 - y0
    for dy in range(h):
        t = dy / max(h - 1, 1)
        r = int(color_top[0] * (1 - t) + color_bot[0] * t)
        g = int(color_top[1] * (1 - t) + color_bot[1] * t)
        b = int(color_top[2] * (1 - t) + color_bot[2] * t)
        draw.line([(x0, y0 + dy), (x1, y0 + dy)], fill=(r, g, b))


# ─────────────────────────────────────────────────────────────
# Yordamchi: raqamlar/statistika olish
# ─────────────────────────────────────────────────────────────
def _iget(item: dict, field: str, lang: str, fallback: str = "") -> str:
    """
    Maqola itemidan to'g'ri til qiymatini olish.
    Maydon dict bo'lsa → item[field][lang] ni qaytaradi.
    Maydon str bo'lsa → to'g'ridan qaytaradi.
    Alternativ maydon nomlarini ham qo'llab-quvvatlaydi.

    Qo'llab-quvvatlanadigan maydonlar:
      sarlavha / sarlavha_uz/ru/en
      scripts / script
      jumla    / jumla1
      location / location_str
    """
    # Alternativ nomlar xaritasi
    _ALIASES = {
        "script":  ["scripts", "script"],
        "jumla1":  ["jumla",   "jumla1"],
        "scripts": ["scripts", "script"],
        "jumla":   ["jumla",   "jumla1"],
    }
    candidates = _ALIASES.get(field, [field])

    for fname in candidates:
        val = item.get(fname, "")
        if not val:
            continue
        if isinstance(val, dict):
            # Dict: tilga qarab ol, fallback boshqa tillar
            result = (val.get(lang) or val.get("uz") or
                      val.get("ru") or val.get("en") or "")
            if result:
                return str(result).strip()
        elif isinstance(val, str) and val.strip():
            return val.strip()
    return fallback


def _extract_stats(text: str) -> list[dict]:
    """Matndan muhim raqamlarni ajratish (%, $, o'lim, nafar...)"""
    stats = []
    patterns = [
        # Foiz
        (r'(\d[\d,\.]*)\s*(%|процент|foiz|percent)',
         lambda m: {"val": m.group(1), "unit": "%", "icon": "📊"}),
        # Dollar/pul
        (r'\$\s*(\d[\d,\.]*)\s*(billion|million|трлн|млрд|mlrd|мln|mln)?',
         lambda m: {"val": "$" + m.group(1), "unit": (m.group(2) or "").strip(), "icon": "💰"}),
        # Qurbonlar
        (r'(\d[\d,\.]*)\s*(killed|dead|жертв|halok|qurbon|nafar)',
         lambda m: {"val": m.group(1), "unit": m.group(2), "icon": "🔴"}),
        # Yillar
        (r'\b(20\d\d)\b',
         lambda m: {"val": m.group(1), "unit": "", "icon": "📅"}),
        # Minglar/Milionlar
        (r'(\d[\d,\.]*)\s*(million|billion|млн|млрд|mln|ming)',
         lambda m: {"val": m.group(1), "unit": m.group(2), "icon": "📈"}),
    ]
    seen = set()
    for pat, extractor in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            key = m.group(0)[:20]
            if key in seen:
                continue
            seen.add(key)
            try:
                stat = extractor(m)
                stats.append(stat)
                if len(stats) >= 2:
                    break
            except Exception:
                pass
        if len(stats) >= 2:
            break
    return stats


_POLITICIAN_CONTEXT = {
    # Ism (kichik harf) → (qidiruv so'rovi, davlat/lavozim)
    "netanyahu":  ("Netanyahu Israel Prime Minister", "Netanyahu"),
    "sinwar":     ("Hamas Gaza leader", "Gaza Hamas"),
    "zelensky":   ("Zelensky Ukraine President", "Ukraine president"),
    "putin":      ("Putin Russia President Kremlin", "Putin Russia"),
    "trump":      ("Trump US President White House", "Trump"),
    "biden":      ("Biden US President", "Biden White House"),
    "xi jinping": ("Xi Jinping China President", "China president"),
    "xi":         ("Xi Jinping China President", "China Beijing"),
    "macron":     ("Macron France President Elysee", "Macron France"),
    "modi":       ("Modi India Prime Minister", "India Modi"),
    "erdogan":    ("Erdogan Turkey President", "Turkey Ankara"),
    "kim":        ("Kim Jong Un North Korea leader", "North Korea"),
    "sunak":      ("Sunak UK Prime Minister", "UK government"),
    "starmer":    ("Starmer UK Prime Minister", "UK government"),
    "scholz":     ("Scholz Germany Chancellor", "Germany Berlin"),
    "khamenei":   ("Khamenei Iran Supreme Leader", "Iran Tehran"),
    "nasrallah":  ("Nasrallah Hezbollah Lebanon", "Lebanon Hezbollah"),
    "musk":       ("Elon Musk CEO Tesla SpaceX", "Elon Musk"),
    "zelenski":   ("Zelensky Ukraine President", "Ukraine president"),
    "herzog":     ("Herzog Israel President", "Israel"),
    "gallant":    ("Gallant Israel Defense Minister", "Israel military"),
    "blinken":    ("Blinken US Secretary State", "US diplomacy"),
    "rubio":      ("Rubio US Secretary State", "US diplomacy"),
    "lavrov":     ("Lavrov Russia Foreign Minister", "Russia diplomacy"),
    "guterres":   ("Guterres UN Secretary General", "United Nations"),
}


# ── Joylar va obyektlar — kontekstli Pexels so'rovlari uchun ────
_LOCATION_CONTEXT = {
    # Ukraina/Rossiya
    "zaporizhzhia": "Zaporizhzhia nuclear power plant Ukraine",
    "zaporizhzhya": "Zaporizhzhia nuclear power plant Ukraine",
    "chernobyl":    "Chernobyl nuclear plant Ukraine",
    "kyiv":         "Kyiv Ukraine",
    "kiev":         "Kyiv Ukraine",
    "mariupol":     "Mariupol Ukraine destroyed city",
    "kharkiv":      "Kharkiv Ukraine",
    "donbas":       "Donbas Ukraine war",
    "donetsk":      "Donetsk Ukraine",
    "crimea":       "Crimea peninsula",
    "moscow":       "Moscow Russia Kremlin",
    "kremlin":      "Kremlin Moscow",
    "st. petersburg": "Saint Petersburg Russia",
    # Yaqin Sharq
    "gaza":         "Gaza Palestine destruction",
    "rafah":        "Rafah Gaza",
    "tel aviv":     "Tel Aviv Israel",
    "jerusalem":    "Jerusalem Israel",
    "beirut":       "Beirut Lebanon",
    "tehran":       "Tehran Iran",
    "damascus":     "Damascus Syria",
    "aleppo":       "Aleppo Syria",
    "baghdad":      "Baghdad Iraq",
    "kabul":        "Kabul Afghanistan",
    # Yevropa
    "london":       "London UK",
    "paris":        "Paris France",
    "berlin":       "Berlin Germany",
    "rome":         "Rome Italy",
    "madrid":       "Madrid Spain",
    "brussels":     "Brussels Belgium EU",
    # Boshqalar
    "washington":   "Washington DC United States",
    "white house":  "White House Washington",
    "pentagon":     "Pentagon US military",
    "beijing":      "Beijing China",
    "pyongyang":    "Pyongyang North Korea",
    "ankara":       "Ankara Turkey",
    "istanbul":     "Istanbul Turkey",
    "mali":         "Mali West Africa military",
    "sahel":        "Sahel Africa desert",
    "yemen":        "Yemen Sanaa",
    "bamako":       "Bamako Mali",
}

_OBJECT_CONTEXT = {
    # Hujum/qurol
    "nuclear plant":   "nuclear power station reactor",
    "nuclear power":   "nuclear power station reactor",
    "nuclear":         "nuclear power station",
    "drone strike":    "military drone Ukraine",
    "drone":           "military drone aircraft",
    "missile":         "missile launch military",
    "rocket":          "rocket missile attack",
    "tank":            "military tank battle",
    "warship":         "navy warship military",
    "fighter jet":     "fighter jet military aircraft",
    "airstrike":       "airstrike bombing aftermath",
    "explosion":       "explosion smoke aftermath",
    "blast":           "explosion blast smoke",
    "shelling":        "artillery shelling war",
    "ceasefire":       "ceasefire peace negotiation",
    # Ofat/yong'in
    "wildfire":        "wildfire forest fire",
    "earthquake":      "earthquake destruction",
    "flood":           "flood disaster",
    "tornado":         "tornado storm",
    "hurricane":       "hurricane storm aftermath",
    # Diplomatiya
    "summit":          "international summit meeting",
    "talks":           "diplomatic talks meeting",
    "negotiations":    "diplomatic negotiations table",
    "elections":       "voting ballot election",
    "protests":        "street protest demonstration",
    "rally":           "political rally crowd",
    # Iqtisod
    "oil":             "oil refinery industry",
    "gas pipeline":    "natural gas pipeline",
    "stock market":    "stock market trading",
    # Boshqa
    "hospital":        "hospital emergency",
    "refugee":         "refugees crisis migration",
    "earthquake":      "earthquake destruction city",
}


def _extract_person_queries(en_title: str) -> list:
    """
    Sarlavhadan shaxs/joy/obyekt nomlarini ajratish va kontekstli Pexels so'rovlari yasash.
    Tartib: 1) Joy + obyekt birga (eng aniq) → 2) Siyosatchi → 3) Joy → 4) Obyekt → 5) Proper nouns.
    """
    title_lower = en_title.lower()
    result = []
    seen_q = set()

    def _add(q: str):
        ql = q.lower().strip()
        if ql and ql not in seen_q:
            seen_q.add(ql)
            result.append(q)

    # 1. Joy + obyekt kombinatsiyasi (masalan, "Zaporizhzhia nuclear plant drone")
    found_loc = None
    for loc_key, loc_q in _LOCATION_CONTEXT.items():
        if loc_key in title_lower:
            found_loc = (loc_key, loc_q)
            break

    found_obj = None
    # Obyektlardan eng spesifikni topish (uzunroq matn — aniqroq)
    for obj_key in sorted(_OBJECT_CONTEXT.keys(), key=len, reverse=True):
        if obj_key in title_lower:
            found_obj = (obj_key, _OBJECT_CONTEXT[obj_key])
            break

    if found_loc and found_obj:
        # Birlashma so'rov — eng aniq tasvir uchun
        _add(f"{found_loc[1]} {found_obj[0]}")

    # 2. Taniqli siyosatchilar
    for name, (contextual_q, fallback_q) in _POLITICIAN_CONTEXT.items():
        if name in title_lower:
            _add(contextual_q)
            _add(fallback_q)
            if len(result) >= 3:
                break

    # 3. Faqat joy (siyosatchi yo'q bo'lsa)
    if found_loc:
        _add(found_loc[1])

    # 4. Faqat obyekt
    if found_obj:
        _add(found_obj[1])

    # 5. Proper nouns (zaxira)
    if not result:
        _COMMON_CAPS = {
            "The","A","An","In","On","At","To","For","Of","And",
            "Or","But","As","By","Is","Are","Was","Has","Says",
            "New","Over","After","Before","US","EU","UN","UK",
        }
        phrases = re.findall(r'\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*)\b', en_title)
        for ph in phrases:
            if ph not in _COMMON_CAPS:
                _add(ph)

    return result[:4]   # Eng ko'pi 4 ta so'rov


def _make_infographic_card(
        sarlavha: str, stats: list, names: list, lang: str, out_path: str) -> str:
    """
    Stats va muhim nomlar bilan infografik karta.
    Sof matnli, qoramtir fon — videoniyalik uchun.
    """
    img  = Image.new("RGB", (VW, VH), (4, 8, 22))
    _gradient_rect(ImageDraw.Draw(img), 0, 0, VW, VH, (6, 12, 35), (2, 5, 18))
    img  = _draw_map_bg(img, alpha=20)
    draw = ImageDraw.Draw(img)

    # Aksent chiziq
    draw.rectangle([(0, 0), (8, VH)], fill=C_RED)
    draw.rectangle([(0, 0), (VW, 5)], fill=C_RED)

    # Yuqori: kanal nomi
    draw.text((VW // 2, 35), "1DAY GLOBAL", font=_font(26), fill=C_WHITE, anchor="mm")
    draw.rectangle([(0, 60), (VW, 63)], fill=C_RED)

    # Sarlavha qisqa (2 qator)
    if sarlavha:
        wrapped = textwrap.wrap(sarlavha, width=48)[:2]
        ty = 90
        for i, line in enumerate(wrapped):
            fs = 36 if i == 0 else 30
            _text_shadow(draw, (VW // 2, ty), line, font=_font(fs),
                         fill=C_WHITE, offset=2, anchor="mm")
            ty += fs + 8

    # Stats bloklari — markazda katta raqamlar
    if stats:
        sx = 120
        sy = 200
        for stat in stats[:3]:
            val  = stat.get("val", "")
            unit = stat.get("unit", "")
            if not val:
                continue
            # Katta raqam
            draw.rectangle([(sx - 10, sy - 10),
                             (sx + 240, sy + 100)],
                            fill=(10, 20, 55))
            draw.rectangle([(sx - 10, sy - 10),
                             (sx + 240, sy - 7)],
                            fill=C_GOLD)
            draw.text((sx + 115, sy + 40), val,
                      font=_font(52), fill=C_GOLD, anchor="mm")
            if unit:
                draw.text((sx + 115, sy + 82), unit.upper()[:12],
                          font=_font(20, bold=False), fill=C_LGRAY, anchor="mm")
            sx += 280
            if sx + 240 > VW - 20:
                sx = 120; sy += 130

    # Ismlar / kalit so'zlar (pastda)
    if names:
        ny = VH - 100
        name_str = "  ·  ".join(n[:25] for n in names[:5])
        draw.rectangle([(0, ny - 10), (VW, VH - 50)],
                       fill=(8, 16, 40))
        draw.text((VW // 2, ny + 20), name_str,
                  font=_font(22), fill=C_LGRAY, anchor="mm")

    # Pastki chiziq
    draw.rectangle([(0, VH - 5), (VW, VH)], fill=C_GOLD)

    img.save(out_path, "JPEG", quality=93)
    return out_path


# ─────────────────────────────────────────────────────────────
# Yordamchi: xarita uslubidagi fon
# ─────────────────────────────────────────────────────────────
def _draw_map_bg(img: Image.Image, alpha: int = 40):
    """
    Xarita uslubi: ko'k-qoramtir fon ustiga subtle grid + meridianlar.
    Haqiqiy xarita emas — grafik effekt.
    """
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # Grid chiziqlar (meridian/parallel uslub)
    line_col = (*C_NAVY, alpha)
    for x in range(0, VW, 80):
        d.line([(x, 0), (x, VH)], fill=line_col, width=1)
    for y in range(0, VH, 60):
        d.line([(0, y), (VW, y)], fill=line_col, width=1)

    # Diagonal aksentlar (xarita proyeksiya uslubi)
    accent_col = (*C_NAVY, alpha // 2)
    for x in range(-VH, VW + VH, 120):
        d.line([(x, 0), (x + VH, VH)], fill=accent_col, width=1)

    img_rgba = img.convert("RGBA")
    img_rgba.alpha_composite(overlay)
    return img_rgba.convert("RGB")


# ─────────────────────────────────────────────────────────────
# Yordamchi: geo-marker (pulsing red dot + joy nomi)
# ─────────────────────────────────────────────────────────────
def _draw_geo_marker(draw, x, y, location, lang="uz"):
    """Qizil nuqta + joy nomi + pulsing halqa effekti."""
    # Tashqi halqa (pulse)
    for r_off, a in [(22, 40), (14, 90), (8, 180)]:
        col = (*C_RED, a)
        draw.ellipse([(x - r_off, y - r_off), (x + r_off, y + r_off)],
                     outline=col, width=2)
    # Ichki qizil nuqta
    draw.ellipse([(x - 7, y - 7), (x + 7, y + 7)], fill=C_RED)
    draw.ellipse([(x - 3, y - 3), (x + 3, y + 3)], fill=C_WHITE)

    # Joy nomi
    if location:
        label = f"  {location.upper()}"
        draw.rectangle([(x + 12, y - 13), (x + 14 + len(label) * 9, y + 14)],
                        fill=(*C_RED, 230))
        draw.text((x + 16, y), label,
                  font=_font(18), fill=C_WHITE, anchor="lm")


# ─────────────────────────────────────────────────────────────
# Yordamchi: pastki ticker chizish
# ─────────────────────────────────────────────────────────────
def _draw_bottom_ticker(draw, next_title: str, lang: str, story_num: int, total: int):
    """Ekran pastida 'KEYINGI:' ticker."""
    ticker_h = 36
    y0 = VH - ticker_h

    # Ticker foni
    draw.rectangle([(0, y0), (VW, VH)], fill=C_TICKER)

    # Chap: qizil badge
    badge_text = {
        "uz": f" {story_num}/{total} ",
        "ru": f" {story_num}/{total} ",
        "en": f" {story_num}/{total} ",
    }.get(lang, f" {story_num}/{total} ")
    bw = len(badge_text) * 10 + 6
    draw.rectangle([(0, y0), (bw, VH)], fill=C_RED)
    draw.text((bw // 2, y0 + ticker_h // 2), badge_text,
              font=_font(18), fill=C_WHITE, anchor="mm")

    # "KEYINGI:" label
    next_label = {"uz": "KEYINGI:", "ru": "ДАЛЕЕ:", "en": "NEXT:"}.get(lang, "NEXT:")
    draw.text((bw + 10, y0 + ticker_h // 2), next_label,
              font=_font(17), fill=C_GOLD, anchor="lm")

    # Keyingi sarlavha
    if next_title:
        short = next_title[:65] + ("…" if len(next_title) > 65 else "")
        draw.text((bw + 90, y0 + ticker_h // 2), short,
                  font=_font(17, bold=False), fill=C_WHITE, anchor="lm")


# ─────────────────────────────────────────────────────────────
# KARTA 1: Ochilish kartasi (channel intro)
# ─────────────────────────────────────────────────────────────
def _make_open_card(lang: str, story_count: int, out_path: str,
                    stories: list = None):
    """
    1DAY GLOBAL digest overview (16:9) — Image 5 uslubi:
      · Qora top bar: '1DAY GLOBAL · DIGEST' + qizil nuqta + sana
      · Chap panel (38%): krem fon, 'THE WORLD TODAY' katta, duration
      · O'ng panel (62%): qora fon, 01–05 numbered story list oq matn
    stories: [{"sarlavha": str}, ...] — ixtiyoriy sarlavhalar
    """
    img  = Image.new("RGB", (VW, VH), (245, 240, 232))   # krem fon
    draw = ImageDraw.Draw(img)

    # ── TOP BAR (qora, 46px) ──────────────────────────────────
    top_h = 46
    draw.rectangle([(0, 0), (VW, top_h)], fill=(8, 8, 8))
    draw.text((18, top_h // 2), "1DAY GLOBAL  ·  DIGEST",
              font=_font(18, False), fill=(220, 215, 205), anchor="lm")
    today_s = date.today().strftime("%d %b %Y").upper()
    dot_x   = VW - 16 - len(today_s) * 10 - 22
    draw.ellipse([(dot_x, top_h // 2 - 5), (dot_x + 10, top_h // 2 + 5)],
                 fill=C_RED)
    draw.text((VW - 16, top_h // 2), today_s,
              font=_font(16, False), fill=(180, 175, 165), anchor="rm")

    # ── CHAP/O'NG PANEL AJRATISH ──────────────────────────────
    lw = int(VW * 0.38)   # chap panel kengligi (~486px)
    draw.rectangle([(lw, top_h), (VW, VH)], fill=(14, 14, 14))
    draw.rectangle([(lw, top_h), (lw + 1, VH)], fill=(40, 40, 40))

    # ── CHAP PANEL ────────────────────────────────────────────
    count_lbl = {
        "uz": f"TOP {story_count} YANGILIK",
        "ru": f"ТОП {story_count} НОВОСТЕЙ",
        "en": f"TOP {story_count} STORIES",
    }.get(lang, f"TOP {story_count} STORIES")
    draw.text((24, top_h + 26), count_lbl,
              font=_font(16), fill=C_RED, anchor="lm")

    hl_map = {
        "uz": ["THE WORLD", "TODAY"],
        "ru": ["МИР", "СЕГОДНЯ"],
        "en": ["THE WORLD", "TODAY"],
    }
    ty = top_h + 62
    for line in hl_map.get(lang, ["THE WORLD", "TODAY"]):
        draw.text((22, ty), line, font=_font(80), fill=(10, 10, 10))
        ty += 88

    dur   = story_count * 2
    tils  = {"uz": "UZ / RU / EN", "ru": "RU / UZ / EN", "en": "EN / UZ / RU"}.get(lang, "UZ/RU/EN")
    draw.text((26, ty + 14), f"{dur} MIN  ·  {tils}",
              font=_font(17, False), fill=(130, 125, 115), anchor="lm")

    # ── O'NG PANEL: Story ro'yxati ────────────────────────────
    stories = stories or []
    n       = min(story_count, 5)
    item_h  = (VH - top_h) // n if n else 60
    rx      = lw + 22

    for i in range(1, n + 1):
        sarlavha = ""
        if i - 1 < len(stories):
            sarlavha = (stories[i - 1].get("sarlavha") or "").strip()

        item_cy = top_h + (i - 1) * item_h + item_h // 2

        if i > 1:
            div_y = top_h + (i - 1) * item_h
            draw.rectangle([(rx - 6, div_y), (VW - 14, div_y + 1)],
                           fill=(35, 35, 35))

        draw.text((rx, item_cy), f"{i:02d}",
                  font=_font(16), fill=C_RED, anchor="lm")

        title_txt = sarlavha[:52] + ("…" if len(sarlavha) > 52 else "") if sarlavha else f"STORY {i}"
        title_col = (220, 215, 205) if sarlavha else (60, 60, 60)
        draw.text((rx + 48, item_cy), title_txt,
                  font=_font(22), fill=title_col, anchor="lm")

    # ── PASTKI FOOTER ─────────────────────────────────────────
    draw.text((22, VH - 22), "youtube.com/@1dayglobal",
              font=_font(13, False), fill=(110, 105, 95), anchor="lm")
    draw.text((VW - 14, VH - 22), "1DAYGLOBAL.NEWS",
              font=_font(13, False), fill=C_RED, anchor="rm")

    img.save(out_path, "JPEG", quality=93)
    return out_path


# ─────────────────────────────────────────────────────────────
# KARTA 2: Yangilik sarlavha kartasi (per-story title)
# ─────────────────────────────────────────────────────────────
def _make_story_title_card(
        sarlavha: str, location: str, daraja: str,
        story_num: int, total: int, lang: str, out_path: str):
    """
    Rasm topilmaganda fallback karta — TOZA dizayn.
    Raqam, sana, progress bar YO'Q — faqat brend + sarlavha + joylashuv.
    """
    img  = Image.new("RGB", (VW, VH), C_BG)
    draw = ImageDraw.Draw(img)

    # Subtle grid (brand element)
    for gx in range(0, VW, 80):
        draw.line([(gx, 0), (gx, VH)], fill=(20, 20, 20), width=1)
    for gy in range(0, VH, 80):
        draw.line([(0, gy), (VW, gy)], fill=(20, 20, 20), width=1)

    # Chap qizil aksent bar
    draw.rectangle([(0, 0), (7, VH)], fill=C_RED)

    # ── YUQORI BAR ────────────────────────────────────────────
    top_h = 52
    draw.rectangle([(0, 0), (VW, top_h)], fill=(8, 8, 8))
    draw.rectangle([(0, top_h - 3), (VW, top_h)], fill=C_RED)

    # LIVE badge
    draw.rectangle([(10, 8), (70, top_h - 8)], fill=C_RED)
    draw.text((40, top_h // 2), "LIVE", font=_font(17), fill=C_WHITE, anchor="mm")

    # Brand nomi — FAQAT, sana/raqam YO'Q
    draw.text((82, top_h // 2), "1DAY GLOBAL  ·  WORLD NEWS",
              font=_font(18), fill=C_WHITE, anchor="lm")

    # ── DARAJA BADGE ─────────────────────────────────────────
    badge_labels = {
        "uz": {"muhim": "MUHIM YANGILIK", "tezkor": "TEZKOR", "xabar": "YANGILIK"},
        "ru": {"muhim": "ГЛАВНАЯ",        "tezkor": "СРОЧНО", "xabar": "НОВОСТЬ"},
        "en": {"muhim": "BREAKING",       "tezkor": "URGENT", "xabar": "NEWS"},
    }
    badge_txt = badge_labels.get(lang, badge_labels["en"]).get(daraja, "NEWS")
    bw = len(badge_txt) * 13 + 28
    bx, by = 20, top_h + 30
    draw.rectangle([(bx, by), (bx + bw, by + 34)], fill=C_RED)
    draw.text((bx + bw // 2, by + 17), badge_txt,
              font=_font(20), fill=C_WHITE, anchor="mm")

    # ── SARLAVHA (markazda, katta) ───────────────────────────
    title_text = sarlavha or ""
    wrapped    = textwrap.wrap(title_text, width=32)[:5]
    ty = VH // 2 - (len(wrapped) * 64) // 2
    for i, line in enumerate(wrapped):
        fs   = 58 if i == 0 else 48
        fill = C_WHITE if i == 0 else C_LGRAY
        _text_shadow(draw, (20, ty), line, font=_font(fs), fill=fill, offset=3)
        ty += fs + 14

    # ── LOCATION (pastda, agar bor bo'lsa) ───────────────────
    if location:
        loc_txt = f"📍 {location.upper()[:35]}"
        loc_y   = VH - 90
        draw.rectangle([(20, loc_y), (20 + len(loc_txt) * 11 + 16, loc_y + 30)],
                        fill=(18, 18, 18))
        draw.rectangle([(20, loc_y), (23, loc_y + 30)], fill=C_RED)
        draw.text((36, loc_y + 15), loc_txt, font=_font(16, False),
                  fill=C_LGRAY, anchor="lm")

    # ── PASTKI BAR ────────────────────────────────────────────
    bot_h = 32
    bot_y = VH - bot_h
    draw.rectangle([(0, bot_y), (VW, VH)], fill=(8, 8, 8))
    draw.rectangle([(0, bot_y), (VW, bot_y + 2)], fill=C_RED)
    draw.text((14, bot_y + bot_h // 2), "1D  1DAY GLOBAL",
              font=_font(14), fill=C_WHITE, anchor="lm")

    img.save(out_path, "JPEG", quality=93)
    return out_path


# ─────────────────────────────────────────────────────────────
# KARTA 3: Yangilik foto + overlay (per-story photo segment)
# ─────────────────────────────────────────────────────────────
def _crop_resize_photo(photo_path: str, out_path: str) -> bool:
    """Rasmni 16:9, 1280x720 ga crop/resize qilish (matn yo'q, fon uchun)."""
    try:
        img    = Image.open(photo_path).convert("RGB")
        bw, bh = img.size
        tgt_r  = VW / VH
        src_r  = bw / bh
        if src_r > tgt_r:
            nw = int(bh * tgt_r); x = (bw - nw) // 2
            img = img.crop((x, 0, x + nw, bh))
        else:
            nh = int(bw / tgt_r); y = (bh - nh) // 2
            img = img.crop((0, y, bw, y + nh))
        img = img.resize((VW, VH), Image.LANCZOS)
        img = ImageEnhance.Brightness(img).enhance(0.78)   # biroz qoraytiramiz
        img.save(out_path, "JPEG", quality=92)
        return True
    except Exception:
        return False


def _make_photo_overlay_png(
        sarlavha: str, location: str,
        daraja: str, stats: list, lang: str,
        next_title: str, story_num: int, total: int,
        out_path: str) -> str:
    """
    1DAY GLOBAL broadcast-style overlay (RGBA) — TV yangiliklar formati:
      · Yuqori bar: LIVE + brend + vaqt/sana + epizod
      · Pastki lower-third: BREAKING badge + sarlavha
      · Ticker: NEXT → keyingi yangilik
      · Pastki bar: logotip + handles
      · Geo karta: o'ng pastda (faqat digest uchun, shorts uchun yo'q)
    """
    img  = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    now_t    = datetime.now()
    date_str = now_t.strftime("%d %b %Y").upper()
    time_str = now_t.strftime("%H:%M  GMT+5")
    ep_str   = f"EP. {story_num}/{total}"
    lang_tag = lang.upper()

    # ── 1. YUQORI BAR (broadcast style) ──────────────────────
    bar_h = 46
    draw.rectangle([(0, 0), (VW, bar_h)], fill=(0, 0, 0, 240))
    draw.rectangle([(0, bar_h - 3), (VW, bar_h)], fill=(*C_RED, 255))

    # LIVE badge
    draw.rectangle([(8, 8), (72, bar_h - 8)], fill=(*C_RED, 255))
    draw.text((40, bar_h // 2), "LIVE", font=_font(18), fill=C_WHITE, anchor="mm")

    # Brend nomi
    draw.text((84, bar_h // 2), "1DAY GLOBAL  ·  WORLD NEWS",
              font=_font(18), fill=C_WHITE, anchor="lm")

    # O'ng tomon: sana + vaqt + epizod
    draw.text((VW - 8, bar_h // 2),
              f"{date_str}  ·  {time_str}  ·  {ep_str}  ·  {lang_tag}",
              font=_font(15, False), fill=C_LGRAY, anchor="rm")

    # ── 2. PROGRESS BAR ──────────────────────────────────────
    if total > 1:
        prog_w = int(VW * story_num / total)
        draw.rectangle([(0, bar_h), (prog_w, bar_h + 3)], fill=(*C_RED, 220))
        draw.rectangle([(prog_w, bar_h), (VW, bar_h + 3)], fill=(40, 40, 40, 150))

    # ── 3. GEO MINI-KARTA (o'ng tomonida, pastki qismda) ────
    if location:
        geo_w, geo_h = 220, 135
        geo_x = VW - geo_w - 10
        geo_y = bar_h + 8
        try:
            from geo_map import draw_geo_card, _lookup_city as _lc
            import uuid
            tmp_geo = os.path.join(TEMP_DIR, f"dg_geo_{uuid.uuid4().hex[:8]}.png")
            _zoom = 5 if _lc(location) else 4
            draw_geo_card(location, tmp_geo, card_w=geo_w, card_h=geo_h, zoom=_zoom)
            geo_img = Image.open(tmp_geo).convert("RGBA")
            r, g, b, a_ch = geo_img.split()
            a_ch = a_ch.point(lambda p: int(p * 0.80))
            geo_img.putalpha(a_ch)
            img.alpha_composite(geo_img, (geo_x, geo_y))
            try: os.remove(tmp_geo)
            except: pass
        except Exception:
            # Fallback: qizil nuqta + joy nomi
            draw.rectangle([(geo_x, geo_y), (geo_x + geo_w, geo_y + geo_h)],
                            fill=(10, 10, 10, 180), outline=(*C_RED, 120), width=1)
            cx = geo_x + geo_w // 2
            cy = geo_y + (geo_h - 20) // 2
            draw.ellipse([(cx - 6, cy - 6), (cx + 6, cy + 6)], fill=(*C_RED, 240))
            draw.ellipse([(cx - 3, cy - 3), (cx + 3, cy + 3)], fill=(255, 255, 255, 255))
            draw.text((cx, cy + 18), location.upper()[:18],
                      font=_font(13), fill=C_WHITE, anchor="mm")

    # ── 4. PASTKI QORA GRADIENT (lower-third foni) ───────────
    grad_h   = 230
    grad_img = Image.new("RGBA", (VW, grad_h), (0, 0, 0, 0))
    g_draw   = ImageDraw.Draw(grad_img)
    for dy in range(grad_h):
        alpha = int(252 * (dy / grad_h) ** 1.1)
        g_draw.line([(0, dy), (VW, dy)], fill=(0, 0, 0, alpha))
    img.paste(grad_img, (0, VH - grad_h), grad_img)

    # ── 5. BREAKING BADGE + SARLAVHA ─────────────────────────
    brk_labels = {
        "uz": {"muhim": "BREAKING", "tezkor": "TEZKOR", "xabar": "YANGILIK"},
        "ru": {"muhim": "СРОЧНО",   "tezkor": "СРОЧНО", "xabar": "НОВОСТЬ"},
        "en": {"muhim": "BREAKING", "tezkor": "URGENT", "xabar": "NEWS"},
    }
    brk_text = brk_labels.get(lang, brk_labels["en"]).get(daraja, "NEWS")
    brk_w    = len(brk_text) * 14 + 24
    brk_y    = VH - 148
    draw.rectangle([(0, brk_y), (brk_w, brk_y + 38)], fill=(*C_RED, 255))
    draw.text((brk_w // 2, brk_y + 19), brk_text,
              font=_font(22), fill=C_WHITE, anchor="mm")

    # Sarlavha
    if sarlavha:
        wrapped = textwrap.wrap(sarlavha, width=52)[:2]
        ty = VH - 143
        for i, line in enumerate(wrapped):
            fs  = 38 if i == 0 else 33
            col = (255, 255, 255, 255) if i == 0 else (200, 200, 200, 240)
            draw.text((brk_w + 10, ty + 2), line, font=_font(fs), fill=(0, 0, 0, 150))
            draw.text((brk_w + 8, ty),      line, font=_font(fs), fill=col)
            ty += fs + 6

    # ── 6. TICKER BAR — faqat sana va kanal nomi (keyingi sarlavha YO'Q) ──
    tick_h = 36
    tick_y = VH - tick_h - 32
    draw.rectangle([(0, tick_y), (VW, tick_y + tick_h)], fill=(10, 10, 10, 245))
    draw.rectangle([(0, tick_y), (0 + 3, tick_y + tick_h)], fill=(*C_RED, 255))

    # "WORLD" dot badge
    draw.rectangle([(6, tick_y + 4), (80, tick_y + tick_h - 4)], fill=(*C_RED, 220))
    draw.text((43, tick_y + tick_h // 2), "WORLD", font=_font(14), fill=C_WHITE, anchor="mm")

    # Sana + kanal (keyingi sarlavha yo'q — foydalanuvchi so'rovi)
    from datetime import date as _date
    ticker_txt = f"1DAY GLOBAL  ·  THE WORLD IN ONE DAY  ·  {_date.today().strftime('%d.%m.%Y')}"
    draw.text((90, tick_y + tick_h // 2), ticker_txt,
              font=_font(16, False), fill=C_WHITE, anchor="lm")

    # ── 7. PASTKI BAR (logotip + handles) ────────────────────
    bot_h = 30
    bot_y = VH - bot_h
    draw.rectangle([(0, bot_y), (VW, VH)], fill=(0, 0, 0, 255))
    draw.rectangle([(0, bot_y), (VW, bot_y + 2)], fill=(*C_RED, 200))

    handles = {
        "uz": "YOUTUBE.COM/@1DAYGLOBAL  ·  TELEGRAM  @BIRKUNDAY",
        "ru": "YOUTUBE.COM/@1DAYGLOBAL  ·  TELEGRAM  @BIRKUNDAY_RU",
        "en": "YOUTUBE.COM/@1DAYGLOBAL  ·  TELEGRAM  @BIRKUNDAY_EN",
    }.get(lang, "YOUTUBE.COM/@1DAYGLOBAL")
    draw.text((10, bot_y + bot_h // 2), "1D  1DAY GLOBAL",
              font=_font(14), fill=C_WHITE, anchor="lm")
    draw.text((VW - 8, bot_y + bot_h // 2), handles,
              font=_font(12, False), fill=C_LGRAY, anchor="rm")

    img.save(out_path, "PNG")
    return out_path


# ─────────────────────────────────────────────────────────────
# KARTA 4: Outro kartasi
# ─────────────────────────────────────────────────────────────
def _make_outro_card(lang: str, out_path: str):
    """
    1DAY GLOBAL brand style outro card (endi ishlatilmaydi — lekin arxiv uchun saqlanadi).
    Foydalanuvchi so'rovi: barcha video turlaridan intro/outro olib tashlangan.
    """
    img  = Image.new("RGB", (VW, VH), C_BG)
    draw = ImageDraw.Draw(img)

    # Subtle grid
    for gx in range(0, VW, 80):
        draw.line([(gx, 0), (gx, VH)], fill=(20, 20, 20), width=1)
    for gy in range(0, VH, 80):
        draw.line([(0, gy), (VW, gy)], fill=(20, 20, 20), width=1)

    # Aksent barlar
    draw.rectangle([(0, 0), (7, VH)], fill=C_RED)
    draw.rectangle([(VW - 7, 0), (VW, VH)], fill=C_RED)
    draw.rectangle([(0, 0), (VW, 5)], fill=C_RED)
    draw.rectangle([(0, VH - 5), (VW, VH)], fill=C_RED)

    # Brand nomi
    _text_shadow(draw, (VW // 2, VH // 2 - 80), "1DAY GLOBAL",
                 font=_font(82), fill=C_WHITE, offset=3, anchor="mm")

    # Tagline
    draw.text((VW // 2, VH // 2 - 20), "THE WORLD · IN ONE DAY",
              font=_font(26, False), fill=C_RED, anchor="mm")

    cta = {
        "uz": ("OBUNA BO'LING!", "Har kuni dunyo yangiliklari — kanalimizda"),
        "ru": ("ПОДПИСЫВАЙТЕСЬ!", "Главные новости мира — каждый день"),
        "en": ("SUBSCRIBE NOW!", "World news delivered daily"),
    }.get(lang, ("SUBSCRIBE!", "World news every day"))

    draw.rectangle([(VW // 2 - 200, VH // 2 + 20), (VW // 2 + 200, VH // 2 + 66)],
                   fill=C_RED)
    draw.text((VW // 2, VH // 2 + 43), cta[0], font=_font(36), fill=C_WHITE, anchor="mm")
    draw.text((VW // 2, VH // 2 + 85), cta[1],
              font=_font(20, bold=False), fill=C_LGRAY, anchor="mm")

    draw.text((VW // 2, VH - 50), "youtube.com/@1dayglobal  ·  t.me/birkunday",
              font=_font(16, bold=False), fill=C_LGRAY, anchor="mm")

    img.save(out_path, "JPEG", quality=93)
    return out_path


# ─────────────────────────────────────────────────────────────
# Rasm → video (2 xil usul)
# ─────────────────────────────────────────────────────────────

# Juda sekin pan effektlari — fon uchun (overlay bilan birga)
_PAN_EFFECTS = [
    "scale=iw*1.08:ih*1.08,crop={w}:{h}:0:0",                        # chapdan
    "scale=iw*1.08:ih*1.08,crop={w}:{h}:iw*0.08:0",                  # o'ngdan
    "scale=iw*1.08:ih*1.08,crop={w}:{h}:iw*0.04:ih*0.04",            # markazdan
    "scale=iw*1.08:ih*1.08,crop={w}:{h}:0:ih*0.08",                   # pastdan
]


def _photo_to_video_composite(
        raw_photo: str, overlay_png: str,
        duration: float, pan_idx: int, out_path: str) -> bool:
    """
    Sekin horizontal pan fon + STATIK overlay composite.

    Fon 1.10x o'lchamda, sekin chap→o'ng yoki o'ng→chap siljiydi.
    Overlay PNG (matn, geo, ticker) HARAKATSIZ — hech qachon kadrdan chiqmaydi.
    """
    dur_s = f"{duration:.3f}"
    # Pan yo'nalishi: juft → chapdan, toq → o'ngdan
    extra = int(VW * 0.10)   # 128px qo'shimcha kenglik (10%)
    extra_h = int(VH * 0.10) # 72px qo'shimcha balandlik
    scaled_w = VW + extra
    scaled_h = VH + extra_h

    if pan_idx % 2 == 0:
        # Chapdan o'ngga sekin pan
        pan_x = f"trunc({extra}*t/{duration})"
    else:
        # O'ngdan chapga sekin pan
        pan_x = f"trunc({extra}*(1-t/{duration}))"

    pan_y = str(extra_h // 2)   # vertikal markaz

    fc = (
        f"[0:v]scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
        f"crop={VW}:{VH}:x='{pan_x}':y={pan_y},fps={FPS}[bg];"
        f"[1:v]scale={VW}:{VH}[ovl];"
        f"[bg][ovl]overlay=0:0:shortest=1[out]"
    )
    r = subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", raw_photo,
        "-loop", "1", "-i", overlay_png,
        "-filter_complex", fc,
        "-map", "[out]",
        "-t", dur_s,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-an", out_path,
    ], capture_output=True, timeout=180)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace")[-300:]
        print(f"     composite xato: {err}")
        return _still_to_video(raw_photo, duration, out_path)
    return os.path.exists(out_path)


def _still_to_video(img_path: str, duration: float, out_path: str) -> bool:
    r = subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", img_path,
        "-vf", (f"scale={VW}:{VH}:force_original_aspect_ratio=decrease,"
                f"pad={VW}:{VH}:(ow-iw)/2:(oh-ih)/2:color=black,fps={FPS}"),
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-an", out_path,
    ], capture_output=True, timeout=60)
    return r.returncode == 0 and os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# Rasm yuklash
# ─────────────────────────────────────────────────────────────
def _fetch_og_image(article_url: str, out_path: str) -> bool:
    if not article_url or not article_url.startswith("http"):
        return False
    try:
        hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(article_url, headers=hdrs, timeout=12, allow_redirects=True)
        if resp.status_code != 200:
            return False
        for pat in [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ]:
            m = re.search(pat, resp.text, re.IGNORECASE)
            if m:
                img_url = m.group(1).strip()
                if img_url.startswith("http"):
                    ir = requests.get(img_url, headers=hdrs, timeout=15)
                    if ir.status_code == 200 and len(ir.content) >= 10_000:
                        with open(out_path, "wb") as fh:
                            fh.write(ir.content)
                        return True
    except Exception:
        pass
    return False


def _fetch_pexels(query: str, out_path: str, seen_ids: set) -> bool:
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key or not query.strip():
        return False
    # Faqat ASCII so'rovlar Pexels uchun
    if not all(c.isascii() or not c.isalpha() for c in query):
        return False
    try:
        hdrs = {"Authorization": api_key}
        url  = (f"https://api.pexels.com/v1/search"
                f"?query={requests.utils.quote(query[:80])}"
                f"&per_page=15&orientation=landscape")
        resp = requests.get(url, headers=hdrs, timeout=12)
        if resp.status_code != 200:
            return False
        photos = resp.json().get("photos", [])
        random.shuffle(photos)
        for ph in photos:
            ph_id = ph.get("id")
            if ph_id in seen_ids:
                continue
            seen_ids.add(ph_id)
            src     = ph.get("src", {})
            img_url = src.get("large2x") or src.get("large") or src.get("medium", "")
            if not img_url:
                continue
            ir = requests.get(img_url, timeout=20)
            if ir.status_code == 200 and len(ir.content) >= 20_000:
                with open(out_path, "wb") as f:
                    f.write(ir.content)
                return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────
# TTS
# ─────────────────────────────────────────────────────────────
_CYR2LAT = {
    'А':'A','а':'a','Б':'B','б':'b','В':'V','в':'v','Г':'G','г':'g',
    'Ғ':"G'",'ғ':"g'",'Д':'D','д':'d','Е':'E','е':'e','Ё':'Yo','ё':'yo',
    'Ж':'J','ж':'j','З':'Z','з':'z','И':'I','и':'i','Й':'Y','й':'y',
    'К':'K','к':'k','Қ':'Q','қ':'q','Л':'L','л':'l','М':'M','м':'m',
    'Н':'N','н':'n','О':'O','о':'o','П':'P','п':'p','Р':'R','р':'r',
    'С':'S','с':'s','Т':'T','т':'t','У':'U','у':'u','Ф':'F','ф':'f',
    'Х':'X','х':'x','Ҳ':'H','ҳ':'h','Ч':'Ch','ч':'ch','Ш':'Sh','ш':'sh',
    'Ъ':"'",'ъ':"'",'Ь':''  ,'ь':''  ,'Э':'E','э':'e',
    'Ю':'Yu','ю':'yu','Я':'Ya','я':'ya','Ў':"O'",'ў':"o'",
}


async def _tts_async(text: str, voice: str, rate: str, out_path: str):
    comm = edge_tts.Communicate(text, voice, rate=rate)
    await comm.save(out_path)


_CYR_CHARS = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯЎҚҒҲ"

# UZ matni axlat ekanligini aniqlash uchun belgilar
_GARBAGE_UZ_WORDS = {
    "ўзбекчалаштирилди", "tarjima", "translated", "translation",
    "ёғин",      # fire (ёнғин) o'rniga rain (ёғин) — tez-tez xato
    "шамбí",     # lotin harflar aralashtirish
    "бор",       # "oldinга bor" — mantiqsiz
    "чўнгай",    # mavjud bo'lmagan so'z
    "қўллай",    # mavjud bo'lmagan so'z
    "ажимасчи",  # mavjud bo'lmagan so'z
    "буйин",     # "bosh" o'rniga
    "асослари",  # "bazalari" o'rniga
    "саллатиш",  # "ağdarish" o'rniga
    "олдинга",   # mantiqsiz
}

def _is_garbage_uz(text: str) -> bool:
    """UZ matn axlat (noto'g'ri tarjima) ekanligini aniqlash."""
    if not text or len(text.strip()) < 5:
        return True
    t = text.lower()
    # Aniq axlat so'zlar bormi?
    for bad in _GARBAGE_UZ_WORDS:
        if bad.lower() in t:
            return True
    # Lotin harflar kirillda: highway, fire, kabi
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return True
    cyr_ratio = sum(1 for c in alpha if c in _CYR_CHARS) / len(alpha)
    ascii_ratio = sum(1 for c in alpha if c.isascii()) / len(alpha)
    # 30%+ ASCII — inglizcha/lotincha aralashtirish (axlat)
    if ascii_ratio > 0.30:
        return True
    # 50% dan kam kirill — yaxshi tarjima emas
    if cyr_ratio < 0.50:
        return True
    return False


def _fix_uz_from_ru(ru_text: str, en_title: str) -> str:
    """RU matnini UZ Kirill ga tarjima qilish (ekran matn uchun).
    _generate_script dan farqi: bu kirill chiqaradi, u lotin."""
    if not ru_text or not ru_text.strip():
        return ""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(_HERE, "..", "TELEGRAM"))
        from translator import _uz_from_russian
        result = _uz_from_russian(ru_text[:400], context_en=en_title)
        if result and len(result.split()) >= 3:
            return result
    except Exception as e:
        log.warning(f"  _fix_uz_from_ru xato: {e}")
    return ""


def _generate_script(en_title: str, jumla_text: str, lang: str) -> str:
    """Script bo'sh yoki qisqa bo'lsa — AI bilan yangi script yaratish.
    Zanjir: Anthropic → Groq → OpenRouter
    Natija: 200-250 so'z, TTS uchun tayyor."""
    import sys as _sys
    _sys.path.insert(0, os.path.join(_HERE, "..", "TELEGRAM"))

    lang_map = {
        "uz": ("Uzbek LATIN script ONLY (lotin harflarda, TTS uchun). SOF O'ZBEK tili. "
               "Ruscha yoki inglizcha so'z ishlatma. "
               "Trump=Tramp, Biden=Bayden, Netanyahu=Netanyaxu, Gaza=G'azo, "
               "Iran=Eron, Russia=Rossiya, Ukraine=Ukraina, Israel=Isroil. "
               "200-250 so'z."),
        "ru": "Russian Cyrillic ONLY. 200-250 слов. Подробный репортаж. Все имена и страны на русском.",
        "en": "English. 200-250 words. Detailed news report.",
    }
    lang_instr = lang_map.get(lang, lang_map["en"])
    prompt = (
        f"Write a detailed news narration script in {lang_instr}\n"
        f"News headline: {en_title}\n"
        f"Context: {(jumla_text or '')[:500] or 'No additional context.'}\n\n"
        "Cover: what happened, where, who is involved, why it matters, consequences.\n"
        "NO intro phrases like 'Welcome', 'This is', 'Efirda', 'V efire'.\n"
        "Return ONLY the narration text."
    )

    def _clean(text: str) -> str:
        text = re.sub(
            r"^(Efirda\s+1KUN|В\s+эфире\s+1ДЕНЬ|This\s+is\s+1DAY)[^.]*\.\s*",
            "", text.strip(), flags=re.IGNORECASE
        ).strip()
        return text

    # ── 1. Anthropic (eng yuqori sifat) ──────────────────────
    try:
        from translator import _ask_anthropic
        result = _clean(_ask_anthropic(prompt, max_tokens=1500, model="claude-sonnet-4-6"))
        if len(result.split()) >= 50:
            log.info(f"  Script Anthropic [{lang.upper()}]: {len(result.split())} so'z")
            return result
    except Exception as e:
        log.warning(f"  Script Anthropic xato ({lang}): {str(e)[:80]}")

    # ── 2. Groq (tez va bepul) ────────────────────────────────
    try:
        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            groq_models = [
                "llama-3.3-70b-versatile",
                "llama-3.1-70b-versatile",
                "gemma2-9b-it",
                "llama-3.1-8b-instant",
            ]
            for gm in groq_models:
                try:
                    r = requests.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {groq_key}",
                                 "Content-Type": "application/json"},
                        json={"model": gm,
                              "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": 1500, "temperature": 0.6},
                        timeout=30
                    )
                    if r.ok:
                        result = _clean(r.json()["choices"][0]["message"]["content"])
                        if len(result.split()) >= 50:
                            log.info(f"  Script Groq/{gm} [{lang.upper()}]: {len(result.split())} so'z")
                            return result
                    elif r.status_code == 429:
                        import time; time.sleep(5)
                    else:
                        break   # 400/404 → keyingi model emas, keyingi servis
                except Exception:
                    continue
    except Exception as e:
        log.warning(f"  Script Groq xato ({lang}): {str(e)[:80]}")

    # ── 3. OpenRouter (fallback) ──────────────────────────────
    try:
        or_key = os.getenv("OPENROUTER_API_KEY", "")
        if or_key:
            or_models = [
                "meta-llama/llama-3.3-70b-instruct:free",
                "google/gemma-3-27b-it:free",
                "qwen/qwen3-8b:free",
                "google/gemma-2-9b-it:free",
            ]
            for om in or_models:
                try:
                    r = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {or_key}",
                                 "Content-Type": "application/json",
                                 "X-Title": "1Kun Global"},
                        json={"model": om,
                              "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": 1500},
                        timeout=45
                    )
                    if r.ok:
                        result = _clean(r.json()["choices"][0]["message"]["content"])
                        if len(result.split()) >= 50:
                            log.info(f"  Script OpenRouter/{om} [{lang.upper()}]: {len(result.split())} so'z")
                            return result
                    elif r.status_code in (404, 402):
                        continue   # Model yo'q → keyingisi
                    elif r.status_code == 429:
                        import time; time.sleep(10); continue
                except Exception:
                    continue
    except Exception as e:
        log.warning(f"  Script OpenRouter xato ({lang}): {str(e)[:80]}")

    log.warning(f"  Script barcha servislar muvaffaqiyatsiz ({lang})")
    return ""


_TTS_NAME_FIX_UZ = {
    # Loqin atoqli ismlar → O'zbek TTS talaffuzi
    "Mladic":       "Mladich",
    "mladic":       "mladich",
    "Hague":        "Gaaga",
    "hague":        "gaaga",
    "Srebrenica":   "Srebrenitsa",
    "srebrenica":   "srebrenitsa",
    "Zelensky":     "Zelenskiy",
    "zelensky":     "zelenskiy",
    "Zelenskyy":    "Zelenskiy",
    "zelenskyy":    "zelenskiy",
    "Netanyahu":    "Netanyaxu",
    "netanyahu":    "Netanyaxu",
    "Hamas":        "Xamas",
    "hamas":        "xamas",
    "Hezbollah":    "Hizbulloh",
    "hezbollah":    "hizbulloh",
    "Houthi":       "Xusiy",
    "houthi":       "xusiy",
    "Houthis":      "Xusiylar",
    "houthis":      "xusiylar",
    "Macron":       "Makron",
    "macron":       "makron",
    "Erdogan":      "Erdo'g'on",
    "erdogan":      "erdo'g'on",
    "Khamenei":     "Xomeneiy",
    "khamenei":     "xomeneiy",
    "Khomeini":     "Xumayni",
    "khomeini":     "xumayni",
    "Kissinger":    "Kissinjer",
    "kissinger":    "kissinjer",
    "Milosevic":    "Miloshevich",
    "milosevic":    "miloshevich",
    "Karadzic":     "Karajich",
    "karadzic":     "karajich",
    "Sejdiu":       "Seydiu",
    "Vucic":        "Vuchich",
    "vucic":        "vuchich",
    "Djokovic":     "Jokovich",
    "djokovic":     "jokovich",
    "Macgregor":    "Makgregor",
    "macgregor":    "makgregor",
    "Guterres":     "Guterresh",
    "guterres":     "guterresh",
    "Merkel":       "Merkel",
    "Schumer":      "Shumer",
    "schumer":      "shumer",
    "Scholz":       "Sholts",
    "scholz":       "sholts",
    "Ursula":       "Ursula",
    "Lavrov":       "Lavrov",
    "Peskov":       "Peskov",
    "Shoigu":       "Shoygу",
    "shoigu":       "shoygu",
    "Patrushev":    "Patrushev",
    "Medvedev":     "Medvedev",
    "Lukashenko":   "Lukashenko",
    "lukashenko":   "lukashenko",
    "Orbán":        "Orban",
    "orban":        "orban",
    "Orban":        "Orban",
    "Salvini":      "Salvini",
    "Meloni":       "Meloni",
    "Trudeau":      "Trudo",
    "trudeau":      "trudo",
    "Sunak":        "Sunak",
    "Starmer":      "Starmer",
    "Albanese":     "Albaneze",
    "Milei":        "Miley",
    "milei":        "miley",
    "Bukele":       "Bukele",
    "Petro":        "Petro",
    "Boric":        "Borich",
    "boric":        "borich",
    "Lula":         "Lula",
    "Bolsonaro":    "Bolsonaro",
    "Maduro":       "Maduro",
    "Chavez":       "Chaves",
    "chavez":       "chaves",
    "Chavez's":     "Chavesning",
    "Mugabe":       "Mugabe",
    "Mnangagwa":    "Mnangagva",
    "Ramaphosa":    "Ramaphoza",
    "ramaphosa":    "ramaphoza",
    "Sissi":        "Sissi",
    "Sisi":         "Sisi",
    "MBS":          "MBS",
    "Mohammed":     "Muhammad",
    "Bin":          "Bin",
    "Salman":       "Salmon",
    "Raisi":        "Raisi",
    "Pezeshkian":   "Pezeshkiyon",
    "pezeshkian":   "pezeshkiyon",
    # Joylar
    "Gaza":         "G'azo",
    "Kyiv":         "Kiyev",
    "kyiv":         "kiyev",
    "Kiev":         "Kiyev",
    "Moscow":       "Moskva",
    "moscow":       "moskva",
    "Tehran":       "Tehron",
    "tehran":       "tehron",
    "Warsaw":       "Varshava",
    "warsaw":       "varshava",
    "Prague":       "Praha",
    "prague":       "praha",
    "Brussels":     "Bryussel",
    "brussels":     "bryussel",
    "Geneva":       "Jeneva",
    "geneva":       "jeneva",
    "Vienna":       "Vena",
    "vienna":       "vena",
    "Beirut":       "Bayrut",
    "beirut":       "bayrut",
    "Riyadh":       "Ar-Riyod",
    "riyadh":       "ar-riyod",
    "Doha":         "Doha",
    "Kabul":        "Kobul",
    "kabul":        "kobul",
    "Khartoum":     "Xartum",
    "khartoum":     "xartum",
    "Rafah":        "Rafah",
    "rafah":        "rafah",
    "Jenin":        "Jenin",
    "jenin":        "jenin",
    "Aleppo":       "Xalab",
    "aleppo":       "xalab",
    "Tripoli":      "Tripoli",
    "Benghazi":     "Bengazi",
    "Nairobi":      "Nayrobi",
    "nairobi":      "nayrobi",
    "Mogadishu":    "Mogadisho",
    "mogadishu":    "mogadisho",
    "Bamako":       "Bamako",
    "Niamey":       "Niamey",
    "Ouagadougou":  "Vogogudu",
    "Monrovia":     "Monroviya",
    "Harare":       "Harare",
    "Kinshasa":     "Kinshasa",
    "Yangon":       "Yangon",
    "Naypyidaw":    "Naypyido",
    "Pyongyang":    "Phenyan",
    "pyongyang":    "phenyan",
    "Seoul":        "Seul",
    "seoul":        "seul",
    "Tokyo":        "Tokio",
    "tokyo":        "tokio",
    "Beijing":      "Pekin",
    "beijing":      "pekin",
    "Taipei":       "Tayvan",
    "taipei":       "tayvan",
    "New Delhi":    "Nyu-Dehli",
    "new delhi":    "nyu-dehli",
    "Islamabad":    "Islomobod",
    "islamabad":    "islomobod",
    "Dhaka":        "Daka",
    "dhaka":        "daka",
    # Tashkilotlar
    "NATO":         "NATO",
    "IAEA":         "XAEA",
    "OPEC":         "OPEK",
    "IMF":          "XVF",
    "ICC":          "XJK",
    "ICJ":          "XAD",
    "BRICS":        "BRIKS",
    "brics":        "briks",
    "G7":           "G-yetti",
    "G20":          "G-yigirma",
    "WHO":          "JSST",
    "WTO":          "JSD",
}

# Yil oralig'ini o'qish: "1992-1995" → til bo'yicha
_YEAR_RANGE_PATTERNS = [
    # "1992-1995" yoki "1992–1995" (en dash)
    (re.compile(r'\b(\d{4})\s*[-–]\s*(\d{4})\b'), {
        "uz": lambda m: f"{m.group(1)} yildan {m.group(2)} yilgacha",
        "ru": lambda m: f"с {m.group(1)} по {m.group(2)} год",
        "en": lambda m: f"from {m.group(1)} to {m.group(2)}",
    }),
    # "1992-yil" yoki "1992-yilgi" — yildan keyin chiziqli qo'shimcha
    (re.compile(r'\b(\d{4})-(?:yil|yilgi|yilda|yildan|yilda)\b', re.IGNORECASE), {
        "uz": lambda m: f"{m.group(1)} yil",
        "ru": lambda m: f"{m.group(1)} год",
        "en": lambda m: f"{m.group(1)}",
    }),
    # "1990s" → "1990-yillar" (UZ), "1990-е годы" (RU)
    (re.compile(r'\b(\d{4})s\b'), {
        "uz": lambda m: f"{m.group(1)}-yillar",
        "ru": lambda m: f"{m.group(1)}-е годы",
        "en": lambda m: f"{m.group(1)}s",
    }),
]

# Raqamdan keyin "-chi"/"-nchi" qo'shimchasi: "5-chi" → "beshinchi"
_NUM_SUFFIX_UZ = re.compile(r'\b(\d+)-(?:chi|nchi|inchi|nchisi|chisi)\b', re.IGNORECASE)
_NUM_ORDINAL_UZ = {
    1:"birinchi",2:"ikkinchi",3:"uchinchi",4:"to'rtinchi",5:"beshinchi",
    6:"oltinchi",7:"yettinchi",8:"sakkizinchi",9:"to'qqizinchi",10:"o'ninchi",
    11:"o'n birinchi",12:"o'n ikkinchi",13:"o'n uchinchi",14:"o'n to'rtinchi",
    15:"o'n beshinchi",16:"o'n oltinchi",17:"o'n yettinchi",18:"o'n sakkizinchi",
    19:"o'n to'qqizinchi",20:"yigirmanchi",21:"yigirma birinchi",
    22:"yigirma ikkinchi",23:"yigirma uchinchi",24:"yigirma to'rtinchi",
    25:"yigirma beshinchi",26:"yigirma oltinchi",27:"yigirma yettinchi",
    28:"yigirma sakkizinchi",29:"yigirma to'qqizinchi",30:"o'ttizinchi",
    31:"o'ttiz birinchi",
}

# Raqamdan keyin "-го"/"-й"/"-ый"/"-ой" (RU) — sanalar uchun
_NUM_SUFFIX_RU = re.compile(r'\b(\d+)[-–](?:го|й|ый|ой|ей|им|ом|е|ю|х|ми|мя)\b', re.IGNORECASE)

# Intro/outro iboralar (TTS dan tozalash uchun)
_TTS_INTRO_OUTRO_RX = re.compile(
    r'(?:^|\.\s+|\n)'                          # gap boshi yoki avvalgi gapdan keyin
    r'(?:'
    r'Keyingi\s+xabar[^.]*|'
    r'Keyingi\s+yangilik[^.]*|'
    r'Xulosa\s+qilib\s+aytganda[^.]*|'
    r'Biz\s+(?:sizga\s+)?xabar\s+berdik[^.]*|'
    r'Biz\s+(?:sizga\s+)?yetkazib\s+berdik[^.]*|'
    r"O'z\s+kanalimizga\s+obuna\s+bo'ling[^.]*|"
    r'Obuna\s+bo\'ling[^.]*|'
    r'Like\s+bosing[^.]*|'
    r'Далее[^.]*|'
    r'Следующая\s+новость[^.]*|'
    r'Следующий\s+сюжет[^.]*|'
    r'Подписывайтесь\s+на\s+канал[^.]*|'
    r'Ставьте\s+лайк[^.]*|'
    r'Next\s+up[^.]*|'
    r'Moving\s+on[^.]*|'
    r'Stay\s+tuned[^.]*|'
    r'Subscribe\s+to[^.]*|'
    r'Like\s+and\s+subscribe[^.]*|'
    r'Don\'t\s+forget\s+to\s+subscribe[^.]*'
    r')'
    r'(?:\.|$)',
    re.IGNORECASE | re.MULTILINE
)


def _preprocess_tts_text(text: str, lang: str) -> str:
    """TTS matnini qayta ishlash:
    1. Yil oralig'i: "1992-1995" → "1992 yildan 1995 yilgacha" (UZ)
    2. Raqam+qo'shimcha: "5-chi" → "beshinchi" (UZ); "1-го" → avtomat (RU)
    3. Xorijiy ismlar: "Mladic" → "Mladich" (UZ)
    4. Intro/outro iboralarini o'chirish
    """
    if not text:
        return text

    # 1. Intro/outro tozalash
    text = _TTS_INTRO_OUTRO_RX.sub("", text).strip()

    # 2. Yil oralig'i
    for rx, handlers in _YEAR_RANGE_PATTERNS:
        handler = handlers.get(lang)
        if handler:
            text = rx.sub(handler, text)

    # 3. UZ: "N-chi" → so'z bilan
    if lang == "uz":
        def _replace_ordinal(m):
            n = int(m.group(1))
            return _NUM_ORDINAL_UZ.get(n, f"{n}-chi")
        text = _NUM_SUFFIX_UZ.sub(_replace_ordinal, text)

    # 4. RU: "1-го" kabi qo'shimchalarni raqam bilan (TTS o'zi o'qiydi, chiziqni olib tashlash)
    if lang == "ru":
        text = _NUM_SUFFIX_RU.sub(lambda m: m.group(1), text)

    # 5. UZ: xorijiy ismlarni almashtirish (faqat so'z chegarasida)
    if lang == "uz":
        for orig, repl in _TTS_NAME_FIX_UZ.items():
            # So'z chegarasida almashtirish
            text = re.sub(r'\b' + re.escape(orig) + r'\b', repl, text)

    # 6. Ortiqcha bo'sh joylarni tozalash
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text


def _make_tts(text: str, lang: str, daraja: str, out_path: str) -> bool:
    vcfg = VOICES.get(lang, VOICES["uz"])
    cfg  = vcfg.get(daraja, vcfg.get("default", vcfg.get(list(vcfg.keys())[0])))
    # TTS matnini qayta ishlash (yil, ismlar, intro/outro)
    text = _preprocess_tts_text(text, lang)
    if lang == "uz":
        text = "".join(_CYR2LAT.get(c, c) for c in text)
    if not text or not text.strip():
        print(f"  TTS: matn bo'sh ({lang})")
        return False
    try:
        asyncio.run(_tts_async(text, cfg["voice"], cfg.get("rate", "-5%"), out_path))
        return os.path.exists(out_path)
    except Exception as e:
        print(f"  TTS xato ({lang}): {e}")
        return False


def _audio_dur(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────
# Segmentlarni birlashtirish (xfade)
# ─────────────────────────────────────────────────────────────
def _concat_xfade(video_parts: list, durations: list, out_path: str) -> bool:
    n = len(video_parts)
    if n == 0:
        return False
    if n == 1:
        return _still_to_video(video_parts[0], durations[0], out_path) or \
               bool(shutil.copy(video_parts[0], out_path))

    cmd = ["ffmpeg", "-y"]
    for vp in video_parts:
        cmd += ["-i", vp]

    trans = ["fade", "slideleft", "slideright", "slideup",
             "wipeleft", "wiperight", "fade", "slideleft"]
    fc_v = []
    prev = "[0:v]"
    for i in range(1, n):
        t   = trans[(i - 1) % len(trans)]
        off = sum(durations[:i]) - i * TRANS_DUR
        out = f"[v{i:02d}]"
        fc_v.append(
            f"{prev}[{i}:v]xfade=transition={t}"
            f":duration={TRANS_DUR:.2f}:offset={max(off,0):.2f}{out}"
        )
        prev = out

    cmd += ["-filter_complex", ";".join(fc_v), "-map", prev]
    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-an", "-movflags", "+faststart", out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        print("  xfade xato:", r.stderr.decode("utf-8", errors="replace")[-200:])
        # Fallback: oddiy concat
        ts  = datetime.now().strftime("%Y%m%d%H%M%S%f")
        txt = os.path.join(TEMP_DIR, f"dg_fc_{ts}.txt")
        with open(txt, "w") as fh:
            for p in video_parts:
                fh.write(f"file '{os.path.abspath(p)}'\n")
        r2 = subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", txt,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an", out_path,
        ], capture_output=True, timeout=300)
        try:
            os.remove(txt)
        except Exception:
            pass
        return r2.returncode == 0 and os.path.exists(out_path)
    return os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# Ovoz + musiqa miksaj
# ─────────────────────────────────────────────────────────────
_CACHED_MUSIC: str | None = None   # Sessiya davomida qayta ishlatish

def _get_music() -> str | None:
    """
    Yangiliklar uchun 120 BPM ritmik fon musiqasi.
    Kick drum (80 Hz, har urish) + bass + melodiya.
    """
    global _CACHED_MUSIC
    if _CACHED_MUSIC and os.path.exists(_CACHED_MUSIC):
        return _CACHED_MUSIC

    # assets/ dan haqiqiy musiqa fayli
    for fname in ("news_beat.mp3", "news_beat.aac",
                  "background_fast.mp3", "background_fast.aac",
                  "background.mp3", "background.aac"):
        p = os.path.join(_HERE, "assets", fname)
        if os.path.exists(p):
            _CACHED_MUSIC = p
            print(f"  🎵 Musiqa fayli: {fname}")
            return p

    gen_path = os.path.join(TEMP_DIR, "dg_beat_v2.aac")
    if os.path.exists(gen_path) and os.path.getsize(gen_path) > 50_000:
        _CACHED_MUSIC = gen_path
        return gen_path

    print("  🎵 120 BPM beat generatsiya qilinmoqda...")

    # 120 BPM = 2 Hz (har 0.5 sekunda bir urish)
    # 120 BPM beat — sodda ifoda (aevalsrc uchun tez):
    # Kick drum (80Hz) + Bass (110Hz) + Harmony (220Hz) + Melody (440Hz)
    # 4 ta atov, har biri amplituda modulatsiyali
    # Faqat 30s generatsiya — _mix_audio da -stream_loop bilan loop
    BP = "(t-floor(t*2)*0.5)"   # beat pozitsiyasi (0→0.5)

    expr = (
        f"sin(2*PI*80*{BP})*0.65*exp(-18*{BP})"          # kick drum
        "+sin(2*PI*110*t)*0.30*(0.5+0.5*cos(2*PI*2*t))"  # bass
        "+sin(2*PI*220*t)*0.22*(0.5+0.5*cos(2*PI*2*t))"  # bass oktava
        "+sin(2*PI*440*t)*0.16*(0.5+0.5*cos(2*PI*8*t))"  # melody
        "+sin(2*PI*330*t)*0.12*(0.4+0.6*cos(2*PI*4*t))"  # harmony
        "+sin(2*PI*55*t)*0.25*(0.5+0.5*cos(2*PI*2*t))"   # sub bass
    )

    r = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=44100:c=mono",
        "-t", "30",    # faqat 30s → loopda ishlatiladi
        "-af", (
            "highpass=f=50,"
            "equalizer=f=80:width_type=o:width=2:g=+5,"
            "equalizer=f=2500:width_type=o:width=2:g=+2,"
            "acompressor=threshold=0.3:ratio=5:attack=3:release=60:makeup=2.0,"
            "volume=0.90"
        ),
        "-c:a", "aac", "-b:a", "128k", gen_path,
    ], capture_output=True, timeout=60)

    if r.returncode != 0 or not os.path.exists(gen_path) or os.path.getsize(gen_path) < 5_000:
        # Fallback: minimal beat
        expr2 = (
            "sin(2*PI*110*t)*0.40*(0.5+0.5*cos(2*PI*2*t))"
            "+sin(2*PI*220*t)*0.28*(0.5+0.5*cos(2*PI*2*t))"
            "+sin(2*PI*440*t)*0.18*(0.4+0.6*cos(2*PI*4*t))"
        )
        r2 = subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"aevalsrc={expr2}:s=44100:c=mono",
            "-t", "30",
            "-af", "acompressor=threshold=0.3:ratio=4,volume=0.85",
            "-c:a", "aac", "-b:a", "128k", gen_path,
        ], capture_output=True, timeout=30)

    if os.path.exists(gen_path) and os.path.getsize(gen_path) > 10_000:
        print("  🎵 120 BPM beat tayyor!")
        _CACHED_MUSIC = gen_path
        return gen_path

    print("  ⚠️  Musiqa yaratilmadi")
    return None


def _mix_audio(video_path: str, voice_path: str, out_path: str, lang: str) -> bool:
    """
    Video (silent) + AI ovoz + fon musiqasi → yakuniy video.

    Muhim: output uzunligi = ovoz uzunligi (video ortiqcha qolmaydi).
    Musiqa + ovoz: amix:duration=shortest → ovoz tugashi bilan to'xtaydi.
    """
    fx         = AUDIO_FX.get(lang, AUDIO_FX.get("uz", "volume=1.0"))
    music_path = _get_music()
    vid_dur    = _audio_dur(video_path)
    voice_dur  = _audio_dur(voice_path)

    if vid_dur < 1:
        print(f"  ⚠️  Video uzunligi aniqlanmadi")
        return False
    if voice_dur < 1:
        print(f"  ⚠️  Ovoz uzunligi aniqlanmadi")
        return False

    # Output = video uzunligi (TTS qisqa bo'lsa — oxiriga jim musiqa davom etadi)
    # vid_dur = to'liq video (barcha segmentlar + xfade)
    # voice_dur — TTS uzunligi; agar qisqa bo'lsa — musiqa to'ldiradi
    target = vid_dur          # Doim to'liq video ko'rsatiladi
    vd     = f"{target:.3f}"

    if music_path and os.path.exists(music_path):
        # Ovoz: FX + apad → video uzunligiga to'ldiriladi
        # Musiqa: loop → video uzunligiga kesiladi
        # amix:duration=longest → ikkalasi ham to'liq eshitiladi
        af = (
            f"[1:a]aresample=44100,{fx},"
            f"apad=whole_dur={vd}[voice];"
            f"[2:a]aresample=44100,atrim=duration={vd},volume={MUSIC_VOL:.3f}[mus];"
            f"[voice][mus]amix=inputs=2:duration=longest,"
            f"aformat=channel_layouts=stereo[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", voice_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex", af,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k",
            "-t", vd,
            "-movflags", "+faststart", out_path,
        ]
    else:
        # Faqat ovoz (musiqa yo'q) — apad bilan to'ldiramiz
        af = (
            f"[1:a]aresample=44100,{fx},"
            f"apad=whole_dur={vd},"
            f"aformat=channel_layouts=stereo[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", voice_path,
            "-filter_complex", af,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k",
            "-t", vd,
            "-movflags", "+faststart", out_path,
        ]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        print("  Audio mix xato:", r.stderr.decode("utf-8", errors="replace")[-300:])
    return r.returncode == 0 and os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# SHORTS FORMAT (9:16 vertical, YouTube Shorts < 60s)
# ─────────────────────────────────────────────────────────────
SHORT_W, SHORT_H = 720, 1280
SHORT_DUR        = 55.0     # 55 sekund (< 60s Shorts limit)


def _crop_resize_photo_vertical(photo_path: str, out_path: str) -> bool:
    """Rasmni 9:16 (720x1280) ga crop/resize qilish."""
    try:
        img    = Image.open(photo_path).convert("RGB")
        bw, bh = img.size
        tgt_r  = SHORT_W / SHORT_H   # 0.5625
        src_r  = bw / bh
        if src_r > tgt_r:
            nw = int(bh * tgt_r); x = (bw - nw) // 2
            img = img.crop((x, 0, x + nw, bh))
        else:
            nh = int(bw / tgt_r); y = (bh - nh) // 2
            img = img.crop((0, y, bw, y + nh))
        img = img.resize((SHORT_W, SHORT_H), Image.LANCZOS)
        img = ImageEnhance.Brightness(img).enhance(0.75)
        img.save(out_path, "JPEG", quality=92)
        return True
    except Exception:
        return False


def _still_to_video_vertical(img_path: str, duration: float, out_path: str) -> bool:
    """720x1280 still image → video."""
    r = subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", img_path,
        "-vf", (f"scale={SHORT_W}:{SHORT_H}:force_original_aspect_ratio=decrease,"
                f"pad={SHORT_W}:{SHORT_H}:(ow-iw)/2:(oh-ih)/2:color=black,fps={FPS}"),
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-an", out_path,
    ], capture_output=True, timeout=60)
    return r.returncode == 0 and os.path.exists(out_path)


def _make_short_overlay_png(
        sarlavha: str, location: str,
        daraja: str, stats: list, lang: str,
        out_path: str) -> str:
    """
    720x1280 RGBA overlay — 1DAY GLOBAL brand style.
    Qora/oq/qizil, GEO XARITA YO'Q (foydalanuvchi so'rovi).
    """
    img  = Image.new("RGBA", (SHORT_W, SHORT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── 1. Chap qizil aksent chiziq ─────────────────────────
    draw.rectangle([(0, 0), (7, SHORT_H)], fill=(*C_RED, 245))

    # ── 2. Yuqori qora panel + qizil chiziq ─────────────────
    draw.rectangle([(0, 0), (SHORT_W, 80)], fill=(0, 0, 0, 235))
    draw.rectangle([(0, 78), (SHORT_W, 82)], fill=(*C_RED, 255))
    # Brend nomi
    brand = "1DAY GLOBAL"
    draw.text((SHORT_W // 2, 40), brand, font=_font(34), fill=C_WHITE, anchor="mm")

    # ── 3. Daraja badge (yuqori chap, qizil) ─────────────────
    banners = {
        "uz": {"muhim": "MUHIM", "tezkor": "TEZKOR", "xabar": "YANGILIK"},
        "ru": {"muhim": "ГЛАВНОЕ", "tezkor": "СРОЧНО", "xabar": "НОВОСТЬ"},
        "en": {"muhim": "BREAKING", "tezkor": "URGENT", "xabar": "NEWS"},
    }
    blabel = banners.get(lang, banners["en"]).get(daraja, "NEWS")
    b_w = len(blabel) * 16 + 24
    draw.rectangle([(18, 94), (18 + b_w, 136)], fill=(*C_RED, 255))
    draw.text((18 + b_w // 2, 115), blabel,
              font=_font(28), fill=C_WHITE, anchor="mm")

    # ── 4. Pastki qora gradient (sarlavha zonasi) ────────────
    grad_h = 620
    grad_img = Image.new("RGBA", (SHORT_W, grad_h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad_img)
    for dy in range(grad_h):
        alpha = int(255 * (dy / grad_h) ** 1.1)
        gd.line([(0, dy), (SHORT_W, dy)], fill=(0, 0, 0, alpha))
    img.paste(grad_img, (0, SHORT_H - grad_h), grad_img)

    # ── 5. Sarlavha (katta, pastki 1/3 qism) ─────────────────
    if sarlavha:
        wrapped = textwrap.wrap(sarlavha, width=18)[:4]
        total_h = sum([(62 if i == 0 else 56) + 14 for i in range(len(wrapped))])
        ty = SHORT_H - 170 - total_h
        for i, line in enumerate(wrapped):
            fs   = 62 if i == 0 else 56
            col  = (255, 255, 255, 255) if i == 0 else (200, 200, 200, 240)
            # Soya
            draw.text((20, ty + 2), line, font=_font(fs), fill=(0, 0, 0, 180))
            draw.text((18, ty),     line, font=_font(fs), fill=col)
            ty += fs + 14

    # ── 6. Location matn (sarlavha ustida, kichik) ───────────
    if location:
        loc_str = f"  {location.upper()}  "
        lw = len(loc_str) * 10 + 10
        loc_y = SHORT_H - 175 - total_h if sarlavha else SHORT_H - 200
        draw.rectangle([(18, loc_y - 22), (18 + lw, loc_y + 4)],
                        fill=(*C_RED, 200))
        draw.text((18 + lw // 2, loc_y - 9), loc_str,
                  font=_font(16, False), fill=C_WHITE, anchor="mm")

    # ── 7. Pastki qora panel + qizil chiziq + subscribe ──────
    ticker_h = 78
    y0 = SHORT_H - ticker_h
    draw.rectangle([(0, y0), (SHORT_W, SHORT_H)], fill=(0, 0, 0, 250))
    draw.rectangle([(0, y0), (SHORT_W, y0 + 3)], fill=(*C_RED, 255))
    sub = {
        "uz": "OBUNA BO'LING  ·  #SHORTS  ·  @birkunday",
        "ru": "ПОДПИСАТЬСЯ  ·  #SHORTS  ·  @birkunday_ru",
        "en": "SUBSCRIBE  ·  #SHORTS  ·  @birkunday_en",
    }.get(lang, "#SHORTS")
    draw.text((SHORT_W // 2, y0 + ticker_h // 2), sub,
              font=_font(20, False), fill=C_LGRAY, anchor="mm")

    img.save(out_path, "PNG")
    return out_path


def _photo_to_short_video(
        raw_photo: str, overlay_png: str,
        duration: float, out_path: str) -> bool:
    """720x1280 vertical video: sekin vertikal pan + statik overlay."""
    extra  = int(SHORT_H * 0.08)    # ~102px qo'shimcha balandlik (vertikal pan)
    dur_s  = f"{duration:.3f}"
    # Vertikal: tepadan pastga sekin pan
    pan_y  = f"trunc({extra}*t/{duration})"

    fc = (
        f"[0:v]scale={SHORT_W}:{SHORT_H + extra}:force_original_aspect_ratio=increase,"
        f"crop={SHORT_W}:{SHORT_H}:0:'{pan_y}',fps={FPS}[bg];"
        f"[1:v]scale={SHORT_W}:{SHORT_H}[ovl];"
        f"[bg][ovl]overlay=0:0:shortest=1[out]"
    )
    r = subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", raw_photo,
        "-loop", "1", "-i", overlay_png,
        "-filter_complex", fc,
        "-map", "[out]",
        "-t", dur_s,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-an", out_path,
    ], capture_output=True, timeout=120)
    return r.returncode == 0 and os.path.exists(out_path)


def _upload_short(video_path: str, item: dict, lang: str) -> str | None:
    """Short videoni YouTube ga yuklash (9:16, ≤60s)."""
    try:
        from youtube_maker import youtube_auth
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return None
    try:
        youtube = youtube_auth(lang)
    except Exception as e:
        print(f"  Short upload auth xato ({lang}): {e}"); return None

    today    = date.today().strftime("%d.%m.%Y")
    sarlavha = _iget(item, "sarlavha", lang)
    yt_title = f"{sarlavha} | {today}"[:100]

    htags = {
        "uz": "#Shorts #Yangiliklar #BreakingNews #1KUN #Dunyo #Siyosat",
        "ru": "#Shorts #Новости #BreakingNews #1ДЕНЬ #Мир #Политика",
        "en": "#Shorts #News #BreakingNews #1DAY #World #Politics",
    }.get(lang, "#Shorts #News")
    desc = f"{sarlavha}\n\n{htags}\n#News2026"

    kw   = item.get("keywords_en", [])
    tags = kw[:8] + ["Shorts", "News", "BreakingNews", "2026", "World"]

    body = {
        "snippet": {
            "title":           yt_title,
            "description":     desc[:4900],
            "tags":            tags,
            "categoryId":      "25",
            "defaultLanguage": lang,
        },
        "status": {
            "privacyStatus":           "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    print(f"   → Short yuklash: {yt_title[:60]}")
    try:
        media    = MediaFileUpload(video_path, mimetype="video/mp4",
                                   resumable=True, chunksize=5 * 1024 * 1024)
        req      = youtube.videos().insert(part="snippet,status",
                                           body=body, media_body=media)
        response = None
        while response is None:
            _, response = req.next_chunk()
        vid_id = response.get("id", "")
        print(f"   ✅ Short: https://youtu.be/{vid_id}")
        return vid_id
    except Exception as e:
        print(f"   Short upload xato: {e}"); return None


def create_short_from_item(item: dict, lang: str) -> str | None:
    """
    Bitta yangilikdan YouTube Short yaratish (9:16, ~55s).
    digest_pipeline() dan har bir til uchun chaqiriladi.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S%f")[:18]
    print(f"   📱 Short [{lang.upper()}]: {item.get('sarlavha','')[:50]}")
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    sarlavha = _iget(item, "sarlavha", lang)
    location = _iget(item, "location", lang)
    daraja   = item.get("daraja", "xabar")
    art_url  = item.get("article_url", "")
    kw       = item.get("keywords_en", [])
    en_title = (_iget(item, "sarlavha", "en") or item.get("en_title", ""))
    jumla1   = _iget(item, "jumla", lang)
    stats    = _extract_stats(jumla1 + " " + _iget(item, "scripts", lang)[:200])

    all_temps = []

    # 1. Rasm
    photo_path  = None
    seen_pexels = set()
    og_path     = os.path.join(TEMP_DIR, f"sh_og_{ts}.jpg")
    if _fetch_og_image(art_url, og_path):
        photo_path = og_path; all_temps.append(og_path)
    else:
        px_path = os.path.join(TEMP_DIR, f"sh_px_{ts}.jpg")
        for q in ([en_title[:60]] if en_title else []) + [k for k in kw[:3]]:
            if q and all(c.isascii() or not c.isalpha() for c in q):
                if _fetch_pexels(q, px_path, seen_pexels):
                    photo_path = px_path; all_temps.append(px_path); break

    # 2. TTS — to'liq script (55s video davomida gapirishi kerak, ~200+ so'z)
    voice_path = os.path.join(TEMP_DIR, f"sh_voice_{ts}.mp3")
    all_temps.append(voice_path)
    script     = (_iget(item, "scripts", lang) or
                  _iget(item, "jumla",   lang) or
                  sarlavha)
    # UZ script kirill bo'lsa — sarlavhaga fallback (TTS kirill o'qiy olmaydi)
    if lang == "uz" and script:
        _cyr_c = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯЎҚҒҲ"
        _al = [c for c in script if c.isalpha()]
        if _al and sum(1 for c in _al if c in _cyr_c) / len(_al) > 0.40:
            script = sarlavha  # Kirill → faqat sarlavha
    # SHORT_DUR = 55s, ~150+ so'z kerak (3 so'z/sekunda)
    # Script bo'lsa — to'liq ishlatamiz (max 300 so'z), bo'lmasa — sarlavha
    short_text = (" ".join(script.split()[:300]) if len(script.split()) > 50 else script) or sarlavha
    voice_ok   = _make_tts(short_text, lang, daraja, voice_path)

    # 3. Video
    out_name    = f"{ts}_short_{lang}.mp4"
    out_path    = os.path.join(OUTPUT_DIR, out_name)
    silent_path = os.path.join(TEMP_DIR, f"sh_sil_{ts}.mp4")
    all_temps.append(silent_path)

    if photo_path and os.path.exists(photo_path):
        raw_bg  = os.path.join(TEMP_DIR, f"sh_bg_{ts}.jpg")
        ovl_png = os.path.join(TEMP_DIR, f"sh_ovl_{ts}.png")
        all_temps += [raw_bg, ovl_png]
        _crop_resize_photo_vertical(photo_path, raw_bg)
        _make_short_overlay_png(sarlavha, location, daraja, stats, lang, ovl_png)
        ok = _photo_to_short_video(raw_bg, ovl_png, SHORT_DUR, silent_path)
    else:
        # Fallback: to'q karta
        card_path = os.path.join(TEMP_DIR, f"sh_card_{ts}.jpg")
        all_temps.append(card_path)
        cimg = Image.new("RGB", (SHORT_W, SHORT_H), C_BG)
        _gradient_rect(ImageDraw.Draw(cimg), 0, 0, SHORT_W, SHORT_H, C_BG, C_DARK)
        cd = ImageDraw.Draw(cimg)
        cd.rectangle([(0, 0), (SHORT_W, 80)], fill=(8, 8, 8))
        cd.rectangle([(0, 77), (SHORT_W, 80)], fill=C_RED)
        cd.rectangle([(0, 0), (6, SHORT_H)], fill=C_RED)
        cd.text((SHORT_W // 2, 40), "1DAY GLOBAL", font=_font(36), fill=C_WHITE, anchor="mm")
        ty2 = 500
        for line in textwrap.wrap(sarlavha, width=20)[:4]:
            cd.text((SHORT_W // 2, ty2), line, font=_font(52), fill=C_WHITE, anchor="mm")
            ty2 += 66
        cd.rectangle([(0, SHORT_H - 60), (SHORT_W, SHORT_H)], fill=(8, 8, 8))
        cd.rectangle([(0, SHORT_H - 60), (SHORT_W, SHORT_H - 58)], fill=C_RED)
        cd.text((SHORT_W // 2, SHORT_H - 30), "#SHORTS  ·  1DAY GLOBAL",
                font=_font(22), fill=C_LGRAY, anchor="mm")
        cimg.save(card_path, "JPEG", quality=92)
        ok = _still_to_video_vertical(card_path, SHORT_DUR, silent_path)

    if not ok:
        for p in all_temps:
            try:
                if p and os.path.exists(p): os.remove(p)
            except Exception: pass
        return None

    # 4. Audio mix
    if voice_ok and os.path.exists(voice_path):
        mix_ok = _mix_audio(silent_path, voice_path, out_path, lang)
    else:
        mix_ok = False

    if not mix_ok:
        shutil.copy(silent_path, out_path)

    # 5. Tozalash
    for p in all_temps:
        try:
            if p and os.path.exists(p): os.remove(p)
        except Exception: pass

    if not os.path.exists(out_path):
        return None

    sz = os.path.getsize(out_path) / 1_048_576
    print(f"   ✅ Short: {out_name}  ({sz:.1f} MB, {SHORT_DUR:.0f}s)")

    # 6. YouTube yuklash
    _upload_short(out_path, item, lang)

    return out_path


# ─────────────────────────────────────────────────────────────
# YouTube yuklash
# ─────────────────────────────────────────────────────────────
def _upload_digest(video_path: str, items: list, lang: str) -> str | None:
    try:
        from youtube_maker import youtube_auth
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        print(f"  YouTube import xato: {e}")
        return None
    try:
        youtube = youtube_auth(lang)
    except Exception as e:
        print(f"  YouTube auth xato ({lang}): {e}")
        return None

    today     = date.today().strftime("%d.%m.%Y")
    n         = len(items)
    chan_name = "1DAY GLOBAL"
    digest_label = {"uz": "Yangiliklar dayjesti", "ru": "Дайджест новостей", "en": "News Digest"}.get(lang, "News Digest")

    # SEO sarlavha: eng muhim yangilik sarlavhasi + "| X ta yangilik"
    first_title = _iget(items[0], "sarlavha", lang)
    count_str   = {"uz": f"{n} ta yangilik", "ru": f"{n} новости", "en": f"{n} stories"}.get(lang, f"{n} news")
    yt_title    = f"{first_title} | {count_str} | {today}"[:100]

    # Tavsif
    intro = {
        "uz": f"{chan_name} | {digest_label} | {today}\n\n",
        "ru": f"{chan_name} | {digest_label} | {today}\n\n",
        "en": f"{chan_name} | {digest_label} | {today}\n\n",
    }.get(lang, "")

    story_lines = []
    for i, item in enumerate(items, 1):
        story_lines.append(f"{i}. {_iget(item, 'sarlavha', lang)}")
        jumla_txt = _iget(item, "jumla", lang)
        if jumla_txt:
            story_lines.append(f"   {jumla_txt[:120]}")
        story_lines.append("")

    hashtags = {
        "uz": "#Yangiliklar #BreakingNews #1KUN #Dunyo #Siyosat #Dayjest",
        "ru": "#Новости #BreakingNews #1ДЕНЬ #Мир #Политика #Дайджест",
        "en": "#News #BreakingNews #1DAY #World #Politics #Digest",
    }.get(lang, "")

    desc = intro + "\n".join(story_lines) + f"\n{'━'*30}\n{hashtags}\n#News2026"
    desc = desc[:4900]

    # Tags
    all_kw = []
    for item in items:
        all_kw += item.get("keywords_en", [])
    tags = list(dict.fromkeys(all_kw))[:12] + ["News", "BreakingNews", "2026", "World", "Politics"]

    body = {
        "snippet": {
            "title":           yt_title,
            "description":     desc,
            "tags":            tags,
            "categoryId":      "25",
            "defaultLanguage": lang,
        },
        "status": {
            "privacyStatus":           "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    print(f"  → YouTube yuklash: {yt_title[:60]}")
    try:
        media   = MediaFileUpload(video_path, mimetype="video/mp4",
                                  resumable=True, chunksize=5 * 1024 * 1024)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"     Upload: {int(status.progress()*100)}%", end="\r")
        vid_id = response.get("id", "")
        print(f"\n     ✅ https://youtu.be/{vid_id}")

        playlist_id = YOUTUBE_PLAYLIST.get(lang, "").strip()
        if vid_id and playlist_id:
            try:
                youtube.playlistItems().insert(
                    part="snippet",
                    body={"snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": vid_id},
                    }},
                ).execute()
                print(f"     📋 Playlist ({lang.upper()})")
            except Exception as pe:
                print(f"  Playlist xato: {pe}")
        return vid_id
    except Exception as e:
        print(f"  Upload xato: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# Temp tozalash
# ─────────────────────────────────────────────────────────────
def _cleanup(ts: str, paths: list):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    prefix = ts[:14]
    for ext in ("jpg", "jpeg", "mp4", "mp3", "aac"):
        for f in glob.glob(os.path.join(TEMP_DIR, f"dg_*_{prefix}*.{ext}")):
            try:
                os.remove(f)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────
# MULTI-VOICE AUDIO MIX  (har yangilik o'z vaqtida gapiradi)
# ─────────────────────────────────────────────────────────────
def _mix_multi_voice(video_path: str,
                     voices: list,       # [(voice_mp3_path, start_sec), ...]
                     out_path: str,
                     lang: str) -> bool:
    """
    Har yangilik ovozi o'z vaqtida (adelay) eshitiladi.
    Fon musiqasi butun video davomida (shu jumladan outro) yangradi.
    TTS tugagandan keyin ham musiqa kamida 5s davom etadi.
    """
    fx         = AUDIO_FX.get(lang, AUDIO_FX.get("uz", "volume=1.0"))
    music_path = _get_music()
    vid_dur    = _audio_dur(video_path)
    if vid_dur < 1:
        return False

    valid = [(vp, vs) for vp, vs in voices if vp and os.path.exists(vp)]
    if not valid:
        return False

    # Har bir ovozning real tugash vaqtini hisoblaymiz
    # va barcha ovozlar tugaguncha video davom etishini ta'minlaymiz
    max_voice_end = 0.0
    for vp, start_t in valid:
        v_dur = _audio_dur(vp)
        max_voice_end = max(max_voice_end, start_t + v_dur)
    # Video davomiyligi: concat videosi yoki oxirgi ovoz + 1.5s bufer — qaysi kattaroq bo'lsa
    out_dur = max(vid_dur, max_voice_end + 1.5)

    cmd = ["ffmpeg", "-y", "-i", video_path]
    for vp, _ in valid:
        cmd += ["-i", vp]

    has_music = bool(music_path and os.path.exists(music_path))
    music_idx = len(valid) + 1
    if has_music:
        cmd += ["-stream_loop", "-1", "-i", music_path]

    fc   = []
    vlabels = []
    for i, (vp, start_t) in enumerate(valid):
        delay_ms = max(0, int(start_t * 1000))
        lbl = f"v{i}"
        fc.append(f"[{i+1}:a]{fx},adelay={delay_ms}|{delay_ms}[{lbl}]")
        vlabels.append(f"[{lbl}]")

    # Barcha ovozlarni birlashtirish
    fc.append(
        f"{''.join(vlabels)}amix=inputs={len(vlabels)}:"
        f"normalize=0:duration=longest[allv_raw]"
    )
    # Ovoz (TTS) tugagandan keyin audio oxirigacha uzaytirish (musiqa davom etsin)
    # out_dur = max(vid_dur, barcha ovozlar tugash vaqti + buffer)
    fc.append(f"[allv_raw]apad=whole_dur={out_dur:.3f}[allv]")

    if has_music:
        # Musiqa: video davomida past (background), oxirgi 3s da fade out
        fade_out_st = max(0.0, out_dur - 3.0)
        vol_expr = (
            f"if(lt(t,{fade_out_st:.2f}),"
            f"{MUSIC_VOL:.3f},"
            f"max(0.0,{MUSIC_VOL:.3f}*(1.0-(t-{fade_out_st:.2f})/3.0)))"
        )
        fc.append(
            f"[{music_idx}:a]aresample=44100,"
            f"atrim=duration={out_dur:.3f},"
            f"volume=volume='{vol_expr}':eval=frame[mus]"
        )
        # duration=first → [allv] (out_dur uzunlikda) tugaganda to'xtaydi
        fc.append("[allv][mus]amix=inputs=2:duration=first[aout]")
        map_a = "[aout]"
    else:
        map_a = "[allv]"

    cmd += [
        "-filter_complex", ";".join(fc),
        "-map", "0:v", "-map", map_a,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "160k",
        "-t", f"{out_dur:.3f}",
        "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        print("  Multi-voice xato:", r.stderr.decode("utf-8", errors="replace")[-300:])
    return r.returncode == 0 and os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# ASOSIY FUNKSIYA
# ─────────────────────────────────────────────────────────────
def digest_pipeline(items: list, lang: str) -> str | None:
    """
    items: [
      {
        "sarlavha":    str,
        "jumla1":      str,
        "script":      str,     # naratsiya matni (300 so'z)
        "location":   str,
        "daraja":     str,
        "article_url": str,
        "keywords_en": list,
        "en_title":    str,     # Pexels uchun EN sarlavha
      }, ...
    ]
    lang: "uz" | "ru" | "en"
    """
    items = [it for it in items[:MAX_ITEMS]
             if it.get("sarlavha") or it.get("scripts")]
    n     = len(items)
    if n < MIN_ITEMS:
        print(f"  ⚠️  Kamida {MIN_ITEMS} ta yangilik kerak (bor: {n})")
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S%f")[:18]
    print(f"\n  📺 Digest pipeline [{lang.upper()}]: {n} ta yangilik")
    os.makedirs(TEMP_DIR,   exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_temps   = []
    segments    = []
    durations   = []
    seen_pexels = set()

    # Per-item ovoz va boshlanish vaqtlari
    voice_info  = []   # [(voice_mp3_path, start_sec_in_final_video), ...]
    current_t   = 0.0  # Joriy vaqt (xfade hisobga olingan)

    # ── OCHILISH KARTI YO'Q — darhol birinchi yangilikdan boshlanadi ──
    # (Foydalanuvchi so'rovi bo'yicha intro olib tashlandi)

    # ── 2. HAR BIR YANGILIK ───────────────────────────────────
    for idx, item in enumerate(items):
        story_num = idx + 1
        sarlavha  = _iget(item, "sarlavha", lang)
        location  = _iget(item, "location", lang)
        daraja    = item.get("daraja", "xabar")
        art_url   = item.get("article_url", "")
        kw        = item.get("keywords_en", [])
        en_title  = (_iget(item, "sarlavha", "en") or
                     item.get("en_title", ""))
        jumla1    = _iget(item, "jumla", lang)
        next_title = _iget(items[idx + 1], "sarlavha", lang) if idx + 1 < n else ""
        stats     = _extract_stats(jumla1 + " " + _iget(item, "scripts", lang)[:300])

        # ── UZ axlat tekshiruvi: sarlavha/jumla1 yomon bo'lsa RU→UZ qayta tarjima ──
        if lang == "uz":
            sarlavha_ru = _iget(item, "sarlavha", "ru") or ""
            jumla1_ru   = _iget(item, "jumla",    "ru") or ""

            if _is_garbage_uz(sarlavha):
                log.info(f"  🔄 sarlavha_uz axlat → RU→UZ tuzatish...")
                fixed_sv = _fix_uz_from_ru(sarlavha_ru, en_title)
                if fixed_sv:
                    log.info(f"     ✓ {fixed_sv[:60]}")
                    sarlavha = fixed_sv
                    item = dict(item); item["sarlavha"] = fixed_sv

            if _is_garbage_uz(jumla1):
                log.info(f"  🔄 jumla1_uz axlat → RU→UZ tuzatish...")
                fixed_j1 = _fix_uz_from_ru(jumla1_ru, en_title)
                if fixed_j1:
                    log.info(f"     ✓ {fixed_j1[:80]}")
                    jumla1 = fixed_j1

        print(f"  ─ Yangilik {story_num}/{n}: {sarlavha[:55]}")

        # -- 2a. Per-item TTS: faqat body_text (script/jumla1)
        # sarlavha TTS ga QЎШILMAYDI — script o'zi sarlavhani o'z ichiga oladi,
        # ikki marta o'qib berilishini oldini olish uchun
        tts_parts = []

        # Script (to'liq naratsiya) — jumla1 dan ustunroq
        script_text = _iget(item, "scripts", lang) or _iget(item, "script", lang) or ""
        script_text = script_text.strip()

        # Intro/outro iboralarini keng tozalash
        _intro_rx = re.compile(
            r"^(?:"
            r"Efirda\s+1KUN[^.]*\.|"
            r"В\s+эфире\s+1ДЕНЬ[^.]*\.|"
            r"This\s+is\s+1DAY[^.]*\.|"
            r"Assalomu\s+alaykum[^.]*\.|"
            r"Salom\s+aziz[^.]*\.|"
            r"Xurmatli\s+tomoshabinlar[^.]*\.|"
            r"Hurmatli\s+tomoshabinlar[^.]*\.|"
            r"Diqqatingizga\s+taqdim\s+etamiz[^.]*\.|"
            r"Bugungi\s+diJest[^.]*\.|"
            r"Bugungi\s+yangiliklar[^.]*\.|"
            r"Здравствуйте[^.]*\.|"
            r"Добрый\s+(?:день|вечер|утро)[^.]*\.|"
            r"Уважаемые\s+зрители[^.]*\.|"
            r"Представляем\s+вашему\s+вниманию[^.]*\.|"
            r"Good\s+(?:morning|evening|afternoon)[^.]*\.|"
            r"Dear\s+viewers[^.]*\.|"
            r"Welcome\s+to[^.]*\."
            r")\s*",
            re.IGNORECASE
        )
        for _ in range(3):   # Bir nechta intro gap bo'lishi mumkin
            script_text = _intro_rx.sub("", script_text).strip()

        # Outro iboralarini ham tozalash (oxirida)
        _outro_rx = re.compile(
            r'(?:\s*[\.\!\?])?\s*'
            r'(?:'
            r'Obuna\s+bo\'ling[^.]*|'
            r"O'z\s+kanalimizga[^.]*|"
            r'Like\s+bosing[^.]*|'
            r'Kanali[mn]izga\s+obuna[^.]*|'
            r'Xayrli\s+kun[^.]*|'
            r'Keyingi\s+(?:xabar|yangilik)[^.]*|'
            r'Podpisyvayties[^.]*|'
            r'Подписывайтесь[^.]*|'
            r'Ставьте\s+лайк[^.]*|'
            r'До\s+свидания[^.]*|'
            r'Хорошего\s+дня[^.]*|'
            r'Subscribe[^.]*|'
            r'Like\s+and[^.]*|'
            r'Stay\s+tuned[^.]*|'
            r'Next\s+up[^.]*|'
            r'Moving\s+on[^.]*'
            r')'
            r'[\.\!\?]?\s*$',
            re.IGNORECASE
        )
        for _ in range(3):
            script_text = _outro_rx.sub("", script_text).strip()

        # ── UZ script kirill bo'lsa — TTS o'qiy olmaydi → tozalash ──
        if lang == "uz" and script_text:
            _cyr_chars = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯЎҚҒҲ"
            _alpha = [c for c in script_text if c.isalpha()]
            if _alpha and sum(1 for c in _alpha if c in _cyr_chars) / len(_alpha) > 0.40:
                log.info(f"  ⚠️  UZ script kirill ({sum(1 for c in _alpha if c in _cyr_chars)/len(_alpha):.0%}) → o'chirildi, yangi generatsiya qilinadi")
                script_text = ""   # Kirill UZ TTS uchun yaroqsiz

        body_text = script_text or jumla1 or ""

        # Script qisqa bo'lsa (<60 so'z) — AI bilan yangi script yaratish
        if len(body_text.split()) < 60:
            log.info(f"  🔄 Script qisqa ({len(body_text.split())} so'z) → AI bilan generatsiya...")
            generated = _generate_script(en_title, jumla1, lang)
            if generated and len(generated.split()) >= 50:
                body_text = generated
            else:
                # AI ham ishlamasa — jumla1 + jumla2 birlashtirish
                jumla2 = _iget(item, "jumla2", lang) or _iget(item, "jumla", lang) or ""
                fallback = " ".join(filter(None, [jumla1, jumla2])).strip()
                if len(fallback.split()) >= 10:
                    log.info(f"  ⚠️  AI muvaffaqiyatsiz → jumla1+jumla2 fallback ({len(fallback.split())} so'z)")
                    body_text = fallback
                elif sarlavha:
                    # Eng oxirgi chora: sarlavhani kengaytirish
                    log.info(f"  ⚠️  Hech narsa yo'q → sarlavha takrorlash")
                    body_text = sarlavha

        if body_text and body_text.strip() != sarlavha.strip():
            # 220 so'z ≈ ~90-110 soniya (1.5 daqiqa) — minimal 30s kafolat
            body_words = body_text.split()[:220]
            tts_parts.append(" ".join(body_words))
        elif body_text:
            # body_text sarlavha bilan bir xil bo'lsa — baribir qo'shish (TTS bo'sh qolmasin)
            tts_parts.append(body_text.strip())

        tts_text = ". ".join(tts_parts) if tts_parts else (sarlavha or "")
        if not tts_text or not tts_text.strip():
            log.warning(f"  ⚠️  {story_num}-yangilik TTS matni bo'sh! sarlavha ishlatiladi.")
            tts_text = sarlavha or en_title or ""

        voice_i   = os.path.join(TEMP_DIR, f"dg_voice_{ts}_{idx:02d}.mp3")
        all_temps.append(voice_i)
        tts_ok    = _make_tts(tts_text, lang, daraja, voice_i) if tts_text else False
        tts_dur   = _audio_dur(voice_i) if tts_ok and os.path.exists(voice_i) else 0.0

        # Segment davomiyligi = TTS + 2s buffer (min 30s — har yangilik kamida yarim daqiqa)
        seg_dur   = max(tts_dur + 2.0, 30.0) if tts_dur > 0 else 35.0

        # Bu item ovozi shu vaqtdan boshlanadi
        voice_info.append((voice_i if tts_ok else None, current_t))
        current_t += seg_dur - TRANS_DUR   # Keyingi item xfade bilan boshlanganda

        # -- 2b. Rasm yuklash (RELEVANCE CHECK: shaxs/joy nomi bo'yicha)
        photo_path = None
        person_q   = _extract_person_queries(en_title)   # infografik uchun ham kerak
        og_path    = os.path.join(TEMP_DIR, f"dg_og_{ts}_{idx:02d}.jpg")
        if _fetch_og_image(art_url, og_path):
            photo_path = og_path
            all_temps.append(og_path)
            print(f"     📰 og:image olindi")
        else:
            # Pexels dan qidiramiz — RELEVANCE PRIORITY:
            # 1. Sarlavhadagi shaxs/joy nomlari (eng aniq)
            # 2. To'liq sarlavha
            # 3. Kalit so'zlar
            px_path  = os.path.join(TEMP_DIR, f"dg_px_{ts}_{idx:02d}.jpg")
            queries  = []
            # Birinchi: shaxs/joy nomlari (eng muvofiq rasm uchun)
            for pq in person_q:
                if all(c.isascii() or not c.isalpha() for c in pq):
                    queries.append(pq)
            # Ikkinchi: to'liq sarlavha
            if en_title and all(c.isascii() or not c.isalpha() for c in en_title):
                queries.append(en_title[:60])
            # Uchinchi: kalit so'zlar
            for k in kw[:3]:
                if k and all(c.isascii() or not c.isalpha() for c in k):
                    queries.append(k)
            for q in queries:
                if _fetch_pexels(q, px_path, seen_pexels):
                    photo_path = px_path
                    all_temps.append(px_path)
                    print(f"     📸 Pexels: {q[:40]}")
                    break

        # -- 2c. Foto overlay + video
        ovl_img  = os.path.join(TEMP_DIR, f"dg_ovl_{ts}_{idx:02d}.jpg")
        seg_vid  = os.path.join(TEMP_DIR, f"dg_seg_{ts}_{idx:02d}.mp4")
        all_temps += [ovl_img, seg_vid]

        if photo_path and os.path.exists(photo_path):
            # 2c-i. Fon rasmi (matn yo'q)
            raw_bg  = os.path.join(TEMP_DIR, f"dg_bg_{ts}_{idx:02d}.jpg")
            # 2c-ii. Overlay PNG (shaffof, matn bilan)
            ovl_png = os.path.join(TEMP_DIR, f"dg_ovl_{ts}_{idx:02d}.png")
            all_temps += [raw_bg, ovl_png]

            bg_ok  = _crop_resize_photo(photo_path, raw_bg)
            _make_photo_overlay_png(
                sarlavha, location, daraja,
                stats, lang, next_title, story_num, n, ovl_png
            )
            if bg_ok and _photo_to_video_composite(raw_bg, ovl_png, seg_dur, idx, seg_vid):
                segments.append(seg_vid)
                durations.append(seg_dur)
                print(f"     ✓ Segment {seg_dur:.1f}s + overlay")

                # -- 2d. INFOGRAFIK SLAYD — O'CHIRILGAN
                # Yangiliklar orasida qo'shimcha slayd kerak emas:
                # yangilikdan yangilikka to'g'ridan-to'g'ri o'tish kerak
                if False:  # stats or person_q:
                    pass
                continue

        # Fallback: sarlavha karta (matn baked-in, statik)
        fb_img = os.path.join(TEMP_DIR, f"dg_fb_{ts}_{idx:02d}.jpg")
        all_temps.append(fb_img)
        _make_story_title_card(sarlavha, location, daraja, story_num, n, lang, fb_img)
        if _still_to_video(fb_img, seg_dur, seg_vid):
            segments.append(seg_vid)
            durations.append(seg_dur)
            print(f"     ✓ Segment {seg_dur:.1f}s (karta fallback)")

    # ── 3. YAKUNLASH YO'Q — foydalanuvchi so'rovi bo'yicha outro olib tashlandi ──
    # (barcha video turlaridan intro/outro olib tashlangan)

    if not segments:
        print("  ⚠️  Hech bir segment yaratilmadi")
        return None

    # ── 4. CONCAT ─────────────────────────────────────────────
    concat_vid = os.path.join(TEMP_DIR, f"dg_concat_{ts}.mp4")
    all_temps.append(concat_vid)
    if not _concat_xfade(segments, durations, concat_vid):
        print("  ⚠️  Concat xato")
        _cleanup(ts, all_temps)
        return None
    print(f"  ✓ Concat: {len(segments)} segment")

    # ── 5. MULTI-VOICE AUDIO MIX ──────────────────────────────
    # Har yangilik ovozi o'z rasmi ko'rsatilayotganda eshitiladi (adelay)
    out_name = f"{ts}_digest_{lang}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    ok = _mix_multi_voice(concat_vid, voice_info, out_path, lang)

    if not ok:
        shutil.copy(concat_vid, out_path)
        print("  ℹ️  Multi-voice mix xato — faqat video")

    # ── 7. TOZALASH ───────────────────────────────────────────
    _cleanup(ts, all_temps)

    if not os.path.exists(out_path):
        print("  ⚠️  Yakuniy video topilmadi")
        return None

    sz  = os.path.getsize(out_path) / 1_048_576
    dur = sum(durations)
    print(f"\n  ✅ {out_name}  ({sz:.1f} MB, ~{dur:.0f}s)")

    # ── 8. YOUTUBE YUKLASH — upload_pending.py orqali (ikki marta yuklash yo'q) ──
    # digest_maker.py video yaratadi va saqlaydi → upload_pending.py yuklaydi
    # Bu yerda to'g'ridan-to'g'ri yuklamaslik uchun o'chirildi
    yt_vid_id = None
    yt_url    = ""

    # ── 9. SHORT YARATISH (har digest bilan birga) ────────────
    best_item = None
    for priority in ("muhim", "tezkor", "xabar"):
        for it in items:
            if it.get("daraja") == priority:
                best_item = it; break
        if best_item:
            break
    if not best_item and items:
        best_item = items[0]

    short_path = None
    if best_item:
        try:
            short_path = create_short_from_item(best_item, lang)
        except Exception as _se:
            print(f"   ⚠️  Short xato: {_se}")

    # ── 10. TELEGRAM + FACEBOOK POSTLASH (shu til kanali) ────────
    # ⚠️ VAQTINCHA O'CHIRILGAN — foydalanuvchi so'rovi bo'yicha
    # YouTube digest videolar Telegram kanaliga yuborilmasin (faqat YouTube'ga)
    if False:
        log.info(f"  📤 Telegram+Facebook postlash boshlandi [{lang.upper()}]...")
    else:
        log.info(f"  ⏸️  Telegram/FB post o'chirilgan (foydalanuvchi sozlamasi) [{lang.upper()}]")
    try:
        from social_poster import post_telegram_video
        daraja_tg   = items[0].get("daraja", "xabar") if items else "xabar"

        # Digest Telegram caption: barcha maqolalar ro'yxati (1-chi maqola kabi ko'rinmaydi)
        from datetime import datetime as _dtt
        import pytz as _pytz
        _tz = _pytz.timezone("Asia/Tashkent")
        _vaqt = _dtt.now(_tz).strftime("🕐 %H:%M | %d.%m.%Y")
        _n = len(items)
        # 1 ta yangilik bo'lsa "DIGEST" emas, oddiy "YANGILIK" deyiladi
        if _n == 1:
            _digest_title = {
                "uz": "📰 YANGILIK",
                "ru": "📰 НОВОСТЬ",
                "en": "📰 NEWS",
            }.get(lang, "📰 NEWS")
        else:
            _digest_title = {
                "uz": f"📋 YANGILIKLAR DAYJESTI — {_n} ta yangilik",
                "ru": f"📋 ДАЙДЖЕСТ НОВОСТЕЙ — {_n} новости",
                "en": f"📋 NEWS DIGEST — {_n} stories",
            }.get(lang, f"📋 DIGEST — {_n} stories")
        _kanal = {
            "uz": "@birkunday",
            "ru": "@birkunday_ru",
            "en": "@birkunday_en",
        }.get(lang, "")

        _lines = [f"<b>{_digest_title}</b>", ""]
        for _i, _it in enumerate(items, 1):
            _sv = _iget(_it, "sarlavha", lang)
            _j1 = _iget(_it, "jumla", lang)
            _emoji = {"muhim": "🔴", "tezkor": "🟠"}.get(_it.get("daraja",""), "🟢")
            if _sv:
                _lines.append(f"{_i}. {_emoji} <b>{_sv[:80]}</b>")
            if _j1:
                _short_j1 = (_j1[:120] + "…") if len(_j1) > 120 else _j1
                _lines.append(f"   {_short_j1}")
            _lines.append("")
        _lines.append(_vaqt)
        if yt_url:
            _lines.append(f"▶️ {yt_url}")
        _lines.append(f"📰 {_kanal}")
        _htag = {
            "uz": "#Yangiliklar #Dayjest #1KUN",
            "ru": "#Новости #Дайджест #1День",
            "en": "#News #Digest #1Day",
        }.get(lang, "#Digest")
        _lines.append(f"\n{_htag}")
        digest_caption_tg = "\n".join(_lines)[:1020]

        # Digest video → Telegram (message_id qaytaradi)
        tg_channel = {
            "uz": "birkunday",
            "ru": "birkunday_ru",
            "en": "birkunday_en",
        }.get(lang, "")

        # ⚠️ YouTube digest video Telegram'ga yuborilmasin (foydalanuvchi so'rovi)
        # Telegram bot o'z yangiliklarini alohida postlaydi
        tg_msg_id = None
        # tg_msg_id = post_telegram_video(
        #     video_path = out_path,
        #     sarlavha   = "",
        #     jumla      = "",
        #     lang       = lang,
        #     daraja     = daraja_tg,
        #     yt_url     = yt_url,
        #     location   = "",
        #     caption    = digest_caption_tg,
        # )
        tg_post_url = ""

        # FB/IG uchun sarlavha va jumla (1-maqola)
        sarlavha_tg = _iget(items[0], "sarlavha", lang) if items else ""
        jumla_tg    = _iget(items[0], "jumla",    lang) if items else ""
        loc_tg      = _iget(items[0], "location", lang) if items else ""

        # Short → Telegram — o'chirilgan (digest allaqachon yuborildi, 2-chi post kerak emas)
        # if short_path and os.path.exists(short_path): ...

        # ── Facebook: UZ va RU Telegram bilan birga ─────────
        # EN Facebook'ga yuklanmaydi
        if lang in ("uz", "ru"):
            from social_poster import post_facebook_text, post_facebook_yt_link
            print(f"   📘 Facebook Sahifa ({lang.upper()}) matnli post...")

            # Telegram UZ/RU → Facebook matnli post + Telegram havolasi
            post_facebook_text(
                sarlavha    = sarlavha_tg,
                jumla       = jumla_tg,
                lang        = lang,
                daraja      = daraja_tg,
                yt_url      = yt_url,
                location    = loc_tg,
                tg_post_url = tg_post_url,   # https://t.me/birkunday/1097
            )

            # YouTube UZ/RU → Facebook link post (yt_url bo'lsa)
            if yt_url:
                print(f"   🔗 Facebook YouTube link elon qilinmoqda...")
                post_facebook_yt_link(
                    yt_url      = yt_url,
                    title       = sarlavha_tg[:100],
                    description = jumla_tg,
                    lang        = lang,
                    daraja      = daraja_tg,
                    location    = loc_tg,
                )

    except Exception as _tg_e:
        log.error(f"   ⚠️  Telegram/Facebook post xato: {_tg_e}", exc_info=True)

    # yt_url va short_path ni tashqariga chiqaramiz (app.py uchun)
    return out_path, yt_url, short_path
