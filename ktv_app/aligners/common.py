"""Copy lyric lines without retaining prior alignment state."""


def blank(lines):
    result = [l.model_copy(deep=True) for l in lines]
    for line in result:
        line.units = []
        line.start = line.end = None
        line.flags = []
        line.display_kind = "karaoke"
        line.completion_note = ""
    return result
