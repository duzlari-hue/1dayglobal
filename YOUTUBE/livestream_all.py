"""livestream_all.py — 3 ta tilda parallel YouTube Live (UZ, RU, EN).

3 ta livestream.py jarayonini parallel ishga tushiradi.
Har biri o'z RTMP stream key'iga jo'natadi.

.env da bo'lishi shart:
    YT_LIVE_STREAM_KEY_UZ=xxxx-xxxx-xxxx-xxxx
    YT_LIVE_STREAM_KEY_RU=xxxx-xxxx-xxxx-xxxx
    YT_LIVE_STREAM_KEY_EN=xxxx-xxxx-xxxx-xxxx

Foydalanish:
    py livestream_all.py             # Hammasi (UZ + RU + EN)
    py livestream_all.py --langs uz  # Faqat UZ
    py livestream_all.py --langs uz,ru  # UZ + RU

Resurslar (1 ta stream):
    CPU:     ~30% bitta core (1080p @ 4500kbps libx264 veryfast)
    RAM:     ~400 MB
    Tarmoq:  ~5 Mbps yuklab berish
3 ta stream uchun: ~90% CPU, ~1.2 GB RAM, ~15 Mbps upload.

Ctrl+C — barchasini to'xtatadi.
"""
import os
import sys
import time
import signal
import argparse
import subprocess
import pathlib

from dotenv import load_dotenv
load_dotenv()

ROOT = pathlib.Path(__file__).parent
PYTHON_EXE = sys.executable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--langs", default="uz,ru,en",
                        help="Vergul bilan ajratilgan tillar (uz,ru,en)")
    parser.add_argument("--test", action="store_true",
                        help="60s lokal MP4 — RTMP emas")
    args = parser.parse_args()

    langs = [l.strip() for l in args.langs.split(",") if l.strip()]

    # Stream key tekshirish
    if not args.test:
        missing = []
        for lang in langs:
            key = os.getenv(f"YT_LIVE_STREAM_KEY_{lang.upper()}", "").strip()
            if not key:
                missing.append(f"YT_LIVE_STREAM_KEY_{lang.upper()}")
        if missing:
            print("\n❌ .env da quyidagi stream keylar yo'q:")
            for m in missing:
                print(f"   - {m}")
            print("\nYouTube Studio → Yaratish → Jonli efir → Stream key")
            sys.exit(1)

    # Har bir til uchun jarayon ishga tushirish
    procs = []
    print(f"\n🎥 {len(langs)} ta til uchun parallel live stream:")
    for lang in langs:
        cmd = [PYTHON_EXE, str(ROOT / "livestream.py"), "--lang", lang]
        if args.test:
            cmd.append("--test")
        print(f"   ▶  {lang.upper()}: {' '.join(cmd)}")
        # Yangi konsol oynasi (Windows) — har til alohida log ko'rinadi
        if os.name == "nt":
            p = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            p = subprocess.Popen(cmd)
        procs.append((lang, p))
        time.sleep(2)   # Har ishga tushirish orasida 2s pauza

    print(f"\n✅ {len(procs)} ta stream ishga tushdi.")
    print("   Ctrl+C bilan barchasini to'xtatish")

    try:
        # Hammasini kutish
        while True:
            time.sleep(5)
            # Qaysidir tugagan bo'lsa — log
            for lang, p in procs:
                if p.poll() is not None:
                    print(f"⚠️  [{lang.upper()}] tugadi (exit code: {p.returncode})")
            # Barchasi tugagan bo'lsa — chiqish
            if all(p.poll() is not None for _, p in procs):
                print("Barcha streamlar to'xtadi.")
                break
    except KeyboardInterrupt:
        print("\n\n⏹️  To'xtatilmoqda...")
        for lang, p in procs:
            try:
                if p.poll() is None:
                    if os.name == "nt":
                        p.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        p.terminate()
            except Exception:
                pass
        time.sleep(3)
        for lang, p in procs:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass
        print("Barcha streamlar to'xtatildi.")


if __name__ == "__main__":
    main()
