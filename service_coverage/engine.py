"""归属引擎：边界版本、责任时间线与两种分析口径。

口径（lens）定义
----------------
as-was（当时责任口径）
    诉求按发生当日的责任归属解释：默认取当日有效边界覆盖的网格；
    若存在生效的移交记录，则以移交为准——已接受（accepted=yes）的移交
    自生效日起把责任交给 to_grid；待确认（pending 等非 yes）的移交
    不视为已转移，责任留在 from_grid。旧月份永远按当时责任解释，
    新边界不会被套用到旧记录上。

unified（统一空间口径）
    全部期间统一使用指定的某一个边界版本做空间归属，与发生日期、
    移交状态无关。它回答的是“在同一空间口径下是否可比”，
    是一种分析参照，不代表、也不改写当时的责任事实。

精度分级
--------
exact：确定归属，计入“确定”列。
range：地址只能定位到某个范围，归属不唯一，只按范围估计——
    候选网格取当日责任网格与当日边界网格的去重集合，等份额分摊，
    全部计入“估计”列，与确定归属分列展示，绝不假装有精确网格。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .model import BoundaryRecord, RequestRecord, TransferRecord

UNATTRIBUTED = "（未归属）"


def quarter_of(day: date) -> str:
    """日期所属季度期间，如 2026-04-03 -> '2026-Q2'。"""
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


class BoundaryIndex:
    """边界版本索引：按版本或按日期查询片区所属网格。"""

    def __init__(self, records: list[BoundaryRecord]):
        self._records = list(records)
        self.versions: tuple[str, ...] = tuple(
            sorted({r.version for r in self._records})
        )

    def grid_at(
        self,
        area: str,
        day: date | None = None,
        version: str | None = None,
    ) -> str | None:
        """片区在指定版本（或指定日期有效版本）下的网格；无覆盖返回 None。

        指定 version 时不看有效期——统一空间口径的语义就是把该版本的
        划分用于所有日期。同日多记录覆盖时取生效最晚者（装载时已登记问题）。
        """
        recs = [r for r in self._records if area in r.areas]
        if version is not None:
            recs = [r for r in recs if r.version == version]
        elif day is not None:
            recs = [r for r in recs if r.covers(day)]
        else:
            return None
        if not recs:
            return None
        recs.sort(key=lambda r: (r.valid_from or date.min, r.id))
        return recs[-1].grid

    def areas_of(self, grid: str, version: str) -> tuple[str, ...]:
        found: list[str] = []
        for rec in self._records:
            if rec.version == version and rec.grid == grid:
                found.extend(rec.areas)
        return tuple(sorted(set(found)))

    def grids(self, version: str | None = None) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    r.grid
                    for r in self._records
                    if version is None or r.version == version
                }
            )
        )

    def grids_in_interval(self, start: date, end: date) -> tuple[str, ...]:
        return tuple(
            sorted({r.grid for r in self._records if r.overlaps(start, end)})
        )

    def boundary_changed(self, area: str) -> bool:
        """该片区的网格覆盖是否在不同版本间发生过变化。"""
        grids = {
            r.grid for r in self._records if area in r.areas
        }
        return len(grids) > 1


@dataclass(frozen=True)
class Responsibility:
    """某片区在某日的责任归属解释。"""

    grid: str | None
    boundary_grid: str | None
    transfer_id: str | None
    transfer_status: str | None  # "accepted" / "pending" / None（无记录）
    divergence: bool             # 责任网格与当日边界网格不一致
    note: str


class ResponsibilityIndex:
    """责任时间线：当日有效边界为默认，移交记录优先。

    - 已接受移交：自 effective_at 起责任归 to_grid；
    - 待确认移交：责任留在 from_grid（待确认不等于已转移）；
    - 无移交记录：责任跟随当日边界；若该片区的边界划分跨版本发生过
      变化而无移交记录，登记说明（建议补录），不静默当作无变化。
    """

    def __init__(
        self,
        boundaries: BoundaryIndex,
        transfers: list[TransferRecord],
    ):
        self._boundaries = boundaries
        self._by_area: dict[str, list[TransferRecord]] = {}
        for t in transfers:
            if t.effective_at is not None:
                self._by_area.setdefault(t.area, []).append(t)

    def grid_at(self, area: str, day: date) -> Responsibility:
        boundary_grid = self._boundaries.grid_at(area, day)
        applicable = [
            t for t in self._by_area.get(area, []) if t.effective_at <= day  # type: ignore[operator]
        ]
        if not applicable:
            note = ""
            future = [
                t for t in self._by_area.get(area, [])
                if t.effective_at is not None and t.effective_at > day
            ]
            if future and boundary_grid is not None:
                soonest = min(future, key=lambda t: (t.effective_at, t.id))  # type: ignore[arg-type]
                note = (
                    f"移交 {soonest.id} 将于 {soonest.effective_at} 生效，"
                    f"当日责任仍归 {boundary_grid}"
                )
            elif self._boundaries.boundary_changed(area):
                note = "边界划分曾调整但无移交记录，责任默认跟随当日边界，建议补录移交"
            return Responsibility(
                grid=boundary_grid,
                boundary_grid=boundary_grid,
                transfer_id=None,
                transfer_status=None,
                divergence=False,
                note=note,
            )
        latest = max(applicable, key=lambda t: (t.effective_at, t.id))  # type: ignore[arg-type]
        if latest.is_accepted:
            grid: str | None = latest.to_grid
            status = "accepted"
            note = f"移交 {latest.id} 已接受，责任自 {latest.effective_at} 起归 {latest.to_grid}"
        else:
            grid = latest.from_grid
            status = "pending"
            note = (
                f"移交 {latest.id} 状态 {latest.accepted or '缺失'}，"
                f"待确认不等于已转移，责任仍归 {latest.from_grid}"
            )
        divergence = boundary_grid is not None and grid != boundary_grid
        return Responsibility(
            grid=grid,
            boundary_grid=boundary_grid,
            transfer_id=latest.id,
            transfer_status=status,
            divergence=divergence,
            note=note,
        )


@dataclass(frozen=True)
class Lens:
    """分析口径。kind: 'as-was' | 'unified'；unified 需指定 boundary_version。"""

    kind: str
    boundary_version: str | None = None

    @property
    def name(self) -> str:
        if self.kind == "unified":
            return f"unified:{self.boundary_version}"
        return "as-was"

    @property
    def label(self) -> str:
        if self.kind == "unified":
            return f"统一空间口径（边界版本 {self.boundary_version}）"
        return "当时责任口径（as-was）"


@dataclass
class Attribution:
    """单条诉求在某口径下的归属结果。"""

    request_id: str
    period: str | None
    precision: str | None
    certain: bool
    home: str | None                              # 唯一归属网格（估计且多候选时为 None）
    candidates: tuple[tuple[str, float], ...]     # (网格, 份额)，份额合计为 1（未归属为空）
    serving_grid: str | None
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_cross_boundary(self) -> bool | None:
        """承办网格是否跨界（相对归属）。无法判断时返回 None。"""
        if self.serving_grid is None or not self.candidates:
            return None
        return all(self.serving_grid != g for g, _ in self.candidates)


def attribute_request(
    req: RequestRecord,
    lens: Lens,
    boundaries: BoundaryIndex,
    responsibility: ResponsibilityIndex,
) -> Attribution:
    """计算单条诉求在指定口径下的归属。"""
    flags: list[str] = []
    if req.occurred_at is None or req.area is None:
        return Attribution(
            request_id=req.id,
            period=None,
            precision=req.precision,
            certain=False,
            home=None,
            candidates=(),
            serving_grid=req.serving_grid,
            flags=("缺少发生时间或居住片区，无法归属（不丢弃，单列统计）",),
        )
    period = quarter_of(req.occurred_at)
    exact = req.precision == "exact"

    if lens.kind == "unified":
        grid = boundaries.grid_at(req.area, version=lens.boundary_version)
        if grid is None:
            flags.append(
                f"统一口径版本 {lens.boundary_version} 未覆盖片区 {req.area}"
            )
        candidates: tuple[tuple[str, float], ...] = ((grid, 1.0),) if grid else ()
        if exact:
            return Attribution(req.id, period, req.precision, True, grid,
                               candidates, req.serving_grid, tuple(flags))
        return Attribution(req.id, period, req.precision, False,
                           grid if len(candidates) == 1 else None,
                           candidates, req.serving_grid, tuple(flags))

    # as-was：当时责任口径
    resp = responsibility.grid_at(req.area, req.occurred_at)
    if resp.note:
        flags.append(resp.note)
    if resp.divergence:
        flags.append(
            f"口径分歧：当日边界归 {resp.boundary_grid}，责任归 {resp.grid}"
        )
    if resp.grid is None:
        flags.append(f"当日有效边界未覆盖片区 {req.area}，归属未知")

    if exact:
        # 精确位置：责任网格即确定归属；分歧仅作标记，不改变责任解释。
        candidates = ((resp.grid, 1.0),) if resp.grid else ()
        return Attribution(req.id, period, req.precision, True, resp.grid,
                           candidates, req.serving_grid, tuple(flags))

    # range：只按范围估计。候选 = 当日责任网格与当日边界网格的去重集合，
    # 等份额分摊；份额合计为 1，保证汇总不重复计算。
    grids = [g for g in dict.fromkeys((resp.grid, resp.boundary_grid)) if g]
    if not grids:
        candidates = ()
    else:
        share = round(1.0 / len(grids), 6)
        candidates = tuple((g, share) for g in grids)
    flags.append("位置精度为 range，按范围估计，不计入确定归属")
    return Attribution(
        req.id, period, req.precision, False,
        grids[0] if len(grids) == 1 else None,
        candidates, req.serving_grid, tuple(flags),
    )
