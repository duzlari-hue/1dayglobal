import os
import sys
import asyncio
import re
import subprocess
import json as _json
import requests
import textwrap
import feedparser
from datetime import datetime, timedelta

# Windows terminalda UTF-8 (Kirill, o'zbek harflari uchun)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from moviepy import AudioFileClip
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import edge_tts

load_dotenv(".env")

from config import (
    CLIENT_SECRETS, TOKEN_FILE, SCOPES,
    VIDEO_W, VIDEO_H, FPS, OUTPUT_DIR, TEMP_DIR, VOICES, AUDIO_FX
)

C_BG    = (5,  10, 22)
C_GOLD  = (240,165,  0)
C_RED   = (204,  0,  0)
C_WHITE = (255,255,255)
C_GRAY  = (175,180,190)
C_BLUE  = (  0, 85,170)
C_YELLOW= (255,210,  0)


def get_fonts():
    try:
        return {
            "brand":  ImageFont.truetype("arialbd.ttf", 26),
            "title":  ImageFont.truetype("arialbd.ttf", 44),
            "small":  ImageFont.truetype("arial.ttf",   19),
            "ticker": ImageFont.truetype("arialbd.ttf", 22),
            "break":  ImageFont.truetype("arialbd.ttf", 19),
            "xl":     ImageFont.truetype("arialbd.ttf", 86),
            "lg":     ImageFont.truetype("arialbd.ttf", 54),
            "time":   ImageFont.truetype("arialbd.ttf", 30),
        }
    except Exception:
        f = ImageFont.load_default()
        return {k: f for k in ["brand","title","small","ticker","break","xl","lg","time"]}


def tw(draw, text, font):
    bbox = draw.textbbox((0,0), text, font=font)
    return bbox[2] - bbox[0]


# ── Bing rasmlar ──────────────────────────────────────────────
# Bloklangan domenlar
BLOCKED_DOMAINS = [
    "bbc.co.uk", "bbc.com", "reuters.com", "nytimes.com",
    "nationalinterest.org", "rfi.fr", "apnews.com",
    "washingtonpost.com", "theguardian.com"
]


# ── Maqoladan og:image olish ─────────────────────────────────
def fetch_article_images(article_url, count=4):
    """Maqola URLidan og:image va boshqa rasmlarni yuklab olish"""
    paths = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
    }
    try:
        r = requests.get(article_url, headers=headers, timeout=8)
        if not r.ok:
            return paths
        html = r.text[:50000]

        # og:image (asosiy rasm)
        og = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
        og += re.findall(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
        og += re.findall(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', html)

        # Maqola ichidagi rasmlar
        srcset = re.findall(r'srcset=["\']([^"\']+)["\']', html)
        for s in srcset:
            urls_in_set = re.findall(r'(https://[^\s,]+)', s)
            og.extend(urls_in_set)

        src_imgs = re.findall(r'src=["\']([^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)["\'][^>]*>', html)
        og.extend([u for u in src_imgs if u.startswith("http")])

        seen = set()
        for i, url in enumerate(og[:20]):
            if len(paths) >= count:
                break
            url = url.strip().split(" ")[0]  # srcset da bo'sh joy bo'lishi mumkin
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            try:
                ir = requests.get(url, timeout=6, headers=headers)
                ct = ir.headers.get("content-type", "")
                if ir.ok and len(ir.content) > 15000 and "image" in ct:
                    path = f"{TEMP_DIR}/article_{i}.jpg"
                    with open(path, "wb") as f:
                        f.write(ir.content)
                    # Tekshirish
                    img = Image.open(path)
                    if img.size[0] >= 400:
                        paths.append(path)
                        print(f"     Maqola rasmi {len(paths)}: {img.size} — {url[:50]}")
            except Exception:
                continue
    except Exception as e:
        print(f"  Maqola rasmi xato: {e}")
    return paths

SKIP_DOMAINS = ['workday','linkedin','facebook','twitter',
                'instagram','tiktok','logo','icon','avatar','sprite']

def _try_download(url, headers):
    """Rasmni yuklab ko'rib chiqish"""
    try:
        ir = requests.get(url, timeout=5, headers=headers)
        ct = ir.headers.get('content-type','')
        size = len(ir.content)
        if ir.ok and size > 20000 and 'image' in ct:
            return True, size
    except Exception:
        pass
    return False, 0


def fetch_images(queries, count=6):
    images = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
    }
    all_queries = queries if isinstance(queries, list) else [queries]

    def add_from_urls(url_list):
        for url in url_list:
            if len(images) >= count:
                break
            url = url.strip()
            if not url.startswith("http"):
                continue
            if any(d in url for d in BLOCKED_DOMAINS):
                continue
            if any(d in url.lower() for d in SKIP_DOMAINS):
                continue
            ok, size = _try_download(url, headers)
            if ok:
                images.append(url)
                print(f"     {size//1024}kb {url[:55]}")

    # 1. Bing
    for q in all_queries[:3]:
        if len(images) >= count: break
        try:
            r = requests.get(
                f"https://www.bing.com/images/search?q={requests.utils.quote(q)}&form=HDRSC2",
                headers=headers, timeout=8)
            if r.ok:
                raw_list = re.findall(r'murl&quot;:(.*?)&amp', r.text)
                urls = [u.replace("&quot;","").replace(",","").strip() for u in raw_list[:20]]
                add_from_urls(urls)
        except Exception as e:
            print(f"  Bing: {e}")

    # 2. Google (agar Bing yetarli bo'lmasa)
    if len(images) < count:
        for q in all_queries[:2]:
            if len(images) >= count: break
            try:
                r = requests.get(
                    f"https://www.google.com/search?q={requests.utils.quote(q)}&tbm=isch&hl=en",
                    headers=headers, timeout=8)
                if r.ok:
                    g_urls = re.findall(
                        r'"(https://(?!encrypted)[^"]+\.(?:jpg|jpeg|png|webp))"',
                        r.text)
                    add_from_urls(g_urls[:15])
            except Exception as e:
                print(f"  Google: {e}")

    # 3. Yandex (agar hali yetarli bo'lmasa)
    if len(images) < count:
        for q in all_queries[:2]:
            if len(images) >= count: break
            try:
                r = requests.get(
                    f"https://yandex.com/images/search?text={requests.utils.quote(q)}&isize=large",
                    headers=headers, timeout=8)
                if r.ok:
                    y_urls = re.findall(r'"url":"(https://[^"]+\.(?:jpg|jpeg|png))"', r.text)
                    add_from_urls(y_urls[:15])
            except Exception as e:
                print(f"  Yandex: {e}")

    print(f"  Topilgan rasm: {len(images)} ta")
    return images[:count]


def download_images(sources, count=6):
    paths = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for i, src in enumerate(sources[:count]):
        try:
            r = requests.get(src, timeout=10, headers=headers)
            if r.ok and len(r.content) > 5000:
                path = f"{TEMP_DIR}/slide_{i}.jpg"
                with open(path, "wb") as f:
                    f.write(r.content)
                img = Image.open(path)
                if img.size[0] > 100:
                    paths.append(path)
                    print(f"  Rasm {i+1}: {img.size}")
        except Exception as e:
            print(f"  Rasm {i+1} xato: {e}")
    return paths


# ── YouTube video kliplar (mavzuga mos, so'nggi yangiliklar) ──
def fetch_youtube_clips(keywords, count=5, search_queries=None):
    """Yangilik kliplarini qidirish — so'nggi 14 kunlik, kanal xilmaxilligi.
    Strategiya:
      1. Barcha yangilik kanallarida qidiruv (har kanaldan max 2 kanditat)
      2. Sana bo'yicha saralash (eng yangi birinchi)
      3. Kanal xilmaxilligini ta'minlab yuklab olish
      4. Umumiy YouTube qidiruvi (fallback)
    """
    try:
        import yt_dlp
    except ImportError:
        print("   yt-dlp o'rnatilmagan: pip install yt-dlp")
        return []

    import urllib.parse, glob as _glob
    from datetime import datetime, timedelta

    sq = list(search_queries or [])
    kw = list(keywords or [])

    # ── So'nggi N kun filtri ─────────────────────────────────────
    MAX_DAYS_OLD    = 14      # 14 kundan eski videoni o'tkazib yuborish
    MAX_PER_CHANNEL = 2       # Har kanaldan maksimum kandidat
    MAX_DL_PER_CH   = 2       # Yuklashda ham har kanaldan max 2 ta

    cutoff_date = (datetime.now() - timedelta(days=MAX_DAYS_OLD)).strftime("%Y%m%d")

    def _days_old(upload_date_str: str) -> int:
        """YYYYMMDD → necha kun oldin."""
        if not upload_date_str or len(upload_date_str) != 8:
            return -1   # Noma'lum — o'tkazib yubormaymiz
        try:
            d = datetime.strptime(upload_date_str, "%Y%m%d")
            return (datetime.now() - d).days
        except Exception:
            return -1

    # ── Yangilik kanallari ───────────────────────────────────────
    NEWS_CHANNELS = [
        # Asosiy inglizcha kanallar (ishonchli)
        "@BBCNews", "@AlJazeeraEnglish", "@Reuters",
        "@DWNews", "@FRANCE24", "@SkyNews",
        "@euronews", "@TRTWorld", "@channelnewsasia",
        "@NBCNews", "@Independent",
        # Qo'shimcha kanallar
        "@AssociatedPress", "@abcnews", "@CBSNews",
        "@TheGuardian", "@AJEnglish",
    ]

    # ── Kalit so'zlar ────────────────────────────────────────────
    _GENERIC = {
        "scandal","fear","hope","ahead","vote","deal","says","told",
        "report","warns","calls","plan","move","make","take","give",
        "after","over","amid","into","with","from","have","will",
        "news","latest","update","breaking","world","global","full",
    }
    proper_kw  = [k.lower() for k in kw if len(k)>3 and k[0].isupper()
                  and k.lower() not in _GENERIC]
    general_kw = [k.lower() for k in kw if len(k)>4
                  and k.lower() not in _GENERIC and k.lower() not in proper_kw]

    # Qidiruv so'rovlari
    main_queries = []
    for q in sq[:2]:
        main_queries.append(" ".join(q.split()[:6]))
    if proper_kw:
        main_queries.append(" ".join(proper_kw[:4]))
    if not main_queries and general_kw:
        main_queries.append(" ".join(general_kw[:4]))

    def _is_relevant(title_lower):
        if proper_kw:
            return any(k in title_lower for k in proper_kw)
        if general_kw:
            return any(k in title_lower for k in general_kw)
        return True

    # ── Studiya / tahlil videolarini aniqlash ────────────────────
    # Podcast va tahlil ko'rsatuvlari — haqiqiy voqea kadri yo'q
    _SHOW_NAMES = {
        "newscast", "global story", "global news podcast", "americast",
        "security brief", "beyond 100 days", "dateline", "hardtalk",
        "the briefing", "panorama", "the papers", "news review",
        "question time", "bbc question", "the context", "talking politics",
        "in depth", "the documentary", "assignment", "outlook",
        "the inquiry", "the food chain", "discovery", "digital planet",
        "podcast", "roundup", "opinion", "weekly", "analysis",
        "full show", "full episode", "full broadcast", "full program",
        "full interview", "full press conference",
        "24/7 live", "live stream", "live blog", "weather forecast",
        "morning show", "evening show", "breakfast show", "talk show",
    }
    # Tushuntirish (explainer) sarlavhalari — sarlavha BOSHIDA
    _EXPLAINER_START = re.compile(
        r'^\s*(what is|what are|why is|why are|how is|how are|'
        r'what was|why was|how did|who is|who are|who was|'
        r'what does|what did|what happened to|'
        r'everything you|all you need|what you need|'
        r'explained?|explainer|the history of|background|'
        r'inside [a-z]+:)',
        re.IGNORECASE
    )
    # Sarlavha ICHIDA qayerda bo'lmasin — tahlil belgisi
    _ANALYSIS_ANYWHERE = re.compile(
        r'\b(what happens if|what would happen|'
        r'years ago (matter|still matter|mattered)|'
        r'\d+ years ago|40 years|50 years|history of|'
        r'explained?|explainer|in depth|opinion|editorial|roundup|'
        r'global story|security brief|americast|newscast)\b',
        re.IGNORECASE
    )
    # "?" bilan tugagan sarlavha (hashtag yoki | kanal nomi keyin bo'lsa ham)
    _QUESTION_RE = re.compile(r'\?[\s|#\w\d.]*$')

    # Studiya / presenter so'zlari — bu kadrlar bizga kerak emas
    _STUDIO_WORDS = {
        "anchor", "presenter", "host", "correspondent", "journalist",
        "reporter", "interview", "panel", "roundtable", "round table",
        "expert", "analyst", "discussion", "debate", "reaction",
        "studio", "in studio", "live update", "live coverage",
        "breaking panel", "your questions", "speaks to", "talks to",
        "fact check", "fact-check", "explainer", "explained",
        "what is", "why is", "how is", "what are", "opinion",
    }

    def _is_studio_or_analysis(title: str) -> bool:
        tl = title.lower()
        # Ko'rsatuv / podcast nomlar
        if any(s in tl for s in _SHOW_NAMES):
            return True
        # Studiya / presenter so'zlari
        if any(sw in tl for sw in _STUDIO_WORDS):
            return True
        # Sarlavha boshida explainer pattern
        if _EXPLAINER_START.search(title):
            return True
        # Sarlavha ichida tahlil belgisi
        if _ANALYSIS_ANYWHERE.search(title):
            return True
        # "?" bilan tugagan sarlavha (< 15 so'z) = tahlil
        if _QUESTION_RE.search(title) and len(title.split()) <= 15:
            return True
        # #Shorts — vertikal format
        if "#shorts" in tl or "| shorts" in tl:
            return True
        return False

    # ── Kandidat reytingi ────────────────────────────────────────
    def _candidate_score(vid_id, title, upload_date_str) -> int:
        days = _days_old(upload_date_str)
        if days < 0:
            score = 5   # Sana noma'lum — neytral
        elif days == 0:
            score = 100
        elif days <= 3:
            score = 80
        elif days <= 7:
            score = 50
        elif days <= 14:
            score = 20
        else:
            score = 0   # Juda eski — keyinroq filtrlash
        # Harakatli sarlavha: voqea kadri bo'lishi yuqori
        ACTION = {"attack","strike","launch","bomb","fire","clash","kill",
                  "confirm","announce","breaking","warn","threat","rescue",
                  "arrest","resign","vote","sign","agree","reject","flee"}
        if any(w in title.lower() for w in ACTION):
            score += 10
        return score

    downloaded        = []
    seen_ids          = set()
    _yt_bot_detected  = False   # YouTube "Sign in" xatosi aniqlansa — to'xtatish

    # ── yt-dlp sozlamalari ───────────────────────────────────────
    _cookies_file = os.getenv("YOUTUBE_COOKIES_FILE",
                              os.path.join(os.path.dirname(__file__), "youtube_cookies.txt"))

    def _base_opts(outtmpl_prefix="yt"):
        opts = {
            "quiet":               True,
            "no_warnings":         True,
            "noprogress":          True,
            "logger":              type("NullLogger", (), {   # yt-dlp xatolarini yashirish
                                       "debug":   lambda s, m: None,
                                       "info":    lambda s, m: None,
                                       "warning": lambda s, m: None,
                                       "error":   lambda s, m: None,
                                   })(),
            "format":              "bestvideo[height<=720][ext=mp4]+bestaudio/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "merge_output_format": "mp4",
            "outtmpl":             os.path.join(TEMP_DIR, f"{outtmpl_prefix}_%(id)s.%(ext)s"),
            "noplaylist":          True,
            "match_filter":        yt_dlp.utils.match_filter_func("duration > 20 & duration < 480"),
            "extractor_args":      {"youtube": {
                                       "player_client": ["android_vr"],
                                       # bgutil-ytdlp-pot-provider — PO Token server
                                       "getpot_bgutil_baseurl": ["http://localhost:4416"],
                                   }},
            "sleep_interval":      1,
            "retries":             2,
        }
        if _cookies_file and os.path.exists(_cookies_file):
            opts["cookiefile"] = _cookies_file
        return opts

    def _find_file(vid_id, prefix="yt"):
        pattern = os.path.join(TEMP_DIR, f"{prefix}_{vid_id}.*")
        files   = [f for f in _glob.glob(pattern)
                   if os.path.getsize(f) > 100_000]
        return files[0] if files else None

    _bot_fail_count = 0   # Consecutive "Sign in" failures

    def _download_video(vid_id, prefix="yt"):
        nonlocal _yt_bot_detected, _bot_fail_count
        if _yt_bot_detected:
            return None
        url  = f"https://www.youtube.com/watch?v={vid_id}"
        path = _find_file(vid_id, prefix)
        if path:
            return path
        try:
            opts = _base_opts(prefix)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            _bot_fail_count = 0  # Muvaffaqiyatli — counter reset
            return _find_file(vid_id, prefix)
        except Exception as e:
            err = str(e)
            if "Sign in to confirm" in err or "Please sign in" in err:
                _bot_fail_count += 1
                if _bot_fail_count >= 3:
                    # 3 ta ketma-ket bot xatosi — YouTube to'liq blok qilgan
                    print(f"   ⚠️  YouTube bot-detection! Cookies kerak — Pexels fallbackga o'tilmoqda")
                    _yt_bot_detected = True
                return None
            print(f"   Yuklash xato ({vid_id}): {err[:60]}")
            return None

    # ══════════════════════════════════════════════════════════════
    # 1-BOSQICH: Barcha kanallarda qidiruv (max MAX_PER_CHANNEL ta)
    # ══════════════════════════════════════════════════════════════
    print(f"   1-bosqich: {len(NEWS_CHANNELS)} kanalda qidiruv (so'nggi {MAX_DAYS_OLD} kun)...")

    _null_logger = type("NullLogger", (), {
        "debug":   lambda s, m: None,
        "info":    lambda s, m: None,
        "warning": lambda s, m: None,
        "error":   lambda s, m: None,
    })()

    flat_opts = {
        "quiet":          True,
        "no_warnings":    True,
        "noprogress":     True,
        "logger":         _null_logger,
        "extract_flat":   "in_playlist",
        "playlist_end":   6,          # Har qidiruvda max 6 ta
        "extractor_args": {"youtube": {
            "player_client": ["android_vr"],
            "getpot_bgutil_baseurl": ["http://localhost:4416"],
        }},
        **( {"cookiefile": _cookies_file} if _cookies_file and os.path.exists(_cookies_file) else {} ),
    }

    # (score, vid_id, title, channel, upload_date)
    all_candidates = []
    channel_found  = {}    # channel → count

    for channel in NEWS_CHANNELS:
        ch_count = 0
        for q in main_queries[:2]:
            if not q.strip() or ch_count >= MAX_PER_CHANNEL:
                break
            url = f"https://www.youtube.com/{channel}/search?query={urllib.parse.quote(q)}"
            try:
                with yt_dlp.YoutubeDL(flat_opts) as ydl:
                    info    = ydl.extract_info(url, download=False)
                    entries = (info or {}).get("entries", []) or []
                    for e in entries:
                        if ch_count >= MAX_PER_CHANNEL:
                            break
                        if not e:
                            continue
                        vid_id = e.get("id") or ""
                        if not vid_id or vid_id in seen_ids:
                            continue
                        title       = e.get("title", "")
                        upload_date = e.get("upload_date", "") or ""
                        tl          = title.lower()

                        # Mavzu aloqadorligi
                        if not _is_relevant(tl):
                            continue
                        # Studiya / tahlil filtri
                        if _is_studio_or_analysis(title):
                            continue
                        # Sana filtri — noma'lum sana o'tkaziladi, eski aniq sana bloklanadi
                        days = _days_old(upload_date)
                        if days > MAX_DAYS_OLD:
                            continue

                        seen_ids.add(vid_id)
                        score = _candidate_score(vid_id, title, upload_date)
                        all_candidates.append((score, vid_id, title, channel, upload_date))
                        ch_count += 1
                        age_str = f"{days}k" if days >= 0 else "yangi"
                        print(f"   ✓ [{channel}|{age_str}] {title[:55]}")
            except Exception:
                pass

        if ch_count > 0:
            channel_found[channel] = ch_count

    # Reyting bo'yicha saralash (eng yangi va eng mos — birinchi)
    all_candidates.sort(key=lambda x: x[0], reverse=True)

    print(f"   Jami kandidatlar: {len(all_candidates)} ta ({len(channel_found)} kanaldan)")

    # Yuklab olish — kanal xilmaxilligini ta'minlash
    channel_dl_count = {}
    for score, vid_id, title, channel, upload_date in all_candidates:
        if len(downloaded) >= count or _yt_bot_detected:
            break
        # Bir kanaldan ko'pi bilan MAX_DL_PER_CH ta
        if channel_dl_count.get(channel, 0) >= MAX_DL_PER_CH:
            continue
        path = _download_video(vid_id, "ch")
        if path:
            downloaded.append(path)
            channel_dl_count[channel] = channel_dl_count.get(channel, 0) + 1
            days = _days_old(upload_date)
            age_str = f"{days} kun oldin" if days >= 0 else "sana noma'lum"
            print(f"   + [{channel}|{age_str}] {title[:50]}")

    # ══════════════════════════════════════════════════════════════
    # 2-BOSQICH: ytsearch fallback (agar yetarli klip topilmasa)
    # ══════════════════════════════════════════════════════════════
    if len(downloaded) < count and not _yt_bot_detected:
        need = count - len(downloaded)
        print(f"   2-bosqich: ytsearch fallback ({need} ta kerak)...")
        search_flat_opts = {
            "quiet":          True,
            "no_warnings":    True,
            "extract_flat":   "in_playlist",
            "playlist_end":   12,
            "extractor_args": {"youtube": {
                "player_client": ["android_vr"],
                "getpot_bgutil_baseurl": ["http://localhost:4416"],
            }},
            **( {"cookiefile": _cookies_file} if _cookies_file and os.path.exists(_cookies_file) else {} ),
        }
        for q in main_queries:
            if len(downloaded) >= count:
                break
            try:
                with yt_dlp.YoutubeDL(search_flat_opts) as ydl:
                    info    = ydl.extract_info(f"ytsearch12:{q}", download=False)
                    entries = (info or {}).get("entries", []) or []
                    fb_candidates = []
                    for e in entries:
                        if not e:
                            continue
                        vid_id = e.get("id","")
                        if not vid_id or vid_id in seen_ids:
                            continue
                        title       = e.get("title","")
                        upload_date = e.get("upload_date","") or ""
                        tl          = title.lower()
                        if not _is_relevant(tl) or _is_studio_or_analysis(title):
                            continue
                        days = _days_old(upload_date)
                        if days > MAX_DAYS_OLD:
                            continue
                        score = _candidate_score(vid_id, title, upload_date)
                        fb_candidates.append((score, vid_id, title, upload_date))

                    fb_candidates.sort(key=lambda x: x[0], reverse=True)
                    for score, vid_id, title, upload_date in fb_candidates:
                        if len(downloaded) >= count or _yt_bot_detected:
                            break
                        seen_ids.add(vid_id)
                        path = _download_video(vid_id, "yt")
                        if path:
                            downloaded.append(path)
                            days = _days_old(upload_date)
                            age_str = f"{days} kun" if days >= 0 else "?"
                            print(f"   + [search|{age_str}k] {title[:50]}")
            except Exception as ex:
                print(f"   xato: {str(ex)[:80]}")

    print(f"   Jami: {len(downloaded)} ta klip ({len(set(channel_dl_count.keys()))} kanal)")
    return downloaded


# ── Pexels video kliplar (fallback) ──────────────────────────
def fetch_pexels_clips(keywords, count=4, search_queries=None):
    """Pexels API orqali mavzuga mos video kliplarni yuklab olish.
    YouTube bot-detection blok qilsa — bu fallback ishlatiladi.
    API: https://api.pexels.com/videos/search
    """
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        print("   Pexels: PEXELS_API_KEY topilmadi — o'tkazildi")
        return []

    sq  = list(search_queries or [])
    kw  = list(keywords or [])
    # Qidiruv so'rovlari: birinchi search_query, keyin keywords
    queries = []
    if sq:
        queries.append(" ".join(sq[0].split()[:5]))
    if kw:
        queries.append(" ".join(kw[:4]))
    if not queries:
        return []

    os.makedirs(TEMP_DIR, exist_ok=True)
    downloaded = []
    seen_ids   = set()
    headers    = {"Authorization": api_key}

    for q in queries[:2]:
        if len(downloaded) >= count:
            break
        try:
            url  = f"https://api.pexels.com/videos/search?query={requests.utils.quote(q)}&per_page=8&size=medium"
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code != 200:
                print(f"   Pexels xato {resp.status_code}: {q[:40]}")
                continue
            data   = resp.json()
            videos = data.get("videos", [])
            print(f"   Pexels '{q[:35]}': {len(videos)} ta natija")

            for vid in videos:
                if len(downloaded) >= count:
                    break
                vid_id  = str(vid.get("id", ""))
                if not vid_id or vid_id in seen_ids:
                    continue

                # Eng yaxshi format: HD (1280) yoki Full HD (1920) MP4
                vfiles = vid.get("video_files", [])
                # Faqat MP4, HD yoki FHD, landscape
                candidates = [
                    f for f in vfiles
                    if f.get("file_type") == "video/mp4"
                    and f.get("width", 0) >= 1280
                    and f.get("height", 0) >= 720
                    and f.get("width", 0) > f.get("height", 0)  # landscape
                ]
                if not candidates:
                    # 720p yoki har qanday MP4
                    candidates = [f for f in vfiles if f.get("file_type") == "video/mp4"]
                if not candidates:
                    continue
                # Eng kichik faylni olish (sekin internet uchun)
                best = min(candidates, key=lambda f: abs(f.get("width", 0) - 1280))
                video_url = best.get("link", "")
                if not video_url:
                    continue

                out_path = os.path.join(TEMP_DIR, f"pexels_{vid_id}.mp4")
                if os.path.exists(out_path) and os.path.getsize(out_path) > 100_000:
                    print(f"   + [Pexels|cache] {vid.get('user', {}).get('name','?')} #{vid_id}")
                    downloaded.append(out_path)
                    seen_ids.add(vid_id)
                    continue

                # Yuklash
                try:
                    r = requests.get(video_url, headers={"User-Agent": "Mozilla/5.0"},
                                     stream=True, timeout=60)
                    if r.status_code == 200:
                        with open(out_path, "wb") as fp:
                            for chunk in r.iter_content(chunk_size=1024*256):
                                fp.write(chunk)
                        size_mb = os.path.getsize(out_path) / 1_048_576
                        if size_mb > 0.5:
                            print(f"   + [Pexels|{size_mb:.1f}MB] {q[:35]} #{vid_id}")
                            downloaded.append(out_path)
                            seen_ids.add(vid_id)
                        else:
                            os.remove(out_path)
                    else:
                        print(f"   Pexels yuklab olish xato ({r.status_code}): #{vid_id}")
                except Exception as e:
                    print(f"   Pexels yuklab olish xato: {str(e)[:60]}")
        except Exception as ex:
            print(f"   Pexels API xato: {str(ex)[:80]}")

    print(f"   Pexels kliplar: {len(downloaded)} ta")
    return downloaded


# ── Dailymotion + Vimeo kliplar ───────────────────────────────
def fetch_web_clips(keywords, count=3, search_queries=None):
    """Dailymotion va Vimeo dan yt-dlp orqali klip yuklash."""
    try:
        import yt_dlp
    except ImportError:
        return []

    sq = list(search_queries or [])
    kw = list(keywords or [])
    queries = (sq[:2] + [" ".join(kw[:3])])[:3]

    downloaded = []
    seen_ids   = set()
    out_tmpl   = os.path.join(TEMP_DIR, "web_%(id)s.%(ext)s")
    kw_lower   = [k.lower() for k in kw if len(k) > 3]

    ydl_opts = {
        "quiet":               True,
        "no_warnings":         True,
        "format":              "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "merge_output_format": "mp4",
        "outtmpl":             out_tmpl,
        "noplaylist":          True,
        "match_filter":        yt_dlp.utils.match_filter_func(
                                   "duration > 15 & duration < 600"),
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        "retries": 3,
    }

    def _is_relevant(title_lower):
        if not kw_lower:
            return True
        return any(k in title_lower for k in kw_lower)

    def _grab_web(entries):
        for entry in entries or []:
            if not entry or len(downloaded) >= count:
                break
            vid_id = entry.get("id", "")
            if not vid_id or vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)
            title_raw   = entry.get("title", "")
            title_lower = title_raw.lower()
            if not _is_relevant(title_lower):
                continue
            path = os.path.join(TEMP_DIR, f"web_{vid_id}.mp4")
            if os.path.exists(path) and os.path.getsize(path) > 100_000:
                print(f"   + [DM/Vimeo] {title_raw[:60]}")
                downloaded.append(path)

    # Dailymotion
    for q in queries:
        if len(downloaded) >= count:
            break
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"dmsearch{count}:{q}", download=True)
                _grab_web((info or {}).get("entries", []))
        except Exception as e:
            pass  # Dailymotion xato — davom etamiz

    # Vimeo
    for q in queries:
        if len(downloaded) >= count:
            break
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"vimeo:search:{q}", download=True)
                _grab_web((info or {}).get("entries", []))
        except Exception as e:
            pass

    print(f"   Web kliplar: {len(downloaded)} ta")
    return downloaded


# ── Shot list bo'yicha kliplar (har shot uchun alohida qidiruv) ──
def fetch_clips_per_shot(shot_list):
    """
    Har bir shot uchun alohida YouTube qidiruvi.
    shot_list = [{"shot":1,"description":"...","search":"...","duration":5}, ...]
    Qaytaradi: [path1, path2, ...] — tartib shot tartibiga mos.
    Topilmagan shot uchun None (montaj vaqtida qo'shni klipi uzaytiriladi).
    """
    try:
        import yt_dlp
    except ImportError:
        return []

    os.makedirs(TEMP_DIR, exist_ok=True)
    cookies_file = os.getenv("YOUTUBE_COOKIES_FILE",
                             os.path.join(os.path.dirname(__file__), "youtube_cookies.txt"))

    null_log = type("NL", (), {
        "debug": lambda s,m: None, "info": lambda s,m: None,
        "warning": lambda s,m: None, "error": lambda s,m: None,
    })()

    base_opts = {
        "quiet": True, "no_warnings": True, "noprogress": True,
        "logger": null_log,
        "format": "bestvideo[height<=720][ext=mp4]+bestaudio/bestvideo[height<=720]+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "match_filter": yt_dlp.utils.match_filter_func("duration > 15 & duration < 300"),
        "extractor_args": {"youtube": {
            "player_client": ["android_vr"],
            "getpot_bgutil_baseurl": ["http://localhost:4416"],
        }},
        "retries": 2,
        **({"cookiefile": cookies_file} if cookies_file and os.path.exists(cookies_file) else {}),
    }

    # Studiya so'zlari — shot qidiruvda ham filtr
    _BAD = {"anchor","presenter","studio","panel","interview","expert","analyst",
            "roundtable","discussion","debate","podcast","opinion","explained",
            "reaction","briefing","correspondent","reporter","journalist"}

    def _is_bad_title(t):
        tl = t.lower()
        return any(b in tl for b in _BAD) or "#shorts" in tl

    def _days(upload_date_str):
        if not upload_date_str or len(upload_date_str) < 8:
            return -1
        try:
            d = datetime.strptime(upload_date_str[:8], "%Y%m%d")
            return (datetime.now() - d).days
        except Exception:
            return -1

    seen_ids = set()
    results  = []

    for shot in shot_list:
        shot_n  = shot.get("shot", "?")
        search  = shot.get("search", "")
        desc    = shot.get("description", "")
        if not search:
            results.append(None)
            continue

        print(f"   🎬 Shot {shot_n}: '{desc[:45]}'")
        print(f"      🔍 '{search[:60]}'")

        found = None
        # Flat qidiruv — kandidatlarni topish
        flat_opts = dict(base_opts, extract_flat="in_playlist", playlist_end=8)
        flat_opts.pop("format", None)
        flat_opts.pop("merge_output_format", None)
        flat_opts.pop("match_filter", None)

        try:
            with yt_dlp.YoutubeDL(flat_opts) as ydl:
                info = ydl.extract_info(f"ytsearch8:{search}", download=False)
                entries = (info or {}).get("entries", []) or []
                candidates = []
                for e in entries:
                    if not e:
                        continue
                    vid_id = e.get("id", "")
                    if not vid_id or vid_id in seen_ids:
                        continue
                    title = e.get("title", "")
                    if _is_bad_title(title):
                        continue
                    upload_date = e.get("upload_date", "") or ""
                    days = _days(upload_date)
                    # Sana bonusi
                    score = 100 if days == 0 else (80 if days <= 3 else (50 if days <= 7 else (20 if days <= 30 else 5)))
                    candidates.append((score, vid_id, title, upload_date))

                candidates.sort(key=lambda x: x[0], reverse=True)

                for score, vid_id, title, upload_date in candidates:
                    if vid_id in seen_ids:
                        continue
                    out_path = os.path.join(TEMP_DIR, f"shot_{vid_id}.mp4")
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 100_000:
                        print(f"      ✓ Cache: {title[:50]}")
                        seen_ids.add(vid_id)
                        found = out_path
                        break
                    try:
                        dl_opts = dict(base_opts, outtmpl=os.path.join(TEMP_DIR, "shot_%(id)s.%(ext)s"))
                        with yt_dlp.YoutubeDL(dl_opts) as ydl2:
                            ydl2.download([f"https://www.youtube.com/watch?v={vid_id}"])
                        days_str = f"{_days(upload_date)}k" if _days(upload_date) >= 0 else "yangi"
                        if os.path.exists(out_path) and os.path.getsize(out_path) > 100_000:
                            print(f"      ✓ [{days_str}] {title[:50]}")
                            seen_ids.add(vid_id)
                            found = out_path
                            break
                    except Exception:
                        pass
        except Exception as ex:
            print(f"      ⚠️  Shot {shot_n} qidiruv xato: {str(ex)[:60]}")

        if not found:
            print(f"      ✗ Shot {shot_n} uchun klip topilmadi")
        results.append(found)

    found_count = sum(1 for r in results if r)
    print(f"   Shot list natija: {found_count}/{len(shot_list)} ta klip")
    return [r for r in results if r]  # None larni chiqarib tashlash


# ── Maqola rasmlari → video klip (slideshow fallback) ────────
def fetch_images_as_clip(article_url, article_title, count=6, duration_each=4):
    """Maqola rasmlari yoki Bing qidiruvidan slideshow klip yasash.
    YouTube blok bo'lganda fallback sifatida ishlatiladi.
    Har bir rasm {duration_each} soniya ko'rsatiladi, zoom effekti bilan.
    """
    os.makedirs(TEMP_DIR, exist_ok=True)

    # 1. Maqola rasmlari
    img_paths = fetch_article_images(article_url, count=count)

    # 2. Agar kamroq bo'lsa — Bing qidiruv
    if len(img_paths) < 3:
        try:
            from youtube_maker import fetch_bing_images
            bing = fetch_bing_images(article_title, count=count - len(img_paths))
            img_paths.extend(bing)
        except Exception:
            pass

    if not img_paths:
        print("   Rasm topilmadi — klip yasash mumkin emas")
        return None

    # 3. Har bir rasmni 1280x720 ga o'zgartirish
    resized = []
    for i, src in enumerate(img_paths[:count]):
        try:
            dst = os.path.join(TEMP_DIR, f"slide_{i:02d}.jpg")
            img = Image.open(src).convert("RGB")
            # Letter-box / pillar-box: 1280x720 ga to'ldirish
            img.thumbnail((1280, 720), Image.LANCZOS)
            bg = Image.new("RGB", (1280, 720), (10, 15, 30))
            x = (1280 - img.width)  // 2
            y = (720  - img.height) // 2
            bg.paste(img, (x, y))
            bg.save(dst, "JPEG", quality=90)
            resized.append(dst)
        except Exception as e:
            print(f"   Rasm {i} xato: {e}")

    if not resized:
        return None

    # 4. ffmpeg concat slideshow → mp4
    out_path   = os.path.abspath(os.path.join(TEMP_DIR, "slideshow_clip.mp4"))
    concat_txt = os.path.abspath(os.path.join(TEMP_DIR, "slideshow_list.txt"))
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in resized:
            abs_p = os.path.abspath(p).replace("\\", "/")
            f.write(f"file '{abs_p}'\n")
            f.write(f"duration {duration_each}\n")
        # Oxirgi rasm uchun yana bir bor (ffmpeg talab qiladi)
        abs_last = os.path.abspath(resized[-1]).replace("\\", "/")
        f.write(f"file '{abs_last}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_txt,
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
               "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=#0a0f16",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-r", "25",
        out_path
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        if size > 10_000:
            print(f"   + [Slideshow|{size//1024}KB] {len(resized)} ta rasm × {duration_each}s")
            return out_path
    except Exception as e:
        print(f"   Slideshow xato: {e}")
    return None


# ── Video klipdan segment (ffmpeg subprocess, HUD overlay) ───
def _clip_has_audio(clip_path):
    """ffprobe orqali klipda audio bor-yo'qligini tekshirish."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", clip_path],
            capture_output=True, text=True, timeout=5)
        return "audio" in r.stdout
    except Exception:
        return False


def _clip_duration(clip_path):
    """ffprobe orqali video davomiyligini aniqlash."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", clip_path],
            capture_output=True, text=True, timeout=10)
        info = _json.loads(r.stdout or "{}")
        for s in info.get("streams", []):
            if s.get("codec_type") == "video":
                return float(s.get("duration", 20.0))
    except Exception:
        pass
    return 20.0


def make_video_segment(clip_path, hud_path, duration, out_path, lang="uz"):
    """ffmpeg subprocess: klip → scale/crop 1280×720 + HUD overlay.
    Har doim audio chiqaradi (klipda yo'q bo'lsa — jimlik)."""
    has_audio = _clip_has_audio(clip_path)

    if has_audio:
        filt = (
            f"[0:v]scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_W}:{VIDEO_H}[bg];"
            f"[bg][1:v]overlay=0:0[vout];"
            f"[0:a]volume=0.08,aresample=44100[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-t", str(duration), "-i", clip_path,
            "-i", hud_path,
            "-filter_complex", filt,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2",
            "-r", str(FPS), "-t", str(duration),
            out_path,
        ]
    else:
        filt = (
            f"[0:v]scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_W}:{VIDEO_H}[bg];"
            f"[bg][1:v]overlay=0:0[vout];"
            f"[2:a]atrim=0:{duration},asetpts=PTS-STARTPTS[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-t", str(duration), "-i", clip_path,
            "-i", hud_path,
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-filter_complex", filt,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2",
            "-r", str(FPS), "-t", str(duration),
            out_path,
        ]

    r = subprocess.run(cmd, capture_output=True, timeout=180)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace")[-600:]
        raise RuntimeError(f"ffmpeg segment xato:\n{err}")
    return out_path


# ── Musiqa ────────────────────────────────────────────────────
def get_music():
    music_file = f"{TEMP_DIR}/bg_music_news.mp3"
    if os.path.exists(music_file):
        return music_file
    urls = [
        "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "https://cdn.pixabay.com/download/audio/2023/03/28/audio_2a9e14e5a1.mp3",
        "https://cdn.pixabay.com/download/audio/2022/10/25/audio_946f0a2660.mp3",
        "https://cdn.pixabay.com/download/audio/2022/01/18/audio_d0c6ff1bac.mp3",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=20)
            if r.ok and len(r.content) > 30000:
                with open(music_file, "wb") as f:
                    f.write(r.content)
                print("   Musiqa yuklandi")
                return music_file
        except Exception:
            continue
    return None


# ── Matnni uzaytirish (Claude API) ───────────────────────────
def extend_script(text, lang="uz", target_words=400):
    """Qisqa matni 2-3 daqiqalik narration uchun uzaytirish.
    Avval Groq, bo'lmasa OpenRouter ishlatiladi."""
    word_count = len(text.split())
    if word_count >= target_words:
        print(f"   Matn yetarli: {word_count} so'z")
        return text

    prompts = {
        "uz": (
            f"Bu yangilik matnini kamida {target_words} so'zga uzaytir. "
            "Faqat tayyor matnni qaytargin, boshqa hech narsa yozma. "
            "Sof o'zbek lotin tilida yoz, ruscha so'z ishlatma. "
            "Jurnalistik uslubda kontekst, tarix va tafsilotlar qo'sh. "
            "Matn 'Efirda 1KUN Global.' bilan boshlanib, "
            "'Siz bilan 1 Kun bo'ldi. Kelgusi yangiliklarda ko'rishamiz.' bilan tugasin.\n\n"
            f"{text}"
        ),
        "ru": (
            f"Расширь этот новостной текст до минимум {target_words} слов. "
            "Верни только текст без пояснений. "
            "Добавь контекст, предысторию и детали в журналистском стиле. "
            "Текст должен начинаться с 'В эфире 1ДЕНЬ Global.' и заканчиваться "
            "'Это был 1ДЕНЬ Global. До следующих новостей.'\n\n"
            f"{text}"
        ),
        "en": (
            f"Expand this news text to at least {target_words} words. "
            "Return only the expanded text, nothing else. "
            "Add context, background and details in journalistic style. "
            "Text must start with 'This is 1DAY Global.' and end with "
            "'That was 1DAY Global. Stay tuned for more.'\n\n"
            f"{text}"
        ),
    }
    prompt = prompts.get(lang, prompts["en"])

    # 1. Groq (tez, bepul)
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}",
                         "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 2000, "temperature": 0.7},
                timeout=30
            )
            if r.ok:
                extended = r.json()["choices"][0]["message"]["content"].strip()
                print(f"   Groq: {word_count} -> {len(extended.split())} so'z")
                return extended
        except Exception as e:
            print(f"   Groq xato: {e}")

    # 2. OpenRouter (fallback)
    or_key = os.getenv("OPENROUTER_API_KEY")
    if or_key:
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {or_key}",
                         "Content-Type": "application/json"},
                json={"model": "meta-llama/llama-3.3-70b-instruct",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 2000},
                timeout=30
            )
            if r.ok:
                extended = r.json()["choices"][0]["message"]["content"].strip()
                print(f"   OpenRouter: {word_count} -> {len(extended.split())} so'z")
                return extended
        except Exception as e:
            print(f"   OpenRouter xato: {e}")

    print(f"   Matn uzaytirilmadi ({word_count} so'z)")
    return text


# ── Matndan kalit so'zlar ajratish ───────────────────────────
def extract_keywords(text, title="", count=8):
    """Matndagi muhim so'zlarni YouTube qidiruvi uchun ajratish."""
    from collections import Counter
    combined = f"{title} {text}"

    # Bosh harfli so'zlar (nom, joy, tashkilot)
    proper = re.findall(r'\b[A-Z][a-zA-Z]{2,}\b', combined)
    stop = {
        "The","This","That","With","From","Have","Been","Will","They","Their",
        "There","What","When","Where","Which","After","Before","Also","News",
        "Said","Says","Were","Into","More","Most","Some","Such","Than","Then",
        "These","Those","Very","Just","About","Over","Under","While","Since",
        "During","Both","Each","Many","Only","Same","Other","Another","Because",
        "Through","Between","Against","Without","Within","However","Although",
    }
    keywords = [w for w in proper if w not in stop]
    top = [w for w, _ in Counter(keywords).most_common(count)]

    # Yetarli bo'lmasa — uzun so'zlar
    if len(top) < 3:
        long_w = re.findall(r'\b[a-zA-Z]{6,}\b', combined)
        extra = [w.lower() for w in long_w if w not in stop]
        top += [w for w, _ in Counter(extra).most_common(5)]

    return top[:count]


# ── O'zbek matni TTS uchun preprocessing ─────────────────────

# Rasmiy o'zbek lotin: G' va O' → modifier letter turned comma (U+02BB)
# Bu unicode Microsoft o'zbek neural modelida to'g'ri talaffuz beradi
_TC = '\u02bb'   # ʻ  modifier letter turned comma

# Barcha apostrophe variantlari: ' U+0027, ' U+2018, ' U+2019, ʼ U+02BC
_APOS = ["'", '\u2018', '\u2019', '\u02bc', '\u02b9']

_UZ_FIXES = {
    # Qisqartmalar → to'liq shakl (TTS uchun)
    r'\bAQSh\b':    "Amerika",
    r'\bBMT\b':     "Birlashgan Millatlar",
    r'\bNATO\b':    "Nato",
    r'\bYeI\b':     "Yevropa Ittifoqi",
    r'\bYIT\b':     "Yevropa Ittifoqi",
    r'\bOAV\b':     "ommaviy axborot vositalari",
    r'\bYuNESKO\b': "Yunesko",
    r'\bYuNISEF\b': "Yunisyef",
    r'\bYAIM\b':    "yalpi ichki mahsulot",
    r'\bJSST\b':    "Jahon sogʻliqni saqlash tashkiloti",
    r'\bXXI\b':     "yigirma birinchi",
    r'\bXX\b':      "yigirmanchi",

    # Xorijiy nomlar → o'zbek transkripsiyasi
    r'\bTrump\b':      "Tramp",
    r'\bBiden\b':      "Bayden",
    r'\bZelensky\b':   "Zelenskiy",
    r'\bNetanyahu\b':  "Netanyaxu",
    r'\bTwitter\b':    "Tvitter",
    r'\bFacebook\b':   "Feysbuk",
    r'\bYouTube\b':    "Yutub",

    # Ko'p nuqta → bitta nuqta
    r'\.{2,}': '.',
}


def _preprocess_uz(text: str) -> str:
    """O'zbek matni — TTS talaffuzini yaxshilash uchun.

    Asosiy tuzatishlar:
    1. G' va O' → Gʻ / Oʻ  (U+02BB) — to'g'ri talaffuz
    2. Qisqartmalar → to'liq shakl
    3. Ortiqcha raqamlar olib tashlanadi
    """
    # ── 1. G' va O' → rasmiy o'zbek unicode (U+02BB) ─────────
    # Barcha apostrophe variantlarini bir xil qilamiz
    for apos in _APOS:
        text = text.replace(f'G{apos}', f'G{_TC}')
        text = text.replace(f'g{apos}', f'g{_TC}')
        text = text.replace(f'O{apos}', f'O{_TC}')
        text = text.replace(f'o{apos}', f'o{_TC}')

    # ── 2. Regex almashtirish ────────────────────────────────
    for pat, repl in _UZ_FIXES.items():
        text = re.sub(pat, repl, text, flags=re.IGNORECASE)

    # ── 3. Raqamlarni o'chirish (TTS noto'g'ri o'qiydi) ─────
    text = re.sub(r'\b\d{5,}\b',   '', text)   # Uzun raqamlar
    text = re.sub(r'\b\d{4}\b',    '', text)   # To'rt xonali (yillar)
    text = re.sub(r'\b\d+[.,]\d+\b', '', text) # O'nlik sonlar

    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text


# ── Audio post-processing (broadcast EQ + kompressor) ────────
def _enhance_audio(raw_file: str, out_file: str, lang: str = "uz") -> str:
    """ffmpeg EQ + kompressor — broadcast sifati.
    raw_file → out_file. Xato bo'lsa raw_file qaytariladi."""
    fx = AUDIO_FX.get(lang)
    if not fx:
        return raw_file
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", raw_file,
             "-af", fx,
             "-c:a", "mp3", "-b:a", "192k",
             out_file],
            capture_output=True, timeout=60
        )
        if r.returncode == 0 and os.path.exists(out_file):
            return out_file
        err = r.stderr.decode("utf-8", errors="replace")[-200:]
        print(f"   Audio FX xato: {err}")
    except Exception as e:
        print(f"   Audio FX: {e}")
    return raw_file  # fallback


# ── Ovoz ──────────────────────────────────────────────────────
def make_audio(text, filename, lang="uz", daraja="xabar"):
    """TTS + broadcast audio post-processing.
    lang: 'uz'|'ru'|'en'
    daraja: 'muhim'|'tezkor'|'xabar'  — UZ uchun ovoz tanlash"""

    # ── Matn tozalash ────────────────────────────────────────
    text = re.sub(r'\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b', '', text)
    if lang == "uz":
        text = _preprocess_uz(text)
    elif lang == "en":
        # "1Kun"/"1DAY" → "One Day" (TTS "one KUN" deb o'qishini oldini olish)
        text = re.sub(r'1\s*Kun\b', 'One Day', text, flags=re.IGNORECASE)
        text = re.sub(r'1DAY\b', 'One Day', text, flags=re.IGNORECASE)
        text = re.sub(r'\b\d+\b', '', text)
    elif lang == "ru":
        # "1Kun"/"1ДЕНЬ" → "Один День" (TTS uchun)
        text = re.sub(r'1\s*Kun\b', 'Один День', text, flags=re.IGNORECASE)
        text = re.sub(r'1ДЕНЬ\b', 'Один День', text, flags=re.IGNORECASE)
        text = re.sub(r'\b\d+\b', '', text)
    else:
        text = re.sub(r'\b\d+\b', '', text)
    text = re.sub(r'\s+', ' ', text).strip()

    # ── Ovoz tanlash ─────────────────────────────────────────
    lang_voices = VOICES.get(lang, VOICES["uz"])
    if isinstance(lang_voices, dict) and "voice" not in lang_voices:
        # Yangi tuzilma: {daraja: cfg, "default": cfg}
        voice_cfg = lang_voices.get(daraja, lang_voices.get("default"))
    else:
        voice_cfg = lang_voices  # eski tuzilma — fallback

    print(f"   Ovoz: {voice_cfg['voice']} | "
          f"rate={voice_cfg['rate']} pitch={voice_cfg['pitch']}")

    word_timings = []

    async def _run():
        communicate = edge_tts.Communicate(
            text,
            voice=voice_cfg["voice"],
            rate=voice_cfg["rate"],
            pitch=voice_cfg["pitch"],
            volume=voice_cfg["volume"]
        )
        audio_chunks = []
        async for event in communicate.stream():
            if event["type"] == "audio":
                audio_chunks.append(event["data"])
            elif event["type"] == "WordBoundary":
                word_timings.append({
                    "word":   event["text"],
                    "offset": event["offset"] / 10_000_000,
                    "dur":    event["duration"] / 10_000_000,
                })
        with open(filename, "wb") as f:
            for chunk in audio_chunks:
                f.write(chunk)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()

    # ── Broadcast EQ + kompressor ────────────────────────────
    base = filename[:-4] if filename.endswith(".mp3") else filename
    enhanced = base + "_fx.mp3"
    final_file = _enhance_audio(filename, enhanced, lang)

    # Davomiylikni aniqlash
    audio = AudioFileClip(final_file)
    dur = audio.duration
    audio.close()
    print(f"   Ovoz: {dur:.1f} sek | FX: {'✓' if final_file != filename else '—'}")
    return final_file, dur, word_timings


# ── Til bo'yicha matnlar ──────────────────────────────────────
_LANG_LABELS = {
    "uz": {"brand": "1KUN",  "on_air": "— EFIRDA —",  "subscribe": "OBUNA BO'LING  ", "like": "LIKE BOSING  ", "handle": "@birkunday",    "outro_line1": "Siz bilan 1 Kun bo'ldi.",   "outro_line2": "Kelgusi yangiliklarda ko'rishamiz."},
    "ru": {"brand": "1ДЕНЬ", "on_air": "— В ЭФИРЕ —", "subscribe": "ПОДПИСАТЬСЯ  ",  "like": "ЛАЙК  ",        "handle": "@birkunday_ru", "outro_line1": "Это был 1ДЕНЬ Global.",     "outro_line2": "До следующих новостей."},
    "en": {"brand": "1DAY",  "on_air": "— ON AIR —",  "subscribe": "SUBSCRIBE  ",    "like": "LIKE  ",         "handle": "@birkunday_en", "outro_line1": "That was 1DAY Global.",      "outro_line2": "Stay tuned for more."},
}

def _ll(lang, key):
    """Til bo'yicha label olish"""
    return _LANG_LABELS.get(lang, _LANG_LABELS["uz"]).get(key, "")


# ── HUD overlay (yuqori panel + pastki panel) ─────────────────
def make_hud(sarlavha, daraja, location, sana_vaqt, w, h, lang="uz"):
    """Shaffof HUD overlay — rasm ustiga qo'yiladi"""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fonts = get_fonts()

    # ── Yuqori panel — SANA + geolokatsiya ───────────────────
    draw.rectangle([(0,0),(w,5)], fill=C_RED)
    draw.rectangle([(0,5),(w,50)], fill=(8,14,28,220))

    # Sana tepada (chap, qizil fon) — faqat sana, soat yo'q
    sana = datetime.now().strftime("%d.%m.%Y")
    draw.rectangle([(0,5),(115,50)], fill=C_RED)
    draw.rectangle([(115,5),(121,50)], fill=C_YELLOW)
    sw = tw(draw, sana, font=fonts["ticker"])
    draw.text(((115-sw)//2, 15), sana, font=fonts["ticker"], fill=C_WHITE)

    # Geolokatsiya tepada markazda
    if location:
        loc = f"  {location}"
        ltw = tw(draw, loc, font=fonts["small"])
        draw.text(((w - ltw)//2, 16), loc, font=fonts["small"], fill=C_WHITE)

    # O'ng tomonda vaqt
    vtw = tw(draw, sana_vaqt, font=fonts["small"])
    draw.text((w - vtw - 12, 16), sana_vaqt, font=fonts["small"], fill=C_GOLD)

    # ── O'ng uchburchak ribbon ────────────────────────────────
    size = 85
    rc = C_RED if daraja == "muhim" else C_BLUE
    draw.polygon([(w-size,5),(w,5),(w,5+size)], fill=rc)

    # ── Pastki panel — sarlavha + kanal nomi ─────────────────
    panel_h = 100
    py = h - panel_h
    draw.rectangle([(0,py),(w,h)], fill=(8,14,28,235))
    draw.rectangle([(0,py),(w,py+3)], fill=C_RED)

    # Chap pastda: brand fon + matn
    draw.rectangle([(0,py+3),(105,h)], fill=(10,18,38))
    draw.rectangle([(105,py+3),(111,h)], fill=C_GOLD)
    brand = _ll(lang, "brand")
    lw = tw(draw, brand, font=fonts["brand"])
    bx = (105 - lw) // 2
    by = py + (panel_h - 26) // 2 + 3
    draw.text((bx, by), brand, font=fonts["brand"], fill=C_GOLD)

    # Sarlavha — katta, panel ichida, 2-3 qator
    max_w = w - 130  # 1KUN (110px) + bo'shliq
    # Har qator uchun optimal kenglik topish
    test_font = fonts["title"]
    s_lines = textwrap.wrap(sarlavha, width=46)[:3]
    # Agar 1 qator sig'sa — katta font
    if len(s_lines) == 1 and tw(draw, s_lines[0], test_font) <= max_w:
        line_h = 46
        f_use = fonts["title"]
    elif len(s_lines) <= 2:
        line_h = 42
        f_use = fonts["title"]
        s_lines = textwrap.wrap(sarlavha, width=44)[:2]
    else:
        line_h = 36
        f_use = fonts["ticker"]
        s_lines = textwrap.wrap(sarlavha, width=55)[:3]
    total_sh = len(s_lines) * line_h
    sy = py + (panel_h - total_sh) // 2 + 3
    for line in s_lines:
        draw.text((119, sy), line, font=f_use, fill=C_WHITE)
        sy += line_h

    path = f"{TEMP_DIR}/hud_overlay.png"
    img.save(path)
    return path


# ── Intro frame ───────────────────────────────────────────────
def make_intro_frame(lang="uz"):
    img = Image.new("RGB", (VIDEO_W, VIDEO_H), C_BG)
    draw = ImageDraw.Draw(img)
    fonts = get_fonts()

    for i in range(0, VIDEO_W+VIDEO_H, 80):
        draw.line([(i,0),(0,i)], fill=(12,22,48), width=1)

    draw.rectangle([(0,0),(VIDEO_W,6)], fill=C_GOLD)
    draw.rectangle([(0,VIDEO_H-6),(VIDEO_W,VIDEO_H)], fill=C_GOLD)
    draw.rectangle([(0,215),(VIDEO_W,490)], fill=(10,18,38))
    draw.rectangle([(0,215),(VIDEO_W,223)], fill=C_GOLD)

    t = _ll(lang, "brand")
    w = tw(draw, t, fonts["xl"])
    draw.text(((VIDEO_W-w)//2, 228), t, font=fonts["xl"], fill=C_GOLD)

    t2 = "GLOBAL NEWS"
    w2 = tw(draw, t2, fonts["lg"])
    draw.text(((VIDEO_W-w2)//2, 340), t2, font=fonts["lg"], fill=C_WHITE)

    t3 = _ll(lang, "on_air")
    w3 = tw(draw, t3, fonts["small"])
    draw.text(((VIDEO_W-w3)//2, 578), t3, font=fonts["small"], fill=C_GRAY)

    path = f"{TEMP_DIR}/frame_intro_{lang}.png"
    img.save(path)
    return path


# ── Outro frame ───────────────────────────────────────────────
def make_outro_frame(lang="uz"):
    img = Image.new("RGB", (VIDEO_W, VIDEO_H), C_BG)
    draw = ImageDraw.Draw(img)
    fonts = get_fonts()

    for i in range(0, VIDEO_W+VIDEO_H, 80):
        draw.line([(i,0),(0,i)], fill=(12,22,48), width=1)

    draw.rectangle([(0,0),(VIDEO_W,6)], fill=C_GOLD)
    draw.rectangle([(0,VIDEO_H-6),(VIDEO_W,VIDEO_H)], fill=C_GOLD)
    draw.rectangle([(80,155),(VIDEO_W-80,535)], fill=(10,18,38))
    draw.rectangle([(80,155),(VIDEO_W-80,163)], fill=C_RED)

    t = _ll(lang, "brand")
    w = tw(draw, t, fonts["xl"])
    draw.text(((VIDEO_W-w)//2, 170), t, font=fonts["xl"], fill=C_GOLD)

    t2 = _ll(lang, "outro_line1")
    w2 = tw(draw, t2, fonts["lg"])
    draw.text(((VIDEO_W-w2)//2, 280), t2, font=fonts["lg"], fill=C_WHITE)

    t3 = _ll(lang, "outro_line2")
    w3 = tw(draw, t3, fonts["small"])
    draw.text(((VIDEO_W-w3)//2, 358), t3, font=fonts["small"], fill=C_GRAY)

    # Like tugmasi
    draw.rounded_rectangle([(175,408),(520,460)], radius=18, fill=C_RED)
    tl = _ll(lang, "like")
    wl = tw(draw, tl, fonts["ticker"])
    draw.text((175 + (345-wl)//2, 418), tl, font=fonts["ticker"], fill=C_WHITE)

    # Obuna
    draw.rounded_rectangle([(760,408),(1105,460)], radius=18, fill=C_RED)
    ts2 = _ll(lang, "subscribe")
    ws2 = tw(draw, ts2, fonts["ticker"])
    draw.text((760 + (345-ws2)//2, 418), ts2, font=fonts["ticker"], fill=C_WHITE)

    t4 = _ll(lang, "handle")
    w4 = tw(draw, t4, fonts["ticker"])
    draw.text(((VIDEO_W-w4)//2, 482), t4, font=fonts["ticker"], fill=C_GOLD)

    path = f"{TEMP_DIR}/frame_outro_{lang}.png"
    img.save(path)
    return path


# ── Rasmdan video klip (HUD bilan) ───────────────────────────
def make_slide_clip(img_path, hud_path, duration, effect_idx=0):
    """Rasmni HUD bilan birlashtirish + Ken Burns effekti"""
    import numpy as np
    from moviepy import VideoClip

    bg_orig = Image.open(img_path).convert("RGB")
    hud_img = Image.open(hud_path).convert("RGBA")

    # Effektlar: zoom, pan, diagonal, pulse
    effects = ["zoom_in", "zoom_out", "pan_right", "pan_left",
               "diagonal_tl", "diagonal_br", "pulse", "pan_up"]
    effect = effects[effect_idx % len(effects)]

    def make_frame(t):
        import math
        progress = t / max(duration, 0.001)
        ease = progress * (2 - progress)  # ease-out

        if effect == "zoom_in":
            scale = 1.0 + 0.18 * ease
            ox, oy = 0, 0
        elif effect == "zoom_out":
            scale = 1.18 - 0.18 * ease
            ox, oy = 0, 0
        elif effect == "pan_right":
            scale = 1.12
            ox = ease
            oy = 0
        elif effect == "pan_left":
            scale = 1.12
            ox = 1 - ease
            oy = 0
        elif effect == "diagonal_tl":
            scale = 1.15
            ox = ease * 0.5
            oy = ease * 0.5
        elif effect == "diagonal_br":
            scale = 1.15
            ox = 1 - ease * 0.5
            oy = 1 - ease * 0.5
        elif effect == "pulse":
            scale = 1.05 + 0.08 * math.sin(progress * math.pi)
            ox, oy = 0, 0
        else:  # pan_up
            scale = 1.12
            ox = 0
            oy = 1 - ease

        new_w = int(VIDEO_W * scale)
        new_h = int(VIDEO_H * scale)
        bg = bg_orig.resize((new_w, new_h), Image.LANCZOS)

        max_dx = new_w - VIDEO_W
        max_dy = new_h - VIDEO_H
        x = int(max_dx * ox) if max_dx > 0 else 0
        y = int(max_dy * oy) if max_dy > 0 else 0

        bg = bg.crop((x, y, x + VIDEO_W, y + VIDEO_H))
        bg_rgba = bg.convert("RGBA")
        bg_rgba.paste(hud_img, (0, 0), hud_img)
        result = bg_rgba.convert("RGB")
        return np.array(result)

    clip = VideoClip(make_frame, duration=duration)
    clip = clip.with_fps(FPS)
    return clip


# ── Video build (ffmpeg subprocess — xotira samarali) ────────
def build_video(voice_file, voice_dur, sarlavha, daraja, location,
                yt_clips, output_file, word_timings=None, lang="uz"):
    """Video kliplardan tayyor video yaratish — ffmpeg subprocess.
    Moviepy/numpy ishlatilmaydi: static frame va MemoryError yo'q.
    Klip ovozi 8% intershum + narration voice amix."""
    if not yt_clips:
        print("   Video kliplar yo'q — o'tkazildi")
        return None

    # Minimum 2 daqiqa
    target_dur = max(voice_dur, 120.0)
    print(f"   Video: {len(yt_clips)} ta klip, maqsad {target_dur:.0f}s...")

    sana_vaqt = datetime.now().strftime("%H:%M")
    hud_path  = make_hud(sarlavha, daraja, location, sana_vaqt, VIDEO_W, VIDEO_H, lang=lang)

    # ── Har klipning davomiyligini aniqlash ──────────────────────
    clip_durs = [min(_clip_duration(cp), 40.0) for cp in yt_clips]

    # ── Kliplarni looplash — jami target_dur ga yetkazish ────────
    # Har bir klip maksimum 3 marta takrorlanadi (zerikarlilikni kamaytirish uchun)
    MAX_REPEAT = 3
    looped     = []
    total      = 0.0
    i          = 0
    clip_uses  = [0] * len(yt_clips)
    while total < target_dur and i < len(yt_clips) * MAX_REPEAT * 2:
        idx = i % len(yt_clips)
        if clip_uses[idx] < MAX_REPEAT:
            looped.append(yt_clips[idx])
            clip_uses[idx] += 1
            total += clip_durs[idx]
        i += 1
        # Agar barcha kliplar MAX_REPEAT ga yetsa — to'xtatish
        if all(u >= MAX_REPEAT for u in clip_uses):
            break

    per_clip = target_dur / max(len(looped), 1)
    per_clip = max(4.0, min(per_clip, 40.0))
    print(f"   {len(looped)} segment × {per_clip:.1f}s = {len(looped)*per_clip:.0f}s")

    # ── Har klipni ffmpeg bilan qayta ishlash ────────────────────
    processed = []
    for idx, clip_path in enumerate(looped):
        out_seg = os.path.join(TEMP_DIR, f"seg_{idx:03d}.mp4")
        try:
            make_video_segment(clip_path, hud_path, per_clip, out_seg, lang=lang)
            if os.path.exists(out_seg) and os.path.getsize(out_seg) > 10_000:
                processed.append(out_seg)
                print(f"  Klip {idx+1}/{len(looped)}: {per_clip:.1f}s ✓ {os.path.basename(clip_path)}")
            else:
                print(f"  Klip {idx+1}: chiqish fayli bo'sh")
        except subprocess.TimeoutExpired:
            print(f"  Klip {idx+1}: timeout (180s)")
        except Exception as e:
            print(f"  Klip {idx+1} xato: {e}")

    if not processed:
        print("   Hech bir klip qayta ishlanmadi")
        return None

    # ── Concat demuxer ───────────────────────────────────────────
    concat_txt = os.path.join(TEMP_DIR, "concat_list.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in processed:
            # Mutlaq yo'l + '\\' → '/' (ffmpeg Windows uchun)
            abs_p = os.path.abspath(p).replace(os.sep, '/')
            f.write(f"file '{abs_p}'\n")

    merged = os.path.join(TEMP_DIR, "merged_video.mp4")
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", concat_txt, "-c", "copy", merged],
        capture_output=True, timeout=300)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace")[-400:]
        print(f"   Concat xato:\n{err}")
        return None
    print(f"   Concat: {len(processed)} segment → {os.path.basename(merged)}")

    # ── Audio: klip intershum + voice (fon musiqasi o'chirilgan) ──
    music_file = None   # Foydalanuvchi so'rovi: fon musiqasi kerak emas
    has_music  = False

    try:
        ap = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", merged],
            capture_output=True, text=True, timeout=5)
        merged_has_audio = "audio" in ap.stdout
    except Exception:
        merged_has_audio = False

    if merged_has_audio and has_music:
        # 3 kanal: klip intershum (8%) + voice + musiqa (6%)
        audio_filt = (
            "[0:a]volume=1.0[bg];"
            "[1:a]volume=1.0[voice];"
            "[2:a]volume=0.06[mus];"
            "[bg][voice][mus]amix=inputs=3:duration=first:dropout_transition=3[aout]"
        )
        cmd_final = [
            "ffmpeg", "-y",
            "-i", merged,
            "-i", voice_file,
            "-stream_loop", "-1", "-i", music_file,   # musiqa loop
            "-filter_complex", audio_filt,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", output_file,
        ]
    elif merged_has_audio:
        # Musiqa yo'q: klip intershum + voice
        audio_filt = (
            "[0:a]volume=1.0[bg];"
            "[1:a]volume=1.0[voice];"
            "[bg][voice]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd_final = [
            "ffmpeg", "-y",
            "-i", merged, "-i", voice_file,
            "-filter_complex", audio_filt,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", output_file,
        ]
    elif has_music:
        # Klipda audio yo'q: voice + musiqa
        audio_filt = (
            "[0:a]volume=1.0[voice];"
            "[1:a]volume=0.06[mus];"
            "[voice][mus]amix=inputs=2:duration=first:dropout_transition=3[aout]"
        )
        cmd_final = [
            "ffmpeg", "-y",
            "-i", merged, "-i", voice_file,
            "-stream_loop", "-1", "-i", music_file,
            "-filter_complex", audio_filt,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", output_file,
        ]
    else:
        # Faqat voice
        cmd_final = [
            "ffmpeg", "-y",
            "-i", merged, "-i", voice_file,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", output_file,
        ]
    print(f"   Audio: intershum={'✓' if merged_has_audio else '—'} "
          f"| musiqa={'✓' if has_music else '—'}")

    r = subprocess.run(cmd_final, capture_output=True, timeout=300)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace")[-400:]
        print(f"   Audio mix xato:\n{err}")
        # Fallback: video + voice faqat
        r2 = subprocess.run(
            ["ffmpeg", "-y",
             "-i", merged, "-i", voice_file,
             "-map", "0:v", "-map", "1:a",
             "-c:v", "copy", "-c:a", "aac", "-shortest",
             output_file],
            capture_output=True, timeout=300)
        if r2.returncode != 0:
            print(f"   Fallback ham xato: "
                  f"{r2.stderr.decode('utf-8', errors='replace')[-200:]}")
            return None

    if os.path.exists(output_file):
        sz = os.path.getsize(output_file) // 1024 // 1024
        print(f"   ✅ {output_file} ({sz} MB, {VIDEO_W}x{VIDEO_H})")
        return output_file
    return None


# ── YouTube auth ──────────────────────────────────────────────
def youtube_auth():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            print("   Token yangilandi")
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS, SCOPES)
            creds = flow.run_local_server(
                port=0, prompt="consent", access_type="offline")
            print("   Token saqlandi!")
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload_to_youtube(youtube, video_file, title, description, tags):
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:15],
            "categoryId": "25",
        },
        "status": {"privacyStatus": "public",
                   "selfDeclaredMadeForKids": False}
    }
    media = MediaFileUpload(
        video_file, chunksize=-1, resumable=True, mimetype="video/mp4")
    req = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media)
    print("   Yuklanmoqda...")
    response = None
    while response is None:
        status, response = req.next_chunk()
        if status:
            print(f"     {int(status.progress()*100)}%...")
    video_id = response["id"]
    print(f"   https://youtu.be/{video_id}")
    return video_id


# ── Pipeline ──────────────────────────────────────────────────
def youtube_pipeline(data):
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = "".join(c if c.isalnum() else "_"
                   for c in data.get("sarlavha", "yangilik")[:20])

    lang = data.get("lang", "uz")
    audio_file = f"{TEMP_DIR}/{ts}_{lang}_voice.mp3"
    video_file = f"{OUTPUT_DIR}/{ts}_{lang}_{slug}.mp4"

    sarlavha    = data.get("sarlavha", "YANGILIK")
    jumla1      = data.get("jumla1", "")
    jumla2      = data.get("jumla2", "")
    daraja      = data.get("daraja", "xabar").lower()
    keywords_en = data.get("keywords_en", ["news"])
    keywords_ru = data.get("keywords_ru", [])
    location    = data.get("location", data.get("location_uz", ""))
    script      = data.get("youtube_script_latin", "")

    print(f"\n 1KUN: {sarlavha[:45]}...")

    yt_clips = data.get("yt_clips", [])
    if not yt_clips:
        print("   Video kliplar yo'q — o'tkazildi")
        return None

    print(f"  1  Video kliplar: {len(yt_clips)} ta")

    # 2. Matn uzaytirish — ~150 so'z/daqiqa, 2.5 daqiqa = 375 so'z
    print("  2  Matn tekshirilmoqda...")
    lang = data.get("lang", "uz")
    script = extend_script(script, lang=lang, target_words=375)
    word_count = len(script.split())
    print(f"     So'zlar: {word_count} (~{word_count//150:.1f} daqiqa)")

    # 3. Ovoz — daraja bo'yicha ovoz tanlash
    print("  3  Ovoz...")
    voice_file_out, voice_dur, word_timings = make_audio(
        script, audio_file, lang=lang, daraja=daraja)
    # FX versiyasi qaytarilgan bo'lishi mumkin
    audio_file = voice_file_out
    print(f"     Ovoz uzunligi: {voice_dur:.1f}s ({voice_dur/60:.1f} daqiqa)")

    # 4. Video
    print("  4  Video...")
    result = build_video(audio_file, voice_dur, sarlavha,
                         daraja, location, yt_clips, video_file, word_timings,
                         lang=lang)
    if result is None:
        return None

    # 4. YouTube yoki lokal
    youtube_enabled = os.getenv("YOUTUBE_ENABLED", "false").lower() == "true"
    if youtube_enabled:
        print("  4  YouTube...")
        yt      = youtube_auth()
        brand   = _ll(lang, "brand")
        handle  = _ll(lang, "handle")
        _yt_cta = {
            "uz": f"Yangiliklarga obuna bo'ling!\nLike bosing |\n{handle} | {brand} Global",
            "ru": f"Подпишитесь на новости!\nЛайк |\n{handle} | {brand} Global",
            "en": f"Subscribe for more news!\nLike |\n{handle} | {brand} Global",
        }
        desc = (f"{jumla1}\n\n{jumla2}\n\n" + _yt_cta.get(lang, _yt_cta["uz"]))
        _base_tags = {"uz": ["yangilik","uzbek","1kun"], "ru": ["новости","1день"], "en": ["news","1day"]}
        tags = ([t.replace("#","") for t in data.get("hashtaglar","").split()]
                + keywords_en + _base_tags.get(lang, ["news"]))
        return upload_to_youtube(yt, video_file, sarlavha, desc, tags)
    else:
        print(f"   Lokal: {video_file}")
        return video_file
