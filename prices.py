"""Цены с lis-skins и курс доллара.

lis-skins отдаёт открытый прайс-лист в долларах, без проверки «я не робот».
Рубли считаем по курсу ЦБ (можно подправить множителем RATE_MULTIPLIER).
"""

import difflib
import json
import os
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


def similar(name, n=12):
    """Похожие названия — если ИИ ошибся в написании скина."""
    names = price_list().keys()
    # сначала сужаем по оружию (часть до « | »), чтобы поиск был точнее
    weapon = name.split("|")[0].strip().lower()
    pool = [x for x in names if x.lower().startswith(weapon)] or list(names)
    return difflib.get_close_matches(name, pool, n=n, cutoff=0.4)
