"""
analysis_maker.py — 10-15 daqiqalik tahlil video generatori

Format:
  [Ochilish 6s] →
  [Yangilik-1 sarlavha 5s] → [Yangilik-1 rasmlar (voice1_dur s)] →
  [JINGLE 0.8s] →
  [Yangilik-2 sarlavha 5s] → ... →
  [Yakunlash 8s]

Xususiyatlar:
  • Har yangilik uchun alohida TTS → uzunlik TTS dan aniqlanadi
  • Yangiliklar orasida jingle (chime ovoz + o'tish effekti)
  • "Keyingi yangilik" matn yo'q — naratsiya uzluksiz
  • 120 BPM fon musiqasi
  • Pastda ob-havo ticker
  • Har yangilik uchun 2 rasm (slow pan)
"""

import os, sys, re, json, glob, shutil, textwrap, math
import subprocess, requests, asyncio, random
from datetime import date, datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(".env")

from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import edge_tts

from config import OUTPUT_DIR, TEMP_DIR, VOICES, AUDIO_FX, YOUTUBE_PLAYLIST

# ─────────────────────────────────────────────────────────────
# Konstantlar
# ─────────────────────────────────────────────────────────────
VW, VH           = 1280, 720
FPS              = 25
OPEN_DUR         = 6       # Ochilish kartasi
TITLE_DUR        = 5       # Yangilik sarlavha kartasi
PHOTOS_PER_STORY = 2       # Yangilik boshiga rasmlar
OUTRO_DUR        = 8       # Yakunlash kartasi
JINGLE_DUR       = 0.8     # Jingle kartasi
TRANS_DUR        = 0.6     # Crossfade
MAX_ITEMS        = 6
MIN_ITEMS        = 2
WORDS_PER_STORY  = 380     # ~130s naratsiya (~2 daqiqa)
MUSIC_VOL        = 0.20    # Tahlil uchun biroz pastroq musiqa

_HERE = os.path.dirname(os.path.abspath(__file__))

# Ranglar
C_BG     = (4,   8,  20)
C_DARK   = (8,  16,  38)
C_NAVY   = (8,  22,  58)
C_GOLD   = (255, 185,   0)
C_RED    = (220,  30,  30)
C_WHITE  = (255, 255, 255)
C_LGRAY  = (175, 188, 212)
C_ACCENT = (0,  140, 255)
C_TICKER = (12,  22,  50)


# ─────────────────────────────────────────────────────────────
# Shrift
# ─────────────────────────────────────────────────────────────
def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    cands = (
        ["C:\\Windows\\Fonts\\arialbd.ttf",
         "C:\\Windows\\Fonts\\calibrib.ttf"]
        if bold else
        ["C:\\Windows\\Fonts\\arial.ttf",
         "C:\\Windows\\Fonts\\calibri.ttf"]
    )
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _iget(item: dict, field: str, lang: str, fallback: str = "") -> str:
    """Dict yoki str maqola maydonidan to'g'ri til qiymatini olish."""
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
            result = (val.get(lang) or val.get("uz") or
                      val.get("ru") or val.get("en") or "")
            if result:
                return str(result).strip()
        elif isinstance(val, str) and val.strip():
            return val.strip()
    return fallback


def _text_shadow(draw, xy, text, font, fill, shadow=(0, 0, 0), offset=2, anchor=None):
    x, y = xy
    kw = {"anchor": anchor} if anchor else {}
    draw.text((x + offset, y + offset), text, font=font, fill=(*shadow, 160), **kw)
    draw.text((x, y), text, font=font, fill=fill, **kw)


def _gradient_rect(draw, x0, y0, x1, y1, color_top, color_bot):
    h = y1 - y0
    for dy in range(h):
        t = dy / max(h - 1, 1)
        r = int(color_top[0] * (1 - t) + color_bot[0] * t)
        g = int(color_top[1] * (1 - t) + color_bot[1] * t)
        b = int(color_top[2] * (1 - t) + color_bot[2] * t)
        draw.line([(x0, y0 + dy), (x1, y0 + dy)], fill=(r, g, b))


# ─────────────────────────────────────────────────────────────
# Ob-havo (Open-Meteo API — bepul)
# ─────────────────────────────────────────────────────────────
_WEATHER_CITIES = [
    ("Toshkent",   "Tashkent",   41.30,  69.24),
    ("Moskva",     "Moscow",     55.75,  37.62),
    ("Dubay",      "Dubai",      25.20,  55.27),
    ("London",     "London",     51.51,  -0.13),
    ("Nyu-York",   "New York",   40.71, -74.01),
    ("Pekin",      "Beijing",    39.91, 116.39),
    ("Tehron",     "Tehran",     35.69,  51.39),
    ("Qohira",     "Cairo",      30.04,  31.24),
    ("Parij",      "Paris",      48.85,   2.35),
    ("Berlin",     "Berlin",     52.52,  13.40),
]

_weather_cache: dict | None = None
_weather_ts:    float       = 0.0


def _get_weather_ticker(lang: str = "uz") -> str:
    global _weather_cache, _weather_ts
    import time
    now = time.time()
    if _weather_cache and (now - _weather_ts) < 7200:
        data = _weather_cache
    else:
        data = {}
        for city_uz, city_en, lat, lon in _WEATHER_CITIES:
            try:
                url  = (f"https://api.open-meteo.com/v1/forecast"
                        f"?latitude={lat}&longitude={lon}"
                        f"&current_weather=true&timezone=auto")
                resp = requests.get(url, timeout=8)
                if resp.status_code == 200:
                    cw = resp.json().get("current_weather", {})
                    t  = cw.get("temperature")
                    wc = cw.get("weathercode", 0)
                    if t is not None:
                        icon = ("☀️" if wc < 3 else "⛅" if wc < 50 else
                                "🌧" if wc < 80 else "❄️")
                        data[city_uz] = (round(float(t)), icon, city_en)
            except Exception:
                pass
        if data:
            _weather_cache = data; _weather_ts = now

    if not data:
        return ""
    parts = []
    for city_uz, city_en, *_ in _WEATHER_CITIES:
        if city_uz in data:
            temp, icon, city_en_name = data[city_uz]
            sign = "+" if temp >= 0 else ""
            if lang == "uz":
                parts.append(f"{icon} {city_uz}: {sign}{temp}°")
            else:
                parts.append(f"{icon} {city_en_name}: {sign}{temp}°{'C' if lang=='en' else ''}")
    return "    ◆    ".join(parts)


# ─────────────────────────────────────────────────────────────
# Musiqa — 120 BPM beat (shared with digest_maker)
# ─────────────────────────────────────────────────────────────
_CACHED_MUSIC: str | None = None


def _get_music() -> str | None:
    global _CACHED_MUSIC
    if _CACHED_MUSIC and os.path.exists(_CACHED_MUSIC):
        return _CACHED_MUSIC

    for fname in ("news_beat.mp3", "news_beat.aac",
                  "background_fast.mp3", "background_fast.aac",
                  "background.mp3", "background.aac"):
        p = os.path.join(_HERE, "assets", fname)
        if os.path.exists(p):
            _CACHED_MUSIC = p
            return p

    # digest_maker tomonidan yaratilgan beat ni qayta ishlatish
    shared = os.path.join(TEMP_DIR, "dg_beat_v2.aac")
    if os.path.exists(shared) and os.path.getsize(shared) > 50_000:
        _CACHED_MUSIC = shared
        return shared

    gen_path = os.path.join(TEMP_DIR, "an_beat_v2.aac")
    if os.path.exists(gen_path) and os.path.getsize(gen_path) > 50_000:
        _CACHED_MUSIC = gen_path
        return gen_path

    print("  🎵 Tahlil beati generatsiya qilinmoqda (120 BPM)...")
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
        "-t", "30",    # 30s → loopda ishlatiladi
        "-af", (
            "highpass=f=50,"
            "equalizer=f=80:width_type=o:width=2:g=+5,"
            "equalizer=f=2500:width_type=o:width=2:g=+2,"
            "acompressor=threshold=0.3:ratio=5:attack=3:release=60:makeup=2.0,"
            "volume=0.85"
        ),
        "-c:a", "aac", "-b:a", "128k", gen_path,
    ], capture_output=True, timeout=60)

    if os.path.exists(gen_path) and os.path.getsize(gen_path) > 10_000:
        print("  🎵 120 BPM beat tayyor!")
        _CACHED_MUSIC = gen_path
        return gen_path
    print("  ⚠️  Musiqa yaratilmadi")
    return None


# ─────────────────────────────────────────────────────────────
# Jingle — yangiliklar o'rtasida chime ovoz
# ─────────────────────────────────────────────────────────────
_JINGLE_PATH: str | None = None


def _get_jingle() -> str | None:
    global _JINGLE_PATH
    if _JINGLE_PATH and os.path.exists(_JINGLE_PATH):
        return _JINGLE_PATH

    jpath = os.path.join(TEMP_DIR, "an_jingle.aac")
    if os.path.exists(jpath) and os.path.getsize(jpath) > 500:
        _JINGLE_PATH = jpath
        return jpath

    # C6-E6-G6-C7 major chord "ding" — 0.8 sekunda
    expr = (
        "sin(2*PI*1047*t)*0.70*exp(-7*t)"   # C6
        "+sin(2*PI*1319*t)*0.50*exp(-9*t)"  # E6
        "+sin(2*PI*1568*t)*0.35*exp(-11*t)" # G6
        "+sin(2*PI*2093*t)*0.20*exp(-15*t)" # C7
    )
    r = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=44100:c=stereo",
        "-t", f"{JINGLE_DUR}",
        "-af", "volume=0.85,aformat=channel_layouts=stereo",
        "-c:a", "aac", "-b:a", "128k", jpath,
    ], capture_output=True, timeout=10)

    if r.returncode == 0 and os.path.exists(jpath):
        _JINGLE_PATH = jpath
        return jpath
    return None


# ─────────────────────────────────────────────────────────────
# Jingle o'tish kartasi (qisqa qoramtir + oltin chiziq)
# ─────────────────────────────────────────────────────────────
def _make_jingle_card(ts: str, idx: int, jingle_path: str | None,
                      dur: float, out_path: str) -> bool:
    """0.8s qoramtir o'tish kartasi + jingle ovozi."""
    img  = Image.new("RGB", (VW, VH), (2, 4, 12))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, VH // 2 - 3), (VW, VH // 2 + 3)], fill=C_GOLD)
    card_p = os.path.join(TEMP_DIR, f"an_jcard_{ts}_{idx:02d}.jpg")
    img.save(card_p, "JPEG", quality=90)

    if jingle_path and os.path.exists(jingle_path):
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", card_p,
            "-i", jingle_path,
            "-t", str(dur),
            "-vf", f"scale={VW}:{VH},fps={FPS}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k",
            out_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", card_p,
            "-t", str(dur),
            "-vf", f"scale={VW}:{VH},fps={FPS}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an",
            out_path,
        ]
    r = subprocess.run(cmd, capture_output=True, timeout=20)
    try: os.remove(card_p)
    except: pass
    return r.returncode == 0 and os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# Audio yordamchilar
# ─────────────────────────────────────────────────────────────
def _audio_dur(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _create_silence(duration: float, out_path: str) -> bool:
    """FFmpeg bilan jimlik audio segmenti yaratish (sinxronizatsiya uchun)."""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", f"{duration:.3f}",
        "-c:a", "aac", "-b:a", "128k", out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    return r.returncode == 0 and os.path.exists(out_path)


def _concat_audios(parts: list, out_path: str) -> bool:
    """Bir nechta audio faylni ketma-ket birlashtirish."""
    parts = [p for p in parts if p and os.path.exists(p)]
    if not parts:
        return False
    if len(parts) == 1:
        shutil.copy(parts[0], out_path)
        return True
    cmd = ["ffmpeg", "-y"]
    for p in parts:
        cmd += ["-i", p]
    n  = len(parts)
    fc = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[aout]"
    cmd += ["-filter_complex", fc, "-map", "[aout]",
            "-c:a", "aac", "-b:a", "160k", out_path]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    return r.returncode == 0 and os.path.exists(out_path)


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
    'Ъ':"'",'ъ':"'",'Ь':'','ь':'','Э':'E','э':'e',
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


# ─────────────────────────────────────────────────────────────
# Video yordamchilar
# ─────────────────────────────────────────────────────────────
def _crop_resize(photo_path: str, out_path: str, brightness: float = 0.78) -> bool:
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
        img = ImageEnhance.Brightness(img).enhance(brightness)
        img.save(out_path, "JPEG", quality=92)
        return True
    except Exception:
        return False


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


def _photo_composite(raw_photo: str, overlay_png: str,
                     duration: float, pan_idx: int, out_path: str) -> bool:
    extra   = int(VW * 0.10)
    extra_h = int(VH * 0.10)
    sw      = VW + extra
    sh      = VH + extra_h
    dur_s   = f"{duration:.3f}"
    pan_x   = (f"trunc({extra}*t/{duration})"
               if pan_idx % 2 == 0
               else f"trunc({extra}*(1-t/{duration}))")
    pan_y   = str(extra_h // 2)
    fc = (
        f"[0:v]scale={sw}:{sh}:force_original_aspect_ratio=increase,"
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
        err = r.stderr.decode("utf-8", errors="replace")[-200:]
        print(f"     composite xato: {err}")
        return _still_to_video(raw_photo, duration, out_path)
    return os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# Karta: Ochilish
# ─────────────────────────────────────────────────────────────
def _make_open_card(lang: str, story_count: int, weather: str, out_path: str):
    img  = Image.new("RGB", (VW, VH), C_BG)
    _gradient_rect(ImageDraw.Draw(img), 0, 0, VW, VH, C_BG, C_DARK)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (8, VH)],        fill=C_GOLD)
    draw.rectangle([(VW - 8, 0), (VW, VH)],  fill=C_GOLD)
    draw.rectangle([(0, 0), (VW, 6)],        fill=C_RED)
    draw.rectangle([(0, VH - 6), (VW, VH)],  fill=C_RED)

    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    _text_shadow(draw, (VW // 2, 160), brand,
                 font=_font(90), fill=C_GOLD, offset=4, anchor="mm")

    label = {"uz": "CHUQUR TAHLIL", "ru": "ГЛУБОКИЙ АНАЛИЗ",
             "en": "IN-DEPTH ANALYSIS"}.get(lang, "ANALYSIS")
    draw.rectangle([(VW // 2 - 220, 210), (VW // 2 + 220, 265)], fill=C_RED)
    draw.text((VW // 2, 237), f"  {label}  ",
              font=_font(32), fill=C_WHITE, anchor="mm")

    today  = date.today().strftime("%d.%m.%Y")
    count_txt = {"uz": f"{story_count} ta mavzu",
                 "ru": f"{story_count} темы",
                 "en": f"{story_count} topics"}.get(lang, str(story_count))
    draw.text((VW // 2, 325), f"{today}   |   {count_txt}",
              font=_font(24, bold=False), fill=C_LGRAY, anchor="mm")

    # Ob-havo ticker
    if weather:
        th = 42; y0 = VH - th
        draw.rectangle([(0, y0 - 2), (VW, VH)], fill=C_TICKER)
        draw.rectangle([(0, y0 - 4), (VW, y0 - 2)], fill=C_GOLD)
        draw.text((VW // 2, y0 + th // 2),
                  weather[:110] + ("…" if len(weather) > 110 else ""),
                  font=_font(15, bold=False), fill=C_LGRAY, anchor="mm")

    draw.text((VW // 2, VH - 65), "youtube.com/@1kunGlobal",
              font=_font(18, bold=False), fill=(*C_GOLD, 120), anchor="mm")
    img.save(out_path, "JPEG", quality=93)
    return out_path


# ─────────────────────────────────────────────────────────────
# Karta: Yangilik sarlavha
# ─────────────────────────────────────────────────────────────
def _make_title_card(sarlavha: str, location: str, daraja: str,
                     story_num: int, total: int, lang: str, out_path: str):
    img  = Image.new("RGB", (VW, VH), C_BG)
    _gradient_rect(ImageDraw.Draw(img), 0, 0, VW, VH, (5, 10, 28), (2, 4, 14))
    draw  = ImageDraw.Draw(img)
    accent = {"muhim": C_RED, "tezkor": (220, 120, 0), "xabar": C_ACCENT}.get(daraja, C_ACCENT)

    draw.rectangle([(0, 0), (10, VH)],      fill=accent)
    draw.rectangle([(0, 0), (VW, 55)],      fill=(*C_BG, 240))
    draw.rectangle([(0, 53), (VW, 57)],     fill=accent)

    brand = {"uz": "1КУН GLOBAL  |  TAHLIL",
             "ru": "1ДЕНЬ GLOBAL  |  АНАЛИЗ",
             "en": "1DAY GLOBAL  |  ANALYSIS"}.get(lang, "1KUN")
    draw.text((22, 27), brand, font=_font(22), fill=C_GOLD, anchor="lm")
    draw.text((VW - 22, 27), date.today().strftime("%d.%m.%Y"),
              font=_font(20, bold=False), fill=C_LGRAY, anchor="rm")

    bx, by = 90, VH // 2 - 20
    draw.ellipse([(bx - 55, by - 55), (bx + 55, by + 55)], fill=accent)
    draw.text((bx, by - 12), str(story_num), font=_font(42), fill=C_WHITE, anchor="mm")
    draw.text((bx, by + 20), f"/{total}",
              font=_font(22, bold=False), fill=(*C_WHITE, 180), anchor="mm")

    banner_map = {
        "uz": {"muhim": "⚡ MUHIM YANGILIK", "tezkor": "🔴 TEZKOR", "xabar": "📰 XABAR"},
        "ru": {"muhim": "⚡ ГЛАВНАЯ НОВОСТЬ", "tezkor": "🔴 СРОЧНО", "xabar": "📰 НОВОСТЬ"},
        "en": {"muhim": "⚡ BREAKING NEWS",  "tezkor": "🔴 URGENT",  "xabar": "📰 NEWS"},
    }
    banner = banner_map.get(lang, banner_map["en"]).get(daraja, "📰")
    bw2 = len(banner) * 13 + 24
    draw.rectangle([(160, 78), (160 + bw2, 116)], fill=(*accent, 245))
    draw.text((160 + 12, 97), banner, font=_font(22), fill=C_WHITE, anchor="lm")

    wrapped = textwrap.wrap(sarlavha or "", width=30)[:4]
    ty = 148 if len(wrapped) <= 2 else 128
    for i, line in enumerate(wrapped):
        fs   = 52 if i == 0 else 46
        fill = C_WHITE if i == 0 else C_LGRAY
        _text_shadow(draw, (160, ty), line, font=_font(fs), fill=fill, offset=3)
        ty += fs + 10

    if location:
        try:
            from geo_map import draw_geo_card
            import uuid
            tmp = os.path.join(TEMP_DIR, f"an_geo_{uuid.uuid4().hex[:8]}.png")
            draw_geo_card(location, tmp, card_w=340, card_h=200)
            geo_img = Image.open(tmp).convert("RGBA")
            gw, gh  = geo_img.size
            base_rgba = img.convert("RGBA")
            base_rgba.alpha_composite(geo_img, (VW - gw - 12, VH - gh - 44))
            img = base_rgba.convert("RGB")
            draw = ImageDraw.Draw(img)
            try: os.remove(tmp)
            except: pass
        except Exception:
            pass

    draw.rectangle([(0, VH - 5), (VW, VH)], fill=accent)
    img.save(out_path, "JPEG", quality=93)


# ─────────────────────────────────────────────────────────────
# Karta: Foto overlay (RGBA transparent)
# ─────────────────────────────────────────────────────────────
def _make_overlay_png(sarlavha: str, location: str, daraja: str,
                      lang: str, story_num: int, total: int,
                      photo_idx: int, photos_total: int,
                      weather: str, out_path: str) -> str:
    accent = {"muhim": C_RED, "tezkor": (220, 120, 0), "xabar": C_ACCENT}.get(daraja, C_ACCENT)
    img  = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (VW, 42)], fill=(4, 8, 20, 215))
    draw.rectangle([(0, 40), (VW, 43)], fill=(*accent, 225))
    brand = {"uz": "1КУН GLOBAL  |  TAHLIL",
             "ru": "1ДЕНЬ GLOBAL  |  АНАЛИЗ",
             "en": "1DAY GLOBAL  |  ANALYSIS"}.get(lang, "1KUN")
    draw.text((16, 21), brand, font=_font(19), fill=C_GOLD, anchor="lm")
    draw.text((VW - 16, 21), date.today().strftime("%d.%m.%Y"),
              font=_font(18, bold=False), fill=C_LGRAY, anchor="rm")

    draw.rectangle([(0, 0), (5, VH)], fill=(*accent, 200))

    if photos_total > 1:
        badge = f"📷 {photo_idx + 1}/{photos_total}"
        bw3   = len(badge) * 12 + 20
        draw.rectangle([(VW - bw3 - 8, 48), (VW - 8, 82)], fill=(*C_NAVY, 210))
        draw.text((VW - bw3 // 2 - 8, 65), badge,
                  font=_font(18, bold=False), fill=C_LGRAY, anchor="mm")

    grad_h = 260
    grad_img = Image.new("RGBA", (VW, grad_h), (0, 0, 0, 0))
    g_draw   = ImageDraw.Draw(grad_img)
    for dy in range(grad_h):
        alpha = int(248 * (dy / grad_h) ** 1.25)
        g_draw.line([(0, dy), (VW, dy)], fill=(4, 8, 20, alpha))
    img.paste(grad_img, (0, VH - grad_h), grad_img)

    bx2, by2 = 36, VH - 175
    draw.ellipse([(bx2 - 25, by2 - 25), (bx2 + 25, by2 + 25)], fill=(*accent, 235))
    draw.text((bx2, by2), str(story_num), font=_font(26), fill=C_WHITE, anchor="mm")

    if sarlavha:
        wrapped = textwrap.wrap(sarlavha, width=48)[:2]
        ty = VH - 170
        for line in wrapped:
            draw.text((70, ty + 2), line, font=_font(36), fill=(0, 0, 0, 130))
            draw.text((68, ty),     line, font=_font(36), fill=(255, 255, 255, 248))
            ty += 44

    if location:
        try:
            from geo_map import draw_geo_card
            import uuid
            tmp = os.path.join(TEMP_DIR, f"an_geo_{uuid.uuid4().hex[:8]}.png")
            draw_geo_card(location, tmp, card_w=300, card_h=178)
            geo_img = Image.open(tmp).convert("RGBA")
            gw, gh  = geo_img.size
            img.paste(geo_img, (VW - gw - 10, VH - gh - 56), geo_img)
            try: os.remove(tmp)
            except: pass
        except Exception:
            pass

    # Ob-havo ticker
    th = 42; y0 = VH - th
    draw.rectangle([(0, y0), (VW, VH)], fill=(*C_TICKER, 238))
    draw.rectangle([(0, y0), (VW, y0 + 2)], fill=(*C_GOLD, 200))
    wx_label = {"uz": "🌡 OB-HAVO:", "ru": "🌡 ПОГОДА:", "en": "🌡 WEATHER:"}.get(lang, "🌡")
    draw.text((12, y0 + th // 2), wx_label,
              font=_font(16), fill=(*C_GOLD, 240), anchor="lm")
    wx_lw = len(wx_label) * 11 + 16
    if weather:
        tick = weather[:120] + ("…" if len(weather) > 120 else "")
        draw.text((wx_lw + 8, y0 + th // 2), tick,
                  font=_font(15, bold=False), fill=(*C_LGRAY, 225), anchor="lm")

    img.save(out_path, "PNG")
    return out_path


# ─────────────────────────────────────────────────────────────
# Karta: Outro
# ─────────────────────────────────────────────────────────────
def _make_outro(lang: str, out_path: str):
    img  = Image.new("RGB", (VW, VH), C_BG)
    _gradient_rect(ImageDraw.Draw(img), 0, 0, VW, VH, C_BG, (2, 5, 16))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (8, VH)],        fill=C_GOLD)
    draw.rectangle([(VW - 8, 0), (VW, VH)],  fill=C_GOLD)
    draw.rectangle([(0, 0), (VW, 5)],        fill=C_RED)
    draw.rectangle([(0, VH - 5), (VW, VH)],  fill=C_RED)
    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    _text_shadow(draw, (VW // 2, VH // 2 - 100), brand,
                 font=_font(82), fill=C_GOLD, offset=4, anchor="mm")
    cta = {
        "uz": ("TAHLILLARIMIZNI KO'RING!", "Har kuni chuqur tahlil — kanalimizda"),
        "ru": ("СМОТРИТЕ НАШ АНАЛИЗ!", "Глубокий анализ событий каждый день"),
        "en": ("WATCH OUR ANALYSIS!", "In-depth world news analysis daily"),
    }.get(lang, ("SUBSCRIBE!", "Daily analysis"))
    draw.text((VW // 2, VH // 2 + 10), cta[0],
              font=_font(40), fill=C_WHITE, anchor="mm")
    draw.text((VW // 2, VH // 2 + 58), cta[1],
              font=_font(22, bold=False), fill=C_LGRAY, anchor="mm")
    draw.text((VW // 2, VH // 2 + 108), "🔔  👍  SHARE",
              font=_font(28, bold=False), fill=(*C_GOLD, 200), anchor="mm")
    img.save(out_path, "JPEG", quality=93)


# ─────────────────────────────────────────────────────────────
# Rasm yuklash
# ─────────────────────────────────────────────────────────────
def _fetch_og_image(article_url: str, out_path: str) -> bool:
    if not article_url or not article_url.startswith("http"):
        return False
    try:
        hdrs = {"User-Agent": "Mozilla/5.0"}
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
            img_url = (ph.get("src", {}).get("large2x") or
                       ph.get("src", {}).get("large", ""))
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
# Concat (xfade)
# ─────────────────────────────────────────────────────────────
def _concat_xfade(video_parts: list, durations: list, out_path: str) -> bool:
    n = len(video_parts)
    if n == 0:
        return False
    if n == 1:
        shutil.copy(video_parts[0], out_path)
        return os.path.exists(out_path)

    cmd = ["ffmpeg", "-y"]
    for vp in video_parts:
        cmd += ["-i", vp]

    trans = ["fade", "slideleft", "slideright", "fade",
             "wipeleft", "wiperight", "fade", "slideleft"]
    fc_v = []
    prev = "[0:v]"
    for i in range(1, n):
        t   = trans[(i - 1) % len(trans)]
        off = sum(durations[:i]) - i * TRANS_DUR
        out = f"[v{i:02d}]"
        fc_v.append(
            f"{prev}[{i}:v]xfade=transition={t}"
            f":duration={TRANS_DUR:.2f}:offset={max(off, 0):.2f}{out}"
        )
        prev = out

    cmd += ["-filter_complex", ";".join(fc_v), "-map", prev]
    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-an", "-movflags", "+faststart", out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=900)
    if r.returncode != 0:
        ts  = datetime.now().strftime("%Y%m%d%H%M%S%f")
        txt = os.path.join(TEMP_DIR, f"an_fc_{ts}.txt")
        with open(txt, "w") as fh:
            for p in video_parts:
                fh.write(f"file '{os.path.abspath(p)}'\n")
        r2 = subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", txt,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an", out_path,
        ], capture_output=True, timeout=600)
        try: os.remove(txt)
        except: pass
        return r2.returncode == 0 and os.path.exists(out_path)
    return os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# Audio mix (ovoz tugashi = video tugashi)
# ─────────────────────────────────────────────────────────────
def _mix_audio(video_path: str, voice_path: str, out_path: str, lang: str) -> bool:
    fx         = AUDIO_FX.get(lang, AUDIO_FX.get("uz", "volume=1.0"))
    music_path = _get_music()
    vid_dur    = _audio_dur(video_path)
    voice_dur  = _audio_dur(voice_path)

    if vid_dur < 1 or voice_dur < 1:
        return False

    # Output uzunligi = VIDEO uzunligi (ovoz qisqa bo'lsa musiqa davom etadi)
    # Ovoz tugagach — fon musiqasi to'liq video davomida eshitiladi
    target = vid_dur
    vd     = f"{target:.3f}"

    if music_path and os.path.exists(music_path):
        af = (
            f"[1:a]aresample=44100,{fx},"
            f"apad=whole_dur={vd}[voice];"
            f"[2:a]aresample=44100,volume={MUSIC_VOL:.3f}[mus];"
            f"[voice][mus]amix=inputs=2:duration=longest,"
            f"atrim=duration={vd},"
            f"aformat=channel_layouts=stereo[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path, "-i", voice_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex", af,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-t", vd, "-movflags", "+faststart", out_path,
        ]
    else:
        af = (
            f"[1:a]aresample=44100,{fx},"
            f"aformat=channel_layouts=stereo[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path, "-i", voice_path,
            "-filter_complex", af,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-t", vd, "-movflags", "+faststart", out_path,
        ]
    r = subprocess.run(cmd, capture_output=True, timeout=900)
    if r.returncode != 0:
        print("  Audio mix xato:", r.stderr.decode("utf-8", errors="replace")[-200:])
    return r.returncode == 0 and os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# YouTube yuklash
# ─────────────────────────────────────────────────────────────
def _upload_analysis(video_path: str, items: list, lang: str) -> str | None:
    try:
        from youtube_maker import youtube_auth
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        print(f"  YouTube import xato: {e}"); return None
    try:
        youtube = youtube_auth()
    except Exception as e:
        print(f"  YouTube auth xato: {e}"); return None

    today      = date.today().strftime("%d.%m.%Y")
    n          = len(items)
    chan_name  = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    an_label   = {"uz": "Таҳлил", "ru": "Анализ", "en": "Analysis"}.get(lang, "Analysis")
    first_title = items[0].get("sarlavha", "")
    yt_title    = f"{first_title} | {an_label} | {today}"[:100]

    story_lines = []
    for i, it in enumerate(items, 1):
        story_lines.append(f"{i}. {it.get('sarlavha', '')}")
        if it.get("jumla1"):
            story_lines.append(f"   {it['jumla1'][:120]}")
        story_lines.append("")

    htags = {
        "uz": "#Tahlil #Yangiliklar #BreakingNews #1КУН #Дунё",
        "ru": "#Анализ #Новости #BreakingNews #1ДЕНЬ #Мир",
        "en": "#Analysis #News #BreakingNews #1DAY #World",
    }.get(lang, "")

    desc = (
        f"{chan_name} | {an_label} | {today}\n\n"
        + "\n".join(story_lines)
        + f"\n\n{'━'*30}\n{htags}\n#Analysis2026"
    )[:4900]

    all_kw = []
    for it in items:
        all_kw += it.get("keywords_en", [])
    tags = list(dict.fromkeys(all_kw))[:12] + ["News", "Analysis", "2026", "World"]

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
    print(f"  → Tahlil yuklash: {yt_title[:60]}")
    try:
        media    = MediaFileUpload(video_path, mimetype="video/mp4",
                                   resumable=True, chunksize=5 * 1024 * 1024)
        request  = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"     Upload: {int(status.progress() * 100)}%", end="\r")
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
        print(f"  Upload xato: {e}"); return None


# ─────────────────────────────────────────────────────────────
# Temp tozalash
# ─────────────────────────────────────────────────────────────
def _cleanup(ts: str, paths: list):
    for p in paths:
        try:
            if p and os.path.exists(p): os.remove(p)
        except Exception: pass
    prefix = ts[:14]
    for ext in ("jpg", "jpeg", "mp4", "mp3", "aac", "png"):
        for f in glob.glob(os.path.join(TEMP_DIR, f"an_*{prefix}*.{ext}")):
            try: os.remove(f)
            except: pass


# ─────────────────────────────────────────────────────────────
# ASOSIY FUNKSIYA
# ─────────────────────────────────────────────────────────────
def analysis_pipeline(items: list, lang: str) -> str | None:
    """
    Per-story TTS → video uzunligi TTS ga mos.
    Yangiliklar orasida jingle (chime + dark transition card).
    Ob-havo ticker pastda ko'rinadi.
    """
    items = [it for it in items[:MAX_ITEMS] if it.get("sarlavha")]
    n     = len(items)
    if n < MIN_ITEMS:
        print(f"  ⚠️  Tahlil uchun kamida {MIN_ITEMS} ta yangilik (bor: {n})")
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S%f")[:18]
    print(f"\n  🎙 Tahlil pipeline [{lang.upper()}]: {n} ta yangilik")
    os.makedirs(TEMP_DIR,   exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_temps   = []
    seen_pexels = set()
    pan_idx     = 0

    # Ob-havo + jingle
    print("   🌡 Ob-havo ticker olinmoqda...")
    weather = _get_weather_ticker(lang)
    jingle  = _get_jingle()
    print(f"   🌡 {weather[:60]}…" if weather else "   ⚠️ Ob-havo yo'q")
    jingle_status = "tayyor" if jingle else "yoq"
    print(f"   🔔 Jingle: {jingle_status}")

    # ── 1. PER-STORY TTS (avval) ──────────────────────────────
    # Har bir yangilik uchun alohida naratsiya ovozi yaratamiz.
    # Natijada video uzunligi = ovoz uzunligi (ortiqcha sukunat yo'q).
    story_voice_info = []   # [(voice_path, voice_dur), ...]
    for idx, item in enumerate(items):
        # Script olish: item["script"] = lang-specific script (app.py tomonidan to'g'ri tilga o'rnatilgan)
        script  = (item.get("script", "").strip() or
                   _iget(item, "jumla", lang))
        words   = script.split()[:WORDS_PER_STORY]
        text    = " ".join(words) if len(words) >= 5 else ""

        # Script 200 so'zdan kam bo'lsa — jumla1 ni ham qo'shish (ortiqcha matn olish)
        if len(text.split()) < 200:
            j1 = item.get("jumla1", "").strip()   # item["jumla1"] = til-specific jumla
            if j1 and j1[:40] not in text:
                combined = (text + " " + j1).strip()
                text = " ".join(combined.split()[:WORDS_PER_STORY])

        # Hali ham bo'sh — sarlavhadan foydalanish
        if not text.strip():
            text = (item.get("sarlavha", "").strip() or
                    _iget(item, "sarlavha", lang, "Xabar"))

        vp   = os.path.join(TEMP_DIR, f"an_voice_{ts}_{idx:02d}.mp3")
        ok   = _make_tts(text, lang, item.get("daraja", "xabar"), vp)
        vdur = _audio_dur(vp) if ok else 0
        if vdur < 2:
            # TTS muvaffaqiyatsiz — sarlavha bilan qayta
            _make_tts(_iget(item, "sarlavha", lang), lang, item.get("daraja", "xabar"), vp)
            vdur = _audio_dur(vp) if os.path.exists(vp) else 30.0
        vdur = max(vdur, 20.0)   # kamida 20s
        story_voice_info.append((vp if ok else None, vdur))
        all_temps.append(vp)
        print(f"     🎤 [{lang.upper()}] Yangilik {idx+1}: TTS {vdur:.1f}s")

    # ── 2. VIDEO SEGMENTLAR ───────────────────────────────────
    segments  = []
    durations = []

    # Ochilish kartasi
    open_img = os.path.join(TEMP_DIR, f"an_open_{ts}.jpg")
    open_vid = os.path.join(TEMP_DIR, f"an_open_{ts}.mp4")
    _make_open_card(lang, n, weather, open_img)
    if _still_to_video(open_img, OPEN_DUR, open_vid):
        segments.append(open_vid); durations.append(OPEN_DUR)
        all_temps += [open_img, open_vid]
    print(f"  ✓ Ochilish kartasi")

    for idx, item in enumerate(items):
        story_num  = idx + 1
        sarlavha   = _iget(item, "sarlavha", lang)
        location   = _iget(item, "location", lang)
        daraja     = item.get("daraja", "xabar")
        art_url    = item.get("article_url", "")
        kw         = item.get("keywords_en", [])
        en_title   = (_iget(item, "sarlavha", "en") or item.get("en_title", ""))
        _, vdur    = story_voice_info[idx]

        print(f"  ─ Yangilik {story_num}/{n}: {sarlavha[:55]}")

        # Sarlavha kartasi (TITLE_DUR)
        ttl_img = os.path.join(TEMP_DIR, f"an_ttl_{ts}_{idx:02d}.jpg")
        ttl_vid = os.path.join(TEMP_DIR, f"an_ttl_{ts}_{idx:02d}.mp4")
        _make_title_card(sarlavha, location, daraja, story_num, n, lang, ttl_img)
        if _still_to_video(ttl_img, TITLE_DUR, ttl_vid):
            segments.append(ttl_vid); durations.append(TITLE_DUR)
            all_temps += [ttl_img, ttl_vid]

        # Rasmlar — har biri voice_dur / PHOTOS_PER_STORY uzunligida
        photos_found = []

        og_path = os.path.join(TEMP_DIR, f"an_og_{ts}_{idx:02d}.jpg")
        if _fetch_og_image(art_url, og_path):
            photos_found.append(og_path); all_temps.append(og_path)
            print(f"     📰 og:image")

        queries = []
        if en_title and all(c.isascii() or not c.isalpha() for c in en_title):
            queries.append(en_title[:60])
        for k in kw[:4]:
            if k and all(c.isascii() or not c.isalpha() for c in k):
                queries.append(k)
        qi = 0
        while len(photos_found) < PHOTOS_PER_STORY and qi < len(queries):
            px_path = os.path.join(TEMP_DIR, f"an_px_{ts}_{idx:02d}_{qi}.jpg")
            if _fetch_pexels(queries[qi], px_path, seen_pexels):
                photos_found.append(px_path); all_temps.append(px_path)
                print(f"     📸 Pexels: {queries[qi][:35]}")
            qi += 1

        # Har foto uchun uzunlik = vdur / photos_found (yoki fallback)
        n_photos = max(len(photos_found), 1)
        per_dur  = max(vdur / n_photos, 18.0)   # kamida 18s

        for pi, photo_path in enumerate(photos_found[:PHOTOS_PER_STORY]):
            raw_bg  = os.path.join(TEMP_DIR, f"an_bg_{ts}_{idx:02d}_{pi}.jpg")
            ovl_png = os.path.join(TEMP_DIR, f"an_ovl_{ts}_{idx:02d}_{pi}.png")
            seg_vid = os.path.join(TEMP_DIR, f"an_seg_{ts}_{idx:02d}_{pi}.mp4")
            all_temps += [raw_bg, ovl_png, seg_vid]

            if not _crop_resize(photo_path, raw_bg):
                continue
            _make_overlay_png(sarlavha, location, daraja, lang,
                              story_num, n, pi, len(photos_found), weather, ovl_png)
            if _photo_composite(raw_bg, ovl_png, per_dur, pan_idx, seg_vid):
                segments.append(seg_vid); durations.append(per_dur)
                pan_idx += 1
                print(f"     ✓ Rasm {pi + 1} ({per_dur:.1f}s)")

        if not photos_found:
            # Fallback: sarlavha karta
            fb_img = os.path.join(TEMP_DIR, f"an_fb_{ts}_{idx:02d}.jpg")
            fb_vid = os.path.join(TEMP_DIR, f"an_fb_{ts}_{idx:02d}.mp4")
            all_temps += [fb_img, fb_vid]
            _make_title_card(sarlavha, location, daraja, story_num, n, lang, fb_img)
            if _still_to_video(fb_img, vdur, fb_vid):
                segments.append(fb_vid); durations.append(vdur)
                print(f"     ✓ Fallback karta ({vdur:.1f}s)")

        # Jingle o'tish kartasi (oxirgi yangilikdan keyin yo'q)
        if idx < n - 1:
            jcard_vid = os.path.join(TEMP_DIR, f"an_jcard_{ts}_{idx:02d}.mp4")
            all_temps.append(jcard_vid)
            if _make_jingle_card(ts, idx, jingle, JINGLE_DUR, jcard_vid):
                segments.append(jcard_vid); durations.append(JINGLE_DUR)
                print(f"     🔔 Jingle")

    # Yakunlash kartasi
    outro_img = os.path.join(TEMP_DIR, f"an_outro_{ts}.jpg")
    outro_vid = os.path.join(TEMP_DIR, f"an_outro_{ts}.mp4")
    _make_outro(lang, outro_img)
    if _still_to_video(outro_img, OUTRO_DUR, outro_vid):
        segments.append(outro_vid); durations.append(OUTRO_DUR)
        all_temps += [outro_img, outro_vid]
    print(f"  ✓ Yakunlash kartasi")

    if not segments:
        print("  ⚠️  Hech segment yaratilmadi")
        return None

    # ── 3. OVOZLARNI BIRLASHTIRISH (sinxronizatsiya bilan) ──────
    # Tuzilma (video bilan sinxron):
    #   [intro_jimlik=6s] [title1_jimlik=5s] voice1
    #   [jingle] [title2_jimlik=5s] voice2
    #   [jingle] [title3_jimlik=5s] voice3 ...
    # Bu har yangilikni naratsiyasi uning rasmlari bilan mos keladi.
    voice_parts = []

    # Intro kartasi jimlik (video ochilish bilan sinxron)
    sil_intro = os.path.join(TEMP_DIR, f"an_sil_intro_{ts}.aac")
    all_temps.append(sil_intro)
    if _create_silence(OPEN_DUR, sil_intro):
        voice_parts.append(sil_intro)

    for idx, (vp, vdur) in enumerate(story_voice_info):
        # Sarlavha kartasi jimlik (5s title card bilan sinxron)
        sil_title = os.path.join(TEMP_DIR, f"an_sil_ttl_{ts}_{idx:02d}.aac")
        all_temps.append(sil_title)
        if _create_silence(TITLE_DUR, sil_title):
            voice_parts.append(sil_title)

        # Yangilik naratsiyasi
        if vp and os.path.exists(vp):
            voice_parts.append(vp)

        # Jingle (oxirgi yangilikdan keyin emas)
        if idx < n - 1 and jingle and os.path.exists(jingle):
            voice_parts.append(jingle)

    combined_voice = os.path.join(TEMP_DIR, f"an_voice_{ts}.mp3")
    all_temps.append(combined_voice)
    if not _concat_audios(voice_parts, combined_voice):
        # Fallback: birinchi voice
        for vp, _ in story_voice_info:
            if vp and os.path.exists(vp):
                shutil.copy(vp, combined_voice)
                break

    # ── 4. CONCAT ─────────────────────────────────────────────
    concat_vid = os.path.join(TEMP_DIR, f"an_concat_{ts}.mp4")
    all_temps.append(concat_vid)
    if not _concat_xfade(segments, durations, concat_vid):
        print("  ⚠️  Concat xato")
        _cleanup(ts, all_temps)
        return None
    print(f"  ✓ Concat: {len(segments)} segment, ~{sum(durations):.0f}s")

    # ── 5. AUDIO MIX ──────────────────────────────────────────
    out_name = f"{ts}_analysis_{lang}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    if os.path.exists(combined_voice):
        ok = _mix_audio(concat_vid, combined_voice, out_path, lang)
    else:
        ok = False

    if not ok:
        shutil.copy(concat_vid, out_path)
        print("  ℹ️  Audio mix yo'q — faqat video")

    # ── 6. TOZALASH ───────────────────────────────────────────
    _cleanup(ts, all_temps)

    if not os.path.exists(out_path):
        print("  ⚠️  Yakuniy video topilmadi")
        return None

    sz  = os.path.getsize(out_path) / 1_048_576
    dur = sum(durations)
    print(f"\n  ✅ {out_name}  ({sz:.1f} MB, ~{dur:.0f}s = {int(dur//60)}:{int(dur%60):02d})")

    # ── 7. YOUTUBE YUKLASH ────────────────────────────────────
    yt_vid_id = _upload_analysis(out_path, items, lang)
    yt_url    = f"https://youtu.be/{yt_vid_id}" if yt_vid_id else ""

    return out_path, yt_url
