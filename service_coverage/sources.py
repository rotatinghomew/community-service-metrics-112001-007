"""来源装载与校验。

装载时保留出处（文件名、记录 id、版本字段），只做结构校验与问题登记，
不修改、不补写任何来源事实。发现的问题进入 issues，最终呈现在报告的
“数据质量与缺失清单”中。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .model import (
    BoundaryRecord,
    PopulationRecord,
    RequestRecord,
    TransferRecord,
)

TABLES = {
    "boundaries": ("boundaries.json", BoundaryRecord),
    "population": ("population.json", PopulationRecord),
    "requests": ("requests.json", RequestRecord),
    "transfers": ("transfers.json", TransferRecord),
}


@dataclass
class Sources:
    """四张来源表及其出处与装载期发现的问题。"""

    boundaries: list[BoundaryRecord]
    population: list[PopulationRecord]
    requests: list[RequestRecord]
    transfers: list[TransferRecord]
    files: dict[str, str]
    issues: list[str] = field(default_factory=list)

    @property
    def areas(self) -> tuple[str, ...]:
        """全部已知片区（来自任一张表），作为分析的片区全集。"""
        found: set[str] = set()
        for rec in self.boundaries:
            found.update(rec.areas)
        found.update(p.area for p in self.population)
        found.update(r.area for r in self.requests if r.area)
        found.update(t.area for t in self.transfers)
        return tuple(sorted(found))

    @property
    def grids(self) -> tuple[str, ...]:
        """全部已知网格（边界、承办、移交中出现的）。"""
        found: set[str] = {b.grid for b in self.boundaries}
        found.update(r.serving_grid for r in self.requests if r.serving_grid)
        for t in self.transfers:
            found.add(t.from_grid)
            found.add(t.to_grid)
        return tuple(sorted(found))


def _load_table(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        records = json.load(fh)
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ValueError(f"{path.name}: 期望为记录数组")
    return records


def load_sources(fixtures_dir: str | Path) -> Sources:
    """从目录装载四张来源表并做结构校验。"""
    base = Path(fixtures_dir)
    tables: dict[str, list] = {}
    files: dict[str, str] = {}
    issues: list[str] = []
    for name, (filename, factory) in TABLES.items():
        path = base / filename
        raw_records = _load_table(path)
        files[name] = filename
        tables[name] = [factory.from_raw(raw) for raw in raw_records]

    sources = Sources(
        boundaries=tables["boundaries"],
        population=tables["population"],
        requests=tables["requests"],
        transfers=tables["transfers"],
        files=files,
        issues=issues,
    )
    _validate(sources)
    return sources


def _validate(sources: Sources) -> None:
    issues = sources.issues

    seen_ids: set[str] = set()
    for req in sources.requests:
        if req.id in seen_ids:
            issues.append(f"诉求 {req.id}: 记录 id 重复")
        seen_ids.add(req.id)
        if req.area is None:
            issues.append(f"诉求 {req.id}: 缺少居住片区，无法做辖区归属")
        if req.occurred_at is None:
            issues.append(f"诉求 {req.id}: 缺少发生时间，无法归入期间")
        if req.serving_grid is None:
            issues.append(f"诉求 {req.id}: 承办网格缺失（来源未给出，不等于无人承办）")
        if req.precision not in ("exact", "range"):
            issues.append(
                f"诉求 {req.id}: 位置精度 {req.precision!r} 未知，按 range（估计）处理"
            )

    for rec in sources.boundaries:
        if not rec.areas:
            issues.append(f"边界 {rec.id}: 未列出任何片区")
        if rec.valid_from is None:
            issues.append(f"边界 {rec.id}: 生效日缺失，按自古有效处理")
        if rec.valid_to is None:
            issues.append(f"边界 {rec.id}: 失效日缺失，按持续有效处理")

    # 同一版本内一个片区被多个网格覆盖：属于来源冲突，登记后由引擎按规则取舍。
    by_version: dict[str, dict[str, list[str]]] = {}
    for rec in sources.boundaries:
        for area in rec.areas:
            by_version.setdefault(rec.version, {}).setdefault(area, []).append(rec.grid)
    for version, area_map in sorted(by_version.items()):
        for area, grids in sorted(area_map.items()):
            if len(set(grids)) > 1:
                issues.append(
                    f"边界版本 {version}: 片区 {area} 同时被 {sorted(set(grids))} 覆盖，"
                    "归属将按生效最晚的记录取舍"
                )

    for rec in sources.population:
        if rec.count is None:
            issues.append(
                f"人口 {rec.area} × {rec.period}: 来源 {rec.source_version or '未知'} "
                "未给出计数（缺失≠0，≠无人居住）"
            )

    for rec in sources.transfers:
        if rec.effective_at is None:
            issues.append(f"移交 {rec.id}: 生效日缺失，该记录不参与责任时间线")
        if not rec.is_accepted:
            issues.append(
                f"移交 {rec.id}（{rec.area}: {rec.from_grid}→{rec.to_grid}）状态 "
                f"{rec.accepted or '缺失'}：待确认不等于责任已转移"
            )
