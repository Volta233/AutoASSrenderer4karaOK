"""Retain semantic owner IDs after TIFA's zero-width phone omission."""


def retained_word_indices(words, spans, timestep):
    result = []
    for owner, span in zip(words, spans):
        if round(float(span[0]) * timestep, 3) >= round(float(span[1]) * timestep, 3):
            continue
        if not result or result[-1] != int(owner):
            result.append(int(owner))
    return result


def complete_word_indices(words, spans, timestep):
    skipped = {
        int(owner)
        for owner, span in zip(words, spans)
        if round(float(span[0]) * timestep, 3) >= round(float(span[1]) * timestep, 3)
    }
    return sorted(set(map(int, words)) - skipped)
