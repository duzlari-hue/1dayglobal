"""check_videos.py — output/videos/ ichidagi audio bilan muammoli mp4'larni topish.

Foydalanish:
    py check_videos.py            # faqat skanerlash, ko'rsatish
    py check_videos.py --move     # buzuq fayllarni output/videos/_broken/ ga ko'chirish
"""
import os
import sys
import glob
import shutil
import argparse
import subprocess
import pathlib

ROOT = pathlib.Path(__file__).parent
VIDEOS_DIR = ROOT / "output" / "videos"
BROKEN_DIR = VIDEOS_DIR / "_broken"


def check_audio(path: str) -> tuple:
    """ffmpeg orqali audio'ni tekshirish — xato bo'lsa qaytaradi."""
    try:
        # -t 5 — faqat birinchi 5s o'qish, tezroq
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-t", "5",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=20,
        )
        err = r.stderr or ""
        # AAC va decoder xatolari muammoli
        critical_errors = [
            "exceeds limit",
            "invalid band type",
            "Invalid data found",
            "Error submitting packet",
            "non-existing PPS",
            "decode_slice_header error",
        ]
        for e in critical_errors:
            if e in err:
                return False, e
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--move", action="store_true",
                        help="Buzuq fayllarni _broken/ ga ko'chirish")
    args = parser.parse_args()

    files = sorted(glob.glob(str(VIDEOS_DIR / "*.mp4")))
    if not files:
        print("output/videos/ bo'sh!")
        return

    print(f"📊 {len(files)} ta video skanerlanmoqda...\n")
    bad = []
    for i, f in enumerate(files, 1):
        ok, err = check_audio(f)
        name = pathlib.Path(f).name
        if ok:
            print(f"  [{i:3d}/{len(files)}] ✅ {name}")
        else:
            print(f"  [{i:3d}/{len(files)}] ❌ {name}  — {err[:50]}")
            bad.append(f)

    print(f"\n══════════ XULOSA ══════════")
    print(f"  Sog'lom:  {len(files) - len(bad)} ta")
    print(f"  Buzuq:    {len(bad)} ta")

    if bad and args.move:
        BROKEN_DIR.mkdir(parents=True, exist_ok=True)
        moved = 0
        for f in bad:
            try:
                dst = BROKEN_DIR / pathlib.Path(f).name
                shutil.move(f, dst)
                moved += 1
            except Exception as e:
                print(f"  Ko'chirish xato: {e}")
        print(f"  📁 {moved} ta fayl _broken/ ga ko'chirildi")
    elif bad:
        print(f"\n  Ko'chirish uchun: py check_videos.py --move")


if __name__ == "__main__":
    main()
