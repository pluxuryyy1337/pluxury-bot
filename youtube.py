"""Ролики с YouTube-канала и их субтитры (через yt-dlp)."""

import json
import re
import urllib.request
import xml.etree.ElementTree as ET

import yt_dlp

CHANNEL = "https://www.youtube.com/@Pluxury."


def _flat(url, limit):
    opts = {"extract_flat": True, "quiet": True, "no_warnings": True, "playlistend": limit}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _dates(channel_id):
    """Даты последних ~15 роликов из RSS-ленты канала: {id: "2026-10-12"}."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    with urllib.request.urlopen(url, timeout=30) as resp:
        root = ET.fromstring(resp.read())
    return {e.find("yt:videoId", ns).text: e.find("a:published", ns).text[:10]
            for e in root.findall("a:entry", ns)}


def recent_videos(shorts=60, longs=10):
    """Список роликов: [{id, title, views, date, url}], сверху новые."""
    videos = []
    channel_id = None
    for tab, limit in (("shorts", shorts), ("videos", longs)):
        try:
            info = _flat(f"{CHANNEL}/{tab}", limit)
        except Exception as e:
            print(f"Не удалось получить {tab}: {e}")
            continue
        channel_id = channel_id or info.get("channel_id")
        for e in info.get("entries") or []:
            videos.append({
                "id": e["id"],
                "title": e.get("title") or "",
                "views": e.get("view_count"),
                "url": f"https://youtube.com/shorts/{e['id']}" if tab == "shorts"
                       else f"https://youtu.be/{e['id']}",
            })
    if channel_id:
        try:
            dates = _dates(channel_id)
            for v in videos:
                v["date"] = dates.get(v["id"])
        except Exception as e:
            print("Не удалось получить даты роликов:", e)
    return videos


def video_info(video_id):
    """Название и субтитры одного ролика. transcript = None, если субтитры не достать."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    result = {"id": video_id, "title": info.get("title") or "", "transcript": None}
    tracks = None
    for source in (info.get("subtitles") or {}, info.get("automatic_captions") or {}):
        tracks = source.get("ru") or source.get("ru-orig")
        if tracks:
            break
    json3 = next((t for t in tracks or [] if t.get("ext") == "json3"), None)
    if json3:
        try:
            with urllib.request.urlopen(json3["url"], timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            words = []
            for ev in data.get("events", []):
                for seg in ev.get("segs") or []:
                    words.append(seg.get("utf8", ""))
            text = " ".join("".join(words).split())
            if len(text) > 100:
                result["transcript"] = text
        except Exception as e:
            print("Не удалось скачать субтитры:", e)
    return result


def video_id_from_text(text):
    """Достаёт id ролика из ссылки youtube.com/shorts/..., youtu.be/..., watch?v=..."""
    m = re.search(r"(?:youtube\.com/(?:shorts/|watch\?v=|live/)|youtu\.be/)([\w-]{11})", text)
    return m.group(1) if m else None
