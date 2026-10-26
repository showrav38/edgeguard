"""Which signals pushed the risk up? Occlusion explanation.

Method: for each signal, replace it by its training median (a "typical" value), keep the other
signals, and recompute the risk. effect = risk(actual) - risk(signal set to median).
  effect > 0  -> this signal's actual value pushed the risk UP
  effect < 0  -> it pulled the risk down
All 6 variants go through the model in ONE batched call (~0.1 ms).

Limits (documented in docs/decisions.md):
  - one signal at a time: interactions are missed (e.g. HDF needs low temp difference AND low
    speed; changing either one alone may remove the whole risk, so both look important, or with
    redundancy neither does);
  - the median is "typical", not "healthy";
  - the effects do not add up to the total risk (unlike SHAP).
"""
import numpy as np


def top_factors(models, x: np.ndarray, risk: float, k: int = 2) -> list[dict]:
    n = x.shape[1]
    batch = np.repeat(x, n, axis=0)                     # n copies of the reading
    batch[np.arange(n), np.arange(n)] = models.medians  # copy i: signal i set to its median
    effects = risk - models.risk_batch(batch)
    order = np.argsort(-effects)[:k]                    # largest upward push first
    return [{"signal": models.features[i], "effect": round(float(effects[i]), 3)} for i in order]
