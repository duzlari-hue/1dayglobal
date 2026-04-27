"""livestream.py — 24/7 YouTube Live News stream — PROFESSIONAL NEWS STUDIO.

Layout (1920x1080):
┌────────────────────────────────────────────────────────────┐
│ 🌍 1KUN GLOBAL    [weather rotating]         🕐 HH:MM     │ ← 60px
├────────────────────────────────────────────────────────────┤
│                                                            │
│                  [DIGEST VIDEO — 1920x780]                │
│                                                            │
├────────────────────────────────────────────────────────────┤
│ ⚡ ЯНГИЛИК: [breaking news headline]                       │ ← 80px RED
│ • headline 1 • headline 2 • headline 3 → SCROLL           │ ← 60px DARK
│ КЕЙИНГИ: [next story]            📍 Toshkent  27.04.2026  │ ← 50px BLUE
└────────────────────────────────────────────────────────────┘

Foydalanish:
    py livestream.py --lang uz                 # UZ live → YouTube RTMP
    py livestream.py --lang uz --test          # 60s lokal MP4 test
    py livestream.py --refresh-only --lang uz  # Faqat matnlarni yangilash
"""
import os
import sys
import json
import time
import glob
import random
import logging
import argparse
import subprocess
import threading
import pathlib
import requests
from datetime import datetime
import pytz

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("livestream")

ROOT       = pathlib.Path(__file__).parent
VIDEOS_DIR = ROOT / "output" / "videos"
LIVE_DIR   = ROOT / "output" / "live"
LIVE_DIR.mkdir(parents=True, exist_ok=True)

FONT_BOLD  = "C:\\Windows\\Fonts\\arialbd.ttf"
FONT_REG   = "C:\\Windows\\Fonts\\arial.ttf"

RTMP_BASE  = "rtmp://a.rtmp.youtube.com/live2"
TASHKENT   = pytz.timezone("Asia/Tashkent")

REFRESH_INTERVAL = 300   # 5 daqiqa

# Output 720p (CPU-friendly, jonli efir uchun yetarli)
OUT_W, OUT_H = 1280, 720

# Layout maydonlari (720p uchun proporsional)
HEADER_H   = 45       # tepa polosa
BREAKING_H = 60       # qizil breaking strip
TICKER_H   = 45       # scroll ticker
SUBINFO_H  = 38       # ko'k pastki info
BOTTOM_H   = BREAKING_H + TICKER_H + SUBINFO_H   # 143px

VIDEO_AREA_Y = HEADER_H
VIDEO_AREA_H = OUT_H - HEADER_H - BOTTOM_H        # 830px


# ── Banner/Ticker matnlarini yangilash ────────────────────────────
def _read_recent_titles(lang: str = "uz", n: int = 15) -> list:
    """queue/done dagi eng so'nggi N ta sarlavha (lang bo'yicha)."""
    titles = []
    files = sorted(
        glob.glob(str(ROOT / "queue" / "done" / "*.json")),
        key=os.path.getmtime, reverse=True
    )[:n * 3]
    for f in files:
        try:
            d = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
            sv = d.get("sarlavha", {}).get(lang, "").strip()
            if sv and sv not in titles:
                titles.append(sv)
            if len(titles) >= n:
                break
        except Exception:
            continue
    return titles


def _fetch_weather(cities: list) -> dict:
    """Open-Meteo API (free, no key) — temperature for cities."""
    coords = {
        "Toshkent":  (41.31, 69.24),
        "London":    (51.51, -0.13),
        "Nyu-York":  (40.71, -74.00),
        "Tehron":    (35.69, 51.39),
        "Pekin":     (39.90, 116.40),
        "Moskva":    (55.75, 37.62),
        "Dubay":     (25.27, 55.30),
        "Istanbul":  (41.01, 28.97),
    }
    out = {}
    for city in cities:
        if city not in coords:
            continue
        try:
            lat, lon = coords[city]
            r = requests.get(
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}&current_weather=true",
                timeout=8,
            )
            if r.status_code == 200:
                t = r.json().get("current_weather", {}).get("temperature")
                if t is not None:
                    out[city] = round(t)
        except Exception as e:
            log.debug(f"Weather {city}: {e}")
    return out


def _refresh_text_files(lang: str = "uz"):
    """Barcha overlay matnlarini yangilash."""
    titles = _read_recent_titles(lang, 15)
    if not titles:
        default_msg = {
            "uz": "1KUN GLOBAL — Jonli efir 24/7 — Dunyo yangiliklari",
            "ru": "1ДЕНЬ GLOBAL — Прямой эфир 24/7 — Новости мира",
            "en": "1DAY GLOBAL — Live 24/7 — World News",
        }
        titles = [default_msg.get(lang, default_msg["en"])]

    # 1. BREAKING (qizil, eng so'nggi 1 ta) — 60 ta belgi maximum
    breaking_path = LIVE_DIR / f"breaking_{lang}.txt"
    label = {"uz": "ЯНГИЛИК", "ru": "СРОЧНО", "en": "BREAKING"}.get(lang, "BREAKING")
    breaking = titles[0][:65]
    breaking_path.write_text(f"  ⚡ {label}: {breaking}  ", encoding="utf-8")

    # 2. TICKER (scroll, 12 ta sarlavha)
    ticker_path = LIVE_DIR / f"ticker_{lang}.txt"
    sep = "    ◆    "
    ticker_text = sep.join(titles[1:13]) + sep
    ticker_path.write_text(ticker_text, encoding="utf-8")

    # 3. KEYINGI (ko'k pastki) — keyingi sarlavha
    keyingi_path = LIVE_DIR / f"keyingi_{lang}.txt"
    next_label = {"uz": "КЕЙИНГИ", "ru": "ДАЛЕЕ", "en": "NEXT"}.get(lang, "NEXT")
    city_label = {"uz": "Toshkent", "ru": "Ташкент", "en": "Tashkent"}.get(lang, "Tashkent")
    next_title = (titles[1] if len(titles) > 1 else titles[0])[:55]
    today_str  = datetime.now(TASHKENT).strftime("%d.%m.%Y")
    keyingi_path.write_text(
        f"  {next_label}: {next_title}    📍 {city_label}  {today_str}  ",
        encoding="utf-8"
    )

    # 5. CLOCK — alohida fayl, har 30s yangilanadi
    clock_path = LIVE_DIR / f"clock_{lang}.txt"
    clock_path.write_text(
        datetime.now(TASHKENT).strftime("%H:%M"), encoding="utf-8"
    )

    # 4. WEATHER (header — markazda aylanib turuvchi) — lang bo'yicha shahar nomlari
    weather_path = LIVE_DIR / f"weather_{lang}.txt"
    cities_by_lang = {
        "uz": ["Toshkent", "London", "Nyu-York", "Tehron", "Pekin", "Moskva"],
        "ru": ["Toshkent", "London", "Nyu-York", "Tehron", "Pekin", "Moskva"],
        "en": ["Toshkent", "London", "Nyu-York", "Tehron", "Pekin", "Dubay"],
    }
    label_map = {
        "uz": {"Toshkent":"Toshkent","London":"London","Nyu-York":"Nyu-York","Tehron":"Tehron","Pekin":"Pekin","Moskva":"Moskva","Dubay":"Dubay"},
        "ru": {"Toshkent":"Ташкент","London":"Лондон","Nyu-York":"Нью-Йорк","Tehron":"Тегеран","Pekin":"Пекин","Moskva":"Москва","Dubay":"Дубай"},
        "en": {"Toshkent":"Tashkent","London":"London","Nyu-York":"New York","Tehron":"Tehran","Pekin":"Beijing","Moskva":"Moscow","Dubay":"Dubai"},
    }
    cities = cities_by_lang.get(lang, cities_by_lang["uz"])
    w = _fetch_weather(cities)
    lmap = label_map.get(lang, label_map["uz"])
    if w:
        parts = [f"{lmap.get(c, c)} {('+' if t > 0 else '')}{t}°" for c, t in w.items()]
        weather_text = "    ●    ".join(parts)
    else:
        weather_text = {
            "uz": "1KUN GLOBAL  ●  Jonli efir  ●  Dunyo yangiliklari",
            "ru": "1ДЕНЬ GLOBAL  ●  Прямой эфир  ●  Новости мира",
            "en": "1DAY GLOBAL  ●  Live  ●  World News",
        }.get(lang, "1DAY GLOBAL — Live")
    weather_path.write_text(f"   {weather_text}   ", encoding="utf-8")

    log.info(f"  Yangilandi: breaking + ticker + keyingi + weather + clock [{lang}]")
    return breaking_path, ticker_path, keyingi_path, weather_path, clock_path


def _clock_loop(lang: str, stop_event: threading.Event):
    """Soatni har 30s yangilab turish."""
    clock_path = LIVE_DIR / f"clock_{lang}.txt"
    while not stop_event.is_set():
        try:
            clock_path.write_text(
                datetime.now(TASHKENT).strftime("%H:%M"), encoding="utf-8"
            )
        except Exception:
            pass
        stop_event.wait(30)


def _refresh_loop(lang: str, stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            _refresh_text_files(lang)
        except Exception as e:
            log.warning(f"Refresh xato: {e}")
        stop_event.wait(REFRESH_INTERVAL)


# ── Concat playlist ───────────────────────────────────────────────
def _build_playlist(lang: str = "uz") -> pathlib.Path:
    videos = sorted(glob.glob(str(VIDEOS_DIR / f"*_{lang}.mp4")))
    if not videos:
        videos = sorted(glob.glob(str(VIDEOS_DIR / "*.mp4")))
    if not videos:
        raise SystemExit(f"output/videos/ da {lang} videolari yo'q!")
    random.shuffle(videos)
    playlist_path = LIVE_DIR / f"playlist_{lang}.txt"
    with open(playlist_path, "w", encoding="utf-8") as f:
        for v in videos:
            v_esc = v.replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{v_esc}'\n")
    log.info(f"  Playlist: {len(videos)} ta video [{lang}]")
    return playlist_path


def _esc(p) -> str:
    """ffmpeg drawtext path escape (Windows uchun)."""
    return str(p).replace("\\", "/").replace(":", "\\:")


# ── Asosiy ffmpeg filter ──────────────────────────────────────────
def _build_filter(breaking_path, ticker_path, keyingi_path, weather_path,
                   clock_path, lang: str = "uz", logo_path=None) -> tuple:
    """Murakkab overlay filter — header + bottom 3 strips.

    logo_path — agar berilgan bo'lsa, logo matni shu fayldan o'qiladi (smart overlay).
    Aks holda — lang asosida statik logo.
    """
    fb = _esc(FONT_BOLD)
    fr = _esc(FONT_REG)
    logo_text = {"uz": "1KUN GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN GLOBAL")

    # Y koordinatalari
    y_breaking = OUT_H - BOTTOM_H                       # 890
    y_ticker   = OUT_H - BOTTOM_H + BREAKING_H          # 970
    y_keyingi  = OUT_H - SUBINFO_H                      # 1030

    parts = []

    # 1. Asosiy video — pad qora bilan, header va bottom uchun joy qoldirish
    #    Avval scale, keyin pad qilamiz
    parts.append(
        f"[0:v]scale={OUT_W}:{VIDEO_AREA_H}:force_original_aspect_ratio=decrease,"
        f"pad={OUT_W}:{VIDEO_AREA_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"pad={OUT_W}:{OUT_H}:0:{HEADER_H}:color=#0a0e1a[bg]"
    )

    chain = "[bg]"
    last  = "v0"

    # 2. HEADER — qora fon (yuqorida 60px)
    parts.append(f"{chain}drawbox=x=0:y=0:w={OUT_W}:h={HEADER_H}:color=#0a0e1a@1.0:t=fill[{last}]")
    chain = f"[{last}]"

    # 3. Header chap: logo (qizil) + LIVE bayroqcha (720p — kichikroq)
    last = "v1"
    if logo_path:
        logo_esc = _esc(logo_path)
        parts.append(
            f"{chain}drawtext=textfile='{logo_esc}':reload=1:fontfile='{fb}':"
            f"fontsize=24:fontcolor=#e63946:x=20:y=10,"
            f"drawtext=text='LIVE':fontfile='{fb}':"
            f"fontsize=16:fontcolor=white:box=1:boxcolor=#e63946@1.0:boxborderw=5:"
            f"x=215:y=14[{last}]"
        )
    else:
        parts.append(
            f"{chain}drawtext=text='{logo_text}':fontfile='{fb}':"
            f"fontsize=24:fontcolor=#e63946:x=20:y=10,"
            f"drawtext=text='LIVE':fontfile='{fb}':"
            f"fontsize=16:fontcolor=white:box=1:boxcolor=#e63946@1.0:boxborderw=5:"
            f"x=200:y=14[{last}]"
        )
    chain = f"[{last}]"

    # 4. Header markaz: weather
    last = "v2"
    weather_esc = _esc(weather_path)
    parts.append(
        f"{chain}drawtext=textfile='{weather_esc}':reload=1:"
        f"fontfile='{fr}':fontsize=16:fontcolor=#aab8c8:"
        f"x=w-mod(t*30\\,w+text_w):y=14[{last}]"
    )
    chain = f"[{last}]"

    # 5. Header o'ng: jonli soat HH:MM
    last = "v3"
    clock_esc = _esc(clock_path)
    parts.append(
        f"{chain}drawtext=textfile='{clock_esc}':reload=1:"
        f"fontfile='{fb}':fontsize=24:fontcolor=white:"
        f"x=w-text_w-20:y=10[{last}]"
    )
    chain = f"[{last}]"

    # 6. BOTTOM strip 1: BREAKING (qizil, 80px)
    last = "v4"
    parts.append(
        f"{chain}drawbox=x=0:y={y_breaking}:w={OUT_W}:h={BREAKING_H}:"
        f"color=#e63946@1.0:t=fill[{last}]"
    )
    chain = f"[{last}]"

    last = "v5"
    breaking_esc = _esc(breaking_path)
    parts.append(
        f"{chain}drawtext=textfile='{breaking_esc}':reload=1:"
        f"fontfile='{fb}':fontsize=28:fontcolor=white:"
        f"x=20:y={y_breaking + 14}[{last}]"
    )
    chain = f"[{last}]"

    # 7. BOTTOM strip 2: TICKER (qora fon, scroll, 60px)
    last = "v6"
    parts.append(
        f"{chain}drawbox=x=0:y={y_ticker}:w={OUT_W}:h={TICKER_H}:"
        f"color=#1a1f2e@1.0:t=fill[{last}]"
    )
    chain = f"[{last}]"

    last = "v7"
    ticker_esc = _esc(ticker_path)
    parts.append(
        f"{chain}drawtext=textfile='{ticker_esc}':reload=1:"
        f"fontfile='{fr}':fontsize=22:fontcolor=#ffd166:"
        f"x=w-mod(t*80\\,w+text_w):y={y_ticker + 10}[{last}]"
    )
    chain = f"[{last}]"

    # 8. BOTTOM strip 3: KEYINGI (ko'k fon, 50px)
    last = "v8"
    parts.append(
        f"{chain}drawbox=x=0:y={y_keyingi}:w={OUT_W}:h={SUBINFO_H}:"
        f"color=#073b4c@1.0:t=fill[{last}]"
    )
    chain = f"[{last}]"

    last = "v9"
    keyingi_esc = _esc(keyingi_path)
    parts.append(
        f"{chain}drawtext=textfile='{keyingi_esc}':reload=1:"
        f"fontfile='{fb}':fontsize=18:fontcolor=white:"
        f"x=20:y={y_keyingi + 8}[{last}]"
    )
    chain = f"[{last}]"

    # 9. Yon chiziq qizil — header pastida (1px ajratuvchi)
    last = "vfinal"
    parts.append(
        f"{chain}drawbox=x=0:y={HEADER_H - 2}:w={OUT_W}:h=2:"
        f"color=#e63946@1.0:t=fill[{last}]"
    )

    return ";".join(parts), f"[{last}]"


def _build_ffmpeg_cmd(playlist_path, breaking_p, ticker_p, keyingi_p, weather_p,
                      clock_p, lang: str, output_target: str, test_mode: bool = False) -> list:
    filter_graph, last_label = _build_filter(breaking_p, ticker_p, keyingi_p,
                                              weather_p, clock_p, lang)

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "concat", "-safe", "0",
        "-stream_loop", "-1",
        "-i", str(playlist_path),
        "-filter_complex", filter_graph,
        "-map", last_label, "-map", "0:a?",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-b:v", "2800k", "-maxrate", "2800k", "-bufsize", "5600k",
        "-pix_fmt", "yuv420p",
        "-g", "50", "-keyint_min", "50",
        "-r", "25",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
    ]

    # -re realtime (live uchun)
    if not test_mode:
        cmd.insert(1, "-re")

    if test_mode:
        cmd += ["-t", "60", "-f", "mp4", str(LIVE_DIR / "test_stream.mp4")]
    else:
        cmd += ["-f", "flv", output_target]

    return cmd


# ── Main ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", default="uz", choices=["uz", "ru", "en"])
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--refresh-only", action="store_true")
    args = parser.parse_args()

    if args.refresh_only:
        _refresh_text_files(args.lang)
        return

    key_var = f"YT_LIVE_STREAM_KEY_{args.lang.upper()}"
    stream_key = os.getenv(key_var, "").strip()

    if not args.test and not stream_key:
        log.error(f"❌ .env da {key_var} yo'q!")
        return

    log.info(f"🎥 Live stream tayyorlanmoqda [{args.lang.upper()}]  —  Studio Mode")
    playlist = _build_playlist(args.lang)
    breaking_p, ticker_p, keyingi_p, weather_p, clock_p = _refresh_text_files(args.lang)

    stop_event = threading.Event()
    refresher = threading.Thread(target=_refresh_loop,
                                  args=(args.lang, stop_event), daemon=True)
    refresher.start()
    clock_th = threading.Thread(target=_clock_loop,
                                  args=(args.lang, stop_event), daemon=True)
    clock_th.start()

    output_target = f"{RTMP_BASE}/{stream_key}" if not args.test else ""
    cmd = _build_ffmpeg_cmd(playlist, breaking_p, ticker_p, keyingi_p,
                             weather_p, clock_p, args.lang, output_target, args.test)

    if args.test:
        log.info(f"🎬 TEST — 60s MP4 → {LIVE_DIR}/test_stream.mp4")
    else:
        log.info(f"📡 RTMP → {RTMP_BASE}/{stream_key[:8]}...")
    log.info("   Ctrl+C bilan to'xtatish")

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        log.info("⏹️ To'xtatildi")
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
