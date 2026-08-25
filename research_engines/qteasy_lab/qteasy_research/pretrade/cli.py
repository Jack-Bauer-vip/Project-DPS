"""投前研究 CLI。"""

from __future__ import annotations

import argparse
import json

from qteasy_research.pretrade import (
    close_research_project,
    create_research_project,
    export_report_bundle,
    export_research_report,
    list_research_projects,
    reopen_research_project,
)
from qteasy_research.pretrade.orchestrator import continue_research, run_instrument_research


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ETF/股票自动投前研究引擎")
    sub = parser.add_subparsers(dest="command", required=True)
    research = sub.add_parser("research", help="执行一个标的的投前研究")
    research.add_argument("code", help="证券代码，例如 518880.SH")
    research.add_argument("--benchmark", default=None)
    research.add_argument("--horizon", default="medium", choices=["short", "medium", "long"])
    research.add_argument("--force-refresh", action="store_true")
    research.add_argument("--provider", choices=["none", "ollama", "deepseek"], default="none")
    research.add_argument("--model", default=None)
    research.add_argument("--indicator-config", default=None, help="指标配置 JSON，例如 {\"rsi_period\":21}")
    research.add_argument("--offline", action="store_true", help="只使用本地 CSV")
    research.add_argument("--data-mode", choices=["direct", "hybrid", "local"], default="direct")
    research.add_argument("--no-evidence", action="store_true", help="关闭联网定性证据补研")
    research.add_argument("--local-data-provider", default=None)
    research.add_argument("--no-knowledge-ref", action="store_true", help="关闭 K 知识库研究起点参考查询")
    research.add_argument("--output-dir", default=None)
    research.add_argument("--project-id", default=None)
    research.add_argument("--update-policy", choices=["reuse", "check_update", "refresh", "force_refresh"], default="reuse")
    research.add_argument("--as-of-date", default=None, help="历史回放截止日，只使用该日以前的数据，例如 2023-01-15")
    project = sub.add_parser("project", help="管理持久化研究项目")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_create = project_sub.add_parser("create", help="创建项目")
    project_create.add_argument("name")
    project_create.add_argument("code")
    project_create.add_argument("--objective", default=None)
    project_create.add_argument("--project-type", choices=["ASSET_PROFILE", "STRATEGY_PORTFOLIO"], default="ASSET_PROFILE")
    project_create.add_argument("--strategy-name", default=None)
    project_create.add_argument("--output-dir", default=None)
    project_list = project_sub.add_parser("list", help="列出项目")
    project_list.add_argument("--status", default=None)
    project_list.add_argument("--output-dir", default=None)
    for command, help_text in (("close", "关闭项目"), ("reopen", "重新打开项目")):
        item = project_sub.add_parser(command, help=help_text)
        item.add_argument("project_id")
        item.add_argument("--output-dir", default=None)
    followup = sub.add_parser("followup", help="对既有研究运行继续研究")
    followup.add_argument("run_id")
    followup.add_argument("question")
    followup.add_argument("--provider", choices=["ollama", "deepseek"], required=True)
    followup.add_argument("--model", default=None)
    export = sub.add_parser("export", help="导出既有研究报告")
    export.add_argument("run_id")
    export.add_argument("--format", dest="formats", action="append", choices=["md", "html", "pdf"], default=None)
    export.add_argument("--bundle", action="store_true", help="生成完整报告包")
    export.add_argument("--no-draft", action="store_true", help="不包含人工研究补充草稿")
    export.add_argument("--output-dir", default=None, help="导出文件目标目录")
    export.add_argument("--store-dir", default=None, help="研究数据存储目录；不指定时使用默认 research_store")
    args = parser.parse_args(argv)
    if args.command == "research":
        indicator_config = None
        if args.indicator_config:
            try:
                indicator_config = json.loads(args.indicator_config)
            except json.JSONDecodeError as exc:
                parser.error(f"--indicator-config 必须是合法 JSON：{exc}")
            if not isinstance(indicator_config, dict):
                parser.error("--indicator-config 顶层必须是 JSON 对象")
        result = run_instrument_research(
            args.code,
            benchmark=args.benchmark,
            horizon=args.horizon,
            force_refresh=args.force_refresh,
            llm_provider=None if args.provider == "none" else args.provider,
            llm_model=args.model,
            network_research=not args.offline,
            data_mode="local" if args.offline else args.data_mode,
            evidence_research=not args.no_evidence and not args.offline,
            local_data_provider=args.local_data_provider,
            indicator_config=indicator_config,
            output_dir=args.output_dir,
            project_id=args.project_id,
            update_policy=args.update_policy,
            as_of_date=args.as_of_date,
            knowledge_reference=not args.no_knowledge_ref,
        )
        print(result.report)
        print(f"\n报告文件：{result.artifacts.get('report', '未生成')}")
        print(f"结果文件：{result.artifacts.get('result', '未生成')}")
        return 0 if result.run_status != "FAILED" else 1
    if args.command == "project":
        if args.project_command == "create":
            project = create_research_project(
                args.name,
                args.code,
                args.objective,
                project_type=args.project_type,
                strategy_name=args.strategy_name,
                output_dir=args.output_dir,
            )
            print(json.dumps(project.__dict__, ensure_ascii=False, indent=2))
            return 0
        if args.project_command == "list":
            print(json.dumps([item.__dict__ for item in list_research_projects(args.status, output_dir=args.output_dir)], ensure_ascii=False, indent=2))
            return 0
        if args.project_command == "close":
            print(json.dumps(close_research_project(args.project_id, output_dir=args.output_dir).__dict__, ensure_ascii=False, indent=2))
            return 0
        if args.project_command == "reopen":
            print(json.dumps(reopen_research_project(args.project_id, output_dir=args.output_dir).__dict__, ensure_ascii=False, indent=2))
            return 0
    if args.command == "followup":
        result = continue_research(
            args.run_id,
            args.question,
            llm_provider=args.provider,
            llm_model=args.model,
        )
        print(result.report)
        print(f"\n追问报告文件：{result.artifacts.get('followup', '未生成')}")
        return 0 if result.run_status != "FAILED" else 1
    if args.command == "export":
        if args.bundle:
            exported = export_report_bundle(
                args.run_id,
                include_draft=not args.no_draft,
                destination_dir=args.output_dir,
                output_dir=args.store_dir,
            )
        else:
            exported = export_research_report(
                args.run_id,
                formats=tuple(args.formats or ("md", "html", "pdf")),
                include_draft=not args.no_draft,
                destination_dir=args.output_dir,
                output_dir=args.store_dir,
            )
        print(json.dumps(exported.__dict__, ensure_ascii=False, indent=2))
        return 0 if exported.formats_generated else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
