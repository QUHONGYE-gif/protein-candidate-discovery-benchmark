"""E4 固定 RF bootstrap 和线性高斯三点 lookahead；仅接收已付费状态。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import RandomForestRegressor  # type: ignore[import-untyped]

from pcdb.m2.models import _RANDOM_FOREST_PARAMETERS
from pcdb.m6.policies import Scores
from pcdb.replay._m6_engine import State
from pcdb.replay.oracle import Observation

Array = NDArray[np.float64]


def domain_seed(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big")


def information_value(
    h: Array, f: Array, unseen: NDArray[np.bool_], best: float, epsilon: float
) -> Array:
    """population covariance；HF/LF 返回值都用 HF label 单位。"""
    if (
        h.ndim != 2
        or f.shape != h.shape
        or unseen.shape != (h.shape[1],)
        or not np.isfinite(h).all()
        or not np.isfinite(f).all()
        or not np.isfinite(best)
        or not np.isfinite(epsilon)
        or epsilon <= 0
    ):
        raise ValueError("invalid lookahead input")
    if not unseen.any():
        return np.zeros(h.shape[1])
    mu = h[:, unseen].mean(axis=0)
    dh = h[:, unseen] - mu
    df = f - f.mean(axis=0)
    beta = (dh.T @ df / len(h)) / np.sqrt((df * df).mean(axis=0) + epsilon**2)
    base = max(best, float(mu.max()))
    plus = np.maximum(best, (mu[:, None] + np.sqrt(2) * beta).max(axis=0))
    minus = np.maximum(best, (mu[:, None] - np.sqrt(2) * beta).max(axis=0))
    return np.asarray(np.maximum(0.0, 0.25 * plus + 0.25 * minus - 0.5 * base), dtype=np.float64)


class RFScorer:
    """无 oracle/truth 参数；每次从 State 重建，不共享训练标签缓存。"""

    def __init__(
        self,
        ids: tuple[str, ...],
        features: Array,
        *,
        task: str,
        seed: int,
        initial_ids: tuple[str, ...],
        warmup_ids: tuple[str, ...],
    ) -> None:
        if (
            not ids
            or len(set(ids)) != len(ids)
            or features.shape != (len(ids), 420)
            or not np.isfinite(features).all()
            or not initial_ids
            or not set(initial_ids).issubset(ids)
            or not set(warmup_ids).issubset(ids)
        ):
            raise ValueError("invalid observable feature or initial identity")
        self.ids = ids
        self.x = features.copy()
        self.x.flags.writeable = False
        self.task, self.seed = task, seed
        self.initial_ids, self.warmup_ids = initial_ids, warmup_ids
        self.index = {candidate: i for i, candidate in enumerate(ids)}

    def _rows(self, observations: Sequence[Observation]) -> tuple[list[int], Array, str]:
        ordered = sorted(observations, key=lambda o: o.candidate_id)
        ids = [o.candidate_id for o in ordered]
        if len(set(ids)) != len(ids) or not set(ids).issubset(self.ids):
            raise ValueError("observation identity mismatch")
        y = np.array([o.label for o in ordered], dtype=np.float64)
        if not np.isfinite(y).all():
            raise ValueError("nonfinite observation")
        digest = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
        return [self.index[c] for c in ids], y, digest

    def _draw(self, fidelity: str, digest: str, member: int, n: int) -> NDArray[np.int64]:
        seed = domain_seed(
            f"m6-e4-bootstrap-v1:{self.task}:{self.seed}:{fidelity}:{digest}:{member}"
        )
        return np.random.Generator(np.random.PCG64(seed)).integers(n, size=n)

    def samples(self, state: State) -> tuple[Array, Array]:
        if state.candidate_ids != self.ids:
            raise ValueError("state feature universe mismatch")
        hi, hy, hh = self._rows(state.high)
        li, ly, lh = self._rows(state.low)
        if not hi:
            raise ValueError("HF observations required")
        h, lf_samples = np.zeros((5, len(self.ids))), np.zeros((5, len(self.ids)))
        for member in range(5):
            if li:
                draw = self._draw("low", lh, member, len(li))
                model = RandomForestRegressor(**dict(_RANDOM_FOREST_PARAMETERS))
                model.fit(self.x[li][draw], ly[draw])
                lf_samples[member] = model.predict(self.x)
                lf_samples[member, li] = ly
            paid = np.zeros(len(self.ids))
            paid[li] = 1
            xh = np.column_stack((self.x, lf_samples[member], paid))
            draw = self._draw("high", hh, member, len(hi))
            model = RandomForestRegressor(**dict(_RANDOM_FOREST_PARAMETERS))
            model.fit(xh[hi][draw], hy[draw])
            h[member] = model.predict(xh)
            h[member, hi] = hy
        return h, lf_samples

    def __call__(self, state: State) -> Scores:
        h, lf_samples = self.samples(state)
        high = {o.candidate_id: o.label for o in state.high}
        low = {o.candidate_id: o.label for o in state.low}
        if not set(self.initial_ids).issubset(high) or not set(self.warmup_ids).issubset(low):
            raise ValueError("initial/warmup observations incomplete")
        epsilon_h = 0.1 * max(float(np.std([high[c] for c in self.initial_ids])), 1e-8)
        epsilon_l = (
            0.1 * max(float(np.std([low[c] for c in self.warmup_ids])), 1e-8)
            if self.warmup_ids
            else 1.0
        )
        unseen = np.array([c not in high for c in self.ids])
        vh = information_value(h, h, unseen, max(high.values()), epsilon_h)
        vl = information_value(h, lf_samples, unseen, max(high.values()), epsilon_l)
        return Scores(
            dict(zip(self.ids, map(float, h.mean(axis=0)), strict=True)),
            dict(zip(self.ids, map(float, vh), strict=True)),
            dict(zip(self.ids, map(float, vl), strict=True)),
        )
