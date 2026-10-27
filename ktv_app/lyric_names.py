"""Name-only query variants. These conversions never rewrite lyric content."""

import difflib
import re
from functools import lru_cache

from .timeline import canonical

# Conservative, unambiguous Japanese shinjitai variants used in song/artist names.
# OpenCC's Python distribution ships Chinese conversions, but no t2jp dictionary.
_TRADITIONAL = "櫻黑澤樂廣戰聲國龍圓氣戀"
_JAPANESE = "桜黒沢楽広戦声国竜円気恋"
_TO_JAPANESE = str.maketrans(_TRADITIONAL, _JAPANESE)
_FROM_JAPANESE = str.maketrans(_JAPANESE, _TRADITIONAL)


@lru_cache(maxsize=2)
def chinese_converter(conversion):
    from opencc import OpenCC

    return OpenCC(conversion)


def name_canonical(text):
    text = text.translate(_FROM_JAPANESE)
    if re.search(r"[\u3400-\u9fff]", text):
        text = chinese_converter("t2s").convert(text)
    return canonical(text)


def similarity(a, b):
    return difflib.SequenceMatcher(None, name_canonical(a), name_canonical(b)).ratio()


def artist_similarity(artist, credited):
    # Compare with an explicitly credited collaborator; never invent aliases.
    people = re.split(
        r"\s*(?:/|、|&|\bfeat\.?\s+|\bfeaturing\s+)\s*", credited, flags=re.I
    )
    return max(
        (similarity(artist, name) for name in [credited, *people] if name.strip()),
        default=0,
    )


def title_variants(song):
    if not re.search(r"[\u3400-\u9fff]", song):
        return []
    traditional = chinese_converter("s2t").convert(song.translate(_FROM_JAPANESE))
    variants = [
        traditional,
        chinese_converter("t2s").convert(traditional),
        traditional.translate(_TO_JAPANESE),
    ]
    return list(dict.fromkeys(v for v in variants if v != song))[:3]


def japanese_title(song):
    return chinese_converter("s2t").convert(song).translate(_TO_JAPANESE)
