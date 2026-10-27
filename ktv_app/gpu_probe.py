"""Standalone CUDA startup probe; emits progress before importing large DLLs."""

import json


def report(**record):
    print("LYRIFRAME_GPU=" + json.dumps(record), flush=True)


def main():
    report(stage="import")
    import torch

    report(stage="cuda")
    available = torch.cuda.is_available()
    if not available:
        report(cuda=False, bf16=False)
        return
    # Verify a real operation, not merely driver enumeration. Avoid BF16
    # emulation on older GPUs when choosing the model's mixed precision.
    data = torch.ones((2, 2), device="cuda")
    valid = bool(torch.isfinite(data @ data).all().item())
    torch.cuda.synchronize()
    report(cuda=valid, bf16=torch.cuda.is_bf16_supported(including_emulation=False))


if __name__ == "__main__":
    main()
