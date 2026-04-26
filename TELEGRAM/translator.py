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
    _lde(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=False)
except Exception:
    pass

GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")  # openrouter.ai (fallback)

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
    """Sarlavha to'g'ri ekanligini tekshirish."""
    if not text or not text.strip():
        return False
    t = text.strip()
    if t.lower() in _BAD_TITLES:
        return False
    if len(t) < 8:          # juda qisqa
        return False
    if len(t) > 150:        # juda uzun
        return False
    words = t.split()
    if len(words) < 3:      # kamida 3 so'z
        return False
    # UZ sarlavha — kiriллcha harflar bo'lishi shart
    if lang == "uz":
        if not any(c in t for c in _CYR):
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
    brand = {"uz": "#1КУН #Янгилик", "ru": "#1День #Новости", "en": "#1Day #News"}
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
        base = {"uz": "#МУХИМ #1КУН", "ru": "#ВАЖНО #1День", "en": "#BREAKING #1Day"}.get(lang, base)
    elif daraja == "tezkor":
        base = {"uz": "#Тезкор #1КУН", "ru": "#Срочно #1День", "en": "#Urgent #1Day"}.get(lang, base)

    parts = kw_tags + [base]
    return " ".join(parts[:5])


def _fix_title_only(original_en: str, lang: str) -> str:
    """Faqat sarlavhani alohida qayta so'rash (qisqa prompt)."""
    lang_map = {
        "uz": "Uzbek CYRILLIC script (ўзбек кириллида). 5-8 so'z, sentence case. Faqat birinchi so'z va xos ismlar bosh harf. Ruscha so'z ishlatma.",
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
        _geo = ("\nCRITICAL: This is about LEBANON (Ливан/Livan, Middle East). "
                "NEVER use 'Ливия' or 'Liviya' (that is Libya/Africa).\n")
    elif _is_lby and not _is_lbn:
        _geo = ("\nCRITICAL: This is about LIBYA (Ливия/Liviya, North Africa). "
                "NEVER use 'Ливан' or 'Livan' (that is Lebanon/Middle East).\n")
    else:
        _geo = ""

    prompt = (
        f"Translate this news headline to {instruction}\n"
        f"{_geo}"
        f"Headline: {original_en}\n\n"
        f"Return ONLY the translated headline text, nothing else."
    )
    try:
        result = groq_ask(prompt, max_tokens=80).strip()
        # Tirnoq va germetik belgilarni tozalash
        result = result.strip('"\'«»„"')
        if lang == "uz":
            # Lotin bo'lsa — kiriллga
            if not any(c in result for c in _CYR):
                result = lat2cyr(result)
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
        "meta-llama/llama-3.3-70b-instruct:free",  # Tasdiqlangan! (429 = mavjud)
        "deepseek/deepseek-r1:free",               # DeepSeek R1 — kuchli, bepul
        "deepseek/deepseek-chat-v3-0324:free",     # DeepSeek V3
        "mistralai/mistral-7b-instruct:free",      # Klassik bepul model
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


def groq_ask(prompt, max_tokens=2500, retries=2):
    """Tarjimon zanjiri: 1.Gemini (2 urinish, ~45s) → 2.OpenRouter (claude-3-5-haiku)"""
    errors = []

    # ── 1. Gemini 2.0 Flash (asosiy, bepul) ─────────────────
    # 6 marta urinadi: 30s, 60s, 90s, 120s, 150s, 180s kutadi
    if GEMINI_API_KEY:
        try:
            return _ask_gemini(prompt, max_tokens, retries)
        except Exception as e:
            log.warning(f"Gemini muvaffaqiyatsiz → OpenRouter ga o'tilmoqda: {e}")
            errors.append(f"Gemini: {e}")

    # ── 2. OpenRouter — claude-3-5-haiku (yuqori sifat) ─────
    if OPENROUTER_API_KEY:
        log.info("  ↩️  OpenRouter (claude-3-5-haiku)...")
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

⚠️ CRITICAL: sarlavha_uz, jumla1_uz, jumla2_uz, location_uz, hashtag_uz fields MUST be in Uzbek CYRILLIC script.
DO NOT write English in these fields. TRANSLATE everything into Uzbek Cyrillic.
Example: "Six months after ceasefire" → "Оташбас бошланганидан олти ой ўтгач"

Return ONLY valid JSON, no extra text, no markdown:
{{
  "sarlavha_uz": "⚠️ ФАҚАТ ЎЗБЕК КИРИЛЛ АЛИФБОСИДА — инглизча ёзма! 5-8 сўз, sentence case. Намуна: 'Трамп Европага янги божхона солиғини эълон қилди'. Trump=Трамп, Biden=Байден, NATO=НАТО, fumes=ғазабланди, rant=танқид",
  "jumla1_uz": "⚠️ ФАҚАТ ЎЗБЕК КИРИЛЛ — инглизча ёзма! Воқеанинг асосий мазмуни батафсил, 4-5 жумла. Нима бўлди, қаерда, ким, нима учун — барчасини ёз. Тафсилотлар ва контекст қўш.",
  "jumla2_uz": "⚠️ ФАҚАТ ЎЗБЕК КИРИЛЛ — инглизча ёзма! Қўшимча муҳим тафсилотлар, 4-5 жумла. Натижалар, реакциялар, тарихий фон, эксперт фикрлари.",
  "sarlavha_ru": "Заголовок 5-8 слов на РУССКОМ языке (не на английском!), sentence case. Пример: 'Трамп объявил новые пошлины для Европы'",
  "jumla1_ru": "⚠️ ТОЛЬКО РУССКИЙ ЯЗЫК — не пиши по-английски! Главное событие подробно, 4-5 предложений. Что произошло, где, кто, почему — всё подробно.",
  "jumla2_ru": "⚠️ ТОЛЬКО РУССКИЙ ЯЗЫК — не пиши по-английски! Дополнительные детали, 4-5 предложений. Последствия, реакции, исторический контекст.",
  "sarlavha_en": "English headline 5-8 words, sentence case. Example: 'Trump announces new tariffs on European goods'",
  "jumla1_en": "Main event detailed, 4-5 sentences in English. What happened, where, who, why — full context.",
  "jumla2_en": "Additional details 4-5 sentences. Consequences, reactions, historical background.",
  "script_uz": "[450-500 so'z, SOF O'ZBEK LOTIN tilida — bu TTS uchun. Intro/outro yozma. Ruscha so'z EMAS. Xorijiy nomlar: Trump=Tramp, Biden=Bayden, Netanyahu=Netanyaxu. Yangilik mazmunini, kontekstini, tarixini va tafsilotlarini yoz.]",
  "script_ru": "[450-500 слов на русском языке. Без вступления и заключения типа 'В эфире...'. Добавь контекст, историю, детали события.]",
  "script_en": "[450-500 words in English. No intro/outro phrases. Add context, background and details about the event.]",
  "daraja": "muhim OR tezkor OR xabar",
  "hook_uz": "Thumbnail учун қисқа ЎЗБЕК КИРИЛЛ жумла, 3-5 сўз, ҳайратланарли ёки шошилинч. Намуна: 'Дунё ларзага келди!' ёки 'Ҳаммаси ўзгарди'",
  "hook_ru": "Короткая фраза для thumbnail 3-5 слов, интригующая. Пример: 'Мир изменился навсегда!' или 'Это меняет всё'",
  "hook_en": "Short thumbnail hook 3-5 words, urgent/intriguing. Example: 'World shocked!' or 'Everything changes now'",
  "hashtag_uz": "3-5 та мавзуга оид ҳэштег ўзбек кирилл тилида. Мисол: '#Трамп #АҚШ #Иқтисодиёт #1КУН'. Placeholder #УзТег1 ишлатма!",
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
  "location_uz": "Шаҳар ёки давлат ўзбек КИРИЛЛ алифбосида",
  "location_ru": "Город или страна на русском",
  "location_en": "City or country in English"
}}

RULES:
- sarlavha_uz, jumla1_uz, jumla2_uz, location_uz, hashtag_uz: MUST be in Uzbek CYRILLIC script
- script_uz: MUST be in Uzbek LATIN script (for TTS voice synthesis)
- sarlavha fields: sentence case — NOT ALL CAPS, NOT Title Case
- daraja: muhim=war/disaster/crisis, tezkor=politics/economy/diplomacy, xabar=other
- hashtag fields: REAL topic-specific hashtags ONLY. NEVER use placeholders like #УзТег1 #РуТег1 #EnTag1
- UZBEK PLACE NAMES for sarlavha/jumla (CYRILLIC). Use EXACT forms (NOT Russian forms!):
  Iran=Эрон (NOT Иран, NOT Ирон!), Iraq=Ироқ, Afghanistan=Афғонистон,
  Pakistan=Покистон (NOT Пакистан, NOT Пакистон!), India=Ҳиндистон,
  China=Хитой, Israel=Исроил (NOT Израил!), Palestine=Фаластин, Syria=Сурия, Yemen=Яман,
  Lebanon=Ливан, Egypt=Миср, Turkey=Туркия,
  Jordan=Урдун, Libya=Либия (Afrika, NOT Lebanon!),
  Morocco=Мароқаш, Algeria=Жазоир, Sudan=Судон, Ethiopia=Ҳабашистон,
  Saudi Arabia=Саудия Арабистони, UAE=БАА, Gaza=Ғазо,
  Islamabad=Исломобод, Tehran=Теҳрон, Damascus=Дамашқ, Baghdad=Бағдод,
  Kabul=Қобул, Delhi=Деҳли, Ankara=Анқара, Istanbul=Истанбул,
  Beirut=Байрут, Riyadh=Риёд, Doha=Доҳа, Tokyo=Токио (NOT Токиё)
- UZBEK TERMS: ceasefire=ўт очишни тўхтатиш (NOT оташбас, NOT оташкесим!),
  West Bank=Ғарбий соҳил, airstrikes=авиазарба, sanctions=санкциялар,
  negotiations=музокаралар, Jewish/jew=яҳудий (NOT еврей!),
  Jews=яҳудийлар, Israeli=исроиллик, settlement=мустамлака
- UZBEK PLACE NAMES for script_uz (LATIN TTS):
  Israel=Isroil (NOT Izrail!), Lebanon=Livan (NOT Liviya! Liviya=Libya/Afrika),
  Iran=Eron, Iraq=Iroq, Palestine=Falastin, Syria=Suriya, Gaza=Gʻazo,
  Turkey=Turkiya, Egypt=Misr, Saudi Arabia=Saudiya Arabistoni,
  ceasefire=oʻt ochishni toʻxtatish
- search_queries: REAL EVENT footage only, NO studio/anchor/presenter. Use EXACT names from the news.
- keywords_en: 5 SPECIFIC proper nouns — person names, countries, organizations.
- shot_list: 6 shots that tell the visual story. Each "search" must target FIELD footage — NO anchors, NO studio, NO panel, NO interview, NO analysis, NO presenter. Use specific locations, people, actions. Include year 2026."""

    # ── Qisqa prompt (OpenRouter fallback uchun — skriptsiz, ~600 token) ──
    short_prompt = f"""Translate this news to Uzbek Cyrillic, Russian, English. Return ONLY valid JSON (no markdown, no extra text).
{_geo_warning}
Title: {title}
Details: {description}

{{
  "sarlavha_uz": "5-7 so'z FAQAT O'ZBEK KIRIЛЛIDA — inglizcha yozma! Misol: 'Yamanда минали инqiroz davom etmoqda'",
  "jumla1_uz": "FAQAT O'ZBEK KIRIЛЛIDA — 3-4 ta jumla. Nima bo'ldi, qayerda, kim, nima uchun — barchasi kiriллda",
  "jumla2_uz": "FAQAT O'ZBEK KIRIЛЛIDA — 2-3 ta jumla. Natijalar, kontekst, tafsilotlar",
  "sarlavha_ru": "5-7 слов ТОЛЬКО НА РУССКОМ — не по-английски! Пример: 'Минный кризис в Йемене продолжается'",
  "jumla1_ru": "ТОЛЬКО РУССКИЙ — 3-4 предложения. Что произошло, где, кто, почему",
  "jumla2_ru": "ТОЛЬКО РУССКИЙ — 2-3 предложения. Последствия, контекст",
  "sarlavha_en": "5-7 words in English. Example: 'Yemen landmine crisis persists despite truce'",
  "jumla1_en": "3-4 sentences in English. What happened, where, who, why",
  "jumla2_en": "2-3 sentences. Consequences and context",
  "daraja": "muhim OR tezkor OR xabar",
  "hashtag_uz": "#3-4 та хэштег O'ЗБЕК КИРИЛЛИДА. Мисол: '#Яман #Дунё #1КУН'",
  "hashtag_ru": "#3-4 хэштега по-РУССКИ. Пример: '#Йемен #Мир #1День'",
  "hashtag_en": "#3-4 hashtags. Example: '#Yemen #World #1Day'",
  "location_uz": "Joy nomi kiriллda (shahar yoki davlat)",
  "location_ru": "Место по-русски",
  "location_en": "Location in English",
  "keywords_en": ["Person", "Country", "Organization", "Topic", "Term"]
}}
CRITICAL: sarlavha_uz, jumla1_uz, jumla2_uz — FAQAT O'ZBEK KIRILLI (а,б,в,г,д...). Inglizcha YOZMA!
CRITICAL: sarlavha_ru, jumla1_ru, jumla2_ru — FAQAT RUSCHA (а,б,в,г,д...). Inglizcha YOZMA!"""

    # ── 1. Gemini — to'liq prompt (skript, shot_list bilan) ─────────────
    data     = None
    _gem_err = None   # Python 3: except clause var deleted after block — save separately
    try:
        data = parse_json(_ask_gemini(prompt, max_tokens=3000, retries=2))
    except Exception as _e:
        _gem_err = _e
        log.warning(f"Gemini muvaffaqiyatsiz → OpenRouter qisqa so'rov: {_gem_err}")

    # ── 2. OpenRouter — qisqa prompt (skriptsiz, max 700 token) ─────────
    if data is None:
        try:
            raw_or = _ask_openrouter(short_prompt, max_tokens=700)
            data = parse_json(raw_or)
            # Skript maydonlari bo'sh — Gemini ishlamadi
            for _sf in ("script_uz", "script_ru", "script_en",
                        "hook_uz", "hook_ru", "hook_en"):
                data.setdefault(_sf, "")
            data.setdefault("shot_list", [])
            data.setdefault("search_queries", [])
            log.info("✅ OpenRouter qisqa so'rov muvaffaqiyatli")
        except Exception as e_or:
            log.warning(f"Tarjima xato (barcha servislar): Gemini: {_gem_err} | OpenRouter: {e_or}")
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
                "hashtag_uz":   "#Янгилик #Дунё #1КУН",
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

    # ── UZ kirill maydonlar (sarlavha/jumla) — kiriллcha bo'lishi kerak ──
    _CYR_FIELDS = ("sarlavha_uz", "jumla1_uz", "jumla2_uz", "location_uz")

    # O'zbek lotiniga xos belgilar — inglizchadan farqlash uchun
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

    for field in _CYR_FIELDS:
        val = data.get(field, "")
        if not val:
            continue
        if _is_mostly_cyr(val):
            continue  # Allaqachon kirill — yaxshi

        if _is_uzbek_latin(val):
            # O'zbek lotin → kirill (to'g'ri yo'l)
            data[field] = lat2cyr(val)
            log.debug(f"lat2cyr ({field}): o'zbek lotin→kirill")
        else:
            # Inglizcha yoki noto'g'ri til — lat2cyr QILMAYMIZ
            log.warning(f"⚠️  {field} inglizcha: '{val[:50]}' — qayta so'ralmoqda...")
            fixed = _fix_title_only(title, "uz")
            if fixed and _is_mostly_cyr(fixed):
                if "sarlavha" in field:
                    data[field] = fixed
                elif "jumla1" in field:
                    data[field] = fixed  # jumla1 uchun sarlavhani ishlat
                else:
                    data[field] = ""    # jumla2, location — bo'sh qoldir
                log.debug(f"  ✓ {field} tuzatildi: '{data[field][:50]}'")
            else:
                data[field] = ""  # Bo'sh — lat2cyr gibberish'dan yaxshi
                log.warning(f"  ✗ {field} bo'sh qoldirildi (inglizcha tarjima qilinmadi)")

    # ── UZ kirill maydonlarga joy nomlari (rus→o'zbek) ───────
    for field in _CYR_FIELDS:
        val = data.get(field, "")
        if val:
            fixed = _apply_uz_places(val)
            if fixed != val:
                log.debug(f"joy nomi tuzatildi ({field}): '{val[:40]}' → '{fixed[:40]}'")
                data[field] = fixed

    # ── UZ kirill maydonlarga atama tuzatish (оташбас→оташкесим va b.) ──
    for field in _CYR_FIELDS + ("jumla1_uz", "jumla2_uz"):
        val = data.get(field, "")
        if val:
            fixed = _apply_uz_terms(val)
            if fixed != val:
                log.debug(f"atama tuzatildi ({field}): '{val[:40]}' → '{fixed[:40]}'")
                data[field] = fixed

    # ── script_uz — Lotin, TTS uchun; lotin joy nomlari tuzatish ─
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
    script = data.get("script_uz", "")
    if script:
        import re as _re
        for wrong, right in _LATIN_PLACES_ALWAYS.items():
            if wrong in script and wrong != right:
                script = _re.sub(r'(?<![a-zA-Z])' + _re.escape(wrong) + r'(?![a-zA-Z])',
                                 right, script)
        data["script_uz"] = script

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

    # ── hashtag_uz kiriллcha bo'lishi kerak ──────────────────
    htag = data.get("hashtag_uz", "")
    if htag and not any(c in htag for c in _CYR):
        data["hashtag_uz"] = lat2cyr(htag)

    # ══════════════════════════════════════════════════════════
    # Sarlavha validatsiyasi — xato bo'lsa alohida retry
    # ══════════════════════════════════════════════════════════
    en_title = title  # original inglizcha sarlavha (fallback uchun)

    for lang_key, lang_code in [("sarlavha_uz", "uz"), ("sarlavha_ru", "ru"), ("sarlavha_en", "en")]:
        val = data.get(lang_key, "")
        if not _is_valid_title(val, lang_code):
            log.warning(f"⚠️  {lang_key} yaroqsiz: '{val[:60]}' — qayta so'ralmoqda...")
            fixed = _fix_title_only(en_title, lang_code)
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
    _LATIN_IN_CYR = re.compile(r'\b[a-zA-Z][a-zA-Z\'\u02BB\u02BC]{2,}\b')

    def _fix_latin_in_cyr_text(text: str) -> str:
        """Kirill matnidagi lotin so'zlarni (3+ harf) kirill ga o'tkazish."""
        if not text or not _LATIN_IN_CYR.search(text):
            return text
        def _convert(m):
            w = m.group(0)
            if w.isupper():   # NATO, UN, USA kabi — saqlash
                return w
            return lat2cyr(w.lower())
        return _LATIN_IN_CYR.sub(_convert, text)

    # UZ Kirill maydoni: lotin so'zlar + bosh harf
    for _f in ("sarlavha_uz", "jumla1_uz", "jumla2_uz"):
        val = data.get(_f, "")
        if not val:
            continue
        val = _fix_latin_in_cyr_text(val)
        if val and val[0].islower():
            val = val[0].upper() + val[1:]
        data[_f] = val

    # RU Kirill maydoni: bosh harf
    for _f in ("sarlavha_ru", "jumla1_ru", "jumla2_ru"):
        val = data.get(_f, "")
        if val and val[0].islower():
            data[_f] = val[0].upper() + val[1:]

    # EN maydoni: bosh harf
    for _f in ("sarlavha_en", "jumla1_en", "jumla2_en", "script_en"):
        val = data.get(_f, "")
        if val and val[0].islower():
            data[_f] = val[0].upper() + val[1:]

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
                if _lang == "uz" and not _is_mostly_cyr(part) and _is_uzbek_latin(part):
                    part = lat2cyr(part)
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
            if _lang == "uz" and not _is_mostly_cyr(part2) and _is_uzbek_latin(part2):
                part2 = lat2cyr(part2)
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
