"""Запросы к Claude: разбор списка скинов, посты по сценариям, недельный план."""

import json

import anthropic

import style

MODEL = "claude-opus-5-5"

_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # ключ берётся из ANTHROPIC_API_KEY
    return _client


def ask(system, user, effort="medium", schema=None, max_tokens=16000):
    """Один запрос к Claude. С schema — ответ строго в JSON по схеме."""
    output_config = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    resp = client().beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config=output_config,
        # если Claude откажется отвечать, запрос сам повторится на другой модели
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude отказался отвечать на этот запрос")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return json.loads(text) if schema else text


def _obj(props):
    return {"type": "object", "properties": props,
            "required": list(props), "additionalProperties": False}


ITEM = _obj({"label": {"type": "string"}, "market_name": {"type": "string"}})
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
