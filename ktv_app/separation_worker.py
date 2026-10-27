"""Run the verified official Kim model in a dedicated CUDA environment."""

import json
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ktv_app.hashing import file_sha256


def main():
    import numpy as np
    import soundfile as sf
    import torch
    import yaml
    from ml_collections import ConfigDict

    req = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not torch.cuda.is_available():
        raise RuntimeError("Kim requires NVIDIA CUDA")
    sys.path.insert(0, req["source"])
    from models.mel_band_roformer import MelBandRoformer
    from utils import demix_track

    random.seed(20261010)
    np.random.seed(20261010)
    torch.manual_seed(20261010)
    config_path = Path(req["source"]) / "configs/config_vocals_mel_band_roformer.yaml"
    # The official config contains !!python/tuple STFT window sizes.
    config = ConfigDict(
        yaml.load(config_path.read_text(encoding="utf-8"), Loader=yaml.FullLoader)
    )
    model = MelBandRoformer(**dict(config.model))
    checkpoint = Path(req["checkpoint"])
    model.load_state_dict(
        torch.load(checkpoint, map_location="cpu", weights_only=True), strict=True
    )
    model = model.eval().cuda()
    data, sr = sf.read(req["mixture"], dtype="float32", always_2d=True)
    if sr != 44100 or data.shape[1] != 2:
        raise ValueError("Kim expects 44100 Hz stereo audio")
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    start = time.monotonic()
    with torch.inference_mode():
        vocals = demix_track(config, model, torch.from_numpy(data.T.copy()), "cuda")[0][
            "vocals"
        ]
    torch.cuda.synchronize()
    if vocals.shape != data.T.shape or not np.isfinite(vocals).all():
        raise ValueError("Kim output shape or samples invalid")
    out = Path(req["output"])
    sf.write(out, vocals.T, sr, subtype="FLOAT")
    info = dict(
        device="cuda",
        precision="fp16",
        parameters=sum(p.numel() for p in model.parameters()),
        frames=len(data),
        sample_rate=sr,
        elapsed=time.monotonic() - start,
        peak_gpu_gib=torch.cuda.max_memory_allocated() / 1024**3,
        checkpoint_sha256=file_sha256(checkpoint),
        chunk_samples=config.inference.chunk_size,
        overlap=config.inference.num_overlap,
        strict_checkpoint_load=True,
        no_silence_trimming=True,
        no_time_stretching=True,
    )
    out.with_suffix(".json").write_text(json.dumps(info, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
