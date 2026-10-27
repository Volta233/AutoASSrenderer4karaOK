import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from ..timeline import Timeline, Unit, Phone, canonical, quality
from ..text_normalization import normalize_lyrics, alignment_display_map
from ..model_runtime import worker_environment


def display_map(text):
    return alignment_display_map(text)


def parse_result(lines, grid_path, trace_path, duration, origin, diagnostics=None):
    import textgrid

    grid = textgrid.TextGrid.fromFile(str(grid_path))
    tiers = {
        t.name: [
            {"text": i.mark, "start": float(i.minTime), "end": float(i.maxTime)}
            for i in t
            if i.mark.strip()
        ]
        for t in grid
    }
    native = tiers.get("texts", [])
    trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))
    indices_path = Path(trace_path).with_name("native_word_indices.json")
    native_indices = (
        json.loads(indices_path.read_text(encoding="utf-8"))
        if indices_path.exists()
        else None
    )
    owners = native_indices["word_ids"] if native_indices else None
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics["incomplete_native_word_ids"] = []
    if owners is not None and (
        len(owners) != len(native) or owners != sorted(set(owners))
    ):
        raise ValueError("原生词编号与TextGrid不一致；保留原始结果，不按重复文本猜测")
    # Sequential matching preserves repeated choruses. Missing words remain explicitly unaligned.
    cursor = 0
    encoded_index = 0
    offsets = {l.id: 0 for l in lines}
    by_id = {l.id: l for l in lines}
    for l in lines:
        l.units = []
    for wi, word in enumerate(trace):
        line = by_id[word["line_id"]]
        flat, positions = display_map(line.text)
        token = canonical(word["text"])
        if not token:
            continue
        pos = flat.find(token, offsets[line.id])
        if pos < 0:
            raise ValueError(
                f"无法精确映射 G2P 文本到原歌词: {word['text']} / {line.text}。请修改歌词或发音配置。"
            )
        a, b = positions[pos], positions[pos + len(token) - 1] + 1
        offsets[line.id] = pos + len(token)
        interval = None
        if not word.get("omitted"):
            encoded_index += 1
            if cursor < len(native) and (
                owners is None or owners[cursor] == encoded_index
            ):
                if canonical(native[cursor]["text"]) == token:
                    interval = native[cursor]
                    cursor += 1
                elif owners is not None:
                    raise ValueError("原生词编号对应文本不一致；拒绝近似映射")
        unit = Unit(
            id=f"{line.id}-tifa-s{a}-{b}",
            text=line.text[a:b],
            normalized=word["text"],
            source_start=a,
            source_end=b,
        )
        if (
            native_indices
            and interval
            and encoded_index not in native_indices["complete_word_ids"]
        ):
            diagnostics["incomplete_native_word_ids"].append(unit.id)
        if interval:
            start, end = interval["start"] + origin, interval["end"] + origin
            if 0 <= start < end <= duration + 0.02:
                unit.start, unit.end = round(start, 4), round(min(end, duration), 4)
                for name, target in (("words", unit.readings), ("phones", unit.phones)):
                    for part in tiers.get(name, []):
                        if (
                            part["start"] >= interval["start"] - 0.002
                            and part["end"] <= interval["end"] + 0.002
                        ):
                            target.append(
                                Phone(
                                    text=part["text"],
                                    start=round(part["start"] + origin, 4),
                                    end=round(part["end"] + origin, 4),
                                )
                            )
            else:
                unit.flags.append("unmatched_text")
        else:
            unit.flags.append("unmatched_text")
        line.units.append(unit)
    if cursor != len(native):
        raise ValueError(
            "TextGrid 文本序列与 G2P 追踪不一致；拒绝近似分配时间，请查看原始结果"
        )
    for line in lines:
        flat, positions = display_map(line.text)
        owned = {i for u in line.units for i in range(u.source_start, u.source_end)}
        for index in sorted(set(positions) - owned):
            line.units.append(
                Unit(
                    id=f"{line.id}-missing-{index}",
                    text=line.text[index],
                    normalized=line.text[index],
                    source_start=index,
                    source_end=index + 1,
                    flags=["unmatched_text"],
                )
            )
        line.units.sort(key=lambda u: u.source_start)
    return quality(
        Timeline(
            duration=duration, origin=origin, lines=lines, diagnostics=diagnostics or {}
        )
    )


class TIFAAdapter:
    def __init__(self, settings):
        self.settings = settings

    def session(self, work):
        return TIFASession(self, work)

    def prepare(self, audio, lines, work):
        s = self.settings
        # Recompute from display text for cached projects and browser edits too.
        for line in lines:
            line.normalized, line.normalization_changes = normalize_lyrics(line.text)
        for p, label in (
            (s.tifa_python, "TIFA Python 环境"),
            (s.tifa_model, "TIFA 权重"),
            (s.tifa_source / "infer.py", "TIFA 源码"),
        ):
            if not p.is_file():
                raise FileNotFoundError(f"缺少 {label}: {p}")
        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        # A retry must wait for its own worker output, never previous completion files.
        for name in (
            "error.json",
            "stream_done.json",
            "input.TextGrid",
            "native_word_indices.json",
        ):
            (work / name).unlink(missing_ok=True)
        target = work / "input.wav"
        shutil.copyfile(audio, target)
        # Relocate the repository's known local G2P resource paths without editing research files.
        g2p = s.g2p
        if not g2p and (s.tifa_model.parent / "g2p.local.yaml").exists():
            import yaml

            config = yaml.safe_load(
                (s.tifa_model.parent / "g2p.local.yaml").read_text(encoding="utf-8")
            )
            for converter in config.get("converters", []):
                for key, value in converter.get("kwargs", {}).items():
                    if key.endswith("_path") or key.endswith("_dir"):
                        normalized = str(value).replace("\\", "/")
                        marker = "/models/forced_alignment/"
                        if marker in normalized:
                            relative = normalized.split(marker, 1)[1]
                            base = s.tifa_model.parents[2]
                            converter["kwargs"][key] = (base / relative).as_posix()
            local = work / "g2p.resolved.yaml"
            local.write_text(
                yaml.safe_dump(config, allow_unicode=True), encoding="utf-8"
            )
            g2p = str(local)
        req = {
            "source": str(s.tifa_source),
            "model": str(s.tifa_model),
            "g2p": g2p,
            "audio": str(target),
            "work": str(work),
            "device": s.device,
            "cpu_threads": getattr(s, "cpu_threads", 8),
            "lines": [l.model_dump() for l in lines],
        }
        request = work / "request.json"
        request.write_text(
            json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        env = worker_environment(s.device)
        env["MPLBACKEND"] = "Agg"
        return request, env

    def align(self, audio, lines, duration, origin, work):
        request, env = self.prepare(audio, lines, work)
        work = Path(work)
        with (work / "infer.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(
                [
                    str(self.settings.tifa_python),
                    str(Path(__file__).with_name("worker.py")),
                    str(request),
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=7200,
            )
        return self.collect(lines, duration, origin, work, result.returncode)

    def align_many(self, jobs, duration, work):
        """Reuse model and G2P inside one subprocess, processing windows serially."""
        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        requests = []
        for job in jobs:
            request, env = self.prepare(
                job["audio"], job["lines"], job["folder"] / "tifa"
            )
            requests.append(str(request))
        if not requests:
            return {}
        root_request = work / "request.json"
        root_request.write_text(
            json.dumps(
                {"work": str(work), "batch_requests": requests},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        with (work / "infer.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(
                [
                    str(self.settings.tifa_python),
                    str(Path(__file__).with_name("worker.py")),
                    str(root_request),
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=7200,
            )
        timelines = {}
        for job in jobs:
            try:
                timelines[job["id"]] = self.collect(
                    job["lines"],
                    duration,
                    job["origin"],
                    job["folder"] / "tifa",
                    result.returncode,
                    work / "infer.log",
                )
            except (RuntimeError, ValueError, OSError) as error:
                for line in job["lines"]:
                    line.units = []
                    line.start = line.end = None
                timelines[job["id"]] = quality(
                    Timeline(
                        duration=duration,
                        origin=job["origin"],
                        lines=job["lines"],
                        diagnostics={"error": str(error)[:1000]},
                    )
                )
        return timelines

    def collect(self, lines, duration, origin, work, returncode=0, error_log=None):
        work = Path(work)
        if (
            returncode
            or (work / "error.json").exists()
            or not (work / "input.TextGrid").exists()
        ):
            if (work / "error.json").exists():
                error = json.loads((work / "error.json").read_text(encoding="utf-8"))
                raise RuntimeError("TIFA 推理失败：" + error["message"])
            log_path = work / "infer.log"
            tail = (log_path if log_path.exists() else error_log).read_text(
                encoding="utf-8"
            )[-3500:]
            tail = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", tail)
            if "Traceback (most recent call last):" in tail:
                tail = tail[tail.rfind("Traceback (most recent call last):") :]
            raise RuntimeError("TIFA 推理失败。" + tail)
        diagnosis = work / "statistics/diagnosis.json"
        diagnostics = {
            "runtime": json.loads((work / "runtime.json").read_text(encoding="utf-8"))
            if (work / "runtime.json").exists()
            else {},
            "native": json.loads(diagnosis.read_text(encoding="utf-8"))
            if diagnosis.exists()
            else [],
            "source_word_mapping": "native_semantic_word_ids"
            if (work / "native_word_indices.json").exists()
            else "ordered_text_matching",
            "g2p_omitted": [
                w
                for w in json.loads(
                    (work / "g2p_trace.json").read_text(encoding="utf-8")
                )
                if w.get("omitted")
            ],
            "normalization_changes": [
                {"line_id": l.id, "changes": l.normalization_changes}
                for l in lines
                if l.normalization_changes
            ],
            "mapping": "native semantic text spans; no character-count interpolation",
            "review": "强制对齐不能验证漏唱或版本正确性；请试听标记片段。日语以语义词对应多个原生读音组。",
        }
        timeline = parse_result(
            lines,
            work / "input.TextGrid",
            work / "g2p_trace.json",
            duration,
            origin,
            diagnostics,
        )
        records = diagnostics["native"]
        for record in records:
            metrics = record.get("metrics", record)
            if any(
                isinstance(metrics.get(k), (int, float)) and metrics[k] < threshold
                for k, threshold in [
                    ("agreement", 0.65),
                    ("confidence", 0.5),
                    ("determinacy", 0.6),
                    ("monotonicity", 0.75),
                ]
            ):
                for line in timeline.lines:
                    line.flags.append("low_model_support")
            if metrics.get("num_skipped_tokens", 0):
                for line in timeline.lines:
                    if "low_model_support" not in line.flags:
                        line.flags.append("low_model_support")
        return timeline


class TIFASession:
    """A private sequential worker for dependent adaptive-window inferences."""

    def __init__(self, adapter, work):
        self.adapter = adapter
        self.work = Path(work)
        self.process = None
        self.log = None

    def __enter__(self):
        self.work.mkdir(parents=True, exist_ok=True)
        return self

    def align(self, audio, lines, duration, origin, work):
        request, env = self.adapter.prepare(audio, lines, work)
        content = json.loads(request.read_text(encoding="utf-8"))
        content["native_word_indices"] = True
        request.write_text(
            json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        work = Path(work)
        if self.process is None:
            root = json.loads(request.read_text(encoding="utf-8"))
            root.update(stream=True, work=str(self.work))
            root_file = self.work / "request.json"
            root_file.write_text(json.dumps(root, ensure_ascii=False), encoding="utf-8")
            self.log = (self.work / "infer.log").open("w", encoding="utf-8")
            self.process = subprocess.Popen(
                [
                    str(self.adapter.settings.tifa_python),
                    str(Path(__file__).with_name("worker.py")),
                    str(root_file),
                ],
                env=env,
                stdin=subprocess.PIPE,
                stdout=self.log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
            )
        if self.process.poll() is not None:
            raise RuntimeError("TIFA 递进推理进程已退出；详见 stream/infer.log")
        self.process.stdin.write(json.dumps({"request": str(request.resolve())}) + "\n")
        self.process.stdin.flush()
        done = work / "stream_done.json"
        deadline = time.monotonic() + 3600
        while not done.exists():
            if self.process.poll() is not None:
                raise RuntimeError("TIFA 递进推理中断；详见 stream/infer.log")
            if time.monotonic() > deadline:
                self.process.kill()
                raise RuntimeError("TIFA 递进推理超时")
            time.sleep(0.1)
        status = json.loads(done.read_text(encoding="utf-8"))["returncode"]
        return self.adapter.collect(
            lines, duration, origin, work, status, self.work / "infer.log"
        )

    def __exit__(self, *args):
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    self.process.stdin.write('{"close":true}\n')
                    self.process.stdin.flush()
                    self.process.stdin.close()
                    self.process.wait(timeout=30)
                except (OSError, subprocess.TimeoutExpired):
                    self.process.kill()
                    self.process.wait(timeout=10)
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
        if self.log:
            self.log.close()
