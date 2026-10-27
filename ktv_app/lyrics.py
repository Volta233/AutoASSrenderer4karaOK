import re
import uuid
import math
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from .timeline import Line, canonical
from .web_search import search_web
from .text_normalization import (
    clean_lyric_text,
    normalize_lyrics,
)
from .lyric_names import artist_similarity, similarity, title_variants
from .lyric_sources import lookup_sites
from .lyric_netease import lookup_netease


def language_of(text, default="zh"):
    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\u3400-\u9fff]", text):
        return default if default == "ja" else "zh"
    return "en"


def contextual_languages(texts):
    """Use predominantly Japanese lyrics to disambiguate kanji-only lines.

    Kana is positive evidence; isolated Japanese phrases in bilingual lyrics
    are insufficient. Explicit user language choices never call this helper.
    """
    languages = [language_of(text) for text in texts]
    japanese = languages.count("ja")
    cjk = japanese + languages.count("zh")
    if japanese >= 2 and japanese / max(1, cjk) >= 0.7:
        return ["ja" if language == "zh" else language for language in languages]
    return languages


def preprocess(text, language="auto"):
    lines = []
    for index, display in enumerate(clean_lyric_text(text).split("\n")):
        normalized, normalization_changes = normalize_lyrics(display)
        if not canonical(normalized):
            continue
        lines.append(
            Line(
                id=uuid.uuid4().hex[:12],
                text=display,
                normalized=normalized,
                language=language_of(display) if language == "auto" else language,
                source_line=index,
                normalization_changes=normalization_changes,
            )
        )
    if language == "auto":
        for line, inferred in zip(lines, contextual_languages(l.text for l in lines)):
            line.language = inferred
    if not lines:
        raise ValueError("没有可对齐的歌词，请粘贴文本或导入 TXT/LRC")
    return lines


def candidate_evidence(song, artist, duration, record):
    title = similarity(song, record["trackName"])
    artist_match = (
        artist_similarity(artist, record["artistName"]) if artist.strip() else None
    )
    declared = float(record.get("duration") or 0)
    if not math.isfinite(declared) or declared < 0:
        raise ValueError("歌词来源录音时长无效")
    difference = abs(declared - duration) if declared > 0 else None
    tolerance = max(5, duration * 0.05)
    duration_match = (
        math.exp(-difference / tolerance) if difference is not None else None
    )
    audit = {"warnings": [], "requires_version_review": False}
    if difference is not None and difference > max(15, duration * 0.15):
        audit["warnings"].append(
            f"录音时长差 {difference:.1f} 秒，可能为剪辑/现场/不同版本；不能靠压缩时间轴解决"
        )
        audit["requires_version_review"] = True
    content = clean_lyric_text(
        record.get("plainLyrics") or record.get("syncedLyrics") or ""
    )
    recommendation, reasons = "review", []
    if title < 0.85:
        audit["warnings"].append("歌名相似度较低，请先核对是否同一首歌")
        reasons.append("歌名存在差异")
    if artist_match is None:
        reasons.append("未填写歌手，不能区分原唱与翻唱")
    elif artist_match < 0.7:
        audit["warnings"].append("歌手名称不同；可能为别名或翻唱，请核对实际演唱版本")
        reasons.append("歌手名称需核对")
    if difference is None:
        reasons.append("来源没有录音时长")
    elif difference > tolerance:
        reasons.append("录音时长与 MV 有差异")
    body = re.sub(r"\[[^\]\n]+\]", "", content)
    languages = [language_of(body)] if canonical(body) else []
    if re.search(r"[\u3040-\u30ff]", body) and re.search(
        r"^[A-Za-z][A-Za-z '-]{25,}$", body, re.M
    ):
        audit["warnings"].append(
            "歌词包含较长拉丁字母行，请检查是否混有罗马音或译文；原文未自动删改"
        )
    translated = bool(
        re.search(
            r"中文(?:版|填词|翻唱)|English (?:ver|cover)|翻译|中文翻譯",
            record["trackName"],
            re.I,
        )
    )
    if translated:
        audit["warnings"].append(
            "标题提示翻译或改编版本，请确认演唱语言，勿把译文作为原文对齐"
        )
        reasons.append("可能为翻译或改编版")
    if not reasons and not audit["requires_version_review"]:
        recommendation = "recommended"
        reasons.append("歌名、歌手名称及录音时长相符；试听确认后采用")
    elif (
        title >= 0.85
        and artist_match is None
        and not audit["requires_version_review"]
        and not translated
    ):
        recommendation = "compare"
    if audit["requires_version_review"]:
        reasons.append("需要核对完整版、剪辑或现场版本")
    observed = [(title, 0.55), (artist_match, 0.30), (duration_match, 0.15)]
    score = sum(v * w for v, w in observed if v is not None) / sum(
        w for v, w in observed if v is not None
    )
    if audit["requires_version_review"]:
        score = min(score, 0.65)
    return {
        "score": round(score, 4),
        "score_kind": "retrieval_ranking_not_probability",
        "recommendation": recommendation,
        "recommendation_reasons": reasons,
        "content_languages": languages,
        **audit,
        "evidence": {
            "title_similarity": round(title, 4),
            "artist_similarity": round(artist_match, 4)
            if artist_match is not None
            else None,
            "duration_difference_seconds": round(difference, 2)
            if difference is not None
            else None,
            "duration_tolerance_seconds": round(tolerance, 2),
            "duration_consistent": difference is not None and difference <= tolerance,
            "recording_verified": False,
            "name_comparison": "Chinese script and conservative Japanese character variants, followed by canonical comparison",
            "note": "名称为繁简统一后的文本相似度，不证明歌手别名等价；时长只检验版本一致性。排序分不是匹配成功概率，尚未比较音频演唱内容。",
        },
    }


def search(song, artist, duration, settings):
    if not song.strip():
        raise ValueError("请填写歌曲名")
    params = {"track_name": song}
    if artist.strip():
        params["artist_name"] = artist
    retrieval_error, candidates, attempts = None, [], []
    with httpx.Client(
        timeout=httpx.Timeout(10, connect=4),
        follow_redirects=True,
        headers={"User-Agent": "LocalKTV/1.0 (local lyrics alignment)"},
    ) as client:

        def retrieve(query, reason):
            nonlocal retrieval_error
            attempt = {
                "provider": "LRCLIB",
                "query": dict(query),
                "reason": reason,
                "http_status": None,
                "records": 0,
                "candidates": 0,
            }
            attempts.append(attempt)
            try:
                response = client.get(
                    settings.lyrics_url.rstrip("/") + "/search", params=query
                )
                attempt["http_status"] = response.status_code
                response.raise_for_status()
                records = response.json()
                if not isinstance(records, list):
                    raise ValueError("invalid lyrics response")
                attempt["records"] = len(records)
                result = records_to_candidates(
                    [
                        dict(r, provider="LRCLIB", source=None)
                        if isinstance(r, dict)
                        else r
                        for r in records
                    ],
                    song,
                    artist,
                    duration,
                )
                attempt["candidates"] = len(result)
                attempt["status"] = "success"
                return result
            except (httpx.HTTPError, ValueError):
                attempt["status"] = "failed"
                retrieval_error = (
                    "LRCLIB 检索失败或返回格式异常；可以重试或导入有来源的 TXT/LRC"
                )
                return []

        candidates = retrieve(
            params, "original_song_and_artist" if artist.strip() else "original_song"
        )
        # Artist spelling/aliases may differ. Drop the API filter, but retain the
        # user's original artist and song for every candidate's ranking.
        if (
            not suitable_candidates(candidates)
            and not retrieval_error
            and artist.strip()
        ):
            candidates.extend(
                retrieve({"track_name": song}, "original_song_without_artist_filter")
            )
        if not suitable_candidates(candidates) and not retrieval_error:
            for variant in title_variants(song):
                candidates.extend(
                    retrieve(
                        {"track_name": variant},
                        "title_script_variant_without_artist_filter",
                    )
                )
                if suitable_candidates(candidates) or retrieval_error:
                    break
    if not suitable_candidates(candidates):
        records, extra_attempts = lookup_netease(song, artist, duration)
        attempts.extend(extra_attempts)
        candidates.extend(records_to_candidates(records, song, artist, duration))
    if not suitable_candidates(candidates):
        records, extra_attempts = lookup_sites(song, artist, search_web)
        attempts.extend(extra_attempts)
        candidates.extend(records_to_candidates(records, song, artist, duration))
    # Preserve independent recordings; only identical source IDs are collapsed.
    candidates = list({c["id"]: c for c in candidates}.values())
    candidates.sort(
        key=lambda x: (
            {"recommended": 2, "compare": 1, "review": 0}[x["recommendation"]],
            x["score"],
        ),
        reverse=True,
    )
    notes = {}
    if not candidates:
        notes["web"] = lyric_web_sources(song, artist)
    if retrieval_error:
        notes["retrieval_warning"] = (
            "LRCLIB 本次不可用，已继续查询独立歌词来源。"
            if candidates
            else "LRCLIB 等歌词来源请求失败；这不代表歌曲没有歌词。可查看检索记录后重试或导入 TXT/LRC。"
        )
    elif (
        any("artist_name" not in attempt["query"] for attempt in attempts)
        and artist.strip()
    ):
        notes["retrieval_warning"] = (
            "已放宽歌手过滤并尝试歌名的繁简/日文字形。排序仍与原歌名、原歌手和 MV 时长比较；歌手别名与录音版本需你确认。"
        )
    reliable = bool(
        candidates
        and not candidates[0]["requires_version_review"]
        and artist
        and candidates[0]["recommendation"] == "recommended"
        and candidates[0]["score"] >= 0.94
        and (
            len(candidates) == 1
            or candidates[0]["score"] - candidates[1]["score"] > 0.08
        )
    )
    return {
        "query": params,
        "actual_query_attempts": attempts,
        "retrieval_policy": "lrclib_script_variants_then_netease_then_fixed_lyric_sites",
        "retrieval_status": "results"
        if candidates
        else (
            "unavailable"
            if any(a["status"] == "failed" for a in attempts)
            else "no_results"
        ),
        "candidates": candidates,
        "verification": notes,
        "auto_id": candidates[0]["id"] if reliable else None,
    }


def suitable_candidates(candidates):
    return any(
        c["recommendation"] == "recommended"
        or (c["recommendation"] == "compare" and c["evidence"]["duration_consistent"])
        for c in candidates
    )


def records_to_candidates(records, song, artist, duration):
    candidates, seen = [], set()
    for record in records:
        if (
            not isinstance(record, dict)
            or not all(
                isinstance(record.get(k), str) for k in ("trackName", "artistName")
            )
            or record.get("id") is None
            or str(record["id"]) in seen
        ):
            continue
        content = record.get("plainLyrics") or record.get("syncedLyrics")
        if (
            not isinstance(content, str)
            or not content.strip()
            or record.get("instrumental")
        ):
            continue
        content = clean_lyric_text(content)
        if not canonical(content):
            continue
        try:
            evidence = candidate_evidence(song, artist, duration, record)
        except (ValueError, TypeError, OverflowError):
            continue
        seen.add(str(record["id"]))
        candidates.append(
            {
                "id": str(record["id"]),
                "song": record["trackName"],
                "artist": record["artistName"],
                "album": record.get("albumName"),
                "duration": record.get("duration"),
                **evidence,
                "source": record.get("source")
                or f"https://lrclib.net/api/get/{record['id']}",
                "text": content,
                "provider": record.get("provider", "LRCLIB"),
            }
        )
    return candidates


def lyric_web_sources(song, artist):
    query = f"{song} {artist} 完整歌词 lyrics".strip()[:220]
    sources, warnings = [], []
    try:
        for item in search_web(query)[:6]:
            url = item.get("href") or item.get("url") or ""
            parsed = urlparse(url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                continue
            sources.append(
                {
                    "id": f"web-{len(sources) + 1}",
                    "url": url,
                    "title": str(item.get("title", ""))[:300],
                    "snippet": str(item.get("body", ""))[:1500],
                    "excerpt": None,
                    "read_status": "snippet_only",
                }
            )
    except Exception:
        warnings.append("网页检索不可用，请重试或导入 TXT/LRC")
    if sources:
        warnings.append(
            "网页摘要仅供核对版本，不会自动当作完整歌词；可查看来源后导入实际歌词"
        )
    return {
        "provider": "DDGS web search (no AI)",
        "searched_at": datetime.now(timezone.utc).isoformat(),
        "queries": [query],
        "sources": sources,
        "warnings": warnings,
        "status": "results" if sources else "unavailable",
    }
