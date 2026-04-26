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
PHOTO_DUR     = 56      # Har bir yangilik foto (statik) — ~1 daqiqa per story
OUTRO_DUR     = 4       # Yakunlash kartasi
TRANS_DUR     = 0.5     # Crossfade
MAX_ITEMS     = 6       # Bir videoda maksimal yangilik soni
MIN_ITEMS     = 1       # Minimum yangilik
WORDS_PER_STORY = 120   # Har bir yangilik naratsiya so'zlari (~50s speech)
MUSIC_VOL     = 0.28    # Fon musiqasi balandligi (eshitilarli bo'lsin)

_HERE = os.path.dirname(os.path.abspath(__file__))

# Rang palitasi
C_BG      = (4,   8,  20)   # Qoramtir ko'k fon
C_DARK    = (10,  18,  38)
C_NAVY    = (8,   20,  55)
C_GOLD    = (255, 185,  0)   # Yorqin oltin
C_RED     = (220,  30,  30)  # Qizil
C_WHITE   = (255, 255, 255)
C_LGRAY   = (180, 190, 210)
C_ACCENT  = (0,  140, 255)   # Ko'k aksent
C_GREEN   = (0,  200,  80)   # Yashil
C_TICKER  = (15,  25,  55)   # Ticker foni


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
def _make_open_card(lang: str, story_count: int, out_path: str):
    img  = Image.new("RGB", (VW, VH), C_BG)
    _gradient_rect(ImageDraw.Draw(img), 0, 0, VW, VH, C_BG, C_DARK)
    img  = _draw_map_bg(img, alpha=30)
    draw = ImageDraw.Draw(img)

    # Vertikal aksent chiziqlar
    draw.rectangle([(0, 0), (6, VH)], fill=C_RED)
    draw.rectangle([(VW - 6, 0), (VW, VH)], fill=C_RED)

    # Yuqori gorizontal chiziq
    draw.rectangle([(0, 0), (VW, 5)], fill=C_GOLD)
    draw.rectangle([(0, VH - 5), (VW, VH)], fill=C_GOLD)

    # Kanal nomi
    brand = {
        "uz": "1КУН GLOBAL",
        "ru": "1ДЕНЬ GLOBAL",
        "en": "1DAY GLOBAL",
    }.get(lang, "1KUN GLOBAL")
    _text_shadow(draw, (VW // 2, 200), brand,
                 font=_font(86), fill=C_GOLD, offset=3, anchor="mm")

    # Subtitle
    subtitle = {
        "uz": "ДУНЁ ЯНГИЛИКЛАРИ",
        "ru": "МИРОВЫЕ НОВОСТИ",
        "en": "WORLD NEWS DIGEST",
    }.get(lang, "WORLD NEWS")
    draw.text((VW // 2, 300), subtitle,
              font=_font(36, bold=False), fill=C_LGRAY, anchor="mm")

    # Sana + yangilik soni
    today = date.today().strftime("%d.%m.%Y")
    count_label = {
        "uz": f"{story_count} TA YANGILIK",
        "ru": f"{story_count} НОВОСТИ",
        "en": f"{story_count} STORIES",
    }.get(lang, f"{story_count} STORIES")
    draw.rectangle([(VW // 2 - 160, 370), (VW // 2 + 160, 420)],
                   fill=C_RED)
    draw.text((VW // 2, 395), f"  {today}  |  {count_label}  ",
              font=_font(22), fill=C_WHITE, anchor="mm")

    # Pastki brend chiziq
    draw.text((VW // 2, VH - 35), "youtube.com/@1kunGlobal",
              font=_font(18, bold=False), fill=(*C_GOLD, 150), anchor="mm")

    img.save(out_path, "JPEG", quality=93)
    return out_path


# ─────────────────────────────────────────────────────────────
# KARTA 2: Yangilik sarlavha kartasi (per-story title)
# ─────────────────────────────────────────────────────────────
def _make_story_title_card(
        sarlavha: str, location: str, daraja: str,
        story_num: int, total: int, lang: str, out_path: str):
    img  = Image.new("RGB", (VW, VH), C_BG)
    _gradient_rect(ImageDraw.Draw(img), 0, 0, VW, VH, (6, 12, 30), (2, 5, 18))
    img  = _draw_map_bg(img, alpha=25)
    draw = ImageDraw.Draw(img)

    # Chap aksent bar
    accent = {"muhim": C_RED, "tezkor": (230, 130, 0), "xabar": C_ACCENT}.get(daraja, C_ACCENT)
    draw.rectangle([(0, 0), (8, VH)], fill=accent)

    # Yuqori bar
    draw.rectangle([(0, 0), (VW, 55)], fill=(*C_BG, 230))
    draw.rectangle([(0, 52), (VW, 55)], fill=accent)

    # Kanal nomi yuqorida
    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    draw.text((20, 27), brand, font=_font(24), fill=C_GOLD, anchor="lm")
    draw.text((VW - 20, 27), date.today().strftime("%d.%m.%Y"),
              font=_font(22, bold=False), fill=C_LGRAY, anchor="rm")

    # Yangilik raqami badge (chapda, markazda)
    badge_cx, badge_cy = 80, VH // 2
    for r, a in [(58, 30), (48, 60)]:
        draw.ellipse([(badge_cx - r, badge_cy - r),
                      (badge_cx + r, badge_cy + r)],
                     outline=(*accent, a), width=2)
    draw.ellipse([(badge_cx - 40, badge_cy - 40),
                  (badge_cx + 40, badge_cy + 40)], fill=accent)
    draw.text((badge_cx, badge_cy - 8), str(story_num),
              font=_font(36), fill=C_WHITE, anchor="mm")
    draw.text((badge_cx, badge_cy + 20), f"/{total}",
              font=_font(18, bold=False), fill=(*C_WHITE, 180), anchor="mm")

    # Daraja banner
    banner = {
        "muhim":  "⚡ MUHIM YANGILIK",
        "tezkor": "🔴 TEZKOR",
        "xabar":  "📰 XABAR",
    }.get(daraja, "📰 YANGILIK")
    if lang == "ru":
        banner = {
            "muhim":  "⚡ ГЛАВНАЯ НОВОСТЬ",
            "tezkor": "🔴 СРОЧНО",
            "xabar":  "📰 НОВОСТЬ",
        }.get(daraja, "📰 НОВОСТЬ")
    elif lang == "en":
        banner = {
            "muhim":  "⚡ BREAKING",
            "tezkor": "🔴 URGENT",
            "xabar":  "📰 NEWS",
        }.get(daraja, "📰 NEWS")

    bx = 140
    draw.rectangle([(bx, 75), (bx + len(banner) * 14 + 20, 110)],
                   fill=(*accent, 240))
    draw.text((bx + 10, 92), banner, font=_font(22), fill=C_WHITE, anchor="lm")

    # Sarlavha (katta, yorqin)
    title_text = sarlavha or ""
    wrapped    = textwrap.wrap(title_text, width=32)[:4]
    ty         = 150 if len(wrapped) <= 2 else 130
    for i, line in enumerate(wrapped):
        fs   = 54 if i == 0 else 48
        fill = C_WHITE if i == 0 else C_LGRAY
        _text_shadow(draw, (140, ty), line, font=_font(fs), fill=fill, offset=3)
        ty += fs + 10

    # Geo-marker (o'ng pastda — qorong'i fon, sarlavhaga to'sqinlik yo'q)
    if location:
        _draw_geo_marker(draw, VW - 170, VH - 95, location, lang)

    # Pastki chiziq
    draw.rectangle([(0, VH - 5), (VW, VH)], fill=accent)

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
    Shaffof PNG overlay (RGBA) — qayta dizayn:
      · Chap panel: sana (katta, yorqin) + geo mini-karta (shaffof)
      · Sarlavha to'liq kenglikda (karta to'sqinlik qilmaydi)
      · Progress bar (hikoyalar progressi)
      · Chap aksent glow effekti
      · O'ng tomon — stats (yuqorida, fotodan ajratilgan)
    """
    accent = {"muhim": C_RED, "tezkor": (230, 130, 0), "xabar": C_ACCENT}.get(daraja, C_ACCENT)

    img  = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── 1. CHAP AKSENT BAR + GLOW ─────────────────────────────
    # Asosiy aksent chizig'i
    draw.rectangle([(0, 0), (5, VH)], fill=(*accent, 225))
    # Yumshoq glow (kengroq, pastroq alpha)
    for gw_off, ga in [(14, 55), (24, 28), (36, 12)]:
        draw.rectangle([(5, 0), (5 + gw_off, VH)], fill=(*accent, ga))

    # ── 2. YUQORI BAR ─────────────────────────────────────────
    draw.rectangle([(0, 0), (VW, 44)], fill=(4, 8, 20, 215))
    draw.rectangle([(0, 42), (VW, 45)], fill=(*accent, 235))
    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    draw.text((18, 22), brand, font=_font(22), fill=C_GOLD, anchor="lm")
    # (sana yuqori o'ngdan OLIB TASHLANDI — geo panelga ko'chdi)

    # ── 3. PROGRESS BAR (hikoyalar progressi) ─────────────────
    if total > 1:
        prog_w = int(VW * story_num / total)
        draw.rectangle([(0, 45), (prog_w, 49)], fill=(*accent, 200))
        draw.rectangle([(prog_w, 45), (VW, 49)], fill=(30, 45, 80, 100))
    else:
        draw.rectangle([(0, 45), (VW, 49)], fill=(*accent, 180))

    # ── 4. CHAP PANEL: SANA + GEO MINI-KARTA ─────────────────
    panel_x  = 14         # chap chegaradan 14px
    panel_top = 58        # yuqori bardan pastga

    # 4a. SANA — katta, yorqin
    date_str  = date.today().strftime("%d.%m.%Y")
    date_font = _font(36)
    # Soya
    draw.text((panel_x + 2, panel_top + 2), date_str,
              font=date_font, fill=(0, 0, 0, 120))
    # Asosiy matn (yorqin oltin)
    draw.text((panel_x, panel_top), date_str,
              font=date_font, fill=(255, 220, 40, 255))

    # 4b. GEO MINI-KARTA (kvadrat, shaffof) — sana ostida
    geo_top = panel_top + 50   # sana ostidan
    geo_w, geo_h = 215, 158    # kompakt o'lcham

    if location:
        try:
            from geo_map import draw_geo_card
            import uuid
            tmp_geo = os.path.join(TEMP_DIR, f"dg_geo_{uuid.uuid4().hex[:8]}.png")
            draw_geo_card(location, tmp_geo, card_w=geo_w, card_h=geo_h)
            geo_img = Image.open(tmp_geo).convert("RGBA")
            # 72% shaffoflik (transparent ko'rinish)
            r, g, b, a_ch = geo_img.split()
            a_ch = a_ch.point(lambda p: int(p * 0.72))
            geo_img.putalpha(a_ch)
            img.alpha_composite(geo_img, (panel_x, geo_top))
            try:
                os.remove(tmp_geo)
            except Exception:
                pass
        except Exception:
            # Fallback: minimal karta bloki
            draw.rectangle([(panel_x, geo_top),
                             (panel_x + geo_w, geo_top + geo_h)],
                            fill=(4, 10, 28, 185), outline=(*accent, 140), width=1)
            cx = panel_x + geo_w // 2
            cy = geo_top + (geo_h - 22) // 2
            draw.ellipse([(cx - 7, cy - 7), (cx + 7, cy + 7)], fill=(*C_RED, 230))
            draw.ellipse([(cx - 3, cy - 3), (cx + 3, cy + 3)], fill=(255, 255, 255, 240))
            loc_short = (location[:16]).upper()
            draw.text((cx, cy + 18), loc_short,
                      font=_font(14), fill=(*C_GOLD, 220), anchor="mm")

    # ── 5. STATS — O'NG YUQORI (fotodan uzilgan zona) ─────────
    # Emoji → ASCII belgi (PIL Windows fontlarida emoji ko'rsatmaydi)
    _ICON_MAP = {"📊": "[%]", "💰": "[$]", "🔴": "[!]", "📅": "[D]", "📈": "[N]"}
    if stats:
        sx = VW - 18
        sy = 60
        for stat in stats[:2]:
            val  = stat.get("val", "")
            unit = stat.get("unit", "")
            icon = _ICON_MAP.get(stat.get("icon", ""), "")
            if not val:
                continue
            label   = f"{icon} {val}".strip()
            box_w   = max(120, len(label) * 13 + 22)
            draw.rectangle([(sx - box_w, sy), (sx, sy + 52)],
                            fill=(*C_NAVY, 215))
            draw.rectangle([(sx - box_w, sy), (sx, sy + 3)],
                            fill=(*C_GOLD, 235))
            draw.text((sx - box_w // 2, sy + 26),
                       label,
                       font=_font(26), fill=(255, 255, 255, 248), anchor="mm")
            if unit:
                draw.text((sx - box_w // 2, sy + 44),
                           unit.upper()[:12],
                           font=_font(12, bold=False), fill=(*C_LGRAY, 205), anchor="mm")
            sy += 62

    # ── 6. PASTKI GRADIENT (lower third) ──────────────────────
    grad_h   = 245
    grad_img = Image.new("RGBA", (VW, grad_h), (0, 0, 0, 0))
    g_draw   = ImageDraw.Draw(grad_img)
    for dy in range(grad_h):
        alpha = int(242 * (dy / grad_h) ** 1.25)
        g_draw.line([(0, dy), (VW, dy)], fill=(4, 8, 20, alpha))
    img.paste(grad_img, (0, VH - grad_h), grad_img)

    # ── 7. HIKOYA RAQAMI (doira badge) ────────────────────────
    bx, by = 40, VH - 185
    draw.ellipse([(bx - 30, by - 30), (bx + 30, by + 30)], fill=(*accent, 235))
    draw.text((bx, by), str(story_num), font=_font(30), fill=C_WHITE, anchor="mm")

    # ── 8. SARLAVHA — TO'LIQ KENGLIKDA ───────────────────────
    if sarlavha:
        wrapped = textwrap.wrap(sarlavha, width=50)[:2]
        ty = VH - 178
        for i, line in enumerate(wrapped):
            fs   = 40 if i == 0 else 36
            col  = (255, 255, 255, 250) if i == 0 else (220, 230, 245, 235)
            # Qalin soya (o'qilishi uchun)
            draw.text((78, ty + 3), line, font=_font(fs), fill=(0, 0, 0, 165))
            draw.text((76, ty),     line, font=_font(fs), fill=col)
            ty += fs + 8

    # ── 9. PASTKI TICKER ──────────────────────────────────────
    ticker_h = 38
    y0       = VH - ticker_h
    draw.rectangle([(0, y0), (VW, VH)], fill=(*C_TICKER, 238))
    badge    = f" {story_num}/{total} "
    bw2      = len(badge) * 11 + 8
    draw.rectangle([(0, y0), (bw2, VH)], fill=(*C_RED, 248))
    draw.text((bw2 // 2, y0 + ticker_h // 2), badge,
              font=_font(19), fill=(255, 255, 255, 255), anchor="mm")
    next_label = {"uz": "KEYINGI:", "ru": "ДАЛЕЕ:", "en": "NEXT:"}.get(lang, "NEXT:")
    draw.text((bw2 + 10, y0 + ticker_h // 2), next_label,
              font=_font(18), fill=(*C_GOLD, 248), anchor="lm")
    if next_title:
        short = next_title[:62] + ("…" if len(next_title) > 62 else "")
        draw.text((bw2 + 100, y0 + ticker_h // 2), short,
                  font=_font(18, bold=False), fill=(255, 255, 255, 238), anchor="lm")

    img.save(out_path, "PNG")
    return out_path


# ─────────────────────────────────────────────────────────────
# KARTA 4: Outro kartasi
# ─────────────────────────────────────────────────────────────
def _make_outro_card(lang: str, out_path: str):
    img  = Image.new("RGB", (VW, VH), C_BG)
    _gradient_rect(ImageDraw.Draw(img), 0, 0, VW, VH, C_BG, (2, 5, 18))
    img  = _draw_map_bg(img, alpha=30)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (6, VH)], fill=C_RED)
    draw.rectangle([(VW - 6, 0), (VW, VH)], fill=C_RED)
    draw.rectangle([(0, 0), (VW, 5)], fill=C_GOLD)
    draw.rectangle([(0, VH - 5), (VW, VH)], fill=C_GOLD)

    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    _text_shadow(draw, (VW // 2, VH // 2 - 80), brand,
                 font=_font(78), fill=C_GOLD, offset=3, anchor="mm")

    cta = {
        "uz": ("ОБУНА БЎЛИНГ!", "Ҳар куни дунё янгиликлари — каналимизда"),
        "ru": ("ПОДПИСЫВАЙТЕСЬ!", "Главные новости мира — каждый день"),
        "en": ("SUBSCRIBE NOW!", "World news delivered daily"),
    }.get(lang, ("SUBSCRIBE!", "World news every day"))
    draw.text((VW // 2, VH // 2 + 10), cta[0], font=_font(42), fill=C_WHITE, anchor="mm")
    draw.text((VW // 2, VH // 2 + 60), cta[1], font=_font(22, bold=False),
              fill=C_LGRAY, anchor="mm")

    # Bell icon area
    draw.text((VW // 2, VH // 2 + 110), "🔔  👍  SHARE",
              font=_font(28, bold=False), fill=(*C_GOLD, 200), anchor="mm")

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


def _make_tts(text: str, lang: str, daraja: str, out_path: str) -> bool:
    vcfg = VOICES.get(lang, VOICES["uz"])
    cfg  = vcfg.get(daraja, vcfg.get("default", vcfg.get(list(vcfg.keys())[0])))
    if lang == "uz":
        text = "".join(_CYR2LAT.get(c, c) for c in text)
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
    """720x1280 RGBA overlay (shaffof) — Shorts uchun."""
    accent = {"muhim": C_RED, "tezkor": (230, 130, 0), "xabar": C_ACCENT}.get(daraja, C_ACCENT)
    img  = Image.new("RGBA", (SHORT_W, SHORT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Yuqori panel ────────────────────────────────────────
    draw.rectangle([(0, 0), (SHORT_W, 72)], fill=(4, 8, 20, 225))
    draw.rectangle([(0, 70), (SHORT_W, 74)], fill=(*accent, 230))
    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    draw.text((SHORT_W // 2, 36), brand, font=_font(30), fill=C_GOLD, anchor="mm")

    # ── Chap aksent bar ─────────────────────────────────────
    draw.rectangle([(0, 0), (7, SHORT_H)], fill=(*accent, 200))

    # ── Daraja badge ────────────────────────────────────────
    banner = {"muhim": "⚡ MUHIM", "tezkor": "🔴 TEZKOR", "xabar": "📰 YANGILIK"}.get(daraja, "📰")
    if lang == "ru":
        banner = {"muhim": "⚡ СРОЧНО", "tezkor": "🔴 СРОЧНО", "xabar": "📰 НОВОСТЬ"}.get(daraja, "📰")
    elif lang == "en":
        banner = {"muhim": "⚡ BREAKING", "tezkor": "🔴 URGENT", "xabar": "📰 NEWS"}.get(daraja, "📰")
    bw2 = len(banner) * 15 + 28
    draw.rectangle([(18, 88), (18 + bw2, 134)], fill=(*accent, 245))
    draw.text((18 + bw2 // 2, 111), banner, font=_font(26), fill=C_WHITE, anchor="mm")

    # ── Pastki gradient ─────────────────────────────────────
    grad_h = 550
    grad_img = Image.new("RGBA", (SHORT_W, grad_h), (0, 0, 0, 0))
    g_draw   = ImageDraw.Draw(grad_img)
    for dy in range(grad_h):
        alpha = int(252 * (dy / grad_h) ** 1.15)
        g_draw.line([(0, dy), (SHORT_W, dy)], fill=(4, 8, 20, alpha))
    img.paste(grad_img, (0, SHORT_H - grad_h), grad_img)

    # ── Sarlavha (katta, pastki 3rd) ─────────────────────────
    if sarlavha:
        wrapped = textwrap.wrap(sarlavha, width=20)[:4]
        ty = SHORT_H - 400
        for i, line in enumerate(wrapped):
            fs   = 56 if i == 0 else 50
            fill = (255, 255, 255, 252) if i == 0 else (200, 212, 235, 242)
            draw.text((20, ty + 2), line, font=_font(fs), fill=(0, 0, 0, 150))
            draw.text((18, ty),     line, font=_font(fs), fill=fill)
            ty += fs + 14

    # ── Stats ────────────────────────────────────────────────
    if stats:
        stat = stats[0]
        val  = stat.get("val", ""); unit = stat.get("unit", ""); icon = stat.get("icon", "")
        if val:
            box_w = 210; box_x = SHORT_W - box_w - 8; box_y = SHORT_H - 500
            draw.rectangle([(box_x, box_y), (SHORT_W - 8, box_y + 90)], fill=(*C_NAVY, 220))
            draw.rectangle([(box_x, box_y), (SHORT_W - 8, box_y + 4)], fill=(*C_GOLD, 230))
            draw.text((box_x + box_w // 2, box_y + 34),
                      f"{icon} {val}", font=_font(34), fill=(255, 255, 255, 248), anchor="mm")
            if unit:
                draw.text((box_x + box_w // 2, box_y + 66),
                          unit.upper()[:10], font=_font(17, bold=False),
                          fill=(*C_LGRAY, 210), anchor="mm")

    # ── Geo karta ────────────────────────────────────────────
    if location:
        try:
            from geo_map import draw_geo_card
            import uuid
            tmp_geo = os.path.join(TEMP_DIR, f"sh_geo_{uuid.uuid4().hex[:8]}.png")
            draw_geo_card(location, tmp_geo, card_w=300, card_h=175)
            geo_img = Image.open(tmp_geo).convert("RGBA")
            gw, gh  = geo_img.size
            img.paste(geo_img, (SHORT_W - gw - 6, SHORT_H - gh - 108), geo_img)
            try: os.remove(tmp_geo)
            except: pass
        except Exception:
            pass

    # ── Pastki ticker ────────────────────────────────────────
    ticker_h = 82
    y0 = SHORT_H - ticker_h
    draw.rectangle([(0, y0), (SHORT_W, SHORT_H)], fill=(*C_TICKER, 245))
    draw.rectangle([(0, y0), (SHORT_W, y0 + 3)], fill=(*C_GOLD, 220))
    sub = {
        "uz": "OBUNA BO'LING  🔔  #SHORTS",
        "ru": "ПОДПИШИТЕСЬ  🔔  #SHORTS",
        "en": "SUBSCRIBE  🔔  #SHORTS",
    }.get(lang, "#SHORTS")
    draw.text((SHORT_W // 2, y0 + ticker_h // 2), sub,
              font=_font(23), fill=(*C_GOLD, 248), anchor="mm")

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
        youtube = youtube_auth()
    except Exception as e:
        print(f"  Short upload auth xato: {e}"); return None

    today    = date.today().strftime("%d.%m.%Y")
    sarlavha = _iget(item, "sarlavha", lang)
    yt_title = f"{sarlavha} | {today}"[:100]

    htags = {
        "uz": "#Shorts #Yangiliklar #BreakingNews #1КУН #Дунё #Сиёсат",
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

    # 2. TTS (~90 so'z ≈ 35-40s)
    voice_path = os.path.join(TEMP_DIR, f"sh_voice_{ts}.mp3")
    all_temps.append(voice_path)
    script     = (_iget(item, "scripts", lang) or
                  _iget(item, "jumla",   lang) or
                  sarlavha)
    short_text = " ".join(script.split()[:90]) or sarlavha
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
        brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
        cd.text((SHORT_W // 2, 120), brand, font=_font(42), fill=C_GOLD, anchor="mm")
        ty2 = 500
        for line in textwrap.wrap(sarlavha, width=20)[:4]:
            cd.text((SHORT_W // 2, ty2), line, font=_font(52), fill=C_WHITE, anchor="mm")
            ty2 += 66
        cd.text((SHORT_W // 2, SHORT_H - 80), "#SHORTS",
                font=_font(30), fill=C_GOLD, anchor="mm")
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
        youtube = youtube_auth()
    except Exception as e:
        print(f"  YouTube auth xato: {e}")
        return None

    today     = date.today().strftime("%d.%m.%Y")
    n         = len(items)
    chan_name = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN GLOBAL")
    digest_label = {"uz": "Янгиликлар дайджести", "ru": "Дайджест новостей", "en": "News Digest"}.get(lang, "News Digest")

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
        "uz": "#Янгиликлар #BreakingNews #1КУН #Дунё #Сиёсат #Дайджест",
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
    Fon musiqasi butun video davomida past ovozda yangradi.
    """
    fx         = AUDIO_FX.get(lang, AUDIO_FX.get("uz", "volume=1.0"))
    music_path = _get_music()
    vid_dur    = _audio_dur(video_path)
    if vid_dur < 1:
        return False

    valid = [(vp, vs) for vp, vs in voices if vp and os.path.exists(vp)]
    if not valid:
        return False

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
        f"normalize=0:duration=longest[allv]"
    )

    if has_music:
        fc.append(
            f"[{music_idx}:a]aresample=44100,"
            f"atrim=duration={vid_dur:.3f},"
            f"volume={MUSIC_VOL:.3f}[mus]"
        )
        fc.append("[allv][mus]amix=inputs=2:duration=first[aout]")
        map_a = "[aout]"
    else:
        map_a = "[allv]"

    cmd += [
        "-filter_complex", ";".join(fc),
        "-map", "0:v", "-map", map_a,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "160k",
        "-t", f"{vid_dur:.3f}",
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

    # ── 1. OCHILISH ───────────────────────────────────────────
    open_img = os.path.join(TEMP_DIR, f"dg_open_{ts}.jpg")
    open_vid = os.path.join(TEMP_DIR, f"dg_open_{ts}.mp4")
    _make_open_card(lang, n, open_img)
    if _still_to_video(open_img, OPEN_DUR, open_vid):
        segments.append(open_vid)
        durations.append(OPEN_DUR)
        all_temps += [open_img, open_vid]
        current_t = OPEN_DUR - TRANS_DUR   # Birinchi item shu vaqtdan boshlanadi
    print(f"  ✓ Ochilish kartasi")

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

        print(f"  ─ Yangilik {story_num}/{n}: {sarlavha[:55]}")

        # -- 2a. Per-item TTS: sarlavha + jumla1
        tts_parts = []
        if sarlavha:
            tts_parts.append(sarlavha.strip())
        if jumla1 and jumla1.strip() != sarlavha.strip():
            body_words = jumla1.split()[:80]   # Max ~80 so'z (~40s speech)
            tts_parts.append(" ".join(body_words))
        tts_text = ". ".join(tts_parts) if tts_parts else (sarlavha or "")

        voice_i   = os.path.join(TEMP_DIR, f"dg_voice_{ts}_{idx:02d}.mp3")
        all_temps.append(voice_i)
        tts_ok    = _make_tts(tts_text, lang, daraja, voice_i) if tts_text else False
        tts_dur   = _audio_dur(voice_i) if tts_ok and os.path.exists(voice_i) else 0.0

        # Segment davomiyligi = TTS + 2s buffer (min 8s)
        seg_dur   = max(tts_dur + 2.0, 8.0) if tts_dur > 0 else 12.0

        # Bu item ovozi shu vaqtdan boshlanadi
        voice_info.append((voice_i if tts_ok else None, current_t))
        current_t += seg_dur - TRANS_DUR   # Keyingi item xfade bilan boshlanganda

        # -- 2b. Rasm yuklash
        photo_path = None
        og_path    = os.path.join(TEMP_DIR, f"dg_og_{ts}_{idx:02d}.jpg")
        if _fetch_og_image(art_url, og_path):
            photo_path = og_path
            all_temps.append(og_path)
            print(f"     📰 og:image olindi")
        else:
            # Pexels dan qidiramiz
            px_path  = os.path.join(TEMP_DIR, f"dg_px_{ts}_{idx:02d}.jpg")
            queries  = []
            if en_title and all(c.isascii() or not c.isalpha() for c in en_title):
                queries.append(en_title[:60])
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
                continue

        # Fallback: sarlavha karta (matn baked-in, statik)
        fb_img = os.path.join(TEMP_DIR, f"dg_fb_{ts}_{idx:02d}.jpg")
        all_temps.append(fb_img)
        _make_story_title_card(sarlavha, location, daraja, story_num, n, lang, fb_img)
        if _still_to_video(fb_img, seg_dur, seg_vid):
            segments.append(seg_vid)
            durations.append(seg_dur)
            print(f"     ✓ Segment {seg_dur:.1f}s (karta fallback)")

    # ── 3. YAKUNLASH ──────────────────────────────────────────
    outro_img = os.path.join(TEMP_DIR, f"dg_outro_{ts}.jpg")
    outro_vid = os.path.join(TEMP_DIR, f"dg_outro_{ts}.mp4")
    _make_outro_card(lang, outro_img)
    if _still_to_video(outro_img, OUTRO_DUR, outro_vid):
        segments.append(outro_vid)
        durations.append(OUTRO_DUR)
        all_temps += [outro_img, outro_vid]
    print(f"  ✓ Yakunlash kartasi")

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

    # ── 8. YOUTUBE YUKLASH ────────────────────────────────────
    yt_vid_id = _upload_digest(out_path, items, lang)
    yt_url    = f"https://youtu.be/{yt_vid_id}" if yt_vid_id else ""

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
    log.info(f"  📤 Telegram+Facebook postlash boshlandi [{lang.upper()}]...")
    try:
        from social_poster import post_telegram_video
        sarlavha_tg = _iget(items[0], "sarlavha", lang) if items else ""
        jumla_tg    = _iget(items[0], "jumla",    lang) if items else ""
        loc_tg      = _iget(items[0], "location", lang) if items else ""
        daraja_tg   = items[0].get("daraja", "xabar") if items else "xabar"

        # Digest video → Telegram (message_id qaytaradi)
        tg_channel = {
            "uz": "birkunday",
            "ru": "birkunday_ru",
            "en": "birkunday_en",
        }.get(lang, "")

        tg_msg_id = post_telegram_video(
            video_path = out_path,
            sarlavha   = sarlavha_tg,
            jumla      = jumla_tg,
            lang       = lang,
            daraja     = daraja_tg,
            yt_url     = yt_url,
            location   = loc_tg,
        )
        # Telegram post havolasi: https://t.me/birkunday/1097
        tg_post_url = f"https://t.me/{tg_channel}/{tg_msg_id}" if (tg_channel and tg_msg_id) else ""

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
