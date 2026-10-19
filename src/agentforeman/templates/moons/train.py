"""Fixed training entry point. Outputs are real model weights, not claimed scores."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
from model import train

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent
config = json.loads((root / "config.json").read_text())
data = np.loadtxt(root / "train.csv", delimiter=",", skiprows=1)
start = time.time()
weights = train(data[:, :2], data[:, 2], config)
args.output.mkdir(parents=True, exist_ok=True)
np.savez(args.output / "model.npz", **weights)
(args.output / "training.json").write_text(json.dumps({
    "config": config, "training_rows": len(data), "duration_seconds": time.time() - start,
    "numpy_version": np.__version__, "device": "cpu",
}, indent=2))
print(f"Saved checkpoint: {args.output / 'model.npz'}", flush=True)
