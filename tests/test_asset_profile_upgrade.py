from __future__ import annotations

import shutil
import tempfile
import unittest
import os
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.pretrade.factors import analyze_asset_factors
from qteasy_research.pretrade.projects import (
    create_strategy_project,
    create_research_project,
    delete_research_report,
    get_report_draft,
    list_portfolio_asset_contexts,
    list_project_versions,
    restore_research_report,
    save_report_draft,
    update_portfolio_asset_context,
    update_project_portfolio_assets,
)
from qteasy_research.pretrade.export import export_report_bundle, export_research_report
from qteasy_research.pretrade.schemas import AssetProfile
from qteasy_research.pretrade.storage import ResearchStore
from qteasy_research.pretrade.orchestrator import run_instrument_research


class AssetProfileUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="qteasy_asset_upgrade_", dir="D:/Project DPS"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_fixed_profile_versions_only_change_when_content_changes(self) -> None:
        store = ResearchStore(self.root)
        profile = AssetProfile(
            code="518880.SH",
            name="华安易富黄金ETF",
            asset_type="ETF",
            exchange="SH",
            benchmark="000300.SH",
            fixed_metadata={"manager": "华安基金"},
            source="fixture",
            as_of="2026-07-30",
        )
        first = store.save_asset_profile(profile)
        second = store.save_asset_profile(profile)
        self.assertEqual(first.profile_version, second.profile_version)
        self.assertEqual(len(store.list_asset_profile_snapshots("518880.SH")), 1)

        changed = AssetProfile(**{**profile.__dict__, "fixed_metadata": {"manager": "华安基金", "fee": "0.50%"}})
        store.save_asset_profile(changed)
        self.assertEqual(len(store.list_asset_profile_snapshots("518880.SH")), 2)

    def test_factor_horizons_do_not_add_kdj(self) -> None:
        dates = pd.bdate_range("2024-01-01", periods=400)
        close = 10 * np.exp(np.linspace(0, 0.25, len(dates)))
        frame = pd.DataFrame({"trade_date": dates, "close": close})
        for horizon in ("short", "medium", "long"):
            result = analyze_asset_factors("518880.SH", frame, horizon=horizon)
            self.assertEqual(result["horizon"], horizon)
            self.assertTrue(all("kdj" not in str(item).lower() for item in result["factors"]))

    def test_same_asset_has_independent_portfolio_contexts(self) -> None:
        first = create_strategy_project("短线组合", "短线策略", output_dir=self.root)
        second = create_strategy_project("中线组合", "中线策略", output_dir=self.root)
        update_portfolio_asset_context(
            first.project_id,
            "518880.SH",
            horizon="short",
            weight=0.10,
            role="战术",
            buy_condition="动量转强",
            output_dir=self.root,
        )
        update_portfolio_asset_context(
            second.project_id,
            "518880.SH",
            horizon="medium",
            weight=0.30,
            role="防御",
            buy_condition="站上 MA120",
            output_dir=self.root,
        )
        contexts = list_portfolio_asset_contexts(code="518880.SH", output_dir=self.root)
        self.assertEqual({item.horizon for item in contexts}, {"short", "medium"})
        self.assertEqual({item.weight for item in contexts}, {0.10, 0.30})

    def test_removed_context_is_logically_deleted(self) -> None:
        project = create_strategy_project("组合", "策略", output_dir=self.root)
        update_project_portfolio_assets(
            project.project_id,
            [{"code": "518880.SH", "weight": 0.3, "horizon": "medium"}],
            output_dir=self.root,
        )
        update_project_portfolio_assets(project.project_id, [], output_dir=self.root)
        self.assertEqual(list_portfolio_asset_contexts(project_id=project.project_id, output_dir=self.root), [])

    def test_report_draft_and_logical_delete_preserve_versions(self) -> None:
        project = create_research_project("报告测试", "518880.SH", output_dir=self.root)
        first = run_instrument_research(
            "518880.SH",
            project_id=project.project_id,
            network_research=False,
            data_mode="local",
            update_policy="force_refresh",
            output_dir=str(self.root),
        )
        second = run_instrument_research(
            "518880.SH",
            project_id=project.project_id,
            network_research=False,
            data_mode="local",
            update_policy="force_refresh",
            output_dir=str(self.root),
        )
        save_report_draft(second.run_id, manual_summary="人工摘要", output_dir=self.root)
        self.assertEqual(get_report_draft(second.run_id, output_dir=self.root).manual_summary, "人工摘要")
        delete_research_report(second.run_id, reason="测试删除", output_dir=self.root)
        self.assertEqual([item.version_no for item in list_project_versions(project.project_id, output_dir=self.root)], [1])
        self.assertEqual([item.version_no for item in list_project_versions(project.project_id, include_deleted=True, output_dir=self.root)], [2, 1])
        restore_research_report(second.run_id, output_dir=self.root)
        self.assertEqual([item.version_no for item in list_project_versions(project.project_id, output_dir=self.root)], [2, 1])
        self.assertEqual(first.version_no, 1)

    def test_report_exports_keep_source_and_include_draft(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        project = create_research_project("导出测试", "518880.SH", output_dir=self.root)
        result = run_instrument_research(
            "518880.SH",
            project_id=project.project_id,
            network_research=False,
            data_mode="local",
            update_policy="force_refresh",
            output_dir=str(self.root),
        )
        source_json = Path(result.artifacts["result"]).read_bytes()
        save_report_draft(result.run_id, manual_summary="人工导出摘要", output_dir=self.root)
        export_dir = self.root / "exports"
        exported = export_research_report(
            result.run_id,
            formats=("md", "html", "pdf"),
            destination_dir=export_dir,
            output_dir=self.root,
        )
        self.assertEqual(set(("md", "html", "pdf")), set(exported.formats_generated))
        folder = export_dir / f"518880.SH_v1_{result.run_id}"
        self.assertIn("人工导出摘要", (folder / "report_with_draft.md").read_text(encoding="utf-8"))
        html_text = (folder / "report.html").read_text(encoding="utf-8")
        self.assertIn("人工导出摘要", html_text)
        self.assertIn("data:image/png;base64,", html_text)
        self.assertTrue((folder / "report.pdf").read_bytes().startswith(b"%PDF"))
        self.assertEqual(source_json, Path(result.artifacts["result"]).read_bytes())

        no_draft = export_research_report(
            result.run_id,
            formats=("html",),
            include_draft=False,
            destination_dir=export_dir / "no_draft",
            output_dir=self.root,
        )
        no_draft_html = Path(no_draft.paths["html"]).read_text(encoding="utf-8")
        self.assertNotIn("人工导出摘要", no_draft_html)

        bundle = export_report_bundle(result.run_id, destination_dir=export_dir, output_dir=self.root)
        with zipfile.ZipFile(bundle.paths["bundle"]) as archive:
            names = set(archive.namelist())
        self.assertIn("report.html", names)
        self.assertIn("report.pdf", names)
        self.assertIn("manifest.json", names)


if __name__ == "__main__":
    unittest.main()
