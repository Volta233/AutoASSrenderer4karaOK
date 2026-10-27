"""Unit tests never depend on the availability of independent lyric websites."""

import pytest


@pytest.fixture(autouse=True)
def offline_lyric_fallbacks(monkeypatch):
    # Provider adapters have their own mocked-HTTP tests. Orchestration tests may
    # explicitly replace these aliases to exercise availability/fallback paths.
    monkeypatch.setattr("ktv_app.lyrics.lookup_netease", lambda *args: ([], []))
    monkeypatch.setattr("ktv_app.lyrics.lookup_sites", lambda *args: ([], []))
