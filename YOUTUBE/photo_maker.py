"""
photo_maker.py — YouTube monetizatsiyaga 100% mos yangilik video generatori

ORIGINAL KONTENT (YouTube monetizatsiya talablariga javob beradi):
  ✓ Pexels CC0 royalty-free rasmlar  — Ken Burns animatsiya
  ✓ Maqola og:image                  — yangilik maqsadida fair use
  ✓ AI ovoz (edge-tts)               — original diktor
  ✓ PIL original grafika             — kartalar, overlay, matn
  ✓ ffmpeg/avlSrc fon musiqasi       — original musiqa
  ✗ YouTube kliplar — YO'Q
  ✗ Dailymotion/Vimeo kliplar — YO'Q
  ✗ Boshqa kanallar kontenti — YO'Q

Video tuzilishi (~2 daqiqa):
  [Intro karta 5s] → [Rasm 1 Ken Burns 18s] → ... → [Rasm N] → [Outro 5s]
  Ustida: AI ovoz + fon musiqasi
"""

import os, sys, re, json, glob, hashlib, shutil, textwrap
import subprocess, requests, asyncio, random
from datetime import date, datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(".env")

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import edge_tts

from config import OUTPUT_DIR, TEMP_DIR, VOICES, AUDIO_FX, YOUTUBE_PLAYLIST

# ─────────────────────────────────────────────────────────────
# Konstantlar
# ─────────────────────────────────────────────────────────────
VW, VH      = 1280, 720          # 16:9 gorizontal video
FPS         = 25
PHOTO_DUR   = 18                 # Har bir rasm (soniya)
INTRO_DUR   = 5
OUTRO_DUR   = 5
TRANS_DUR   = 0.7                # Crossfade davomiyligi
MAX_PHOTOS  = 6                  # Maksimal rasm soni
WORDS_LIMIT = 300                # Skriptdan necha so'z ishlatiladi
MUSIC_VOL   = 0.10               # Fon musiqasi balandligi

_HERE = os.path.dirname(os.path.abspath(__file__))

C_BG    = (5,  10, 22)
C_GOLD  = (240, 165, 0)
C_RED   = (204,   0, 0)
C_WHITE = (255, 255, 255)
C_DARK  = (20,  25, 40)
C_BLUE  = (0,   80, 180)

# ─────────────────────────────────────────────────────────────
# Ken Burns effektlari (zoompan variantlari)
# ─────────────────────────────────────────────────────────────
def _kb_filter(frames, effect=0):
    """Ken Burns zoompan filtri matni."""
    d = int(frames)
    effects = [
        # 0: Markazdan zoom in
        f"zoompan=z='min(zoom+0.0008,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}",
        # 1: Markazdan zoom out
        f"zoompan=z='if(eq(on,1),1.5,max(zoom-0.0008,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}",
        # 2: Chapdan o'ngga pan + zoom
        f"zoompan=z='min(zoom+0.0005,1.3)':x='min(x+0.6,iw*0.25)':y='ih/2-(ih/zoom/2)':d={d}",
        # 3: O'ngdan chapga pan + zoom
        f"zoompan=z='min(zoom+0.0005,1.3)':x='max(x-0.6,0)':y='ih/2-(ih/zoom/2)':d={d}",
        # 4: Yuqori chapdan zoom in
        f"zoompan=z='min(zoom+0.0008,1.4)':x='0':y='0':d={d}",
        # 5: Pastki o'ngdan zoom in
        f"zoompan=z='min(zoom+0.0008,1.4)':x='iw-iw/zoom':y='ih-ih/zoom':d={d}",
    ]
    return effects[effect % len(effects)]


# ─────────────────────────────────────────────────────────────
# Shrift
# ─────────────────────────────────────────────────────────────
def _font(size, bold=True):
    cands = (["C:\\Windows\\Fonts\\arialbd.ttf",
               "C:\\Windows\\Fonts\\calibrib.ttf"] if bold else
             ["C:\\Windows\\Fonts\\arial.ttf",
              "C:\\Windows\\Fonts\\calibri.ttf"])
    for p in cands:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except: pass
    return ImageFont.load_default()


# ─────────────────────────────────────────────────────────────
# Rasm yuklash: og:image
# ─────────────────────────────────────────────────────────────
def _fetch_og_image(article_url, out_path):
    """Maqolaning og:image metatesidan rasm yuklash."""
    if not article_url or not article_url.startswith("http"):
        return False
    try:
        hdrs = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }
        resp = requests.get(article_url, headers=hdrs, timeout=12, allow_redirects=True)
        if resp.status_code != 200:
            return False
        html = resp.text
        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ]
        img_url = None
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                img_url = m.group(1).strip()
                if img_url.startswith("http"):
                    break
        if not img_url or not img_url.startswith("http"):
            return False
        ir = requests.get(img_url, headers=hdrs, timeout=15)
        if ir.status_code != 200 or len(ir.content) < 10_000:
            return False
        with open(out_path, "wb") as fh:
            fh.write(ir.content)
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
# Rasm yuklash: Pexels (bir nechta rasm)
# ─────────────────────────────────────────────────────────────
def _fetch_pexels_many(queries, out_dir, prefix, count=6):
    """Pexels dan bir nechta turli rasm yuklash."""
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        return []
    headers   = {"Authorization": api_key}
    seen_ids  = set()
    results   = []

    for q in queries:
        if len(results) >= count:
            break
        q = (q or "").strip()
        if not q:
            continue
        for page in (1, 2):
            if len(results) >= count:
                break
            try:
                url = (
                    f"https://api.pexels.com/v1/search"
                    f"?query={requests.utils.quote(q)}"
                    f"&per_page=15&page={page}&orientation=landscape"
                )
                resp = requests.get(url, headers=headers, timeout=12)
                if resp.status_code != 200:
                    continue
                photos = resp.json().get("photos", [])
                random.shuffle(photos)   # Xilma-xillik uchun aralashtiramiz
                for ph in photos:
                    if len(results) >= count:
                        break
                    ph_id = ph.get("id")
                    if ph_id in seen_ids:
                        continue
                    seen_ids.add(ph_id)
                    src     = ph.get("src", {})
                    img_url = src.get("large2x") or src.get("large") or src.get("medium", "")
                    if not img_url:
                        continue
                    out_path = os.path.join(out_dir, f"{prefix}_{len(results):02d}.jpg")
                    ir = requests.get(img_url, timeout=20)
                    if ir.status_code != 200 or len(ir.content) < 20_000:
                        continue
                    with open(out_path, "wb") as f:
                        f.write(ir.content)
                    results.append(out_path)
                    print(f"     📸 Pexels [{len(results)}/{count}]: {q[:40]}")
            except Exception as e:
                print(f"     Pexels xato: {e}")
    return results


# ─────────────────────────────────────────────────────────────
# Barcha rasmlarni yig'ish
# ─────────────────────────────────────────────────────────────
def _gather_photos(article_url, keywords, sarlavha, lang, ts, count=MAX_PHOTOS,
                   en_title=""):
    """og:image + Pexels dan bir nechta rasm to'plash."""
    photo_dir = TEMP_DIR
    photos    = []

    # 1. Maqolaning o'z rasmi (eng aniq)
    og_path = os.path.join(photo_dir, f"pm_og_{ts}.jpg")
    if _fetch_og_image(article_url, og_path):
        photos.append(og_path)
        print(f"     📰 Maqola rasmi olindi")

    # 2. Pexels qidiruv so'rovlari — FAQAT inglizcha (Pexels Kirilni tushunmaydi)
    kw_str  = " ".join(keywords[:4]) if keywords else ""
    queries = []
    # EN sarlavha (original) eng aniq qidiruv
    if en_title and all(c.isascii() or not c.isalpha() for c in en_title):
        queries.append(en_title[:60])
    elif lang == "en" and sarlavha and all(c.isascii() or not c.isalpha() for c in sarlavha):
        queries.append(sarlavha[:60])
    # Kalit so'zlar (doim inglizcha)
    if kw_str:
        queries.append(kw_str)
    for kw in keywords[:4]:
        queries.append(f"{kw} news")
    queries = list(dict.fromkeys(q for q in queries if q.strip()))

    pexels_photos = _fetch_pexels_many(
        queries, photo_dir, f"pm_px_{ts}", count=count - len(photos)
    )
    photos += pexels_photos

    # Yetarli rasm yo'q → qora fon bilan ishlaymiz
    print(f"     Jami rasm: {len(photos)}")
    return photos[:count]


# ─────────────────────────────────────────────────────────────
# PIL: Foto ustiga matn overlay qo'shish
# ─────────────────────────────────────────────────────────────
def _add_text_overlay(photo_path, caption, location, daraja, out_path):
    """
    Rasm pastiga (lower-third) yangilik sarlavhasi va joy yoziladi.
    Gradient fon ustida oq matn.
    """
    accent = {"muhim": C_RED, "tezkor": (240, 140, 0), "xabar": C_BLUE}.get(daraja, C_BLUE)

    try:
        img = Image.open(photo_path).convert("RGB")
        # 16:9 ga crop/resize
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
    except Exception:
        img = Image.new("RGB", (VW, VH), C_BG)

    canvas = img.copy()

    # Pastki gradient (lower third)
    grad_h = 200
    for y in range(VH - grad_h, VH):
        alpha = int(0.88 * (y - (VH - grad_h)) / grad_h)
        ov = Image.new("RGBA", (VW, 1), (5, 10, 22, int(255 * 0.88 * (y - (VH - grad_h)) / grad_h)))
        canvas.paste(ov, (0, y), ov)

    draw = ImageDraw.Draw(canvas)

    # Aksent chiziq (chap)
    draw.rectangle([(0, 0), (6, VH)], fill=(*accent, 230))

    # Location (joy nomi)
    if location:
        draw.text((18, VH - 85), f"📍 {location}",
                  font=_font(22, False), fill=(*C_GOLD, 220))

    # Caption (qisqa sarlavha)
    if caption:
        wrapped = textwrap.wrap(caption, width=55)[:2]
        ty = VH - 60 if location else VH - 70
        for line in wrapped:
            draw.text((18, ty), line, font=_font(28), fill=C_WHITE)
            ty += 34

    # Brend
    draw.text((VW - 10, VH - 10), "1KUN GLOBAL",
              font=_font(18, False), fill=(*C_GOLD, 180), anchor="rb")

    canvas.save(out_path, "JPEG", quality=92)
    return out_path


# ─────────────────────────────────────────────────────────────
# PIL: Intro karta
# ─────────────────────────────────────────────────────────────
def _make_intro_card(sarlavha, lang, daraja, location, out_path):
    """Professional yangilik intro kartasi (1280x720)."""
    img  = Image.new("RGB", (VW, VH), C_BG)
    draw = ImageDraw.Draw(img)

    # Gradient fon
    for y in range(VH):
        t = y / VH
        r = int(C_BG[0] * (1 - t) + C_DARK[0] * t)
        g = int(C_BG[1] * (1 - t) + C_DARK[1] * t)
        b = int(C_BG[2] * (1 - t) + C_DARK[2] * t)
        draw.line([(0, y), (VW, y)], fill=(r, g, b))

    # Chiziqlar
    draw.rectangle([(0, 0), (6, VH)],      fill=C_RED)
    draw.rectangle([(VW - 6, 0), (VW, VH)],fill=C_GOLD)
    draw.rectangle([(0, VH - 6), (VW, VH)],fill=C_GOLD)
    draw.rectangle([(0, 0), (VW, 6)],       fill=C_GOLD)

    # Breaking News banner
    accent = {"muhim": C_RED, "tezkor": (240, 140, 0), "xabar": C_BLUE}.get(daraja, C_BLUE)
    banner_labels = {"muhim": "⚡ MUHIM YANGILIK", "tezkor": "🔴 TEZKOR", "xabar": "📰 YANGILIK"}
    banner_text   = banner_labels.get(daraja, "📰 YANGILIK")
    bx, by, bw2, bh2 = 30, 30, 280, 44
    draw.rectangle([(bx, by), (bx + bw2, by + bh2)], fill=(*accent, 240))
    draw.text((bx + bw2 // 2, by + bh2 // 2), banner_text,
              font=_font(20), fill=C_WHITE, anchor="mm")

    # Kanal brendi
    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    draw.text((VW // 2, 95), brand, font=_font(52), fill=C_GOLD, anchor="mm")

    # Sarlavha
    wrapped = textwrap.wrap(sarlavha or "", width=38)[:4]
    ty = VH // 2 - 60
    for line in wrapped:
        draw.text((VW // 2 + 2, ty + 2), line, font=_font(38), fill=(0, 0, 0, 160), anchor="mt")
        draw.text((VW // 2, ty),          line, font=_font(38), fill=C_WHITE,         anchor="mt")
        ty += 50

    # Joy va sana
    if location:
        draw.text((VW // 2, VH - 80), f"📍 {location}",
                  font=_font(24, False), fill=(*C_GOLD, 200), anchor="mm")
    draw.text((VW // 2, VH - 45), date.today().strftime("%d.%m.%Y"),
              font=_font(26, False), fill=(150, 160, 180), anchor="mm")

    img.save(out_path, "JPEG", quality=92)
    return out_path


# ─────────────────────────────────────────────────────────────
# PIL: Outro karta
# ─────────────────────────────────────────────────────────────
def _make_outro_card(lang, out_path):
    """Obuna chaqiruvi bilan outro karta."""
    img  = Image.new("RGB", (VW, VH), C_BG)
    draw = ImageDraw.Draw(img)

    for y in range(VH):
        t = y / VH
        r = int(C_BG[0] * (1 - t) + C_DARK[0] * t)
        g = int(C_BG[1] * (1 - t) + C_DARK[1] * t)
        b = int(C_BG[2] * (1 - t) + C_DARK[2] * t)
        draw.line([(0, y), (VW, y)], fill=(r, g, b))

    draw.rectangle([(0, 0), (6, VH)],       fill=C_RED)
    draw.rectangle([(VW - 6, 0), (VW, VH)], fill=C_GOLD)
    draw.rectangle([(0, VH - 6), (VW, VH)], fill=C_GOLD)
    draw.rectangle([(0, 0), (VW, 6)],        fill=C_GOLD)

    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    draw.text((VW // 2, VH // 2 - 70), brand, font=_font(64), fill=C_GOLD, anchor="mm")

    cta = {
        "uz": ["Obuna bo'ling!", "Kanalga subscribe bo'ling va jonli yangiliklar oling"],
        "ru": ["Подписывайтесь!", "Подпишитесь на канал и следите за новостями"],
        "en": ["Subscribe now!", "Subscribe to the channel for live breaking news"],
    }.get(lang, ["Subscribe!", "Follow us for more news"])
    draw.text((VW // 2, VH // 2 + 20), cta[0], font=_font(40), fill=C_WHITE, anchor="mm")
    draw.text((VW // 2, VH // 2 + 75), cta[1], font=_font(22, False),
              fill=(160, 170, 190), anchor="mm")

    img.save(out_path, "JPEG", quality=92)
    return out_path


# ─────────────────────────────────────────────────────────────
# Rasm → Ken Burns video
# ─────────────────────────────────────────────────────────────
def _photo_to_video(photo_path, duration, effect_idx, out_path):
    """
    Foto rasm → Ken Burns animatsiyali video (silent).
    Rasm avval 2x o'lchamga kengaytiriladi, keyin zoompan.
    """
    frames = int(duration * FPS)
    kb     = _kb_filter(frames, effect_idx)
    vf     = (
        f"scale=iw*2:ih*2:force_original_aspect_ratio=increase,"
        f"crop=iw:ih,"
        f"{kb}:s={VW}x{VH}:fps={FPS},"
        f"setsar=1"
    )
    r = subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", photo_path,
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-an",
        out_path,
    ], capture_output=True, timeout=120)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace")[-300:]
        print(f"     Ken Burns xato: {err}")
        # Fallback: oddiy still video
        return _still_to_video(photo_path, duration, out_path)
    return r.returncode == 0 and os.path.exists(out_path)


def _still_to_video(img_path, duration, out_path):
    """Fallback: hech qanday harakat yo'q, oddiy still video."""
    r = subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", img_path,
        "-vf", f"scale={VW}:{VH}:force_original_aspect_ratio=decrease,"
               f"pad={VW}:{VH}:(ow-iw)/2:(oh-ih)/2:color=black,fps={FPS}",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-an",
        out_path,
    ], capture_output=True, timeout=60)
    return r.returncode == 0 and os.path.exists(out_path)


def _card_to_video(img_path, duration, out_path):
    """PIL karta rasmi → still video (harakat yo'q)."""
    return _still_to_video(img_path, duration, out_path)


# ─────────────────────────────────────────────────────────────
# TTS (AI ovoz)
# ─────────────────────────────────────────────────────────────
async def _tts_async(text, voice, rate, out_path):
    comm = edge_tts.Communicate(text, voice, rate=rate)
    await comm.save(out_path)

def _make_tts(script, lang, out_path):
    """Skriptni TTS ga aylantirish."""
    vcfg = VOICES.get(lang, VOICES["uz"])["default"]
    # UZ uchun kirill → lotin
    text = script
    if lang == "uz":
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
        text = ''.join(_CYR2LAT.get(c, c) for c in text)
    try:
        asyncio.run(_tts_async(text, vcfg["voice"], vcfg.get("rate", "-5%"), out_path))
        return os.path.exists(out_path)
    except Exception as e:
        print(f"  TTS xato: {e}")
        return False


def _audio_duration(path):
    """ffprobe orqali audio davomiyligini aniqlash."""
    try:
        r = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", path,
        ], capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────
# Silence (audio dummy)
# ─────────────────────────────────────────────────────────────
def _make_silence(duration, out_path):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=mono",
        "-t", str(duration), "-c:a", "aac", "-b:a", "64k", out_path,
    ], capture_output=True)


# ─────────────────────────────────────────────────────────────
# Segmentlarni birlashtirish (xfade + acrossfade)
# ─────────────────────────────────────────────────────────────
def _concat_segments(video_parts, audio_parts, out_path):
    """
    N ta video segment + har biri uchun silent audio →
    xfade tranzitsiyalar bilan birlashtirilgan video.
    """
    n = len(video_parts)
    if n == 0:
        return False
    if n == 1:
        # Faqat bitta segment — audio qo'shib qaytaramiz
        r = subprocess.run([
            "ffmpeg", "-y", "-i", video_parts[0],
            "-c:v", "copy", "-an",
            "-map", "0:v", out_path,
        ], capture_output=True, timeout=60)
        return r.returncode == 0 and os.path.exists(out_path)

    # filter_complex: barcha segmentlar xfade bilan
    cmd = ["ffmpeg", "-y"]
    for vp in video_parts:
        cmd += ["-i", vp]

    durs = [INTRO_DUR] + [PHOTO_DUR] * (n - 2) + [OUTRO_DUR]

    fc_v  = []
    prev  = "[0:v]"
    trans = ["fade", "slideleft", "slideright", "slideup", "slideleft",
             "slideright", "fade", "slideleft"]

    for i in range(1, n):
        t    = trans[(i - 1) % len(trans)]
        off  = sum(durs[:i]) - i * TRANS_DUR
        out  = f"[v{i:02d}]"
        fc_v.append(
            f"{prev}[{i}:v]xfade=transition={t}"
            f":duration={TRANS_DUR:.2f}:offset={off:.2f}{out}"
        )
        prev = out

    cmd += ["-filter_complex", ";".join(fc_v), "-map", prev]
    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-an", "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        print("  xfade xato:", r.stderr.decode("utf-8", errors="replace")[-300:])
        return _simple_concat(video_parts, out_path)
    return os.path.exists(out_path)


def _simple_concat(video_parts, out_path):
    """Fallback: oddiy concat."""
    ts  = datetime.now().strftime("%Y%m%d%H%M%S%f")
    txt = os.path.join(TEMP_DIR, f"pm_fc_{ts}.txt")
    with open(txt, "w", encoding="utf-8") as f:
        for p in video_parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    r = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", txt,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-an", "-movflags", "+faststart", out_path,
    ], capture_output=True, timeout=300)
    try:
        os.remove(txt)
    except Exception:
        pass
    return r.returncode == 0 and os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# Ovoz va musiqa qo'shish
# ─────────────────────────────────────────────────────────────
def _get_music():
    """Fon musiqasi faylini olish (assets/ yoki avto-yaratish)."""
    music_path = os.path.join(_HERE, "assets", "background.mp3")
    if os.path.exists(music_path):
        return music_path
    # Avto-yaratish
    ts      = datetime.now().strftime("%Y%m%d%H%M%S%f")
    gen_path = os.path.join(TEMP_DIR, f"pm_music_{ts}.aac")
    expr = (
        "0.40*sin(2*PI*60*t)*exp(0-9*(t-floor(t/0.5)*0.5))"
        "+0.25*sin(2*PI*200*t)*exp(0-14*(t+0.25-floor(t+0.25)))"
        "+0.15*sin(2*PI*82*t)*(0.5+0.5*sin(2*PI*0.5*t))"
    )
    r = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=44100:c=mono",
        "-t", "300", "-af", "volume=0.7",
        "-c:a", "aac", "-b:a", "128k", gen_path,
    ], capture_output=True, timeout=60)
    return gen_path if r.returncode == 0 and os.path.exists(gen_path) else None


def _mix_voice_music(video_path, voice_path, out_path, lang="uz"):
    """
    Video (silent) + AI ovoz + fon musiqasi → yakuniy video.
    Audio FX (EQ, kompressor) ovoz ustiga qo'llaniladi.
    """
    music_path = _get_music()
    fx         = AUDIO_FX.get(lang, AUDIO_FX.get("uz", "volume=1.0"))

    if music_path and os.path.exists(music_path):
        audio_filt = (
            f"[1:a]aresample=44100,{fx}[voice];"
            f"[2:a]volume={MUSIC_VOL},aresample=44100[mus];"
            f"[voice][mus]amix=inputs=2:duration=first:dropout_transition=3[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", voice_path,
            "-stream_loop", "-1", "-i", music_path,
            "-filter_complex", audio_filt,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", "-shortest",
            out_path,
        ]
    else:
        # Faqat ovoz
        audio_filt = f"[1:a]aresample=44100,{fx}[aout]"
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", voice_path,
            "-filter_complex", audio_filt,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", "-shortest",
            out_path,
        ]

    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0:
        print("  Audio mix xato:", r.stderr.decode("utf-8", errors="replace")[-300:])
    return r.returncode == 0 and os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# YouTube yuklash (SEO)
# ─────────────────────────────────────────────────────────────
def upload_photo_video(video_path: str, data: dict, lang: str) -> str | None:
    """Original foto-video ni YouTube ga SEO bilan yuklash."""
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

    sarlavha  = data.get("sarlavha", "")
    jumla1    = data.get("jumla1", "")
    jumla2    = data.get("jumla2", "")
    keywords  = data.get("keywords_en", [])
    today_str = date.today().strftime("%d.%m.%Y")

    yt_titles = {
        "uz": f"{sarlavha} | {today_str}",
        "ru": f"{sarlavha} | {today_str}",
        "en": f"{sarlavha} | {today_str}",
    }
    yt_title = yt_titles.get(lang, sarlavha)[:100]

    intro_desc = {
        "uz": f"1КУН GLOBAL — Дунёдаги энг муҳим янгиликлар.\n\n{sarlavha}\n\n",
        "ru": f"1ДЕНЬ GLOBAL — Главные новости мира.\n\n{sarlavha}\n\n",
        "en": f"1DAY GLOBAL — Top world news.\n\n{sarlavha}\n\n",
    }.get(lang, "")

    desc_lines = [intro_desc, jumla1, "", jumla2, ""]
    hashtags = {
        "uz": "#Янгиликлар #BreakingNews #1КУН #Дунё #Сиёсат",
        "ru": "#Новости #BreakingNews #1ДЕНЬ #Мир #Политика",
        "en": "#News #BreakingNews #1DAY #World #Politics",
    }.get(lang, "")
    desc_lines += ["━" * 30, hashtags, "#News2026"]

    description = "\n".join(desc_lines)[:4900]
    tags = list(dict.fromkeys(keywords))[:15] + ["News", "BreakingNews", "2026", "World", "Politics"]

    body = {
        "snippet": {
            "title":           yt_title,
            "description":     description,
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
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"     Upload: {int(status.progress()*100)}%", end="\r")
        vid_id = response.get("id", "")
        print(f"\n     ✅ https://youtu.be/{vid_id}")

        # Playlist ga qo'shish
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
                print(f"     📋 Playlist ({lang.upper()}): {playlist_id}")
            except Exception as pe:
                print(f"  Playlist xato: {pe}")
        return vid_id
    except Exception as e:
        print(f"  Upload xato: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# Asosiy pipeline
# ─────────────────────────────────────────────────────────────
def photo_pipeline(data: dict) -> str | None:
    """
    Queue JSON ma'lumotlaridan original foto-video yaratish.
    100% original kontent — YouTube monetizatsiyaga mos.

    data kalitlari (app.py / process_queue dan keladi):
        lang, sarlavha, youtube_script_latin / script_uz/ru/en,
        article_url, keywords_en, search_queries,
        location, daraja, jumla1, jumla2, hook
    """
    lang        = data.get("lang", "uz")
    sarlavha    = data.get("sarlavha", "")
    location    = data.get("location", "")
    daraja      = data.get("daraja", "xabar")
    article_url = data.get("article_url", "")
    keywords    = data.get("keywords_en", [])
    sq          = data.get("search_queries", keywords[:3])
    jumla1      = data.get("jumla1", "")
    jumla2      = data.get("jumla2", "")

    # Skript (narration matni)
    script = (
        data.get("youtube_script_latin", "")
        or data.get(f"script_{lang}", "")
        or data.get("script_uz", "")
        or f"{sarlavha}. {jumla1} {jumla2}"
    )
    # Faqat birinchi WORDS_LIMIT so'zni ishlatamiz (~120s naratsiya)
    words  = script.split()
    script = " ".join(words[:WORDS_LIMIT]) if len(words) > WORDS_LIMIT else script

    if not sarlavha or not script:
        print(f"  ⚠️  [{lang}] sarlavha yoki script yo'q — o'tkazildi")
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S%f")[:18]
    print(f"\n  📸 Photo pipeline [{lang.upper()}]: {sarlavha[:55]}")
    os.makedirs(TEMP_DIR,   exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── 1. Rasmlar yuklash ───────────────────────────────────
    # Asl inglizcha sarlavha — Pexels qidiruvida eng yaxshi natija beradi
    en_title = data.get("en_title", "") or data.get("article_title_en", "")

    photos = _gather_photos(
        article_url, keywords, sarlavha, lang, ts,
        count=MAX_PHOTOS, en_title=en_title,
    )

    # ── 2. TTS (AI ovoz) ─────────────────────────────────────
    voice_raw = os.path.join(TEMP_DIR, f"pm_voice_{ts}.mp3")
    if not _make_tts(script, lang, voice_raw):
        print(f"  ⚠️  TTS muvaffaqiyatsiz")
        return None
    voice_dur = _audio_duration(voice_raw)
    if voice_dur < 1:
        print(f"  ⚠️  TTS audio bo'sh")
        return None
    print(f"  TTS: {voice_dur:.1f}s")

    # ── 3. Video segmentlar ───────────────────────────────────
    # Segmentlar davomiyligi: intro + rasmlar + outro
    # Umumiy video davomiyligi ≥ voice_dur
    needed_photo_dur = max(voice_dur - INTRO_DUR - OUTRO_DUR + 5, PHOTO_DUR)
    n_photos_needed  = max(1, int(needed_photo_dur / PHOTO_DUR) + 1)

    # Rasmlarni loop qilib yetarlicha segment yaratamiz
    photo_pool = photos if photos else []
    if not photo_pool:
        print("  ℹ️  Rasm topilmadi — rang fon ishlatiladi")

    segments = []   # Video fayl yo'llari (silent)

    # Intro karta
    intro_img = os.path.join(TEMP_DIR, f"pm_intro_{ts}.jpg")
    intro_vid = os.path.join(TEMP_DIR, f"pm_intro_{ts}.mp4")
    _make_intro_card(sarlavha, lang, daraja, location, intro_img)
    if _card_to_video(intro_img, INTRO_DUR, intro_vid):
        segments.append(intro_vid)

    # Rasm segmentlari
    for seg_idx in range(n_photos_needed):
        effect = seg_idx % 6  # Har bir segmentga boshqa KB effekti

        if photo_pool:
            ph_source = photo_pool[seg_idx % len(photo_pool)]
        else:
            ph_source = None

        # Rasm + matn overlay
        caption = sarlavha if seg_idx == 0 else (jumla1 if seg_idx == 1 else "")
        overlay_img = os.path.join(TEMP_DIR, f"pm_ovl_{ts}_{seg_idx:02d}.jpg")
        seg_vid     = os.path.join(TEMP_DIR, f"pm_seg_{ts}_{seg_idx:02d}.mp4")

        if ph_source and os.path.exists(ph_source):
            _add_text_overlay(ph_source, caption, location, daraja, overlay_img)
            if _photo_to_video(overlay_img, PHOTO_DUR, effect, seg_vid):
                segments.append(seg_vid)
                print(f"  ✓ Segment {seg_idx+1} Ken Burns [{effect}] ({PHOTO_DUR}s)")
                continue

        # Fallback: rangli PIL karta
        fallback_img = os.path.join(TEMP_DIR, f"pm_fb_{ts}_{seg_idx:02d}.jpg")
        _make_dark_card(sarlavha, caption, location, lang, daraja, seg_idx+1, fallback_img)
        if _card_to_video(fallback_img, PHOTO_DUR, seg_vid):
            segments.append(seg_vid)
            print(f"  ✓ Segment {seg_idx+1} (karta fallback)")

    # Outro
    outro_img = os.path.join(TEMP_DIR, f"pm_outro_{ts}.jpg")
    outro_vid = os.path.join(TEMP_DIR, f"pm_outro_{ts}.mp4")
    _make_outro_card(lang, outro_img)
    if _card_to_video(outro_img, OUTRO_DUR, outro_vid):
        segments.append(outro_vid)

    if not segments:
        print("  ⚠️  Hech bir segment yaratilmadi")
        return None

    # ── 4. Concat ────────────────────────────────────────────
    concat_vid = os.path.join(TEMP_DIR, f"pm_concat_{ts}.mp4")
    if not _concat_segments(segments, [], concat_vid):
        print("  ⚠️  Concat muvaffaqiyatsiz")
        return None

    # ── 5. Ovoz + musiqa ─────────────────────────────────────
    out_name = f"{ts}_photo_{lang}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)
    if not _mix_voice_music(concat_vid, voice_raw, out_path, lang):
        # Fallback: faqat video
        shutil.copy(concat_vid, out_path)

    # ── 6. Cleanup ───────────────────────────────────────────
    _cleanup_pm(ts, segments, concat_vid)

    if os.path.exists(out_path):
        sz = os.path.getsize(out_path) / 1_048_576
        dur = voice_dur
        print(f"\n  ✅ {out_name} ({sz:.1f} MB, ~{dur:.0f}s)")

        # YouTube ga yuklash
        upload_photo_video(out_path, data, lang)
        return out_path
    return None


# ─────────────────────────────────────────────────────────────
# Qo'shimcha: qoraytirilgan karta (fallback rasm yo'q bo'lsa)
# ─────────────────────────────────────────────────────────────
def _make_dark_card(sarlavha, caption, location, lang, daraja, number, out_path):
    """Rasm topilmasa ishlatiladigan rang fon karta."""
    accent = {"muhim": C_RED, "tezkor": (240, 140, 0), "xabar": C_BLUE}.get(daraja, C_BLUE)
    img    = Image.new("RGB", (VW, VH), C_BG)
    draw   = ImageDraw.Draw(img)

    for y in range(VH):
        t = y / VH
        r = int(C_BG[0] * (1 - t) + C_DARK[0] * t)
        g = int(C_BG[1] * (1 - t) + C_DARK[1] * t)
        b = int(C_BG[2] * (1 - t) + C_DARK[2] * t)
        draw.line([(0, y), (VW, y)], fill=(r, g, b))

    draw.rectangle([(0, 0), (6, VH)],       fill=(*accent, 230))
    draw.rectangle([(0, VH - 6), (VW, VH)], fill=(*C_GOLD, 200))

    text = caption or sarlavha or ""
    wrapped = textwrap.wrap(text, width=40)[:4]
    ty = VH // 2 - len(wrapped) * 30
    for line in wrapped:
        draw.text((VW // 2 + 2, ty + 2), line, font=_font(36), fill=(0, 0, 0, 150), anchor="mt")
        draw.text((VW // 2, ty),          line, font=_font(36), fill=C_WHITE,         anchor="mt")
        ty += 48

    if location:
        draw.text((VW // 2, VH - 55), f"📍 {location}",
                  font=_font(22, False), fill=(*C_GOLD, 200), anchor="mm")

    brand = {"uz": "1КУН GLOBAL", "ru": "1ДЕНЬ GLOBAL", "en": "1DAY GLOBAL"}.get(lang, "1KUN")
    draw.text((VW // 2, VH - 25), brand, font=_font(20, False),
              fill=(*C_GOLD, 180), anchor="mm")

    img.save(out_path, "JPEG", quality=92)
    return out_path


# ─────────────────────────────────────────────────────────────
# Temp fayllarni tozalash
# ─────────────────────────────────────────────────────────────
def _cleanup_pm(ts, segments, concat_vid):
    for p in segments:
        try:
            if os.path.exists(p): os.remove(p)
        except Exception:
            pass
    try:
        if os.path.exists(concat_vid): os.remove(concat_vid)
    except Exception:
        pass
    for ext in ("jpg", "jpeg", "mp4", "mp3", "aac"):
        for f in glob.glob(os.path.join(TEMP_DIR, f"pm_*_{ts[:14]}*.{ext}")):
            try: os.remove(f)
            except Exception: pass
