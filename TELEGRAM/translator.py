"""translator.py — Gemini AI tarjima (1 so'rov)"""
import json
import re
import time
import requests
import logging
import os

# .env fayldan API key yuklab olish (app.py oldin yuklamasa ham ishlaydi)
try:
    from dotenv import load_dotenv as _lde
    _lde(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=True)
except Exception:
    pass

GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")  # openrouter.ai (fallback)
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")   # api.anthropic.com (asosiy)
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")        # groq.com (tezkor zaxira)

log = logging.getLogger(__name__)


# ── Latin → Kirill (o'zbek) ──────────────────────────────────
def lat2cyr(text: str) -> str:
    """O'zbek lotin yozuvini kirill yozuviga o'girish.
    Tartib muhim: avval uzunroq kombinatsiyalar almashtiriladi."""
    if not text:
        return text

    # Apostroflarni normallashtirish
    text = text.replace("\u2018", "'").replace("\u2019", "'").replace("`", "'")

    # (kombinatsiyalar avval — yakka harflardan oldin)
    table = [
        # 3 harfli
        ("SHʼ", "Шъ"), ("Shʼ", "Шъ"),
        # 2 harfli — katta
        ("SH", "Ш"), ("Sh", "Ш"),
        ("CH", "Ч"), ("Ch", "Ч"),
        ("NG", "НГ"), ("Ng", "Нг"),
        ("YO", "Ё"), ("Yo", "Ё"),
        ("YU", "Ю"), ("Yu", "Ю"),
        ("YA", "Я"), ("Ya", "Я"),
        ("YE", "Е"), ("Ye", "Е"),
        # O' va G' — katta va kichik
        ("O'", "Ў"), ("o'", "ў"),
        ("G'", "Ғ"), ("g'", "ғ"),
        ("O'", "Ў"), ("o'", "ў"),  # typografik apostrof ham
        ("G'", "Ғ"), ("g'", "ғ"),
        # 2 harfli — kichik
        ("sh", "ш"), ("ch", "ч"), ("ng", "нг"),
        ("yo", "ё"), ("yu", "ю"), ("ya", "я"), ("ye", "е"),
        # Yakka harflar — E/e ataylab YO'Q (quyida regex bilan ishlaydi)
        ("A","А"), ("B","Б"), ("D","Д"),
        ("F","Ф"), ("G","Г"), ("H","Ҳ"), ("I","И"),
        ("J","Ж"), ("K","К"), ("L","Л"), ("M","М"),
        ("N","Н"), ("O","О"), ("P","П"), ("Q","Қ"),
        ("R","Р"), ("S","С"), ("T","Т"), ("U","У"),
        ("V","В"), ("X","Х"), ("Y","Й"), ("Z","З"),
        ("a","а"), ("b","б"), ("d","д"),
        ("f","ф"), ("g","г"), ("h","ҳ"), ("i","и"),
        ("j","ж"), ("k","к"), ("l","л"), ("m","м"),
        ("n","н"), ("o","о"), ("p","п"), ("q","қ"),
        ("r","р"), ("s","с"), ("t","т"), ("u","у"),
        ("v","в"), ("x","х"), ("y","й"), ("z","з"),
        # Apostrof → qattiq belgi
        ("'", "ъ"), ("'", "ъ"),
    ]
    result = text
    for lat, cyr in table:
        result = result.replace(lat, cyr)

    # ── E/e: so'z BOSHIDA → Э/э,  so'z ICHIDA → Е/е ────────
    # \b so'z chegarasi — Kirill+Lotin aralash matnda ishlaydi:
    #   "кeldi":  'к'(Kirill \w) + 'e' → chegara yo'q → е (to'g'ri)
    #   "erisha": so'z boshi + 'e'     → chegara bor  → э (to'g'ri)
    result = re.sub(r'\bE', 'Э', result)
    result = re.sub(r'\be', 'э', result)
    result = result.replace('E', 'Е').replace('e', 'е')

    # еъ/Еъ → эъ/Эъ  (agar qolgan bo'lsa: e'lon → эълон)
    result = result.replace("еъ", "эъ").replace("Еъ", "Эъ")
    return result


def _fix_case(text: str) -> str:
    """CAPS → Sentence case, kichikdan boshlangan → birinchi harf katta."""
    if not text:
        return text
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return text
    upper_n = sum(1 for c in letters if c.isupper())
    # 50%dan ko'p katta harf bo'lsa — hammasi kichik, birinchisi katta
    if upper_n / len(letters) > 0.5:
        lowered = text.lower()
        return lowered[0].upper() + lowered[1:] if lowered else lowered
    # Birinchi harf kichik bo'lsa — katta qilish (all-lowercase modeldan)
    t = text.strip()
    if t and t[0].islower():
        return t[0].upper() + t[1:]
    return text


# ── Sarlavha sifat tekshiruvi ─────────────────────────────────
_CYR = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯЎҚҒҲ"
_BAD_TITLES = {
    "none", "n/a", "sarlavha", "заголовок", "headline",
    "title", "no title", "untitled", "null", "",
}

def _is_valid_title(text: str, lang: str = "uz") -> bool:
    """Sarlavha to'g'ri ekanligini tekshirish — minimal cheklovlar."""
    if not text or not text.strip():
        return False
    t = text.strip()
    if t.lower() in _BAD_TITLES:
        return False
    if len(t) < 5:          # juda qisqa (8 dan 5 ga tushirildi)
        return False
    if len(t) > 300:        # juda uzun (150 dan 300 ga oshirildi)
        return False
    words = t.split()
    if len(words) < 2:      # kamida 2 so'z (3 dan 2 ga tushirildi)
        return False
    # RU sarlavha — kamida 30% kirill (40% dan pastlatildi)
    if lang == "ru":
        _letters = [c for c in t if c.isalpha()]
        if _letters:
            _cyr_n = sum(1 for c in _letters if c in _CYR)
            if _cyr_n / len(_letters) < 0.30:
                return False
    return True


# ══════════════════════════════════════════════════════════════
# Ўзбекча жой номлари луғати — рус шакли → ўзбек шакли
# (post-processing: AI рус шаклини ишлатса — алмаштирилади)
# ══════════════════════════════════════════════════════════════
_UZ_PLACES = {
    # ── Давлатлар (рус шакли → тўғри ўзбекча) ───────────────
    "Иран":                "Эрон",
    "Ирак":                "Ироқ",
    "Афганистан":          "Афғонистон",
    "Пакистан":            "Покистон",
    "Индия":               "Ҳиндистон",
    "Китай":               "Хитой",
    "Израиль":             "Исроил",
    "Израил":              "Исроил",
    "Палестина":           "Фаластин",
    "Сирия":               "Сурия",
    "Йемен":               "Яман",
    "Ливан":               "Ливан",
    "Ливанон":             "Ливан",
    "Египет":              "Миср",
    "Турция":              "Туркия",
    "Иордания":            "Урдун",
    "Ливия":               "Ливия",
    "Марокко":             "Марокаш",
    "Алжир":               "Жазоир",
    "Судан":               "Судан",
    "Бахрейн":             "Баҳрайн",
    "Кувейт":              "Қувайт",
    "Эфиопия":             "Ҳабашистон",
    "Саудовская Аравия":   "Саудия Арабистони",
    "Саудовской Аравии":   "Саудия Арабистонида",
    "ОАЭ":                 "БАА",
    # ── AI нотўғри ўзбекча шакллари → тўғри ўзбекча ─────────
    "Ирон":                "Эрон",
    "Пакистон":            "Покистон",
    "Лубнон":              "Ливан",      # AI: Лубнон → тўғри: Ливан
    "Либнон":              "Ливан",      # AI: Либнон → тўғри: Ливан
    "Ғазза":               "Ғазо",       # AI: Ғазза → тўғри: Ғазо
    "Сауди Арабистон":     "Саудия Арабистони",
    "Сауди Арабистони":    "Саудия Арабистони",
    # ── Шаҳарлар ─────────────────────────────────────────────
    "Исламабад":           "Исломобод",
    "Токиё":               "Токио",
    "Тегеран":             "Теҳрон",
    "Дамаск":              "Дамашқ",
    "Багдад":              "Бағдод",
    "Кабул":               "Қобул",
    "Дели":                "Деҳли",
    "Мумбаи":              "Мумбай",
    "Карачи":              "Қарочи",
    "Лахор":               "Лоҳур",
    "Анкара":              "Анқара",
    "Стамбул":             "Истанбул",
    "Бейрут":              "Байрут",
    "Эр-Рияд":             "Риёд",
    "Эр-Рияде":            "Риёдда",
    "Доха":                "Доҳа",
    "Абу-Даби":            "Абу-Дабий",
    "Иерусалим":           "Байтулмуқаддас",
    "Ханой":               "Ҳаной",
    "Амман":               "Аммон",
    "Катманду":            "Қатманду",
    "Найроби":             "Найроби",
    # ── Оролсимон / худудлар ─────────────────────────────────
    "Газа":                "Ғазо",
    "Газе":                "Ғазода",
    "Газы":                "Ғазонинг",
    "Газой":               "Ғазо",
    "Персидский залив":    "Форс кўрфази",
    "Персидского залива":  "Форс кўрфазининг",
    "Западный берег":      "Ғарбий соҳил",
    "Западного берега":    "Ғарбий соҳилнинг",
}

# ── Ўзбекча хабар терминлари (AI нотўғри ишлатади) ──────────
_UZ_TERMS = {
    # Оташкесим / ўт очишни тўхтатиш
    "оташбас":              "ўт очишни тўхтатиш",
    "Оташбас":              "Ўт очишни тўхтатиш",
    "оташ бас":             "ўт очишни тўхтатиш",
    "оташкесим":            "ўт очишни тўхтатиш",
    "Оташкесим":            "Ўт очишни тўхтатиш",
    # Яҳудий (еврей — рус сўзи)
    "еврей":                "яҳудий",
    "Еврей":                "Яҳудий",
    "евреи":                "яҳудийлар",
    "Евреи":                "Яҳудийлар",
    "еврейлар":             "яҳудийлар",
    "Еврейлар":             "Яҳудийлар",
    "еврейча":              "яҳудийча",
    "Израил":               "Исроил",        # Израиль → Исроил
    "Израиль":              "Исроил",
    "Газа":                 "Ғазо",
    "Ливнон":               "Ливан",
    # БМТ (AI нотўғри форма ишлатади)
    " бмн ":                " БМТ ",
    " БМН ":                " БМТ ",
    "бмн ":                 "БМТ ",
    "БМН ":                 "БМТ ",
    " бмн.":                " БМТ.",
    " бмт ":                " БМТ ",
    "бмт ":                 "БМТ ",
    " оон ":                " БМТ ",
    " ООН ":                " БМТ ",
    "оон ":                 "БМТ ",
    "ООН ":                 "БМТ ",
    " оон.":                " БМТ.",
    # НАТО → НАТО (to'g'ri, o'zgartirmaymiz)
}

def _apply_uz_terms(text: str) -> str:
    """Ўзбекча хабар терминларини тузатиш."""
    if not text:
        return text
    for wrong, right in _UZ_TERMS.items():
        if wrong in text:
            text = re.sub(r'(?<!\w)' + re.escape(wrong) + r'(?!\w)', right, text)
    return text

# Сўз чегараси билан алмаштириш
def _apply_uz_places(text: str) -> str:
    """Рус жой номларини ўзбекча шаклга алмаштириш."""
    if not text:
        return text
    # Узунроқ (кўп сўзли) ибораларни аввал алмаштириш
    for ru, uz in sorted(_UZ_PLACES.items(), key=lambda x: -len(x[0])):
        if ru in text:
            text = re.sub(r'(?<!\w)' + re.escape(ru) + r'(?!\w)', uz, text)
    return text


# ══════════════════════════════════════════════════════════════
# Хэштег placeholder текшируви ва тузатиш
# ══════════════════════════════════════════════════════════════
_FAKE_HASHTAG = re.compile(
    r'#[A-Za-zА-ЯЁа-яёЎўҚқҒғҲҳ]+[Тт]ег\d|'   # #УзТег1 #РуТег1
    r'#[A-Z][a-z]+[Tt]ag\d|'                   # #EnTag1
    r'#[Тт]ег\d|#[Tt]ag\d'                     # #Тег1 #Tag1
)

def _is_fake_hashtag(text: str) -> bool:
    return bool(_FAKE_HASHTAG.search(text or ""))

def _gen_hashtags(keywords_en: list, lang: str, daraja: str = "xabar") -> str:
    """Kalit so'zlardan oddiy hashtag yaratish."""
    # Asosiy brend teglari
    brand = {"uz": "#1KUN #Yangilik", "ru": "#1День #Новости", "en": "#1Day #News"}
    base = brand.get(lang, "#News")

    # keyword_en dan birinchi 2 ta nom (bosh harfli) ni hashtag qilish
    kw_tags = []
    for k in keywords_en[:5]:
        k = k.strip()
        if k and k[0].isupper() and len(k) > 2 and " " not in k:
            kw_tags.append("#" + k)
        if len(kw_tags) >= 2:
            break

    if daraja == "muhim":
        base = {"uz": "#MUHIM #1KUN", "ru": "#ВАЖНО #1День", "en": "#BREAKING #1Day"}.get(lang, base)
    elif daraja == "tezkor":
        base = {"uz": "#Tezkor #1KUN", "ru": "#Срочно #1День", "en": "#Urgent #1Day"}.get(lang, base)

    parts = kw_tags + [base]
    return " ".join(parts[:5])


def _uz_from_russian(ru_text: str, context_en: str = "") -> str:
    """Ruscha matnni o'zbek LOTIN alifbosiga tarjima qilish.
    YouTube va Telegram UZ kanal uchun faqat LOTIN."""
    if not ru_text or not ru_text.strip():
        return ""
    prompt = (
        "Translate the following Russian text into Uzbek LATIN script (lotin alifbosi).\n"
        "Use ONLY Latin letters (a-z, o', g', sh, ch, ng). NO Cyrillic letters.\n"
        "Use Uzbek vocabulary (NOT Russian words).\n"
        "Name equivalents: Украина=Ukraina, Россия=Rossiya, Израиль=Isroil, "
        "Иран=Eron, Газа=G'azo, Трамп=Tramp, Байден=Bayden, Путин=Putin, "
        "Зеленский=Zelenskiy, Нетаньяху=Netanyaxu, Москва=Moskva, Лондон=London.\n"
        "Sentence case only.\n\n"
        f"Russian text: {ru_text}\n\n"
        "Return ONLY the translated Uzbek Latin text, nothing else."
    )
    try:
        result = groq_ask(prompt, max_tokens=400).strip()
        result = result.strip('"\'«»„"')
        # Tekshirish: asosan lotin bo'lishi kerak (kirill emas)
        cyr_count = sum(1 for c in result if c in _CYR)
        if cyr_count > 5:
            log.warning(f"_uz_from_russian: kirill harflar bor ({cyr_count}) — o'tkazildi")
            return ""
        if result and len(result.split()) >= 2:
            return _fix_case(result)
        log.warning(f"_uz_from_russian: natija bo'sh — '{result[:50]}'")
        return ""
    except Exception as e:
        log.warning(f"_uz_from_russian xato: {e}")
        return ""


def _fix_title_only(original_en: str, lang: str, source_ru: str = "") -> str:
    """Faqat sarlavhani alohida qayta so'rash (qisqa prompt).
    UZ uchun: source_ru mavjud bo'lsa RU->UZ (ancha yaxshi) ishlatiladi."""

    # UZ: to'g'ridan-to'g'ri inglizchadan Lotin o'zbek (Kirill EMAS)

    lang_map = {
        "uz": "Uzbek LATIN script (o'zbek lotin alifbosi). 5-8 so'z, sentence case. Faqat birinchi so'z va xos ismlar bosh harf. Ruscha so'z ishlatma. Misol: 'Tramp Yevropaga yangi boj soliqlarini e'lon qildi'.",
        "ru": "Russian. 5-8 words, sentence case. Only first word and proper nouns capitalized.",
        "en": "English. 5-8 words, sentence case. Only first word and proper nouns capitalized.",
    }
    instruction = lang_map.get(lang, lang_map["en"])

    # Lebanon vs Libya farqlash
    _en_lower = (original_en or "").lower()
    _is_lbn = any(k in _en_lower for k in
                  ("lebanon", "liban", "beirut", "lebanese", "hezbollah", "nasrallah"))
    _is_lby = any(k in _en_lower for k in
                  ("libya", "libyan", "tripoli", "benghazi", "haftar"))
    if _is_lbn and not _is_lby:
        _geo = ("\nCRITICAL: This is about LEBANON (Livan, Middle East). "
                "NEVER use 'Liviya' (that is Libya/Africa).\n")
    elif _is_lby and not _is_lbn:
        _geo = ("\nCRITICAL: This is about LIBYA (Liviya, North Africa). "
                "NEVER use 'Livan' (that is Lebanon/Middle East).\n")
    else:
        _geo = ""

    prompt = (
        f"Translate this news headline to {instruction}\n"
        f"{_geo}"
        f"Headline: {original_en}\n\n"
        f"Return ONLY the translated headline text, nothing else."
    )
    try:
        result = groq_ask(prompt, max_tokens=120).strip()
        # Tirnoq va germetik belgilarni tozalash
        result = result.strip('"\'«»„"')
        if lang == "uz":
            # Lotin qoladi — kirillga O'TKAZMAYMIZ
            result = _fix_case(result)
        else:
            result = _fix_case(result)
        return result
    except Exception as e:
        log.warning(f"Sarlavha retry xato ({lang}): {e}")
        return ""


_GEMINI_MODEL = "gemini-2.0-flash"
_GEMINI_URL   = f"https://generativelanguage.googleapis.com/v1beta/models/{_GEMINI_MODEL}:generateContent"


# ══════════════════════════════════════════════════════════════
# Yordamchi funksiyalar — har bir API servisi
# ══════════════════════════════════════════════════════════════

def _ask_gemini(prompt, max_tokens=2500, retries=2) -> str:
    """Gemini 2.0 Flash — asosiy tarjimon (bepul, 15 RPM).
    429 limitda: 15s, 30s kutadi — jami max ~45s, keyin OpenRouter."""
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens},
    }
    for attempt in range(retries):
        try:
            r = requests.post(
                _GEMINI_URL,
                params={"key": GEMINI_API_KEY},
                headers={"Content-Type": "application/json"},
                json=body, timeout=60,
            )
            if r.status_code == 429:
                wait = 15 * (attempt + 1)   # 15s, 30s
                log.warning(f"Gemini limit — {wait}s kutilmoqda (urinish {attempt+1}/{retries})...")
                time.sleep(wait)
                continue
            if r.status_code in (401, 403):
                raise Exception("Gemini API key yaroqsiz")
            if r.status_code == 400:
                raise Exception(f"Gemini 400: {r.text[:100]}")
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            if any(x in str(e) for x in ("400:", "yaroqsiz")):
                raise
            if attempt < retries - 1:
                log.warning(f"Gemini urinish {attempt+1}/{retries}: {e}")
                time.sleep(5)
            else:
                raise Exception(f"Gemini {retries} urinishdan keyin xato: {e}")
    raise Exception("Gemini: barcha urinishlar muvaffaqiyatsiz")


def _ask_openrouter(prompt, max_tokens=2500) -> str:
    """OpenRouter — bepul modellar zanjiri:
    1. meta-llama/llama-3.3-70b-instruct:free  (tasdiqlangan — mavjud!)
    2. deepseek/deepseek-r1:free               (DeepSeek R1 — kuchli bepul)
    3. deepseek/deepseek-chat-v3-0324:free     (DeepSeek V3)
    4. mistralai/mistral-7b-instruct:free      (klassik bepul)
    5. anthropic/claude-3-5-haiku              (to'lovli, so'nggi chora)
    """
    if not OPENROUTER_API_KEY:
        raise Exception("OPENROUTER_API_KEY yo'q")
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://birkunday.com",
        "X-Title":       "1Kun Global News",
    }
    # Bepul modellar avval sinab ko'riladi (404 xato bo'lsa — keyingiga o'tamiz)
    free_models = [
        "meta-llama/llama-3.3-70b-instruct:free",   # Eng kuchli bepul
        "google/gemma-3-27b-it:free",               # Google Gemma 3 27B
        "qwen/qwen3-8b:free",                       # Alibaba Qwen 3
        "google/gemma-2-9b-it:free",                # Google Gemma 2 9B
        "microsoft/phi-3-mini-128k-instruct:free",  # Microsoft Phi-3
        "mistralai/mistral-7b-instruct:free",       # Mistral (eski, lekin ishonchli)
    ]
    paid_models = [
        "anthropic/claude-3-5-haiku",              # Kredit kerak (so'nggi chora)
    ]
    all_models = free_models + paid_models
    errors = []
    for model in all_models:
        # claude-haiku uchun max_tokens ni kamaytirish (402 oldini olish)
        # min() ishlatish: title retry (80) → 80, asosiy (700) → 700
        _mt = min(max_tokens, 800) if "claude" in model else min(max_tokens, 2000)
        body = {
            "model":       model,
            "messages":    [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens":  _mt,
        }
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers, json=body, timeout=90,
            )
            if r.status_code == 429:
                log.warning(f"OpenRouter {model} limit — 30s kutilmoqda...")
                time.sleep(30)  # 30s: rate limit o'tishi uchun
                errors.append(f"{model}: 429 limit")
                continue
            if r.status_code == 402:
                # Kredit yo'q — keyingi modelga
                log.warning(f"OpenRouter {model}: kredit kerak — keyingi model...")
                errors.append(f"{model}: 402 no credits")
                continue
            if r.status_code == 404:
                # Model mavjud emas — keyingi modelga
                log.warning(f"OpenRouter {model}: topilmadi (404) — keyingi model...")
                errors.append(f"{model}: 404 not found")
                continue
            if r.status_code in (401, 403):
                raise Exception(f"OpenRouter {r.status_code}: {r.text[:80]}")
            r.raise_for_status()
            result = r.json()["choices"][0]["message"]["content"].strip()
            log.info(f"  ✅ OpenRouter {model}")
            return result
        except Exception as e:
            err_str = str(e)
            if any(x in err_str for x in ("401", "403")):
                raise
            log.warning(f"OpenRouter {model} xato: {err_str[:80]}")
            errors.append(f"{model}: {err_str[:50]}")
            time.sleep(3)
    raise Exception("OpenRouter barcha modellar muvaffaqiyatsiz: " + " | ".join(errors))


def _ask_anthropic(prompt, max_tokens=2500, model="claude-sonnet-4-6") -> str:
    """Anthropic API — to'g'ridan-to'g'ri Claude (asosiy tarjimon).
    Model: claude-sonnet-4-6 (yuqori sifat, o'zbek/rus tarjimasi uchun eng yaxshi)
    """
    if not ANTHROPIC_API_KEY:
        raise Exception("ANTHROPIC_API_KEY yo'q")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      model,
            "max_tokens": min(max_tokens, 8192),
            "messages":   [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    if r.status_code == 429:
        log.warning("Anthropic rate limit — 30s kutilmoqda...")
        time.sleep(30)
        raise Exception("Anthropic 429: rate limit")
    if r.status_code in (401, 403):
        raise Exception(f"Anthropic API key xato: {r.status_code}")
    if r.status_code == 400:
        raise Exception(f"Anthropic 400: {r.text[:120]}")
    r.raise_for_status()
    result = r.json()["content"][0]["text"].strip()
    log.info(f"  ✅ Anthropic {model}")
    return result


def _ask_groq(prompt, max_tokens=2500) -> str:
    """Groq API — llama-3.3-70b (tezkor, bepul, yaxshi sifat).
    Groq OpenAI-compatible API ishlatadi.
    """
    if not GROQ_API_KEY:
        raise Exception("GROQ_API_KEY yo'q")
    models = [
        "llama-3.3-70b-versatile",       # Eng kuchli (rate limit bo'lsa keyingisi)
        "llama-3.1-70b-versatile",       # Llama 3.1 70B
        "llama3-groq-70b-8192-tool-use-preview",  # Tool-use versiya
        "gemma2-9b-it",                  # Google Gemma 2 (Groq da mavjud)
        "llama-3.1-8b-instant",          # Tez, kichik (oxirgi chora)
    ]
    for model in models:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       model,
                    "messages":    [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens":  min(max_tokens, 6000),
                },
                timeout=60,
            )
            if r.status_code == 429:
                log.warning(f"Groq {model} rate limit — 10s...")
                time.sleep(10)
                continue
            if r.status_code in (400, 404):
                log.warning(f"Groq {model} xato {r.status_code} — keyingi model...")
                continue
            if r.status_code in (401, 403):
                raise Exception(f"Groq API key xato: {r.status_code}")
            r.raise_for_status()
            result = r.json()["choices"][0]["message"]["content"].strip()
            log.info(f"  ✅ Groq {model}")
            return result
        except Exception as e:
            err = str(e)
            if "401" in err or "403" in err:
                raise
            log.warning(f"Groq {model} xato: {err[:80]}")
    raise Exception("Groq: barcha modellar muvaffaqiyatsiz")


def groq_ask(prompt, max_tokens=2500, retries=2):
    """Tarjimon zanjiri: 1.Anthropic Sonnet → 2.Groq Llama → 3.Gemini → 4.OpenRouter"""
    errors = []

    # ── 1. Anthropic Claude Sonnet 4.6 (asosiy, eng yuqori sifat) ──
    if ANTHROPIC_API_KEY:
        try:
            return _ask_anthropic(prompt, max_tokens, model="claude-sonnet-4-6")
        except Exception as e:
            log.warning(f"Anthropic Sonnet → Groq ga o'tilmoqda: {e}")
            errors.append(f"Anthropic: {e}")

    # ── 2. Groq Llama-3.3-70b (tezkor, bepul, yaxshi sifat) ─────
    if GROQ_API_KEY:
        try:
            return _ask_groq(prompt, max_tokens)
        except Exception as e:
            log.warning(f"Groq → Gemini ga o'tilmoqda: {e}")
            errors.append(f"Groq: {e}")

    # ── 3. Gemini 2.0 Flash (zaxira, bepul) ─────────────────
    if GEMINI_API_KEY:
        try:
            return _ask_gemini(prompt, max_tokens, retries)
        except Exception as e:
            log.warning(f"Gemini → OpenRouter ga o'tilmoqda: {e}")
            errors.append(f"Gemini: {e}")

    # ── 4. OpenRouter (so'nggi zaxira) ──────────────────────
    if OPENROUTER_API_KEY:
        log.info("  ↩️  OpenRouter (zaxira)...")
        try:
            return _ask_openrouter(prompt, max_tokens)
        except Exception as e:
            errors.append(f"OpenRouter: {e}")

    raise Exception("Barcha tarjimon servislari muvaffaqiyatsiz: " + " | ".join(errors))


def parse_json(raw):
    if "```" in raw:
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip()
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", raw.strip())
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        raw = m.group(0)
    return json.loads(raw)


def groq_translate(title, description, source):
    """BITTA so'rov — barcha maydonlar + skriptlar"""

    # ── Manba tekshiruvi: Lebanon vs Libya farqlash ──────────
    _src_text = (title + " " + (description or "")).lower()
    _is_lebanon = any(kw in _src_text for kw in
                      ("lebanon", "liban", "beirut", "lebanese", "hezbollah",
                       "south lebanon", "southern lebanon", "nasrallah"))
    _is_libya   = any(kw in _src_text for kw in
                      ("libya", "libyan", "tripoli", "benghazi", "gaddafi",
                       "haftar", "tobruk"))

    # Prompt uchun geo-ogohlantirish
    if _is_lebanon and not _is_libya:
        _geo_warning = (
            "\n\n🚨 GEO-CRITICAL: This news is about LEBANON (Ливан/Livan) — "
            "a country in the Middle East. "
            "NEVER write 'Ливия' or 'Liviya' — that is Libya (Africa)! "
            "Lebanon = Ливан (Uzbek/Russian), Livan (Latin Uzbek), Liban (French). "
            "Beirut is Lebanon's capital. Hezbollah is a Lebanese group.\n"
        )
    elif _is_libya and not _is_lebanon:
        _geo_warning = (
            "\n\n🚨 GEO-CRITICAL: This news is about LIBYA (Ливия/Liviya) — "
            "a country in North Africa. "
            "NEVER write 'Ливан' or 'Livan' — that is Lebanon (Middle East)! "
            "Libya = Ливия (Uzbek/Russian), Liviya (Latin Uzbek).\n"
        )
    else:
        _geo_warning = ""

    prompt = f"""You are a professional multilingual news editor for "1Kun Global" Uzbek news channel.
{_geo_warning}
News title: {title}
News details: {description}

⚠️ ?? CRITICAL: sarlavha_uz, jumla1_uz, jumla2_uz, location_uz, hashtag_uz fields MUST be in Uzbek LATIN script.
DO NOT use Cyrillic or English in these fields. Write ONLY in Uzbek Latin alphabet (o'zbek lotin yozuvi).
Example: "Six months after ceasefire" ? "O?t ochishni to?xtatishdan olti oy o?tgach"

Return ONLY valid JSON, no extra text, no markdown:
{{
  "sarlavha_uz": "⚠️ O'ZBEK LOTIN ALIFBOSIDA — inglizcha yozma! 5-8 so'z, sentence case. Misol: 'Tramp Yevropaga yangi boj soliqlarini e'lon qildi'. Trump=Tramp, Biden=Bayden, NATO=NATO, Zelensky=Zelenskiy",
  "jumla1_uz": "⚠️ O'ZBEK LOTIN alifbosida — inglizcha yozma! Voqeaning asosiy mazmuni batafsil, 4-5 jumla. Nima bo'ldi, qayerda, kim, nima uchun — barchasini yoz. Tafsilotlar va kontekst qo'sh.",
  "jumla2_uz": "⚠️ O'ZBEK LOTIN alifbosida — inglizcha yozma! Qo'shimcha muhim tafsilotlar, 4-5 jumla. Natijalar, reaktsiyalar, tarixiy fon, ekspert fikrlari.",
  "sarlavha_ru": "Заголовок 5-8 слов на РУССКОМ языке (не на английском!), sentence case. Пример: 'Трамп объявил новые пошлины для Европы'",
  "jumla1_ru": "⚠️ ТОЛЬКО РУССКИЙ ЯЗЫК — не пиши по-английски! Главное событие подробно, 4-5 предложений. Что произошло, где, кто, почему — всё подробно.",
  "jumla2_ru": "⚠️ ТОЛЬКО РУССКИЙ ЯЗЫК — не пиши по-английски! Дополнительные детали, 4-5 предложений. Последствия, реакции, исторический контекст.",
  "sarlavha_en": "English headline 5-8 words, sentence case. Example: 'Trump announces new tariffs on European goods'",
  "jumla1_en": "Main event detailed, 4-5 sentences in English. What happened, where, who, why — full context.",
  "jumla2_en": "Additional details 4-5 sentences. Consequences, reactions, historical background.",
  "script_uz": "[450-500 so'z, SOF O'ZBEK LOTIN tilida — bu TTS uchun. Intro/outro yozma. Sarlavhani takrorlaMA — skript sarlavha BILAN emas, voqea KONTEKSTI yoki SABABI bilan boshlansin. Ruscha so'z EMAS. Xorijiy nomlar: Trump=Tramp, Biden=Bayden, Netanyahu=Netanyaxu. Yangilik mazmunini, kontekstini, tarixini va tafsilotlarini yoz.]",
  "script_ru": "[450-500 слов на русском языке. Без вступления и заключения типа 'В эфире...'. НЕ ПОВТОРЯЙ заголовок в начале — начинай с контекста, истории вопроса или причин события. Добавь контекст, историю, детали события.]",
  "script_en": "[450-500 words in English. No intro/outro phrases. DO NOT repeat the headline at the start — begin with context, background or reasons behind the event. Add context, background and details about the event.]",
  "daraja": "muhim OR tezkor OR xabar",
  "hook_uz": "Thumbnail uchun qisqa O'ZBEK LOTIN jumla, 3-5 so'z, hayratlanarli yoki shoshilinch. Misol: 'Dunyo larzaga keldi!' yoki 'Hamma narsa o'zgardi'",
  "hook_ru": "Короткая фраза для thumbnail 3-5 слов, интригующая. Пример: 'Мир изменился навсегда!' или 'Это меняет всё'",
  "hook_en": "Short thumbnail hook 3-5 words, urgent/intriguing. Example: 'World shocked!' or 'Everything changes now'",
  "hashtag_uz": "3-5 ta mavzuga oid hashteg O'ZBEK LOTIN alifbosida. Misol: '#Tramp #AQSH #Iqtisodiyot #1KUN'. Placeholder #UzTag1 ishlatma!",
  "hashtag_ru": "3-5 тематических хэштегов на русском. Пример: '#Трамп #США #Экономика #1День'. НЕ используй #РуТег1!",
  "hashtag_en": "3-5 topic hashtags in English. Example: '#Trump #USA #Economy #1Day'. Do NOT use #EnTag1!",
  "keywords_en": ["PersonName", "CountryOrCity", "OrganizationName", "EventTopic", "KeyTerm"],
  "search_queries": [
    "PersonName CountryName specific event 2026 footage",
    "CountryName EventTopic on the ground video",
    "OrganizationName event news footage 2026"
  ],
  "shot_list": [
    {{"shot": 1, "description": "Opening wide shot of the location or event (5-8 words)", "search": "EXACT location or event raw footage 2026", "duration": 6}},
    {{"shot": 2, "description": "Key person speaking or acting (5-8 words)", "search": "PersonName specific action or speech 2026", "duration": 5}},
    {{"shot": 3, "description": "Close-up of key subject or consequence (5-8 words)", "search": "specific subject close footage 2026", "duration": 5}},
    {{"shot": 4, "description": "Reaction or secondary scene (5-8 words)", "search": "related scene reaction footage 2026", "duration": 5}},
    {{"shot": 5, "description": "Context or background scene (5-8 words)", "search": "background context footage 2026", "duration": 5}},
    {{"shot": 6, "description": "Closing wide or symbolic shot (5-8 words)", "search": "symbolic closing shot footage 2026", "duration": 5}}
  ],
  "location_uz": "Shahar yoki davlat O'ZBEK LOTIN alifbosida. Misol: Moskva, Vashington, Eron, Ukraina",
  "location_ru": "Город или страна на русском",
  "location_en": "City or country in English"
}}

RULES:
- sarlavha_uz, jumla1_uz, jumla2_uz, location_uz, hashtag_uz, script_uz: ALL MUST be in Uzbek LATIN script
- DO NOT use Cyrillic in ANY Uzbek field — only Latin o'zbek alifbosi
- sarlavha fields: sentence case — NOT ALL CAPS, NOT Title Case
- daraja: muhim=war/disaster/crisis, tezkor=politics/economy/diplomacy, xabar=other
- hashtag fields: REAL topic-specific hashtags ONLY. NEVER use placeholders like #УзТег1 #РуТег1 #EnTag1
- UZBEK PLACE NAMES for sarlavha/jumla/script (ALL in LATIN). Use EXACT Uzbek Latin forms:
  Iran=Eron, Iraq=Iroq, Afghanistan=Afgʻoniston, Pakistan=Pokiston, India=Hindiston,
  China=Xitoy, Israel=Isroil (NOT Izrail!), Palestine=Falastin, Syria=Suriya, Yemen=Yaman,
  Lebanon=Livan (NOT Liviya!), Egypt=Misr, Turkey=Turkiya, Jordan=Iordaniya,
  Libya=Liviya (Africa, NOT Lebanon!), Morocco=Marokash, Algeria=Jazoir,
  Sudan=Sudan, Ethiopia=Habashiston, Saudi Arabia=Saudiya Arabistoni, UAE=BAA, Gaza=Gʻazo,
  Russia=Rossiya, Ukraine=Ukraina, Belarus=Belarus, Kazakhstan=Qozogʻiston,
  Azerbaijan=Ozarbayjon (NOT Ozarboyjon!), Armenia=Armaniston, Georgia=Gruziya,
  Kyiv=Kiyev, Moscow=Moskva, Washington=Vashington, London=London,
  Paris=Parij, Berlin=Berlin, Brussels=Bryussel, Geneva=Jeneva,
  Islamabad=Islomobod, Tehran=Tehron, Damascus=Damashq, Baghdad=Bagʻdod,
  Kabul=Qobul, Delhi=Dehli, Ankara=Anqara, Istanbul=Istanbul,
  Beirut=Bayrut, Riyadh=Riyod, Doha=Doha, Tokyo=Tokio,
  New York=Nyu-York, Vienna=Vena, Warsaw=Varshava, Rome=Rim, Madrid=Madrid
- UZBEK TERMS (LATIN — noto'g'ri tarjimalarni TAQIQLANG!):
  negotiations=muzokaralar (NOT suhbatlar!), talks=muzokaralar,
  peace talks=tinchlik muzokaralari,
  ceasefire=oʻt ochishni toʻxtatish, truce=sulh,
  aid=yordam (NOT qarz!), foreign aid=xorijiy yordam,
  humanitarian aid=insonparvarlik yordami,
  loan=qarz (bu 'aid' EMAS!), grant=grant, donation=xayriya,
  PM-designate=Bosh vazir nomzodi, designate=nomzod (NOT tadbirkor!),
  candidate=nomzod, nominee=nomzod, appointed=tayinlangan,
  businessman=tadbirkor (FAQAT 'businessman/entrepreneur' uchun!),
  report=hisobot, parliamentary report=parlament hisoboti,
  committee=qo'mita, MPs warn=deputatlar ogohlantirmoqda,
  significant gaps=jiddiy kamchiliklar, strategy=strategiya,
  cuts=qisqartirish, budget cuts=byudjet qisqartmasi,
  West Bank=Gʻarbiy qirgʻoq, airstrikes=aviazarba, sanctions=sanksiyalar,
  meeting=uchrashuv, summit=sammit, agreement=kelishuv, deal=bitim,
  missile=raketa, drone=dron, troops=qoʻshinlar, forces=kuchlar,
  president=prezident, minister=vazir, parliament=parlament,
  Jewish/Jew=yahudiy, Jews=yahudiylar, Israeli=isroillik,
  settlement=mustamlaka, hostages=garovdagilar, prisoners=mahbuslar,
  Zelensky=Zelenskiy, Putin=Putin, Trump=Tramp, Biden=Bayden,
  Netanyahu=Netanyaxu, Macron=Makron, Modi=Modi, Xi=Si (Si Szinpin),
  Musk=Mask, OpenAI=OpenAI, Tesla=Tesla
- HEADLINE RULES (MAJBURIY!):
  * sarlavha_uz MUST capture the SPECIFIC story, NOT generic statements
  * BAD example: "Британияда ўзгаришлар бўлди" (too generic, meaningless)
  * GOOD example: "Британия хорижий ёрдам стратегиясида жиддий камчиликлар"
  * Include: WHO did WHAT or WHAT happened — specific subject + action
  * Avoid empty phrases: "...ҳақида", "...бўлди", "...билан боғлиқ"
- CAPITALIZATION (MANDATORY — ALL languages, ALL fields):
  * Person names ALWAYS start with CAPITAL letter (even mid-sentence):
    English: Trump, Putin, Zelensky, Biden, Macron, Modi, Musk
    Russian: Трамп, Путин, Зеленский, Байден, Макрон, Моди, Маск
    Uzbek Latin: Tramp, Putin, Zelenskiy, Bayden, Makron, Modi, Mask
  * Country names ALWAYS capitalized: Ukraine/Украина/Ukraina, Russia/Россия/Rossiya,
    USA/США/AQSH, China/Китай/Xitoy, Israel/Израиль/Isroil, Iran/Иран/Eron
  * City names ALWAYS capitalized: Kyiv/Киев/Kiyev, Moscow/Москва/Moskva,
    London/Лондон/London, Paris/Париж/Parij, Washington/Вашингтон/Vashington,
    Berlin/Берлин/Berlin, Vienna/Вена/Vena, Warsaw/Варшава/Varshava,
    Beirut/Бейрут/Bayrut, Tehran/Тегеран/Tehron, Tokyo/Токио/Tokio
  * Organization names ALWAYS capitalized: NATO/НАТО, UN/ООН/BMT, EU/ЕС,
    IMF/МВФ, Hamas/ХАМАС, Hezbollah/Хезболла, ISIS/ИГИЛ
  * NEVER write proper nouns in lowercase: "trump" "москва" "ukraina" are WRONG
- RUSSIAN RULES (MANDATORY — applies to sarlavha_ru, jumla1_ru, jumla2_ru, script_ru):
  * ALWAYS "в Украине", "в Киеве" — NEVER "на Украине"! "на Украине" is politically incorrect.
  * City/country names ALWAYS start with CAPITAL letter: Вена, Лондон, Берлин, Рим, Мадрид, Токио, Вашингтон, Париж, Варшава, Брюссель, Пекин, Сеул, Тегеран, Бейрут, Эр-Рияд
  * ALL person names MUST be written in Cyrillic — NEVER leave them in Latin!
    Зеленский (NOT Zelensky!), Путин (NOT Putin!), Трамп (NOT Trump!),
    Байден (NOT Biden!), Нетаньяху (NOT Netanyahu!), Макрон (NOT Macron!),
    Моди (NOT Modi!), Си Цзиньпин (NOT Xi Jinping!), Маск (NOT Musk!),
    Шольц (NOT Scholz!), Старший (NOT Starmer!), Орбан (NOT Orban!)
  * NEVER mix Latin letters into Russian text — transliterate ALL foreign names
  * Proper nouns: first letter ALWAYS capital — names, countries, cities, organizations
- UZBEK PERSON NAME RULES (sarlavha_uz, jumla_uz ? LOTIN, MANDATORY):
  * ALL person names in sarlavha_uz/jumla_uz MUST be in Uzbek LATIN ? NEVER Cyrillic!
    Zelenskiy, Putin, Tramp, Bayden, Netanyaxu, Makron, Modi, Si Szinpin, Mask
  * NEVER write Cyrillic letters in sarlavha_uz or jumla_uz fields
  * All words in sarlavha_uz/jumla_uz must be fully Latin (a-z, o?, g?)
- UZBEK PLACE NAMES for script_uz (LATIN TTS):
  Israel=Isroil (NOT Izrail!), Lebanon=Livan (NOT Liviya!),
  Iran=Eron, Iraq=Iroq, Palestine=Falastin, Syria=Suriya, Gaza=Gʻazo,
  Turkey=Turkiya, Egypt=Misr, Saudi Arabia=Saudiya Arabistoni,
  Russia=Rossiya, Ukraine=Ukraina, Azerbaijan=Ozarbayjon (NOT Ozarboyjon!),
  Kazakhstan=Qozogʻiston, Armenia=Armaniston, Georgia=Gruziya,
  ceasefire=oʻt ochishni toʻxtatish,
  negotiations=muzokaralar (NOT suhbat!), talks=muzokaralar
- search_queries: REAL EVENT footage only, NO studio/anchor/presenter. Use EXACT names from the news.
- keywords_en: 5 SPECIFIC proper nouns — person names, countries, organizations.
- shot_list: 6 shots that tell the visual story. Each "search" must target FIELD footage — NO anchors, NO studio, NO panel, NO interview, NO analysis, NO presenter. Use specific locations, people, actions. Include year 2026."""

    # ── Qisqa prompt (OpenRouter fallback uchun — skriptsiz, ~600 token) ──
    short_prompt = f"""Translate this news to Uzbek Cyrillic, Russian, English. Return ONLY valid JSON (no markdown, no extra text).
{_geo_warning}
Title: {title}
Details: {description}

{{
  "sarlavha_uz": "5-7 so'z O'ZBEK LOTIN alifbosida — inglizcha yozma! Misol: 'Yamanda minali inqiroz davom etmoqda'",
  "jumla1_uz": "O'ZBEK LOTIN alifbosida — 3-4 ta jumla. Nima bo'ldi, qayerda, kim, nima uchun",
  "jumla2_uz": "O'ZBEK LOTIN alifbosida — 2-3 ta jumla. Natijalar, kontekst, tafsilotlar",
  "sarlavha_ru": "5-7 слов ТОЛЬКО НА РУССКОМ — не по-английски! Пример: 'Минный кризис в Йемене продолжается'",
  "jumla1_ru": "ТОЛЬКО РУССКИЙ — 3-4 предложения. Что произошло, где, кто, почему",
  "jumla2_ru": "ТОЛЬКО РУССКИЙ — 2-3 предложения. Последствия, контекст",
  "sarlavha_en": "5-7 words in English. Example: 'Yemen landmine crisis persists despite truce'",
  "jumla1_en": "3-4 sentences in English. What happened, where, who, why",
  "jumla2_en": "2-3 sentences. Consequences and context",
  "daraja": "muhim OR tezkor OR xabar",
  "hashtag_uz": "#3-4 ta hashteg O'ZBEK LOTIN alifbosida. Misol: '#Yaman #Dunyo #1KUN'",
  "hashtag_ru": "#3-4 хэштега по-РУССКИ. Пример: '#Йемен #Мир #1День'",
  "hashtag_en": "#3-4 hashtags. Example: '#Yemen #World #1Day'",
  "location_uz": "Joy nomi LOTIN alifbosida (shahar yoki davlat)",
  "location_ru": "Место по-русски",
  "location_en": "Location in English",
  "keywords_en": ["Person", "Country", "Organization", "Topic", "Term"]
}}
CRITICAL: sarlavha_uz, jumla1_uz, jumla2_uz — O'ZBEK LOTIN alifbosida. Kirill YOZMA! Inglizcha YOZMA!
CRITICAL: sarlavha_ru, jumla1_ru, jumla2_ru — FAQAT RUSCHA (а,б,в,г,д...). Inglizcha YOZMA!"""

    # ── 1. Anthropic Claude Sonnet 4.6 — to'liq prompt (asosiy, eng yuqori sifat) ──
    data      = None
    _ant_err  = None
    if ANTHROPIC_API_KEY:
        try:
            raw_ant = _ask_anthropic(prompt, max_tokens=8000, model="claude-sonnet-4-6")
            data = parse_json(raw_ant)
            log.info("✅ Anthropic Sonnet 4.6 — asosiy tarjima")
        except Exception as _e:
            _ant_err = _e
            log.warning(f"Anthropic Sonnet xato → Groq ga o'tilmoqda: {_ant_err}")

    # ── 2. Groq Llama-3.3-70b — to'liq prompt (tezkor, bepul, yaxshi sifat) ──
    _groq_err = None
    if data is None and GROQ_API_KEY:
        try:
            data = parse_json(_ask_groq(prompt, max_tokens=6000))
            log.info("✅ Groq Llama-3.3-70b — zaxira tarjima")
        except Exception as _e:
            _groq_err = _e
            log.warning(f"Groq xato → Gemini ga o'tilmoqda: {_groq_err}")

    # ── 3. Gemini 2.0 Flash — to'liq prompt (bepul, 3-chi zaxira) ───────
    _gem_err = None
    if data is None and GEMINI_API_KEY:
        try:
            data = parse_json(_ask_gemini(prompt, max_tokens=3000, retries=2))
            log.info("✅ Gemini 2.0 Flash — zaxira tarjima")
        except Exception as _e:
            _gem_err = _e
            log.warning(f"Gemini ham xato → OpenRouter qisqa so'rov: {_gem_err}")

    # ── 4. OpenRouter — qisqa prompt (so'nggi zaxira) ───────────────────
    if data is None:
        try:
            raw_or = _ask_openrouter(short_prompt, max_tokens=700)
            data = parse_json(raw_or)
            for _sf in ("script_uz", "script_ru", "script_en",
                        "hook_uz", "hook_ru", "hook_en"):
                data.setdefault(_sf, "")
            data.setdefault("shot_list", [])
            data.setdefault("search_queries", [])
            log.info("✅ OpenRouter qisqa so'rov muvaffaqiyatli")
        except Exception as e_or:
            log.warning(f"Tarjima xato: Anthropic:{_ant_err} | Groq:{_groq_err} | Gemini:{_gem_err} | OpenRouter:{e_or}")
            # Fallback: UZ/RU bo'sh (placeholder emas!), EN — orijinal matn
            _en_script = (description or title or "").strip()
            _en_j1     = _en_script[:600] if _en_script else title
            data = {
                "sarlavha_uz":  "",
                "jumla1_uz":    "",
                "jumla2_uz":    "",
                "sarlavha_ru":  "",
                "jumla1_ru":    "",
                "jumla2_ru":    "",
                "sarlavha_en":  title[:80],
                "jumla1_en":    _en_j1,
                "jumla2_en":    "",
                "script_uz":    "",
                "script_ru":    "",
                "script_en":    _en_script,
                "daraja":       "xabar",
                "hashtag_uz":   "#Yangilik #Dunyo #1KUN",
                "hashtag_ru":   "#Новости #Мир #1День",
                "hashtag_en":   "#News #World #1Day",
                "keywords_en":  title.split()[:5],
                "keywords_ru":  [],
                "search_queries": [title[:50]],
                "location_uz":  "",
                "location_ru":  "",
                "location_en":  "",
            }

    # ── AI placeholder larni tozalash: {musiqa}, {sarlavha}, {yangilik} ──
    # AI ba'zan JSON da to'ldirilmagan o'zgaruvchi qoldiradi — barchani o'chirish
    _placeholder_re = re.compile(r'\{[^}]{1,40}\}')
    for _pf in ("sarlavha_uz","sarlavha_ru","sarlavha_en",
                "jumla1_uz","jumla2_uz","jumla1_ru","jumla2_ru",
                "jumla1_en","jumla2_en","script_uz","script_ru","script_en"):
        if data.get(_pf):
            data[_pf] = _placeholder_re.sub('', data[_pf]).strip()

    # ── Post-processing: CAPS → Sentence Case ────────────────────
    for field in ("sarlavha_uz", "sarlavha_ru", "sarlavha_en"):
        data[field] = _fix_case(data.get(field, ""))

    # ── Proper noun capitalization — isim, joy, mamlakat katta harf ──
    # Known proper nouns: har doim bosh harf (lotin va kirill)
    _PROPER_NOUNS_LAT = [
        # ── Shaxslar (Uzbek Latin / English) ─────────────────────────
        "Trump","Tramp","Biden","Bayden","Putin","Zelensky","Zelenskiy",
        "Netanyahu","Netanyaxu","Macron","Makron","Modi","Scholz","Shols",
        "Starmer","Orban","Erdogan","Musk","Mask","Johnson","Sunak","Meloni",
        "Guterres","Blinken","Lavrov","Lukashenko","Aliyev","Pashinyan",
        "Tokayev","Sinwar","Abbas","Khamenei","Xi","Jinping","Szinpin",
        "Kim","Kishida","Zelenski","Milei","Lula","Modi","Albanese",
        # ── Davlatlar ──────────────────────────────────────────────────
        "Ukraine","Ukraina","Russia","Rossiya","USA","AQSH","China","Xitoy",
        "Israel","Isroil","Iran","Eron","Iraq","Iroq","Turkey","Turkiya",
        "Germany","Germaniya","France","Fransiya","Britain","Britaniya",
        "England","Angliya","Italy","Italiya","Spain","Ispaniya",
        "Poland","Polsha","Hungary","Vengriya","Sweden","Shvetsiya",
        "Finland","Finlandiya","Norway","Norvegiya","Denmark","Daniya",
        "Pakistan","Pokiston","India","Hindiston","Afghanistan","Afgʻoniston",
        "Palestine","Falastin","Syria","Suriya","Yemen","Yaman",
        "Egypt","Misr","Lebanon","Livan","Libya","Liviya",
        "Saudi","Saudiya","Qatar","Quvayt","Kuwait","Bahrain","Bahrayn",
        "Kazakhstan","Qozogʻiston","Azerbaijan","Ozarbayjon",
        "Georgia","Gruziya","Belarus","Armenia","Armaniston",
        "Japan","Yaponiya","Korea","Koreya","Australia","Avstraliya",
        "Canada","Kanada","Brazil","Braziliya","Argentina","Argentina",
        "Mexico","Meksika","Nigeria","Nigeriya","Ethiopia","Habashiston",
        "Somalia","Somali","Sudan","Liviya","Morocco","Marokash",
        # ── Shaharlar ──────────────────────────────────────────────────
        "Kyiv","Kiyev","Moscow","Moskva","London","Paris","Parij",
        "Washington","Vashington","Berlin","Vienna","Vena",
        "Warsaw","Varshava","Beirut","Bayrut","Tehran","Tehron",
        "Tokyo","Tokio","Doha","Riyadh","Riyod","Ankara","Anqara",
        "Istanbul","Baghdad","Bagʻdod","Kabul","Qobul",
        "Delhi","Dehli","Islamabad","Islomobod","Damascus","Damashq",
        "Amman","Ammol","Cairo","Qohira","Rabat","Algiers","Jazoir",
        "Nairobi","Lagos","Addis","Abeba","Mogadishu","Mogadisho",
        "Zaporizhzhia","Zaporizh","Mariupol","Mariupol",
        "Kharkiv","Xarkiv","Odesa","Odessa","Kherson","Xerson",
        "Donetsk","Donetsk","Luhansk","Lugansk","Donbas","Donbass",
        "Geneva","Jeneva","Brussels","Bryussel","Rome","Rim","Rim",
        "Madrid","Madrid","Lisbon","Lissabon","Athens","Afina",
        "Helsinki","Xelsinki","Oslo","Oslo","Stockholm","Stokgolm",
        "New York","Nyu-York","Los Angeles","Chicago","Houston",
        "Beijing","Pekin","Shanghai","Shanxay","Hong Kong","Gonkong",
        "Singapore","Singapur","Bangkok","Bangkok","Jakarta","Jakarta",
        "Riyadh","Riyod","Abu Dhabi","Abu-Dabi","Dubai","Dubay",
        # ── Tashkilotlar ───────────────────────────────────────────────
        "NATO","UN","BMT","EU","IMF","CIA","FBI","WHO","WTO","ICC",
        "Hamas","Hezbollah","ISIS","ISIL","Houthis","Houthi","Husillar",
        "Kremlin","Pentagon","Interpol","Europol","OPEC",
    ]
    _PROPER_NOUNS_CYR = [
        # ── Shaxslar (Russian Cyrillic) ───────────────────────────────
        "Трамп","Байден","Путин","Зеленский","Нетаньяху","Макрон","Моди",
        "Шольц","Стармер","Орбан","Эрдоган","Маск","Джонсон","Сунак",
        "Мелони","Гутерреш","Блинкен","Лавров","Лукашенко","Алиев",
        "Пашинян","Токаев","Синвар","Аббас","Хаменеи","Си","Цзиньпин",
        "Милей","Лула","Альбанезе","Ким","Мирзиёев","Назарбаев",
        # ── Davlatlar + tuslangan shakllari ──────────────────────────
        # Ukraina: Украина, Украине, Украины, Украину, Украиной
        "Украина","Украине","Украины","Украину","Украиной","Украинe",
        # Rossiya: Россия, России, России, Россию, Россией
        "Россия","России","Россию","Россией",
        # Boshqa davlatlar
        "Израиль","Израиля","Израилю","Израилем","Израиле",
        "Иран","Ирана","Ирану","Ираном","Иране",
        "Ирак","Ирака","Ираку","Ираком","Ираке",
        "Германия","Германии","Германию","Германией",
        "Франция","Франции","Францию","Францией",
        "Британия","Британии","Британию","Британией",
        "Китай","Китая","Китаю","Китаем","Китае",
        "США","Турция","Турции","Турцию","Турцией",
        "Египет","Египта","Египту","Египтом","Египте",
        "Ливан","Ливана","Ливану","Ливаном","Ливане",
        "Ливия","Ливии","Ливию","Ливией",
        "Саудовская","Катар","Кувейт","Пакистан","Пакистана",
        "Индия","Индии","Индию","Индией",
        "Казахстан","Казахстана","Казахстане",
        "Азербайджан","Азербайджана","Азербайджане",
        "Беларусь","Белоруссия","Армения","Армении","Грузия","Грузии",
        "Польша","Польши","Венгрия","Венгрии","Швеция","Финляндия",
        "Япония","Японии","Корея","Кореи","Австралия","Австралии",
        "Бразилия","Аргентина","Мексика","Нигерия","Эфиопия",
        # ── Shaharlar + tuslangan shakllari ──────────────────────────
        # Kiyev
        "Киев","Киева","Киеву","Киевом","Киеве",
        # Moskva
        "Москва","Москве","Москвы","Москву","Москвой",
        # Boshqa shaharlar
        "Лондон","Лондона","Лондону","Лондоном","Лондоне",
        "Париж","Парижа","Парижу","Парижем","Париже",
        "Вашингтон","Вашингтона","Вашингтоне",
        "Берлин","Берлина","Берлине",
        "Вена","Вены","Вене","Вену","Веной",
        "Варшава","Варшавы","Варшаве","Варшаву","Варшавой",
        "Бейрут","Бейрута","Бейруте",
        "Тегеран","Тегерана","Тегеране",
        "Токио","Доха","Эр-Рияд","Эр-Рияде",
        "Анкара","Анкары","Анкаре",
        "Стамбул","Стамбула","Стамбуле",
        "Багдад","Багдада","Багдаде",
        "Кабул","Дели","Исламабад","Дамаск","Аммон","Каир","Рабат",
        "Запорожье","Мариуполь","Харьков","Одесса","Херсон",
        "Донецк","Луганск","Донбасс",
        "Женева","Женевы","Женеве",
        "Брюссель","Брюсселя","Брюсселе",
        "Рим","Рима","Риме",
        "Мадрид","Лиссабон","Афины","Хельсинки","Осло","Стокгольм",
        "Нью-Йорк","Пекин","Шанхай","Сингапур","Бангкок","Дубай",
        "Абу-Даби","Эр-Рияд",
        # ── Tashkilotlar ─────────────────────────────────────────────
        "НАТО","ООН","ЕС","МВФ","ВОЗ","ВТО","МУС","ИГИЛ","ХАМАС",
        "Хезболла","Хуситы","Кремль","Пентагон","Интерпол","ОПЕК",
    ]

    def _capitalize_proper(text: str, nouns: list) -> str:
        # Matnda proper noun larni katta harf bilan yozish.
        # Uzbek suffikslari (-da, -ga, -ni, -ning, -dan, -lar) bilan ham ishlaydi:
        #   "ukrainada" → "Ukrainada", "moskvaga" → "Moskvaga"
        # Kirill uchun: tuslangan shakllar ro'yxatda berilgan
        if not text:
            return text
        # Uzbek apostroplarini normallashtirish (g' va gʻ, o' va oʻ tenglashtirish)
        text_norm = text.replace("ʻ", "'").replace("ʼ", "'")
        modified  = False
        result    = text_norm
        for noun in nouns:
            if len(noun) < 2:
                continue
            noun_norm = noun.replace("ʻ", "'").replace("ʼ", "'")
            # Faqat lookBEHIND — so'z boshi (oxiri TEKSHIRILMAYDI — suffiks uchun)
            pattern = r'(?<![a-zA-ZЀ-ӿʻʼ\'])' + re.escape(noun_norm)
            replacement = noun_norm[0].upper() + noun_norm[1:]
            try:
                new_result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
                if new_result != result:
                    modified = True
                    result = new_result
            except Exception:
                pass
        if not modified:
            return text   # Hech narsa o'zgarmadi — original qaytariladi
        # Apostrof normalizatsiyasini teskari qaytarish (original format saqlash)
        # (lotin matnda g' qoladi, Kirill matnda o'zgartirish kerak emas)
        return result


    # Apply to Latin UZ fields (sarlavha, jumla, script, location)
    for _f in ("sarlavha_uz", "jumla1_uz", "jumla2_uz", "script_uz", "location_uz"):
        _v = data.get(_f, "")
        if _v:
            data[_f] = _capitalize_proper(_v, _PROPER_NOUNS_LAT)

    # Apply to Cyrillic RU fields
    for _f in ("sarlavha_ru", "jumla1_ru", "jumla2_ru", "script_ru", "location_ru"):
        _v = data.get(_f, "")
        if _v:
            data[_f] = _capitalize_proper(_v, _PROPER_NOUNS_CYR)

    # Apply to EN fields
    for _f in ("sarlavha_en", "jumla1_en", "jumla2_en", "script_en", "location_en"):
        _v = data.get(_f, "")
        if _v:
            data[_f] = _capitalize_proper(_v, _PROPER_NOUNS_LAT)


    # ── UZ maydonlar — LOTIN alifbosida bo'lishi kerak ──────
    # (Kirill validatsiyasi yo'q — lotin qabul qilinadi)
    _UZ_LATIN_MARKERS = ("o'", "g'", "o'", "g'", "sh", "ch", "ng",
                         "o'z", "va ", "bu ", "lar", "dan", "ga ",
                         "ni ", "da ", "ham", "bir", "bor")

    def _is_mostly_cyr(text: str) -> bool:
        """Matnning kamida 60% harflari kiriллcha bo'lsa — True."""
        letters = [c for c in text if c.isalpha()]
        if not letters:
            return False
        cyr_n = sum(1 for c in letters if c in _CYR)
        return cyr_n / len(letters) >= 0.60

    def _is_uzbek_latin(text: str) -> bool:
        """Matn o'zbek lotinida yozilganmi (inglizcha emas)?"""
        tl = text.lower()
        return any(m in tl for m in _UZ_LATIN_MARKERS)

    # UZ maydonlar: agar Kirill kelsa — retry so'rab lotin olish
    for field in ("sarlavha_uz", "jumla1_uz", "jumla2_uz"):
        val = data.get(field, "")
        if not val:
            continue
        if _is_mostly_cyr(val):
            # Kirill keldi — lotin so'rab retry
            log.warning(f"⚠️  {field} Kirill keldi — lotin retry...")
            if "sarlavha" in field:
                fixed = _fix_title_only(title, "uz")
                if fixed and not _is_mostly_cyr(fixed):
                    data[field] = fixed
                    log.info(f"  ✓ {field} lotin retry: '{fixed[:50]}'")
                # aks holda — kirill qolsin (bo'shdan yaxshi)


    # ── UZ LOTIN maydonlarga joy nomlari tuzatish ────────────
    # (Kirill _apply_uz_places o'rniga — lotin shakllari)
    _LATIN_UZ_PLACES = {
        "Israel":   "Isroil",   "Izrail":   "Isroil",
        "Lebanon":  "Livan",    "Livon":    "Livan",
        "Liban":    "Livan",
        "Iran":     "Eron",     "Iraq":     "Iroq",
        "Palestine":"Falastin", "Syria":    "Suriya",
        "Yemen":    "Yaman",    "Egypt":    "Misr",
        "Turkey":   "Turkiya",  "India":    "Hindiston",
        "China":    "Xitoy",    "Germany":  "Germaniya",
        "France":   "Fransiya", "Italy":    "Italiya",
        "Pakistan": "Pokiston", "Afghanistan": "Afgʻoniston",
        "Libya":    "Liviya",   "Gaza":     "Gʻazo",
        "Saudi Arabia": "Saudiya Arabistoni",
    }
    for field in ("sarlavha_uz", "jumla1_uz", "jumla2_uz", "location_uz"):
        val = data.get(field, "")
        if not val or _is_mostly_cyr(val):
            continue  # Kirill — tegmaymiz
        for wrong, right in sorted(_LATIN_UZ_PLACES.items(), key=lambda x: -len(x[0])):
            if wrong in val:
                val = re.sub(r'(?<![a-zA-Z])' + re.escape(wrong) + r'(?![a-zA-Z])', right, val)
        data[field] = val

    # ── UZ LOTIN maydonlar (sarlavha/jumla + script) — joy nomlari tuzatish ─
    _LATIN_PLACES_ALWAYS = {
        # Har doim to'g'rilanadigan xatolar
        "Izrail":     "Isroil",
        "Livon":      "Livan",      # AI xatosi
        "Lebanon":    "Livan",      # inglizcha qolsa
        "Liban":      "Livan",
        "Iroq":       "Iroq",
        "Afgoniston": "Afgʻoniston",
        "Pokiston":   "Pokiston",
    }
    for _uz_f in ("sarlavha_uz", "jumla1_uz", "jumla2_uz", "script_uz"):
        _uz_v = data.get(_uz_f, "")
        if _uz_v and not _is_mostly_cyr(_uz_v):
            import re as _re
            for wrong, right in _LATIN_PLACES_ALWAYS.items():
                if wrong in _uz_v and wrong != right:
                    _uz_v = _re.sub(r'(?<![a-zA-Z])' + _re.escape(wrong) + r'(?![a-zA-Z])',
                                    right, _uz_v)
            data[_uz_f] = _uz_v

    # (Eski kontekst-ga asoslangan tuzatish olib tashlandi —
    #  endi _is_lebanon/_is_libya manba-asosli tuzatish OXIRDA bajariladi)

    # ── Hashtag tekshiruvi — placeholder bo'lsa yangilash ────
    kw_en = data.get("keywords_en", [])
    drj   = data.get("daraja", "xabar")
    for ht_field, lang_code in [("hashtag_uz","uz"), ("hashtag_ru","ru"), ("hashtag_en","en")]:
        htag = data.get(ht_field, "")
        if not htag or _is_fake_hashtag(htag):
            generated = _gen_hashtags(kw_en, lang_code, drj)
            data[ht_field] = generated
            log.warning(f"⚠️  {ht_field} placeholder — yangilandi: {generated}")

    # ── hashtag_uz LOTIN bo'lishi kerak (Kirill→Lotin agar Kirill kelsa) ──
    # Lotin qoladi — lat2cyr QILMAYMIZ

    # ══════════════════════════════════════════════════════════
    # Sarlavha validatsiyasi — xato bo'lsa alohida retry
    # ══════════════════════════════════════════════════════════
    en_title = title  # original inglizcha sarlavha (fallback uchun)

    for lang_key, lang_code in [("sarlavha_uz", "uz"), ("sarlavha_ru", "ru"), ("sarlavha_en", "en")]:
        val = data.get(lang_key, "")
        if not _is_valid_title(val, lang_code):
            log.warning(f"⚠️  {lang_key} yaroqsiz: '{val[:60]}' — qayta so'ralmoqda...")
            # UZ uchun: avval RU sarlavhasidan tarjima qilish (sifat yaxshiroq)
            _src_ru = data.get("sarlavha_ru", "") if lang_code == "uz" else ""
            fixed = _fix_title_only(en_title, lang_code, source_ru=_src_ru)
            if _is_valid_title(fixed, lang_code):
                data[lang_key] = fixed
                log.info(f"✅ {lang_key} tuzatildi: '{fixed}'")
            else:
                # Hali ham xato — UZ va RU uchun bo'sh qolish yaxshi
                # (lat2cyr(inglizcha) = "Трумп агаин фумес" kabi axlat hosil qiladi!)
                # EN kanalda inglizcha kanal uchun orijinal nom ishlatiladi
                if lang_code in ("uz", "ru"):
                    data[lang_key] = ""   # Bo'sh — inglizcha sarlavha UZ/RU kanalda chiqmasin
                    log.warning(f"⚠️  {lang_key} bo'sh qoldirildi (inglizcha sarlavha {lang_code} kanalga mos emas)")
                else:  # EN uchun inglizcha orijinal nom yaxshi
                    data[lang_key] = en_title[:80]
                    log.warning(f"⚠️  {lang_key} fallback (original): '{en_title[:60]}'")

    # ══════════════════════════════════════════════════════════
    # Barcha matn maydonlari — bosh harf + lotin-kirill tuzatish
    # (Groq modeli ba'zan kichik harfdan boshlaydi va lotin aralashtiradi)
    # ══════════════════════════════════════════════════════════
    _LATIN_IN_CYR = re.compile(r'\b[a-zA-Z][a-zA-Z\'ʻʼ]{2,}\b')
    # "prezidentи" — Latin chars followed immediately by Cyrillic (no word boundary!)
    _CYR_CHARS = "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯўқғҳЎҚҒҲ"
    _LATIN_BEFORE_CYR = re.compile(r'[a-zA-Z]{3,}(?=[' + _CYR_CHARS + r'])')

    # ── RU: "на Украине" → "в Украине" ──────────────────────────
    _UA_FIX = [
        ("на Украине", "в Украине"),
        ("На Украине", "В Украине"),
        ("на Украину", "в Украину"),
        ("с Украины",  "из Украины"),
    ]

    # ── RU: lotin ismlar → ruscha kirill ─────────────────────────
    _RU_NAME_FIX = {
        # ── Siyosatchilar / Davlat rahbarlari ──────────────────────────
        "Von der Leyen": "Фон дер Ляйен",
        "Zelensky":    "Зеленский",  "Zelenskyy":  "Зеленский",
        "Zelenskiy":   "Зеленский",  "zelensky":   "Зеленский",
        "Netanyahu":   "Нетаньяху",  "netanyahu":  "Нетаньяху",
        "Lukashenko":  "Лукашенко",  "lukashenko": "Лукашенко",
        "Guterres":    "Гутерреш",   "guterres":   "Гутерреш",
        "Pashinyan":   "Пашинян",    "Tokayev":    "Токаев",
        "Khamenei":    "Хаменеи",    "Blinken":    "Блинкен",
        "Putin":       "Путин",      "Trump":      "Трамп",
        "Biden":       "Байден",     "Macron":     "Макрон",
        "Scholz":      "Шольц",      "Starmer":    "Стармер",
        "Orban":       "Орбан",      "Erdogan":    "Эрдоган",
        "Modi":        "Моди",       "Musk":       "Маск",
        "Johnson":     "Джонсон",    "Sunak":      "Сунак",
        "Meloni":      "Мелони",     "Sinwar":     "Синвар",
        "Abbas":       "Аббас",      "Aliyev":     "Алиев",
        "Mirziyoyev":  "Мирзиёев",  "Mirziyev":   "Мирзиёев",
        "Nazarbayev":  "Назарбаев",  "Lukashenka": "Лукашенко",
        "Pezeshkian":  "Пезешкиан", "Raisi":      "Раиси",
        "Khamenei":    "Хаменеи",   "Nasrallah":  "Насралла",
        "Sinwar":      "Синвар",     "Haniyeh":    "Хания",
        # ── AQSh ma'murlari ────────────────────────────────────────────
        "Hegseth":     "Хегсет",     "hegseth":    "Хегсет",
        "Rubio":       "Рубио",      "rubio":      "Рубио",
        "Sullivan":    "Салливан",   "sullivan":   "Салливан",
        "Austin":      "Остин",      "austin":     "Остин",
        "Blinken":     "Блинкен",    "blinken":    "Блинкен",
        "Pompeo":      "Помпео",     "pompeo":     "Помпео",
        "Bolton":      "Болтон",     "bolton":     "Болтон",
        "Milley":      "Милли",      "milley":     "Милли",
        "Harris":      "Харрис",     "harris":     "Харрис",
        "Pence":       "Пенс",       "pence":      "Пенс",
        "Pelosi":      "Пелоси",     "pelosi":     "Пелоси",
        "Kennedy":     "Кеннеди",    "kennedy":    "Кеннеди",
        "Vance":       "Вэнс",       "vance":      "Вэнс",
        "Gates":       "Гейтс",      "gates":      "Гейтс",
        "Waltz":       "Уолтц",      "waltz":      "Уолтц",
        # ── Xalqaro rahbarlar ──────────────────────────────────────────
        "Lavrov":      "Лавров",     "lavrov":     "Лавров",
        "Shoigu":      "Шойгу",      "shoigu":     "Шойгу",
        "Peskov":      "Песков",     "peskov":     "Песков",
        "Medvedev":    "Медведев",   "medvedev":   "Медведев",
        "Mishustin":   "Мишустин",   "mishustin":  "Мишустин",
        "Patrushev":   "Патрушев",   "patrushev":  "Патрушев",
        "Gerasimov":   "Герасимов",  "gerasimov":  "Герасимов",
        "Syrsky":      "Сырский",    "syrsky":     "Сырский",
        "Zaluzhny":    "Залужный",   "zaluzhny":   "Залужный",
        "Arestovich":  "Арестович",  "arestovich": "Арестович",
        "Kim":         "Ким",
        "Xi":          "Си",         "Jinping":    "Цзиньпин",
        "Lula":        "Лула",       "lula":       "Лула",
        "Milei":       "Милей",      "milei":      "Милей",
        "Albanese":    "Альбанезе",  "albanese":   "Альбанезе",
        # ── Tashkilotlar / Harakatlar ──────────────────────────────────
        "Hezbollah":   "Хезболла",   "Houthis":    "хуситы",
        "Houthi":      "хуситы",     "Hamas":      "ХАМАС",
        "ISIS":        "ИГИЛ",       "ISIL":       "ИГИЛ",
        "Taliban":     "Талибан",    "Al-Qaeda":   "Аль-Каида",
        "Wagner":      "Вагнер",     "Azov":       "Азов",
        # ── Shaharlari / Joylari ───────────────────────────────────────
        "Zaporizhzhia":"Запорожье",  "Mariupol":   "Мариуполь",
        "Kharkiv":     "Харьков",    "Donbas":     "Донбасс",
        "Donetsk":     "Донецк",     "Luhansk":    "Луганск",
        "Kyiv":        "Киев",       "kyiv":       "Киев",
        "Odesa":       "Одесса",     "Kherson":    "Херсон",
        "Bakhmut":     "Бахмут",     "Avdiivka":   "Авдеевка",
        "Kherson":     "Херсон",     "Mykolaiv":   "Николаев",
        "Bucha":       "Буча",       "Irpin":      "Ирпень",
        "Crimea":      "Крым",       "crimea":     "Крым",
        "Sevastopol":  "Севастополь","Simferopol": "Симферополь",
        "Gaza":        "Газа",       "Rafah":      "Рафах",
        "Ramallah":    "Рамалла",    "Jenin":      "Дженин",
        "Fallujah":    "Эль-Фаллуджа",
        "Aleppo":      "Алеппо",     "aleppo":     "Алеппо",
        "Raqqa":       "Ракка",      "Mosul":      "Мосул",
        "Tripoli":     "Триполи",    "tripoli":    "Триполи",
        "Benghazi":    "Бенгази",    "benghazi":   "Бенгази",
        "Bamako":      "Бамако",     "bamako":     "Бамако",
        "Sahel":       "Сахель",     "sahel":      "Сахель",
        "Mali":        "Мали",       "mali":       "Мали",
        "Niger":       "Нигер",      "Burkina":    "Буркина",
        "Sudan":       "Судан",      "sudan":      "Судан",
        "Khartoum":    "Хартум",     "khartoum":   "Хартум",
        "Belgrade":    "Белград",    "belgrade":   "Белград",
        "Serbia":      "Сербия",     "serbia":     "Сербия",
        "Kosovo":      "Косово",     "kosovo":     "Косово",
        "Tbilisi":     "Тбилиси",   "tbilisi":    "Тбилиси",
        "Yerevan":     "Ереван",     "yerevan":    "Ереван",
        "Baku":        "Баку",       "baku":       "Баку",
        "Karabakh":    "Карабах",    "karabakh":   "Карабах",
        "Nagorno":     "Нагорный",   "nagorno":    "Нагорный",
    }

    def _fix_latin_in_cyr_text(text: str, lang: str = "uz") -> str:
        """Kirill matnidagi lotin so'zlarni (3+ harf) kirill ga o'tkazish.
        Shuningdek 'prezidentи' kabi Latin+Kirill aralash so'zlarni ham tuzatadi."""
        if not text:
            return text
        # 1-usul: "prezidentи" — Latin qismi Kirill bilan tutashgan
        def _fix_mixed(m):
            w = m.group(0)
            return w if w.isupper() else lat2cyr(w.lower())
        text = _LATIN_BEFORE_CYR.sub(_fix_mixed, text)
        # 2-usul: to'liq lotin so'z (word boundary bor)
        if not _LATIN_IN_CYR.search(text):
            return text
        def _convert(m):
            w = m.group(0)
            if w.isupper():   # NATO, UN, USA kabi — saqlash
                return w
            if lang == "ru":
                return w   # RU uchun lotin ismlar alohida lug'at bilan tuzatiladi
            return lat2cyr(w.lower())
        return _LATIN_IN_CYR.sub(_convert, text)

    def _fix_ru_latin_names(text: str) -> str:
        """Ruscha matnda qolgan lotin ismlarni kirillga almashtirish."""
        if not text:
            return text
        for lat, cyr in sorted(_RU_NAME_FIX.items(), key=lambda x: -len(x[0])):
            if lat in text:
                text = re.sub(r'(?<![a-zA-Z])' + re.escape(lat) + r'(?![a-zA-Z])', cyr, text)
        return text

    # ── Rus tiliga xos lotin→kirill transliteratsiya (noma'lum ismlar uchun) ──
    _RU_TRANSLIT = [
        # 2-harf kombinatsiyalar avval
        ("zh", "ж"), ("Zh", "Ж"), ("ZH", "Ж"),
        ("sh", "ш"), ("Sh", "Ш"), ("SH", "Ш"),
        ("ch", "ч"), ("Ch", "Ч"), ("CH", "Ч"),
        ("ts", "ц"), ("Ts", "Ц"), ("TS", "Ц"),
        ("sch","щ"), ("Sch","Щ"),
        ("kh", "х"), ("Kh", "Х"), ("KH", "Х"),
        ("yu", "ю"), ("Yu", "Ю"), ("YU", "Ю"),
        ("ya", "я"), ("Ya", "Я"), ("YA", "Я"),
        ("yo", "ё"), ("Yo", "Ё"),
        ("ye", "е"), ("Ye", "Е"),
        # Yakka harflar
        ("A","А"), ("B","Б"), ("V","В"), ("G","Г"), ("D","Д"),
        ("E","Е"), ("Z","З"), ("I","И"), ("J","Й"), ("K","К"),
        ("L","Л"), ("M","М"), ("N","Н"), ("O","О"), ("P","П"),
        ("R","Р"), ("S","С"), ("T","Т"), ("U","У"), ("F","Ф"),
        ("X","Х"), ("Y","Й"), ("W","В"), ("H","Х"), ("Q","К"),
        ("a","а"), ("b","б"), ("v","в"), ("g","г"), ("d","д"),
        ("e","е"), ("z","з"), ("i","и"), ("j","й"), ("k","к"),
        ("l","л"), ("m","м"), ("n","н"), ("o","о"), ("p","п"),
        ("r","р"), ("s","с"), ("t","т"), ("u","у"), ("f","ф"),
        ("x","х"), ("y","й"), ("w","в"), ("h","х"), ("q","к"),
        ("'","ъ"),
    ]

    def _ru_translit_word(word: str) -> str:
        """Bitta lotin so'zni rus kirilliga transliterlash."""
        if not word:
            return word
        # Saqlanadigan so'zlar: NATO, UN, USA, EU, IMF kabi to'liq bosh harflar
        if word.isupper() and len(word) <= 5:
            return word
        result = word
        for lat, cyr_ch in _RU_TRANSLIT:
            result = result.replace(lat, cyr_ch)
        return result

    def _fix_ru_remaining_latin(text: str) -> str:
        """Lug'at bilan hal qilinmagan lotin so'zlarni rus translit bilan kirillga."""
        if not text:
            return text
        # Faqat 3+ harf lotin so'zlar (qisqartmalar EMAS)
        def _convert(m):
            w = m.group(0)
            # Bosh harfli qisqartmalar (NATO, UN) — saqlaymiz
            if w.isupper() and len(w) <= 5:
                return w
            return _ru_translit_word(w)
        return _LATIN_IN_CYR.sub(_convert, text)

    # UZ LOTIN maydoni: bosh harf (lat2cyr QILMAYMIZ!)
    for _f in ("sarlavha_uz", "jumla1_uz", "jumla2_uz"):
        val = data.get(_f, "")
        if not val:
            continue
        # Agar Kirill bo'lsa — tegmaymiz (sarlavha retry qiladi)
        if not _is_mostly_cyr(val) and val and val[0].islower():
            val = val[0].upper() + val[1:]
        data[_f] = val

    # RU Kirill maydoni: bosh harf + "в Украине" + lotin ismlar tuzatish
    for _f in ("sarlavha_ru", "jumla1_ru", "jumla2_ru", "script_ru"):
        val = data.get(_f, "")
        if not val:
            continue
        # "на Украине" → "в Украине"
        for wrong, right in _UA_FIX:
            val = val.replace(wrong, right)
        # 1) Lug'at bilan ma'lum lotin ismlarni kirillga
        val = _fix_ru_latin_names(val)
        # 2) "prezidentи" kabi aralash so'zlar (Latin qism + Kirill davom)
        val = _LATIN_BEFORE_CYR.sub(
            lambda m: m.group(0) if m.group(0).isupper() else lat2cyr(m.group(0).lower()),
            val
        )
        # 3) Qolgan noma'lum lotin so'zlarni ru-translit bilan kirillga
        #    (Hegseth, voennaya, sila kabi — lug'atda yo'q)
        if _LATIN_IN_CYR.search(val):
            before = val
            val = _fix_ru_remaining_latin(val)
            if val != before:
                log.debug(f"  RU lotin fallback: '{before[:60]}' → '{val[:60]}'")
        if val and val[0].islower():
            val = val[0].upper() + val[1:]
        data[_f] = val

    # EN maydoni: bosh harf
    for _f in ("sarlavha_en", "jumla1_en", "jumla2_en", "script_en"):
        val = data.get(_f, "")
        if val and val[0].islower():
            data[_f] = val[0].upper() + val[1:]

    # ══════════════════════════════════════════════════════════
    # SCRIPT DUPLICATE HEADLINE FIX
    # Agar script sarlavha bilan bir xil so'zlar bilan boshlansa — o'chirish.
    # Voiceover sarlavhani takrorlamasligi kerak (kadr ustida sarlavha ko'rinadi).
    # ══════════════════════════════════════════════════════════
    def _title_words(text: str) -> set:
        """Sarlavhadagi muhim so'zlar (stop-so'zlarsiz, kichik harf)."""
        stops = {"va","bilan","dan","ga","ni","da","ham","bir","bu","u","o'","the","a",
                 "an","and","or","of","in","on","at","to","by","is","are","was","were",
                 "в","и","на","с","по","за","от","для","при","под","над","из","к"}
        return {w.lower().strip(".,!?;:\"'«»") for w in text.split()
                if len(w) > 3 and w.lower() not in stops}

    def _strip_duplicate_start(script: str, sarlavha: str) -> str:
        """Script birinchi jumla sarlavha bilan juda o'xshash bo'lsa — uni o'chirish."""
        if not script or not sarlavha:
            return script
        # Birinchi jumlani ajratish
        first_end = -1
        for sep in (". ", "! ", "? ", ".\n"):
            idx = script.find(sep)
            if idx != -1:
                if first_end == -1 or idx < first_end:
                    first_end = idx + 1
        if first_end <= 0 or first_end > 200:
            return script  # Birinchi jumla topilmadi yoki juda uzun
        first_sent = script[:first_end].strip()
        title_kws  = _title_words(sarlavha)
        sent_kws   = _title_words(first_sent)
        if not title_kws:
            return script
        # 60%dan ko'p so'z mos kelsa — sarlavhani takrorlaydi
        overlap = len(title_kws & sent_kws)
        if overlap / len(title_kws) >= 0.60:
            remainder = script[first_end:].strip()
            if len(remainder) > 100:  # Qolgan qism yetarli bo'lsa
                log.debug(f"  Script duplicate start removed: '{first_sent[:60]}'")
                return remainder
        return script

    for _lang in ("uz", "ru", "en"):
        _sc_f = f"script_{_lang}"
        _sv_f = f"sarlavha_{_lang}"
        _sc   = data.get(_sc_f, "")
        _sv   = data.get(_sv_f, "")
        if _sc and _sv:
            data[_sc_f] = _strip_duplicate_start(_sc, _sv)

    # ── Eski nom uchun moslik ─────────────────────────────────
    data["sarlavha"]             = data.get("sarlavha_uz", "")
    data["jumla1"]               = data.get("jumla1_uz", "")
    data["jumla2"]               = data.get("jumla2_uz", "")
    data["hashtaglar"]           = data.get("hashtag_uz", "")
    data["youtube_script_latin"] = data.get("script_uz", "")
    data["location"]             = data.get("location_uz", "")
    data.setdefault("keywords_ru", [])

    # ════════════════════════════════════════════════════════════
    # LEBANON / LIBYA — manba asosida qat'iy tuzatish
    #
    #  Inglizcha  →  Ўзбекча/Русча   Lotin ўзбек (TTS)
    #  ─────────────────────────────────────────────
    #  lebanon    →  Ливан           Livan
    #  liban      →  Ливан           Livan
    #  beirut     →  Ливан           Livan
    #  lebanese   →  Ливан           Livan
    #  hezbollah  →  Ливан           Livan
    #  nasrallah  →  Ливан           Livan
    #  ─────────────────────────────────────────────
    #  libya      →  Ливия           Liviya
    #  libyan     →  Ливия           Liviya
    #  tripoli    →  Ливия           Liviya
    #  benghazi   →  Ливия           Liviya
    #  haftar     →  Ливия           Liviya
    #  tobruk     →  Ливия           Liviya
    # ════════════════════════════════════════════════════════════
    def _force_country(d, wrong_cyr, right_cyr, wrong_lat, right_lat):
        """Barcha string maydondlarda wrong → right almashtirish."""
        cnt = 0
        for fld, val in list(d.items()):
            if not isinstance(val, str):
                continue
            new = val.replace(wrong_cyr, right_cyr).replace(wrong_lat, right_lat)
            if new != val:
                d[fld] = new
                cnt += 1
        return cnt

    if _is_lebanon and not _is_libya:
        n = _force_country(data, "Ливия", "Ливан", "Liviya", "Livan")
        if n:
            log.info(f"Lebanon fix: Ливия->Ливан, Liviya->Livan ({n} maydon)")
    elif _is_libya and not _is_lebanon:
        n = _force_country(data, "Ливан", "Ливия", "Livan", "Liviya")
        if n:
            log.info(f"Libya fix: Ливан->Ливия, Livan->Liviya ({n} maydon)")

    # ══════════════════════════════════════════════════════════
    # jumla1 / jumla2 bo'sh bo'lsa — script dan yoki description dan to'ldirish
    # ══════════════════════════════════════════════════════════
    _desc_clean = (description or "").strip()

    for _lang in ("uz", "ru", "en"):
        _j1_k  = f"jumla1_{_lang}"
        _j2_k  = f"jumla2_{_lang}"
        _sc_k  = f"script_{_lang}"
        _sv_k  = f"sarlavha_{_lang}"

        j1  = data.get(_j1_k, "").strip()
        j2  = data.get(_j2_k, "").strip()
        sv  = data.get(_sv_k, "").strip()
        sc  = data.get(_sc_k, "").strip()

        # ─── jumla1 to'ldirish ───────────────────────────────
        if not j1 or j1 == sv:
            # 1-usul: script dan birinchi 70 so'z
            if sc and len(sc.split()) >= 15:
                words   = sc.split()
                part    = " ".join(words[:70])
                # UZ uchun lotin qoladi (lat2cyr QILMAYMIZ)
                # So'nggi yarim jumlani kes (nuqta yoki undov bilan tugasin)
                for sep in (". ", "! ", "? "):
                    last = part.rfind(sep)
                    if last > len(part) // 2:
                        part = part[:last + 1].strip()
                        break
                if _lang == "uz" and _is_mostly_cyr(part):
                    data[_j1_k] = part
                    log.debug(f"jumla1_uz script dan to'ldirildi")
                elif _lang == "ru" and any(c in part for c in "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"):
                    data[_j1_k] = part
                    log.debug(f"jumla1_ru script dan to'ldirildi")
                elif _lang == "en":
                    data[_j1_k] = part
                    log.debug(f"jumla1_en script dan to'ldirildi")

        # 2-usul (EN va RU uchun): article description'dan
        j1 = data.get(_j1_k, "").strip()
        if not j1 or j1 == sv:
            if _lang == "en":
                if _desc_clean and _desc_clean.strip() != title.strip():
                    data[_j1_k] = _desc_clean[:500]
                else:
                    # Description = title bo'lsa — sarlavhani takrorlamaslik
                    data[_j1_k] = ""

        # ─── jumla2 to'ldirish (jumla1 dan ikkinchi qism) ────
        j1 = data.get(_j1_k, "").strip()
        j2 = data.get(_j2_k, "").strip()
        if j1 and not j2 and sc and len(sc.split()) >= 80:
            # Script dan ikkinchi yarmi
            words = sc.split()
            half  = len(words) // 2
            part2 = " ".join(words[half: half + 60])
            # UZ: lotin qoladi (lat2cyr QILMAYMIZ)
            for sep in (". ", "! ", "? "):
                last = part2.rfind(sep)
                if last > len(part2) // 2:
                    part2 = part2[:last + 1].strip()
                    break
            if _lang == "uz" and _is_mostly_cyr(part2):
                data[_j2_k] = part2
            elif _lang == "ru" and any(c in part2 for c in "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"):
                data[_j2_k] = part2
            elif _lang == "en":
                data[_j2_k] = part2

    return data
