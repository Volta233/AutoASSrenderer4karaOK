"""Run only inside the selected TIFA environment. Uses upstream inference API."""

import json
import pathlib
import sys
import time


def main():
    root_request = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    requests = (
        [
            json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
            for p in root_request["batch_requests"]
        ]
        if "batch_requests" in root_request
        else [root_request]
    )
    request = requests[0]
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    sys.path.insert(0, request["source"])
    import torch
    from ktv_app.model_runtime import resolve_device

    device = resolve_device(torch, request.get("device", "cuda"))
    torch.set_num_threads(request.get("cpu_threads", 8))
    torch.set_num_interop_threads(1)
    from inference.api import load_g2p_config, load_inference_model
    from lib.config.schema import ConfigurationScope
    from lib.g2p import build_pipeline_from_config

    print(
        f"TIFA runtime: device={device}, precision=fp32, threads={torch.get_num_threads()}",
        flush=True,
    )
    model_path = pathlib.Path(request["model"])
    backend, vocabulary, config = load_inference_model(
        model_path, scope=ConfigurationScope.FA
    )
    g2p_file = pathlib.Path(request["g2p"]) if request.get("g2p") else None
    g2p_config = load_g2p_config(g2p_file) if g2p_file else config.g2p
    if g2p_config is None:
        raise ValueError("模型缺少 G2P 配置；请设置 TIFA_G2P")
    pipeline = build_pipeline_from_config(g2p_config, root_path=model_path.parent)
    started = time.monotonic()
    if root_request.get("stream"):
        # Sequential requests share one loaded model. Completion is signalled
        # through a file, leaving upstream progress output in the inference log.
        for message in sys.stdin:
            command = json.loads(message)
            if command.get("close"):
                break
            request = json.loads(
                pathlib.Path(command["request"]).read_text(encoding="utf-8")
            )
            status = 0
            try:
                process(request, backend, vocabulary, g2p_config, pipeline, model_path)
            except Exception as error:
                status = 1
                pathlib.Path(request["work"], "error.json").write_text(
                    json.dumps(
                        {"type": type(error).__name__, "message": str(error)},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            done = pathlib.Path(request["work"], "stream_done.json")
            temporary = done.with_suffix(".tmp")
            temporary.write_text(json.dumps({"returncode": status}), encoding="utf-8")
            temporary.replace(done)
        return
    for index, request in enumerate(requests):
        try:
            process(request, backend, vocabulary, g2p_config, pipeline, model_path)
        except Exception as error:
            pathlib.Path(request["work"], "error.json").write_text(
                json.dumps(
                    {"type": type(error).__name__, "message": str(error)},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            if len(requests) == 1:
                raise
        pathlib.Path(root_request["work"], "progress.json").write_text(
            json.dumps(
                {
                    "completed": index + 1,
                    "total": len(requests),
                    "elapsed": round(time.monotonic() - started, 3),
                }
            ),
            encoding="utf-8",
        )


def process(request, backend, vocabulary, g2p_config, pipeline, model_path):
    import torch
    from ktv_app.model_runtime import resolve_device
    from ktv_app.text_normalization import normalize_lyrics
    from ktv_app.aligners.partial_g2p import convert_parts
    from g2pflow import to_pfml
    from g2pflow.converters.base import G2PConversionError
    from inference.data import AudioTextDataset
    from inference.callbacks import SaveTextGridCallback, StatisticsCallback
    from lib.g2p_encoding import encode_paths, G2PEncodingError

    device = resolve_device(torch, request.get("device", "cuda"))
    words, trace = [], []
    languages = []
    for index, line in enumerate(request["lines"]):
        line["normalized"], line["normalization_changes"] = normalize_lyrics(
            line["text"]
        )
        active = [line["language"]]
        if (
            any(c.isascii() and c.isalpha() for c in line["text"])
            and "en" not in active
        ):
            active.append("en")
        languages.extend(active)
        # Full line context is critical for Japanese kanji readings and Chinese polyphones.
        parts = convert_parts(
            line["normalized"] or line["text"],
            lambda text: pipeline.convert(text, languages=active),
            G2PConversionError,
        )
        if not parts:
            trace.append(
                {
                    "line_id": line["id"],
                    "text": line["normalized"],
                    "omitted": True,
                    "reason": "G2P 没有返回可用发音",
                }
            )
        for word, omitted, reason in parts:
            if word is None:
                trace.append(
                    {
                        "line_id": line["id"],
                        "text": omitted,
                        "omitted": True,
                        "reason": reason,
                    }
                )
                continue
            item = {
                "line_id": line["id"],
                "text": word.text,
                "language": str(word.language),
                "readings": [
                    [
                        [{"script": g.script, "phones": g.phonemes} for g in path]
                        for path in reading.paths
                    ]
                    for reading in word.readings
                ],
            }
            try:
                encode_paths([word], vocabulary, "raise", languages=active)
                words.append(word)
            except G2PEncodingError as error:
                item.update(omitted=True, reason=str(error))
            trace.append(item)
    folder = pathlib.Path(request["work"])
    audio = pathlib.Path(request["audio"])
    audio.with_suffix(".pfml").write_text(to_pfml(words), encoding="utf-8")
    (folder / "g2p_trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (folder / "runtime.json").write_text(
        json.dumps(
            {
                "torch": torch.__version__,
                "cuda": True,
                "device": device,
                "device_name": torch.cuda.get_device_name(0),
                "precision": "fp32",
                "cpu_threads": torch.get_num_threads(),
                "source": request["source"],
                "model": request["model"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if not words:
        import soundfile as sf
        import textgrid

        seconds = sf.info(audio).duration
        grid = textgrid.TextGrid(maxTime=seconds)
        grid.append(textgrid.IntervalTier(name="texts", maxTime=seconds))
        grid.write(str(audio.with_suffix(".TextGrid")))
        return
    dataset = AudioTextDataset(
        {audio.stem: audio},
        g2p_config,
        model_path.parent,
        vocabulary,
        backend.sample_rate,
        language=list(dict.fromkeys(languages)),
        oov_handling="raise",
    )
    grid_callback = SaveTextGridCallback(
        folder, language=None, timestep=backend.timestep, skip_handling="omit"
    )
    if request.get("native_word_indices"):
        from ktv_app.aligners.native_word_indices import (
            retained_word_indices,
            complete_word_indices,
        )

        class IndexedGridCallback(SaveTextGridCallback):
            def on_predict_batch_end(
                self, trainer, pl_module, outputs, batch, *args, **kwargs
            ):
                super().on_predict_batch_end(
                    trainer, pl_module, outputs, batch, *args, **kwargs
                )
                for result in outputs:
                    ids = retained_word_indices(
                        result["words"].tolist(),
                        result["spans"].tolist(),
                        self.timestep,
                    )
                    (folder / "native_word_indices.json").write_text(
                        json.dumps(
                            {
                                "word_ids": ids,
                                "texts": [result["texts"][index - 1] for index in ids],
                                "complete_word_ids": complete_word_indices(
                                    result["words"].tolist(),
                                    result["spans"].tolist(),
                                    self.timestep,
                                ),
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )

        grid_callback = IndexedGridCallback(
            folder, language=None, timestep=backend.timestep, skip_handling="omit"
        )
    run_inference_on_device(
        backend,
        dataset,
        [grid_callback, StatisticsCallback(folder / "statistics")],
        device=device,
    )
    if not (folder / (audio.stem + ".TextGrid")).exists():
        raise RuntimeError(
            "TIFA 未产生 TextGrid，可能歌词/G2P 被拒绝；请查看 infer.log"
        )


def run_inference_on_device(backend, dataset, callbacks, device):
    """Upstream TIFA predictor/decoder; explicitly select one runtime device."""
    from ktv_app.model_runtime import require_cuda

    require_cuda(device)
    import lightning.pytorch as pl
    import torch
    from inference.module import ForcedAlignmentInferenceModule

    module = ForcedAlignmentInferenceModule(backend)
    trainer = pl.Trainer(
        accelerator="gpu",
        devices=1,
        precision="32-true",
        logger=False,
        enable_checkpointing=False,
        callbacks=callbacks,
    )
    dataloader = torch.utils.data.DataLoader(
        dataset,
        batch_size=1,
        num_workers=0,
        shuffle=False,
        collate_fn=dataset.collate if hasattr(dataset, "collate") else None,
    )
    trainer.predict(module, dataloader)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        try:
            request = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
            (pathlib.Path(request["work"]) / "error.json").write_text(
                json.dumps(
                    {"type": type(error).__name__, "message": str(error)},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
        raise
