"""Цены с lis-skins и курс доллара.

lis-skins отдаёт открытый прайс-лист в долларах, без проверки «я не робот».
Рубли считаем по курсу ЦБ (можно подправить множителем RATE_MULTIPLIER).
"""

import difflib
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET

PRICE_URL = "https://lis-skins.com/market_export_json/csgo.json"
RATE_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
OPEN_RATE_URL = "https://open.er-api.com/v6/latest/USD"

_cache = {}


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def price_list():
    """{название скина: {"price": доллары, "url": ссылка}} — скачивается один раз за запуск."""
    if "prices" not in _cache:
        items = {}
        for it in _get_json(PRICE_URL):
            items[it["name"]] = {"price": it["price"], "url": it["url"]}
        _cache["prices"] = items
    return _cache["prices"]


def _rate_cbr_mirror():
    return _get_json(RATE_URL)["Valute"]["USD"]["Value"]


def _rate_cbr():
    req = urllib.request.Request(CBR_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        root = ET.fromstring(resp.read())
    for v in root.findall("Valute"):
        if v.findtext("CharCode") == "USD":
            return float(v.findtext("Value").replace(",", ".")) / float(v.findtext("Nominal"))
    raise ValueError("нет USD в ответе ЦБ")


def _rate_open():
    return _get_json(OPEN_RATE_URL)["rates"]["RUB"]


def usd_rate():
    """Курс доллара: ЦБ (два адреса), если оба недоступны — открытый курсовой сервис."""
    if "rate" not in _cache:
        errors = []
        for source in (_rate_cbr_mirror, _rate_cbr, _rate_open):
            try:
                rate = source()
                break
            except Exception as e:
                errors.append(f"{source.__name__}: {e}")
        else:
            raise RuntimeError("Не удалось узнать курс доллара: " + "; ".join(errors))
        _cache["rate"] = rate * float(os.environ.get("RATE_MULTIPLIER", "1"))
    return _cache["rate"]


def ru_link(url):
    """https://lis-skins.com/market/... -> https://lis-skins.com/ru/market/... (как в постах)."""
    return url.replace("lis-skins.com/market/", "lis-skins.com/ru/market/")


def format_rub(rub):
    """10910.4 -> «10.910₽» (тысячи через точку, как в канале)."""
    return f"{round(rub):,}".replace(",", ".") + "₽"


def find(name):
    """Точное совпадение — dict со скином, иначе None."""
    item = price_list().get(name)
    if item:
        return {"name": name, "url": ru_link(item["url"]), "usd": item["price"],
                "rub": item["price"] * usd_rate()}
    return None


# ---------- Русские названия -> английские (словарь из файлов самой игры CS2) ----------

HERE = os.path.dirname(os.path.abspath(__file__))

# как Кирилл называет оружие в списках -> официальное английское название
WEAPON_ALIASES = {
    "мото": "Moto Gloves", "спорт": "Sport Gloves", "водител": "Driver Gloves", "специалист": "Specialist Gloves",
    "бладхаунд": "Bloodhound Gloves", "гидр": "Hydra Gloves", "обмотк": "Hand Wraps", "клык": "Broken Fang Gloves",
    "бабочк": "Butterfly Knife", "керамбит": "Karambit", "когт": "Talon Knife", "коготь": "Talon Knife",
    "м9": "M9 Bayonet", "m9": "M9 Bayonet", "штык": "Bayonet", "фальшион": "Falchion Knife", "боуи": "Bowie Knife",
    "охотнич": "Huntsman Knife", "складн": "Flip Knife", "флип": "Flip Knife", "тычков": "Shadow Daggers",
    "стилет": "Stiletto Knife", "навах": "Navaja Knife", "урсус": "Ursus Knife", "бродяг": "Nomad Knife",
    "скелет": "Skeleton Knife", "паракорд": "Paracord Knife", "выживан": "Survival Knife", "кукри": "Kukri Knife",
    "классическ": "Classic Knife", "крюк": "Gut Knife", "дигл": "Desert Eagle", "глок": "Glock-18",
    "юсп": "USP-S", "usp": "USP-S", "калаш": "AK-47", "ак": "AK-47", "авп": "AWP", "эмка": "M4A4",
    "m4a1": "M4A1-S", "м4а1": "M4A1-S", "m4a4": "M4A4", "м4а4": "M4A4", "револ": "R8 Revolver",
}


def _norm(s):
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[«»\"'„“”]", "", s)
    return " ".join(s.replace("-", " ").split())


def _dictionary():
    if "dict" not in _cache:
        with open(os.path.join(HERE, "ru_names.json"), encoding="utf-8") as f:
            data = json.load(f)
        _cache["dict"] = ({_norm(k): v for k, v in data["skins"].items()},
                          {_norm(k): v for k, v in data["weapons"].items()})
    return _cache["dict"]


# как скины называют игроки, если официальный перевод другой (в игре Doppler = «Волны»)
SKIN_ALIASES = {
    "доплер": "Doppler", "допплер": "Doppler", "гамма доплер": "Gamma Doppler", "гамма допплер": "Gamma Doppler",
    "фейд": "Fade", "фэйд": "Fade", "марбл фейд": "Marble Fade", "мрамор": "Marble Fade",
    "кейс харденед": "Case Hardened", "кейс харден": "Case Hardened", "большая волна": "Big Swell",
    "тигровый зуб": "Tiger Tooth", "дамаск": "Damascus Steel", "ультрафиолет": "Ultraviolet",
}

PHASES = [(r"фаз\w*\s*(\d)", lambda m: f"Phase {m.group(1)}"), (r"рубин", "Ruby"), (r"сапфир", "Sapphire"),
          (r"черн\w*\s*жемчуж\w*", "Black Pearl"), (r"изумруд", "Emerald")]
PHASE_EN = re.compile(r" (Phase \d|Ruby|Sapphire|Black Pearl|Emerald)$")


def _weapon_skins(weapons):
    """Какие скины (по-английски, без фаз) есть в прайсе у этого оружия."""
    if "by_weapon" not in _cache:
        by_weapon = {}
        for name in price_list():
            m = NAME_RE.match(name)
            if m:
                by_weapon.setdefault(m.group(4), set()).add(PHASE_EN.sub("", m.group(5)))
        _cache["by_weapon"] = by_weapon
    result = set()
    for w in weapons:
        result |= _cache["by_weapon"].get(w, set())
    return result


def _score(query, name):
    """Похожесть названий: обычная + «по началам слов» (парча ≈ парчовые цветы)."""
    score = difflib.SequenceMatcher(None, query, name).ratio()
    words = name.split()

    def word_ok(q):
        return any(w.startswith(q[:max(4, len(q) - 2)]) or
                   difflib.SequenceMatcher(None, q, w).ratio() >= 0.8 for w in words)

    if all(word_ok(q) for q in query.split()):
        score = max(score, 0.85)
    return score


def skin_en(name_ru, weapons=()):
    """«Золотая арабеска» -> «Gold Arabesque», «доплер фаза 2» -> «Doppler Phase 2».
    Если известно оружие — ищем только среди его скинов, так почти не ошибиться. None, если не нашли."""
    skins, _ = _dictionary()
    key = _norm(name_ru)
    phase = ""
    for pattern, en in PHASES:
        m = re.search(pattern, key)
        if m:
            phase = " " + (en(m) if callable(en) else en)
            key = (key[:m.start()] + key[m.end():]).strip() or "доплер"
            break
    if phase == " Emerald" and key in ("доплер", "допплер"):
        key = "гамма доплер"  # изумруд бывает только у гамма-доплера
    if key in SKIN_ALIASES:
        return SKIN_ALIASES[key] + phase
    if key in skins:
        return skins[key] + phase
    allowed = _weapon_skins(weapons)
    pool = [k for k, v in skins.items() if v in allowed] if allowed else list(skins)
    if not pool:
        return None
    best = max(pool, key=lambda k: _score(key, k))
    return skins[best] + phase if _score(key, best) >= (0.6 if allowed else 0.75) else None


def weapon_en(name_ru):
    """«мото перчатки» -> «Moto Gloves», «дигл» -> «Desert Eagle». None, если не понятно."""
    _, weapons = _dictionary()
    key = _norm(name_ru)
    if not key:
        return None
    if key in weapons:
        return weapons[key]
    for alias, en in WEAPON_ALIASES.items():
        if any(word.startswith(alias) for word in key.split()):
            return en
    close = difflib.get_close_matches(key, weapons.keys(), n=1, cutoff=0.6)
    return weapons[close[0]] if close else None


NAME_RE = re.compile(r"^(★ )?(StatTrak™ )?(Souvenir )?(.+?) \| (.+?)(?: \((.+)\))?$")


def candidates(skin, weapons=(), wear="", stattrak=False, souvenir=False):
    """Предметы из прайса с этим скином. Доплеры с фазами тоже подходят («Doppler» -> «Doppler Phase 2»)."""
    result = []
    for name in price_list():
        m = NAME_RE.match(name)
        if not m:
            continue
        _, st, sv, weapon, sk, wr = m.groups()
        if bool(st) != stattrak or bool(sv) != souvenir:
            continue
        if not (sk == skin or sk.startswith(skin + " ")):
            continue
        if wear and wr != wear:
            continue
        if weapons and weapon not in weapons:
            continue
        result.append(name)
    return result


def similar(name, n=12):
    """Похожие названия — если ИИ ошибся в написании скина."""
    names = price_list().keys()
    # сначала сужаем по оружию (часть до « | »), чтобы поиск был точнее
    weapon = name.split("|")[0].strip().lower()
    pool = [x for x in names if x.lower().startswith(weapon)] or list(names)
    return difflib.get_close_matches(name, pool, n=n, cutoff=0.4)
