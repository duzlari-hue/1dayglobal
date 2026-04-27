"""livestream_smart.py — SMART OVERLAY 24/7 YouTube Live.

Aralash UZ/RU/EN videolar bitta playlistda. Python "dirijyor" thread
har soniyada qaysi video o'ynayotganini hisoblab, overlay matnlarini
videoning tiliga moslashtiradi:

    Video 1 (UZ) — overlay: 1KUN GLOBAL, узбекча тикер
    Video 2 (RU) — overlay: 1ДЕНЬ GLOBAL, русча тикер
    Video 3 (EN) — overlay: 1DAY GLOBAL, инглизча тикер
    ...

ffmpeg ham qayta ishga tushmasdan, faqat textfile'larni reload qiladi.

Foydalanish:
    py livestream_smart.py
    py livestream_smart.py --langs uz,ru,en      # qaysi tillar aralashadi
    py livestream_smart.py --langs uz,uz,ru,en   # UZ ko'proq turadigan
    py livestream_smart.py --test                # 60s lokal MP4
"""
import os
import sys
import time
import json
import glob
import random
import logging
import argparse
import subprocess
import threading
import pathlib
from datetime import datetime
import pytz

from dotenv import load_dotenv
load_dotenv()

# Reuse from livestream.py
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import livestream as ls

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("smart")

ROOT       = pathlib.Path(__file__).parent
VIDEOS_DIR = ROOT / "output" / "videos"
LIVE_DIR   = ROOT / "output" / "live"
TASHKENT   = pytz.timezone("Asia/Tashkent")


def _ffprobe_duration(path: str) -> float:
    """Video uzunligini sekundlarda olish (ffprobe)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip())
    except Exception as e:
        log.debug(f"ffprobe {path}: {e}")
        return 60.0   # default


def _detect_lang_from_path(path: str) -> str:
    """Faylnomidan tilni aniqlash: *_uz.mp4 → uz."""
    name = pathlib.Path(path).stem.lower()
    for lang in ("uz", "ru", "en"):
        if name.endswith(f"_{lang}"):
            return lang
    return "uz"   # default


def _build_smart_playlist(allowed_langs: list, randomize: bool = True) -> tuple:
    """Aralash playlist yasash. Qaytaradi: (playlist_path, segments).

    segments: [{"path": ..., "lang": ..., "duration": ..., "cum_start": ...}]
    """
    all_videos = []
    for lang in set(allowed_langs):
        pattern = str(VIDEOS_DIR / f"*_{lang}.mp4")
        all_videos.extend(glob.glob(pattern))

    if not all_videos:
        raise SystemExit("output/videos/ da hech qanday video yo'q!")

    # Multiplikatorga qarab har lang qo'shimcha kelishi mumkin
    # (--langs uz,uz,ru,en bilan UZ ikki barobar)
    weighted = []
    for l in allowed_langs:
        l_videos = [v for v in all_videos if _detect_lang_from_path(v) == l]
        weighted.extend(l_videos)

    if randomize:
        random.shuffle(weighted)

    # Har video uchun duration olish
    log.info(f"📊 {len(weighted)} ta video uchun duration o'lchanmoqda...")
    segments = []
    cum = 0.0
    for v in weighted:
        d = _ffprobe_duration(v)
        lang = _detect_lang_from_path(v)
        segments.append({
            "path":      v,
            "lang":      lang,
            "duration":  d,
            "cum_start": cum,
        })
        cum += d
    total_dur = cum
    log.info(f"   Jami davomiyligi: {total_dur:.0f}s ({total_dur/3600:.1f} soat)")

    # ffmpeg uchun playlist .txt
    playlist_path = LIVE_DIR / "playlist_smart.txt"
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    with open(playlist_path, "w", encoding="utf-8") as f:
        for s in segments:
            v_esc = s["path"].replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{v_esc}'\n")

    # Xulosa
    lang_count = {}
    for s in segments:
        lang_count[s["lang"]] = lang_count.get(s["lang"], 0) + 1
    log.info(f"   Tillar: " + ", ".join(f"{k.upper()}={v}" for k, v in lang_count.items()))

    return playlist_path, segments, total_dur


def _write_overlay_for_lang(lang: str):
    """livestream._refresh_text_files'ni chaqirib overlay matnlarini lang ga moslash."""
    try:
        # Smart overlay uchun bitta SET fayl: breaking_smart.txt, ticker_smart.txt ...
        # _refresh_text_files lang bo'yicha alohida fayllar ochadi, biz unidan ko'chiramiz.
        ls._refresh_text_files(lang)
        # Smart fayllarga ko'chirish
        for kind in ("breaking", "ticker", "keyingi", "weather"):
            src = LIVE_DIR / f"{kind}_{lang}.txt"
            dst = LIVE_DIR / f"{kind}_smart.txt"
            if src.exists():
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        # Logo
        logo_text = {"uz": "1KUN GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN GLOBAL")
        (LIVE_DIR / "logo_smart.txt").write_text(logo_text, encoding="utf-8")
    except Exception as e:
        log.warning(f"Overlay yangilash xato: {e}")


def _conductor_loop(segments: list, total_dur: float, t_start_ref: list,
                     stop_event: threading.Event):
    """Dirijyor: vaqt bo'yicha qaysi video o'ynashini hisoblab,
    overlay matnlarini shu video tiliga moslashtiradi."""
    last_lang = None
    last_refresh_time = 0   # 5 daqiqada bir source matnlarni yangilash

    while not stop_event.is_set():
        try:
            elapsed = time.time() - t_start_ref[0]
            cycle_t = elapsed % total_dur

            # Joriy segmentni topish (binary search emas, oddiy linear — segments soni kichik)
            current = segments[0]
            for s in segments:
                if s["cum_start"] <= cycle_t < s["cum_start"] + s["duration"]:
                    current = s
                    break

            # Til o'zgargan bo'lsa — overlay'ni yangilash
            if current["lang"] != last_lang:
                log.info(f"🔄 Til o'zgardi: {current['lang'].upper()}  "
                         f"(video: {pathlib.Path(current['path']).name[:50]})")
                _write_overlay_for_lang(current["lang"])
                last_lang = current["lang"]
                last_refresh_time = time.time()

            # 5 daqiqaga bir matnlarni qayta yangilash (yangi yangiliklar tushishi uchun)
            elif time.time() - last_refresh_time > 300:
                _write_overlay_for_lang(current["lang"])
                last_refresh_time = time.time()

            # Soat har 30s yangilanadi
            (LIVE_DIR / "clock_smart.txt").write_text(
                datetime.now(TASHKENT).strftime("%H:%M"), encoding="utf-8"
            )
        except Exception as e:
            log.warning(f"Conductor xato: {e}")
        stop_event.wait(2)   # har 2s tekshirish (kichik kechikish kifoya)


def _build_smart_filter() -> tuple:
    """Smart-overlay uchun filter — barcha textfile'lar 'smart' versiyasini o'qiydi."""
    breaking = LIVE_DIR / "breaking_smart.txt"
    ticker   = LIVE_DIR / "ticker_smart.txt"
    keyingi  = LIVE_DIR / "keyingi_smart.txt"
    weather  = LIVE_DIR / "weather_smart.txt"
    clock    = LIVE_DIR / "clock_smart.txt"
    logo     = LIVE_DIR / "logo_smart.txt"

    # Default lang sifatida UZ — initial overlay
    _write_overlay_for_lang("uz")

    # Clock fayli — boshlash paytida ham mavjud bo'lishi kerak
    clock.write_text(datetime.now(TASHKENT).strftime("%H:%M"), encoding="utf-8")

    return ls._build_filter(breaking, ticker, keyingi, weather, clock,
                              lang="uz", logo_path=logo)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--langs", default="uz,ru,en",
                        help="Qaysi tillar aralashadi (vergul bilan)")
    parser.add_argument("--test", action="store_true",
                        help="60s lokal MP4 — RTMP emas")
    args = parser.parse_args()

    allowed_langs = [l.strip() for l in args.langs.split(",")
                     if l.strip() in ("uz", "ru", "en")]
    if not allowed_langs:
        allowed_langs = ["uz", "ru", "en"]

    stream_key = os.getenv("YT_LIVE_STREAM_KEY_UZ", "").strip()
    if not args.test and not stream_key:
        log.error("❌ .env da YT_LIVE_STREAM_KEY_UZ yo'q!")
        return

    log.info(f"🎬 SMART OVERLAY stream — tillar: {','.join(allowed_langs).upper()}")

    # 1. Aralash playlist va segments
    playlist_path, segments, total_dur = _build_smart_playlist(allowed_langs)

    # 2. Filter (smart fayllarni o'qiydi)
    filter_graph, last_label = _build_smart_filter()

    # 3. Conductor thread
    t_start_ref = [time.time()]
    stop_event = threading.Event()
    cond = threading.Thread(target=_conductor_loop,
                              args=(segments, total_dur, t_start_ref, stop_event),
                              daemon=True)
    cond.start()

    # 4. ffmpeg buyrug'i
    rtmp = f"{ls.RTMP_BASE}/{stream_key}" if not args.test else ""
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
        "-b:v", "1500k", "-maxrate", "1800k", "-bufsize", "3000k",
        "-pix_fmt", "yuv420p",
        "-g", "50", "-keyint_min", "50",
        "-r", "25",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
    ]
    if not args.test:
        cmd.insert(1, "-re")
        cmd += ["-f", "flv", rtmp]
    else:
        cmd += ["-t", "60", "-f", "mp4", str(LIVE_DIR / "test_smart.mp4")]

    if args.test:
        log.info(f"🎬 TEST → {LIVE_DIR}/test_smart.mp4")
    else:
        log.info(f"📡 RTMP → live2/{stream_key[:8]}...")
    log.info(f"   Conductor thread ishga tushdi — overlay videoga moslanadi")
    log.info(f"   Ctrl+C bilan to'xtatish")

    # ffmpeg ishga tushishidan oldin t_start ni reset qilamiz
    t_start_ref[0] = time.time()
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        log.info("⏹️ To'xtatildi")
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
