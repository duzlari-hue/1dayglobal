"""
daily_shorts.py — "Бугунги 5 та муҳим янгилик" Shorts generator

Jarayon:
  1. Bugungi queue/done JSON fayllaridan top-5 yangilik tanlanadi
  2. Har bir yangilik uchun:
     - 10 soniyalik video klip (asosiy videodan yoki temp klip)
     - PIL overlay: raqam, sarlavha, qisqa tavsif
     - TTS: qisqa naratsiya
  3. Intro karta (2 sek) + 5 × 10 sek = ~52 sek Shorts
  4. ffmpeg concat → final 9:16 video
"""

import os
import sys
import json
import glob
import asyncio
import subprocess
import shutil
import textwrap
from datetime import datetime, date

# Windows UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(".env")

from PIL import Image, ImageDraw, ImageFont
import edge_tts

from config import OUTPUT_DIR, TEMP_DIR, VOICES, QUEUE_DIR

SW, SH = 1080, 1920   # Shorts o'lchami
C_BG    = (5,  10, 22)
C_GOLD  = (240, 165, 0)
C_RED   = (204,   0, 0)
C_WHITE = (255, 255, 255)
C_YELLOW= (255, 210, 0)
C_DARK  = (20,  25, 40)


# ── Shrift ────────────────────────────────────────────────────
def _font(size, bold=True):
    candidates = (
        ["C:\\Windows\\Fonts\\arialbd.ttf",
         "C:\\Windows\\Fonts\\calibrib.ttf"]
        if bold else
        ["C:\\Windows\\Fonts\\arial.ttf",
         "C:\\Windows\\Fonts\\calibri.ttf"]
    )
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ── Bugungi yangiliklar ───────────────────────────────────────
DARAJA_RANK = {"muhim": 0, "tezkor": 1, "xabar": 2}

def load_today_news(lang="uz", count=5):
    """Bugungi queue/done papkasidan top-N yangilik olish."""
    today = date.today().strftime("%Y%m%d")
    patterns = [
        f"{QUEUE_DIR}/done/{today}*.json",
        f"{QUEUE_DIR}/{today}*.json",
    ]
    files = []
    for pat in patterns:
        files += glob.glob(pat)

    news = []
    seen_titles = set()
    for f in sorted(files, reverse=True):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        sarlavha = d.get("sarlavha", {}).get(lang, "")
        if not sarlavha or sarlavha in seen_titles:
            continue
        seen_titles.add(sarlavha)
        daraja = d.get("daraja", "xabar")
        jumla  = d.get("jumla", {}).get(lang, "")
        # jumla yo'q bo'lsa — article description
        if not jumla:
            jumla = d.get("article", {}).get("description", "")[:200]
        news.append({
            "sarlavha": sarlavha,
            "jumla":    jumla[:180],
            "daraja":   daraja,
            "rank":     DARAJA_RANK.get(daraja, 2),
            "file":     f,
            "ts":       os.path.basename(f)[:15],
        })

    # Muhimroq yangiliklar birinchi
    news.sort(key=lambda x: (x["rank"], -os.path.getmtime(x["file"])))
    return news[:count]


# ── Video klip topish ─────────────────────────────────────────
def find_video_for_news(ts, lang):
    """Queue fayl timestamp bo'yicha tayyor video topish."""
    # ts = "20260417_093701"
    date_part = ts[:8]
    pattern = f"{OUTPUT_DIR}/{date_part}*_{lang}_*.mp4"
    # shorts va test fayllarni o'tkazib yuborish
    candidates = [
        f for f in glob.glob(pattern)
        if "_shorts" not in f and "_test" not in f
    ]
    if candidates:
        # Eng yaqin vaqt bo'yicha tanlash
        return sorted(candidates)[0]
    return None


# ── 10 soniyalik klip kesish ──────────────────────────────────
def extract_clip(video_path, out_path, start=5, duration=10):
    """Videodan start sekunddan duration soniya kesish."""
    r = subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-vf", f"crop=ih*9/16:ih,scale={SW}:{SH}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        "-an",   # Audio yo'q — keyinroq qo'shiladi
        out_path
    ], capture_output=True, timeout=60)
    return r.returncode == 0 and os.path.exists(out_path)


# ── Rang: daraja bo'yicha ─────────────────────────────────────
DARAJA_COLOR = {
    "muhim":  (220, 30, 30),
    "tezkor": (240, 140, 0),
    "xabar":  (0, 100, 200),
}


# ── Karta overlay (PIL) ───────────────────────────────────────
def make_news_card(number, sarlavha, jumla, daraja, lang, out_path):
    """
    9:16 overlay rasmi:
    - Yuqori: raqam badge (1,2,3,4,5)
    - O'rta: sarlavha (katta)
    - Pastroq: qisqa tavsif
    - Pastki: 1KUN brend
    """
    img  = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Pastki gradient (matn zonasi) ───────────────────────
    grad_top = SH - 650
    for y in range(grad_top, SH):
        alpha = int(220 * (y - grad_top) / (SH - grad_top))
        draw.line([(0, y), (SW, y)], fill=(5, 10, 22, alpha))

    # ── Yuqori soya ─────────────────────────────────────────
    for y in range(0, 200):
        alpha = int(180 * (1 - y / 200))
        draw.line([(0, y), (SW, y)], fill=(0, 0, 0, alpha))

    # ── Chap aksent chiziq ───────────────────────────────────
    accent = DARAJA_COLOR.get(daraja, C_RED)
    draw.rectangle([(0, 0), (8, SH)], fill=(*accent, 220))

    # ── Pastki oltin chiziq ──────────────────────────────────
    draw.rectangle([(0, SH - 8), (SW, SH)], fill=(*C_GOLD, 255))

    # ── Raqam badge (yuqori o'rta) ───────────────────────────
    bx, by = SW // 2, 110
    draw.ellipse([(bx-65, by-65), (bx+65, by+65)],
                 fill=(*accent, 230), outline=(*C_GOLD, 255), width=3)
    draw.text((bx, by), str(number), font=_font(72), fill=C_WHITE, anchor="mm")

    # Daraja belgisi
    daraja_label = {"muhim": "MUHIM", "tezkor": "TEZKOR", "xabar": "YANGILIK"}
    draw.text((bx, by + 88), daraja_label.get(daraja, "YANGILIK"),
              font=_font(28, bold=False), fill=(*C_GOLD, 200), anchor="mm")

    # ── Sarlavha ─────────────────────────────────────────────
    # Matni qatorlarga bo'lish (max 22 belgi qator)
    wrapped = textwrap.wrap(sarlavha, width=22)[:4]  # max 4 qator
    f_title = _font(58)
    ty = SH - 490
    for line in wrapped:
        # Ko'lanka
        draw.text((SW//2 + 2, ty + 2), line,
                  font=f_title, fill=(0, 0, 0, 160), anchor="mt")
        draw.text((SW//2, ty), line,
                  font=f_title, fill=C_WHITE, anchor="mt")
        ty += 68

    # ── Qisqa tavsif ─────────────────────────────────────────
    if jumla:
        jumla_short = jumla[:120]
        wrapped_j = textwrap.wrap(jumla_short, width=30)[:3]
        f_jumla = _font(34, bold=False)
        jy = ty + 15
        for line in wrapped_j:
            draw.text((SW//2, jy), line,
                      font=f_jumla, fill=(200, 210, 230, 220), anchor="mt")
            jy += 44

    # ── Brend (pastki o'rta) ──────────────────────────────────
    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    draw.text((SW//2, SH - 35), brand,
              font=_font(32), fill=(*C_GOLD, 220), anchor="mb")

    img.save(out_path, "PNG")
    return out_path


# ── Intro karta ───────────────────────────────────────────────
def make_intro_card(lang, out_path):
    """2 soniyalik intro: "Бугунги 5 та муҳим янгилик"."""
    img  = Image.new("RGB", (SW, SH), C_BG)
    draw = ImageDraw.Draw(img)

    # Fon gradient
    for y in range(SH):
        t = y / SH
        r = int(C_BG[0] * (1-t) + C_DARK[0] * t)
        g = int(C_BG[1] * (1-t) + C_DARK[1] * t)
        b = int(C_BG[2] * (1-t) + C_DARK[2] * t)
        draw.line([(0, y), (SW, y)], fill=(r, g, b))

    # Aksent chiziqlari
    draw.rectangle([(0,    0), (8,  SH)],     fill=C_RED)
    draw.rectangle([(SW-8, 0), (SW, SH)],     fill=C_GOLD)
    draw.rectangle([(0, SH-8), (SW, SH)],     fill=C_GOLD)
    draw.rectangle([(0,    0), (SW, 8)],      fill=C_GOLD)

    # Brend
    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    draw.text((SW//2, 180), brand, font=_font(64), fill=C_GOLD, anchor="mm")

    # Sarlavha
    titles = {
        "uz": ["БУГУНГИ", "5 ТА МУҲИМ", "ЯНГИЛИК"],
        "ru": ["СЕГОДНЯ", "5 ГЛАВНЫХ", "НОВОСТЕЙ"],
        "en": ["TODAY'S", "TOP 5", "NEWS"],
    }
    lines = titles.get(lang, titles["uz"])
    colors = [C_WHITE, C_YELLOW, C_WHITE]
    sizes  = [72, 110, 72]
    ys     = [SH//2 - 100, SH//2 + 20, SH//2 + 145]
    for line, color, size, y in zip(lines, colors, sizes, ys):
        draw.text((SW//2, y), line, font=_font(size), fill=color, anchor="mm")

    # Pastki iz
    today_str = date.today().strftime("%d.%m.%Y")
    draw.text((SW//2, SH - 80), today_str,
              font=_font(36, bold=False), fill=(150, 160, 180), anchor="mm")

    img.save(out_path, "JPEG", quality=92)
    return out_path


# ── TTS ───────────────────────────────────────────────────────
async def _tts_async(text, voice, rate, out_path):
    comm = edge_tts.Communicate(text, voice, rate=rate)
    await comm.save(out_path)

def make_tts(text, out_path, lang="uz"):
    vcfg = VOICES.get(lang, VOICES["uz"])["default"]
    voice = vcfg["voice"]
    rate  = vcfg.get("rate", "-5%")
    asyncio.run(_tts_async(text, voice, rate, out_path))
    return os.path.exists(out_path)


# ── TTS matni yaratish ────────────────────────────────────────
def build_tts_text(number, sarlavha, jumla, lang):
    ordinals = {
        "uz": ["Birinchi", "Ikkinchi", "Uchinchi", "To'rtinchi", "Beshinchi"],
        "ru": ["Первая",   "Вторая",   "Третья",   "Четвёртая", "Пятая"],
        "en": ["First",    "Second",   "Third",    "Fourth",    "Fifth"],
    }
    ord_word = ordinals.get(lang, ordinals["uz"])[number - 1]

    if lang == "uz":
        text = f"{ord_word} yangilik. {sarlavha}."
        if jumla:
            text += f" {jumla[:150]}"
    elif lang == "ru":
        text = f"{ord_word} новость. {sarlavha}."
        if jumla:
            text += f" {jumla[:150]}"
    else:
        text = f"{ord_word} news. {sarlavha}."
        if jumla:
            text += f" {jumla[:150]}"
    return text


def build_intro_text(lang):
    texts = {
        "uz": "Bugun 5 ta eng muhim yangilik.",
        "ru": "Сегодня пять главных новостей.",
        "en": "Today's top five news stories.",
    }
    return texts.get(lang, texts["uz"])


# ── Jimlik audio ─────────────────────────────────────────────
def make_silence(duration, out_path):
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
        "-t", str(duration),
        "-c:a", "aac", "-b:a", "64k",
        out_path
    ], capture_output=True)


# ── Rasm → video ─────────────────────────────────────────────
def image_to_video(img_path, audio_path, duration, out_path):
    r = subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", img_path,
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage",
        "-preset", "ultrafast", "-crf", "26",
        "-c:a", "aac", "-b:a", "96k",
        "-t", str(duration),
        "-vf", f"scale={SW}:{SH}:force_original_aspect_ratio=decrease,"
               f"pad={SW}:{SH}:(ow-iw)/2:(oh-ih)/2:color=black",
        "-pix_fmt", "yuv420p",
        "-shortest",
        out_path
    ], capture_output=True, timeout=60)
    return r.returncode == 0


# ── Video + overlay birlashtirish ────────────────────────────
def overlay_video(video_path, overlay_path, audio_path, out_path):
    """Video ustiga PIL overlay + audio."""
    r = subprocess.run([
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", overlay_path,
        "-i", audio_path,
        "-filter_complex",
        "[0:v][1:v]overlay=0:0[v]",
        "-map", "[v]",
        "-map", "2:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        "-c:a", "aac", "-b:a", "96k",
        "-shortest",
        out_path
    ], capture_output=True, timeout=120)
    return r.returncode == 0 and os.path.exists(out_path)


# ── Asosiy funksiya ───────────────────────────────────────────
def make_daily_shorts(lang="uz"):
    print(f"\n📰 Daily Shorts ({lang.upper()}) yaratilmoqda...")
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. Yangiliklar yuklanadi
    news = load_today_news(lang=lang, count=5)
    if not news:
        print("  ⚠️  Bugungi yangilik topilmadi")
        return None

    print(f"  ✓ {len(news)} ta yangilik topildi")
    for i, n in enumerate(news, 1):
        print(f"    {i}. [{n['daraja']:7}] {n['sarlavha'][:55]}")

    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = []   # concat uchun segment yo'llari

    # 2. Intro segmenti
    print("  → Intro...")
    intro_img = os.path.join(TEMP_DIR, f"ds_intro_{ts}.jpg")
    intro_aud = os.path.join(TEMP_DIR, f"ds_intro_{ts}.aac")
    intro_vid = os.path.join(TEMP_DIR, f"ds_intro_{ts}.mp4")
    make_intro_card(lang, intro_img)
    intro_text = build_intro_text(lang)
    if make_tts(intro_text, intro_aud, lang):
        if image_to_video(intro_img, intro_aud, 3, intro_vid):
            parts.append(intro_vid)
            print(f"     Intro ✓")

    # 3. Har bir yangilik segmenti
    for i, item in enumerate(news, 1):
        print(f"  → Yangilik {i}: {item['sarlavha'][:45]}...")

        sarlavha = item["sarlavha"]
        jumla    = item["jumla"]
        daraja   = item["daraja"]

        seg_ovl  = os.path.join(TEMP_DIR, f"ds_ovl_{ts}_{i}.png")
        seg_aud  = os.path.join(TEMP_DIR, f"ds_aud_{ts}_{i}.aac")
        seg_sil  = os.path.join(TEMP_DIR, f"ds_sil_{ts}_{i}.aac")
        seg_vid  = os.path.join(TEMP_DIR, f"ds_seg_{ts}_{i}.mp4")
        seg_clip = os.path.join(TEMP_DIR, f"ds_clip_{ts}_{i}.mp4")

        # Overlay yaratish
        make_news_card(i, sarlavha, jumla, daraja, lang, seg_ovl)

        # TTS naratsiya
        tts_text = build_tts_text(i, sarlavha, jumla, lang)
        tts_ok = make_tts(tts_text, seg_aud, lang)
        if not tts_ok:
            make_silence(9, seg_sil)
            seg_aud = seg_sil

        # Video klip topish
        video_src = find_video_for_news(item["ts"], lang)
        if video_src and os.path.exists(video_src):
            print(f"     🎬 Video: {os.path.basename(video_src)}")
            # Videoning o'rta qismidan 12 sek kesib, overlay qo'shish
            clip_ok = extract_clip(video_src, seg_clip, start=15, duration=12)
            if clip_ok:
                ok = overlay_video(seg_clip, seg_ovl, seg_aud, seg_vid)
                if ok:
                    parts.append(seg_vid)
                    print(f"     ✓ Segment {i} (video+overlay)")
                    continue

        # Video yo'q — karta slayd sifatida
        print(f"     ℹ️  Video topilmadi — slayd rejimi")
        if image_to_video(seg_ovl, seg_aud, 11, seg_vid):
            parts.append(seg_vid)
            print(f"     ✓ Segment {i} (slayd)")

    if not parts:
        print("  ⚠️  Hech bir segment yaratilmadi")
        return None

    # 4. Barcha segmentlarni birlashtirish
    print(f"  → {len(parts)} ta segment concat qilinmoqda...")
    concat_txt = os.path.join(TEMP_DIR, f"ds_concat_{ts}.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")

    out_name = f"{ts}_daily_shorts_{lang}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    r = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_txt,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path
    ], capture_output=True, timeout=300)

    # Temp fayllarni tozalash
    for p in parts + [concat_txt]:
        try:
            if os.path.exists(p): os.remove(p)
        except Exception:
            pass
    for ext in ["jpg", "png", "aac", "mp4"]:
        for f in glob.glob(os.path.join(TEMP_DIR, f"ds_*_{ts}*.{ext}")):
            try: os.remove(f)
            except: pass

    if r.returncode != 0:
        print(f"  ⚠️  Concat xato: {r.stderr.decode('utf-8', errors='replace')[-300:]}")
        return None

    if os.path.exists(out_path):
        sz = os.path.getsize(out_path) / 1_048_576
        print(f"\n  ✅ Daily Shorts: {out_name} ({sz:.1f} MB)")
        return out_path

    return None


# ── CLI ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Daily Top-5 Shorts generator")
    parser.add_argument("--lang", default="uz", choices=["uz", "ru", "en"],
                        help="Til (default: uz)")
    parser.add_argument("--all",  action="store_true",
                        help="Barcha 3 tilda yaratish")
    args = parser.parse_args()

    if args.all:
        for lg in ["uz", "ru", "en"]:
            make_daily_shorts(lg)
    else:
        result = make_daily_shorts(args.lang)
        if result:
            print(f"\nFayl: {result}")
        else:
            print("\nShorts yaratilmadi.")
