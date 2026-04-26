"""
geo_map.py — Geo-marker karta moduli

Shahar nomidan kichik dunyo xaritasini chizadi:
  - Qoramtir fon + qit'a konturlari
  - Qizil nuqta + pulsing halqa
  - Shahar nomi + koordinatalar
  - Kanal brendi

Ishlatish:
  from geo_map import draw_geo_card
  draw_geo_card("Tehron", "output/temp/geo_tehron.png")
"""

import os, re
from PIL import Image, ImageDraw, ImageFont

# Xarita o'lchamlari
MAP_W, MAP_H = 380, 190

# Ranglar
C_OCEAN     = (4,   10,  28)
C_LAND      = (25,  50, 100)
C_LAND_LT   = (40,  75, 140)
C_GRID      = (10,  25,  60)
C_RED       = (220,  30,  30)
C_WHITE     = (255, 255, 255)
C_GOLD      = (255, 185,   0)
C_LGRAY     = (160, 175, 205)
C_BG        = (4,    8,  20)

# ─────────────────────────────────────────────────────────────
# Shaharlar baza (nom → (display_uz, lat, lon))
# ─────────────────────────────────────────────────────────────
_CITIES: dict[str, tuple] = {
    # O'rta Sharq
    "tehron":    ("ТЕҲРОН",    35.69,  51.39),
    "тeҳрон":   ("ТЕҲРОН",    35.69,  51.39),
    "теҳрон":   ("ТЕҲРОН",    35.69,  51.39),
    "tehran":   ("TEHRON",    35.69,  51.39),
    "qohira":   ("ҚОҲИРА",    30.04,  31.24),
    "каир":     ("КАИР",      30.04,  31.24),
    "cairo":    ("CAIRO",     30.04,  31.24),
    "bagdod":   ("БАҒДОД",    33.32,  44.39),
    "baghdad":  ("BAGHDAD",   33.32,  44.39),
    "er-riyod": ("ЭР-РИЁД",  24.69,  46.72),
    "riyadh":   ("RIYADH",    24.69,  46.72),
    "dubay":    ("ДУБАЙ",     25.20,  55.27),
    "dubai":    ("DUBAI",     25.20,  55.27),
    "isroil":   ("ИСРОИЛ",    31.77,  35.22),
    "tel aviv": ("ТЕЛ АВИВ",  32.08,  34.78),
    "gʻazo":    ("ҒАЗО",      31.50,  34.47),
    "ғазо":     ("ҒАЗО",      31.50,  34.47),
    "gaza":     ("GAZA",      31.50,  34.47),
    "beirut":   ("БАЙРУТ",    33.88,  35.50),
    "bayrut":   ("БАЙРУТ",    33.88,  35.50),
    "damashq":  ("ДАМАШҚ",   33.51,  36.29),
    "damascus": ("DAMASCUS",  33.51,  36.29),
    "anqara":   ("АНҚАРА",    39.93,  32.86),
    "ankara":   ("ANKARA",    39.93,  32.86),
    "istanbul": ("ISTANBUL",  41.01,  28.95),
    "maskat":   ("МАСКАТ",    23.58,  58.40),
    "muscat":   ("MUSCAT",    23.58,  58.40),

    # Yevropa
    "berlin":   ("BERLIN",    52.52,  13.40),
    "berlин":   ("БЕРЛИН",    52.52,  13.40),
    "paris":    ("PARIS",     48.85,   2.35),
    "париж":    ("ПАРИЖ",     48.85,   2.35),
    "london":   ("LONDON",    51.51,  -0.13),
    "лондон":   ("ЛОНДОН",    51.51,  -0.13),
    "rim":      ("РИМ",       41.89,  12.49),
    "rome":     ("ROME",      41.89,  12.49),
    "madrid":   ("MADRID",    40.42,  -3.70),
    "madrid":   ("МАДРИД",    40.42,  -3.70),
    "varshava": ("ВАРШАВА",   52.23,  21.01),
    "warsaw":   ("WARSAW",    52.23,  21.01),
    "kyiv":     ("KYIV",      50.45,  30.52),
    "kiev":     ("KYIV",      50.45,  30.52),
    "kiev":     ("ҚИЕВ",      50.45,  30.52),
    "xarkov":   ("ХАРКОВ",    49.99,  36.23),
    "kharkiv":  ("KHARKIV",   49.99,  36.23),
    "bryussel": ("БРЮССЕЛ",   50.85,   4.35),
    "brussels": ("BRUSSELS",  50.85,   4.35),
    "jeneva":   ("ЖЕНЕВА",    46.20,   6.15),
    "geneva":   ("GENEVA",    46.20,   6.15),
    "stokgolm": ("СТОКГОЛЬМ", 59.33,  18.07),
    "stockholm":("STOCKHOLM", 59.33,  18.07),

    # Rossiya/MDH
    "moskva":   ("МОСКВА",    55.75,  37.62),
    "москва":   ("МОСКВА",    55.75,  37.62),
    "moscow":   ("MOSCOW",    55.75,  37.62),
    "peterburg":("ПЕТЕРБУРГ", 59.93,  30.32),
    "minsk":    ("MINSK",     53.90,  27.57),

    # Markaziy Osiyo
    "toshkent": ("ТОШКЕНТ",   41.30,  69.24),
    "tashkent": ("TASHKENT",  41.30,  69.24),
    "dushanbe": ("ДУШАНБЕ",   38.56,  68.77),
    "bishkek":  ("БИШКЕК",    42.87,  74.59),
    "olmaota":  ("ОЛМАОТА",   43.25,  76.95),
    "almaty":   ("ALMATY",    43.25,  76.95),
    "ashgabat": ("АШГАБАТ",   37.95,  58.38),
    "kabul":    ("КОБУЛ",     34.52,  69.18),

    # Osiyo
    "pekin":    ("ПЕКИН",     39.91, 116.39),
    "beijing":  ("BEIJING",   39.91, 116.39),
    "shanxay":  ("ШАНХАЙ",    31.23, 121.47),
    "shanghai": ("SHANGHAI",  31.23, 121.47),
    "tokio":    ("ТОКИО",     35.68, 139.69),
    "tokyo":    ("TOKYO",     35.68, 139.69),
    "seoul":    ("SEUL",      37.57, 126.98),
    "mumbai":   ("MUMBAI",    19.08,  72.88),
    "delhi":    ("DELHI",     28.61,  77.21),
    "yangi deli":("YANGI DELI",28.61, 77.21),
    "karachi":  ("KARACHI",   24.86,  67.01),
    "islamobod":("ISLAMOBOD", 33.72,  73.04),
    "islamabad":("ISLAMABAD", 33.72,  73.04),

    # Amerika
    "vashington":("ВАШИНГТОН",38.91, -77.04),
    "washington":("WASHINGTON",38.91,-77.04),
    "nyu-york": ("НЮ-ЙОРК",  40.71, -74.01),
    "new york": ("NEW YORK",  40.71, -74.01),
    "los anjeles":("LOS ANGELES",34.05,-118.24),
    "ottawa":   ("OTTAWA",    45.42, -75.69),
    "bogota":   ("BOGOTA",    4.71,  -74.07),
    "braziliya":("BRAZILIYA", -15.78,-47.93),
    "brazilia": ("BRASILIA",  -15.78,-47.93),
    "buenos ayres":("BUENOS AYRES",-34.60,-58.38),

    # Afrika
    "nayrоbi":  ("НАЙРОБИ",   -1.29,  36.82),
    "nairobi":  ("NAIROBI",   -1.29,  36.82),
    "lagos":    ("LAGOS",      6.46,   3.38),
    "addis abeba":("ADDIS ABEBA",9.03, 38.74),
    "xartum":   ("ХАРТУМ",    15.55,  32.53),
    "khartoum": ("KHARTOUM",  15.55,  32.53),

    # Avstraliya
    "syudney":  ("SYDNEY",   -33.87, 151.21),
    "sydney":   ("SYDNEY",   -33.87, 151.21),
    "kanberra": ("CANBERRA", -35.28, 149.13),
}

# Qit'a shakllanishlari (lon_min, lat_max, lon_max, lat_min)
_CONTINENTS = [
    # Shimoliy Amerika
    [(-168, 72), (-168, 15), (-53, 15), (-53, 72)],
    # Janubiy Amerika
    [(-82, 13), (-82, -56), (-34, -56), (-34, 13)],
    # Yevropa
    [(-25, 72), (-25, 35), (45, 35), (45, 72)],
    # Afrika
    [(-18, 38), (-18, -35), (52, -35), (52, 38)],
    # Osiyo (G'arb)
    [(26, 75), (26, 1), (100, 1), (100, 75)],
    # Osiyo (Sharq)
    [(100, 72), (100, 0), (145, 0), (145, 72)],
    # Yaponiya taxminiy
    [(130, 45), (130, 31), (145, 31), (145, 45)],
    # Avstraliya
    [(114, -10), (114, -44), (154, -44), (154, -10)],
]


def _latlon_to_px(lat: float, lon: float) -> tuple[int, int]:
    """Lat/lon → xarita pikseli (equirectangular)."""
    x = int((lon + 180) / 360 * MAP_W)
    y = int((90 - lat) / 180 * MAP_H)
    x = max(2, min(MAP_W - 2, x))
    y = max(2, min(MAP_H - 2, y))
    return x, y


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    cands = (
        ["C:\\Windows\\Fonts\\arialbd.ttf", "C:\\Windows\\Fonts\\calibrib.ttf"]
        if bold else
        ["C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\calibri.ttf"]
    )
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _build_base_map() -> Image.Image:
    """Dunyo xaritasi asosi (qit'a bloklari)."""
    img  = Image.new("RGB", (MAP_W, MAP_H), C_OCEAN)
    draw = ImageDraw.Draw(img)

    # Grid chiziqlar (lon/lat)
    for lon in range(-180, 181, 30):
        x = int((lon + 180) / 360 * MAP_W)
        draw.line([(x, 0), (x, MAP_H)], fill=C_GRID, width=1)
    for lat in range(-90, 91, 30):
        y = int((90 - lat) / 180 * MAP_H)
        draw.line([(0, y), (MAP_W, y)], fill=C_GRID, width=1)

    # Qit'a bloklari
    for pts_latlon in _CONTINENTS:
        pts_px = [_latlon_to_px(lat, lon) for lon, lat in pts_latlon]
        draw.polygon(pts_px, fill=C_LAND, outline=C_LAND_LT)

    return img


# Xarita keshi (bir marta yaratiladi)
_BASE_MAP: Image.Image | None = None


def _get_base_map() -> Image.Image:
    global _BASE_MAP
    if _BASE_MAP is None:
        _BASE_MAP = _build_base_map()
    return _BASE_MAP.copy()


def _lookup_city(location: str) -> tuple:
    """Shahar nomidan koordinatalar olish."""
    if not location:
        return None
    key = location.lower().strip()
    # To'g'ri moslik
    if key in _CITIES:
        return _CITIES[key]
    # Qisman moslik
    for k, v in _CITIES.items():
        if k in key or key in k:
            return v
    return None


def draw_geo_card(location: str, out_path: str,
                  card_w: int = MAP_W, card_h: int = MAP_H + 50) -> str:
    """
    Geo karta kartasi (PNG, RGBA) yaratish.
    card_w/card_h — istalgan o'lcham; karta auto-scale qilinadi.
    """
    city_data = _lookup_city(location)

    # ── Karta asosi (to'liq o'lchamda) ───────────────────────
    base = _get_base_map()   # MAP_W x MAP_H

    # ── Nuqta qo'yish (to'liq o'lchamdagi koordinatlarda) ────
    draw = ImageDraw.Draw(base)
    if city_data:
        display_name, lat, lon = city_data
        cx, cy = _latlon_to_px(lat, lon)

        # Pulsing halqalar
        for r_off, alpha in [(16, 50), (10, 110), (6, 200)]:
            tmp = Image.new("RGBA", (MAP_W, MAP_H), (0, 0, 0, 0))
            d2  = ImageDraw.Draw(tmp)
            d2.ellipse([(cx - r_off, cy - r_off),
                         (cx + r_off, cy + r_off)],
                        outline=(220, 30, 30, alpha), width=2)
            base = base.convert("RGBA")
            base.alpha_composite(tmp)
            base = base.convert("RGB")
            draw = ImageDraw.Draw(base)

        # Qizil nuqta + oq marqaz
        draw.ellipse([(cx - 5, cy - 5), (cx + 5, cy + 5)], fill=C_RED)
        draw.ellipse([(cx - 2, cy - 2), (cx + 2, cy + 2)], fill=C_WHITE)

        # Shahar yorlig'i
        name = display_name[:18]
        lx   = min(cx + 8, MAP_W - len(name) * 7)
        ly   = max(4, cy - 16)
        draw.rectangle([(lx - 2, ly - 1), (lx + len(name) * 7 + 2, ly + 14)],
                        fill=(4, 8, 20, 200))
        draw.text((lx, ly), name, font=_font(11, bold=True), fill=C_GOLD)
    else:
        cx, cy = MAP_W // 2, MAP_H // 2
        draw.ellipse([(cx - 5, cy - 5), (cx + 5, cy + 5)], fill=C_RED)

    # ── Pastki panel balandligini hisoblash ───────────────────
    info_h   = card_h - int(card_w * MAP_H / MAP_W)   # karta qismidan qolgan joy
    info_h   = max(info_h, 22)                         # kamida 22px
    map_disp_h = card_h - info_h                       # karta qismi

    # ── Karta o'lchamini moslashtirish (scale down) ───────────
    base_scaled = base.resize((card_w, map_disp_h), Image.LANCZOS)

    # ── Yig'ish: karta + info panel ──────────────────────────
    full = Image.new("RGBA", (card_w, card_h), (4, 8, 20, 230))
    full.paste(base_scaled.convert("RGBA"), (0, 0))

    d3 = ImageDraw.Draw(full)

    # Ajratuvchi chiziq
    d3.line([(0, map_disp_h), (card_w, map_disp_h)],
            fill=(*C_RED, 200), width=2)

    # Shahar nomi
    city_label = city_data[0] if city_data else location.upper()[:18]
    font_sz    = max(10, min(16, int(info_h * 0.55)))
    d3.text((6, map_disp_h + 3), city_label,
            font=_font(font_sz, bold=True), fill=(255, 255, 255, 245))

    # Koordinatalar (faqat panel yetarli keng bo'lsa)
    if city_data and card_w >= 200:
        lat, lon = city_data[1], city_data[2]
        lat_s = f"{'N' if lat >= 0 else 'S'}{abs(lat):.1f}°"
        lon_s = f"{'E' if lon >= 0 else 'W'}{abs(lon):.1f}°"
        coord_sz = max(9, min(13, int(info_h * 0.42)))
        d3.text((card_w - 6, map_disp_h + 3), f"{lat_s} {lon_s}",
                font=_font(coord_sz, bold=False),
                fill=(*C_LGRAY, 215), anchor="ra")

    # Brend (faqat yetarli joy bo'lsa)
    if info_h >= 34:
        brand_sz = max(8, min(11, int(info_h * 0.30)))
        d3.text((6, card_h - brand_sz - 3), "1KUN GLOBAL",
                font=_font(brand_sz, bold=False), fill=(*C_GOLD, 155))

    full.save(out_path, "PNG")
    return out_path
