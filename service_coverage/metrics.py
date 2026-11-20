"""指标汇总：网格×期间指标、片区级序列、跨界矩阵、口径对比与完整性校验。

汇总纪律
--------
- 不重复计算跨界对象：每条诉求在“归属需求”中按份额合计恰为 1，
  在“承办量”中按承办事实恰计 1 次；两个量分列，永不相加。
- 缺失不当作零：人口缺失的片区列入缺失清单，人均指标在人口不完整时
  不给出数值（None），绝不把缺失解释成无人居住。
- 估计与确定分列：range 精度只进入估计列，确定列只含 exact。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .engine import (
    Attribution,
    BoundaryIndex,
    Lens,
    ResponsibilityIndex,
    attribute_request,
    quarter_of,
)
from .sources import Sources

PER_1000 = 1000.0


def period_bounds(period: str) -> tuple[date, date]:
    """'2026-Q1' -> (2026-01-01, 2026-03-31)。"""
    year_s, q_s = period.split("-Q")
    year, quarter = int(year_s), int(q_s)
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    if start_month + 3 > 12:
        end = date(year, 12, 31)
    else:
        end = date(year, start_month + 3, 1) - _ONE_DAY
    return start, end


_ONE_DAY = date.resolution


@dataclass
class GridPeriodMetrics:
    """一个网格在一个期间、一种口径下的全部指标。"""

    grid: str
    period: str
    demand_certain: float = 0.0          # 确定归属需求
    demand_estimated: float = 0.0        # 按范围估计需求（份额）
    workload: float = 0.0                # 承办量（承办事实，每条诉求计 1 次）
    self_served_certain: float = 0.0     # 本网格承办本辖区需求
    self_served_estimated: float = 0.0
    cross_served_certain: float = 0.0    # 跨区承办：本网格承办、归属外区
    cross_served_estimated: float = 0.0
    outflow_certain: float = 0.0         # 外流：归属本网格、由外区承办
    outflow_estimated: float = 0.0
    demand_serving_unknown: float = 0.0  # 归属本网格但承办方缺失
    areas_touched: set[str] = field(default_factory=set)
    # 人口与人均（按同口径映射片区后填充）
    mapped_areas: tuple[str, ...] = ()
    population_known: int = 0
    population_missing: tuple[str, ...] = ()
    population_sources: tuple[str, ...] = ()
    population_status: str = "no-areas"  # complete / partial / missing / no-areas
    per_capita_certain: float | None = None
    per_capita_total: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def demand_total(self) -> float:
        return self.demand_certain + self.demand_estimated

    @property
    def cross_served_total(self) -> float:
        return self.cross_served_certain + self.cross_served_estimated

    @property
    def coverage_known_server(self) -> float | None:
        """服务记录覆盖率：归属需求中承办方明确的比例。"""
        if self.demand_total <= 0:
            return None
        return (self.demand_total - self.demand_serving_unknown) / self.demand_total

    @property
    def self_serve_share(self) -> float | None:
        """自办率：归属需求中由本网格承办的比例。"""
        if self.demand_total <= 0:
            return None
        return (self.self_served_certain + self.self_served_estimated) / self.demand_total


@dataclass
class AreaPeriod:
    """片区×期间的原子序列：片区是稳定单元，不随网格调整而变化。"""

    area: str
    period: str
    demand_certain: float = 0.0
    demand_estimated: float = 0.0
    population: int | None = None
    population_source: str | None = None
    population_missing: bool = False


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass
class GridComparison:
    """同一网格在两种口径下的需求序列对比（判断划分变化 vs 需求变化）。"""

    grid: str
    # period -> {"as-was": float|None, "unified": float|None}；None 表示该口径下网格不存在
    series: dict[str, dict[str, float | None]]
    notes: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    sources: Sources
    lenses: list[Lens]
    periods: list[str]
    attributions: dict[str, list[Attribution]]            # lens.name -> 逐诉求
    grid_metrics: dict[str, dict[str, dict[str, GridPeriodMetrics]]]  # lens -> period -> grid
    area_series: dict[str, dict[str, AreaPeriod]]         # area -> period
    cross_tab: dict[str, dict[str, dict[str, dict[str, float]]]]  # lens -> period -> 归属 -> 承办
    comparisons: list[GridComparison]
    integrity: list[CheckResult]
    data_quality: list[str]


def _population_lookup(sources: Sources) -> dict[tuple[str, str], tuple[int | None, str | None]]:
    """(area, period) -> (count, source_version)。

    同一片区同一期间出现多条记录时，优先取有计数的记录，其次取来源版本
    字典序最大者；冲突登记为数据质量问题。
    """
    best: dict[tuple[str, str], tuple[int | None, str | None]] = {}
    for rec in sources.population:
        key = (rec.area, rec.period)
        cand = (rec.count, rec.source_version)
        if key not in best:
            best[key] = cand
            continue
        prev = best[key]
        if prev[0] is None and cand[0] is not None:
            best[key] = cand
            sources.issues.append(
                f"人口 {rec.area} × {rec.period}: 多条记录冲突，采用来源 "
                f"{cand[1] or '未知'} 的计数"
            )
        elif prev[0] is not None and cand[0] is not None and cand != prev:
            sources.issues.append(
                f"人口 {rec.area} × {rec.period}: 来源 {prev[1]} 与 {cand[1]} "
                "计数不一致，保留先见记录，请核实来源版本"
            )
    return best


def compute(sources: Sources, unified_version: str = "new") -> AnalysisResult:
    boundaries = BoundaryIndex(sources.boundaries)
    responsibility = ResponsibilityIndex(boundaries, sources.transfers)
    lenses = [Lens("as-was"), Lens("unified", unified_version)]
    if unified_version not in boundaries.versions:
        raise ValueError(
            f"统一口径边界版本 {unified_version!r} 不存在；可选：{list(boundaries.versions)}"
        )

    periods = sorted(
        {quarter_of(r.occurred_at) for r in sources.requests if r.occurred_at}
        | {p.period for p in sources.population}
    )
    pop_lookup = _population_lookup(sources)

    attributions: dict[str, list[Attribution]] = {}
    grid_metrics: dict[str, dict[str, dict[str, GridPeriodMetrics]]] = {}
    cross_tab: dict[str, dict[str, dict[str, dict[str, float]]]] = {}

    for lens in lenses:
        attrs = [attribute_request(r, lens, boundaries, responsibility)
                 for r in sources.requests]
        attributions[lens.name] = attrs
        metrics_by_period: dict[str, dict[str, GridPeriodMetrics]] = {
            p: {} for p in periods
        }
        tab_by_period: dict[str, dict[str, dict[str, float]]] = {
            p: {} for p in periods
        }

        for req, attr in zip(sources.requests, attrs):
            if attr.period is None:
                continue
            period_metrics = metrics_by_period[attr.period]
            tab = tab_by_period[attr.period]

            # 归属需求：候选网格按份额分担，合计恰为 1（未归属单列）。
            home_keys: list[tuple[str, float]]
            if attr.candidates:
                home_keys = list(attr.candidates)
            else:
                home_keys = [("（未归属）", 1.0)]
            for grid, share in home_keys:
                gm = period_metrics.setdefault(grid, GridPeriodMetrics(grid, attr.period))
                if attr.certain:
                    gm.demand_certain += share
                else:
                    gm.demand_estimated += share
                if req.area:
                    gm.areas_touched.add(req.area)
                if attr.serving_grid is None:
                    gm.demand_serving_unknown += share
                row = tab.setdefault(grid, {})
                row[attr.serving_grid or "（承办未知）"] = (
                    row.get(attr.serving_grid or "（承办未知）", 0.0) + share
                )

            # 承办量：按承办事实计 1 次；跨界判定相对归属候选。
            if attr.serving_grid is not None:
                gm = period_metrics.setdefault(
                    attr.serving_grid, GridPeriodMetrics(attr.serving_grid, attr.period)
                )
                gm.workload += 1.0
                cand = dict(attr.candidates)
                self_share = cand.get(attr.serving_grid, 0.0)
                cross_share = 1.0 - self_share if cand else 1.0
                if attr.certain:
                    gm.self_served_certain += self_share
                    gm.cross_served_certain += cross_share
                else:
                    gm.self_served_estimated += self_share
                    gm.cross_served_estimated += cross_share
                for grid, share in home_keys:
                    if grid != attr.serving_grid and grid != "（未归属）":
                        out = period_metrics.setdefault(
                            grid, GridPeriodMetrics(grid, attr.period)
                        )
                        if attr.certain:
                            out.outflow_certain += share
                        else:
                            out.outflow_estimated += share

        # 人口映射：与归属同口径。as-was 用期间末日的责任划分，
        # unified 用指定版本的划分。缺失人口列清单，不按 0 计。
        for period in periods:
            start, end = period_bounds(period)
            period_metrics = metrics_by_period[period]
            if lens.kind == "unified":
                area_to_grid = {
                    a: boundaries.grid_at(a, version=lens.boundary_version)
                    for a in sources.areas
                }
                known_grids = set(boundaries.grids(version=lens.boundary_version))
            else:
                area_to_grid = {
                    a: responsibility.grid_at(a, end).grid for a in sources.areas
                }
                known_grids = set(boundaries.grids_in_interval(start, end))
            for grid, areas in _group_areas(area_to_grid).items():
                gm = period_metrics.setdefault(grid, GridPeriodMetrics(grid, period))
                gm.mapped_areas = areas
                _fill_population(gm, areas, period, pop_lookup)
            for grid, gm in period_metrics.items():
                if grid == "（未归属）":
                    continue
                if grid not in known_grids:
                    if lens.kind == "unified":
                        gm.notes.append(
                            f"该网格在统一口径版本 {lens.boundary_version} 中无定义"
                        )
                    else:
                        gm.notes.append(
                            "该期间无有效边界版本，责任/承办记录来自移交或事实记录"
                        )
        grid_metrics[lens.name] = metrics_by_period
        cross_tab[lens.name] = tab_by_period

    area_series = _build_area_series(sources, periods, pop_lookup)
    comparisons = _build_comparisons(lenses, periods, grid_metrics)
    integrity = _integrity_checks(sources, lenses, periods, attributions,
                                  grid_metrics, cross_tab)
    data_quality = _data_quality(sources, responsibility, attributions, lenses)
    return AnalysisResult(
        sources=sources,
        lenses=lenses,
        periods=periods,
        attributions=attributions,
        grid_metrics=grid_metrics,
        area_series=area_series,
        cross_tab=cross_tab,
        comparisons=comparisons,
        integrity=integrity,
        data_quality=data_quality,
    )


def _group_areas(area_to_grid: dict[str, str | None]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for area, grid in area_to_grid.items():
        if grid is not None:
            grouped.setdefault(grid, []).append(area)
    return {g: tuple(sorted(a)) for g, a in grouped.items()}


def _fill_population(
    gm: GridPeriodMetrics,
    areas: tuple[str, ...],
    period: str,
    pop_lookup: dict[tuple[str, str], tuple[int | None, str | None]],
) -> None:
    known = 0
    missing: list[str] = []
    sources_used: set[str] = set()
    for area in areas:
        count, version = pop_lookup.get((area, period), (None, None))
        if count is None:
            missing.append(area)
        else:
            known += count
        if version:
            sources_used.add(version)
    gm.population_known = known
    gm.population_missing = tuple(missing)
    gm.population_sources = tuple(sorted(sources_used))
    if not areas:
        gm.population_status = "no-areas"
    elif len(missing) == len(areas):
        gm.population_status = "missing"
    elif missing:
        gm.population_status = "partial"
    else:
        gm.population_status = "complete"
    # 人口不完整时不给出人均数值：缺失不是 0，更不是无人居住。
    if gm.population_status == "complete" and gm.population_known > 0:
        gm.per_capita_certain = gm.demand_certain / gm.population_known * PER_1000
        gm.per_capita_total = gm.demand_total / gm.population_known * PER_1000
    else:
        gm.per_capita_certain = None
        gm.per_capita_total = None
        if gm.population_status in ("partial", "missing"):
            gm.notes.append(
                f"人口基数不完整（缺失：{'、'.join(missing) or '无'}），"
                "人均诉求不给出数值；缺失≠0，≠无人居住"
            )


def _build_area_series(
    sources: Sources,
    periods: list[str],
    pop_lookup: dict[tuple[str, str], tuple[int | None, str | None]],
) -> dict[str, dict[str, AreaPeriod]]:
    series: dict[str, dict[str, AreaPeriod]] = {
        a: {p: AreaPeriod(a, p) for p in periods} for a in sources.areas
    }
    for req in sources.requests:
        if req.area is None or req.occurred_at is None:
            continue
        period = quarter_of(req.occurred_at)
        ap = series.setdefault(req.area, {}).setdefault(
            period, AreaPeriod(req.area, period)
        )
        if req.precision == "exact":
            ap.demand_certain += 1.0
        else:
            ap.demand_estimated += 1.0
    for (area, period), (count, version) in pop_lookup.items():
        ap = series.setdefault(area, {}).setdefault(period, AreaPeriod(area, period))
        ap.population = count
        ap.population_source = version
    # 人口未知统一标记：来源空计数与无记录同为“未知”，都不是 0。
    for periods_map in series.values():
        for ap in periods_map.values():
            ap.population_missing = ap.population is None
    return series


def _build_comparisons(
    lenses: list[Lens],
    periods: list[str],
    grid_metrics: dict[str, dict[str, dict[str, GridPeriodMetrics]]],
) -> list[GridComparison]:
    as_was, unified = lenses[0].name, lenses[1].name
    grids = sorted(
        {
            g
            for lens_name in (as_was, unified)
            for period_map in grid_metrics[lens_name].values()
            for g in period_map
            if g != "（未归属）"
        }
    )
    comparisons: list[GridComparison] = []
    for grid in grids:
        series: dict[str, dict[str, float | None]] = {}
        for period in periods:
            row: dict[str, float | None] = {}
            for lens_name in (as_was, unified):
                gm = grid_metrics[lens_name][period].get(grid)
                row[lens_name] = gm.demand_total if gm else None
            series[period] = row
        notes: list[str] = []
        for prev, curr in zip(periods, periods[1:]):
            a0, a1 = series[prev][as_was], series[curr][as_was]
            u0, u1 = series[prev][unified], series[curr][unified]
            if a0 is None and a1 is None:
                continue
            if a0 is None or a1 is None:
                note = (
                    f"{prev}→{curr}：当时责任口径下该网格在其中一期不存在，"
                    "期间差不具有同口径含义"
                )
                if u0 is not None and u1 is not None:
                    note += (
                        f"；统一口径下需求 {u0:.2f}→{u1:.2f}"
                        f"（可比变化 {u1 - u0:+.2f}），可供参考"
                    )
                notes.append(note)
                continue
            delta_aswas = a1 - a0
            if u0 is None or u1 is None:
                notes.append(
                    f"{prev}→{curr}：当时责任口径需求变化 {delta_aswas:+.2f}；"
                    "统一口径下该网格无定义，无法给出可比变化"
                )
                continue
            delta_unified = u1 - u0
            mapping_effect = delta_aswas - delta_unified
            notes.append(
                f"{prev}→{curr}：当时责任口径需求变化 {delta_aswas:+.2f}，"
                f"其中口径/划分差异解释 {mapping_effect:+.2f}，"
                f"统一口径下可比需求变化 {delta_unified:+.2f}"
            )
        comparisons.append(GridComparison(grid, series, notes))
    return comparisons


def _integrity_checks(
    sources: Sources,
    lenses: list[Lens],
    periods: list[str],
    attributions: dict[str, list[Attribution]],
    grid_metrics: dict[str, dict[str, dict[str, GridPeriodMetrics]]],
    cross_tab: dict[str, dict[str, dict[str, dict[str, float]]]],
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    eps = 1e-6
    for lens in lenses:
        for period in periods:
            expected = sum(
                1
                for a in attributions[lens.name]
                if a.period == period
            )
            metrics = grid_metrics[lens.name][period]
            demand = sum(m.demand_total for m in metrics.values())
            checks.append(CheckResult(
                f"需求守恒[{lens.name} {period}]",
                abs(demand - expected) < eps,
                f"各网格归属需求合计 {demand:.2f}，当期诉求 {expected} 条"
                "（含未归属单列，跨界对象不重复计算）",
            ))
            workload = sum(m.workload for m in metrics.values())
            expected_work = sum(
                1
                for a in attributions[lens.name]
                if a.period == period and a.serving_grid is not None
            )
            checks.append(CheckResult(
                f"承办守恒[{lens.name} {period}]",
                abs(workload - expected_work) < eps,
                f"承办量合计 {workload:.2f}，承办方明确的诉求 {expected_work} 条",
            ))
            tab = cross_tab[lens.name][period]
            row_sum = sum(v for row in tab.values() for v in row.values())
            checks.append(CheckResult(
                f"矩阵行列一致[{lens.name} {period}]",
                abs(row_sum - demand) < eps,
                f"归属×承办矩阵总量 {row_sum:.2f} 与归属需求合计一致",
            ))
    # 缺失人口不得折算为 0：结构上保证——人口不完整时人均指标必为 None。
    violated = [
        f"{lens.name} {period} {gm.grid}"
        for lens in lenses
        for period in periods
        for gm in [grid_metrics[lens.name][period].get(g) for g in grid_metrics[lens.name][period]]
        if gm is not None
        and gm.population_status != "complete"
        and (gm.per_capita_total is not None or gm.per_capita_certain is not None)
    ]
    checks.append(CheckResult(
        "缺失人口不按 0 计",
        not violated,
        "人口不完整的网格一律不给出人均数值" if not violated
        else f"发现违规：{violated}",
    ))
    # 估计份额守恒：每条估计归属的候选份额合计为 1（或无候选而单列）。
    bad = [
        a.request_id
        for lens in lenses
        for a in attributions[lens.name]
        if not a.certain
        and a.candidates
        and abs(sum(s for _, s in a.candidates) - 1.0) > eps
    ]
    checks.append(CheckResult(
        "估计份额守恒",
        not bad,
        "每条 range 诉求的候选份额合计恰为 1" if not bad
        else f"份额不守恒：{bad}",
    ))
    return checks


def _data_quality(
    sources: Sources,
    responsibility: ResponsibilityIndex,
    attributions: dict[str, list[Attribution]],
    lenses: list[Lens],
) -> list[str]:
    items: list[str] = list(sources.issues)
    # 口径分歧：责任与边界不一致的片区×日期（来自 as-was 归属标记）。
    seen: set[str] = set()
    for attr in attributions[lenses[0].name]:
        for flag in attr.flags:
            key = f"{attr.request_id}:{flag}"
            if key not in seen:
                seen.add(key)
                items.append(f"诉求 {attr.request_id}: {flag}")
    return items
