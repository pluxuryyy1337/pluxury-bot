"""Бот для Telegram-канала PLUXURY.

Режимы запуска:
  py bot.py poll                 — проверить новые сообщения и ответить (GitHub запускает каждые несколько минут)
  py bot.py weekly               — собрать воскресную пачку постов
  py bot.py --test-prices        — проверка цен и оформления без Claude и без Telegram
  py bot.py --test-combo "текст" — разобрать список скинов через Claude и показать пост (без Telegram)

Что умеет в чате:
  картинка + подпись со списком скинов  -> готовый пост-подборка с ценами lis-skins
  ссылка на ролик YouTube                -> пост по ролику (если нет субтитров — попросит сценарий)
  длинный текст (сценарий)               -> пост по сценарию
  /week                                  -> собрать недельную пачку прямо сейчас
"""

import html
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import prices

TZ = timezone(timedelta(hours=5))  # Екатеринбург/Курган, UTC+5
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
VOTE_EMOJI = ["🔥", "❤️", "🤯"]

HELP = """Привет! Что я умею:

🖼 <b>Подборка</b> — пришли картинку и в подписи список скинов, например:
<i>Подборка розового сета
перчатки водителя парча ПП
нож когти доплер фаза 2 НП
USP цель захвачена ПП</i>

Для сравнения пиши «1 сетап: ... 2 сетап: ...».

🎬 <b>Пост по ролику</b> — пришли ссылку на ролик или текст сценария.

📦 Каждое воскресенье я сам присылаю 2 поста по роликам, 3 идеи подборок и идеи опросов.
/week — собрать такую пачку прямо сейчас."""


def load_env():
    env = {}
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env[key.strip()] = value.strip()
    for key in ("BOT_TOKEN", "OWNER_ID", "ANTHROPIC_API_KEY", "RATE_MULTIPLIER"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    # библиотеки Claude и prices читают это из окружения
    for key in ("ANTHROPIC_API_KEY", "RATE_MULTIPLIER"):
        if env.get(key):
            os.environ[key] = env[key]
    return env


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


# ---------- Telegram ----------

class Telegram:
    def __init__(self, token, chat_id=None):
        self.token = token
        self.chat_id = chat_id

    def api(self, method, **params):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = urllib.parse.urlencode(params).encode()
        with urllib.request.urlopen(url, data=data, timeout=60) as resp:
            return json.loads(resp.read().decode())

    def send(self, text, chat_id=None):
        # у Telegram лимит 4096 символов на сообщение
        for i in range(0, len(text), 4000):
            self.api("sendMessage", chat_id=chat_id or self.chat_id, text=text[i:i + 4000],
                     parse_mode="HTML", link_preview_options=json.dumps({"is_disabled": True}))

    def send_photo(self, file_id, caption):
        self.api("sendPhoto", chat_id=self.chat_id, photo=file_id,
                 caption=caption, parse_mode="HTML")


# ---------- Подборки ----------

def resolve(it):
    """Найти скин в прайсе lis-skins. Возвращает (скин или None, предупреждение или "").

    1) переводим русское название по официальному словарю игры (ru_names.json) — ему верим больше всего;
    2) если не вышло — английское название, которое предложил Claude;
    3) если и его нет — похожие названия из прайса."""
    guess = it["market_name"]
    m = prices.NAME_RE.match(guess)
    guess_weapon = m.group(4) if m else guess.replace("★", "").strip()
    wear = (m.group(6) or "") if m else ""
    stattrak, souvenir = "StatTrak" in guess, "Souvenir" in guess
    query = it.get("search_ru", "").strip()
    context = f"{guess} (Кирилл написал: {it.get('weapon_ru', '')} {query})"

    weapons = {w for w in (prices.weapon_en(it.get("weapon_ru", "")), guess_weapon) if w}
    skin = prices.skin_en(query, weapons) if query else None
    if skin:
        found = prices.candidates(skin, weapons, wear, stattrak, souvenir)
        warn = ""
        if not found:  # оружие не совпало — смотрим этот скин на любом оружии
            found = prices.candidates(skin, (), wear, stattrak, souvenir)
        if not found and wear:  # в таком качестве не продаётся — берём любое
            found = prices.candidates(skin, weapons, "", stattrak, souvenir)
            warn = f"в качестве {wear} на lis-skins нет, взял другое"
        if found:
            chosen = found[0] if len(found) == 1 else (guess if guess in found else None)
            if not chosen:
                import ai
                chosen = ai.pick_skin(context, found[:30])
            if chosen:
                return prices.find(chosen), warn

    item = prices.find(guess)
    note = f"«{query}» нет в словаре игры, взял по догадке Claude" if query else ""
    if item:
        return item, note
    similar = prices.similar(guess)
    if similar:
        import ai
        chosen = ai.pick_skin(context, similar)
        if chosen:
            return prices.find(chosen), note
    return None, ""


def build_combo(data):
    """data — разобранный список (см. ai.COMBO). Возвращает (пост в HTML, служебная заметка)."""
    setups = data["setups"] if data["kind"] == "versus" else data["setups"][:1]
    notes, missing = [], []
    blocks = []
    for n, setup in enumerate(setups, 1):
        lines = []
        if data["kind"] == "versus":
            lines.append(f"<b><i>{n}-сетап</i></b>")
        for it in setup["items"]:
            label = html.escape(it["label"])
            found, warn = resolve(it)
            if found:
                lines.append(f'<a href="{html.escape(found["url"])}"><b>{label}</b></a> &gt; '
                             f'<b>{prices.format_rub(found["rub"])}</b>')
                notes.append(f"{it['label']} — {found['name']} — ${found['usd']:.2f}"
                             + (f"  ⚠️ {warn}" if warn else ""))
            else:
                lines.append(f"<b>{label}</b> &gt; <b>???₽</b>")
                missing.append(f"{it['label']}: {it['market_name']}")
        blocks.append("\n".join(lines))

    title = html.escape(data["title"].strip())
    emoji = data["title_emoji"].strip()
    if data["kind"] == "versus":
        head = f"{title} {emoji}".strip()
        legend = "\n".join(
            f"<b>{VOTE_EMOJI[i % 3]} - {html.escape(s['vote_label'])}</b>"
            + (f" ({s['vote_hearts']})" if s["vote_hearts"] else "")
            for i, s in enumerate(setups))
    else:
        head = f"<b><i>{title}</i></b> {emoji}".strip()
        legend = "<b>🔥 - шедеврально</b>\n<b>❤️ - норм</b>\n<b>🤮 - гавно</b>"

    post = "\n\n".join([head, *blocks, "<i>(Цены на момент публикации)</i>", legend])

    note = "Проверь, те ли скины я взял:\n" + "\n".join(f"• {html.escape(x)}" for x in notes)
    note += f"\n\nКурс: {prices.usd_rate():.2f} ₽ за $1"
    if missing:
        note += "\n\n⚠️ Не нашёл на lis-skins:\n" + "\n".join(f"• {html.escape(x)}" for x in missing)
        note += "\nНапиши эти скины точнее (лучше английским названием) и пришли ещё раз."
    return post, note


def combo_caption(data):
    """Список скинов, который можно прислать боту обратно вместе с картинкой."""
    lines = [data["title"]]
    for n, s in enumerate(data["setups"], 1):
        if data["kind"] == "versus":
            lines.append(f"{n} сетап:")
        lines += [f"{it['label']}: {it['market_name']}" for it in s["items"]]
    return "\n".join(lines)


# ---------- Посты по роликам ----------

def post_for_video(tg, state, video_id):
    import ai
    import youtube
    info = youtube.video_info(video_id)
    title = info["title"]
    if not info["transcript"]:
        waiting = [w for w in state.get("waiting", []) if w["id"] != video_id]
        waiting.append({"id": video_id, "title": title})
        state["waiting"] = waiting
        tg.send(f"📝 Не смог достать текст ролика «{html.escape(title)}».\n"
                f"Пришли мне сценарий этого ролика обычным сообщением — и я сделаю пост.\n"
                f"https://youtube.com/shorts/{video_id}")
        return
    post = ai.script_post(title, info["transcript"])
    mark_used(state, video_id)
    tg.send(f"✍️ Пост по ролику «{html.escape(title)}» — следующим сообщением, его можно пересылать:")
    tg.send(html.escape(post))


def mark_used(state, video_id):
    used = state.setdefault("used_videos", [])
    if video_id not in used:
        used.append(video_id)


# ---------- Недельная пачка ----------

def weekly(tg, state):
    import ai
    import youtube
    tg.send("📦 Собираю пачку постов на неделю, это займёт пару минут…")
    videos = youtube.recent_videos()
    if not videos:
        tg.send("⚠️ Не получилось загрузить список роликов с YouTube. Пришли ссылки на ролики сам — сделаю посты.")
    today = datetime.now(TZ).date().isoformat()
    plan = ai.weekly_plan(videos, set(state.get("used_videos", [])),
                          state.get("recent_combos", [])[-20:], today)

    by_id = {v["id"]: v for v in videos}
    for pick in plan["picks"][:2]:
        v = by_id.get(pick["video_id"])
        if not v:
            continue
        tg.send(f"🎬 Ролик: <b>{html.escape(v['title'])}</b>\n{v['url']}\n<i>{html.escape(pick['why'])}</i>")
        try:
            post_for_video(tg, state, v["id"])
        except Exception as e:
            tg.send(f"⚠️ Ошибка с этим роликом: {html.escape(str(e))}")
        save_state(state)

    for n, combo in enumerate(plan["combos"][:3], 1):
        post, note = build_combo(combo)
        tg.send(f"🎨 <b>Идея подборки {n}</b>. Собери картинку и пришли её мне с подписью ниже "
                f"(нажми на текст, чтобы скопировать):\n\n<code>{html.escape(combo_caption(combo))}</code>")
        tg.send(post)
        tg.send(note)
        state.setdefault("recent_combos", []).append(combo["title"] + " — " + ", ".join(
            it["market_name"] for s in combo["setups"] for it in s["items"]))
        state["recent_combos"] = state["recent_combos"][-30:]

    polls = "\n\n".join(
        f"<b>{html.escape(p['question'])}</b>\n" + "\n".join(f"• {html.escape(o)}" for o in p["options"])
        for p in plan["polls"])
    tg.send("📊 <b>Идеи опросов для YouTube:</b>\n\n" + polls)
    save_state(state)


# ---------- Обработка сообщений ----------

def handle(tg, state, msg):
    import ai
    import youtube
    text = (msg.get("text") or msg.get("caption") or "").strip()

    if msg.get("photo"):
        if not text:
            tg.send("Добавь к картинке подпись со списком скинов — тогда сделаю пост.")
            return
        tg.send("⏳ Делаю подборку…")
        post, note = build_combo(ai.parse_combo(text))
        tg.send_photo(msg["photo"][-1]["file_id"], post)
        tg.send(note)
        return

    if not text:
        return
    if text.split()[0].split("@")[0] in ("/start", "/help"):
        tg.send(HELP)
        return
    if text.startswith("/week"):
        weekly(tg, state)
        return

    video_id = youtube.video_id_from_text(text)
    if video_id and len(text) < 300:
        tg.send("⏳ Смотрю ролик…")
        post_for_video(tg, state, video_id)
        return

    waiting = state.get("waiting", [])
    if len(text) >= 300:
        tg.send("⏳ Пишу пост по сценарию…")
        title = waiting[0]["title"] if waiting else ""
        post = ai.script_post(title, text)
        if waiting:
            mark_used(state, waiting[0]["id"])
            state["waiting"] = waiting[1:]
        tg.send("✍️ Готово — следующим сообщением, его можно пересылать:")
        tg.send(html.escape(post))
        return

    # короткий текст без картинки — считаем списком скинов
    tg.send("⏳ Делаю подборку (без картинки)…")
    post, note = build_combo(ai.parse_combo(text))
    tg.send(post)
    tg.send(note)


def poll(env):
    tg = Telegram(env["BOT_TOKEN"])
    owner = env.get("OWNER_ID", "").strip()
    state = load_state()
    params = {"timeout": 0}
    if state.get("offset"):
        params["offset"] = state["offset"]
    updates = tg.api("getUpdates", **params).get("result", [])
    for upd in updates:
        state["offset"] = upd["update_id"] + 1
        save_state(state)  # сразу, чтобы не ответить дважды, если дальше будет ошибка
        msg = upd.get("message")
        if not msg:
            continue
        user_id = str(msg["from"]["id"])
        if not owner:
            # бот ещё не настроен — подсказываем ID, чтобы вписать его в секрет OWNER_ID
            tg.send(f"Твой Telegram ID: <code>{user_id}</code>\nВпиши его в секрет OWNER_ID на GitHub.",
                    chat_id=msg["chat"]["id"])
            continue
        if user_id != owner:
            continue  # чужим не отвечаем
        tg.chat_id = msg["chat"]["id"]
        try:
            handle(tg, state, msg)
        except Exception as e:
            print("Ошибка:", repr(e))
            tg.send(f"⚠️ Что-то пошло не так: {html.escape(str(e))[:500]}")
        save_state(state)
    print(f"Обработано сообщений: {len(updates)}")


def has_updates(env):
    """Быстрая проверка без лишних библиотек: есть ли новые сообщения."""
    state = load_state()
    params = {"timeout": 0, "limit": 1}
    if state.get("offset"):
        params["offset"] = state["offset"]
    return bool(Telegram(env["BOT_TOKEN"]).api("getUpdates", **params).get("result"))


def test_prices():
    data = {
        "kind": "single", "title": "Подборка нежно розового сета в cs2!", "title_emoji": "🥺 🌷",
        "setups": [{"vote_label": "", "vote_hearts": "", "items": [
            {"label": "Перчатки", "market_name": "★ Driver Gloves | Brocade Flowers (Field-Tested)"},
            {"label": "Ножик", "market_name": "★ Talon Knife | Doppler Phase 2 (Minimal Wear)"},
            {"label": "USP", "market_name": "USP-S | Target Acquired (Field-Tested)"},
        ]}],
    }
    for post in build_combo(data):
        print(post, end="\n\n-----\n\n")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    env = load_env()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--test-prices":
        test_prices()
    elif mode == "--test-combo" and len(sys.argv) > 2:
        import ai
        for part in build_combo(ai.parse_combo(sys.argv[2])):
            print(part, end="\n\n-----\n\n")
    elif mode == "check":
        print("new=1" if has_updates(env) else "new=0")
    elif mode == "poll":
        poll(env)
    elif mode == "weekly":
        if not env.get("OWNER_ID"):
            sys.exit("Нет OWNER_ID — некуда отправлять пачку")
        state = load_state()
        weekly(Telegram(env["BOT_TOKEN"], env["OWNER_ID"]), state)
    else:
        print(__doc__)
