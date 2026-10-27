"""Regression cases for the actual 千本樱 failure and independent source parsing."""

import httpx
import pytest

from ktv_app.config import Settings
from ktv_app.lyric_names import artist_similarity, similarity, title_variants
from ktv_app.lyric_netease import lookup_netease
from ktv_app.lyric_sources import LyricPage, fetch_page, lookup_sites, supported_page
from ktv_app.lyrics import candidate_evidence, preprocess, search


def record(**fields):
    return dict(
        id="netease-123",
        trackName="千本桜",
        artistName="黒うさP / 初音ミク",
        duration=245.7,
        plainLyrics="一番の歌詞\n二番の歌詞\n一番の歌詞\n最後の歌詞",
        source="https://music.163.com/song?id=123",
        provider="网易云音乐",
        **fields,
    )


def response(url, data, status=200):
    return httpx.Response(status, request=httpx.Request("GET", url), json=data)


def test_japanese_script_variants_are_name_only_and_bounded():
    assert title_variants("千本樱") == ["千本櫻", "千本桜"]
    assert title_variants("千本桜") == ["千本櫻", "千本樱"]
    assert similarity("千本樱", "千本桜") == 1
    assert similarity("黑うさP", "黒うさP") == 1
    assert artist_similarity("初音ミク", "黒うさP / 初音ミク") == 1
    assert artist_similarity("Hatsune Miku", "初音ミク") < 1
    lines = preprocess("桜と櫻と樱\n桜と櫻と樱", "ja")
    assert [l.text for l in lines] == ["桜と櫻と樱"] * 2


def test_primary_503_continues_to_independent_source_without_ai(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "get", lambda self, url, **kw: response(url, {}, 503)
    )
    monkeypatch.setattr(
        "ktv_app.lyrics.lookup_netease",
        lambda *args: (
            [record()],
            [
                dict(
                    provider="网易云音乐",
                    query={"s": "千本桜"},
                    status="success",
                    records=1,
                    candidates=1,
                )
            ],
        ),
    )
    result = search("千本樱", "", 244.1, Settings())
    assert result["retrieval_status"] == "results"
    assert result["candidates"][0]["source"] == "https://music.163.com/song?id=123"
    assert result["candidates"][0]["evidence"]["title_similarity"] == 1
    assert result["candidates"][0]["recommendation"] == "compare"
    assert result["auto_id"] is None
    assert result["actual_query_attempts"][0]["http_status"] == 503


def test_low_title_similarity_does_not_block_other_sources(monkeypatch):
    unrelated = dict(
        id=2,
        trackName="完全不同的歌",
        artistName="其他歌手",
        duration=244,
        plainLyrics="其他歌曲正文",
    )
    monkeypatch.setattr(
        httpx.Client, "get", lambda self, url, **kw: response(url, [unrelated])
    )
    monkeypatch.setattr("ktv_app.lyrics.lookup_netease", lambda *args: ([record()], []))
    result = search("千本樱", "", 244, Settings())
    assert result["candidates"][0]["song"] == "千本桜"
    assert len(result["candidates"]) == 2


def test_secondary_failure_continues_to_site_body(monkeypatch):
    monkeypatch.setattr(
        httpx.Client, "get", lambda self, url, **kw: response(url, {}, 503)
    )
    monkeypatch.setattr("ktv_app.lyrics.lookup_sites", lambda *args: ([record()], []))
    assert search("千本樱", "", 244, Settings())["candidates"]


def test_full_fallback_routes_real_adapters_after_primary_outage(monkeypatch):
    original_client = httpx.Client
    calls = []

    def route(request):
        calls.append(str(request.url))
        if request.url.host == "lrclib.net":
            return httpx.Response(503)
        if request.url.path.endswith("search/get"):
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "result": {
                        "songs": [
                            dict(
                                id=123,
                                name="千本桜",
                                artists=[{"name": "初音ミク"}],
                                duration=244100,
                            )
                        ]
                    },
                },
            )
        assert request.url.path.endswith("song/lyric")
        return httpx.Response(
            200, json={"code": 200, "lrc": {"lyric": "[00:01]原文の歌詞"}}
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original_client(transport=httpx.MockTransport(route), **kw),
    )
    monkeypatch.setattr("ktv_app.lyrics.lookup_netease", lookup_netease)
    monkeypatch.setattr(
        "ktv_app.lyrics.lookup_sites",
        lambda *args: pytest.fail("source already suitable"),
    )
    result = search("千本樱", "初音ミク", 244.1, Settings())
    assert len(calls) == 4
    assert result["candidates"][0]["recommendation"] == "recommended"
    assert result["candidates"][0]["provider"] == "网易云音乐"


def test_netease_keeps_only_original_lrc_and_correct_seconds(monkeypatch):
    calls = []

    def get(self, url, **kw):
        calls.append((url, kw["params"]))
        if url.endswith("search/get"):
            data = {
                "code": 200,
                "result": {
                    "songs": [
                        dict(
                            id=123,
                            name="千本桜",
                            artists=[{"name": "初音ミク"}],
                            duration=245700,
                        )
                    ]
                },
            }
        else:
            data = {
                "code": 200,
                "lrc": {"lyric": "[00:01.00]原文の歌詞"},
                "tlyric": {"lyric": "译文"},
                "romalrc": {"lyric": "romaji"},
            }
        return response(url, data)

    monkeypatch.setattr(httpx.Client, "get", get)
    rows, attempts = lookup_netease("千本樱", "", 244)
    assert len(rows) == 1 and rows[0]["duration"] == 245.7
    assert rows[0]["syncedLyrics"] == "[00:01.00]原文の歌詞"
    assert rows[0]["source"].endswith("id=123")
    assert len(calls) == 3 and attempts[-1]["candidates"] == 1


def test_netease_unavailable_has_no_immediate_retry(monkeypatch):
    calls = []

    def get(self, url, **kw):
        calls.append(url)
        return response(url, {"code": 503}, 503)

    monkeypatch.setattr(httpx.Client, "get", get)
    rows, attempts = lookup_netease("千本樱", "", 244)
    assert rows == [] and len(calls) == 1 and attempts[0]["status"] == "failed"


def test_utaten_parser_drops_ruby_and_romaji_keeps_repeats_and_breaks():
    page = LyricPage("UtaTen")
    page.feed("""<h2 class="newLyricTitle__main">千本桜<span class="newLyricTitle_afterTxt">歌詞</span></h2>
      <dt class="newLyricWork__name"><h3>黒うさP</h3></dt>
      <div class="hiragana"><span class="ruby"><span class="rb">桜</span><span class="rt">さくら</span></span>だ<br>
      君が歌う<br>桜だ<br>最後の歌</div><div class="romaji">sakura da<br>kimi ga utau</div>
      <div>広告とコメント</div>""")
    row = page.record("https://utaten.com/lyric/test/")
    assert row["plainLyrics"].splitlines() == ["桜だ", "君が歌う", "桜だ", "最後の歌"]
    assert row["trackName"] == "千本桜" and row["artistName"] == "黒うさP"
    assert row["duration"] is None


def test_jlyric_extracts_actual_body_not_description():
    page = LyricPage("J-Lyric")
    page.feed(
        '<meta name="description" content="歌手の「曲名」歌詞ページ。摘要">'
        '<p id="Lyric">第一句<br>第二句<br>第一句<br>第四句</p><p>広告</p>'
    )
    row = page.record("https://j-lyric.net/artist/a123/l456.html")
    assert row["trackName"] == "曲名" and row["artistName"] == "歌手"
    assert row["plainLyrics"].splitlines() == ["第一句", "第二句", "第一句", "第四句"]


def test_short_lyrics_are_not_discarded_by_an_arbitrary_line_count():
    page = LyricPage("J-Lyric")
    page.feed(
        '<meta name="description" content="歌手の「曲名」歌詞ページ。">'
        '<p id="Lyric">一行だけの歌</p>'
    )
    assert (
        page.record("https://j-lyric.net/artist/a123/l456.html")["plainLyrics"]
        == "一行だけの歌"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://utaten.com/lyric/a/",
        "https://utaten.com.evil.example/lyric/a/",
        "https://utaten.com@127.0.0.1/lyric/a/",
        "https://utaten.com:8768/lyric/a/",
        "https://j-lyric.net/not-a-lyric",
        "https://127.0.0.1/lyric/a/",
    ],
)
def test_site_parser_only_reads_fixed_public_lyric_hosts(url):
    assert supported_page(url) is None


def test_site_fetch_rejects_redirects_and_oversized_bodies():
    for status, headers, body in [
        (302, {"location": "http://127.0.0.1"}, b""),
        (200, {"content-type": "text/html"}, b"x" * 600001),
    ]:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(status, headers=headers, content=body)
        )
        with httpx.Client(transport=transport) as client:
            with pytest.raises((httpx.HTTPError, ValueError)):
                fetch_page(client, "https://utaten.com/lyric/test/")


def test_fixed_site_lookup_fetches_real_body_not_web_snippet(monkeypatch):
    calls = []

    def fetch(client, url, params=None):
        calls.append(url)
        if url.endswith("/search"):
            return '<a href="/lyric/test/">曲</a><a href="/lyric/test/">重複</a>'
        return (
            '<h2 class="newLyricTitle__main">曲</h2>'
            '<dt class="newLyricWork__name">歌手</dt>'
            '<div class="hiragana">一句<br>二句<br>一句<br>四句</div>'
        )

    monkeypatch.setattr("ktv_app.lyric_sources.fetch_page", fetch)
    rows, attempts = lookup_sites(
        "曲", "歌手", lambda query: pytest.fail("No web search needed")
    )
    assert len(rows) == 1 and rows[0]["provider"] == "UtaTen"
    assert len(calls) == 2 and attempts[-1]["candidates"] == 1


def test_missing_duration_or_translated_title_not_a_verified_match():
    row = record()
    row["duration"] = None
    ev = candidate_evidence("千本樱", "黒うさP", 244, row)
    assert ev["recommendation"] == "review"
    assert "来源没有录音时长" in ev["recommendation_reasons"]
    row["trackName"] = "千本樱 中文版"
    ev = candidate_evidence("千本樱", "黒うさP", 244, row)
    assert any("译文" in w for w in ev["warnings"])


def test_japanese_credits_removed_but_chorus_never_deduplicated():
    rows = preprocess("作詞：作者\n編曲：作者\n第一句\n第二句\n第一句")
    assert [r.text for r in rows] == ["第一句", "第二句", "第一句"]
