"""Candidate implementation. Preserve the two-layer ReLU checkpoint contract."""
import numpy as np


def train(x, y, config):
    rng = np.random.default_rng(config["seed"])
    h = int(config["hidden"])
    w1 = rng.normal(0, np.sqrt(2 / x.shape[1]), (x.shape[1], h))
    b1 = np.zeros(h)
    w2 = rng.normal(0, np.sqrt(2 / h), (h, 1))
    b2 = np.zeros(1)
    lr = float(config["learning_rate"])
    target = y.reshape(-1, 1)
    for epoch in range(int(config["epochs"])):
        pre = x @ w1 + b1
        hidden = np.maximum(pre, 0)
        logits = hidden @ w2 + b2
        prob = 1 / (1 + np.exp(-np.clip(logits, -40, 40)))
        dlogit = (prob - target) / len(x)
        dw2, db2 = hidden.T @ dlogit, dlogit.sum(axis=0)
        dh = (dlogit @ w2.T) * (pre > 0)
        dw1, db1 = x.T @ dh, dh.sum(axis=0)
        w1 -= lr * dw1
        b1 -= lr * db1
        w2 -= lr * dw2
        b2 -= lr * db2
        if epoch % 25 == 0:
            loss = np.mean(np.logaddexp(0, logits) - target * logits)
            print(f"epoch={epoch} loss={loss:.6f}", flush=True)
    return dict(w1=w1, b1=b1, w2=w2, b2=b2)
