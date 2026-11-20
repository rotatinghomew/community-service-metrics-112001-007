"""指标汇总测试：守恒、缺失人口、跨界不重复计数、口径对比。"""
from pathlib import Path
import unittest

from service_coverage.metrics import compute, period_bounds
from service_coverage.sources import load_sources

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class MetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = load_sources(FIXTURES)
        cls.result = compute(cls.sources, unified_version="new")

    def gm(self, lens, period, grid):
        return self.result.grid_metrics[lens][period][grid]

    def test_periods_cover_requests_and_population(self):
        self.assertEqual(self.result.periods, ["2026-Q1", "2026-Q2"])

    def test_period_bounds(self):
        start, end = period_bounds("2026-Q1")
        self.assertEqual((start.isoformat(), end.isoformat()),
                         ("2026-01-01", "2026-03-31"))

    def test_demand_conservation_per_lens_per_period(self):
        # 不重复计算跨界对象：各网格需求合计恰等于当期诉求数。
        for lens in ("as-was", "unified:new"):
            for period, expected in (("2026-Q1", 2.0), ("2026-Q2", 3.0)):
                total = sum(
                    m.demand_total
                    for m in self.result.grid_metrics[lens][period].values()
                )
                self.assertAlmostEqual(total, expected, msg=f"{lens} {period}")

    def test_workload_conservation(self):
        for lens in ("as-was", "unified:new"):
            for period, expected in (("2026-Q1", 2.0), ("2026-Q2", 3.0)):
                total = sum(
                    m.workload
                    for m in self.result.grid_metrics[lens][period].values()
                )
                self.assertAlmostEqual(total, expected, msg=f"{lens} {period}")

    def test_as_was_q1_g1(self):
        gm = self.gm("as-was", "2026-Q1", "G1")
        self.assertAlmostEqual(gm.demand_certain, 1.0)   # X-1
        self.assertAlmostEqual(gm.demand_estimated, 1.0)  # X-2（range）
        self.assertAlmostEqual(gm.workload, 2.0)
        self.assertEqual(gm.population_known, 670)        # A1 410 + A2 260
        self.assertEqual(gm.population_status, "complete")
        self.assertAlmostEqual(gm.per_capita_total, 2.0 / 670 * 1000, places=4)

    def test_missing_population_not_zero_not_uninhabited(self):
        # A4 的 2026-Q1 人口缺失：G2 人口状态为部分缺失，人均不给数值。
        gm = self.gm("as-was", "2026-Q1", "G2")
        self.assertEqual(gm.population_status, "partial")
        self.assertEqual(gm.population_missing, ("A4",))
        self.assertEqual(gm.population_known, 330)  # 仅 A3，A4 不按 0 计入
        self.assertIsNone(gm.per_capita_total)
        self.assertIsNone(gm.per_capita_certain)

    def test_q2_population_mostly_missing(self):
        # A1/A2/A3 无 2026-Q2 人口记录 → 相关网格人均均不给出。
        self.assertEqual(self.gm("as-was", "2026-Q2", "G1").population_status, "missing")
        self.assertEqual(self.gm("as-was", "2026-Q2", "G2").population_status, "missing")
        g3 = self.gm("as-was", "2026-Q2", "G3")
        self.assertEqual(g3.population_status, "partial")
        self.assertEqual(g3.population_known, 120)  # 仅 A4（来源 p2）
        self.assertEqual(g3.population_sources, ("p2",))
        self.assertIsNone(g3.per_capita_total)

    def test_cross_boundary_counted_once_per_measure(self):
        # X-3：G1 承办、归属 G2。G1 承办量含它，G1 归属需求不含它；
        # G2 归属需求含它并记外流；全系统需求总量不变。
        g1 = self.gm("as-was", "2026-Q2", "G1")
        g2 = self.gm("as-was", "2026-Q2", "G2")
        self.assertAlmostEqual(g1.workload, 1.0)
        self.assertAlmostEqual(g1.cross_served_certain, 1.0)
        self.assertAlmostEqual(g1.demand_total, 0.0)
        self.assertAlmostEqual(g2.demand_certain, 1.0)
        self.assertAlmostEqual(g2.outflow_certain, 1.0)
        self.assertAlmostEqual(g2.workload, 0.0)

    def test_responsibility_without_current_boundary_kept(self):
        # G2 在 2026-Q2 无有效边界，但因 A3 移交待确认仍承担责任。
        gm = self.gm("as-was", "2026-Q2", "G2")
        self.assertTrue(any("无有效边界" in n for n in gm.notes))

    def test_unified_lens_attributes_old_records_by_new_version(self):
        # 统一口径：X-2（3 月，A2）也归 G3；这只是分析参照。
        g3 = self.gm("unified:new", "2026-Q1", "G3")
        self.assertAlmostEqual(g3.demand_estimated, 1.0)
        g1 = self.gm("unified:new", "2026-Q1", "G1")
        self.assertAlmostEqual(g1.demand_certain, 1.0)
        self.assertAlmostEqual(g1.cross_served_estimated, 1.0)  # X-2 由 G1 承办

    def test_estimated_and_certain_kept_separate(self):
        g3 = self.gm("as-was", "2026-Q2", "G3")
        self.assertAlmostEqual(g3.demand_certain, 1.0)    # X-5
        self.assertAlmostEqual(g3.demand_estimated, 1.0)  # X-4（range）

    def test_area_series_is_lens_independent(self):
        a2 = self.result.area_series["A2"]
        self.assertAlmostEqual(a2["2026-Q1"].demand_estimated, 1.0)
        self.assertAlmostEqual(a2["2026-Q2"].demand_certain, 1.0)
        a4 = self.result.area_series["A4"]
        self.assertTrue(a4["2026-Q1"].population_missing)
        self.assertEqual(a4["2026-Q2"].population, 120)

    def test_cross_tab_reconciles(self):
        tab = self.result.cross_tab["as-was"]["2026-Q2"]
        self.assertAlmostEqual(tab["G2"]["G1"], 1.0)  # X-3：归属 G2，G1 承办
        self.assertAlmostEqual(sum(tab["G3"].values()), 2.0)

    def test_integrity_checks_all_pass(self):
        failed = [c for c in self.result.integrity if not c.ok]
        self.assertEqual(failed, [])

    def test_comparison_separates_redivision_from_demand_change(self):
        comp = {c.grid: c for c in self.result.comparisons}
        g1 = comp["G1"]
        self.assertAlmostEqual(g1.series["2026-Q1"]["as-was"], 2.0)
        self.assertAlmostEqual(g1.series["2026-Q1"]["unified:new"], 1.0)
        self.assertAlmostEqual(g1.series["2026-Q2"]["as-was"], 0.0)
        self.assertTrue(any("划分" in n or "口径" in n for n in g1.notes))

    def test_data_quality_mentions_pending_transfer_and_missing_population(self):
        text = "\n".join(self.result.data_quality)
        self.assertIn("待确认不等于责任已转移", text)
        self.assertIn("未给出计数", text)
        self.assertIn("口径分歧", text)


if __name__ == "__main__":
    unittest.main()
