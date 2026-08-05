"""Dependency-free frozen identities for the M2 model configurations."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

MODEL_CONFIG_SHA256: Mapping[str, str] = MappingProxyType(
    {
        "ridge-sklearn-v1": "806e9bb5c16a511b233213ca6f1f08ac848eeb77c7e9702915171ce4a22293d7",
        "random-forest-sklearn-v1": (
            "9a837a91a94a2b3b0b3fb6d93474a5140fbbadefb127b523830334213f2077f1"
        ),
        "xgboost-cpu-v1": "7e0a95e827a829b6d4dfa30db35d46770d82dc980784620bd844a378dc97a2cb",
    }
)
MODEL_CONFIG_SIZE_BYTES: Mapping[str, int] = MappingProxyType(
    {
        "ridge-sklearn-v1": 1_243,
        "random-forest-sklearn-v1": 2_575,
        "xgboost-cpu-v1": 5_693,
    }
)

__all__ = ["MODEL_CONFIG_SHA256", "MODEL_CONFIG_SIZE_BYTES"]
