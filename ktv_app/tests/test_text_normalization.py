from ktv_app.lyrics import contextual_languages, preprocess
from ktv_app.text_normalization import (
    clean_lyric_text,
    normalize_lyrics,
    alignment_display_map,
)
from ktv_app.aligners.tifa import parse_result


def test_source_timing_is_stripped_without_reordering_or_expanding_repeats():
    source = (
        "[ar:Artist]\n[offset:-500]\n[00:40.00][00:10.00]先唱这一句\n"
        "[00:01.00]<00:01.00>心里[爱你]<00:02.00>\n"
        "[00:50.00]先唱这一句\n[chorus]\n作词：某人"
    )
    expected = "先唱这一句\n心里[爱你]\n先唱这一句"
    assert clean_lyric_text(source) == expected
    assert clean_lyric_text(expected) == expected
    lines = preprocess(source)
    assert [line.text for line in lines] == expected.splitlines()
    assert all(
        line.start is None and line.end is None and "hint" not in line.model_dump()
        for line in lines
    )


def test_japanese_song_context_keeps_kanji_only_lines_japanese():
    text = "夜を越えて\n月が照らす\n空へ届け\n少年少女戦国無双"
    lines = preprocess(text)
    assert [line.language for line in lines] == ["ja"] * 4
    assert [line.text for line in lines] == text.splitlines()
    assert len({line.id for line in lines}) == 4


def test_context_does_not_override_explicit_or_bilingual_lyrics():
    texts = ["月光明亮", "静静等候", "愿你平安", "さようなら", "Stay with me"]
    assert contextual_languages(texts) == ["zh", "zh", "zh", "ja", "en"]
    text = "夜を越えて\n月が照らす\n空へ届け\n少年少女戦国無双"
    assert all(line.language == "zh" for line in preprocess(text, "zh"))


def test_auto_japanese_context_preserves_english_lines():
    assert contextual_languages(
        ["君の声", "夜を越え", "空へ届け", "世界平和", "Hello world"]
    ) == [
        "ja",
        "ja",
        "ja",
        "ja",
        "en",
    ]


def test_mixed_lookalike_is_repaired_without_changing_display():
    original = "You ar\u0435 my special"
    line = preprocess(original, "ja")[0]
    assert line.text == original and line.normalized == "You are my special"
    assert line.normalization_changes[0]["codepoint"] == "U+0435"
    assert line.normalization_changes[0]["original"] == "\u0435"
    flat, positions = alignment_display_map(original)
    start = flat.index("are")
    a, b = positions[start], positions[start + 2] + 1
    assert original[a:b] == "ar\u0435"


def test_real_cyrillic_and_unmappable_words_are_not_transliterated():
    for original in ("Привет мир", "СОС", "aЖe", "Bосс", "日本語 Ελληνικά"):
        assert normalize_lyrics(original) == (original, [])
    assert normalize_lyrics("Ａr\u0435")[0] == "Are"


def test_casefold_expansion_preserves_character_mapping():
    flat, positions = alignment_display_map("Straße ar\u0435")
    assert flat == "strasseare" and positions[4:6] == [4, 4]


def test_native_word_times_map_back_to_lookalike_display(tmp_path):
    import json
    import textgrid

    line = preprocess("You ar\u0435 my special", "en")[0]
    grid = textgrid.TextGrid(maxTime=4)
    tier = textgrid.IntervalTier(name="texts", maxTime=4)
    for index, word in enumerate(["you", "are", "my", "special"]):
        tier.add(index, index + 1, word)
    grid.append(tier)
    grid.write(str(tmp_path / "output.TextGrid"))
    trace = [{"line_id": line.id, "text": w} for w in ["you", "are", "my", "special"]]
    (tmp_path / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    result = parse_result(
        [line], tmp_path / "output.TextGrid", tmp_path / "trace.json", 4, 0
    )
    assert (
        result.lines[0].units[1].text == "ar\u0435"
        and result.lines[0].units[1].normalized == "are"
    )
    assert result.lines[0].units[1].start == 1 and result.lines[0].units[1].end == 2
