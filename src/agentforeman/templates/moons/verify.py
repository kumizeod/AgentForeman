"""Trusted external evaluator. NEVER imports model.py or trusts training metrics."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--checkpoint", type=Path, required=True)
parser.add_argument("--data", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
result = {"valid": False, "metric_name": "accuracy", "metric": None}
try:
    if args.checkpoint.is_symlink() or args.checkpoint.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Checkpoint must be a regular NPZ file <=16 MiB")
    with np.load(args.checkpoint, allow_pickle=False) as file:
        if set(file.files) != {"w1", "b1", "w2", "b2"}:
            raise ValueError("Checkpoint keys do not match the contract")
        weights = {key: file[key] for key in file.files}
    w1, b1, w2, b2 = (weights[k] for k in ("w1", "b1", "w2", "b2"))
    if w1.ndim != 2 or w1.shape[0] != 2 or not 1 <= w1.shape[1] <= 512:
        raise ValueError("Unsupported input/hidden dimensions")
    h = w1.shape[1]
    if b1.shape != (h,) or w2.shape != (h, 1) or b2.shape != (1,):
        raise ValueError("Inconsistent tensor dimensions")
    if not all(a.dtype.kind == "f" and np.isfinite(a).all() for a in weights.values()):
        raise ValueError("Weights must be finite floating point arrays")
    data = np.loadtxt(args.data, delimiter=",", skiprows=1)
    logits = np.maximum(data[:, :2] @ w1 + b1, 0) @ w2 + b2
    if not np.isfinite(logits).all():
        raise ValueError("Non-finite predictions")
    pred = (logits.ravel() >= 0).astype(int)
    result.update(valid=True, metric=float(np.mean(pred == data[:, 2])), validation_rows=len(data),
                  checkpoint_sha256=hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
                  data_sha256=hashlib.sha256(args.data.read_bytes()).hexdigest(),
                  parameters=sum(a.size for a in weights.values()))
except Exception as exc:
    result["error"] = f"{type(exc).__name__}: {exc}"
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, indent=2, allow_nan=False))
print(json.dumps(result))
sys.exit(0 if result["valid"] else 1)
