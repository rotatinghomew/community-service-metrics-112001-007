"""报告渲染：Markdown 与 JSON 两种输出。

报告结构
--------
1. 数据来源与版本
2. 口径说明
3. 逐诉求归属明细（区分确定归属与按范围估计）
4. 网格×期间指标（人均诉求、服务覆盖、跨区承办）
5. 归属×承办矩阵
6. 片区级需求序列（判断真实需求变化的稳定视角）
7. 口径对比：划分调整 vs 需求变化
8. 数据质量与缺失清单
9. 完整性校验
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .engine import Lens
from .metrics import AnalysisResult, GridPeriodMetrics

POP_STATUS_LABEL = {
    "complete": "完整",
    "partial": "部分缺失",
    "missing": "全部缺失",
    "no-areas": "无映射片区",
}


def fmt_num(x: float | None, digits: int = 2) -> str:
    if x is None:
        return "—"
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.{digits}f}"


def fmt_pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x * 100:.1f}%"


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def render_markdown(result: AnalysisResult) -> str:
    src = result.sources
    out: list[str] = []
    out.append("# 区域服务覆盖分析报告")
    out.append("")
    out.append(f"生成时间：{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}　"
               f"来源目录记录：边界 {len(src.boundaries)} 条 / 人口 {len(src.population)} 条 / "
               f"诉求 {len(src.requests)} 条 / 移交 {len(src.transfers)} 条")
    failed = [c for c in result.integrity if not c.ok]
    out.append(f"完整性校验：{'全部通过' if not failed else f'{len(failed)} 项未通过'}")
    out.append("")

    # 1. 数据来源与版本
    out.append("## 1. 数据来源与版本")
    out.append("")
    out.append("人口基数、边界生效日与网格责任移交各自有来源与版本，分别列示，互不覆盖：")
    out.append("")
    out.append("**边界版本**（有效期为半开区间，失效日为空表示持续有效）")
    out.append("")
    rows = [
        [b.id, b.grid, b.version,
         str(b.valid_from or "（缺失）"), str(b.valid_to) if b.valid_to else "（开放）",
         "、".join(b.areas)]
        for b in src.boundaries
    ]
    out += _md_table(["记录", "网格", "版本", "生效", "失效", "覆盖片区"], rows)
    out.append("")
    out.append("**人口基数**（按片区×期间，标注来源版本）")
    out.append("")
    rows = [
        [p.area, p.period, fmt_num(p.count) if p.count is not None else "（缺失）",
         p.source_version or "（缺失）"]
        for p in src.population
    ]
    out += _md_table(["片区", "期间", "人口", "来源版本"], rows)
    out.append("")
    out.append("**责任移交**（只有 accepted=yes 才视为责任已转移）")
    out.append("")
    rows = [
        [t.id, t.area, f"{t.from_grid}→{t.to_grid}",
         str(t.effective_at or "（缺失）"),
         "已接受" if t.is_accepted else f"未生效（{t.accepted or '状态缺失'}）"]
        for t in src.transfers
    ]
    out += _md_table(["记录", "片区", "移交", "生效日", "状态"], rows)
    out.append("")

    # 2. 口径说明
    out.append("## 2. 口径说明")
    out.append("")
    out.append("- **当时责任口径（as-was）**：按诉求发生当日的有效边界与移交状态解释责任。"
               "已接受移交自生效日起转移责任；待确认移交不视为已转移，责任留在原网格。"
               "旧月份永远按当时责任解释，新边界不套用到旧记录。")
    out.append("- **统一空间口径（unified）**：所有期间统一使用指定的同一边界版本归属，"
               "用于跨期间可比汇总。它是分析参照，不代表也不改写当时的责任事实。")
    out.append("- **精度分级**：exact 为确定归属；range 只按范围估计（候选网格等份额分摊），"
               "估计与确定分列，绝不把范围地址当作精确网格。")
    out.append("- **空值约定**：来源空值表示“未给出该事实”，不等于 0、否或业务终态；"
               "人口缺失不解释为无人居住，人口不完整时不给出人均数值。")
    out.append("- **承办与归属分列**：承办量按实际承办网格统计，归属需求按口径归属统计；"
               "跨界承办不代表需求属于承办方辖区，两个量分列展示、永不相加。")
    out.append("")

    # 3. 逐诉求归属明细
    out.append("## 3. 逐诉求归属明细")
    for lens in result.lenses:
        out.append("")
        out.append(f"### 3.{result.lenses.index(lens) + 1} {lens.label}")
        out.append("")
        rows = []
        for attr in result.attributions[lens.name]:
            if attr.certain:
                home = f"{attr.home}（确定）"
            elif attr.candidates:
                cand = "、".join(f"{g} {s:.0%}" for g, s in attr.candidates)
                home = f"{cand}（估计）"
            else:
                home = "（未归属）"
            cross = attr.is_cross_boundary
            cross_s = "跨界承办" if cross else ("承办方缺失" if cross is None and attr.serving_grid is None else "本区承办")
            rows.append([
                attr.request_id, attr.period or "（无期间）",
                attr.precision or "（缺失）", home,
                attr.serving_grid or "（缺失）", cross_s,
                "；".join(attr.flags) if attr.flags else "—",
            ])
        out += _md_table(
            ["诉求", "期间", "精度", "归属", "承办网格", "承办关系", "标记"], rows)
    out.append("")

    # 4. 网格×期间指标
    out.append("## 4. 网格×期间指标")
    for lens in result.lenses:
        out.append("")
        out.append(f"### 4.{result.lenses.index(lens) + 1} {lens.label}")
        for period in result.periods:
            metrics = result.grid_metrics[lens.name][period]
            if not metrics:
                continue
            out.append("")
            out.append(f"**{period}**")
            out.append("")
            rows = []
            for grid in sorted(metrics):
                gm = metrics[grid]
                if gm.population_status == "no-areas":
                    pop = "—"
                elif gm.population_status == "missing":
                    pop = f"未知（缺：{'、'.join(gm.population_missing)}）"
                elif gm.population_status == "partial":
                    pop = f"{gm.population_known}（另缺：{'、'.join(gm.population_missing)}）"
                else:
                    pop = str(gm.population_known)
                rows.append([
                    grid,
                    fmt_num(gm.demand_certain),
                    fmt_num(gm.demand_estimated),
                    fmt_num(gm.demand_total),
                    fmt_num(gm.workload),
                    f"{fmt_num(gm.cross_served_certain)}+{fmt_num(gm.cross_served_estimated)}估",
                    f"{fmt_num(gm.outflow_certain)}+{fmt_num(gm.outflow_estimated)}估",
                    pop,
                    POP_STATUS_LABEL[gm.population_status],
                    fmt_num(gm.per_capita_certain),
                    fmt_num(gm.per_capita_total),
                    fmt_pct(gm.coverage_known_server),
                    fmt_pct(gm.self_serve_share),
                    "；".join(gm.notes) if gm.notes else "—",
                ])
            out += _md_table(
                ["网格", "确定需求", "估计需求", "需求合计", "承办量",
                 "跨区承办（确定+估）", "需求外流（确定+估）", "人口（已知）", "人口状态",
                 "人均诉求/千（确定）", "人均诉求/千（合计）",
                 "服务覆盖率", "自办率", "备注"],
                rows,
            )
    out.append("")

    # 5. 归属×承办矩阵
    out.append("## 5. 归属×承办矩阵")
    out.append("")
    out.append("行归属需求、列承办网格；行合计=归属需求，列合计=承办量，"
               "两者分列核对，跨界对象不重复计算。")
    for lens in result.lenses:
        for period in result.periods:
            tab = result.cross_tab[lens.name][period]
            if not tab:
                continue
            servings = sorted({s for row in tab.values() for s in row})
            out.append("")
            out.append(f"**{lens.label} · {period}**")
            out.append("")
            rows = []
            for home in sorted(tab):
                row = [home] + [fmt_num(tab[home].get(s, 0.0)) for s in servings]
                row.append(fmt_num(sum(tab[home].values())))
                rows.append(row)
            totals = ["承办合计"] + [
                fmt_num(sum(tab[h].get(s, 0.0) for h in tab)) for s in servings
            ]
            totals.append(fmt_num(sum(v for row in tab.values() for v in row.values())))
            rows.append(totals)
            out += _md_table(["归属＼承办"] + servings + ["需求合计"], rows)
    out.append("")

    # 6. 片区级需求序列
    out.append("## 6. 片区级需求序列（口径无关的稳定视角）")
    out.append("")
    out.append("片区是稳定单元，不随网格合并或边界调整而变化；真实需求变化"
               "应在片区序列中可见，仅出现在网格层面的变化更可能来自划分调整。")
    out.append("")
    rows = []
    for area in sorted(result.area_series):
        for period in result.periods:
            ap = result.area_series[area].get(period)
            if ap is None:
                continue
            pop = "（未知）" if ap.population_missing else fmt_num(ap.population)
            rows.append([
                area, period,
                fmt_num(ap.demand_certain), fmt_num(ap.demand_estimated),
                pop, ap.population_source or "—",
            ])
    out += _md_table(
        ["片区", "期间", "确定需求", "估计需求", "人口", "人口来源"], rows)
    out.append("")

    # 7. 口径对比
    out.append("## 7. 口径对比：划分调整 vs 需求变化")
    out.append("")
    out.append("同一网格在两种口径下的归属需求对照。口径差反映辖区划分与责任解释的"
               "影响；统一口径内的变化才是可比的需求变化。")
    out.append("")
    as_was, unified = result.lenses[0].name, result.lenses[1].name
    rows = []
    for comp in result.comparisons:
        for period in result.periods:
            s = comp.series[period]
            rows.append([
                comp.grid, period,
                fmt_num(s[as_was]) if s[as_was] is not None else "（无此网格）",
                fmt_num(s[unified]) if s[unified] is not None else "（无此网格）",
            ])
    out += _md_table(
        ["网格", "期间", f"归属需求（{as_was}）", f"归属需求（{unified}）"], rows)
    out.append("")
    for comp in result.comparisons:
        for note in comp.notes:
            out.append(f"- **{comp.grid}**：{note}")
    out.append("")

    # 8. 数据质量与缺失清单
    out.append("## 8. 数据质量与缺失清单")
    out.append("")
    out.append("以下事项影响解释但不阻断汇总；缺失不等于 0、无人居住或业务终态：")
    out.append("")
    if result.data_quality:
        for item in result.data_quality:
            out.append(f"- {item}")
    else:
        out.append("- 无")
    out.append("")

    # 9. 完整性校验
    out.append("## 9. 完整性校验")
    out.append("")
    rows = [[c.name, "通过" if c.ok else "**未通过**", c.detail] for c in result.integrity]
    out += _md_table(["校验", "结果", "说明"], rows)
    out.append("")
    return "\n".join(out)


def to_jsonable(result: AnalysisResult) -> dict[str, Any]:
    """导出结构化结果，供下游系统使用。"""
    src = result.sources
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_files": src.files,
        "lenses": [
            {"name": l.name, "kind": l.kind, "boundary_version": l.boundary_version}
            for l in result.lenses
        ],
        "periods": result.periods,
        "attributions": {
            lens: [
                {
                    "request_id": a.request_id,
                    "period": a.period,
                    "precision": a.precision,
                    "certain": a.certain,
                    "home": a.home,
                    "candidates": [{"grid": g, "share": s} for g, s in a.candidates],
                    "serving_grid": a.serving_grid,
                    "flags": list(a.flags),
                }
                for a in attrs
            ]
            for lens, attrs in result.attributions.items()
        },
        "grid_metrics": {
            lens: {
                period: {
                    grid: _grid_metric_json(gm) for grid, gm in metrics.items()
                }
                for period, metrics in periods_map.items()
            }
            for lens, periods_map in result.grid_metrics.items()
        },
        "area_series": {
            area: {
                period: {
                    "demand_certain": ap.demand_certain,
                    "demand_estimated": ap.demand_estimated,
                    "population": ap.population,
                    "population_source": ap.population_source,
                    "population_missing": ap.population_missing,
                }
                for period, ap in periods_map.items()
            }
            for area, periods_map in result.area_series.items()
        },
        "cross_tab": result.cross_tab,
        "comparisons": [
            {"grid": c.grid, "series": c.series, "notes": c.notes}
            for c in result.comparisons
        ],
        "integrity": [
            {"name": c.name, "ok": c.ok, "detail": c.detail}
            for c in result.integrity
        ],
        "data_quality": result.data_quality,
    }


def _grid_metric_json(gm: GridPeriodMetrics) -> dict[str, Any]:
    return {
        "demand_certain": gm.demand_certain,
        "demand_estimated": gm.demand_estimated,
        "demand_total": gm.demand_total,
        "workload": gm.workload,
        "cross_served_certain": gm.cross_served_certain,
        "cross_served_estimated": gm.cross_served_estimated,
        "outflow_certain": gm.outflow_certain,
        "outflow_estimated": gm.outflow_estimated,
        "demand_serving_unknown": gm.demand_serving_unknown,
        "coverage_known_server": gm.coverage_known_server,
        "self_serve_share": gm.self_serve_share,
        "mapped_areas": list(gm.mapped_areas),
        "population_known": gm.population_known,
        "population_missing": list(gm.population_missing),
        "population_sources": list(gm.population_sources),
        "population_status": gm.population_status,
        "per_capita_certain": gm.per_capita_certain,
        "per_capita_total": gm.per_capita_total,
        "notes": gm.notes,
    }


def render_json(result: AnalysisResult) -> str:
    return json.dumps(to_jsonable(result), ensure_ascii=False, indent=2)
