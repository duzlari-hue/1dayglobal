"""
daily_shorts.py — "Бугунги 5 та муҳим янгилик" Shorts generator

Yangi arxitektura:
  • Har bir yangilik uchun Pexels dan mavzuga mos RASM qidiriladi
  • PIL orqali raqam + sarlavha overlay qilinadi
  • Diktor: tartib raqami + sarlavha o'qiydi
  • xfade tranzitsiyalar (slide, wipe)
  • Fon musiqasi (avto-yaratiladi yoki assets/background.mp3)
  • Jami: ~63 sek (intro 3s + 5×12s)
"""

import os, sys, json, glob, asyncio, subprocess, shutil, textwrap, requests, re, hashlib
from datetime import datetime, date

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(".env")

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import edge_tts

from config import OUTPUT_DIR, TEMP_DIR, VOICES, QUEUE_DIR, YOUTUBE_PLAYLIST

SW, SH    = 1080, 1920
SEG_DUR   = 12        # Har bir yangilik segmenti (soniya)
INTRO_DUR = 3         # Intro davomiyligi (soniya)
TRANS_DUR = 0.5       # Tranzitsiya davomiyligi (soniya)

C_BG    = (5,  10, 22)
C_GOLD  = (240, 165, 0)
C_RED   = (204,   0, 0)
C_WHITE = (255, 255, 255)
C_YELLOW= (255, 210, 0)
C_DARK  = (20,  25, 40)

_HERE      = os.path.dirname(os.path.abspath(__file__))
MUSIC_PATH = os.path.join(_HERE, "assets", "background.mp3")
MUSIC_VOL  = 0.22

# Ишлатилган хабарларни кузатиш
_USED_DIR  = os.path.join(OUTPUT_DIR, "daily_used")

# Tranzitsiya turlari (intro→1, 1→2, ...)
TRANSITIONS = ["slideup", "slideleft", "slideright", "slideleft", "slideright"]


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
# Til tekshiruvi
# ─────────────────────────────────────────────────────────────
_CYR = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯЎҚҒҲ"

def _is_cyr(text):
    a = [c for c in text if c.isalpha()]
    return bool(a) and sum(1 for c in a if c in _CYR)/len(a) >= 0.5

def _is_latin(text):
    a = [c for c in text if c.isalpha()]
    return bool(a) and sum(1 for c in a if c.isascii())/len(a) >= 0.7

def _jumla_ok(jumla, lang):
    if not jumla or len(jumla.strip()) < 10: return False
    return _is_cyr(jumla) if lang in ("uz","ru") else _is_latin(jumla)


# ─────────────────────────────────────────────────────────────
# Termin tuzatish (eski JSON fayllar uchun)
# ─────────────────────────────────────────────────────────────
_TERM_FIX = [
    ("еврейлар","яҳудийлар"), ("Еврейлар","Яҳудийлар"),
    ("евреи","яҳудийлар"),    ("Евреи","Яҳудийлар"),
    ("еврей","яҳудий"),       ("Еврей","Яҳудий"),
    ("оташкесим","ўт очишни тўхтатиш"), ("Оташкесим","Ўт очишни тўхтатиш"),
    ("оташбас","ўт очишни тўхтатиш"),   ("Оташбас","Ўт очишни тўхтатиш"),
    ("Израил","Исроил"), ("Израиль","Исроил"),
]
def _fix_terms(text):
    if not text: return text
    for w, r in _TERM_FIX:
        if w in text: text = text.replace(w, r)
    # AI placeholder larni tozalash: {musiqa}, {sarlavha}, {yangilik} va h.k.
    text = re.sub(r'\{[^}]{1,40}\}', '', text).strip()
    return text


# ─────────────────────────────────────────────────────────────
# Uzbek Kirill → Lotin (TTS uchun: uz-UZ-MadinaNeural Latin kutadi)
# ─────────────────────────────────────────────────────────────
_CYR2LAT = {
    'А':'A','а':'a','Б':'B','б':'b','В':'V','в':'v','Г':'G','г':'g',
    'Ғ':"G'", 'ғ':"g'", 'Д':'D','д':'d','Е':'E','е':'e','Ё':'Yo','ё':'yo',
    'Ж':'J','ж':'j','З':'Z','з':'z','И':'I','и':'i','Й':'Y','й':'y',
    'К':'K','к':'k','Қ':'Q','қ':'q','Л':'L','л':'l','М':'M','м':'m',
    'Н':'N','н':'n','О':'O','о':'o','П':'P','п':'p','Р':'R','р':'r',
    'С':'S','с':'s','Т':'T','т':'t','У':'U','у':'u','Ф':'F','ф':'f',
    'Х':'X','х':'x','Ҳ':'H','ҳ':'h','Ц':'Ts','ц':'ts','Ч':'Ch','ч':'ch',
    'Ш':'Sh','ш':'sh','Щ':'Sh','щ':'sh','Ъ':"'",'ъ':"'",'Ы':'I','ы':'i',
    'Ь':''  ,'ь':''  ,'Э':'E','э':'e','Ю':'Yu','ю':'yu','Я':'Ya','я':'ya',
    'Ў':"O'",'ў':"o'",
}

def _cyr2lat_uz(text: str) -> str:
    """Uzbek Kirill matnni Lotin yozuviga o'tkazish (TTS uchun)."""
    if not text: return text
    result = []
    for ch in text:
        result.append(_CYR2LAT.get(ch, ch))
    return ''.join(result)


# ─────────────────────────────────────────────────────────────
# O'xshash yangiliklar tekshiruvi
# ─────────────────────────────────────────────────────────────
def _news_similar(s1: str, s2: str, threshold=0.30) -> bool:
    """
    Ikkita sarlavha o'xshash mavzuda bo'lsa True.
    Kalit so'zlar (4+ harf) kesishishi threshold dan oshsa — o'xshash.
    """
    w1 = {w.lower() for w in re.split(r'\W+', s1) if len(w) >= 4}
    w2 = {w.lower() for w in re.split(r'\W+', s2) if len(w) >= 4}
    if not w1 or not w2:
        return False
    overlap = len(w1 & w2) / min(len(w1), len(w2))
    return overlap >= threshold


# ─────────────────────────────────────────────────────────────
# Ишлатилган хабарларни кузатиш (kun bo'yicha)
# ─────────────────────────────────────────────────────────────
def _used_file(lang: str) -> str:
    today = date.today().strftime("%Y%m%d")
    os.makedirs(_USED_DIR, exist_ok=True)
    return os.path.join(_USED_DIR, f"{today}_{lang}.json")

def _load_used(lang: str) -> set:
    f = _used_file(lang)
    if not os.path.exists(f):
        return set()
    try:
        return set(json.load(open(f, encoding="utf-8")))
    except Exception:
        return set()

def _save_used(lang: str, news_list: list):
    """Yangiliklar sarlavhasining hash'ini saqlash."""
    existing = _load_used(lang)
    for n in news_list:
        key = hashlib.md5(n["sarlavha"].encode()).hexdigest()
        existing.add(key)
    with open(_used_file(lang), "w", encoding="utf-8") as fp:
        json.dump(list(existing), fp, ensure_ascii=False)

def _is_used(sarlavha: str, used_set: set) -> bool:
    key = hashlib.md5(sarlavha.encode()).hexdigest()
    return key in used_set


# ─────────────────────────────────────────────────────────────
# Maqolaning OG rasmi (eng aniq manba)
# ─────────────────────────────────────────────────────────────
def fetch_article_image(article_url: str, out_path: str) -> bool:
    """
    Yangilik maqolasining og:image meta tegidan rasm yuklab olish.
    Bu Pexels dan aniqroq — aynan o'sha voqeaning rasmi bo'ladi.
    """
    if not article_url or not article_url.startswith("http"):
        return False
    try:
        hdrs = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }
        resp = requests.get(article_url, headers=hdrs, timeout=12,
                            allow_redirects=True)
        if resp.status_code != 200:
            return False
        html = resp.text

        # og:image ni topish (ikkita format bilan)
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
# Pexels rasm qidirish
# ─────────────────────────────────────────────────────────────
def fetch_news_photo(search_queries, keywords_en, out_path):
    """
    Pexels API orqali mavzuga mos portret rasm yuklab olish.
    search_queries → keywords_en tartibida sinab ko'riladi.
    """
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        return False

    headers = {"Authorization": api_key}
    # Eng aniq so'rovlardan boshlab; "2026" qo'shib yangi foto topish
    base_q  = list(search_queries[:3]) + [" ".join(keywords_en[:4])]
    queries = []
    for q in base_q:
        q = q.strip()
        if not q: continue
        queries.append(q)                      # Asl so'rov
        if "2026" not in q and "2025" not in q:
            queries.append(q + " 2026")        # Yil bilan variant
    queries = list(dict.fromkeys(queries))[:6] # Takrorsiz, max 6

    for q in queries:
        try:
            url = (
                f"https://api.pexels.com/v1/search"
                f"?query={requests.utils.quote(q)}"
                f"&per_page=8&orientation=portrait"
            )
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code != 200:
                continue
            photos = resp.json().get("photos", [])
            if not photos:
                continue

            # Eng katta portret rasmni tanlash
            best = None
            best_size = 0
            for ph in photos:
                src = ph.get("src", {})
                img_url = src.get("large2x") or src.get("large") or src.get("medium","")
                if not img_url:
                    continue
                h = ph.get("height", 0)
                w = ph.get("width", 1)
                ratio = h / w
                # Portret (baland) rasmlarni afzal ko'rish
                score = h * ratio
                if score > best_size:
                    best_size = score
                    best = img_url

            if not best:
                continue

            ir = requests.get(best, timeout=20)
            if ir.status_code != 200:
                continue
            with open(out_path, "wb") as f:
                f.write(ir.content)
            print(f"     📸 Pexels: {q[:50]}")
            return True

        except Exception as e:
            print(f"     Pexels xato ({q[:30]}): {e}")
            continue

    return False


# ─────────────────────────────────────────────────────────────
# Karta: to'liq rasm fon + overlay
# ─────────────────────────────────────────────────────────────
DARAJA_COLOR = {"muhim":(220,30,30), "tezkor":(240,140,0), "xabar":(0,100,200)}

def make_card_with_bg(number, sarlavha, daraja, lang, bg_path, out_path):
    """
    9:16 to'liq karta:
    - Fon: Pexels rasm (blur yuqori qism, pastda qoraytirilgan zona)
    - Raqam badge (yuqorida)
    - Sarlavha (katta, pastda)
    """
    accent = DARAJA_COLOR.get(daraja, (0,100,200))

    # Fon rasmi yuklash va crop
    if bg_path and os.path.exists(bg_path):
        try:
            bg = Image.open(bg_path).convert("RGB")
            bw, bh = bg.size
            tgt_r = SW / SH
            src_r = bw / bh
            if src_r > tgt_r:
                nw = int(bh * tgt_r)
                x = (bw - nw) // 2
                bg = bg.crop((x, 0, x+nw, bh))
            else:
                nh = int(bw / tgt_r)
                y = (bh - nh) // 2
                bg = bg.crop((0, y, bw, y+nh))
            bg = bg.resize((SW, SH), Image.LANCZOS)
        except Exception:
            bg = Image.new("RGB", (SW, SH), C_BG)
    else:
        bg = Image.new("RGB", (SW, SH), C_BG)

    # Yuqori qism blur (raqam badge sohasi)
    top_blur = bg.crop((0, 0, SW, 420))
    top_blur = top_blur.filter(ImageFilter.GaussianBlur(radius=12))
    bg.paste(top_blur, (0, 0))

    canvas = bg.copy()
    draw   = ImageDraw.Draw(canvas)

    # Yuqori qoraytirilgan gradient (raqam uchun joy)
    for y in range(0, 350):
        alpha_f = 0.75 * (1 - y/350)
        ov = Image.new("RGBA", (SW, 1), (0,0,0, int(255*alpha_f)))
        canvas.paste(ov, (0, y), ov)

    # Pastki gradient (sarlavha uchun joy)
    grad_h = 800
    for y in range(SH - grad_h, SH):
        alpha_f = 0.92 * (y - (SH-grad_h)) / grad_h
        ov = Image.new("RGBA", (SW, 1), (5,10,22, int(255*alpha_f)))
        canvas.paste(ov, (0, y), ov)

    draw = ImageDraw.Draw(canvas)

    # Chap aksent chiziq
    draw.rectangle([(0,0),(10,SH)], fill=(*accent, 230))
    # Pastki oltin chiziq
    draw.rectangle([(0,SH-10),(SW,SH)], fill=(*C_GOLD,255))
    # Yuqori oltin chiziq
    draw.rectangle([(0,0),(SW,8)], fill=(*C_GOLD,200))

    # Raqam badge
    bx, by = SW//2, 150
    r = 75
    draw.ellipse([(bx-r,by-r),(bx+r,by+r)],
                 fill=(*accent,240), outline=(*C_GOLD,255), width=4)
    draw.text((bx, by), str(number), font=_font(90), fill=C_WHITE, anchor="mm")

    # Daraja label
    dlabel = {"muhim":"MUHIM","tezkor":"TEZKOR","xabar":"YANGILIK"}
    draw.text((bx, by+100), dlabel.get(daraja,"YANGILIK"),
              font=_font(30,False), fill=(*C_GOLD,210), anchor="mm")

    # Sarlavha (katta, pastda)
    wrapped = textwrap.wrap(sarlavha, width=20)[:5]
    f_title = _font(66)
    ty = SH - 680
    for line in wrapped:
        draw.text((SW//2+3, ty+3), line, font=f_title, fill=(0,0,0,180), anchor="mt")
        draw.text((SW//2, ty),      line, font=f_title, fill=C_WHITE,   anchor="mt")
        ty += 76

    # Brend
    brand = {"uz":"1КУН GLOBAL","ru":"1ДЕНЬ GLOBAL","en":"1DAY GLOBAL"}.get(lang,"1KUN")
    draw.text((SW//2, SH-28), brand, font=_font(34), fill=(*C_GOLD,220), anchor="mb")

    canvas.save(out_path, "JPEG", quality=93)
    return out_path


def make_intro_card(lang, out_path):
    img  = Image.new("RGB", (SW, SH), C_BG)
    draw = ImageDraw.Draw(img)
    for y in range(SH):
        t = y/SH
        r = int(C_BG[0]*(1-t)+C_DARK[0]*t)
        g = int(C_BG[1]*(1-t)+C_DARK[1]*t)
        b = int(C_BG[2]*(1-t)+C_DARK[2]*t)
        draw.line([(0,y),(SW,y)], fill=(r,g,b))

    draw.rectangle([(0,0),(10,SH)],      fill=C_RED)
    draw.rectangle([(SW-10,0),(SW,SH)],  fill=C_GOLD)
    draw.rectangle([(0,SH-10),(SW,SH)],  fill=C_GOLD)
    draw.rectangle([(0,0),(SW,10)],      fill=C_GOLD)

    brand = {"uz":"1КУН GLOBAL","ru":"1ДЕНЬ GLOBAL","en":"1DAY GLOBAL"}.get(lang,"1KUN")
    draw.text((SW//2,195), brand, font=_font(68), fill=C_GOLD, anchor="mm")

    titles = {
        "uz":["БУГУНГИ","5 ТА МУҲИМ","ЯНГИЛИК"],
        "ru":["СЕГОДНЯ","5 ГЛАВНЫХ","НОВОСТЕЙ"],
        "en":["TODAY'S","TOP 5","NEWS"],
    }
    lines  = titles.get(lang, titles["uz"])
    colors = [C_WHITE, C_YELLOW, C_WHITE]
    sizes  = [76, 118, 76]
    ys     = [SH//2-110, SH//2+20, SH//2+155]
    for line,color,size,y in zip(lines,colors,sizes,ys):
        draw.text((SW//2,y), line, font=_font(size), fill=color, anchor="mm")

    today_str = date.today().strftime("%d.%m.%Y")
    draw.text((SW//2,SH-90), today_str,
              font=_font(38,False), fill=(150,160,180), anchor="mm")
    img.save(out_path, "JPEG", quality=92)


# ─────────────────────────────────────────────────────────────
# Audio
# ─────────────────────────────────────────────────────────────
async def _tts_async(text, voice, rate, out_path):
    comm = edge_tts.Communicate(text, voice, rate=rate)
    await comm.save(out_path)

def make_tts(text, out_path, lang="uz"):
    vcfg  = VOICES.get(lang, VOICES["uz"])["default"]
    try:
        asyncio.run(_tts_async(text, vcfg["voice"], vcfg.get("rate","-5%"), out_path))
    except Exception as e:
        print(f"     ⚠️  TTS xato: {e}")
        return False
    return os.path.exists(out_path)

def pad_audio_to(in_path, out_path, duration):
    subprocess.run([
        "ffmpeg","-y","-i",in_path,
        "-af", f"apad=pad_dur={duration}",
        "-t", str(duration),
        "-c:a","aac","-b:a","96k", out_path
    ], capture_output=True, timeout=30)
    return os.path.exists(out_path)

def make_silence(duration, out_path):
    subprocess.run([
        "ffmpeg","-y","-f","lavfi",
        "-i","anullsrc=r=44100:cl=mono",
        "-t",str(duration),"-c:a","aac","-b:a","64k",out_path
    ], capture_output=True)


# ─────────────────────────────────────────────────────────────
# TTS matni: tartib raqami + SARLAVHA
# ─────────────────────────────────────────────────────────────
def build_tts_text(number, sarlavha, lang):
    ordinals = {
        "uz": ["Birinchi","Ikkinchi","Uchinchi","To'rtinchi","Beshinchi"],
        "ru": ["Первая","Вторая","Третья","Четвёртая","Пятая"],
        "en": ["First","Second","Third","Fourth","Fifth"],
    }
    ord_word = ordinals.get(lang, ordinals["uz"])[number-1]
    sfx      = {"uz":" yangilik.","ru":" новость.","en":" news."}
    text     = ord_word + sfx.get(lang," yangilik.")

    if sarlavha:
        if lang == "uz":
            # uz-UZ-MadinaNeural LOTIN skriptni kutadi
            # Kiriллча sarlavha → lotin konvertatsiya
            if _is_cyr(sarlavha):
                tts_sarlavha = _cyr2lat_uz(sarlavha)
            elif _is_latin(sarlavha):
                tts_sarlavha = sarlavha
            else:
                tts_sarlavha = ""
            if tts_sarlavha:
                text += f" {tts_sarlavha}."
        elif lang == "ru":
            if _is_cyr(sarlavha):
                text += f" {sarlavha}."
        elif lang == "en":
            if _is_latin(sarlavha):
                text += f" {sarlavha}."
    return text

def build_intro_text(lang):
    return {
        "uz": "Bugun 5 ta eng muhim yangilik.",
        "ru": "Сегодня пять главных новостей.",
        "en": "Today's top five news stories.",
    }.get(lang, "Bugun 5 ta eng muhim yangilik.")


# ─────────────────────────────────────────────────────────────
# Rasm → Video
# ─────────────────────────────────────────────────────────────
def image_to_video(img_path, audio_path, duration, out_path):
    r = subprocess.run([
        "ffmpeg","-y",
        "-loop","1","-i",img_path,
        "-i",audio_path,
        "-c:v","libx264","-preset","fast","-crf","23",
        "-pix_fmt","yuv420p",
        "-profile:v","high","-level","4.1",
        "-c:a","aac","-b:a","96k",
        "-t",str(duration),
        "-vf",f"scale={SW}:{SH}:force_original_aspect_ratio=decrease,"
              f"pad={SW}:{SH}:(ow-iw)/2:(oh-ih)/2:color=black,"
              f"fps=25",           # Barcha segmentlar bir xil fps bo'lishi shart
        "-shortest",
        out_path
    ], capture_output=True, timeout=60)
    if r.returncode != 0:
        print(r.stderr.decode("utf-8","replace")[-300:])
    return r.returncode == 0 and os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# xfade tranzitsiyalar bilan concat
# ─────────────────────────────────────────────────────────────
def concat_with_transitions(parts, out_path):
    """
    N ta segment → xfade slide tranzitsiyalar → birlashtirilgan video.
    Barcha segmentlar bir xil SEG_DUR (intro: INTRO_DUR).
    """
    n = len(parts)
    if n == 0: return False
    if n == 1:
        shutil.copy(parts[0], out_path)
        return True

    cmd = ["ffmpeg", "-y"]
    for p in parts:
        cmd += ["-i", p]

    # Segmentlar davomiyligi
    durs = [float(INTRO_DUR)] + [float(SEG_DUR)] * (n - 1)

    fc_v = []
    fc_a = []
    prev_v = "[0:v]"
    prev_a = "[0:a]"

    for i in range(1, n):
        trans  = TRANSITIONS[(i-1) % len(TRANSITIONS)]
        offset = sum(durs[:i]) - i * TRANS_DUR
        ov = f"[v{i:02d}]"
        oa = f"[a{i:02d}]"
        fc_v.append(
            f"{prev_v}[{i}:v]xfade=transition={trans}"
            f":duration={TRANS_DUR:.2f}:offset={offset:.2f}{ov}"
        )
        fc_a.append(
            f"{prev_a}[{i}:a]acrossfade=d={TRANS_DUR:.2f}{oa}"
        )
        prev_v = ov
        prev_a = oa

    fc = ";".join(fc_v + fc_a)
    cmd += [
        "-filter_complex", fc,
        "-map", prev_v, "-map", prev_a,
        "-c:v","libx264","-preset","fast","-crf","23",
        "-pix_fmt","yuv420p",          # Windows mediaplayer uchun shart!
        "-profile:v","high","-level","4.1",
        "-c:a","aac","-b:a","128k",
        "-movflags","+faststart",
        out_path
    ]

    print(f"  → xfade concat ({n} segment)...")
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace")[-600:]
        print(f"  ⚠️  xfade xato:\n{err}")
        # Fallback: oddiy concat
        return _simple_concat(parts, out_path)
    return r.returncode == 0 and os.path.exists(out_path)


def _simple_concat(parts, out_path):
    """Fallback: tranzitsiyasiz oddiy concat."""
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S%f")
    txt = os.path.join(TEMP_DIR, f"ds_fc_{ts}.txt")
    with open(txt, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    r = subprocess.run([
        "ffmpeg","-y","-f","concat","-safe","0","-i",txt,
        "-c:v","libx264","-preset","fast","-crf","23",
        "-pix_fmt","yuv420p","-profile:v","high","-level","4.1",
        "-c:a","aac","-b:a","128k",
        "-movflags","+faststart", out_path
    ], capture_output=True, timeout=300)
    try: os.remove(txt)
    except: pass
    return r.returncode == 0 and os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# Fon musiqasi
# ─────────────────────────────────────────────────────────────
def generate_news_beat(duration, out_path):
    """120 BPM ambient beat — mod() yo'q, floor() bilan."""
    expr = (
        "0.45*sin(2*PI*60*t)*exp(0-9*(t-floor(t/0.5)*0.5))"
        "+0.28*sin(2*PI*200*t)*exp(0-14*(t+0.25-floor(t+0.25)))"
        "+0.09*sin(2*PI*4000*t)*exp(0-28*(t-floor(t/0.25)*0.25))"
        "+0.16*sin(2*PI*82*t)*(0.5+0.5*sin(2*PI*0.5*t))"
        "+0.06*sin(2*PI*262*t)*(0.4+0.4*sin(2*PI*0.13*t))"
        "+0.04*sin(2*PI*330*t)*(0.4+0.4*sin(2*PI*0.07*t))"
    )
    r = subprocess.run([
        "ffmpeg","-y","-f","lavfi",
        "-i", f"aevalsrc={expr}:s=44100:c=mono",
        "-t", str(duration),
        "-af","volume=0.85",
        "-c:a","aac","-b:a","128k", out_path
    ], capture_output=True, timeout=60)
    if r.returncode != 0:
        print(r.stderr.decode("utf-8","replace")[-300:])
    return r.returncode == 0 and os.path.exists(out_path)

def add_background_music(video_path, music_path, out_path, vol=MUSIC_VOL):
    # Video davomiyligini aniqlash (ffprobe)
    vid_dur = None
    try:
        pr = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ], capture_output=True, text=True, timeout=30)
        vid_dur = float(pr.stdout.strip())
    except Exception:
        pass

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex",
        f"[1:a]volume={vol}[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=3[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level", "4.1",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
    ]
    # Aniq davomiylik bilan kesish (shortest o'rniga, chunki u xato qisqartirishi mumkin)
    if vid_dur and vid_dur > 1:
        cmd += ["-t", f"{vid_dur:.3f}"]
    else:
        cmd += ["-shortest"]
    cmd.append(out_path)

    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0:
        print(r.stderr.decode("utf-8", "replace")[-300:])
    return r.returncode == 0 and os.path.exists(out_path)


# ─────────────────────────────────────────────────────────────
# YouTube ga yuklash (SEO)
# ─────────────────────────────────────────────────────────────
def upload_daily_to_youtube(video_path: str, news: list, lang: str) -> str | None:
    """
    Daily Shorts videoni YouTube ga SEO bilan yuklash.
    Qaytaradi: video ID yoki None.
    """
    try:
        sys.path.insert(0, _HERE)
        from youtube_maker import youtube_auth
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        print(f"  ⚠️  YouTube import xato: {e}")
        return None

    try:
        youtube = youtube_auth()
    except Exception as e:
        print(f"  ⚠️  YouTube auth xato: {e}")
        return None

    today_str = date.today().strftime("%d.%m.%Y")

    # Sarlavha
    yt_titles = {
        "uz": f"Бугунги 5 та муҳим янгилик | {today_str} #Shorts",
        "ru": f"5 главных новостей дня | {today_str} #Shorts",
        "en": f"Today's Top 5 News | {today_str} #Shorts",
    }
    yt_title = yt_titles.get(lang, yt_titles["uz"])[:100]

    # Tavsif (SEO)
    intro = {
        "uz": "Бугунги энг муҳим янгиликлар қисқача:\n\n",
        "ru": "Главные новости дня:\n\n",
        "en": "Top news stories today:\n\n",
    }.get(lang, "")

    lines = [yt_title.replace(" #Shorts", ""), "", intro.strip(), ""]
    all_kw = []
    for i, n in enumerate(news, 1):
        lines.append(f"{i}. {n['sarlavha']}")
        if n.get("jumla"):
            lines.append(f"   {n['jumla'][:120]}")
        lines.append("")
        all_kw += n.get("keywords_en", [])

    hashtag_map = {
        "uz": "#Yangiliklar #Shorts #BreakingNews #1KunGlobal #Uzbek",
        "ru": "#Новости #Shorts #BreакingNews #1DenGlobal #Russian",
        "en": "#News #Shorts #BreakingNews #1DayGlobal #English",
    }
    lines += ["━" * 30, hashtag_map.get(lang, ""), "#News2026 #Shorts"]
    description = "\n".join(lines)[:4900]

    # Teglar
    tags = list(dict.fromkeys(all_kw))[:15]
    tags += ["Shorts", "News", "BreakingNews", "2026", "Daily News"]

    body = {
        "snippet": {
            "title":           yt_title,
            "description":     description,
            "tags":            tags,
            "categoryId":      "25",        # News & Politics
            "defaultLanguage": lang if lang != "uz" else "uz",
        },
        "status": {
            "privacyStatus":              "public",
            "selfDeclaredMadeForKids":    False,
        }
    }

    print(f"  → YouTube yuklash: {yt_title}")
    try:
        media   = MediaFileUpload(
            video_path, mimetype="video/mp4",
            resumable=True, chunksize=5 * 1024 * 1024
        )
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"     Upload: {pct}%", end="\r")
        vid_id = response.get("id", "")
        print(f"\n     ✅ YouTube: https://youtu.be/{vid_id}")

        # ── Playlist ga qo'shish ─────────────────────────────
        playlist_id = YOUTUBE_PLAYLIST.get(lang, "").strip()
        if vid_id and playlist_id:
            try:
                youtube.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": playlist_id,
                            "resourceId": {
                                "kind":    "youtube#video",
                                "videoId": vid_id,
                            },
                        }
                    },
                ).execute()
                print(f"     📋 Playlist ga qo'shildi ({lang.upper()}): {playlist_id}")
            except Exception as pe:
                print(f"  ⚠️  Playlist xato ({lang}): {pe}")

        return vid_id
    except Exception as e:
        print(f"  ⚠️  Upload xato: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# Yangiliklar yuklanishi
# ─────────────────────────────────────────────────────────────
DARAJA_RANK = {"muhim":0,"tezkor":1,"xabar":2}

def load_today_news(lang="uz", count=5):
    """
    Bugungi yangiliklar:
    - Allaqachon ishlatilganlar o'tkaziladi
    - O'xshash mavzudagilar deduplikatsiya qilinadi
    - article_link OG rasm olish uchun saqlanadi
    """
    today   = date.today().strftime("%Y%m%d")
    files   = []
    for pat in [f"{QUEUE_DIR}/done/{today}*.json", f"{QUEUE_DIR}/{today}*.json"]:
        files += glob.glob(pat)
    files = sorted(set(files))

    used_set  = _load_used(lang)   # Bugun allaqachon ishlatilganlar
    news_raw  = []
    seen_exact = set()             # Aniq sarlavha takrori
    accepted   = []                # O'xshashlik tekshiruvi uchun

    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue

        sarlavha = _fix_terms(d.get("sarlavha", {}).get(lang, ""))
        if not sarlavha or sarlavha in seen_exact:
            continue
        seen_exact.add(sarlavha)

        # Allaqachon ishlatilganmi?
        if _is_used(sarlavha, used_set):
            print(f"  ⏭  O'tkazildi (ishlatilgan): {sarlavha[:55]}")
            continue

        # O'xshash yangiliklar (bir mavzudan faqat bittasi)
        similar = any(_news_similar(sarlavha, a["sarlavha"]) for a in accepted)
        if similar:
            print(f"  ⏭  O'tkazildi (o'xshash): {sarlavha[:55]}")
            continue

        jumla = _fix_terms(d.get("jumla", {}).get(lang, ""))
        if not _jumla_ok(jumla, lang):
            jumla = ""

        daraja = d.get("daraja", "xabar")
        item = {
            "sarlavha":       sarlavha,
            "jumla":          jumla[:200],
            "daraja":         daraja,
            "rank":           DARAJA_RANK.get(daraja, 2),
            "file":           f,
            "search_queries": d.get("search_queries", []),
            "keywords_en":    d.get("keywords_en", []),
            "article_link":   d.get("article", {}).get("link", ""),
        }
        accepted.append(item)

    accepted.sort(key=lambda x: (x["rank"], -os.path.getmtime(x["file"])))
    return accepted[:count]


# ─────────────────────────────────────────────────────────────
# Asosiy pipeline
# ─────────────────────────────────────────────────────────────
def make_daily_shorts(lang="uz"):
    total_est = INTRO_DUR + 5*SEG_DUR - 5*TRANS_DUR
    print(f"\n📰 Daily Shorts ({lang.upper()}) — ~{total_est:.0f} sek")
    os.makedirs(TEMP_DIR,   exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    news = load_today_news(lang=lang, count=5)
    if not news:
        print("  ⚠️  Bugungi yangilik topilmadi"); return None
    if len(news) < 3:
        print(f"  ⚠️  Yangilik yetarli emas ({len(news)} ta) — minimal 3 ta kerak. O'tkazildi.")
        return None

    print(f"  ✓ {len(news)} ta yangilik topildi")
    for i,n in enumerate(news,1):
        print(f"    {i}. [{n['daraja']:7}] {n['sarlavha'][:55]}")

    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = []

    # ── Intro ────────────────────────────────────────────────
    print("  → Intro...")
    intro_img = os.path.join(TEMP_DIR, f"ds_intro_{ts}.jpg")
    intro_raw = os.path.join(TEMP_DIR, f"ds_intro_{ts}_r.aac")
    intro_aud = os.path.join(TEMP_DIR, f"ds_intro_{ts}.aac")
    intro_vid = os.path.join(TEMP_DIR, f"ds_intro_{ts}.mp4")

    make_intro_card(lang, intro_img)
    if make_tts(build_intro_text(lang), intro_raw, lang):
        pad_audio_to(intro_raw, intro_aud, INTRO_DUR)
    else:
        make_silence(INTRO_DUR, intro_aud)
    aud = intro_aud if os.path.exists(intro_aud) else (make_silence(INTRO_DUR, intro_aud) or intro_aud)

    if image_to_video(intro_img, intro_aud, INTRO_DUR, intro_vid):
        parts.append(intro_vid)
        print(f"     ✓ Intro ({INTRO_DUR}s)")

    # ── Har bir yangilik ─────────────────────────────────────
    for i, item in enumerate(news, 1):
        print(f"  → Yangilik {i}: {item['sarlavha'][:50]}...")

        sarlavha = item["sarlavha"]
        daraja   = item["daraja"]

        seg_photo = os.path.join(TEMP_DIR, f"ds_ph_{ts}_{i}.jpg")
        seg_card  = os.path.join(TEMP_DIR, f"ds_card_{ts}_{i}.jpg")
        seg_raw   = os.path.join(TEMP_DIR, f"ds_aud_{ts}_{i}_r.aac")
        seg_aud   = os.path.join(TEMP_DIR, f"ds_aud_{ts}_{i}.aac")
        seg_vid   = os.path.join(TEMP_DIR, f"ds_seg_{ts}_{i}.mp4")

        # 1. Rasm: avval maqolaning o'z rasmi, keyin Pexels
        photo_ok = False
        art_link = item.get("article_link", "")
        if art_link:
            photo_ok = fetch_article_image(art_link, seg_photo)
            if photo_ok:
                print(f"     📰 Maqola rasmi: {art_link[:55]}")
        if not photo_ok:
            photo_ok = fetch_news_photo(
                item.get("search_queries", []),
                item.get("keywords_en", []),
                seg_photo
            )
        if not photo_ok:
            print(f"     ℹ️  Rasm topilmadi — qora fon")

        # 2. Karta (rasm + overlay)
        make_card_with_bg(i, sarlavha, daraja, lang,
                          seg_photo if photo_ok else None,
                          seg_card)

        # 3. TTS: raqam + sarlavha
        tts_text = build_tts_text(i, sarlavha, lang)
        if make_tts(tts_text, seg_raw, lang):
            pad_audio_to(seg_raw, seg_aud, SEG_DUR)
        else:
            make_silence(SEG_DUR, seg_aud)
        if not os.path.exists(seg_aud):
            make_silence(SEG_DUR, seg_aud)

        # 4. Rasm → video segment
        if image_to_video(seg_card, seg_aud, SEG_DUR, seg_vid):
            parts.append(seg_vid)
            print(f"     ✓ Segment {i} ({SEG_DUR}s)")
        else:
            print(f"     ⚠️  Segment {i} yaratilmadi")

    if not parts:
        print("  ⚠️  Hech bir segment yaratilmadi"); return None

    # ── xfade concat ─────────────────────────────────────────
    concat_vid = os.path.join(TEMP_DIR, f"ds_raw_{ts}.mp4")
    if not concat_with_transitions(parts, concat_vid):
        print("  ⚠️  Concat muvaffaqiyatsiz"); return None

    # Ishlatilgan yangiliklar saqlanadi (keyingi ishga tushirishda o'tkazish uchun)
    _save_used(lang, news)

    # ── Fon musiqasi ─────────────────────────────────────────
    out_name  = f"{ts}_daily_shorts_{lang}.mp4"
    out_path  = os.path.join(OUTPUT_DIR, out_name)
    total_dur = INTRO_DUR + len(news)*SEG_DUR + 5

    music_file = MUSIC_PATH
    if not os.path.exists(music_file):
        print("  ℹ️  Musiqa fayli yo'q — avto-beat yaratilmoqda...")
        gen_music = os.path.join(TEMP_DIR, f"ds_music_{ts}.aac")
        music_file = gen_music if generate_news_beat(total_dur, gen_music) else None

    if music_file and os.path.exists(music_file):
        print("  → Musiqa qo'shilmoqda...")
        if not add_background_music(concat_vid, music_file, out_path):
            shutil.copy(concat_vid, out_path)
    else:
        shutil.copy(concat_vid, out_path)

    _cleanup(ts, parts, concat_vid)

    if os.path.exists(out_path):
        sz  = os.path.getsize(out_path)/1_048_576
        print(f"\n  ✅ {out_name} ({sz:.1f} MB, ~{total_est:.0f}s)")

        # YouTube ga yuklash — vid_id ni saqlaymiz
        yt_vid_id = upload_daily_to_youtube(out_path, news, lang)
        yt_url    = f"https://youtu.be/{yt_vid_id}" if yt_vid_id else ""

        # ── Telegram + Facebook postlash (UZ va RU) ───────────
        if lang in ("uz", "ru"):
            try:
                from social_poster import post_telegram_video, post_facebook_yt_link
                # Birinchi yangilik sarlavhasi va jumla
                top_title  = news[0].get("sarlavha", "") if news else ""
                top_jumla  = news[0].get("jumla1",   "") if news else ""
                top_loc    = news[0].get("location",  "") if news else ""
                daraja_val = news[0].get("daraja", "xabar") if news else "xabar"

                # Telegram
                tg_ok = post_telegram_video(
                    video_path = out_path,
                    sarlavha   = top_title,
                    jumla      = top_jumla,
                    lang       = lang,
                    daraja     = daraja_val,
                    yt_url     = yt_url,
                    location   = top_loc,
                )
                print(f"  {'✅' if tg_ok else '⚠️ '} Telegram Shorts [{lang.upper()}]")

                # Facebook — YouTube havolasi bilan
                if yt_url:
                    fb_ok = post_facebook_yt_link(
                        yt_url      = yt_url,
                        title       = top_title,
                        description = top_jumla,
                        lang        = lang,
                        daraja      = daraja_val,
                        location    = top_loc,
                    )
                    print(f"  {'✅' if fb_ok else '⚠️ '} Facebook Shorts [{lang.upper()}]")
            except Exception as _sp_e:
                print(f"  ⚠️  Shorts social post xato [{lang.upper()}]: {_sp_e}")

        return out_path
    return None


def _cleanup(ts, parts, concat_vid):
    for p in parts:
        try:
            if os.path.exists(p): os.remove(p)
        except: pass
    try:
        if os.path.exists(concat_vid): os.remove(concat_vid)
    except: pass
    for ext in ("jpg","jpeg","png","aac","mp4"):
        for f in glob.glob(os.path.join(TEMP_DIR, f"ds_*_{ts}*.{ext}")):
            try: os.remove(f)
            except: pass


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Daily Top-5 Shorts")
    parser.add_argument("--lang", default="uz", choices=["uz","ru","en"])
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if args.all:
        for lg in ["uz","ru","en"]:
            make_daily_shorts(lg)
    else:
        result = make_daily_shorts(args.lang)
        print(f"\nFayl: {result}" if result else "\nShorts yaratilmadi.")
