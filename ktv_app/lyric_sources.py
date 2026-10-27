"""Bounded public lyric-site fallback, with source-specific body extraction.

No search snippet, pronunciation annotation, or navigation text becomes lyrics.
Sites requiring login/challenges are reported as unavailable, never bypassed.
"""

import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .lyric_names import artist_similarity, japanese_title, similarity

MAX_PAGE_BYTES = 600_000
MAX_LYRIC_PAGES = 3
SITE_QUERY = "(site:utaten.com/lyric/ OR site:j-lyric.net/artist/)"


class LyricPage(HTMLParser):
    """Read only documented page regions, dropping ruby readings and hidden copies."""

    def __init__(self, provider):
        super().__init__(convert_charrefs=True)
        self.provider = provider
        self.stack = []
        self.parts = {"lyrics": [], "song": [], "artist": []}
        self.links = []
        self.metadata = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        region = self.stack[-1][1] if self.stack else None
        hidden = (self.stack[-1][2] if self.stack else False) or (
            tag in ("script", "style", "noscript", "rt", "rp")
            or "rt" in classes
            or "romaji" in classes
            or "newLyricTitle_afterTxt" in classes
        )
        if self.provider == "UtaTen":
            if "hiragana" in classes:
                region = "lyrics"
            elif "newLyricTitle__main" in classes:
                region = "song"
            elif "newLyricWork__name" in classes:
                region = "artist"
        elif self.provider == "J-Lyric" and attrs.get("id") == "Lyric":
            region = "lyrics"
        if tag == "meta":
            self.metadata[attrs.get("property") or attrs.get("name")] = attrs.get(
                "content", ""
            )
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        if tag == "br" and region and not hidden:
            self.parts[region].append("\n")
        if tag not in ("br", "hr", "img", "input", "meta", "link", "source", "wbr"):
            self.stack.append((tag, region, hidden))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack and not self.stack[-1][2] and self.stack[-1][1]:
            # Source markup indentation is not a verse break; only <br> is.
            self.parts[self.stack[-1][1]].append(re.sub(r"\s+", " ", data))

    def record(self, url):
        text = "\n".join(
            line.strip() for line in "".join(self.parts["lyrics"]).split("\n")
        ).strip()
        song = "".join(self.parts["song"]).strip()
        artist = "".join(self.parts["artist"]).strip()
        if self.provider == "J-Lyric":
            # Metadata supplies identity only, never lyric content.
            title = self.metadata.get("description", "")
            match = re.match(r"(.+?)の「(.+?)」歌詞ページ", title)
            if match:
                artist, song = match.groups()
        if not song or not artist or not text:
            raise ValueError("页面没有可确认身份的歌词正文")
        return {
            "id": self.provider.lower()
            + "-"
            + hashlib.sha256(url.encode()).hexdigest()[:16],
            "trackName": song,
            "artistName": artist,
            "duration": None,
            "plainLyrics": text,
            "syncedLyrics": None,
            "provider": self.provider,
            "source": url,
        }


def supported_page(url):
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password or port:
        return None
    if parsed.hostname == "utaten.com" and re.fullmatch(r"/lyric/[\w-]+/", parsed.path):
        return "UtaTen"
    if parsed.hostname == "j-lyric.net" and re.fullmatch(
        r"/artist/a[0-9a-f]+/l[0-9a-f]+\.html", parsed.path
    ):
        return "J-Lyric"
    return None


def fetch_page(client, url, params=None):
    # Do not follow redirects out of a fixed source, or fetch arbitrary search URLs.
    with client.stream("GET", url, params=params) as response:
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", ""):
            raise ValueError("来源返回的不是歌词网页")
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_PAGE_BYTES:
                raise ValueError("歌词网页超过大小限制")
        return bytes(data).decode("utf-8", errors="strict")


def lookup_sites(song, artist, web_search):
    """UtaTen title search, then a fixed-site search if necessary (no LLM)."""
    attempts, records, sources, seen = [], [], [], set()
    title = japanese_title(song)
    with httpx.Client(
        timeout=httpx.Timeout(8, connect=4),
        follow_redirects=False,
        headers={"User-Agent": "LyriFrame/1.1 (lyric source lookup)"},
    ) as client:
        attempt = {
            "provider": "UtaTen",
            "query": {"title": title},
            "reason": "independent_lyric_site",
            "http_status": None,
            "records": 0,
            "candidates": 0,
        }
        attempts.append(attempt)
        links = []
        try:
            html = fetch_page(client, "https://utaten.com/search", {"title": title})
            parser = LyricPage("UtaTen")
            parser.feed(html)
            links = list(
                dict.fromkeys(
                    urljoin("https://utaten.com", link)
                    for link in parser.links
                    if re.fullmatch(r"/lyric/[\w-]+/", link)
                )
            )[:MAX_LYRIC_PAGES]
            attempt.update(status="success", http_status=200, records=len(links))
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            mark_failure(attempt, exc)

        def read_links(urls):
            for url in urls:
                if len(seen) >= 2 * MAX_LYRIC_PAGES or url in seen:
                    continue
                provider = supported_page(url)
                if not provider:
                    continue
                seen.add(url)
                item = {
                    "provider": provider,
                    "query": {"url": url},
                    "reason": "lyric_body",
                    "http_status": None,
                    "records": 0,
                    "candidates": 0,
                }
                attempts.append(item)
                try:
                    page = LyricPage(provider)
                    page.feed(fetch_page(client, url))
                    records.append(page.record(url))
                    item.update(
                        status="success", http_status=200, records=1, candidates=1
                    )
                except (httpx.HTTPError, ValueError, UnicodeError) as exc:
                    mark_failure(item, exc)

        read_links(links)
        if not any(
            similarity(song, r["trackName"]) >= 0.85
            and (not artist or artist_similarity(artist, r["artistName"]) >= 0.7)
            for r in records
        ):
            query = f"{title} {artist} 歌詞 {SITE_QUERY}".strip()[:350]
            item = {
                "provider": "指定歌词站检索",
                "query": {"q": query},
                "reason": "fixed_site_search",
                "http_status": None,
                "records": 0,
                "candidates": 0,
            }
            attempts.append(item)
            try:
                results = list(web_search(query))[:4]
                item.update(status="success", records=len(results))
                for result in results:
                    url = result.get("href") or result.get("url") or ""
                    if supported_page(url):
                        sources.append(url)
                read_links(sources[:MAX_LYRIC_PAGES])
            except Exception:
                item.update(status="failed", error="网页检索不可用")
    return records, attempts


def mark_failure(attempt, exc):
    attempt["status"] = "failed"
    if isinstance(exc, httpx.HTTPStatusError):
        attempt["http_status"] = exc.response.status_code
    attempt["error"] = "来源暂不可用或页面结构不受支持；未采用摘要作为歌词"
