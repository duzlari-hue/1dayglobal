"""special_shorts.py — Maxsus YouTube Shorts formatlari

FORMAT 1: "1 FAKT"             (One Fact)
  · Bitta yangilikdan eng kuchli bitta fakt/raqam
  · 30 sekund, 1 segment
  · Qora fon, ulkan oq raqam, qizil aksent

FORMAT 2: "BREAKING 60 SEC"   (Breaking News 60 Seconds)
  · Bitta yangilikni 4 qismda yoritish (VOQEA→NIMA→NIMA UCHUN→KEYINCHA)
  · 4 × 15 sekund = 60 sek
  · Qorong'i foto fon + BREAKING badge

FORMAT 3: "TOP-5 TEZKOR"      (Top 5 Fast)
  · Kunda to'plangan 5 ta muhim yangilik
  · 5 × 9 sekund = 45 sek
  · Ro'yxat karta, faol element highlighted

FORMAT 4: "RAQAMLARDA DUNYO"  (World in Numbers)
  · Bitta yangilik haqida 3 ta muhim raqam/stat
  · 3 × 15 sekund = ~45 sek
  · Qora fon, ulkan qizil raqam, oq matn

FORMAT 6: "BUGUN TARIXDA"     (On This Day)
  · Bugun sanasida tarixda yuz bergan 3 ta muhim voqea
  · 3 × 15 sekund = ~45 sek
  · Qorong'i fon, qizil yil, oq matn

Foydalanish:
    python special_shorts.py --fakt              # Format 1
    python special_shorts.py --breaking          # Format 2
    python special_shorts.py --top5              # Format 3
    python special_shorts.py --numbers           # Format 4 (queue dan)
    python special_shorts.py --history           # Format 6
    python special_shorts.py --lang uz           # Faqat bitta til
    python special_shorts.py --all               # Barcha formatlar
"""

import os, sys, re, json, glob, shutil, pathlib, asyncio
import logging, subprocess, random, textwrap, time
from datetime import datetime, date

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "TELEGRAM", ".env"))

from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
import edge_tts

from config import OUTPUT_DIR, TEMP_DIR, VOICES

# ─────────────────────────────────────────────────────────────
# Konstantlar
# ─────────────────────────────────────────────────────────────
SW, SH     = 1080, 1920       # 9:16 vertical
SEG_DUR    = 15               # Har bir segment (sekund)
N_SEGS     = 3
FPS        = 25
TRANS_DUR  = 0.5
MUSIC_VOL  = 0.18

TRANSITIONS = ["slideup", "slideleft", "slideright", "slideleft", "slideright"]

_HERE = pathlib.Path(__file__).parent

# 1DAY GLOBAL brand colors
C_BG    = (0,   0,   0)
C_DARK  = (14,  14,  14)
C_RED   = (204,  0,   0)
C_WHITE = (255, 255, 255)
C_LGRAY = (155, 150, 140)
C_CREAM = (245, 240, 232)

# ─────────────────────────────────────────────────────────────
# Shrift
# ─────────────────────────────────────────────────────────────
def _sf(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
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
# AI yordamchisi
# ─────────────────────────────────────────────────────────────
def _ai_ask(prompt: str, max_tokens: int = 600) -> str:
    """Groq → Anthropic zanjiri bilan AI ga savol."""
    try:
        sys.path.insert(0, str(_HERE.parent / "TELEGRAM"))
        from translator import groq_ask, _ask_anthropic, ANTHROPIC_API_KEY
    except ImportError:
        return ""

    raw = ""
    try:
        raw = groq_ask(prompt, max_tokens=max_tokens)
    except Exception as e:
        log.warning(f"Groq xato: {e}")
    if not raw and ANTHROPIC_API_KEY:
        try:
            raw = _ask_anthropic(prompt, max_tokens=max_tokens)
        except Exception as e:
            log.warning(f"Anthropic xato: {e}")
    return raw or ""


def _parse_json_list(raw: str, fallback: list) -> list:
    """AI javobidan JSON array ajratib olish."""
    try:
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            if isinstance(data, list) and data:
                return data
    except Exception as e:
        log.warning(f"JSON parse xato: {e}")
    return fallback


# ─────────────────────────────────────────────────────────────
# TTS
# ─────────────────────────────────────────────────────────────
async def _tts_async(text: str, voice: str, rate: str, out_path: str):
    comm = edge_tts.Communicate(text, voice, rate=rate)
    await comm.save(out_path)


def _make_tts(text: str, out_path: str, lang: str = "uz") -> bool:
    """TTS mp3 yasash."""
    vcfg = VOICES.get(lang, VOICES.get("uz", {})).get("default", {})
    voice = vcfg.get("voice", "en-US-ChristopherNeural")
    rate  = vcfg.get("rate", "-5%")
    try:
        asyncio.run(_tts_async(text, voice, rate, out_path))
        return os.path.exists(out_path)
    except Exception as e:
        log.warning(f"TTS xato [{lang}]: {e}")
        return False


def _pad_audio(in_path: str, out_path: str, duration: float) -> bool:
    r = subprocess.run([
        "ffmpeg", "-y", "-i", in_path,
        "-af", f"apad=pad_dur={duration}",
        "-t", str(duration),
        "-c:a", "aac", "-b:a", "96k", out_path
    ], capture_output=True, timeout=30)
    return r.returncode == 0 and os.path.exists(out_path)


def _silence(duration: float, out_path: str):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=mono",
        "-t", str(duration), "-c:a", "aac", "-b:a", "64k", out_path
    ], capture_output=True, timeout=20)


# ─────────────────────────────────────────────────────────────
# Rasm → Video
# ─────────────────────────────────────────────────────────────
def _img_to_video(img_path: str, audio_path: str, duration: float,
                  out_path: str, zoom: bool = False) -> bool:
    """
    Rasm → vertical video (1080×1920).
    zoom=True: sekin zoom-in effekti (Ken Burns).
    """
    frames = int(duration * FPS)
    if zoom:
        vf = (
            f"scale=2160:3840,"
            f"zoompan=z='min(zoom+0.0008,1.3)':d={frames}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
            f"scale={SW}:{SH},fps={FPS}"
        )
    else:
        vf = (
            f"scale={SW}:{SH}:force_original_aspect_ratio=decrease,"
            f"pad={SW}:{SH}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={FPS}"
        )
    r = subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", img_path,
        "-i", audio_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-c:a", "aac", "-b:a", "96k",
        "-t", str(duration), "-vf", vf, "-shortest",
        out_path
    ], capture_output=True, timeout=90)
    if r.returncode != 0:
        log.warning(r.stderr.decode("utf-8", "replace")[-400:])
    return r.returncode == 0 and os.path.exists(out_path)


def _concat_segments(parts: list, out_path: str) -> bool:
    """xfade tranzitsiyali concat."""
    n = len(parts)
    if n == 0:
        return False
    if n == 1:
        shutil.copy(parts[0], out_path)
        return True

    cmd = ["ffmpeg", "-y"]
    for p in parts:
        cmd += ["-i", p]

    durs   = [float(SEG_DUR)] * n
    fc_v   = []
    fc_a   = []
    prev_v = "[0:v]"
    prev_a = "[0:a]"

    for i in range(1, n):
        trans  = TRANSITIONS[(i - 1) % len(TRANSITIONS)]
        offset = sum(durs[:i]) - i * TRANS_DUR
        ov, oa = f"[v{i:02d}]", f"[a{i:02d}]"
        fc_v.append(
            f"{prev_v}[{i}:v]xfade=transition={trans}"
            f":duration={TRANS_DUR:.2f}:offset={offset:.2f}{ov}"
        )
        fc_a.append(f"{prev_a}[{i}:a]acrossfade=d={TRANS_DUR:.2f}{oa}")
        prev_v, prev_a = ov, oa

    fc = ";".join(fc_v + fc_a)
    cmd += [
        "-filter_complex", fc,
        "-map", prev_v, "-map", prev_a,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", out_path
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0:
        # Fallback: oddiy concat
        log.warning("xfade muvaffaqiyatsiz — oddiy concat...")
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S%f")
        lst = os.path.join(TEMP_DIR, f"sp_fc_{ts}.txt")
        with open(lst, "w") as f:
            for p in parts:
                f.write(f"file '{os.path.abspath(p)}'\n")
        r2 = subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", out_path
        ], capture_output=True, timeout=300)
        try:
            os.remove(lst)
        except Exception:
            pass
        return r2.returncode == 0 and os.path.exists(out_path)
    return os.path.exists(out_path)


def _add_music(video_path: str, out_path: str, vol: float = MUSIC_VOL) -> bool:
    """Fon musiqasini qo'shish."""
    try:
        pr = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ], capture_output=True, text=True, timeout=15)
        dur = float(pr.stdout.strip())
    except Exception:
        dur = SEG_DUR * N_SEGS

    music = _gen_beat(dur + 2)
    if not music:
        return False

    r = subprocess.run([
        "ffmpeg", "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", music,
        "-filter_complex",
        f"[1:a]volume={vol}[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=3[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-t", f"{dur:.3f}",
        out_path
    ], capture_output=True, timeout=300)
    try:
        os.remove(music)
    except Exception:
        pass
    return r.returncode == 0 and os.path.exists(out_path)


def _gen_beat(duration: float) -> str | None:
    """Qisqa ambient beat yasash."""
    out = os.path.join(TEMP_DIR, f"sp_beat_{int(time.time())}.aac")
    expr = (
        "0.40*sin(2*PI*60*t)*exp(0-9*(t-floor(t/0.5)*0.5))"
        "+0.25*sin(2*PI*200*t)*exp(0-14*(t+0.25-floor(t+0.25)))"
        "+0.08*sin(2*PI*4000*t)*exp(0-28*(t-floor(t/0.25)*0.25))"
        "+0.14*sin(2*PI*82*t)*(0.5+0.5*sin(2*PI*0.5*t))"
        "+0.05*sin(2*PI*330*t)*(0.3+0.3*sin(2*PI*0.07*t))"
    )
    r = subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=44100:c=mono",
        "-t", str(duration + 1),
        "-af", "volume=0.80",
        "-c:a", "aac", "-b:a", "128k", out
    ], capture_output=True, timeout=30)
    return out if r.returncode == 0 and os.path.exists(out) else None


# ─────────────────────────────────────────────────────────────
# FORMAT 4: RAQAMLARDA DUNYO — karta yasash
# ─────────────────────────────────────────────────────────────
def _make_numbers_card(
        stat: str, label: str, context: str,
        seg_num: int, lang: str, out_path: str) -> str:
    """
    Format 4 karta (1080×1920) — Image 4 uslubi:
      · Qora fon + chap qizil aksent bar
      · TOP BAR: '10' logo + 'RAQAMLARDA DUNYO' + seg_num/3
      · Ulkan qizil STAT raqam (e.g. '500K', '73%', '$1.2T')
      · Oq LABEL (e.g. 'YO'QOTILGAN ASKARLAR')
      · Qizil divider
      · Oq CONTEXT matni (2-3 qator)
      · BOTTOM BAR: @handle + 1DAYGLOBAL.NEWS
    """
    W, H = SW, SH
    img  = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)

    # Subtle grid (brand element)
    for gx in range(0, W, 90):
        draw.line([(gx, 0), (gx, H)], fill=(16, 16, 16), width=1)
    for gy in range(0, H, 90):
        draw.line([(0, gy), (W, gy)], fill=(16, 16, 16), width=1)

    # Chap aksent bar
    draw.rectangle([(0, 0), (7, H)], fill=C_RED)

    # ── TOP BAR ──────────────────────────────────────────────
    top_h = 64
    draw.rectangle([(0, 0), (W, top_h)], fill=(10, 10, 10))
    draw.rectangle([(0, top_h - 4), (W, top_h)], fill=C_RED)

    # '10' logo
    draw.rectangle([(14, 10), (62, 54)], fill=C_RED)
    draw.text((38, 32), "10", font=_sf(22), fill=C_WHITE, anchor="mm")

    # Section name
    sec_labels = {
        "uz": "RAQAMLARDA DUNYO",
        "ru": "МИР В ЦИФРАХ",
        "en": "WORLD IN NUMBERS",
    }
    draw.text((76, 32), sec_labels.get(lang, "WORLD IN NUMBERS"),
              font=_sf(18, False), fill=(180, 175, 165), anchor="lm")

    # Progress (N/3)
    draw.text((W - 16, 32), f"{seg_num}/3",
              font=_sf(20), fill=C_RED, anchor="rm")

    # ── STAT RAQAMI (ulkan, qizil) ────────────────────────────
    stat_clean = (stat or "?").strip()
    # Font o'lchamini matn uzunligiga moslash
    if   len(stat_clean) <= 3:   stat_fs = 280
    elif len(stat_clean) <= 5:   stat_fs = 220
    elif len(stat_clean) <= 7:   stat_fs = 180
    else:                        stat_fs = 140

    stat_y = top_h + 130
    draw.text((W // 2, stat_y), stat_clean,
              font=_sf(stat_fs), fill=C_RED, anchor="mt")

    # Taxminan stat raqam pastini hisoblash
    stat_bottom = stat_y + stat_fs + 20

    # ── LABEL (oq, katta) ─────────────────────────────────────
    label_y = max(stat_bottom + 10, top_h + 460)
    for line in textwrap.wrap((label or "").upper(), width=20)[:2]:
        draw.text((W // 2, label_y), line,
                  font=_sf(54), fill=C_WHITE, anchor="mt")
        label_y += 64

    # ── QIZIL DIVIDER ─────────────────────────────────────────
    div_y = label_y + 24
    draw.rectangle([(60, div_y), (W - 60, div_y + 4)], fill=C_RED)

    # ── CONTEXT MATNI ─────────────────────────────────────────
    ctx_y = div_y + 36
    ctx_clean = (context or "").strip()
    for line in textwrap.wrap(ctx_clean, width=26)[:4]:
        draw.text((W // 2, ctx_y), line,
                  font=_sf(40, False), fill=(220, 215, 205), anchor="mt")
        ctx_y += 52

    # ── BOTTOM BAR ────────────────────────────────────────────
    bot_h = 64
    bot_y = H - bot_h
    draw.rectangle([(0, bot_y), (W, H)], fill=(10, 10, 10))
    draw.rectangle([(0, bot_y), (W, bot_y + 3)], fill=C_RED)

    handles = {"uz": "@birkunday", "ru": "@birkunday_ru", "en": "@birkunday_en"}
    draw.text((20, bot_y + bot_h // 2), handles.get(lang, "@birkunday"),
              font=_sf(22, False), fill=C_LGRAY, anchor="lm")
    draw.text((W - 20, bot_y + bot_h // 2), "1DAYGLOBAL.NEWS",
              font=_sf(22, False), fill=C_RED, anchor="rm")

    img.save(out_path, "JPEG", quality=93)
    return out_path


# ─────────────────────────────────────────────────────────────
# FORMAT 6: BUGUN TARIXDA — karta yasash
# ─────────────────────────────────────────────────────────────
def _make_history_card(
        year: str, event: str, detail: str,
        seg_num: int, lang: str, bg_path: str | None,
        out_path: str) -> str:
    """
    Format 6 karta (1080×1920) — On This Day:
      · Qorong'i fon (foto bo'lsa — sepia, bo'lmasa — dark gradient)
      · TOP BADGE: 'BUGUN TARIXDA · ON THIS DAY'
      · Sana (kichik, kulrang)
      · Ulkan qizil YIL raqami
      · Qizil divider
      · Oq VOQEA sarlavhasi (bold)
      · Oq DETAIL matni
      · BOTTOM BAR
    """
    W, H = SW, SH
    img  = Image.new("RGB", (W, H), (12, 10, 10))
    draw = ImageDraw.Draw(img)

    # ── FON (foto yoki gradient) ──────────────────────────────
    if bg_path and os.path.exists(bg_path):
        try:
            bg = Image.open(bg_path).convert("RGB")
            bw, bh = bg.size
            tgt_r  = W / H
            src_r  = bw / bh
            if src_r > tgt_r:
                nh = bh; nw = int(bh * tgt_r)
                x  = (bw - nw) // 2
                bg = bg.crop((x, 0, x + nw, nh))
            else:
                nw = bw; nh = int(bw / tgt_r)
                y  = (bh - nh) // 2
                bg = bg.crop((0, y, nw, y + nh))
            bg = bg.resize((W, H), Image.LANCZOS)

            # Sepia effekti
            bg = ImageEnhance.Color(bg).enhance(0.0)       # greyscale
            bg = ImageEnhance.Brightness(bg).enhance(0.35)  # qoraytirish
            # Yengil jigarrang tint (sepia)
            tint = Image.new("RGB", (W, H), (30, 18, 8))
            bg   = Image.blend(bg, tint, alpha=0.28)

            img.paste(bg, (0, 0))
        except Exception:
            pass

    draw = ImageDraw.Draw(img)

    # Gradient overlay (pastdan qorayish)
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(grad)
    for dy in range(H):
        alpha = int(220 * (dy / H) ** 0.7)
        gd.line([(0, dy), (W, dy)], fill=(0, 0, 0, alpha))
    img = img.convert("RGBA")
    img.alpha_composite(grad)
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)

    # Chap aksent bar
    draw.rectangle([(0, 0), (6, H)], fill=C_RED)

    # ── TOP BAR ──────────────────────────────────────────────
    top_h = 64
    draw.rectangle([(0, 0), (W, top_h)], fill=(0, 0, 0, 220))
    draw.rectangle([(0, top_h - 4), (W, top_h)], fill=C_RED)

    draw.rectangle([(14, 10), (62, 54)], fill=C_RED)
    draw.text((38, 32), "10", font=_sf(22), fill=C_WHITE, anchor="mm")

    # Section label
    sec_labels = {
        "uz": "BUGUN TARIXDA",
        "ru": "В ЭТОТ ДЕНЬ",
        "en": "ON THIS DAY",
    }
    draw.text((76, 32), sec_labels.get(lang, "ON THIS DAY"),
              font=_sf(18, False), fill=(180, 175, 165), anchor="lm")

    # Sana + progress
    today_str = date.today().strftime("%d %b").upper()
    draw.text((W - 16, 32), f"{today_str}  ·  {seg_num}/3",
              font=_sf(17, False), fill=(180, 175, 165), anchor="rm")

    # ── BADGE: "BUGUN TARIXDA" katta ─────────────────────────
    badge_y = top_h + 80
    badge_txt = sec_labels.get(lang, "ON THIS DAY")
    bw = len(badge_txt) * 18 + 40
    bx = (W - bw) // 2
    draw.rectangle([(bx, badge_y), (bx + bw, badge_y + 56)], fill=C_RED)
    draw.text((W // 2, badge_y + 28), badge_txt,
              font=_sf(30), fill=C_WHITE, anchor="mm")

    # ── ULKAN YIL ─────────────────────────────────────────────
    year_y = badge_y + 80
    draw.text((W // 2, year_y), str(year),
              font=_sf(240), fill=C_RED, anchor="mt")

    # ── QIZIL DIVIDER ─────────────────────────────────────────
    div_y = year_y + 268
    draw.rectangle([(60, div_y), (W - 60, div_y + 4)], fill=C_RED)

    # ── VOQEA SARLAVHASI ──────────────────────────────────────
    ev_y = div_y + 36
    for line in textwrap.wrap((event or "").upper(), width=22)[:3]:
        draw.text((W // 2, ev_y), line,
                  font=_sf(54), fill=C_WHITE, anchor="mt")
        ev_y += 66

    # ── DETAIL matni ──────────────────────────────────────────
    det_y = ev_y + 20
    detail_clean = (detail or "").strip()
    for line in textwrap.wrap(detail_clean, width=28)[:3]:
        draw.text((W // 2, det_y), line,
                  font=_sf(36, False), fill=(200, 195, 185), anchor="mt")
        det_y += 46

    # ── BOTTOM BAR ────────────────────────────────────────────
    bot_h = 64
    bot_y = H - bot_h
    draw.rectangle([(0, bot_y), (W, H)], fill=(0, 0, 0))
    draw.rectangle([(0, bot_y), (W, bot_y + 3)], fill=C_RED)

    handles = {"uz": "@birkunday", "ru": "@birkunday_ru", "en": "@birkunday_en"}
    draw.text((20, bot_y + bot_h // 2), handles.get(lang, "@birkunday"),
              font=_sf(22, False), fill=C_LGRAY, anchor="lm")
    draw.text((W - 20, bot_y + bot_h // 2), "1DAYGLOBAL.NEWS",
              font=_sf(22, False), fill=C_RED, anchor="rm")

    img.save(out_path, "JPEG", quality=93)
    return out_path


# ─────────────────────────────────────────────────────────────
# FORMAT 4 — AI: yangilikdan 3 ta stat olish
# ─────────────────────────────────────────────────────────────
def _gen_news_stats(title: str, description: str) -> list:
    """
    AI dan yangilik haqida 3 ta muhim raqam/stat olish.
    Qaytaradi: [{"stat":"500K","label_uz":"...","label_ru":"...","label_en":"...",
                 "context_uz":"...","context_ru":"...","context_en":"...",
                 "narration_uz":"...","narration_ru":"...","narration_en":"..."}, ...]
    """
    prompt = (
        f'News: "{title}"\n'
        f'Details: "{(description or "")[:400]}"\n\n'
        "Generate 3 impactful statistics or numbers related to this story.\n"
        "Each stat must be a SPECIFIC number (%, $, K, M, B, years, km, etc.)\n"
        "Make them concrete, verifiable, and surprising.\n\n"
        "RULES:\n"
        "- uz (label+context+narration): ONLY Latin Uzbek. NO Cyrillic.\n"
        "- ru: Russian Cyrillic only\n"
        "- en: English\n"
        "- narration: full sentence for TTS (pronounce numbers in words if needed)\n"
        "- narration_uz: Latin Uzbek only, no numbers as digits\n\n"
        "Return ONLY valid JSON array (no markdown):\n"
        '[{"stat":"73%","label_uz":"ICHIMLIK SUV","label_ru":"ПИТЬЕВОЙ ВОДЫ",'
        '"label_en":"DRINKING WATER",'
        '"context_uz":"Yer yuzidagi barcha suvning atigi 73 foizi okeanlarda.",'
        '"context_ru":"73% воды Земли находится в океанах.",'
        '"context_en":"73% of Earth\'s water is in oceans.",'
        '"narration_uz":"Birinchi raqam. Yetmish uch foiz. Yer yuzidagi barcha suvning...",'
        '"narration_ru":"Первая цифра. Семьдесят три процента. Вся вода Земли...",'
        '"narration_en":"First number. Seventy three percent. Of all water on Earth..."},'
        '{"stat":"..."},{"stat":"..."}]'
    )

    # Fallback stats (umuman)
    fallbacks = [
        {
            "stat": "1M+",
            "label_uz": "DUNYO BOZORI",
            "label_ru": "МИРОВОЙ РЫНОК",
            "label_en": "GLOBAL MARKET",
            "context_uz": "Bu voqea millionlab odamga ta'sir qilmoqda.",
            "context_ru": "Это событие влияет на миллионы людей.",
            "context_en": "This event is affecting millions of people.",
            "narration_uz": "Birinchi raqam. Bir million. Bu voqea millionlab odamga ta'sir qilmoqda.",
            "narration_ru": "Первая цифра. Один миллион. Это событие влияет на миллионы людей.",
            "narration_en": "First number. One million. This event is affecting millions of people.",
        },
        {
            "stat": "24h",
            "label_uz": "MUDDATI",
            "label_ru": "СРОК",
            "label_en": "DEADLINE",
            "context_uz": "Voqea 24 soat ichida yangi bosqichga o'tdi.",
            "context_ru": "События перешли на новый уровень за 24 часа.",
            "context_en": "The situation escalated to a new level within 24 hours.",
            "narration_uz": "Ikkinchi raqam. Yigirma to'rt soat. Voqea yangi bosqichga o'tdi.",
            "narration_ru": "Вторая цифра. Двадцать четыре часа. События перешли на новый уровень.",
            "narration_en": "Second number. Twenty four hours. The situation escalated.",
        },
        {
            "stat": "3+",
            "label_uz": "DAVLATLAR",
            "label_ru": "СТРАНЫ",
            "label_en": "COUNTRIES",
            "context_uz": "Uchdan ortiq davlat bu jarayonda faol ishtirok etmoqda.",
            "context_ru": "Более трёх стран активно участвуют в процессе.",
            "context_en": "More than three countries are actively involved.",
            "narration_uz": "Uchinchi raqam. Uchdan ortiq davlat. Bu jarayonda faol ishtirok etmoqda.",
            "narration_ru": "Третья цифра. Более трёх стран. Активно участвуют в этом процессе.",
            "narration_en": "Third number. Three plus countries are actively involved in this process.",
        },
    ]

    raw = _ai_ask(prompt, max_tokens=800)
    result = _parse_json_list(raw, fallbacks)

    # Tekshiruv: kamida 3 ta element bo'lsin
    while len(result) < 3:
        result.append(fallbacks[len(result) % len(fallbacks)])
    return result[:3]


# ─────────────────────────────────────────────────────────────
# FORMAT 6 — AI: bugungi tarixiy voqealar
# ─────────────────────────────────────────────────────────────
def _gen_history_facts() -> list:
    """
    Bugungi sanada tarixda yuz bergan 3 ta muhim voqea.
    Qaytaradi: [{"year":"1969","event_uz":"...","event_ru":"...","event_en":"...",
                 "detail_uz":"...","detail_ru":"...","detail_en":"...",
                 "narration_uz":"...","narration_ru":"...","narration_en":"...",
                 "search_query":"..."}]
    """
    today_m_d = date.today().strftime("%B %d")   # e.g. "May 1"
    today_dd  = date.today().strftime("%d %B")   # e.g. "1 May"

    prompt = (
        f"Today's date: {today_m_d}\n\n"
        f"Generate 3 historically significant events that happened on {today_m_d} "
        f"in DIFFERENT years (ideally spread across different centuries/decades).\n\n"
        "Rules:\n"
        "- Choose real, verifiable historical events\n"
        "- Pick events from different eras (e.g. pre-1900, 1900-1970, 1970+)\n"
        "- uz: ONLY Latin Uzbek script. NO Cyrillic at all.\n"
        "- ru: Russian Cyrillic only\n"
        "- en: English\n"
        "- narration: sentence for TTS, write year in words\n"
        "- narration_uz example: 'Bugun tarixda. Bir ming to'qqiz yuz oltmish to'qqizinchi yilda...'\n"
        "- search_query: 2-4 English words for Pexels image search\n\n"
        "Return ONLY valid JSON array (no markdown):\n"
        '[{"year":"1886",'
        '"event_uz":"Chicago\'da Xeymarka maydoni ishchilar qo\'zg\'oloni boshlandi",'
        '"event_ru":"В Чикаго началось восстание рабочих на площади Хеймаркет",'
        '"event_en":"Chicago Haymarket affair workers uprising began",'
        '"detail_uz":"Bu voqea 1 may xalqaro mehnat kuniga asos bo\'ldi.",'
        '"detail_ru":"Это событие стало основой Международного дня труда.",'
        '"detail_en":"This led to May 1st becoming International Workers Day.",'
        '"narration_uz":"Bugun tarixda. Bir ming sakkiz yuz sakson oltinchi yilda. Chicago shahrida...",'
        '"narration_ru":"В этот день. В тысяча восемьсот восемьдесят шестом году. В Чикаго...",'
        '"narration_en":"On this day. In eighteen eighty six. In Chicago workers...",'
        '"search_query":"workers protest historical street"},'
        '{"year":"..."},{"year":"..."}]'
    )

    # Fallback — universal tarixiy voqealar
    today_str = date.today().strftime("%d %B")
    fallbacks = [
        {
            "year": "1945",
            "event_uz": "Ikkinchi jahon urushi yakunlandi",
            "event_ru": "Завершилась Вторая мировая война в Европе",
            "event_en": "World War II ended in Europe",
            "detail_uz": f"{today_str} — tarixning burilish nuqtasi.",
            "detail_ru": f"{today_str} — поворотная точка в истории.",
            "detail_en": f"{today_str} — a turning point in history.",
            "narration_uz": "Bugun tarixda. Bir ming to'qqiz yuz qirq beshinchi yilda. Ikkinchi jahon urushi yakunlandi.",
            "narration_ru": "В этот день. В тысяча девятьсот сорок пятом году. Завершилась Вторая мировая война.",
            "narration_en": "On this day. In nineteen forty five. World War Two ended in Europe.",
            "search_query": "world war history vintage",
        },
        {
            "year": "1969",
            "event_uz": "Apollo 11 oyga parvoz boshladi",
            "event_ru": "Аполлон-11 отправился к Луне",
            "event_en": "Apollo 11 launched to the Moon",
            "detail_uz": "Insoniyat birinchi marta oyga qadam qo'ydi.",
            "detail_ru": "Впервые в истории человек ступил на Луну.",
            "detail_en": "For the first time humans walked on the Moon.",
            "narration_uz": "Bugun tarixda. Bir ming to'qqiz yuz oltmish to'qqizinchi yilda. Apollo parvoz qildi.",
            "narration_ru": "В этот день. В тысяча девятьсот шестьдесят девятом году. Аполлон полетел к Луне.",
            "narration_en": "On this day. In nineteen sixty nine. Apollo eleven launched to the Moon.",
            "search_query": "apollo moon space astronaut",
        },
        {
            "year": "2004",
            "event_uz": "Facebook social tarmog'i tashkil etildi",
            "event_ru": "Основан Facebook",
            "event_en": "Facebook was founded",
            "detail_uz": "Mark Zuckerberg Garvard yotoqxonasida loyihani boshladi.",
            "detail_ru": "Марк Цукерберг запустил проект в общежитии Гарварда.",
            "detail_en": "Mark Zuckerberg launched it from his Harvard dorm room.",
            "narration_uz": "Bugun tarixda. Ikki ming to'rtinchi yilda. Facebook ijtimoiy tarmog'i tuzildi.",
            "narration_ru": "В этот день. В две тысячи четвёртом году. Был основан Facebook.",
            "narration_en": "On this day. In two thousand four. Facebook was founded.",
            "search_query": "social media technology internet",
        },
    ]

    raw = _ai_ask(prompt, max_tokens=900)
    result = _parse_json_list(raw, fallbacks)

    while len(result) < 3:
        result.append(fallbacks[len(result) % len(fallbacks)])
    return result[:3]


# ─────────────────────────────────────────────────────────────
# Pexels — tarixiy voqea uchun rasm qidirish
# ─────────────────────────────────────────────────────────────
def _fetch_pexels(query: str, out_path: str) -> bool:
    """Pexels dan rasm yuklab olish."""
    import requests
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key or not query.strip():
        return False
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": query[:80], "per_page": 10, "orientation": "portrait"},
            headers={"Authorization": api_key},
            timeout=12,
        )
        if r.status_code != 200:
            return False
        photos = r.json().get("photos", [])
        random.shuffle(photos)
        for ph in photos:
            src  = ph.get("src", {})
            url  = src.get("portrait") or src.get("large2x") or src.get("large", "")
            if not url:
                continue
            ir = requests.get(url, timeout=18)
            if ir.status_code == 200 and len(ir.content) >= 20_000:
                with open(out_path, "wb") as f:
                    f.write(ir.content)
                return True
    except Exception as e:
        log.warning(f"Pexels [{query}] xato: {e}")
    return False


# ─────────────────────────────────────────────────────────────
# FORMAT 4 — asosiy pipeline
# ─────────────────────────────────────────────────────────────
def make_numbers_short(title: str, description: str, lang: str = "uz") -> str | None:
    """
    "RAQAMLARDA DUNYO" Shorts video yasash.
    Qaytaradi: yaratilgan MP4 fayl yo'li yoki None.
    """
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info(f"📊 RAQAMLARDA DUNYO [{lang.upper()}] — boshlanmoqda: {title[:50]}")

    # 1. AI dan 3 ta stat olish
    stats = _gen_news_stats(title, description)
    log.info(f"  Statlar: {[s.get('stat','?') for s in stats]}")

    segments = []
    all_temps = []

    ordinals = {
        "uz": ["Birinchi", "Ikkinchi", "Uchinchi"],
        "ru": ["Первая",   "Вторая",   "Третья"],
        "en": ["First",    "Second",   "Third"],
    }

    for i, st in enumerate(stats, 1):
        stat    = st.get("stat", "?")
        label   = st.get(f"label_{lang}", st.get("label_en", ""))
        context = st.get(f"context_{lang}", st.get("context_en", ""))
        narr    = st.get(f"narration_{lang}", "")

        if not narr:
            ord_w = ordinals.get(lang, ordinals["en"])[i - 1]
            narr  = f"{ord_w} raqam. {stat}. {context}"

        # Karta
        card_path = os.path.join(TEMP_DIR, f"sp_num_{ts}_{lang}_{i}.jpg")
        all_temps.append(card_path)
        _make_numbers_card(stat, label, context, i, lang, card_path)

        # TTS
        tts_path = os.path.join(TEMP_DIR, f"sp_num_tts_{ts}_{lang}_{i}.mp3")
        all_temps.append(tts_path)
        tts_ok   = _make_tts(narr, tts_path, lang)

        # TTS ni SEG_DUR ga to'ldirish
        if tts_ok:
            padded = os.path.join(TEMP_DIR, f"sp_num_pad_{ts}_{lang}_{i}.aac")
            all_temps.append(padded)
            _pad_audio(tts_path, padded, SEG_DUR)
            audio_path = padded if os.path.exists(padded) else tts_path
        else:
            audio_path = os.path.join(TEMP_DIR, f"sp_num_sil_{ts}_{lang}_{i}.aac")
            all_temps.append(audio_path)
            _silence(SEG_DUR, audio_path)

        # Segment video
        seg_path = os.path.join(TEMP_DIR, f"sp_num_seg_{ts}_{lang}_{i}.mp4")
        all_temps.append(seg_path)
        if _img_to_video(card_path, audio_path, SEG_DUR, seg_path):
            segments.append(seg_path)
            log.info(f"  ✓ Segment {i}: {stat}")
        else:
            log.warning(f"  ⚠️  Segment {i} yaratilmadi")

    if not segments:
        log.error("  ❌ Hech bir segment yaratilmadi")
        return None

    # 2. Concat
    concat_path = os.path.join(TEMP_DIR, f"sp_num_concat_{ts}_{lang}.mp4")
    all_temps.append(concat_path)
    if not _concat_segments(segments, concat_path):
        log.error("  ❌ Concat muvaffaqiyatsiz")
        return None

    # 3. Musiqa
    final_path = os.path.join(OUTPUT_DIR, "videos",
                              f"{ts}_numbers_{lang}.mp4")
    os.makedirs(os.path.dirname(final_path), exist_ok=True)

    with_music = os.path.join(TEMP_DIR, f"sp_num_music_{ts}_{lang}.mp4")
    all_temps.append(with_music)
    if _add_music(concat_path, with_music):
        shutil.move(with_music, final_path)
    else:
        shutil.move(concat_path, final_path)

    # Temp tozalash
    for p in all_temps:
        try: os.remove(p)
        except Exception: pass

    log.info(f"  ✅ RAQAMLARDA DUNYO [{lang.upper()}]: {final_path}")
    return final_path


# ─────────────────────────────────────────────────────────────
# FORMAT 6 — asosiy pipeline
# ─────────────────────────────────────────────────────────────
def make_history_short(lang: str = "uz") -> str | None:
    """
    "BUGUN TARIXDA" Shorts video yasash.
    Qaytaradi: yaratilgan MP4 fayl yo'li yoki None.
    """
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    today_s = date.today().strftime("%d %B %Y")
    log.info(f"🕐 BUGUN TARIXDA [{lang.upper()}] — {today_s}")

    # 1. AI dan 3 ta tarixiy voqea olish
    facts = _gen_history_facts()
    log.info(f"  Yillar: {[f.get('year','?') for f in facts]}")

    segments  = []
    all_temps = []

    for i, fact in enumerate(facts, 1):
        year   = str(fact.get("year", "?"))
        event  = fact.get(f"event_{lang}", fact.get("event_en", ""))
        detail = fact.get(f"detail_{lang}", fact.get("detail_en", ""))
        narr   = fact.get(f"narration_{lang}", "")
        query  = fact.get("search_query", "history vintage")

        if not narr:
            narr = f"{year}. {event}. {detail}"

        # Pexels dan foto qidirish
        bg_path = os.path.join(TEMP_DIR, f"sp_hist_bg_{ts}_{i}.jpg")
        all_temps.append(bg_path)
        if not _fetch_pexels(query, bg_path):
            bg_path = None

        # Karta
        card_path = os.path.join(TEMP_DIR, f"sp_hist_{ts}_{lang}_{i}.jpg")
        all_temps.append(card_path)
        _make_history_card(year, event, detail, i, lang, bg_path, card_path)

        # TTS
        tts_path = os.path.join(TEMP_DIR, f"sp_hist_tts_{ts}_{lang}_{i}.mp3")
        all_temps.append(tts_path)
        tts_ok   = _make_tts(narr, tts_path, lang)

        if tts_ok:
            padded = os.path.join(TEMP_DIR, f"sp_hist_pad_{ts}_{lang}_{i}.aac")
            all_temps.append(padded)
            _pad_audio(tts_path, padded, SEG_DUR)
            audio_path = padded if os.path.exists(padded) else tts_path
        else:
            audio_path = os.path.join(TEMP_DIR, f"sp_hist_sil_{ts}_{lang}_{i}.aac")
            all_temps.append(audio_path)
            _silence(SEG_DUR, audio_path)

        # Segment video (zoom effekti bilan)
        seg_path = os.path.join(TEMP_DIR, f"sp_hist_seg_{ts}_{lang}_{i}.mp4")
        all_temps.append(seg_path)
        if _img_to_video(card_path, audio_path, SEG_DUR, seg_path, zoom=True):
            segments.append(seg_path)
            log.info(f"  ✓ Segment {i}: {year} — {event[:40]}")
        else:
            log.warning(f"  ⚠️  Segment {i} yaratilmadi")

    if not segments:
        log.error("  ❌ Hech bir segment yaratilmadi")
        return None

    # 2. Concat
    concat_path = os.path.join(TEMP_DIR, f"sp_hist_concat_{ts}_{lang}.mp4")
    all_temps.append(concat_path)
    if not _concat_segments(segments, concat_path):
        log.error("  ❌ Concat muvaffaqiyatsiz")
        return None

    # 3. Musiqa
    final_path = os.path.join(OUTPUT_DIR, "videos",
                              f"{ts}_history_{lang}.mp4")
    os.makedirs(os.path.dirname(final_path), exist_ok=True)

    with_music = os.path.join(TEMP_DIR, f"sp_hist_music_{ts}_{lang}.mp4")
    all_temps.append(with_music)
    if _add_music(concat_path, with_music, vol=0.12):  # tarix uchun tinchroq musiqa
        shutil.move(with_music, final_path)
    else:
        shutil.move(concat_path, final_path)

    # Temp tozalash
    for p in all_temps:
        try: os.remove(p)
        except Exception: pass

    log.info(f"  ✅ BUGUN TARIXDA [{lang.upper()}]: {final_path}")
    return final_path


# ─────────────────────────────────────────────────────────────
# FORMAT 1: 1 FAKT — karta yasash
# ─────────────────────────────────────────────────────────────
def _make_fakt_card(
        stat: str, claim: str, source: str,
        lang: str, out_path: str) -> str:
    """
    Format 1 karta (1080×1920):
      · Qora fon + grid
      · TOP BAR: '10' logo + '1 FAKT'
      · Ulkan jigarrang '1' (background element)
      · Qizil "1 FAKT" badge
      · Giant oq STAT raqam
      · Qizil divider
      · Claim matni
      · Source label
      · BOTTOM BAR
    """
    W, H = SW, SH
    img  = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)

    # Grid
    for gx in range(0, W, 90):
        draw.line([(gx, 0), (gx, H)], fill=(16, 16, 16), width=1)
    for gy in range(0, H, 90):
        draw.line([(0, gy), (W, gy)], fill=(16, 16, 16), width=1)

    # Chap aksent bar
    draw.rectangle([(0, 0), (7, H)], fill=C_RED)

    # TOP BAR
    top_h = 64
    draw.rectangle([(0, 0), (W, top_h)], fill=(10, 10, 10))
    draw.rectangle([(0, top_h - 4), (W, top_h)], fill=C_RED)
    draw.rectangle([(14, 10), (62, 54)], fill=C_RED)
    draw.text((38, 32), "10", font=_sf(22), fill=C_WHITE, anchor="mm")

    sec_lbl = {"uz": "1 FAKT", "ru": "1 ФАКТ", "en": "1 FACT"}.get(lang, "1 FACT")
    draw.text((76, 32), sec_lbl, font=_sf(20), fill=C_WHITE, anchor="lm")
    today_s = date.today().strftime("%d %b %Y").upper()
    draw.text((W - 16, 32), today_s, font=_sf(16, False), fill=C_LGRAY, anchor="rm")

    # Background "1" (massive, very dark)
    draw.text((W // 2, top_h + 60), "1",
              font=_sf(560), fill=(20, 0, 0), anchor="mt")

    # Qizil FAKT badge
    fakt_lbl = sec_lbl
    bw_fakt  = len(fakt_lbl) * 22 + 60
    bx_fakt  = (W - bw_fakt) // 2
    badge_y  = top_h + 80
    draw.rectangle([(bx_fakt, badge_y), (bx_fakt + bw_fakt, badge_y + 70)],
                   fill=C_RED)
    draw.text((W // 2, badge_y + 35), fakt_lbl,
              font=_sf(38), fill=C_WHITE, anchor="mm")

    # STAT (giant white)
    stat_clean = (stat or "").strip()
    s_fs = 200 if len(stat_clean) <= 5 else (160 if len(stat_clean) <= 8 else 120)
    stat_y = badge_y + 110
    draw.text((W // 2, stat_y), stat_clean,
              font=_sf(s_fs), fill=C_WHITE, anchor="mt")

    # Divider
    div_y = stat_y + s_fs + 24
    draw.rectangle([(60, div_y), (W - 60, div_y + 4)], fill=C_RED)

    # Claim matni
    cl_y = div_y + 36
    for line in textwrap.wrap((claim or "").strip(), width=24)[:4]:
        draw.text((W // 2, cl_y), line,
                  font=_sf(44, False), fill=(220, 215, 205), anchor="mt")
        cl_y += 56

    # Source
    if source:
        draw.text((W // 2, cl_y + 20), f"📌 {source}",
                  font=_sf(30, False), fill=C_LGRAY, anchor="mt")

    # BOTTOM BAR
    bot_h = 64
    bot_y = H - bot_h
    draw.rectangle([(0, bot_y), (W, H)], fill=(10, 10, 10))
    draw.rectangle([(0, bot_y), (W, bot_y + 3)], fill=C_RED)
    handles = {"uz": "@birkunday", "ru": "@birkunday_ru", "en": "@birkunday_en"}
    draw.text((20, bot_y + bot_h // 2), handles.get(lang, "@birkunday"),
              font=_sf(22, False), fill=C_LGRAY, anchor="lm")
    draw.text((W - 20, bot_y + bot_h // 2), "1DAYGLOBAL.NEWS",
              font=_sf(22, False), fill=C_RED, anchor="rm")

    img.save(out_path, "JPEG", quality=93)
    return out_path


def _gen_fakt(title: str, description: str) -> dict:
    """
    Yangilikdan bitta kuchli fakt.
    Returns: {"stat":"...", "claim_uz":"...", "claim_ru":"...", "claim_en":"...",
              "narration_uz":"...", "narration_ru":"...", "narration_en":"...",
              "source":"..."}
    """
    prompt = (
        f'News: "{title}"\n'
        f'Details: "{(description or "")[:300]}"\n\n'
        "Extract ONE single most shocking/surprising STATISTIC or FACT from this story.\n"
        "The stat must be a number, percentage, or short metric (≤8 chars max).\n"
        "The claim must explain it in ONE powerful sentence.\n\n"
        "Rules:\n"
        "- uz: ONLY Latin Uzbek script. NO Cyrillic.\n"
        "- ru: Russian Cyrillic\n"
        "- en: English\n"
        "- narration: full TTS sentence (~25 sec), numbers in words\n"
        "- source: short attribution (e.g. 'Reuters 2026', 'UN Report')\n\n"
        "Return ONLY valid JSON object (no markdown):\n"
        '{"stat":"73%","claim_uz":"Dunyo aholisining 73 foizi...",'
        '"claim_ru":"73% населения мира...",'
        '"claim_en":"73% of the world population...",'
        '"narration_uz":"Bugungi fakt. Yetmish uch foiz. Dunyo aholisining...",'
        '"narration_ru":"Факт дня. Семьдесят три процента. Населения мира...",'
        '"narration_en":"Fact of the day. Seventy three percent. Of the world population...",'
        '"source":"Reuters 2026"}'
    )
    fallback = {
        "stat": "1M+",
        "claim_uz": "Bu voqea millionlab odamning hayotiga bevosita ta'sir qilmoqda.",
        "claim_ru": "Это событие напрямую влияет на жизни миллионов людей.",
        "claim_en": "This event is directly affecting the lives of millions of people.",
        "narration_uz": "Bugungi fakt. Bir million. Bu voqea millionlab odamga ta'sir qilmoqda.",
        "narration_ru": "Факт дня. Один миллион. Это событие влияет на жизни миллионов людей.",
        "narration_en": "Fact of the day. One million. This event is affecting millions of people.",
        "source": "1DAY GLOBAL",
    }
    raw = _ai_ask(prompt, max_tokens=500)
    try:
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
        m   = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            if isinstance(data, dict) and data.get("stat"):
                return data
    except Exception as e:
        log.warning(f"_gen_fakt parse xato: {e}")
    return fallback


FAKT_DUR = 30  # 1 segment × 30 sek

def make_fakt_short(title: str, description: str, lang: str = "uz") -> str | None:
    """'1 FAKT' Shorts video (30 sek, 1 segment)."""
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info(f"⚡ 1 FAKT [{lang.upper()}]: {title[:50]}")

    fakt   = _gen_fakt(title, description)
    stat   = fakt.get("stat", "?")
    claim  = fakt.get(f"claim_{lang}", fakt.get("claim_en", ""))
    narr   = fakt.get(f"narration_{lang}", "")
    source = fakt.get("source", "1DAY GLOBAL")
    if not narr:
        narr = f"Bugungi fakt. {stat}. {claim}"

    all_temps = []

    card_path = os.path.join(TEMP_DIR, f"sp_fakt_{ts}_{lang}.jpg")
    all_temps.append(card_path)
    _make_fakt_card(stat, claim, source, lang, card_path)

    tts_path = os.path.join(TEMP_DIR, f"sp_fakt_tts_{ts}_{lang}.mp3")
    all_temps.append(tts_path)
    tts_ok = _make_tts(narr, tts_path, lang)

    if tts_ok:
        padded = os.path.join(TEMP_DIR, f"sp_fakt_pad_{ts}_{lang}.aac")
        all_temps.append(padded)
        _pad_audio(tts_path, padded, FAKT_DUR)
        audio_path = padded if os.path.exists(padded) else tts_path
    else:
        audio_path = os.path.join(TEMP_DIR, f"sp_fakt_sil_{ts}_{lang}.aac")
        all_temps.append(audio_path)
        _silence(FAKT_DUR, audio_path)

    raw_path = os.path.join(TEMP_DIR, f"sp_fakt_raw_{ts}_{lang}.mp4")
    all_temps.append(raw_path)
    if not _img_to_video(card_path, audio_path, FAKT_DUR, raw_path):
        log.error("  ❌ Video yaratilmadi")
        return None

    final_path = os.path.join(OUTPUT_DIR, "videos", f"{ts}_fakt_{lang}.mp4")
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    with_music = os.path.join(TEMP_DIR, f"sp_fakt_music_{ts}_{lang}.mp4")
    all_temps.append(with_music)
    if _add_music(raw_path, with_music, vol=0.15):
        shutil.move(with_music, final_path)
    else:
        shutil.move(raw_path, final_path)

    for p in all_temps:
        try: os.remove(p)
        except Exception: pass

    log.info(f"  ✅ 1 FAKT [{lang.upper()}]: {final_path}")
    return final_path


# ─────────────────────────────────────────────────────────────
# FORMAT 2: BREAKING 60 SEC — karta yasash
# ─────────────────────────────────────────────────────────────
BREAK_SEGS = 4
BREAK_DUR  = 15   # 4 × 15 = 60 sek

_BREAK_SEG_TYPES = {
    "uz": ["VOQEA", "NIMA BO'LDI", "NIMA UCHUN", "NIMA KEYINCHA"],
    "ru": ["СОБЫТИЕ", "ЧТО СЛУЧИЛОСЬ", "ПОЧЕМУ ВАЖНО", "ЧТО ДАЛЬШЕ"],
    "en": ["BREAKING", "WHAT HAPPENED", "WHY IT MATTERS", "WHAT'S NEXT"],
}


def _make_breaking_card(
        seg_type: str, headline: str, body: str,
        seg_num: int, n_segs: int, lang: str,
        photo_path: str | None, out_path: str) -> str:
    """
    Format 2 karta (1080×1920) — Breaking News:
      · Qorong'i foto fon + gradient overlay
      · TOP BAR: '10' logo + 'TEZKOR XABAR' + seg progress
      · Segment badge (VOQEA / ЧТО СЛУЧИЛОСЬ / BREAKING)
      · Large HEADLINE
      · Qizil divider
      · Body matni
      · Progress bar (pastda)
      · BOTTOM BAR
    """
    W, H = SW, SH
    img  = Image.new("RGB", (W, H), (8, 8, 8))
    draw = ImageDraw.Draw(img)

    # FON — foto yoki gradient
    if photo_path and os.path.exists(photo_path):
        try:
            bg = Image.open(photo_path).convert("RGB")
            bw, bh = bg.size
            tgt_r  = W / H
            src_r  = bw / bh
            if src_r > tgt_r:
                nh = bh; nw = int(bh * tgt_r)
                x  = (bw - nw) // 2
                bg = bg.crop((x, 0, x + nw, nh))
            else:
                nw = bw; nh = int(bw / tgt_r)
                y  = (bh - nh) // 2
                bg = bg.crop((0, y, nw, y + nh))
            bg = bg.resize((W, H), Image.LANCZOS)
            bg = ImageEnhance.Brightness(bg).enhance(0.25)
            img.paste(bg, (0, 0))
        except Exception:
            pass

    # Gradient overlay
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd   = ImageDraw.Draw(grad)
    for dy in range(H):
        alpha = int(170 * (dy / H) ** 0.5)
        gd.line([(0, dy), (W, dy)], fill=(0, 0, 0, alpha))
    img = img.convert("RGBA")
    img.alpha_composite(grad)
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)

    # Chap aksent bar
    draw.rectangle([(0, 0), (7, H)], fill=C_RED)

    # TOP BAR
    top_h = 64
    draw.rectangle([(0, 0), (W, top_h)], fill=(0, 0, 0))
    draw.rectangle([(0, top_h - 3), (W, top_h)], fill=C_RED)
    draw.rectangle([(14, 10), (62, 54)], fill=C_RED)
    draw.text((38, 32), "10", font=_sf(22), fill=C_WHITE, anchor="mm")
    break_lbl = {"uz": "TEZKOR XABAR", "ru": "СРОЧНО", "en": "BREAKING NEWS"}.get(lang, "BREAKING")
    draw.text((76, 32), break_lbl, font=_sf(20), fill=(255, 80, 80), anchor="lm")
    draw.text((W - 16, 32), f"{seg_num}/{n_segs}", font=_sf(20), fill=C_RED, anchor="rm")

    # Segment type badge
    badge_y = top_h + 60
    bw_txt  = seg_type.upper()
    bpx     = len(bw_txt) * 18 + 48
    bx      = (W - bpx) // 2
    draw.rectangle([(bx, badge_y), (bx + bpx, badge_y + 60)], fill=C_RED)
    draw.text((W // 2, badge_y + 30), bw_txt,
              font=_sf(30), fill=C_WHITE, anchor="mm")

    # HEADLINE
    hl_y = badge_y + 88
    for line in textwrap.wrap((headline or "").upper(), width=17)[:4]:
        draw.text((W // 2, hl_y), line,
                  font=_sf(72), fill=C_WHITE, anchor="mt")
        hl_y += 86

    # Divider
    div_y = hl_y + 16
    draw.rectangle([(60, div_y), (W - 60, div_y + 3)], fill=C_RED)

    # Body
    body_y = div_y + 36
    for line in textwrap.wrap((body or "").strip(), width=24)[:5]:
        draw.text((W // 2, body_y), line,
                  font=_sf(40, False), fill=(210, 205, 195), anchor="mt")
        body_y += 52

    # Progress bar
    prog_y = H - 130
    bar_w  = W - 60
    draw.rectangle([(30, prog_y), (30 + bar_w, prog_y + 8)], fill=(40, 40, 40))
    filled = int(bar_w * seg_num / n_segs)
    draw.rectangle([(30, prog_y), (30 + filled, prog_y + 8)], fill=C_RED)

    # BOTTOM BAR
    bot_h = 64
    bot_y = H - bot_h
    draw.rectangle([(0, bot_y), (W, H)], fill=(0, 0, 0))
    draw.rectangle([(0, bot_y), (W, bot_y + 3)], fill=C_RED)
    handles = {"uz": "@birkunday", "ru": "@birkunday_ru", "en": "@birkunday_en"}
    draw.text((20, bot_y + bot_h // 2), handles.get(lang, "@birkunday"),
              font=_sf(22, False), fill=C_LGRAY, anchor="lm")
    draw.text((W - 20, bot_y + bot_h // 2), "1DAYGLOBAL.NEWS",
              font=_sf(22, False), fill=C_RED, anchor="rm")

    img.save(out_path, "JPEG", quality=93)
    return out_path


def _gen_breaking_script(title: str, description: str) -> list:
    """
    Breaking news 4-qismli skript.
    Returns: [{"headline_uz":"...", "headline_ru":"...", "headline_en":"...",
               "body_uz":"...", "body_ru":"...", "body_en":"...",
               "narration_uz":"...", ...}, × 4]
    """
    prompt = (
        f'Breaking news: "{title}"\n'
        f'Details: "{(description or "")[:400]}"\n\n'
        "Create a 4-part 60-second breaking news script (4 × 15 seconds each):\n"
        "1. HOOK: shocking opening (grab attention immediately)\n"
        "2. WHAT HAPPENED: clear summary\n"
        "3. WHY IT MATTERS: impact and significance\n"
        "4. WHAT'S NEXT: consequence + 'Follow for updates'\n\n"
        "Rules:\n"
        "- uz: ONLY Latin Uzbek script. NO Cyrillic.\n"
        "- ru: Russian Cyrillic\n"
        "- en: English\n"
        "- headline: max 5 words (punchy)\n"
        "- body: 1-2 sentences\n"
        "- narration: 2-3 TTS sentences (~12 sec each)\n\n"
        "Return ONLY valid JSON array of exactly 4 objects (no markdown):\n"
        '[{"headline_uz":"DUNYO LARZAGA KELDI","headline_ru":"МИР ПОТРЯСЁН",'
        '"headline_en":"WORLD SHAKEN",'
        '"body_uz":"...","body_ru":"...","body_en":"...",'
        '"narration_uz":"...","narration_ru":"...","narration_en":"..."},'
        '{"headline_uz":"..."},{"headline_uz":"..."},{"headline_uz":"..."}]'
    )

    _hd_fb = [
        {"uz": "TEZKOR XABAR",  "ru": "СРОЧНОЕ СООБЩЕНИЕ", "en": "BREAKING NEWS"},
        {"uz": "NIMA BO'LDI",   "ru": "ЧТО ПРОИЗОШЛО",     "en": "WHAT HAPPENED"},
        {"uz": "NIMA UCHUN",    "ru": "ПОЧЕМУ ВАЖНО",       "en": "WHY IT MATTERS"},
        {"uz": "NIMA KEYINCHA", "ru": "ЧТО ДАЛЬШЕ",         "en": "WHAT'S NEXT"},
    ]
    _bd_fb = [title, description or title, title, "Yangiliklarga obuna bo'ling!"]

    fallbacks = []
    for i in range(4):
        bd = (_bd_fb[i] or title)[:120]
        fallbacks.append({
            "headline_uz": _hd_fb[i]["uz"],
            "headline_ru": _hd_fb[i]["ru"],
            "headline_en": _hd_fb[i]["en"],
            "body_uz": bd, "body_ru": bd, "body_en": bd,
            "narration_uz": bd, "narration_ru": bd, "narration_en": bd,
        })

    raw    = _ai_ask(prompt, max_tokens=1000)
    result = _parse_json_list(raw, fallbacks)
    while len(result) < 4:
        result.append(fallbacks[len(result) % 4])
    return result[:4]


def make_breaking_short(title: str, description: str, lang: str = "uz") -> str | None:
    """'BREAKING 60 SEC' Shorts video (60 sek, 4 segment)."""
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info(f"🔴 BREAKING 60 SEC [{lang.upper()}]: {title[:50]}")

    script    = _gen_breaking_script(title, description)
    seg_types = _BREAK_SEG_TYPES.get(lang, _BREAK_SEG_TYPES["en"])
    all_temps = []
    segments  = []

    # Pexels foto (bitta, barcha segmentlar uchun)
    photo_path = os.path.join(TEMP_DIR, f"sp_brk_bg_{ts}.jpg")
    all_temps.append(photo_path)
    photo_ok = False
    for q in [" ".join(title.split()[:4]), "breaking news press conference"]:
        if _fetch_pexels(q, photo_path):
            photo_ok = True
            break
    if not photo_ok:
        photo_path = None

    for i, seg in enumerate(script, 1):
        seg_type = seg_types[(i - 1) % len(seg_types)]
        headline = seg.get(f"headline_{lang}", seg.get("headline_en", "BREAKING"))
        body     = seg.get(f"body_{lang}",    seg.get("body_en",     ""))
        narr     = seg.get(f"narration_{lang}", "")
        if not narr:
            narr = f"{headline}. {body}"

        card_path = os.path.join(TEMP_DIR, f"sp_brk_{ts}_{lang}_{i}.jpg")
        all_temps.append(card_path)
        _make_breaking_card(seg_type, headline, body,
                            i, BREAK_SEGS, lang, photo_path, card_path)

        tts_path = os.path.join(TEMP_DIR, f"sp_brk_tts_{ts}_{lang}_{i}.mp3")
        all_temps.append(tts_path)
        tts_ok = _make_tts(narr, tts_path, lang)

        if tts_ok:
            padded = os.path.join(TEMP_DIR, f"sp_brk_pad_{ts}_{lang}_{i}.aac")
            all_temps.append(padded)
            _pad_audio(tts_path, padded, BREAK_DUR)
            audio_path = padded if os.path.exists(padded) else tts_path
        else:
            audio_path = os.path.join(TEMP_DIR, f"sp_brk_sil_{ts}_{lang}_{i}.aac")
            all_temps.append(audio_path)
            _silence(BREAK_DUR, audio_path)

        seg_path = os.path.join(TEMP_DIR, f"sp_brk_seg_{ts}_{lang}_{i}.mp4")
        all_temps.append(seg_path)
        if _img_to_video(card_path, audio_path, BREAK_DUR, seg_path):
            segments.append(seg_path)
            log.info(f"  ✓ Seg {i}/{BREAK_SEGS}: {headline[:40]}")
        else:
            log.warning(f"  ⚠️  Seg {i} yaratilmadi")

    if not segments:
        log.error("  ❌ Hech bir segment yaratilmadi")
        return None

    concat_path = os.path.join(TEMP_DIR, f"sp_brk_concat_{ts}_{lang}.mp4")
    all_temps.append(concat_path)
    if not _concat_segments(segments, concat_path):
        log.error("  ❌ Concat muvaffaqiyatsiz")
        return None

    final_path = os.path.join(OUTPUT_DIR, "videos", f"{ts}_breaking_{lang}.mp4")
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    with_music = os.path.join(TEMP_DIR, f"sp_brk_music_{ts}_{lang}.mp4")
    all_temps.append(with_music)
    if _add_music(concat_path, with_music, vol=0.20):
        shutil.move(with_music, final_path)
    else:
        shutil.move(concat_path, final_path)

    for p in all_temps:
        try: os.remove(p)
        except Exception: pass

    log.info(f"  ✅ BREAKING 60 SEC [{lang.upper()}]: {final_path}")
    return final_path


# ─────────────────────────────────────────────────────────────
# FORMAT 3: TOP-5 TEZKOR — karta yasash
# ─────────────────────────────────────────────────────────────
TOP5_SEGS = 5
TOP5_DUR  = 9   # 5 × 9 = 45 sek


def _make_top5_card(
        items: list, active_idx: int,
        lang: str, out_path: str) -> str:
    """
    Format 3 karta (1080×1920) — TOP-5 ro'yxati:
      · Qora fon + grid
      · TOP BAR + "TOP 5 TEZKOR"
      · 5 ta yangilik ro'yxati, faol element highlighted (qizil fon, katta shrift)
      · Boshqalar: kichik, kulrang
      · BOTTOM BAR
    items: [{"num":1, "headline":"...", "region":"USA"}, ...]
    """
    W, H = SW, SH
    img  = Image.new("RGB", (W, H), C_BG)
    draw = ImageDraw.Draw(img)

    # Grid
    for gx in range(0, W, 90):
        draw.line([(gx, 0), (gx, H)], fill=(16, 16, 16), width=1)
    for gy in range(0, H, 90):
        draw.line([(0, gy), (W, gy)], fill=(16, 16, 16), width=1)

    # Chap aksent bar
    draw.rectangle([(0, 0), (7, H)], fill=C_RED)

    # TOP BAR
    top_h = 64
    draw.rectangle([(0, 0), (W, top_h)], fill=(10, 10, 10))
    draw.rectangle([(0, top_h - 4), (W, top_h)], fill=C_RED)
    draw.rectangle([(14, 10), (62, 54)], fill=C_RED)
    draw.text((38, 32), "10", font=_sf(22), fill=C_WHITE, anchor="mm")
    sec_lbl = {"uz": "TOP 5 TEZKOR", "ru": "ТОП 5 СРОЧНО", "en": "TOP 5 FAST"}.get(lang, "TOP 5")
    draw.text((76, 32), sec_lbl, font=_sf(20), fill=C_WHITE, anchor="lm")
    today_s = date.today().strftime("%d %b").upper()
    draw.text((W - 16, 32), today_s, font=_sf(17, False), fill=C_LGRAY, anchor="rm")

    # Section label
    list_lbl = {
        "uz": "BUGUNGI ENG MUHIM",
        "ru": "ГЛАВНОЕ СЕГОДНЯ",
        "en": "TODAY'S TOP NEWS",
    }.get(lang, "TODAY'S TOP NEWS")
    draw.text((W // 2, top_h + 44), list_lbl,
              font=_sf(38), fill=C_RED, anchor="mt")

    # Thin separator
    draw.rectangle([(60, top_h + 100), (W - 60, top_h + 103)], fill=(35, 35, 35))

    # List area
    list_top = top_h + 112
    list_bot = H - 80
    list_h   = list_bot - list_top
    item_h   = list_h // TOP5_SEGS

    for idx, item in enumerate(items[:TOP5_SEGS]):
        is_active = (idx == active_idx)
        iy        = list_top + idx * item_h

        if is_active:
            # Highlight row
            draw.rectangle([(8, iy + 2), (W - 4, iy + item_h - 4)],
                           fill=(28, 4, 4))
            draw.rectangle([(0, iy + 2), (7, iy + item_h - 4)], fill=C_RED)

        # Number
        num_str   = str(item.get("num", idx + 1)).zfill(2)
        num_color = C_RED if is_active else (55, 50, 45)
        num_fs    = 54 if is_active else 38
        draw.text((52, iy + item_h // 2), num_str,
                  font=_sf(num_fs), fill=num_color, anchor="mm")

        # Region tag
        region = (item.get("region", "") or "").upper()[:6]
        reg_x  = 106
        if region:
            reg_color = (140, 135, 130) if is_active else (65, 60, 56)
            reg_y     = iy + item_h // 2 - (16 if is_active else 0)
            draw.text((reg_x, reg_y), region,
                      font=_sf(24, False), fill=reg_color, anchor="lm")

        # Headline
        headline = (item.get(f"headline", "") or "").strip()
        hl_color = C_WHITE if is_active else (80, 75, 70)
        hl_fs    = 42 if is_active else 31
        wrap_w   = 19 if is_active else 22
        hl_y_off = (10 if region else 0) + (4 if is_active else 0)

        lines = textwrap.wrap(headline, width=wrap_w)[:2]
        if len(lines) == 1:
            draw.text((reg_x, iy + item_h // 2 + hl_y_off),
                      lines[0], font=_sf(hl_fs), fill=hl_color, anchor="lm")
        else:
            start_y = iy + (item_h // 2 - (hl_fs + 4)) + hl_y_off
            for li, ln in enumerate(lines):
                draw.text((reg_x, start_y + li * (hl_fs + 4)),
                          ln, font=_sf(hl_fs), fill=hl_color, anchor="lm")

        # Divider between items
        if idx < TOP5_SEGS - 1:
            div_y2 = iy + item_h - 2
            draw.rectangle([(60, div_y2), (W - 20, div_y2 + 1)],
                           fill=(28, 28, 28))

    # BOTTOM BAR
    bot_h = 64
    bot_y = H - bot_h
    draw.rectangle([(0, bot_y), (W, H)], fill=(10, 10, 10))
    draw.rectangle([(0, bot_y), (W, bot_y + 3)], fill=C_RED)
    handles = {"uz": "@birkunday", "ru": "@birkunday_ru", "en": "@birkunday_en"}
    draw.text((20, bot_y + bot_h // 2), handles.get(lang, "@birkunday"),
              font=_sf(22, False), fill=C_LGRAY, anchor="lm")
    draw.text((W - 20, bot_y + bot_h // 2), "1DAYGLOBAL.NEWS",
              font=_sf(22, False), fill=C_RED, anchor="rm")

    img.save(out_path, "JPEG", quality=93)
    return out_path


def _gen_top5(articles: list) -> list:
    """
    5 ta yangilikdan TOP-5 ro'yxati.
    articles: [{"title":"...", "description":"...", "source":"..."}, ...]
    Returns: [{"num":1, "headline_uz":"...", "headline_ru":"...", "headline_en":"...",
               "region":"USA", "narration_uz":"...", ...}, ...]
    """
    if not articles:
        return []

    arts_text = "\n".join(
        f'{i+1}. {a.get("title","")[:120]}' for i, a in enumerate(articles[:5])
    )
    prompt = (
        f"Given {min(5, len(articles))} news headlines:\n{arts_text}\n\n"
        "Create a TOP-5 breaking news list for YouTube Shorts.\n"
        "Rules:\n"
        "- headline: max 6 words (punchy, impactful)\n"
        "- region: 2-6 letter code (USA, EU, ASIA, UN, AFRICA, WORLD)\n"
        "- narration: 1-2 TTS sentences (~8 sec each)\n"
        "- uz: ONLY Latin Uzbek. NO Cyrillic.\n"
        "- ru: Russian Cyrillic\n"
        "- en: English\n\n"
        "Return ONLY valid JSON array of exactly 5 objects (no markdown):\n"
        '[{"num":1,"headline_uz":"...","headline_ru":"...","headline_en":"...",'
        '"region":"USA","narration_uz":"...","narration_ru":"...","narration_en":"..."},'
        '{"num":2,...},{"num":3,...},{"num":4,...},{"num":5,...}]'
    )

    fallbacks = []
    for i, a in enumerate(articles[:5], 1):
        t = a.get("title", f"Dunyo yangiligi #{i}")[:80]
        fallbacks.append({
            "num": i,
            "headline_uz": t, "headline_ru": t, "headline_en": t,
            "region": "WORLD",
            "narration_uz": f"Raqam {i}. {t[:100]}",
            "narration_ru": f"Номер {i}. {t[:100]}",
            "narration_en": f"Number {i}. {t[:100]}",
        })
    while len(fallbacks) < 5:
        i = len(fallbacks) + 1
        fallbacks.append({
            "num": i,
            "headline_uz": f"Dunyo yangiligi #{i}",
            "headline_ru": f"Мировые новости #{i}",
            "headline_en": f"World news #{i}",
            "region": "WORLD",
            "narration_uz": f"Beshinchi yangilik. Dunyo yangiliklari davom etmoqda.",
            "narration_ru": f"Пятая новость. Мировые события продолжаются.",
            "narration_en": f"Number five. World news continues today.",
        })

    raw    = _ai_ask(prompt, max_tokens=900)
    result = _parse_json_list(raw, fallbacks)
    for i, r in enumerate(result):
        r["num"] = i + 1
    while len(result) < 5:
        result.append(fallbacks[len(result)])
    return result[:5]


def make_top5_short(articles: list, lang: str = "uz") -> str | None:
    """'TOP-5 TEZKOR' Shorts video (45 sek, 5 segment)."""
    if not articles:
        log.warning("make_top5_short: maqolalar bo'sh")
        return None

    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info(f"📋 TOP-5 TEZKOR [{lang.upper()}]: {len(articles)} ta maqola")

    items     = _gen_top5(articles)
    all_temps = []
    segments  = []

    for active_idx in range(min(TOP5_SEGS, len(items))):
        item = items[active_idx]
        narr = item.get(f"narration_{lang}", item.get("narration_en", ""))
        if not narr:
            narr = item.get(f"headline_{lang}", "")

        card_path = os.path.join(TEMP_DIR, f"sp_top5_{ts}_{lang}_{active_idx+1}.jpg")
        all_temps.append(card_path)
        _make_top5_card(items, active_idx, lang, card_path)

        tts_path = os.path.join(TEMP_DIR, f"sp_top5_tts_{ts}_{lang}_{active_idx+1}.mp3")
        all_temps.append(tts_path)
        tts_ok = _make_tts(narr, tts_path, lang)

        if tts_ok:
            padded = os.path.join(TEMP_DIR, f"sp_top5_pad_{ts}_{lang}_{active_idx+1}.aac")
            all_temps.append(padded)
            _pad_audio(tts_path, padded, TOP5_DUR)
            audio_path = padded if os.path.exists(padded) else tts_path
        else:
            audio_path = os.path.join(TEMP_DIR, f"sp_top5_sil_{ts}_{lang}_{active_idx+1}.aac")
            all_temps.append(audio_path)
            _silence(TOP5_DUR, audio_path)

        seg_path = os.path.join(TEMP_DIR, f"sp_top5_seg_{ts}_{lang}_{active_idx+1}.mp4")
        all_temps.append(seg_path)
        if _img_to_video(card_path, audio_path, TOP5_DUR, seg_path):
            segments.append(seg_path)
            hl = item.get(f"headline_{lang}", item.get("headline_en", ""))
            log.info(f"  ✓ #{active_idx+1}: {hl[:40]}")
        else:
            log.warning(f"  ⚠️  Seg {active_idx+1} yaratilmadi")

    if not segments:
        log.error("  ❌ Hech bir segment yaratilmadi")
        return None

    concat_path = os.path.join(TEMP_DIR, f"sp_top5_concat_{ts}_{lang}.mp4")
    all_temps.append(concat_path)
    if not _concat_segments(segments, concat_path):
        log.error("  ❌ Concat muvaffaqiyatsiz")
        return None

    final_path = os.path.join(OUTPUT_DIR, "videos", f"{ts}_top5_{lang}.mp4")
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    with_music = os.path.join(TEMP_DIR, f"sp_top5_music_{ts}_{lang}.mp4")
    all_temps.append(with_music)
    if _add_music(concat_path, with_music, vol=0.22):
        shutil.move(with_music, final_path)
    else:
        shutil.move(concat_path, final_path)

    for p in all_temps:
        try: os.remove(p)
        except Exception: pass

    log.info(f"  ✅ TOP-5 TEZKOR [{lang.upper()}]: {final_path}")
    return final_path


# ─────────────────────────────────────────────────────────────
# YouTube ga yuklash
# ─────────────────────────────────────────────────────────────
def _upload_to_youtube(video_path: str, title: str, description: str,
                       tags: list, lang: str) -> str | None:
    """
    Yaratilgan Shorts ni YouTube ga yuklash.
    TOKEN_FILE va CLIENT_SECRETS — _HERE (YOUTUBE/) papkasida.
    Nisbiy yo'l muammosini hal qilish uchun vaqtincha shu papkaga o'tiladi.
    """
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        log.warning(f"YouTube import xato (googleapiclient): {e}")
        return None

    # Per-til token: uz→@1kunnews, en→@1daykun, ru→@1dennews
    # MUHIM: client_secrets.json va youtube_token_*.json YOUTUBE/ papkasida.
    # TELEGRAM/app.py dan chaqirilganda:
    #   1) sys.modules["config"] TELEGRAM config ga ko'rsatadi → CLIENT_SECRETS yo'q
    #   2) os.getcwd() TELEGRAM/ → client_secrets.json topilmaydi
    # Ikkalasini ham vaqtincha to'g'rilaymiz.
    import sys as _sys
    import os as _os
    import importlib.util as _ilu

    _prev_cwd    = _os.getcwd()
    _prev_config = _sys.modules.get("config")

    try:
        # 1. Ishchi papkani YOUTUBE/ ga o'tkazish (client_secrets.json va tokenlar shu yerda)
        _os.chdir(str(_HERE))

        # 2. YOUTUBE config ni to'g'ridan-to'g'ri yuklash va o'rnatish
        _yt_cfg_spec = _ilu.spec_from_file_location("config", str(_HERE / "config.py"))
        _yt_cfg_mod  = _ilu.module_from_spec(_yt_cfg_spec)
        _yt_cfg_spec.loader.exec_module(_yt_cfg_mod)
        _sys.modules["config"] = _yt_cfg_mod

        # 3. youtube_maker ni yangi config bilan yuklash
        _sys.modules.pop("youtube_maker", None)
        if str(_HERE) not in _sys.path:
            _sys.path.insert(0, str(_HERE))
        from youtube_maker import youtube_auth as _yt_auth
        yt = _yt_auth(lang)

    except Exception as e:
        log.error(f"  ❌ YouTube auth xato ({lang}): {e}")
        return None
    finally:
        # Ishchi papka va config ni qayta tiklash
        try:
            _os.chdir(_prev_cwd)
        except Exception:
            pass
        try:
            if _prev_config is not None:
                _sys.modules["config"] = _prev_config
            else:
                _sys.modules.pop("config", None)
        except Exception:
            pass

    body = {
        "snippet": {
            "title":           title[:100],
            "description":     description[:4900],
            "tags":            tags,
            "categoryId":      "25",          # News & Politics
            "defaultLanguage": lang,
        },
        "status": {
            "privacyStatus":           "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    # Video fayl absolyut yo'li
    video_abs = os.path.abspath(video_path)
    if not os.path.exists(video_abs):
        log.error(f"  ❌ Video fayl topilmadi: {video_abs}")
        return None

    size_mb = os.path.getsize(video_abs) / 1024 / 1024
    log.info(f"  ⬆️  YouTube yuklash: {os.path.basename(video_abs)} ({size_mb:.1f} MB)")
    log.info(f"     Sarlavha: {title[:70]}")

    try:
        media = MediaFileUpload(
            video_abs, mimetype="video/mp4",
            resumable=True, chunksize=5 * 1024 * 1024
        )
        req  = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                log.info(f"     ... {pct}%")
        vid_id = resp.get("id", "")
        log.info(f"  ✅ YouTube: https://youtu.be/{vid_id}")
        return vid_id
    except Exception as e:
        log.error(f"  ❌ YouTube yuklash xato: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# Entry points (app.py ga integratsiya uchun)
# ─────────────────────────────────────────────────────────────
def run_numbers_short(article: dict, langs: tuple = ("uz", "ru", "en")):
    """
    app.py tomonidan chaqiriladi — pipeline tugagandan keyin.
    article: {"title": str, "description": str, ...}
    """
    title = article.get("title", "")
    desc  = article.get("description", "") or article.get("summary", "")
    if not title:
        log.warning("run_numbers_short: title bo'sh — o'tkazildi")
        return

    for lang in langs:
        try:
            path = make_numbers_short(title, desc, lang)
            if path:
                today = date.today().strftime("%d.%m.%Y")
                sec_names = {
                    "uz": "Raqamlarda Dunyo",
                    "ru": "Мир в Цифрах",
                    "en": "World in Numbers",
                }
                tags = {
                    "uz": ["Shorts","YangilikUzbek","RaqamlardaDunyo","1KUN","Dunyo"],
                    "ru": ["Shorts","НовостиРоссия","МирВЦифрах","1День","Мир"],
                    "en": ["Shorts","WorldNews","WorldInNumbers","1Day","Facts"],
                }.get(lang, ["Shorts","News"])

                _upload_to_youtube(
                    path,
                    title=f"{sec_names[lang]} | {today} | #{lang.upper()}",
                    description=(
                        f"{sec_names[lang]} — {today}\n\n"
                        f"{title}\n\n"
                        f"#Shorts #{'Yangiliklar' if lang=='uz' else 'News'} "
                        f"#1DayGlobal"
                    ),
                    tags=tags,
                    lang=lang,
                )
        except Exception as e:
            log.error(f"run_numbers_short [{lang}] xato: {e}")


def run_fakt_short(article: dict, langs: tuple = ("uz", "ru", "en")):
    """Format 1: '1 FAKT' — pipeline dan keyin chaqiriladi."""
    title = article.get("title", "")
    desc  = article.get("description", "") or article.get("summary", "")
    if not title:
        return
    for lang in langs:
        try:
            path = make_fakt_short(title, desc, lang)
            if path:
                today = date.today().strftime("%d.%m.%Y")
                names = {"uz": "1 Fakt", "ru": "1 Факт", "en": "1 Fact"}
                tags  = {
                    "uz": ["Shorts", "1Fakt", "Yangilik", "1KUN", "Fakt"],
                    "ru": ["Shorts", "1Факт", "Новости",  "1День", "Факт"],
                    "en": ["Shorts", "1Fact", "News",     "1Day",  "Fact"],
                }.get(lang, ["Shorts", "News", "Fact"])
                _upload_to_youtube(
                    path,
                    title=f"{names.get(lang,'1 Fact')} | {today} #{lang.upper()}",
                    description=f"{names.get(lang,'1 Fact')} — {today}\n{title}\n#Shorts #Fact #1DayGlobal",
                    tags=tags, lang=lang,
                )
        except Exception as e:
            log.error(f"run_fakt_short [{lang}] xato: {e}")


def run_breaking_short(article: dict, langs: tuple = ("uz", "ru", "en")):
    """Format 2: 'BREAKING 60 SEC' — pipeline dan keyin chaqiriladi."""
    title = article.get("title", "")
    desc  = article.get("description", "") or article.get("summary", "")
    if not title:
        return
    for lang in langs:
        try:
            path = make_breaking_short(title, desc, lang)
            if path:
                today = date.today().strftime("%d.%m.%Y")
                names = {"uz": "Tezkor Xabar", "ru": "Срочно", "en": "Breaking News"}
                tags  = {
                    "uz": ["Shorts", "TezkorXabar", "Yangilik", "1KUN", "Breaking"],
                    "ru": ["Shorts", "Срочно",      "Новости",  "1День","Breaking"],
                    "en": ["Shorts", "BreakingNews", "News",    "1Day", "Breaking"],
                }.get(lang, ["Shorts", "Breaking", "News"])
                _upload_to_youtube(
                    path,
                    title=f"{names.get(lang,'Breaking')} | {today} #{lang.upper()}",
                    description=f"{names.get(lang,'Breaking')} — {today}\n{title}\n#Shorts #Breaking #1DayGlobal",
                    tags=tags, lang=lang,
                )
        except Exception as e:
            log.error(f"run_breaking_short [{lang}] xato: {e}")


def run_top5_short(articles: list, langs: tuple = ("uz", "ru", "en")):
    """Format 3: 'TOP-5 TEZKOR' — run_daily_digest dan keyin chaqiriladi."""
    if not articles:
        log.warning("run_top5_short: maqolalar ro'yxati bo'sh")
        return
    for lang in langs:
        try:
            path = make_top5_short(articles, lang)
            if path:
                today = date.today().strftime("%d.%m.%Y")
                names = {"uz": "Top 5 Tezkor", "ru": "Топ 5 Срочно", "en": "Top 5 Fast"}
                tags  = {
                    "uz": ["Shorts", "Top5", "Tezkor", "1KUN", "Yangiliklar"],
                    "ru": ["Shorts", "Топ5", "Срочно", "1День","Новости"],
                    "en": ["Shorts", "Top5", "Breaking","1Day", "WorldNews"],
                }.get(lang, ["Shorts", "Top5", "News"])
                _upload_to_youtube(
                    path,
                    title=f"{names.get(lang,'Top 5')} | {today} #{lang.upper()}",
                    description=f"{names.get(lang,'Top 5')} — {today}\n#Shorts #Top5 #News #1DayGlobal",
                    tags=tags, lang=lang,
                )
        except Exception as e:
            log.error(f"run_top5_short [{lang}] xato: {e}")


def run_history_short(langs: tuple = ("uz", "ru", "en")):
    """
    app.py tomonidan chaqiriladi — har kuni bir marta (masalan 10:00).
    """
    for lang in langs:
        try:
            path = make_history_short(lang)
            if path:
                today  = date.today().strftime("%d %B %Y").upper()
                names  = {
                    "uz": "Bugun Tarixda",
                    "ru": "В Этот День",
                    "en": "On This Day",
                }
                tags = {
                    "uz": ["Shorts","BugunTarixda","Tarix","1KUN","OniThisDay"],
                    "ru": ["Shorts","ВЭтотДень","История","1День","OnThisDay"],
                    "en": ["Shorts","OnThisDay","History","1Day","HistoricalFact"],
                }.get(lang, ["Shorts","History","OnThisDay"])

                _upload_to_youtube(
                    path,
                    title=f"{names[lang]} | {today} | #{lang.upper()}",
                    description=(
                        f"{names[lang]} — {today}\n\n"
                        f"Bugun tarixda nima bo'lgan?\n\n"
                        f"#Shorts #OnThisDay #History #1DayGlobal"
                    ),
                    tags=tags,
                    lang=lang,
                )
        except Exception as e:
            log.error(f"run_history_short [{lang}] xato: {e}")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Special Shorts generator")
    parser.add_argument("--fakt",     action="store_true", help="Format 1: 1 Fakt (30 sek)")
    parser.add_argument("--breaking", action="store_true", help="Format 2: Breaking 60 Sec")
    parser.add_argument("--top5",     action="store_true", help="Format 3: Top-5 Tezkor")
    parser.add_argument("--numbers",  action="store_true", help="Format 4: Raqamlarda Dunyo")
    parser.add_argument("--history",  action="store_true", help="Format 6: Bugun Tarixda")
    parser.add_argument("--all",      action="store_true", help="Barcha formatlar")
    parser.add_argument("--lang",     default="all",       help="uz | ru | en | all")
    parser.add_argument("--title",    default="",          help="Yangilik sarlavhasi (1,2,4 uchun)")
    parser.add_argument("--desc",     default="",          help="Yangilik tavsifi")
    parser.add_argument("--queue",    action="store_true", help="Queue dan oxirgi yangilikni olish")
    args = parser.parse_args()

    langs = ("uz", "ru", "en") if args.lang == "all" else (args.lang,)

    any_flag = any([args.fakt, args.breaking, args.top5, args.numbers, args.history, args.all])
    if not any_flag:
        parser.print_help()
        sys.exit(0)

    # Sarlavha va tavsif — queue dan yoki CLI dan
    title, desc = args.title, args.desc
    if (args.fakt or args.breaking or args.numbers or args.all) and (args.queue or not title):
        try:
            import glob as _gl
            queue_dir = _HERE.parent / "YOUTUBE" / "queue"
            files     = sorted(_gl.glob(str(queue_dir / "*.json")), reverse=True)
            if files:
                with open(files[0], encoding="utf-8") as _qf:
                    qdata = json.load(_qf)
                art   = qdata.get("article", {})
                title = title or art.get("title", "")
                desc  = desc  or art.get("description", "")
                log.info(f"  Queue: {title[:60]}")
        except Exception as _qe:
            log.warning(f"Queue xato: {_qe}")

    if not title:
        title = "World news today 2026"
        desc  = "Top stories from around the world"

    # Format 1
    if args.fakt or args.all:
        for lang in langs:
            make_fakt_short(title, desc, lang)

    # Format 2
    if args.breaking or args.all:
        for lang in langs:
            make_breaking_short(title, desc, lang)

    # Format 3 — queue dan 5 ta maqola
    if args.top5 or args.all:
        try:
            import glob as _gl2
            queue_dir = _HERE.parent / "YOUTUBE" / "queue"
            files5    = sorted(_gl2.glob(str(queue_dir / "*.json")), reverse=True)[:5]
            arts5 = []
            for _f in files5:
                with open(_f, encoding="utf-8") as _ff:
                    _qd = json.load(_ff)
                arts5.append(_qd.get("article", {}))
        except Exception:
            arts5 = [{"title": title, "description": desc}]
        for lang in langs:
            make_top5_short(arts5, lang)

    # Format 4
    if args.numbers or args.all:
        for lang in langs:
            make_numbers_short(title, desc, lang)

    # Format 6
    if args.history or args.all:
        for lang in langs:
            make_history_short(lang)
