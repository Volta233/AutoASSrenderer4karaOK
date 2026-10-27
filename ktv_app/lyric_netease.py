"""Optional public NetEase catalogue/lyric fallback, without cookies or login.

This public endpoint has no service guarantee. Errors remain source-specific and
must not prevent the next provider from being tried. Original LRC only; translated
and romanized companion tracks are deliberately not mixed into sung text.
"""

import httpx

from .lyric_names import artist_similarity, japanese_title, similarity


def lookup_netease(song, artist, duration):
    attempts, tracks = [], {}
    titles = list(dict.fromkeys([song, japanese_title(song)]))
    with httpx.Client(
        timeout=httpx.Timeout(8, connect=4),
        follow_redirects=False,
        headers={"User-Agent": "LyriFrame/1.1 (lyric source lookup)"},
    ) as client:

        def request(path, query, reason):
            attempt = {
                "provider": "网易云音乐",
                "query": query,
                "reason": reason,
                "http_status": None,
                "records": 0,
                "candidates": 0,
            }
            attempts.append(attempt)
            try:
                response = client.get("https://music.163.com/api/" + path, params=query)
                attempt["http_status"] = response.status_code
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or data.get("code") != 200:
                    raise ValueError("invalid source response")
                attempt["status"] = "success"
                return data, attempt
            except (httpx.HTTPError, ValueError):
                attempt.update(status="failed", error="来源暂不可用；继续其他来源")
                return {}, attempt

        for title in titles:
            data, attempt = request(
                "search/get", {"s": title, "type": 1, "limit": 6}, "song_catalogue"
            )
            result = data.get("result") or {}
            rows = result.get("songs") or [] if isinstance(result, dict) else None
            if not isinstance(rows, list):
                attempt.update(status="failed", error="歌曲目录格式异常")
                continue
            attempt["records"] = len(rows)
            for row in rows[:6]:
                if not isinstance(row, dict) or not isinstance(row.get("id"), int):
                    continue
                name = row.get("name")
                artists = row.get("artists") or []
                if not isinstance(name, str) or not isinstance(artists, list):
                    continue
                singer = " / ".join(
                    a["name"]
                    for a in artists
                    if isinstance(a, dict) and isinstance(a.get("name"), str)
                )
                if not singer or similarity(song, name) < 0.5:
                    continue
                try:
                    seconds = float(row.get("duration", 0)) / 1000
                except (ValueError, TypeError, OverflowError):
                    continue
                album = row.get("album") or {}
                tracks[row["id"]] = {
                    "id": "netease-" + str(row["id"]),
                    "trackName": name,
                    "artistName": singer,
                    "duration": seconds,
                    "albumName": album.get("name") if isinstance(album, dict) else None,
                    "provider": "网易云音乐",
                    "source": f"https://music.163.com/song?id={row['id']}",
                    "_song_id": row["id"],
                }
            if attempt["status"] == "failed":
                break  # no immediate retries against an unavailable provider

        ordered = sorted(
            tracks.values(),
            key=lambda r: (
                -similarity(song, r["trackName"]),
                -artist_similarity(artist, r["artistName"]) if artist else 0,
                abs(r["duration"] - duration),
            ),
        )
        records = []
        for track in ordered[:3]:
            identifier = track.pop("_song_id")
            data, attempt = request(
                "song/lyric",
                {"id": identifier, "lv": -1, "kv": -1, "tv": -1},
                "original_lyrics",
            )
            lrc = data.get("lrc") or {}
            text = lrc.get("lyric") if isinstance(lrc, dict) else None
            if isinstance(text, str) and text.strip() and not data.get("nolyric"):
                records.append(dict(track, plainLyrics=None, syncedLyrics=text))
                attempt.update(records=1, candidates=1)
    return records, attempts
