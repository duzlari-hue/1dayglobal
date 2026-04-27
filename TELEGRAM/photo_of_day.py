"""photo_of_day.py — Kunning eng yaxshi fotosi (Telegram + YouTube)

Har kuni soat 12:00 da:
  • Pexels Curated API dan trending foto olish
  • 3 tilda (UZ/RU/EN) Telegram kanallariga yuborish
  • YouTube uchun 30s vertical Short yasash
"""
import os
import random
import logging
import pathlib
import tempfile
import requests
import subprocess
from datetime import datetime

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=True)

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHANNEL_UZ,
    TELEGRAM_CHANNEL_RU,
    TELEGRAM_CHANNEL_EN,
    TASHKENT,
)
from translator import _ask_anthropic, ANTHROPIC_API_KEY, GEMINI_API_KEY

log = logging.getLogger(__name__)

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

# ── Sarlavhalar har kuni uchun variatsiyalar ──────────────────
_TITLES_UZ = [
    "📸 KUNNING ENG YAXSHI FOTOSI",
    "🌍 BUGUNGI KUN TASVIRI",
    "📷 KUNNING RASM-LAHZASI",
    "🖼️ BUGUN DUNYODA",
]
_TITLES_RU = [
    "📸 ЛУЧШЕЕ ФОТО ДНЯ",
    "🌍 СНИМОК СЕГОДНЯШНЕГО ДНЯ",
    "📷 МОМЕНТ ДНЯ",
    "🖼️ МИР СЕГОДНЯ",
]
_TITLES_EN = [
    "📸 PHOTO OF THE DAY",
    "🌍 TODAY'S BEST SHOT",
    "📷 MOMENT OF THE DAY",
    "🖼️ THE WORLD TODAY",
]


def _fetch_pexels_curated(out_path: str) -> dict | None:
    """Pexels Curated API — trending landshaft/hujjatli foto."""
    if not PEXELS_API_KEY:
        return None
    try:
        # Curated — trending photos
        r = requests.get(
            "https://api.pexels.com/v1/curated?per_page=30",
            headers={"Authorization": PEXELS_API_KEY},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        photos = r.json().get("photos", [])
        # Eng yaxshi sifatli rasm tanlash (landscape)
        landscape = [
            p for p in photos
            if p.get("width", 0) > p.get("height", 1)
        ]
        candidates = landscape or photos
        random.shuffle(candidates)

        for ph in candidates:
            src = ph.get("src", {})
            img_url = src.get("large2x") or src.get("large") or src.get("original", "")
            if not img_url:
                continue
            ir = requests.get(img_url, timeout=20)
            if ir.status_code == 200 and len(ir.content) >= 30_000:
                with open(out_path, "wb") as f:
                    f.write(ir.content)
                return {
                    "id":           ph.get("id"),
                    "photographer": ph.get("photographer", ""),
                    "url":          ph.get("url", ""),
                    "alt":          ph.get("alt", ""),
                    "width":        ph.get("width", 0),
                    "height":       ph.get("height", 0),
                }
    except Exception as e:
        log.warning(f"Pexels curated xato: {e}")
    return None


def _fetch_pexels_topic(topic: str, out_path: str) -> dict | None:
    """Berilgan mavzuda Pexels dan sifatli foto."""
    if not PEXELS_API_KEY:
        return None
    try:
        r = requests.get(
            f"https://api.pexels.com/v1/search?query={requests.utils.quote(topic)}&per_page=20&orientation=landscape",
            headers={"Authorization": PEXELS_API_KEY},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        photos = r.json().get("photos", [])
        random.shuffle(photos)
        for ph in photos:
            src = ph.get("src", {})
            img_url = src.get("large2x") or src.get("large", "")
            if not img_url:
                continue
            ir = requests.get(img_url, timeout=20)
            if ir.status_code == 200 and len(ir.content) >= 30_000:
                with open(out_path, "wb") as f:
                    f.write(ir.content)
                return {
                    "id":           ph.get("id"),
                    "photographer": ph.get("photographer", ""),
                    "url":          ph.get("url", ""),
                    "alt":          ph.get("alt", ""),
                    "width":        ph.get("width", 0),
                    "height":       ph.get("height", 0),
                }
    except Exception as e:
        log.warning(f"Pexels topic '{topic}' xato: {e}")
    return None


# ── AI bilan foto tavsifini tarjima qilish ───────────────────
def _translate_photo_caption(alt: str, photographer: str) -> dict:
    """Foto tavsifi va muallifi 3 tilda. Alt bo'sh/qisqa bo'lsa — fallback."""
    # Alt juda qisqa yoki bo'sh bo'lsa — AI ga so'ramasdan generik javob (galyutsinatsiyaga yo'l qo'ymaslik)
    if not alt or len(alt.strip()) < 15:
        return {
            "caption_uz":  f"Дунё ҳаётидан гўзал лаҳза. Ҳар бир кадр — ўзига хос ҳикоя.",
            "caption_ru":  f"Прекрасный момент из жизни мира. Каждый кадр — своя история.",
            "caption_en":  f"A beautiful moment from around the world. Each frame tells its own story.",
            "hashtag_uz":  "#Фото #Дунё #1Кун",
            "hashtag_ru":  "#Фото #Мир #1День",
            "hashtag_en":  "#PhotoOfTheDay #World #1Day",
        }

    prompt = f"""Photo alt text: "{alt}"
Photographer: {photographer}

Translate and write a short poetic caption for this photo in 3 languages.
Return ONLY valid JSON (no markdown):
{{
  "caption_uz": "2-3 ta qisqa jumlada o'zbek tilida tavsif (FAQAT KIRILL: а,б,в...)",
  "caption_ru": "2-3 предложения по-русски",
  "caption_en": "2-3 sentences in English",
  "hashtag_uz": "#Foto #Dunyo #1Kun",
  "hashtag_ru": "#Фото #Мир #1День",
  "hashtag_en": "#PhotoOfTheDay #World #1Day"
}}
CRITICAL: caption_uz MUST be in Uzbek Cyrillic script only."""

    try:
        import json
        if ANTHROPIC_API_KEY:
            raw = _ask_anthropic(prompt, max_tokens=600)
            if "```" in raw:
                import re
                raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
            return json.loads(raw)
    except Exception as e:
        log.warning(f"Foto tarjima xato: {e}")

    # Fallback
    return {
        "caption_uz":  f"Фотограф: {photographer}. Дунё лаҳзалари.",
        "caption_ru":  f"Фотограф: {photographer}. Моменты нашего мира.",
        "caption_en":  f"Photographer: {photographer}. Moments of our world.",
        "hashtag_uz":  "#Фото #Дунё #1Кун",
        "hashtag_ru":  "#Фото #Мир #1День",
        "hashtag_en":  "#PhotoOfTheDay #World #1Day",
    }


def send_photo_post(photo_path: str, caption: str, channel: str) -> bool:
    """Rasmli post yuborish."""
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    try:
        with open(photo_path, "rb") as f:
            r = requests.post(
                f"{base}/sendPhoto",
                data={"chat_id": channel, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"photo": f},
                timeout=30,
            )
        ok = r.json().get("ok", False)
        if not ok:
            log.warning(f"sendPhoto xato ({channel}): {r.json().get('description','')}")
        return ok
    except Exception as e:
        log.warning(f"sendPhoto exception ({channel}): {e}")
        return False


def _make_photo_caption(title: str, cap: str, hashtags: str,
                         photographer: str, channel: str, lang: str) -> str:
    """Telegram caption yasash."""
    vaqt  = datetime.now(TASHKENT).strftime("🕐 %H:%M | %d.%m.%Y")
    text  = f"<b>{title}</b>\n\n"
    text += f"{cap}\n\n"
    if photographer:
        credit = {"uz": "📷 Fotograf", "ru": "📷 Фотограф", "en": "📷 Photo by"}.get(lang, "📷")
        text += f"{credit}: {photographer}\n"
    text += f"{vaqt}\n"
    text += f"📰 {channel}\n\n"
    text += hashtags
    return text


def _make_photo_short_youtube(photo_path: str, lang: str,
                               caption_text: str, out_path: str) -> bool:
    """30s vertical YouTube Short rasm + overlay matn + musiqa."""
    try:
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "YOUTUBE"))
        from digest_maker import _make_tts, _audio_dur

        # TTS uchun qisqa matn (birinchi jumla)
        tts_text = caption_text[:200].split(".")[0] + "."
        tmp      = pathlib.Path(tempfile.gettempdir())
        tts_path = str(tmp / f"pod_tts_{lang}.mp3")

        tts_ok  = _make_tts(tts_text, lang, "xabar", tts_path)
        tts_dur = _audio_dur(tts_path) if tts_ok else 0
        dur     = max(tts_dur + 2.0, 8.0) if tts_dur else 30.0

        # Musiqa fayl (YOUTUBE papkasidan)
        yt_dir   = pathlib.Path(__file__).parent.parent / "YOUTUBE"
        musiqalar = list(yt_dir.glob("music*.mp3")) + list(yt_dir.glob("bg*.mp3"))
        musiqa   = str(random.choice(musiqalar)) if musiqalar else None

        # ffmpeg: rasm → vertical short (1080x1920)
        ff = ["ffmpeg", "-y", "-loop", "1", "-i", photo_path]
        if musiqa:
            ff += ["-i", musiqa]
        if tts_ok:
            ff += ["-i", tts_path]

        # Video filtr: scale + pad (vertical 9:16)
        vf = ("scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,"
              "format=yuv420p")

        n_audio = (1 if musiqa else 0) + (1 if tts_ok else 0)
        if n_audio == 2:
            # musiqa + TTS mix
            ff += [
                "-vf", vf,
                "-filter_complex",
                (f"[1:a]volume=0.15[mus];"
                 f"[2:a]volume=1.0[tts];"
                 f"[mus][tts]amix=inputs=2:duration=first[aout]"),
                "-map", "0:v", "-map", "[aout]",
                "-t", str(dur),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                out_path,
            ]
        elif n_audio == 1:
            ff += [
                "-vf", vf,
                "-map", "0:v", "-map", "1:a",
                "-t", str(dur),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                out_path,
            ]
        else:
            ff += [
                "-vf", vf,
                "-t", "30",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-an", out_path,
            ]

        result = subprocess.run(ff, capture_output=True, timeout=120)
        return result.returncode == 0 and pathlib.Path(out_path).exists()
    except Exception as e:
        log.warning(f"Photo short video xato: {e}")
        return False


def run_photo_of_day(force: bool = False):
    """Kunning eng yaxshi fotosi — Telegram + YouTube.

    Kuniga FAQAT 1 marta yuboradi (output/photo_of_day_lock.txt).
    force=True bo'lsa lock e'tiborga olinmaydi.
    """
    log.info("📸 Kunning fotosi pipeline boshlanmoqda...")

    # ── Kunlik lock: bugun allaqachon yuborilganmi? ─────────────
    out_dir   = pathlib.Path(__file__).parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    lock_file = out_dir / "photo_of_day_lock.txt"
    today_str = datetime.now(TASHKENT).strftime("%Y-%m-%d")
    if not force and lock_file.exists():
        try:
            last = lock_file.read_text(encoding="utf-8").strip()
            if last == today_str:
                log.info(f"📸 Bugun ({today_str}) allaqachon yuborilgan — o'tkazildi")
                return
        except Exception:
            pass

    tmp     = pathlib.Path(tempfile.gettempdir())
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    img_raw = str(tmp / f"pod_{ts}.jpg")

    # 1. Pexels dan trending foto
    today_topics = [
        "world news today", "global events", "nature landscape",
        "city people", "documentary photo", "news photography",
    ]
    meta = _fetch_pexels_curated(img_raw)
    if not meta:
        for topic in today_topics:
            meta = _fetch_pexels_topic(topic, img_raw)
            if meta:
                break

    if not meta:
        log.warning("📸 Foto topilmadi — photo of day o'tkazildi")
        return

    log.info(f"📸 Foto topildi: {meta.get('alt','')[:50]} (by {meta.get('photographer','')})")

    # 2. Tarjima / tavsif
    captions = _translate_photo_caption(meta.get("alt", ""), meta.get("photographer", ""))

    # 3. Telegram — 3 kanalga
    langs = [
        ("uz", TELEGRAM_CHANNEL_UZ, _TITLES_UZ, "caption_uz", "hashtag_uz"),
        ("ru", TELEGRAM_CHANNEL_RU, _TITLES_RU, "caption_ru", "hashtag_ru"),
        ("en", TELEGRAM_CHANNEL_EN, _TITLES_EN, "caption_en", "hashtag_en"),
    ]
    for lang, channel, titles, cap_key, ht_key in langs:
        title    = random.choice(titles)
        cap_text = captions.get(cap_key, "")
        hashtags = captions.get(ht_key, "#Photo #1Day")
        post_cap = _make_photo_caption(
            title, cap_text, hashtags,
            meta.get("photographer", ""), channel, lang
        )
        if send_photo_post(img_raw, post_cap, channel):
            log.info(f"  ✅ Photo of Day [{lang.upper()}] → {channel}")
        else:
            log.warning(f"  ⚠️  Photo of Day [{lang.upper()}] → {channel} MUVAFFAQIYATSIZ")

    # 4. YouTube Short — EN tilda
    try:
        yt_out_dir = pathlib.Path(__file__).parent.parent / "YOUTUBE" / "output" / "videos"
        yt_out_dir.mkdir(parents=True, exist_ok=True)
        short_path = str(yt_out_dir / f"{ts}_photo_of_day_en.mp4")
        cap_en     = captions.get("caption_en", "")

        if _make_photo_short_youtube(img_raw, "en", cap_en, short_path):
            log.info(f"  ✅ YouTube Short tayyor: {short_path}")
            # YouTube ga yuklash (YOUTUBE app.py da upload_to_youtube bor)
            try:
                sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "YOUTUBE"))
                import app as yt_app
                title_en = random.choice(_TITLES_EN) + f" | {datetime.now(TASHKENT).strftime('%B %d, %Y')}"
                desc_en  = f"{cap_en}\n\nPhotographer: {meta.get('photographer','')}\n#PhotoOfTheDay #World"
                upload_ok = yt_app.upload_to_youtube(
                    video_path=short_path,
                    title=title_en,
                    description=desc_en,
                    tags=["photo of the day", "world", "photography", "1day"],
                    lang="en",
                )
                if upload_ok:
                    log.info("  ✅ YouTube Short yuklandi")
            except Exception as e:
                log.warning(f"  YouTube upload xato: {e}")
        else:
            log.warning("  ⚠️  YouTube Short yaratilmadi")
    except Exception as e:
        log.warning(f"  YouTube short pipeline xato: {e}")

    # Vaqtinchalik fayl
    try:
        os.remove(img_raw)
    except Exception:
        pass

    # Kunlik lock yozish — bugun qayta yuborilmasin
    try:
        lock_file.write_text(today_str, encoding="utf-8")
    except Exception as e:
        log.debug(f"Lock yozish xato: {e}")

    log.info("✅ Kunning fotosi pipeline tugadi")
