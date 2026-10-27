"""Compare both independent ASR passes to lyric text and verify cut evidence."""

import difflib
from .recognition import match_text
from .block_policy import RELAXATION_RULES


def match_boundary_lines(lines, tokens, origin=0, reading=None, endpoint_chars=2):
    """Global edit alignment maps exact characters to whole timed ASR tokens.

    Missing/incorrect interior recognition can support context, but cannot
    invent a boundary word. No time is interpolated within an ASR token.
    """
    transform = lambda text: match_text(reading(text) if reading else text)
    keys = [transform(line.text) for line in lines]
    source = "".join(keys)
    observed, owners = "", []
    for ti, token in enumerate(tokens):
        key = transform(token["text"])
        observed += key
        owners.extend([ti] * len(key))
    n, m = len(source), len(observed)
    if not n or not m:
        return []
    scores = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        scores[i][0] = -2 * i
    for j in range(m + 1):
        scores[0][j] = -2 * j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            scores[i][j] = max(
                scores[i - 1][j - 1] + (3 if source[i - 1] == observed[j - 1] else -2),
                scores[i - 1][j] - 2,
                scores[i][j - 1] - 2,
            )
    mapping = {}
    i, j = n, m
    while i or j:
        if (
            i
            and j
            and scores[i][j]
            == scores[i - 1][j - 1] + (3 if source[i - 1] == observed[j - 1] else -2)
        ):
            if source[i - 1] == observed[j - 1]:
                mapping[i - 1] = j - 1
            i -= 1
            j -= 1
        elif i and scores[i][j] == scores[i - 1][j] - 2:
            i -= 1
        else:
            j -= 1
    matches = []
    offset = 0
    for li, key in enumerate(keys):
        positions = [p for p in range(len(key)) if offset + p in mapping]
        if not positions:
            offset += len(key)
            continue
        indices = [mapping[offset + p] for p in positions]
        subset = tokens[owners[indices[0]] : owners[indices[-1]] + 1]
        if not subset or subset[-1]["end"] - subset[0]["start"] > max(
            12, len(key) * 0.9 + 3
        ):
            offset += len(key)
            continue
        size = min(endpoint_chars, len(key))

        def anchor(a, b):
            indices = [mapping.get(offset + p) for p in range(a, b)]
            if (
                len(key) < endpoint_chars
                or any(p is None for p in indices)
                or indices != list(range(indices[0], indices[0] + len(indices)))
            ):
                return False
            x, y = indices[0], indices[-1]
            if (a == 0 and x > 0 and owners[x] == owners[x - 1]) or (
                b == len(key) and y + 1 < m and owners[y] == owners[y + 1]
            ):
                return False
            segment = tokens[owners[x] : owners[y] + 1]
            return all(0 < t["end"] - t["start"] <= 4 for t in segment) and all(
                v["start"] >= u["end"] - 0.02 and v["start"] - u["end"] < 2
                for u, v in zip(segment, segment[1:])
            )

        prefix, suffix = anchor(0, size), anchor(len(key) - size, len(key))
        matches.append(
            {
                "line_index": li,
                "line_id": lines[li].id,
                "start": subset[0]["start"] + origin,
                "end": subset[-1]["end"] + origin,
                "coverage": len(positions) / len(key),
                "similarity": difflib.SequenceMatcher(
                    None,
                    key,
                    "".join(transform(t["text"]) for t in subset),
                    autojunk=False,
                ).ratio(),
                "source_key": key,
                "recognized_key": "".join(transform(t["text"]) for t in subset),
                "recognized_text": "".join(t["text"] for t in subset),
                "observed_start": indices[0],
                "observed_end": indices[-1] + 1,
                "prefix_confirmed": prefix,
                "suffix_confirmed": suffix,
                "endpoint_chars": endpoint_chars,
                "matched_positions": positions,
                "method": "exact_character_sequence_with_timed_boundary_words",
                "exact_characters": [
                    dict(
                        position=p,
                        observed_position=mapping[offset + p],
                        token_index=owners[mapping[offset + p]],
                        whole_token_start=(
                            mapping[offset + p] == 0
                            or owners[mapping[offset + p] - 1]
                            != owners[mapping[offset + p]]
                        ),
                        whole_token_end=(
                            mapping[offset + p] + 1 == m
                            or owners[mapping[offset + p] + 1]
                            != owners[mapping[offset + p]]
                        ),
                        start=tokens[owners[mapping[offset + p]]]["start"] + origin,
                        end=tokens[owners[mapping[offset + p]]]["end"] + origin,
                    )
                    for p in positions
                ],
                "context_start": max(
                    origin, subset[0]["start"] + origin - positions[0] * 0.5
                ),
                "context_end": subset[-1]["end"]
                + origin
                + (len(key) - 1 - positions[-1]) * 0.5,
                "next_token_start": tokens[owners[indices[-1]] + 1]["start"] + origin
                if owners[indices[-1]] + 1 < len(tokens)
                else None,
            }
        )
        offset += len(key)
    return matches


def strong_match(
    line, match, minimum_similarity=0.88, minimum_coverage=0.9, require_prefix=True
):
    if (
        match is None
        or match["similarity"] < minimum_similarity
        or match["coverage"] < minimum_coverage
    ):
        return False
    key, observed = (
        match.get("source_key", match_text(line.text)),
        match.get("recognized_key", match_text(match["recognized_text"])),
    )
    if len(key) < 3:
        return False
    blocks = difflib.SequenceMatcher(
        None, key, observed, autojunk=False
    ).get_matching_blocks()
    return (not require_prefix or any(b.a == 0 and b.size for b in blocks)) and any(
        b.a + b.size == len(key) and b.size for b in blocks
    )


def unique_context(lines, index, first, second, source_keys=None, rules=None):
    rules = rules or RELAXATION_RULES[0]
    keys = source_keys or [
        first.get(i, second.get(i, {})).get("source_key", match_text(line.text))
        for i, line in enumerate(lines)
    ]
    for radius in range(1, 5):
        a, b = max(0, index - radius), min(len(lines), index + 2 + radius)
        context = keys[a:b]
        occurrences = sum(
            keys[j : j + len(context)] == context
            for j in range(len(keys) - len(context) + 1)
        )
        if occurrences != 1 or sum(map(len, context)) < 12:
            continue

        # Both passes must observe distinguishing context. At level 2 one
        # interior row may be absent, but the actually supported source pattern
        # must itself be unique; an unseen distinguishing row proves nothing.
        def context_observed(table):
            records = [table.get(j) for j in range(a, b)]
            missing = sum(r is None for r in records)
            if (
                missing > rules["max_missing_context_lines"]
                or not records[0]
                or not records[-1]
            ):
                return False
            if missing:
                mask = [i for i, r in enumerate(records) if r is not None]
                if (
                    len(mask) < 2
                    or sum(
                        all(keys[j + i] == context[i] for i in mask)
                        for j in range(len(keys) - len(context) + 1)
                    )
                    != 1
                ):
                    return False
            if (
                records[0].get("method")
                == "exact_character_sequence_with_timed_boundary_words"
            ):
                supported = sum(
                    len(keys[j]) * table[j]["coverage"]
                    for j in range(a, b)
                    if j in table
                )
                return (
                    supported / max(1, sum(map(len, context)))
                    >= rules["context_coverage"]
                    and all(
                        r["coverage"] >= rules["context_line_coverage"]
                        for r in records
                        if r
                    )
                    and records[-1]["end"] - records[0]["start"]
                    <= max(
                        rules["context_span_floor"],
                        sum(map(len, context)) * rules["context_seconds_per_char"],
                    )
                )
            if missing:
                return False
            return all(strong_match(lines[j], table[j]) for j in range(a, b))

        if context_observed(first) and context_observed(second):
            return list(range(a, b))
    return None


def repeat_proof(
    lines,
    index,
    first,
    second,
    observed_first,
    observed_second,
    trusted=None,
    source_keys=None,
    rules=None,
):
    """An identical refrain needs matching counts and occurrence order in both passes."""
    if observed_first is None or observed_second is None:
        return None
    rules = rules or RELAXATION_RULES[0]
    keys = source_keys or [
        first.get(i, second.get(i, {})).get("source_key", match_text(line.text))
        for i, line in enumerate(lines)
    ]
    size = 2
    phrase = keys[index][-size:] + keys[index + 1][:size]
    source = "".join(keys)

    def occurrences(text):
        return [
            i
            for i in range(len(text) - len(phrase) + 1)
            if text[i : i + len(phrase)] == phrase
        ]

    lower = max(
        (c for c in (trusted or []) if c["after_line_index"] < index),
        key=lambda c: c["after_line_index"],
        default=None,
    )
    upper = min(
        (c for c in (trusted or []) if c["after_line_index"] > index),
        key=lambda c: c["after_line_index"],
        default=None,
    )
    source_lower = sum(map(len, keys[: lower["after_line_index"] + 1])) if lower else 0
    source_upper = (
        sum(map(len, keys[: upper["after_line_index"] + 1])) if upper else len(source)
    )
    expected = [
        p + source_lower for p in occurrences(source[source_lower:source_upper])
    ]
    source_position = sum(len(key) for key in keys[: index + 1]) - size
    if source_position not in expected:
        return None
    ordinal = expected.index(source_position)
    proof = {
        "phrase": phrase,
        "expected_count": len(expected),
        "occurrence": ordinal + 1,
        "bracket_after_line_indices": [
            lower["after_line_index"] if lower else -1,
            upper["after_line_index"] if upper else len(lines) - 1,
        ],
    }
    for name, table, text in (
        ("first", first, observed_first),
        ("second", second, observed_second),
    ):
        observed_lower = (
            table[lower["after_line_index"] + 1]["observed_start"] if lower else 0
        )
        observed_upper = (
            table[upper["after_line_index"]]["observed_end"] if upper else len(text)
        )
        actual = [
            p + observed_lower for p in occurrences(text[observed_lower:observed_upper])
        ]
        left, right = table[index], table[index + 1]
        at = left.get("observed_end", -1) - size
        if (
            len(actual) != len(expected)
            or at not in actual
            or actual.index(at) != ordinal
            or right.get("observed_start") != at + size
        ):
            return None
        proof[name + "_count"] = len(actual)
    context = list(range(max(0, index - 1), min(len(lines), index + 3)))
    for table in (first, second):
        if any(j not in table for j in context):
            return None
        support = sum(len(keys[j]) * table[j]["coverage"] for j in context)
        if support / sum(len(keys[j]) for j in context) < rules[
            "context_coverage"
        ] or any(table[j]["coverage"] < rules["repeat_line_coverage"] for j in context):
            return None
        if table[context[-1]]["end"] - table[context[0]]["start"] > max(
            rules["context_span_floor"],
            sum(len(keys[j]) for j in context) * rules["context_seconds_per_char"],
        ):
            return None
    return context, proof


def complete_line_cut(
    lines,
    index,
    first,
    second,
    source_keys,
    observed_first,
    observed_second,
    relaxation_level=0,
):
    """A jointly timed whole sentence can certify its own end without its neighbour.

    Repeated sentences still need observed counts and occurrence order. A
    single exact-looking refrain cannot identify which repetition was sung.
    """
    left, right = first.get(index), second.get(index)
    rules = RELAXATION_RULES[relaxation_level]
    if not all(
        strong_match(
            lines[index],
            record,
            rules["sentence_similarity"],
            rules["sentence_coverage"],
            rules["sentence_requires_prefix"],
        )
        and (not rules["sentence_requires_prefix"] or record.get("prefix_confirmed"))
        and record.get("suffix_confirmed")
        for record in (left, right)
    ):
        return None
    key = source_keys[index]
    if (
        len(key)
        < rules[
            "sentence_min_en" if lines[index].language == "en" else "sentence_min_zh"
        ]
    ):
        return None
    if (
        rules["sentence_requires_prefix"]
        and abs(left["start"] - right["start"]) > rules["sentence_difference"]
    ) or abs(left["end"] - right["end"]) > rules["sentence_difference"]:
        return None
    source = "".join(source_keys)
    positions = lambda text: [
        p for p in range(len(text) - len(key) + 1) if text[p : p + len(key)] == key
    ]
    expected = positions(source)
    source_position = sum(map(len, source_keys[:index]))
    if source_position not in expected:
        return None
    ordinal = expected.index(source_position)
    proof = {
        "method": "two_pass_complete_unique_sentence",
        "phrase": key,
        "expected_count": len(expected),
    }
    if len(expected) > 1:
        if observed_first is None or observed_second is None:
            return None
        for name, record, text in (
            ("first", left, observed_first),
            ("second", right, observed_second),
        ):
            actual = positions(text)
            at = record["observed_start"]
            if (
                len(actual) != len(expected)
                or at not in actual
                or actual.index(at) != ordinal
            ):
                return None
            proof[name + "_count"] = len(actual)
        proof.update(
            method="two_pass_complete_sentence_occurrence", occurrence=ordinal + 1
        )
    if not rules["sentence_requires_prefix"]:
        # Partial sentences require observed distinguishing surrounding context.
        # An exact-looking final character alone cannot certify a song section.
        context = unique_context(lines, index, first, second, source_keys, rules)
        if context is None:
            return None
        proof.update(
            method="two_pass_confirmed_suffix_with_unique_context",
            context_indices=context,
            prefix_required=False,
        )
    end = max(left["end"], right["end"])
    next_starts = [r.get("next_token_start") for r in (left, right)]
    if relaxation_level and any(s is not None and s < end - 0.02 for s in next_starts):
        return None
    # Only an observed following token provides a pause. With no observed
    # pause, cut at the confirmed sentence endpoint; never extrapolate silence.
    pause_end = (
        min(next_starts) if all(s is not None and s > end for s in next_starts) else end
    )
    time = end + min(0.15, (pause_end - end) / 2)
    return dict(
        after_line_index=index,
        time=round(time, 4),
        pause_start=end,
        pause_end=pause_end,
        context_indices=[index],
        context_proof=proof,
        two_pass_evidence=[left, right],
        boundary_kind="complete_sentence_endpoint"
        if rules["sentence_requires_prefix"]
        else "context_confirmed_sentence_endpoint",
        confirmed_pause=pause_end > end,
    )


def partial_unique_cut(
    lines, index, first, second, keys, observed, level, allow_repeated=False
):
    """Experimental weaker evidence: a unique timed phrase in both ASR passes.

    Never use a lone character or interpolate a boundary inside an ASR word.
    Source and observed occurrence counts prevent accepting a repeated refrain.
    """
    coverage, similarity = (0.45, 0.5) if level == 3 else (0.2, 0.3)
    records = [table.get(index) for table in (first, second)]
    if any(
        not r
        or not r["suffix_confirmed"]
        or r["coverage"] < coverage
        or r["similarity"] < similarity
        for r in records
    ):
        return None
    if abs(records[0]["end"] - records[1]["end"]) > 1.5:
        return None
    key = keys[index]
    source = "".join(keys)
    exact = [{r["position"]: r for r in m["exact_characters"]} for m in records]
    proofs = []
    for size in range(len(key), 1, -1):
        for start in range(len(key) - size + 1):
            phrase = key[start : start + size]
            occurrences = lambda text: [
                p for p in range(len(text) - size + 1) if text[p : p + size] == phrase
            ]
            expected = occurrences(source)
            actual = [occurrences(text) for text in observed]
            if (
                not expected
                or (not allow_repeated and len(expected) != 1)
                or any(len(p) != len(expected) for p in actual)
            ):
                continue
            spans = []
            for mapping in exact:
                segment = [mapping.get(p) for p in range(start, start + size)]
                if any(r is None for r in segment):
                    break
                if any(
                    b["observed_position"] != a["observed_position"] + 1
                    for a, b in zip(segment, segment[1:])
                ):
                    break
                if (
                    not segment[0]["whole_token_start"]
                    or not segment[-1]["whole_token_end"]
                ):
                    break
                # The phrase must occupy complete ASR tokens, with no adjacent
                # matched source character sharing either edge token.
                if (
                    start - 1 in mapping
                    and mapping[start - 1]["token_index"] == segment[0]["token_index"]
                ) or (
                    start + size in mapping
                    and mapping[start + size]["token_index"]
                    == segment[-1]["token_index"]
                ):
                    break
                spans.append([segment[0]["start"], segment[-1]["end"]])
            if len(spans) != 2 or any(abs(a - b) > 0.9 for a, b in zip(*spans)):
                continue
            at = sum(map(len, keys[:index])) + start
            ordinal = expected.index(at)
            if any(
                exact[p][start]["observed_position"] != actual[p][ordinal]
                for p in range(2)
            ):
                continue
            proofs.append(
                dict(
                    phrase=phrase,
                    source_character_span=[start, start + size],
                    occurrences_in_source_and_passes=[len(expected)] * 3,
                    occurrence=ordinal + 1,
                    two_pass_times=spans,
                )
            )
        if proofs:
            break
    if not proofs:
        return None
    end = max(r["end"] for r in records)
    next_starts = [r.get("next_token_start") for r in records]
    if any(s is not None and s < end - 0.02 for s in next_starts):
        return None
    pause_end = (
        min(next_starts) if all(s is not None and s > end for s in next_starts) else end
    )
    return dict(
        after_line_index=index,
        time=round(end + min(0.15, (pause_end - end) / 2), 4),
        pause_start=end,
        pause_end=pause_end,
        context_indices=[index],
        context_proof=dict(
            method="experimental_two_pass_unique_partial_phrase",
            minimum_coverage=coverage,
            minimum_similarity=similarity,
            minimum_phrase_chars=2,
            phrase_evidence=proofs[0],
        ),
        two_pass_evidence=records,
        boundary_kind="partial_unique_sentence_endpoint",
        confirmed_pause=pause_end > end,
        verification_level=level,
    )
