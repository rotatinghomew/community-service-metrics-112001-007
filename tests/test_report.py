"""报告渲染测试：结构完整、JSON 可序列化、关键口径声明在场。"""
import json
from pathlib import Path
import unittest

from service_coverage.metrics import compute
from service_coverage.report import render_json, render_markdown
from service_coverage.sources import load_sources

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = compute(load_sources(FIXTURES), unified_version="new")
        cls.md = render_markdown(cls.result)

    def test_markdown_has_all_sections(self):
        for section in (
            "## 1. 数据来源与版本",
            "## 2. 口径说明",
            "## 3. 逐诉求归属明细",
            "## 4. 网格×期间指标",
            "## 5. 归属×承办矩阵",
            "## 6. 片区级需求序列",
            "## 7. 口径对比",
            "## 8. 数据质量与缺失清单",
            "## 9. 完整性校验",
        ):
            self.assertIn(section, self.md)

    def test_markdown_states_key_conventions(self):
        self.assertIn("待确认", self.md)          # 待确认移交不视为已转移
        self.assertIn("估计", self.md)            # 确定/估计分级
        self.assertIn("缺失", self.md)            # 缺失不等于零
        self.assertIn("跨区承办", self.md)

    def test_markdown_shows_estimated_separately(self):
        # X-2、X-4 为 range，应以估计形式出现，不混入确定归属。
        self.assertIn("（估计）", self.md)
        self.assertIn("（确定）", self.md)

    def test_json_is_serializable_and_complete(self):
        payload = json.loads(render_json(self.result))
        self.assertEqual(payload["periods"], ["2026-Q1", "2026-Q2"])
        self.assertIn("as-was", payload["grid_metrics"])
        self.assertIn("unified:new", payload["grid_metrics"])
        self.assertIn("attributions", payload)
        self.assertIn("cross_tab", payload)
        self.assertTrue(all(c["ok"] for c in payload["integrity"]))

    def test_json_keeps_missing_population_as_null(self):
        payload = json.loads(render_json(self.result))
        a4_q1 = payload["area_series"]["A4"]["2026-Q1"]
        self.assertIsNone(a4_q1["population"])
        self.assertTrue(a4_q1["population_missing"])


if __name__ == "__main__":
    unittest.main()
