"""Запросы к Claude: разбор списка скинов, посты по сценариям, недельный план."""

import json

import anthropic

import style

MODEL = "claude-opus-5-5"

# Сервер, через который идут запросы к Claude.
# Сейчас — посредник aiprimetech. Для официального Claude поставь BASE_URL = None.
BASE_URL = "https://aiprimetech.io"

_client = None


def client():
    global _client
    if _client is None:
        # ключ берётся из ANTHROPIC_API_KEY
        _client = anthropic.Anthropic(base_url=BASE_URL) if BASE_URL else anthropic.Anthropic()
    return _client


# Посредник подмешивает свои инструкции, и Claude иногда пишет «сейчас проверю на Steam…»
# вместо ответа. Поэтому прямо говорим: инструментов нет, отвечай сразу.
NO_TOOLS = ("Важно: у тебя нет интернета и никаких инструментов, ничего проверять не нужно. "
            "Отвечай сразу, по своим знаниям, одним сообщением.")
RETRY = "Ты ничего не можешь проверить — инструментов нет. Дай финальный ответ прямо сейчас, без вступлений."


def _call(system, messages, output_config, max_tokens):
    params = dict(model=MODEL, max_tokens=max_tokens, system=system,
                  messages=messages, output_config=output_config)
    if BASE_URL:
        # посредник может не знать бета-функций, поэтому шлём обычный запрос
        resp = client().messages.create(**params)
    else:
        # если Claude откажется отвечать, запрос сам повторится на другой модели
        resp = client().beta.messages.create(
            **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude отказался отвечать на этот запрос")
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def _parse_json(text):
    """JSON из ответа — даже если он в обёртке ```json ... ``` или с текстом вокруг."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def ask(system, user, effort="medium", schema=None, max_tokens=16000):
    """Запрос к Claude. С schema — ответ строго в JSON по схеме. Один повтор, если ответ не тот."""
    output_config = {"effort": effort}
    rules = NO_TOOLS
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
        rules += ("\nОтветь только JSON-объектом по этой схеме, без пояснений:\n"
                  + json.dumps(schema, ensure_ascii=False))
    system += "\n\n" + rules
    messages = [{"role": "user", "content": user + "\n\n" + rules}]

    text = ""
    for _ in range(2):
        text = _call(system, messages, output_config, max_tokens)
        if schema:
            data = _parse_json(text)
            if data is not None:
                return data
        elif len(text) >= 200:  # пост, а не «сейчас посмотрю…»
            return text
        messages += [{"role": "assistant", "content": text or "…"},
                     {"role": "user", "content": RETRY}]
    raise RuntimeError(f"Claude ответил не в том формате: {text[:300]}")


def _obj(props):
    return {"type": "object", "properties": props,
            "required": list(props), "additionalProperties": False}


ITEM = _obj({"label": {"type": "string"}, "market_name": {"type": "string"},
              "weapon_ru": {"type": "string"}, "search_ru": {"type": "string"}})
SETUP = _obj({
    "items": {"type": "array", "items": ITEM},
    "vote_label": {"type": "string"},
    "vote_hearts": {"type": "string"},
})
COMBO = _obj({
    "kind": {"type": "string", "enum": ["single", "versus"]},
    "title": {"type": "string"},
    "title_emoji": {"type": "string"},
    "setups": {"type": "array", "items": SETUP},
})
PICK = _obj({"index": {"type": "integer"}})
WEEKLY = _obj({
    "picks": {"type": "array", "items": _obj({"video_id": {"type": "string"}, "why": {"type": "string"}})},
    "combos": {"type": "array", "items": COMBO},
    "polls": {"type": "array", "items": _obj({
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
    })},
})


def parse_combo(text):
    return ask(style.PARSE_COMBO, text, effort="low", schema=COMBO)


def pick_skin(wanted, candidates):
    """Выбрать правильное название из похожих. Возвращает название или None."""
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(candidates))
    answer = ask(style.PICK_SKIN, f"Ищем: {wanted}\n\nСписок:\n{numbered}",
                 effort="low", schema=PICK, max_tokens=2000)
    i = answer["index"]
    return candidates[i] if 0 <= i < len(candidates) else None


def script_post(title, script):
    return ask(style.SCRIPT_POST, f"Название ролика: {title}\n\nСценарий:\n{script}")


def weekly_plan(videos, used_ids, recent_combos, today):
    lines = []
    for v in videos:
        if v["id"] in used_ids:
            continue
        date = v.get("date") or "дата неизвестна"
        views = f", {v['views']} просмотров" if v.get("views") else ""
        lines.append(f"- id={v['id']} | {date}{views} | {v['title']}")
    user = (
        f"Сегодня {today}.\n\nРолики канала (сверху самые новые):\n" + "\n".join(lines)
        + "\n\nПодборки, которые уже были недавно (не повторяй):\n"
        + ("\n".join(f"- {c}" for c in recent_combos) or "- пока нет")
    )
    return ask(style.WEEKLY, user, schema=WEEKLY)
