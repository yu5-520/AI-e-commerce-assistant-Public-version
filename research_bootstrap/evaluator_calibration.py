from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, Sequence


class CalibrationError(ValueError):
    pass


def cohen_kappa(labels_a: Sequence[str], labels_b: Sequence[str]) -> float:
    if len(labels_a) != len(labels_b):
        raise CalibrationError("label_length_mismatch")
    if not labels_a:
        raise CalibrationError("labels_empty")
    n = len(labels_a)
    observed = sum(a == b for a, b in zip(labels_a, labels_b)) / n
    ca = Counter(labels_a)
    cb = Counter(labels_b)
    labels = set(ca) | set(cb)
    expected = sum((ca[label] / n) * (cb[label] / n) for label in labels)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def calibration_gate(
    labels_a: Sequence[str],
    labels_b: Sequence[str],
    *,
    min_items: int = 30,
    min_kappa: float = 0.80,
) -> Dict[str, Any]:
    kappa = cohen_kappa(labels_a, labels_b)
    n = len(labels_a)
    passed = n >= min_items and kappa >= min_kappa
    return {
        "status": "PASS" if passed else "BLOCKED",
        "items": n,
        "kappa": round(kappa, 6),
        "min_items": min_items,
        "min_kappa": min_kappa,
        "reason": None if passed else ("insufficient_items" if n < min_items else "kappa_below_threshold"),
    }
