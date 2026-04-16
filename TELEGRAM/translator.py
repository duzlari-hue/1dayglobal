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
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")  # fallback

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
    """Агар сарлавҳа CAPS бўлса — Sentence Case га ўтказиш."""
    if not text:
        return text
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return text
    upper_n = sum(1 for c in letters if c.isupper())
    # 50%дан кўп катта ҳарф бўлса — тузат
    if upper_n / len(letters) > 0.5:
        lowered = text.lower()
        return lowered[0].upper() + lowered[1:] if lowered else lowered
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
    "Египет":              "Миср",
    "Турция":              "Туркия",
    "Иордания":            "Иордания",
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
    # (AI баъзан рус ва ўзбекча аралаш нотўғри шакл яратади)
    "Ирон":                "Эрон",      # AI: Ирон → тўғри: Эрон
    "Пакистон":            "Покистон",  # AI: Пакистон → тўғри: Покистон (П-О не П-А)
    "Сауди Арабистон":     "Саудия Арабистони",  # AI варианти
    "Сауди Арабистони":    "Саудия Арабистони",  # AI варианти
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
}

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
    prompt = (
        f"Translate this news headline to {instruction}\n"
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


def groq_ask(prompt, max_tokens=2500, retries=4):
    """Gemini 2.0 Flash API; xato bo'lsa OpenRouter fallback."""
    # ── 1. Gemini 2.0 Flash ──────────────────────────────────
    if GEMINI_API_KEY:
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature":     0.3,
                "maxOutputTokens": max_tokens,
            },
        }
        for attempt in range(retries):
            try:
                r = requests.post(
                    _GEMINI_URL,
                    params={"key": GEMINI_API_KEY},
                    headers={"Content-Type": "application/json"},
                    json=body,
                    timeout=60,
                )
                if r.status_code == 429:
                    wait = 15 * (attempt + 1)   # 15s, 30s, 45s, 60s
                    log.warning(f"Gemini limit — {wait}s kutilmoqda...")
                    time.sleep(wait)
                    continue
                if r.status_code == 401:
                    log.warning("Gemini API key yaroqsiz — OpenRouter ga o'tilmoqda")
                    break
                if r.status_code == 400:
                    log.warning(f"Gemini 400: {r.text[:200]}")
                    raise Exception(f"Gemini 400 xato: {r.text[:100]}")
                r.raise_for_status()
                data = r.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as e:
                if "400 xato" in str(e):
                    raise
                if attempt < retries - 1:
                    log.warning(f"Gemini xato ({attempt+1}/{retries}): {e}")
                    time.sleep(8)
                else:
                    log.warning(f"Gemini {retries} urinishdan keyin xato — OpenRouter ga o'tilmoqda")

    # ── 2. OpenRouter fallback ───────────────────────────────
    if not OPENROUTER_API_KEY:
        raise Exception("Na Gemini, na OpenRouter API key topilmadi!")

    log.info("  ↩️  OpenRouter fallback (openai/gpt-4o-mini)...")
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://birkunday.com",
        "X-Title":       "1Kun Global News",
    }
    body = {
        "model":       "openai/gpt-4o-mini",
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens":  max_tokens,
    }
    for attempt in range(3):
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers, json=body, timeout=60,
            )
            if r.status_code == 429:
                time.sleep(20)
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt < 2:
                time.sleep(10)
            else:
                raise Exception(f"OpenRouter fallback ham xato: {e}")
    raise Exception("Barcha tarjimon urinishlari muvaffaqiyatsiz")


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

    prompt = f"""You are a professional multilingual news editor for "1Kun Global" Uzbek news channel.

News title: {title}
News details: {description}

⚠️ CRITICAL: sarlavha_uz, jumla1_uz, jumla2_uz, location_uz, hashtag_uz fields MUST be in Uzbek CYRILLIC script.
DO NOT write English in these fields. TRANSLATE everything into Uzbek Cyrillic.
Example: "Six months after ceasefire" → "Оташбас бошланганидан олти ой ўтгач"

Return ONLY valid JSON, no extra text, no markdown:
{{
  "sarlavha_uz": "⚠️ ФАҚАТ ЎЗБЕК КИРИЛЛ АЛИФБОСИДА — инглизча ёзма! 5-8 сўз, sentence case. Намуна: 'Трамп Европага янги божхона солиғини эълон қилди'. Trump=Трамп, Biden=Байден, NATO=НАТО, fumes=ғазабланди, rant=танқид",
  "jumla1_uz": "⚠️ ФАҚАТ ЎЗБЕК КИРИЛЛ — инглизча ёзма! Воқеанинг асосий мазмуни, 2 жумла.",
  "jumla2_uz": "⚠️ ФАҚАТ ЎЗБЕК КИРИЛЛ — инглизча ёзма! Қўшимча тафсилот, 2 жумла.",
  "sarlavha_ru": "Заголовок 5-8 слов на русском, sentence case (только первое слово и имена собственные с заглавной). Пример: 'Трамп объявил новые пошлины для Европы'",
  "jumla1_ru": "Главное событие, 2 предложения на русском языке",
  "jumla2_ru": "Дополнительные детали, 2 предложения на русском",
  "sarlavha_en": "English headline 5-8 words, sentence case (only first word and proper nouns capitalized). Example: 'Trump announces new tariffs on European goods'",
  "jumla1_en": "Main event 2 sentences in English",
  "jumla2_en": "Additional details 2 sentences in English",
  "script_uz": "Efirda 1KUN Global. [450-500 so'z, SOF O'ZBEK LOTIN tilida — bu TTS uchun. Ruscha so'z EMAS. Xorijiy nomlar: Trump=Tramp, Biden=Bayden, Netanyahu=Netanyaxu. Kontekst, tarix, tafsilot qo'sh.]",
  "script_ru": "В эфире 1ДЕНЬ Global. [450-500 слов на русском языке. Добавь контекст, историю, детали.] Это был 1ДЕНЬ Global. До следующих новостей.",
  "script_en": "This is 1DAY Global. [450-500 words in English. Add context, history, details.] That was 1DAY Global. Stay tuned for more.",
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
  Lebanon=Ливия, Egypt=Миср, Turkey=Туркия,
  Jordan=Урдун, Libya=Либия (Afrika, NOT Lebanon!),
  Morocco=Мароқаш, Algeria=Жазоир, Sudan=Судон, Ethiopia=Ҳабашистон,
  Saudi Arabia=Саудия Арабистони, UAE=БАА, Gaza=Ғазо,
  Islamabad=Исломобод, Tehran=Теҳрон, Damascus=Дамашқ, Baghdad=Бағдод,
  Kabul=Қобул, Delhi=Деҳли, Ankara=Анқара, Istanbul=Истанбул,
  Beirut=Байрут, Riyadh=Риёд, Doha=Доҳа, Tokyo=Токио (NOT Токиё)
- UZBEK PLACE NAMES for script_uz (LATIN TTS):
  Israel=Isroil (NOT Izrail!), Lebanon=Liviya),
  Iran=Eron, Iraq=Iroq, Palestine=Falastin, Syria=Suriya, Gaza=Gʻazo,
  Turkey=Turkiya, Egypt=Misr, Saudi Arabia=Saudiya Arabistoni
- search_queries: REAL EVENT footage only, NO studio/anchor/presenter. Use EXACT names from the news.
- keywords_en: 5 SPECIFIC proper nouns — person names, countries, organizations.
- shot_list: 6 shots that tell the visual story. Each "search" must target FIELD footage — NO anchors, NO studio, NO panel, NO interview, NO analysis, NO presenter. Use specific locations, people, actions. Include year 2026."""

    try:
        data = parse_json(groq_ask(prompt, max_tokens=3000))
    except Exception as e:
        log.warning(f"Groq tarjima xato: {e}")
        data = {
            "sarlavha_uz":  lat2cyr(title[:50]),
            "jumla1_uz":    lat2cyr(title),
            "jumla2_uz":    "",
            "sarlavha_ru":  title[:50],
            "jumla1_ru":    title,
            "jumla2_ru":    "",
            "sarlavha_en":  title[:50],
            "jumla1_en":    title,
            "jumla2_en":    "",
            "script_uz":    f"{title}",
            "script_ru":    f"{title} Это был 1ДЕНЬ Global. До следующих новостей.",
            "script_en":    f"{title} That was 1DAY Global. Stay tuned for more.",
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

    # ── script_uz — Lotin, TTS uchun; lotin joy nomlari tuzatish ─
    _LATIN_PLACES = {
        # Xato → To'g'ri (lotin o'zbek TTS uchun)
        "Izrail":   "Isroil",
        "Isroil":   "Isroil",   # To'g'ri — o'zgartirmaslik
        "Liviya":   "Liviya",   # Bu Liviya (Afrika) — LIBAN emas!
        "Livon":    "Livan",    # AI xatosi
        "Livan":    "Livan",    # AI xatosi — Lebanon = Liban
        "Lebanon":  "Livan",
        "Iroq":     "Iroq",
        "Afgoniston": "Afgʻoniston",
        "Pokiston": "Pokiston",
    }
    script = data.get("script_uz", "")
    if script:
        import re as _re
        for wrong, right in _LATIN_PLACES.items():
            if wrong in script and wrong != right:
                script = _re.sub(r'(?<![a-zA-Z])' + _re.escape(wrong) + r'(?![a-zA-Z])',
                                 right, script)
        data["script_uz"] = script

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
                # Hali ham xato — UZ uchun lotin→kirill fallback
                if lang_code == "uz":
                    fb = lat2cyr(en_title[:80])
                    data[lang_key] = fb
                    log.warning(f"⚠️  {lang_key} fallback (lat2cyr): '{fb[:60]}'")
                else:
                    data[lang_key] = en_title[:80]
                    log.warning(f"⚠️  {lang_key} fallback (original): '{en_title[:60]}'")

    # ── Eski nom uchun moslik ─────────────────────────────────
    data["sarlavha"]             = data.get("sarlavha_uz", "")
    data["jumla1"]               = data.get("jumla1_uz", "")
    data["jumla2"]               = data.get("jumla2_uz", "")
    data["hashtaglar"]           = data.get("hashtag_uz", "")
    data["youtube_script_latin"] = data.get("script_uz", "")
    data["location"]             = data.get("location_uz", "")
    data.setdefault("keywords_ru", [])

    return data
