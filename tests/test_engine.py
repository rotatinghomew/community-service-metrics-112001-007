"""归属引擎测试：责任时间线、两种口径与精度分级。"""
from datetime import date
from pathlib import Path
import unittest

from service_coverage.engine import (
    BoundaryIndex,
    Lens,
    ResponsibilityIndex,
    attribute_request,
    quarter_of,
)
from service_coverage.model import RequestRecord, TransferRecord
from service_coverage.sources import load_sources

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def make_request(**kw) -> RequestRecord:
    return RequestRecord.from_raw(
        {
            "id": kw.get("id", "T-1"),
            "area": kw.get("area", "A1"),
            "location_precision": kw.get("precision", "exact"),
            "occurred_at": kw.get("occurred_at", "2026-02-01"),
            "serving_grid": kw.get("serving_grid", "G1"),
        }
    )


class BoundaryIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = load_sources(FIXTURES)
        cls.index = BoundaryIndex(cls.sources.boundaries)

    def test_versions_detected(self):
        self.assertEqual(self.index.versions, ("new", "old"))

    def test_effective_boundary_at_date(self):
        d_before = date(2026, 3, 31)
        d_after = date(2026, 4, 1)
        self.assertEqual(self.index.grid_at("A2", d_before), "G1")
        self.assertEqual(self.index.grid_at("A2", d_after), "G3")
        self.assertEqual(self.index.grid_at("A3", d_before), "G2")
        self.assertEqual(self.index.grid_at("A3", d_after), "G3")

    def test_unified_version_ignores_dates(self):
        # 统一口径：new 版本映射适用于所有日期，包括边界生效前。
        self.assertEqual(
            self.index.grid_at("A2", date(2025, 6, 1), version="new"), "G3"
        )
        self.assertEqual(self.index.grid_at("A4", version="old"), "G2")

    def test_open_valid_to_means_ongoing(self):
        self.assertEqual(self.index.grid_at("A1", date(2027, 1, 1)), "G1")

    def test_unknown_area_returns_none(self):
        self.assertIsNone(self.index.grid_at("A9", date(2026, 1, 1)))


class ResponsibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = load_sources(FIXTURES)
        cls.index = ResponsibilityIndex(
            BoundaryIndex(cls.sources.boundaries), cls.sources.transfers
        )

    def test_accepted_transfer_moves_responsibility(self):
        before = self.index.grid_at("A2", date(2026, 3, 31))
        after = self.index.grid_at("A2", date(2026, 4, 1))
        self.assertEqual(before.grid, "G1")
        self.assertEqual(after.grid, "G3")
        self.assertEqual(after.transfer_status, "accepted")
        self.assertFalse(after.divergence)

    def test_pending_transfer_does_not_move_responsibility(self):
        resp = self.index.grid_at("A3", date(2026, 4, 3))
        self.assertEqual(resp.grid, "G2")          # 待确认 ≠ 已转移
        self.assertEqual(resp.boundary_grid, "G3")  # 但当日边界已是 G3
        self.assertTrue(resp.divergence)
        self.assertEqual(resp.transfer_status, "pending")

    def test_no_transfer_record_follows_boundary_with_note(self):
        resp = self.index.grid_at("A4", date(2026, 4, 5))
        self.assertEqual(resp.grid, "G3")
        self.assertIsNone(resp.transfer_status)
        self.assertIn("无移交记录", resp.note)

    def test_future_transfer_does_not_claim_missing_record(self):
        # A2 有移交记录但 2026-04-01 才生效：3 月时不应报“无移交记录”，
        # 而应说明移交尚未生效、当日责任仍归 G1。
        resp = self.index.grid_at("A2", date(2026, 3, 18))
        self.assertEqual(resp.grid, "G1")
        self.assertNotIn("无移交记录", resp.note)
        self.assertIn("将于 2026-04-01 生效", resp.note)

    def test_untouched_area_stays(self):
        resp = self.index.grid_at("A1", date(2026, 5, 1))
        self.assertEqual(resp.grid, "G1")
        self.assertEqual(resp.note, "")


class AttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = load_sources(FIXTURES)
        cls.boundaries = BoundaryIndex(cls.sources.boundaries)
        cls.responsibility = ResponsibilityIndex(cls.boundaries, cls.sources.transfers)
        cls.as_was = Lens("as-was")
        cls.unified = Lens("unified", "new")

    def attr(self, req, lens):
        return attribute_request(req, lens, self.boundaries, self.responsibility)

    def test_quarter_mapping(self):
        self.assertEqual(quarter_of(date(2026, 3, 12)), "2026-Q1")
        self.assertEqual(quarter_of(date(2026, 4, 3)), "2026-Q2")

    def test_exact_request_is_certain(self):
        a = self.attr(make_request(area="A1", occurred_at="2026-03-12"), self.as_was)
        self.assertTrue(a.certain)
        self.assertEqual(a.home, "G1")
        self.assertEqual(a.candidates, (("G1", 1.0),))

    def test_range_request_is_estimated_not_certain(self):
        a = self.attr(
            make_request(area="A2", precision="range", occurred_at="2026-03-18"),
            self.as_was,
        )
        self.assertFalse(a.certain)
        self.assertEqual(a.home, "G1")  # 单候选，但仍属估计
        self.assertAlmostEqual(sum(s for _, s in a.candidates), 1.0)

    def test_old_month_uses_responsibility_at_that_time(self):
        # X-3：2026-04-03 发生，A3 移交待确认 → 责任仍归 G2，尽管新边界是 G3。
        a = self.attr(make_request(area="A3", occurred_at="2026-04-03"), self.as_was)
        self.assertEqual(a.home, "G2")
        self.assertTrue(any("口径分歧" in f for f in a.flags))

    def test_unified_lens_applies_same_version_to_all_dates(self):
        a = self.attr(make_request(area="A2", occurred_at="2026-03-18"), self.unified)
        self.assertEqual(a.home, "G3")  # 统一口径下旧月份也按 new 边界
        self.assertTrue(a.certain)

    def test_cross_boundary_serving_does_not_change_home(self):
        # G1 承办 A3 的诉求，但需求归属不因承办而改变。
        a = self.attr(
            make_request(area="A3", occurred_at="2026-04-03", serving_grid="G1"),
            self.as_was,
        )
        self.assertEqual(a.home, "G2")
        self.assertEqual(a.serving_grid, "G1")
        self.assertTrue(a.is_cross_boundary)

    def test_range_with_divergence_gets_multiple_candidates(self):
        # range 精度 + 责任/边界分歧 → 多候选等份额，合计为 1。
        a = self.attr(
            make_request(area="A3", precision="range", occurred_at="2026-04-03"),
            self.as_was,
        )
        self.assertFalse(a.certain)
        self.assertIsNone(a.home)
        self.assertEqual({g for g, _ in a.candidates}, {"G2", "G3"})
        self.assertAlmostEqual(sum(s for _, s in a.candidates), 1.0)

    def test_missing_facts_not_dropped(self):
        a = self.attr(
            make_request(area="", occurred_at="2026-04-03"), self.as_was
        )
        self.assertFalse(a.certain)
        self.assertEqual(a.candidates, ())
        self.assertTrue(a.flags)

    def test_fixture_requests_as_was(self):
        """对 fixtures 全量诉求核对 as-was 归属。"""
        expected = {
            "X-1": ("G1", True),
            "X-2": ("G1", False),
            "X-3": ("G2", True),
            "X-4": ("G3", False),
            "X-5": ("G3", True),
        }
        for req in self.sources.requests:
            a = self.attr(req, self.as_was)
            home, certain = expected[req.id]
            self.assertEqual(a.home, home, req.id)
            self.assertEqual(a.certain, certain, req.id)


if __name__ == "__main__":
    unittest.main()
