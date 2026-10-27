"""Recognize short windows without loading any Qwen forced aligner."""

import json
from pathlib import Path
import sys
import time


def main():
    req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from ktv_app.model_runtime import resolve_device

    import numpy as np
    import soundfile as sf
    import torch
    from ktv_app.native_paths import prepare_nagisa

    prepare_nagisa()
    from qwen_asr import Qwen3ASRModel

    started = time.monotonic()
    device = resolve_device(torch, req.get("device", "cuda"))
    precision = req.get("precision", "bf16")
    torch.set_num_threads(req.get("cpu_threads", 8))
    torch.set_num_interop_threads(1)
    runtime = dict(
        device=device,
        precision=precision,
        torch=torch.__version__,
        cpu_threads=torch.get_num_threads(),
        model="Qwen3-ASR-1.7B",
    )
    Path(req["work"], "runtime.json").write_text(
        json.dumps(runtime, indent=2), encoding="utf-8"
    )
    torch.cuda.reset_peak_memory_stats()
    model = Qwen3ASRModel.from_pretrained(
        req["asr_model"],
        dtype={"fp32": torch.float32, "bf16": torch.bfloat16, "fp16": torch.float16}[
            precision
        ],
        device_map="cuda:0",
        attn_implementation="sdpa",
        local_files_only=True,
        max_inference_batch_size=1,
        max_new_tokens=512,
    )
    audio, sr = sf.read(req["audio"], dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    duration = len(audio) / sr
    language = {"zh": "Chinese", "ja": "Japanese", "en": "English"}.get(
        req.get("language")
    )
    chunks = []
    size, overlap = req.get("chunk_seconds", 12), req.get("overlap_seconds", 2)
    shifts = [0, -size / 2] if req.get("verification_pass") else [0]
    for pass_index, shift in enumerate(shifts):
        for offset in np.arange(shift, duration, size):
            left, right = (
                max(0, offset - overlap),
                min(duration, offset + size + overlap),
            )
            if right <= left:
                continue
            result = model.transcribe(
                audio=(audio[round(left * sr) : round(right * sr)], sr),
                context="",
                language=language,
                return_time_stamps=False,
            )[0]
            chunks.append(
                dict(
                    start=float(left),
                    end=float(right),
                    text=result.text,
                    language=result.language,
                    pass_index=pass_index,
                    core_start=float(max(0, offset)),
                    core_end=float(min(duration, offset + size)),
                )
            )
            Path(req["work"], "progress.json").write_text(
                json.dumps(
                    dict(
                        seconds_processed=min(duration, float(offset) + size),
                        duration=duration,
                        chunks=len(chunks),
                        phase="asr_text",
                        pass_index=pass_index + 1,
                        passes=len(shifts),
                        **runtime,
                    )
                ),
                encoding="utf-8",
            )
    output = dict(
        chunks=chunks,
        chunk_seconds=size,
        overlap_seconds=overlap,
        duration=duration,
        asr_elapsed=round(time.monotonic() - started, 3),
        peak_gpu_gib=round(torch.cuda.max_memory_allocated() / 1024**3, 3),
        model="Qwen3-ASR-1.7B",
        runtime=runtime,
        timestamp_model="TIFA-1.0-ST",
        note="ASR只输出独立识别文本；进程退出后由TIFA定位，无Qwen FA。",
    )
    Path(req["work"], "recognition.asr.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        Path(req["work"], "error.json").write_text(
            json.dumps(dict(message=str(error)), ensure_ascii=False), encoding="utf-8"
        )
        raise
