"""Source lyric cleanup and conservative pronunciation normalization."""

import re
import unicodedata

# One-character Cyrillic/Latin lookalikes, restricted to mixed Latin words.
# Unicode confusables: https://www.unicode.org/Public/security/latest/confusables.txt
CYRILLIC_LOOKALIKES = dict(zip("аеорсхуіјѕАВСЕНКМОРТХІЈЅ", "aeopcxyijsABCEHKMOPTXIJS"))
MIXED_WORD = re.compile(r"[A-Za-z\u0400-\u052f]+")


def strip_lyric_timestamps(text):
    text = re.sub(r"\[\d+:\d+(?:[.,:]\d+)*\]", "", text)
    return re.sub(r"<\d+:\d+(?:[.,:]\d+)*>", "", text)


def clean_lyric_text(text):
    """Discard source timing and metadata without sorting or expanding lines.

    Explicit repeated lines and non-timing brackets remain in source order.
    Pronunciation normalization is separate from the displayed lyric text.
    """
    lines = []
    for raw in text.replace("\r", "").split("\n"):
        display = strip_lyric_timestamps(raw)
        display = re.sub(
            r"\[(?:ar|ti|al|by|offset|length):[^\]]*\]", "", display, flags=re.I
        )
        display = re.sub(
            r"\[(?:verse|chorus|intro|outro|bridge|instrumental|副歌|主歌|前奏|间奏)(?:\s+\d+)?\]",
            "",
            display,
            flags=re.I,
        ).strip()
        if not display or re.match(
            r"^(作词|作詞|作曲|编曲|編曲|制作人|製作人|词|詞|曲|歌手|演唱|歌|Lyrics|Composer)\s*[:：]",
            display,
            re.I,
        ):
            continue
        lines.append(display)
    return "\n".join(lines)


def repair_mixed_latin(text):
    changes = []

    def replace(match):
        word = match.group()
        latin_count = sum(c.isascii() and c.isalpha() for c in word)
        if not latin_count or latin_count < len(word) - latin_count:
            return word
        if not all(c.isascii() or c in CYRILLIC_LOOKALIKES for c in word):
            return word
        fixed = "".join(CYRILLIC_LOOKALIKES.get(c, c) for c in word)
        for i, (before, after) in enumerate(zip(word, fixed)):
            if before != after:
                changes.append(
                    {
                        "normalized_index": match.start() + i,
                        "original": before,
                        "replacement": after,
                        "codepoint": f"U+{ord(before):04X}",
                        "name": unicodedata.name(before),
                        "word": word,
                        "pronunciation_word": fixed,
                    }
                )
        return fixed

    return MIXED_WORD.sub(replace, text), changes


def normalize_lyrics(text):
    return repair_mixed_latin(unicodedata.normalize("NFKC", text))


def alignment_display_map(text):
    normalized, source_positions = [], []
    for index, char in enumerate(text):
        expanded = unicodedata.normalize("NFKC", char)
        normalized.extend(expanded)
        source_positions.extend([index] * len(expanded))
    repaired, _ = repair_mixed_latin("".join(normalized))
    chars, positions = [], []
    for char, index in zip(repaired, source_positions):
        for folded in char.casefold():
            if folded.isalnum():
                chars.append(folded)
                positions.append(index)
    return "".join(chars), positions
