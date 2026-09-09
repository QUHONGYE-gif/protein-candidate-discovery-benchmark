"""M5D 开发消融：只从可观测突变与已核验残基映射生成特征。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pcdb.datasets.skempi_readiness import AA, ObservableCandidate, observable_features

ARMS = ("F0", "F1", "F2", "F3")


@dataclass(frozen=True)
class FeatureAblation:
    """共享候选顺序的四臂矩阵；词表身份必须随尝试保存。"""

    matrices: dict[str, tuple[tuple[int, ...], ...]]
    columns: dict[str, tuple[str, ...]]
    center_count: int
    neighbor_count: int
    neighbor_present: int


def build_features(
    candidates: Sequence[ObservableCandidate],
    residues: Mapping[tuple[str, str, str], str],
) -> FeatureAblation:
    """全池类别词表仅利用 observables，不接收任何标签或 evaluator 结果。"""
    if not candidates or len({c.candidate_id for c in candidates}) != len(candidates):
        raise ValueError("候选为空或重复")
    if len({c.complex_id for c in candidates}) != 1:
        raise ValueError("位点词表不得跨复合物共享")
    sites = sorted({t[1:-1] for c in candidates for t in c.mutations})
    substitutions = sorted({t[1:] for c in candidates for t in c.mutations})
    names0 = tuple([f"wild:{a}" for a in AA] + [f"mutant:{a}" for a in AA] + ["count"])
    names1 = names0 + tuple(f"pair:{a}>{b}" for a in AA for b in AA)
    names2 = (
        names1
        + tuple(f"site:{s}" for s in sites)
        + tuple(f"site-mutant:{s}" for s in substitutions)
    )
    names3 = names2 + tuple(f"context:{offset}:{aa}" for offset in range(-2, 3) for aa in AA + "?")
    columns = dict(zip(ARMS, (names0, names1, names2, names3), strict=True))
    site_index = {s: i for i, s in enumerate(sites)}
    sub_index = {s: i for i, s in enumerate(substitutions)}
    matrices: dict[str, list[tuple[int, ...]]] = {arm: [] for arm in ARMS}
    centers = neighbors = present = 0
    for candidate in candidates:
        base = observable_features(candidate)
        pair = [0] * 400
        position = [0] * (len(sites) + len(substitutions))
        for token in candidate.mutations:
            pair[AA.index(token[0]) * 20 + AA.index(token[-1])] += 1
            position[site_index[token[1:-1]]] += 1
            position[len(sites) + sub_index[token[1:]]] += 1
        context = [0] * 105
        for token in candidate.cleaned_mutations:
            pdb, chain, site = candidate.complex_id[:4], token[1], token[2:-1]
            if residues.get((pdb, chain, site)) != token[0]:
                raise ValueError("局部窗口中心残基不匹配")
            centers += 1
            for j, offset in enumerate(range(-2, 3)):
                if offset == 0:
                    aa = token[0]
                else:
                    neighbors += 1
                    aa = (
                        residues.get((pdb, chain, str(int(site) + offset)), "?")
                        if site.lstrip("-").isdigit()
                        else "?"
                    )
                    present += aa != "?"
                context[j * 21 + (AA + "?").index(aa)] += 1
        values = (
            base,
            base + tuple(pair),
            base + tuple(pair + position),
            base + tuple(pair + position + context),
        )
        for arm, row in zip(ARMS, values, strict=True):
            matrices[arm].append(row)
    return FeatureAblation(
        {arm: tuple(rows) for arm, rows in matrices.items()}, columns, centers, neighbors, present
    )
