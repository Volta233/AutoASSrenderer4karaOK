"""Bounded source-only lookup handles script differences and artist spelling aliases."""

import httpx
import pytest

from ktv_app.config import Settings
from ktv_app.lyrics import candidate_evidence, search, title_variants


@pytest.fixture(autouse=True)
def no_external_web_or_unselected_ai(monkeypatch):
    monkeypatch.setattr("ktv_app.lyrics.search_web", lambda query: [])


def record(identifier=123, song="綠光", artist="孫燕姿", **fields):
    return {
        "id": identifier,
        "trackName": song,
        "artistName": artist,
        "duration": 221,
        "plainLyrics": "來源歌詞不被繁簡重寫",
        **fields,
    }


def mock_lookup(monkeypatch, result):
    calls = []

    def get(self, url, **kwargs):
        params = dict(kwargs["params"])
        calls.append(params)
        status, rows = result(params)
        return httpx.Response(status, request=httpx.Request("GET", url), json=rows)

    monkeypatch.setattr(httpx.Client, "get", get)
    return calls


def test_exact_success_does_not_add_queries_or_change_source_text(monkeypatch):
    calls = mock_lookup(monkeypatch, lambda params: (200, [record()]))
    result = search("绿光", "孙燕姿", 221, Settings())
    assert calls == [{"track_name": "绿光", "artist_name": "孙燕姿"}]
    assert result["query"] == calls[0]
    assert result["actual_query_attempts"][0]["http_status"] == 200
    assert result["candidates"][0]["text"] == "來源歌詞不被繁簡重寫"
    assert result["candidates"][0]["song"] == "綠光"
    assert result["candidates"][0]["score"] == 1


def test_song_only_handles_artist_spelling_but_ranks_against_original_artist(
    monkeypatch,
):
    calls = mock_lookup(
        monkeypatch,
        lambda params: (
            200,
            [] if "artist_name" in params else [record(124, artist="宋雨琦"), record()],
        ),
    )
    result = search("绿光", "孙燕姿", 221, Settings())
    assert calls == [
        {"track_name": "绿光", "artist_name": "孙燕姿"},
        {"track_name": "绿光"},
    ]
    assert result["query"]["artist_name"] == "孙燕姿"
    assert result["candidates"][0]["id"] == "123"
    assert result["candidates"][1]["evidence"]["artist_similarity"] < 1
    assert "歌手别名" in result["verification"]["retrieval_warning"]
    assert (
        result["actual_query_attempts"][1]["reason"]
        == "original_song_without_artist_filter"
    )


def test_chinese_title_variant_only_after_original_queries_have_no_lyrics(monkeypatch):
    calls = mock_lookup(
        monkeypatch,
        lambda params: (
            200,
            [record(), record()] if params["track_name"] == "綠光" else [],
        ),
    )
    result = search("绿光", "孙燕姿", 221, Settings())
    assert calls == [
        {"track_name": "绿光", "artist_name": "孙燕姿"},
        {"track_name": "绿光"},
        {"track_name": "綠光"},
    ]
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["source"] == "https://lrclib.net/api/get/123"
    assert result["actual_query_attempts"][2]["candidates"] == 1
    assert result["query"] == calls[0]


def test_maximum_two_title_variants_and_no_duplicate_requests(monkeypatch):
    calls = mock_lookup(monkeypatch, lambda params: (200, []))
    result = search("绿光與爱", "歌手", 221, Settings())
    assert title_variants("绿光與爱") == ["綠光與愛", "绿光与爱"]
    assert len(calls) == 4
    assert calls[2:] == [{"track_name": "綠光與愛"}, {"track_name": "绿光与爱"}]
    assert len({tuple(sorted(p.items())) for p in calls}) == len(calls)
    assert len(result["actual_query_attempts"]) == len(calls)
    assert result["candidates"] == [] and result["auto_id"] is None


def test_without_artist_does_not_repeat_identical_song_query(monkeypatch):
    calls = mock_lookup(monkeypatch, lambda params: (200, []))
    result = search("Yesterday", "", 221, Settings())
    assert calls == [{"track_name": "Yesterday"}]
    assert result["actual_query_attempts"][0]["reason"] == "original_song"


def test_http_failure_is_reported_without_rapid_fallback_retry(monkeypatch):
    calls = mock_lookup(
        monkeypatch, lambda params: (503, {"error": "service temporarily unavailable"})
    )
    result = search("绿光", "孙燕姿", 221, Settings())
    assert len(calls) == 1
    assert result["actual_query_attempts"][0]["http_status"] == 503
    assert result["actual_query_attempts"][0]["status"] == "failed"
    assert "LRCLIB" in result["verification"]["retrieval_warning"]
    assert result["candidates"] == []


def test_records_without_actual_lyrics_do_not_prevent_bounded_fallback(monkeypatch):
    calls = mock_lookup(
        monkeypatch,
        lambda params: (
            200,
            [record(plainLyrics=None, instrumental=True)]
            if "artist_name" in params
            else [record()],
        ),
    )
    result = search("绿光", "孙燕姿", 221, Settings())
    assert len(calls) == 2 and len(result["candidates"]) == 1
    assert result["actual_query_attempts"][0]["records"] == 1
    assert result["actual_query_attempts"][0]["candidates"] == 0


def test_script_equivalence_does_not_pretend_romanized_artist_alias_is_verified():
    evidence = candidate_evidence("绿光", "孙燕姿", 221, record(artist="Sun Yan-Zi"))
    assert evidence["evidence"]["title_similarity"] == 1
    assert evidence["evidence"]["artist_similarity"] < 1
    assert evidence["evidence"]["recording_verified"] is False
    assert evidence["score_kind"] == "retrieval_ranking_not_probability"
