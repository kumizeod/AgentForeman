# Experiment contract

This example trains an actual two-layer ReLU MLP on a synthetic two-moons CSV
dataset. Training runs on CPU with NumPy so the control plane can be tested offline.
It is a pipeline example, not a GPU benchmark or a claim of novel model discovery.

Editable: `model.py`, `config.json`. Keep `train(x, y, config) -> dict` compatible.
Training data: `train.csv` with columns `x1,x2,label` and labels 0 or 1.
The manager owns training/evaluation; do not launch a full training job yourself.

Checkpoint arrays must describe exactly:

`logits = maximum(x @ w1 + b1, 0) @ w2 + b2`

Shapes: `w1=(2,H)`, `b1=(H,)`, `w2=(H,1)`, `b2=(1,)`, `1 <= H <= 512`.
All arrays must contain finite floats. Preserve these semantics if optimizing the
training implementation. You may change hidden width, initialization, optimizer,
learning rate, loss or epoch count within the configured job timeout.

An external evaluator loads the NPZ with `allow_pickle=False`, checks its tensors,
and computes validation accuracy itself. It does not import this candidate code.
The validation data, evaluator and metric policy are outside the editable workspace.
They are validation data, not a final held-out research test set.
