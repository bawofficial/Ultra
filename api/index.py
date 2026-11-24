import json
import random
import re
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify
from yt_dlp import YoutubeDL

app = Flask(__name__)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

ORDINALS = ["Первое", "Второе", "Третье", "Четвёртое", "Пятое",
            "Шестое", "Седьмое", "Восьмое", "Девятое", "Десятое"]

NUM_WORDS = {}
_pairs = [
    ("первое", 1), ("первый", 1), ("первую", 1), ("один", 1), ("одну", 1), ("1", 1),
    ("второе", 2), ("второй", 2), ("вторую", 2), ("два", 2), ("2", 2),
    ("третье", 3), ("третий", 3), ("третью", 3), ("три", 3), ("3", 3),
    ("четвёртое", 4), ("четвертое", 4), ("четвёртый", 4), ("четвертый", 4), ("четыре", 4), ("4", 4),
    ("пятое", 5), ("пятый", 5), ("пятую", 5), ("пять", 5), ("5", 5),
    ("шестое", 6), ("шестой", 6), ("6", 6),
    ("седьмое", 7), ("седьмой", 7), ("7", 7),
    ("восьмое", 8), ("восьмой", 8), ("8", 8),
    ("девятое", 9), ("девятый", 9), ("9", 9),
    ("десятое", 10), ("десятый", 10), ("10", 10),
]
for w, n in _pairs:
    NUM_WORDS[w] = n

LIST_MODES = ("search", "channel", "playlist", "recommend")

DROP_WORDS = {
    "включи", "включить", "запусти", "запустить", "поставь", "поставить",
    "слушать", "слушаем", "давай", "врубай", "пожалуйста",
    "ютуб", "youtube", "вк", "vk", "вконтакте", "рутуб", "rutube",
    "видео", "с", "из", "на", "а", "ну", "типа",
}


def short(s, n=70):
    if not s:
        return ""
    s = str(s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def first_number(tokens):
    for t in tokens:
        if t in NUM_WORDS:
            return NUM_WORDS[t]
    return None


def phrase_after(tokens, heads):
    for h in heads:
        if h in tokens:
            rest = tokens[tokens.index(h) + 1:]
            if rest:
                return " ".join(rest)
    return None


def detect_platform(tokens):
    if any(t in tokens for t in ("вк", "vk", "вконтакте")):
        return "vk"
    if any(t in tokens for t in ("рутуб", "rutube")):
        return "rutube"
    return "youtube"


def respond(text, tts=None, state=None, user_update=None, buttons=None, directives=None, end=False):
    body = {
        "response": {
            "text": text,
            "tts": tts if tts is not None else text,
            "end_session": end,
            "buttons": buttons or [],
        },
        "session_state": state if state is not None else {},
        "version": "1.0",
    }
    if user_update:
        body["user_state_update"] = user_update
    if directives:
        body["directives"] = directives
    return body


def play_directive(url, title):
    return [{
        "name": "audio_player_play",
        "type": "AudioPlayer.Play",
        "payload": {"url": url, "title": short(title, 100), "offset_ms": 0},
    }]


# ---------------- поиск и потоки ----------------

def yt_search(query, n=5):
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}
    try:
        with YoutubeDL(opts) as ydl:
            data = ydl.extract_info("ytsearch%d:%s" % (n, query), download=False)
    except Exception:
        return []
    out = []
    for e in (data.get("entries") or []):
        if not e:
            continue
        out.append({
            "platform": "youtube",
            "url": e.get("url") or ("https://www.youtube.com/watch?v=%s" % e.get("id") if e.get("id") else None),
            "title": e.get("title") or "без названия",
            "channel": e.get("uploader") or e.get("channel"),
            "channel_id": e.get("channel_id") or e.get("uploader_id"),
        })
    return out


def rutube_search(query, n=5):
    try:
        r = requests.get("https://rutube.ru/api/search/video/", params={"query": query, "page": 1},
                         headers=UA, timeout=8)
        r.raise_for_status()
        results = (r.json() or {}).get("results") or []
    except Exception:
        return []
    out = []
    for e in results[:n]:
        url = e.get("video_url") or e.get("url")
        if url:
            out.append({"platform": "rutube", "url": url, "title": e.get("title") or "без названия"})
    return out


def get_audio(url):
    opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best", "noplaylist": True}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info.get("url"), info.get("title") or "видео"


def channel_videos(channel_id, n=5):
    if not channel_id:
        return []
    url = channel_id if channel_id.startswith("http") else "https://www.youtube.com/channel/%s/videos" % channel_id
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "playlist_items": "1:%d" % n}
    try:
        with YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=False)
    except Exception:
        return []
    out = []
    for e in (data.get("entries") or []):
        if not e:
            continue
        out.append({
            "platform": "youtube",
            "url": e.get("url") or ("https://www.youtube.com/watch?v=%s" % e.get("id") if e.get("id") else None),
            "title": e.get("title") or "без названия",
        })
    return out


def playlist_random_by_query(query):
    search_url = "https://www.youtube.com/results?search_query=%s&sp=EgIQAw%%3D%%3D" % quote(query)
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}
    try:
        with YoutubeDL(opts) as ydl:
            data = ydl.extract_info(search_url, download=False)
        entries = [e for e in (data.get("entries") or []) if e]
        if not entries:
            return None
        pl = random.choice(entries[:5])
        with YoutubeDL(opts) as ydl:
            pdata = ydl.extract_info(pl.get("url"), download=False)
        vids = [e for e in (pdata.get("entries") or []) if e]
        if not vids:
            return None
        v = random.choice(vids)
        return {
            "platform": "youtube",
            "url": v.get("url") or ("https://www.youtube.com/watch?v=%s" % v.get("id") if v.get("id") else None),
            "title": v.get("title") or "видео из плейлиста",
        }
    except Exception:
        return None


# ---------------- озвучка списков ----------------

def read_list(items):
    text_parts, tts_parts = [], []
    for i, it in enumerate(items[:5]):
        title = short(it.get("title") or "без названия", 70)
        num = ORDINALS[i]
        text_parts.append("%s «%s»." % (num, title))
        tts_parts.append("%s «%s»\\s" % (num, title))
    tts = " ".join(tts_parts) + " Что включаем?"
    return " ".join(text_parts) + " Что включаем?", tts


def add_history(user_state, title, platform):
    hist = list(user_state.get("history") or [])
    hist.insert(0, {"title": title, "platform": platform})
    return {"history": hist[:10]}


# ---------------- обработчики ----------------

def start_menu():
    text = ("Привет! Это Ультиматум. Могу включить звук любого видео. "
            "Скажи «включи» и что искать, «порекомендуй», «канал», "
            "«случайное из плейлиста» или «что умеешь».")
    tts = "Привет! Это Ультиматум. Скажи «включи» и что искать, «порекомендуй» или «что умеешь»."
    return respond(text, tts=tts)


def help_text(state):
    text = ("Вот что я умею: «включи» и название — найду и зачитаю пять вариантов, "
            "ты говоришь «второе» или «третье» — и играю звук. "
            "«ещё пять» — следующие варианты. «канал» — покажу свежие видео канала. "
            "«случайное из плейлиста» и название — случайное видео из найденного плейлиста. "
            "«порекомендуй» — предложу по твоей истории. "
            "Ссылку с любой площадки тоже можно прислать. "
            "Работают ютуб, рутуб и ссылки из ВК.")
    return respond(text, state=state)


def do_play(utt, tokens, state, user_state):
    m = re.search(r"(https?://\S+)", utt or "")
    if m:
        return play_single(m.group(1).rstrip(".,)"), state, user_state)
    platform = detect_platform(tokens)
    query = " ".join(t for t in tokens if t not in DROP_WORDS)
    if not query:
        return respond("Скажи, что включить. Например: «включи лолбит».", state=state)
    if platform == "vk":
        return respond("Поиск по названию у ВКонтакте пока не прикручен: кинь ссылку на видео ВК, "
                       "а поиск по словам работает в ютубе и рутубе.", state=state)
    if platform == "rutube":
        items = rutube_search(query, 5)
    else:
        items = yt_search(query, 5)
    if not items:
        return respond("Ничего не нашёл. Попробуй сказать по-другому.", state=state)
    text, tts = read_list(items)
    new_state = {"mode": "search", "items": items[:5], "query": query, "platform": platform, "page": 1}
    return respond(text, tts=tts, state=new_state,
                   buttons=[{"title": "ещё пять", "hide": True}, {"title": "повтори", "hide": True}])


def select_item(state, user_state, index):
    items = state.get("items") or []
    if not items or index < 1 or index > len(items):
        return respond("Такого номера нет. Скажи число от первого до %s." % ORDINALS[len(items) - 1].lower(), state=state)
    item = items[index - 1]
    try:
        audio_url, _ = get_audio(item["url"])
    except Exception:
        return respond("Не смог достать звук. Попробуй другой номер.", state=state)
    new_state = {"mode": "audio", "queue": items, "pos": index - 1}
    return respond("Играю: %s." % short(item.get("title"), 70),
                   state=new_state,
                   user_update=add_history(user_state, item.get("title"), item.get("platform")),
                   directives=[play_directive(audio_url, item.get("title"))])


def play_single(url, state, user_state):
    try:
        audio_url, title = get_audio(url)
    except Exception:
        return respond("Не смог достать звук с этой ссылки.", state=state)
    new_state = {"mode": "audio", "queue": [{"platform": "link", "url": url, "title": title}], "pos": 0}
    return respond("Играю: %s." % short(title, 70),
                   state=new_state,
                   user_update=add_history(user_state, title, "link"),
                   directives=[play_directive(audio_url, title)])


def more_results(state, user_state):
    page = (state.get("page") or 1) + 1
    query = state.get("query") or ""
    platform = state.get("platform") or "youtube"
    if platform == "rutube":
        items = rutube_search(query, page * 5)[(page - 1) * 5: page * 5]
    else:
        items = yt_search(query, page * 5)[(page - 1) * 5: page * 5]
    if not items:
        return respond("Дальше пусто. Скажи новое название.", state=state)
    text, tts = read_list(items)
    new_state = {**state, "items": items[:5], "page": page}
    return respond(text, tts=tts, state=new_state)


def repeat_list(state):
    items = state.get("items") or []
    if not items:
        return respond("Списка пока нет — скажи «включи» и что искать.", state=state)
    text, tts = read_list(items)
    return respond(text, tts=tts, state=state)


def open_channel(state, num):
    items = state.get("items") or []
    if not items or not (1 <= num <= len(items)):
        return respond("Сначала выбери видео номером, потом уйду на его канал.", state=state)
    channel_id = items[num - 1].get("channel_id")
    vids = channel_videos(channel_id, 5)
    if not vids:
        return respond("Не смог вытащить видео канала.", state=state)
    text, tts = read_list(vids)
    new_state = {"mode": "channel", "items": vids, "query": None}
    return respond(text, tts=tts, state=new_state)


def open_channel_by_name(name, state):
    items = yt_search(name, 3)
    if not items:
        return respond("Канал не нашёл.", state=state)
    vids = channel_videos(items[0].get("channel_id"), 5)
    if not vids:
        return respond("Не смог вытащить видео канала.", state=state)
    text, tts = read_list(vids)
    return respond(text, tts=tts, state={"mode": "channel", "items": vids, "query": None})


def random_from_current(state, user_state):
    items = state.get("items") or []
    if not items:
        return respond("Нет списка — не из чего выбирать. Скажи «включи» и что искать.", state=state)
    return select_item(state, user_state, random.randint(1, len(items)))


def random_from_playlist_name(query, state):
    item = playlist_random_by_query(query) if query else None
    if not item:
        return respond("Плейлист не нашёл или он пустой.", state=state)
    try:
        audio_url, _ = get_audio(item["url"])
    except Exception:
        return respond("Не смог достать звук из плейлиста.", state=state)
    return respond("Играю: %s." % short(item.get("title"), 70),
                   state={"mode": "audio", "queue": [item], "pos": 0},
                   directives=[play_directive(audio_url, item.get("title"))])


def do_recommend(user_state):
    hist = user_state.get("history") or []
    base = None
    if hist:
        base = random.choice(hist)
        query = base.get("title") or ""
        items = yt_search(query + " похожее", 6)
        items = [i for i in items if (i.get("title") or "").lower() != query.lower()][:5]
        if not items:
            items = yt_search(query, 5)
    else:
        seed = random.choice(["интересная музыка", "русский рэп", "лоу-фай", "рок хиты",
                              "электронная музыка", "смешные видео", "научпоп"])
        items = yt_search(seed, 5)
    if not items:
        return respond("Рекомендации не вытащились, попробуй ещё раз.", state={})
    if base and base.get("title"):
        intro = "Ты недавно слушал «%s», вот похожее." % short(base["title"], 50)
    else:
        intro = "Вот что могу предложить."
    list_text, list_tts = read_list(items)
    return respond("%s %s" % (intro, list_text), tts="%s %s" % (intro, list_tts),
                   state={"mode": "recommend", "items": items[:5], "query": None})


def audio_event(rtype, state):
    if rtype == "AudioPlayer.Next":
        queue = state.get("queue") or []
        pos = (state.get("pos") or 0) + 1
        if pos >= len(queue):
            pos = 0
        item = queue[pos]
        try:
            audio_url, _ = get_audio(item["url"])
        except Exception:
            return respond("", state=state)
        new_state = {**state, "pos": pos}
        return respond("", state=new_state, directives=[play_directive(audio_url, item.get("title"))])
    if rtype == "AudioPlayer.Failed":
        return respond("Похоже, поток не заиграл. Скажи «дальше» или назови номер.", state=state)
    return respond("", state=state)


def not_understood(state):
    return respond("Не понял. Скажи «включи» и что искать, «порекомендуй» или «что умеешь».", state=state)


# ---------------- вход ----------------

@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    return jsonify(handle(req))


def handle(req):
    rreq = req.get("request") or {}
    rtype = rreq.get("type", "")
    state = (req.get("state") or {}).get("session") or {}
    user_state = (req.get("state") or {}).get("user") or {}
    utt = rreq.get("original_utterance") or ""
    tokens = [t.lower() for t in ((rreq.get("nlu") or {}).get("tokens") or [])]
    new = (req.get("session") or {}).get("new")

    if rtype.startswith("AudioPlayer"):
        return audio_event(rtype, state)

    if new and not tokens:
        return start_menu()

    if any(t in tokens for t in ("помощь", "умеешь", "команды")):
        return help_text(state)
    if any(t in tokens for t in ("порекомендуй", "рекомендуй", "посоветуй", "послушать")):
        return do_recommend(user_state)
    if "повтори" in tokens:
        return repeat_list(state)
    if any(t in tokens for t in ("ещё", "еще", "дальше")) and state.get("mode") in LIST_MODES and state.get("query"):
        return more_results(state, user_state)

    num = first_number(tokens)
    if num and state.get("mode") in LIST_MODES:
        if any(t in tokens for t in ("случайное", "случайно", "рандом")):
            return random_from_current(state, user_state)
        if "канал" in tokens:
            return open_channel(state, num)
        return select_item(state, user_state, num)

    if any(t in tokens for t in ("случайное", "рандом")):
        return random_from_current(state, user_state)

    if any(t in tokens for t in ("плейлист", "плейлиста", "плейлисте")):
        rest = phrase_after(tokens, ["плейлиста", "плейлист", "плейлисте"])
        return random_from_playlist_name(rest, state)

    if "канал" in tokens:
        rest = phrase_after(tokens, ["канала", "канал", "каналу"])
        if rest:
            return open_channel_by_name(rest, state)
        return open_channel(state, num or 1)

    if any(t in tokens for t in ("включи", "запусти", "поставь", "слушать", "врубай")) or "http" in utt.lower():
        return do_play(utt, tokens, state, user_state)

    return not_understood(state)
