"""livestream_rotate.py — 1 ta YouTube Live stream + til rotatsiyasi.

Bitta stream key'ga 24/7 efir, har 2 soatda til o'zgaradi:
    00:00-02:00 UZ → 02:00-04:00 RU → 04:00-06:00 EN → 06:00-08:00 UZ ...

Foydalanish:
    py livestream_rotate.py                     # 2 soatlik rotatsiya
    py livestream_rotate.py --hours 1           # 1 soatda til o'zgaradi
    py livestream_rotate.py --start-lang ru     # RU dan boshlash
    py livestream_rotate.py --order uz,uz,ru,en # UZ ko'proq turadigan tartib

.env da bo'lishi shart:
    YT_LIVE_STREAM_KEY_UZ=xxxx-xxxx-xxxx-xxxx
    (Bitta key kifoya — barcha tillar shu key'ga jonatadi)
"""
import os
import sys
import time
import signal
import logging
import argparse
import subprocess
import threading
import pathlib
from datetime import datetime, timedelta
import pytz

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rotate")

ROOT       = pathlib.Path(__file__).parent
LIVE_DIR   = ROOT / "output" / "live"
TASHKENT   = pytz.timezone("Asia/Tashkent")
PYTHON_EXE = sys.executable


def _get_stream_key() -> str:
    """UZ key default, qolganlari fallback."""
    for var in ("YT_LIVE_STREAM_KEY_UZ", "YT_LIVE_STREAM_KEY_RU",
                "YT_LIVE_STREAM_KEY_EN"):
        k = os.getenv(var, "").strip()
        if k:
            return k
    raise SystemExit("❌ .env da YT_LIVE_STREAM_KEY_UZ yo'q!")


def _build_ffmpeg_for_lang(lang: str, stream_key: str) -> list:
    """livestream.py ichidagi build_ffmpeg_cmd ni ishlatamiz."""
    sys.path.insert(0, str(ROOT))
    import livestream as ls

    playlist = ls._build_playlist(lang)
    breaking_p, ticker_p, keyingi_p, weather_p, clock_p = ls._refresh_text_files(lang)

    rtmp = f"{ls.RTMP_BASE}/{stream_key}"
    cmd = ls._build_ffmpeg_cmd(playlist, breaking_p, ticker_p, keyingi_p,
                                weather_p, clock_p, lang, rtmp, test_mode=False)
    return cmd


def _refresh_loop(lang_holder: dict, stop_event: threading.Event):
    """Background — har 5 daqiqada matnlarni yangilaydi (joriy lang uchun)."""
    sys.path.insert(0, str(ROOT))
    import livestream as ls

    while not stop_event.is_set():
        try:
            ls._refresh_text_files(lang_holder["lang"])
        except Exception as e:
            log.warning(f"Refresh xato: {e}")
        stop_event.wait(300)


def _clock_loop(lang_holder: dict, stop_event: threading.Event):
    """Soat fayli — har 30s yangilanadi (joriy lang uchun)."""
    while not stop_event.is_set():
        try:
            (LIVE_DIR / f"clock_{lang_holder['lang']}.txt").write_text(
                datetime.now(TASHKENT).strftime("%H:%M"), encoding="utf-8"
            )
        except Exception:
            pass
        stop_event.wait(30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=2.0,
                        help="Har necha soatda til o'zgaradi (default: 2)")
    parser.add_argument("--start-lang", default=None,
                        help="Qaysi tildan boshlash (uz/ru/en)")
    parser.add_argument("--order", default="uz,ru,en",
                        help="Aylanish tartibi (vergul bilan)")
    args = parser.parse_args()

    stream_key = _get_stream_key()
    rotation = [l.strip() for l in args.order.split(",") if l.strip() in ("uz","ru","en")]
    if not rotation:
        rotation = ["uz", "ru", "en"]

    # Boshlash tilini aniqlash
    if args.start_lang and args.start_lang in rotation:
        idx = rotation.index(args.start_lang)
    else:
        # Joriy soatga qarab boshlash (mantiqiy davom)
        hour = datetime.now(TASHKENT).hour
        idx = (hour // int(args.hours)) % len(rotation)

    interval = int(args.hours * 3600)   # sekund
    log.info(f"🎥 Til rotatsiyasi: {' → '.join(rotation)}")
    log.info(f"   Har {args.hours} soatda almashinadi")
    log.info(f"   Boshlash: {rotation[idx].upper()}")
    log.info(f"   Stream key: {stream_key[:8]}...")

    # Background threads (refresh va clock — joriy tilga moslashadi)
    lang_holder = {"lang": rotation[idx]}
    stop_event = threading.Event()
    threading.Thread(target=_refresh_loop, args=(lang_holder, stop_event),
                      daemon=True).start()
    threading.Thread(target=_clock_loop, args=(lang_holder, stop_event),
                      daemon=True).start()

    current_proc = None

    def cleanup(*a):
        log.info("⏹️  To'xtatilmoqda...")
        stop_event.set()
        if current_proc and current_proc.poll() is None:
            try:
                current_proc.terminate()
                time.sleep(2)
                if current_proc.poll() is None:
                    current_proc.kill()
            except Exception:
                pass
        sys.exit(0)

    if os.name != "nt":
        signal.signal(signal.SIGINT, cleanup)
        signal.signal(signal.SIGTERM, cleanup)

    # Asosiy loop
    try:
        while True:
            lang = rotation[idx]
            lang_holder["lang"] = lang
            cmd = _build_ffmpeg_for_lang(lang, stream_key)

            log.info(f"")
            log.info(f"🎙️  ═══════ STREAM [{lang.upper()}] ═══════")
            log.info(f"   Boshlandi: {datetime.now(TASHKENT).strftime('%H:%M')}")
            log.info(f"   Keyingi til: {rotation[(idx+1) % len(rotation)].upper()}")
            log.info(f"   Davomiyligi: {args.hours} soat")

            current_proc = subprocess.Popen(cmd)
            t_start = time.time()

            # interval davomida kutamiz, lekin ffmpeg crash bo'lsa darhol qaytadan
            while time.time() - t_start < interval:
                if current_proc.poll() is not None:
                    log.warning(f"⚠️  ffmpeg [{lang.upper()}] tugadi — qayta ishga tushiramiz")
                    time.sleep(3)
                    current_proc = subprocess.Popen(cmd)
                    t_start = time.time()
                time.sleep(5)

            # Vaqt tugadi — joriy ffmpeg ni yopamiz
            log.info(f"⏰ {lang.upper()} vaqti tugadi — keyingi tilga o'tamiz...")
            try:
                current_proc.terminate()
                for _ in range(10):
                    if current_proc.poll() is not None:
                        break
                    time.sleep(1)
                if current_proc.poll() is None:
                    current_proc.kill()
            except Exception:
                pass

            # Keyingi til
            idx = (idx + 1) % len(rotation)
            time.sleep(3)   # YouTube ulanish tiklash uchun

    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
