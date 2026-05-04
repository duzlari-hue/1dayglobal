"""upload_pending.py — output/videos/ dagi yuklnmagan videolarni YouTube ga yuklash.

STRATEGIYA:
  · Har video bir marta yuklanadi (uploaded.json da tracking)
  · Kvota samaradorligi: 10,000 unit/kun, upload = 1,600 unit → max 6/kun
  · Prioritet: daily_shorts > digest > short (analysis alohida)
  · Kvota tugasa — darhol to'xtatiladi
  · --all: barcha (bugungi emas) videoları ham yuklash
  · --dry: faqat ro'yxat, yuklamaslik

Foydalanish:
    python upload_pending.py           # bugungi yuklnmaganlarni yuklash
    python upload_pending.py --all     # barcha yuklnmaganlarni (kun bo'yi)
    python upload_pending.py --dry     # ro'yxat, yuklamaslik
    python upload_pending.py --reset   # uploaded.json ni tozalash
"""
import os, sys, re, glob, json, pathlib, logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

ROOT        = pathlib.Path(__file__).parent
VIDEOS_DIR  = ROOT / "output" / "videos"
TRACK_FILE  = ROOT / "output" / "uploaded.json"
QUEUE_DIR   = ROOT / "queue"

TODAY       = datetime.now().strftime("%Y%m%d")
DRY_RUN     = "--dry"   in sys.argv
ALL_DATES   = "--all"   in sys.argv
RESET       = "--reset" in sys.argv

# YouTube Data API standart: 10,000 unit/kun, lekin kvota oshirilgan yoki
# kanalda cheklov yo'q bo'lsa — limitni oshirish mumkin.
# .env da YT_MAX_UPLOADS=50 deb yozing — yoki bu yerda o'zgartiring.
MAX_UPLOADS = int(os.getenv("YT_MAX_UPLOADS", "50"))

# ─── Prioritet tartibi ─────────────────────────────────────────
# Yuqoriroq raqam = muhimroq (birinchi yuklanadi)
PRIORITY = {
    "daily_shorts": 100,   # Daily Shorts
    "numbers":       90,   # Raqamlarda Dunyo
    "history":       88,   # Bugun Tarixda
    "breaking":      85,   # Breaking 60 Sec
    "fakt":          80,   # 1 Fakt
    "top5":          78,   # Top-5 Tezkor
    "digest":        60,   # Digest video
    "analysis":      40,   # Tahlil video
    "short":         10,   # Short clip (eng past)
}


# ─── Uploaded tracker ──────────────────────────────────────────
def _load_uploaded() -> set:
    if TRACK_FILE.exists():
        try:
            return set(json.loads(TRACK_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def _save_uploaded(uploaded: set):
    TRACK_FILE.parent.mkdir(exist_ok=True)
    TRACK_FILE.write_text(
        json.dumps(sorted(uploaded), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─── Fayl ma'lumotlari ─────────────────────────────────────────
def _detect_lang(filename: str) -> str:
    fn = filename.lower()
    for suffix in ("_uz.mp4", "_uz.", "daily_shorts_uz", "_digest_uz", "_short_uz",
                   "_numbers_uz", "_history_uz", "_breaking_uz", "_fakt_uz", "_top5_uz"):
        if suffix in fn:
            return "uz"
    for suffix in ("_ru.mp4", "_ru.", "daily_shorts_ru", "_digest_ru", "_short_ru",
                   "_numbers_ru", "_history_ru", "_breaking_ru", "_fakt_ru", "_top5_ru"):
        if suffix in fn:
            return "ru"
    return "en"


def _detect_type(filename: str) -> str:
    fn = filename.lower()
    for t in PRIORITY:
        if t in fn:
            return t
    return "other"


def _make_title(filename: str, lang: str, vtype: str) -> str:
    today = datetime.now().strftime("%d.%m.%Y")
    ts_part = re.sub(r'^\d{8}_\d{4,9}_?', '', pathlib.Path(filename).stem)

    type_titles = {
        "uz": {
            "daily_shorts": f"Bugungi 5 ta eng muhim yangilik | {today} #Shorts",
            "numbers":      f"Raqamlarda Dunyo | {today} #Shorts",
            "history":      f"Bugun Tarixda | {today} #Shorts",
            "breaking":     f"Tezkor Xabar | {today} #Shorts",
            "fakt":         f"1 Fakt | {today} #Shorts",
            "top5":         f"Top 5 Tezkor | {today} #Shorts",
            "digest":       f"Yangiliklar Dayjesti | {today}",
            "analysis":     f"Dunyo Tahlili | {today}",
            "short":        f"Yangilik Klip | {today} #Shorts",
        },
        "ru": {
            "daily_shorts": f"5 главных новостей дня | {today} #Shorts",
            "numbers":      f"Мир в Цифрах | {today} #Shorts",
            "history":      f"В Этот День | {today} #Shorts",
            "breaking":     f"Срочно | {today} #Shorts",
            "fakt":         f"1 Факт | {today} #Shorts",
            "top5":         f"Топ 5 Срочно | {today} #Shorts",
            "digest":       f"Дайджест новостей | {today}",
            "analysis":     f"Анализ событий | {today}",
            "short":        f"Новостной клип | {today} #Shorts",
        },
        "en": {
            "daily_shorts": f"Today's Top 5 News | {today} #Shorts",
            "numbers":      f"World in Numbers | {today} #Shorts",
            "history":      f"On This Day | {today} #Shorts",
            "breaking":     f"Breaking News | {today} #Shorts",
            "fakt":         f"1 Fact | {today} #Shorts",
            "top5":         f"Top 5 Fast | {today} #Shorts",
            "digest":       f"News Digest | {today}",
            "analysis":     f"World Analysis | {today}",
            "short":        f"News Clip | {today} #Shorts",
        },
    }
    lang_map = type_titles.get(lang, type_titles["en"])
    return lang_map.get(vtype, f"1DAY GLOBAL | {today}")[:100]


def _make_desc(lang: str, vtype: str, extra_content: str = "") -> str:
    today = datetime.now().strftime("%d.%m.%Y")

    yt_channels = {
        "uz": "https://www.youtube.com/@1kunnews",
        "en": "https://www.youtube.com/@1daykun",
        "ru": "https://www.youtube.com/@1dennews",
    }
    yt_channel = yt_channels.get(lang, yt_channels["en"])

    tg_links   = {
        "uz": "https://t.me/birkunday",
        "ru": "https://t.me/birkunday_ru",
        "en": "https://t.me/birkunday_en",
    }

    # ── Asosiy tanlov ───────────────────────────────────────────��─
    channel_intro = {
        "uz": "1DAY GLOBAL — Dunyo yangiliklari o'zbek tilida",
        "ru": "1DAY GLOBAL — Мировые новости на русском языке",
        "en": "1DAY GLOBAL — World news in English",
    }.get(lang, "1DAY GLOBAL — World News")

    type_line = {
        "uz": {
            "daily_shorts": "Bugungi eng muhim 5 ta yangilik qisqacha.",
            "numbers":      "Dunyo yangiliklari raqamlarda.",
            "history":      "Bugun tarixda nimalar bo'lgan?",
            "breaking":     "So'nggi 60 soniya ichidagi tezkor xabarlar.",
            "fakt":         "Dunyo haqida bitta hayratlanarli fakt.",
            "top5":         "Eng tezkor 5 ta yangilik.",
            "digest":       "Bugungi yangiliklar dayjesti — batafsil tahlil.",
            "short":        "Qisqacha yangilik.",
        },
        "ru": {
            "daily_shorts": "5 главных новостей дня в кратком формате.",
            "numbers":      "Мировые новости в цифрах.",
            "history":      "Что произошло в этот день в истории?",
            "breaking":     "Срочные новости за последние 60 секунд.",
            "fakt":         "Один удивительны�� факт о нашем мире.",
            "top5":         "Топ 5 самых важных новостей.",
            "digest":       "Дайджест новостей дня — подробный обзор.",
            "short":        "Краткий новостной сюжет.",
        },
        "en": {
            "daily_shorts": "Today's top 5 world news in brief.",
            "numbers":      "World news told through numbers and statistics.",
            "history":      "What happened on this day in history?",
            "breaking":     "Breaking news in the last 60 seconds.",
            "fakt":         "One amazing fact about our world.",
            "top5":         "Top 5 fastest news stories.",
            "digest":       "Today's news digest — detailed roundup.",
            "short":        "Quick news clip.",
        },
    }.get(lang, {}).get(vtype, "")

    # ── Hashtag to'plami ──────────────────────────────────────────
    base_tags = {
        "uz": "#Yangiliklar #Dunyo #1KUN #UzbekNews #O'zbekcha",
        "ru": "#Новости #Мир #1День #Новости2026 #РусскиеНовости",
        "en": "#News #WorldNews #1Day #BreakingNews #2026",
    }.get(lang, "#News #World #1Day")

    type_tags = {
        "daily_shorts": "#DailyShorts #Top5 #Shorts",
        "numbers":      "#WorldInNumbers #Statistics #Facts #Shorts",
        "history":      "#OnThisDay #History #HistoricalFacts #Shorts",
        "breaking":     "#Breaking #BreakingNews #Urgent #Shorts",
        "fakt":         "#Facts #DidYouKnow #Amazing #Shorts",
        "top5":         "#Top5 #TopNews #Shorts",
        "digest":       "#NewsDigest #Roundup #Summary",
        "analysis":     "#Analysis #Explained #DeepDive",
        "short":        "#Shorts #QuickNews",
    }.get(vtype, "#Shorts")

    all_hashtags = f"{base_tags} {type_tags}"

    # ── Qurish ────────────────────────────────────────────────────
    lines = [
        f"{channel_intro} | {today}",
        "",
    ]
    if type_line:
        lines += [type_line, ""]

    if extra_content:
        lines += [extra_content.strip()[:1000], ""]

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        f"📺 YouTube: {yt_channel}",
        f"📱 Telegram: {tg_links.get(lang, tg_links['en'])}",
        "",
        all_hashtags,
    ]

    return "\n".join(lines)[:4900]


def _make_tags(lang: str, vtype: str) -> list:
    base = {
        "uz": ["Yangiliklar", "Dunyo", "1KUN", "UzbekNews", "BreakingNews", "2026", "Shorts"],
        "ru": ["Новости", "Мир", "1День", "RussianNews", "BreakingNews", "2026", "Shorts"],
        "en": ["News", "World", "1Day", "WorldNews", "BreakingNews", "2026", "Shorts"],
    }.get(lang, ["News", "World", "Shorts"])

    extra = {
        "daily_shorts": ["DailyShorts", "Top5", "TopNews"],
        "numbers":      ["WorldInNumbers", "Facts", "Statistics"],
        "history":      ["OnThisDay", "History", "HistoricalFacts"],
        "breaking":     ["BreakingNews", "Urgent", "LiveNews"],
        "fakt":         ["Facts", "DidYouKnow", "Interesting"],
        "top5":         ["Top5", "TopNews", "BestOf"],
        "digest":       ["NewsDigest", "Roundup", "Summary"],
        "analysis":     ["Analysis", "Explained", "DeepDive"],
    }.get(vtype, [])

    return (base + extra)[:15]


# ── Per-til token fayllari ──────────────────────────────────────
_TOKEN_FILES = {
    "uz": "youtube_token_uz.json",
    "en": "youtube_token_en.json",
    "ru": "youtube_token_ru.json",
}
_YT_CHANNELS = {
    "uz": "@1kunnews",
    "en": "@1daykun",
    "ru": "@1dennews",
}
_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def _get_yt_client(lang: str):
    """Til bo'yicha to'g'ri YouTube kanal tokenini yuklaydi.
    Qaytaradi: (youtube_client, None) yoki (None, xato_xabar)
    """
    try:
        from googleapiclient.http import MediaFileUpload   # noqa: F401 (import tekshiruvi)
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as e:
        return None, f"Import xato: {e}"

    token_fname = _TOKEN_FILES.get(lang, f"youtube_token_{lang}.json")
    token_path  = str(ROOT / token_fname)
    secrets_path = str(ROOT / "client_secrets.json")

    # Eskirgan umumiy token mavjud bo'lsa — UZ ga nusxalash (birinchi marta)
    old_token = str(ROOT / "youtube_token.json")
    if lang == "uz" and not os.path.exists(token_path) and os.path.exists(old_token):
        import shutil
        shutil.copy(old_token, token_path)
        log.info(f"  📋 Eskirgan youtube_token.json → {token_fname} ga nusxalandi")

    if not os.path.exists(token_path):
        channel = _YT_CHANNELS.get(lang, lang.upper())
        return None, (
            f"❌ {channel} ({lang.upper()}) uchun token yo'q!\n"
            f"   Avval autentifikatsiya qiling:\n"
            f"   py -3 youtube_maker.py --auth {lang}"
        )

    creds = None
    try:
        creds = Credentials.from_authorized_user_file(token_path, _SCOPES)
    except Exception:
        creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(token_path, "w") as tf:
                    tf.write(creds.to_json())
                log.info(f"  🔑 [{lang.upper()}] Token yangilandi")
            except Exception as e:
                channel = _YT_CHANNELS.get(lang, lang.upper())
                return None, (
                    f"Token yangilash xato ({channel}): {e}\n"
                    f"   py -3 youtube_maker.py --auth {lang}"
                )
        else:
            channel = _YT_CHANNELS.get(lang, lang.upper())
            return None, (
                f"❌ [{lang.upper()}] Token yaroqsiz — qayta autentifikatsiya kerak:\n"
                f"   py -3 youtube_maker.py --auth {lang}"
            )

    try:
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        return yt, None
    except Exception as e:
        return None, f"YouTube build xato: {e}"


# ─── Upload ────────────────────────────────────────────────────
def upload_video(video_path: str, lang: str, vtype: str) -> str | None:
    """Bitta videoni tilga mos YouTube kanalga yuklash. Vid_id yoki None qaytaradi."""
    from googleapiclient.http import MediaFileUpload

    yt, err = _get_yt_client(lang)
    if yt is None:
        log.error(f"  {err}")
        return None

    fname = pathlib.Path(video_path).name
    title = _make_title(fname, lang, vtype)
    desc  = _make_desc(lang, vtype)
    tags  = _make_tags(lang, vtype)

    # Shorts flag: 60 sek dan kalta yoki nom ichida "short"/"shorts" bo'lsa
    is_short = vtype in ("daily_shorts", "short", "numbers", "history",
                         "breaking", "fakt", "top5")

    body = {
        "snippet": {
            "title":           title,
            "description":     desc,
            "tags":            tags,
            "categoryId":      "25",
            "defaultLanguage": lang if lang != "uz" else "uz",
        },
        "status": {
            "privacyStatus":           "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    size_mb  = pathlib.Path(video_path).stat().st_size / 1024 / 1024
    ch_label = _YT_CHANNELS.get(lang, lang.upper())
    log.info(f"  ⬆️  [{lang.upper()}] [{vtype}] → {ch_label} | {fname} ({size_mb:.1f} MB)")
    log.info(f"     '{title}'")

    try:
        media = MediaFileUpload(
            video_path, mimetype="video/mp4",
            resumable=True, chunksize=5 * 1024 * 1024
        )
        req  = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                log.info(f"     {int(status.progress() * 100)}%...")
        vid_id = resp.get("id", "")
        log.info(f"  ✅ https://youtu.be/{vid_id}")
        return vid_id
    except Exception as e:
        err = str(e)
        if "quotaExceeded" in err or "quota" in err.lower():
            log.error("  ❌ YouTube KVOTA TUGAGAN! Soat 00:00 UTC da yangilanadi.")
            raise QuotaError()
        log.error(f"  ❌ Yuklash xato: {err[:200]}")
        return None


class QuotaError(Exception):
    pass


# ─── Smart selection ───────────────────────────────────────────
def _select_files(all_mp4: list, uploaded: set, today_only: bool) -> list:
    """
    Yuklanmagan fayllarni prioritet bo'yicha tanlash.
    today_only=True: faqat bugungi sana prefiksi
    """
    candidates = []
    for f in all_mp4:
        fname = pathlib.Path(f).name
        if fname in uploaded:
            continue
        if today_only and not fname.startswith(TODAY):
            continue
        vtype = _detect_type(fname)
        if vtype == "other":
            continue   # noma'lum tur — o'tkazib yuborish
        prio  = PRIORITY.get(vtype, 0)
        candidates.append((prio, fname, f, vtype))

    # Prioritet bo'yicha saralash (eng yuqori birinchi)
    candidates.sort(key=lambda x: (-x[0], x[1]))

    # Bir xil tur+til ni dedup — faqat eng yangisini olish
    seen_type_lang = {}
    selected = []
    for prio, fname, fpath, vtype in candidates:
        lang  = _detect_lang(fname)
        key   = f"{vtype}_{lang}"
        # Agar bu tur+til allaqachon tanlangan bo'lsa — eng yangisini saqla
        if key in seen_type_lang:
            # Yanginisi (timestamp bo'yicha katta = keyingi) olindi
            old_fname = seen_type_lang[key][1]
            if fname > old_fname:
                # Eskisini olib, yangisini qo'yish
                selected = [(p, fn, fp, vt) for p, fn, fp, vt in selected
                            if fn != old_fname]
                selected.append((prio, fname, fpath, vtype))
                seen_type_lang[key] = (prio, fname, fpath, vtype)
        else:
            seen_type_lang[key] = (prio, fname, fpath, vtype)
            selected.append((prio, fname, fpath, vtype))

    # Qayta prioritet bo'yicha saralash
    selected.sort(key=lambda x: (-x[0], x[1]))
    return selected


def main():
    if RESET:
        if TRACK_FILE.exists():
            TRACK_FILE.unlink()
            log.info("uploaded.json tozalandi.")
        return

    all_mp4    = sorted(glob.glob(str(VIDEOS_DIR / "*.mp4")))
    uploaded   = _load_uploaded()
    today_only = not ALL_DATES

    if not all_mp4:
        log.info("output/videos/ bo'sh.")
        return

    selected = _select_files(all_mp4, uploaded, today_only)

    if not selected:
        scope = "bugun" if today_only else "barcha"
        log.info(f"Yuklanmagan yangi video topilmadi ({scope}).")
        already = sum(1 for f in all_mp4 if pathlib.Path(f).name in uploaded)
        log.info(f"Allaqachon yuklangan: {already} ta | Jami: {len(all_mp4)} ta")
        return

    log.info(f"Yuklanishi kerak: {len(selected)} ta video "
             f"(max {MAX_UPLOADS} ta yuklanadi)")
    log.info("─" * 60)
    for i, (prio, fname, fpath, vtype) in enumerate(selected, 1):
        lang = _detect_lang(fname)
        size = pathlib.Path(fpath).stat().st_size / 1024 / 1024
        mark = "→ YUKLANADI" if i <= MAX_UPLOADS else "  (keyingacha)"
        log.info(f"  {i:>2}. [{lang.upper()}] [{vtype}] {fname[:50]} "
                 f"({size:.1f} MB) {mark}")
    log.info("─" * 60)

    if DRY_RUN:
        log.info("--dry rejimi: yuklanmadi.")
        return

    ok = failed = skipped = 0
    for prio, fname, fpath, vtype in selected[:MAX_UPLOADS]:
        lang = _detect_lang(fname)
        try:
            vid_id = upload_video(fpath, lang, vtype)
            if vid_id:
                uploaded.add(fname)
                _save_uploaded(uploaded)
                ok += 1
            else:
                failed += 1
        except QuotaError:
            log.warning(f"  ⏸️  Kvota tugadi. {ok} ta yuklandi, {len(selected)-ok} ta qoldi.")
            log.warning("  Keyingi soatda yoki ertaga qayta ishga tushiring.")
            break

    skipped = max(0, len(selected) - MAX_UPLOADS)
    log.info("=" * 60)
    log.info(f"✅ Yuklandi: {ok} ta | ❌ Xato: {failed} ta | ⏭️  Keyinga: {skipped} ta")
    if skipped > 0:
        log.info(f"   Qolgan {skipped} ta video uchun: python upload_pending.py --all")


if __name__ == "__main__":
    main()
