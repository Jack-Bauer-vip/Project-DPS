"""网格策略系统化 P3：为网格两实例初始化 pretrade 研究项目（B 侧研究记录）。

目标实例与标的（只读系统A ``config/grid_config.csv`` 的 enabled 行，缺失时回退
``strategy_contract.json`` 的 enabled_assets）：

- ``grid_lh``：159985.SZ / 513520.SH / 513650.SH / 515180.SH
- ``grid_scz``：513520.SH / 513650.SH / 515180.SH / 518880.SH

对每个实例建立 ``STRATEGY_PORTFOLIO`` 研究项目（strategy_name = 实例 ID），把
enabled 标的写入组合标的上下文（portfolio asset context，初始记录）；对首次出现的
标的建立 ``ASSET_PROFILE`` 研究项目，并把资产档案引用挂到所属策略实例项目。
项目名称/策略标识全 ASCII（grid_lh / grid_scz）。

纪律：
- 只写 B 本地 ``research_store``，**不写共享目录、不写 A**。
- 幂等：已存在项目复用、不重复建；只 upsert 缺失的标的上下文，**不删除**用户手动
  添加的组合标的（非破坏性）；不覆盖历史研究版本/快照。
- 默认 ``--dry-run`` 只打印计划；写入需显式 ``--apply``。
- 只读系统A ``config/``（grid_config.csv / strategy_contract.json），绝不修改。

用法：
  .\\.venv\\Scripts\\python.exe -B scripts\\init_grid_research_projects.py            # 预览
  .\\.venv\\Scripts\\python.exe -B scripts\\init_grid_research_projects.py --apply     # 写入
  .\\.venv\\Scripts\\python.exe -B scripts\\init_grid_research_projects.py --output-dir <临时目录> --apply  # 测试隔离
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from qteasy_research.pretrade import (
    add_asset_reference,
    add_project_note,
    create_research_project,
    create_strategy_project,
    list_asset_references,
    list_project_notes,
    list_research_projects,
    update_portfolio_asset_context,
    ResearchProjectType,
)
from qteasy_research.pretrade.symbols import normalize_code
from qteasy_research.reference.config import STRATEGY_CONTRACT_PATH, SYSTEM_A_ROOT

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORE_ROOT = PROJECT_ROOT / "research_store"
GRID_CONFIG_PATH = SYSTEM_A_ROOT / "config" / "grid_config.csv"
# 网格策略两实例（全 ASCII 标识；与 A 侧 grid_config.csv / strategy_contract.json 对齐）。
GRID_INSTANCE_IDS = ("grid_lh", "grid_scz")
# 初始记录笔记标题（幂等判断键）。
INIT_NOTE_TITLE = "grid_pretrade_init"


# ---------------------------------------------------------------------------
# 数据读取（只读系统A config/）
# ---------------------------------------------------------------------------

def _read_grid_config(grid_config_path: Path) -> dict[str, list[dict[str, Any]]]:
    """读 A ``config/grid_config.csv`` 的 enabled 行，按 strategy_id 分组。

    返回 ``{strategy_id: [row dict, ...]}``（仅 enabled=1 的行）。
    缺失/不可读 → 空 dict（调用方回退契约）。
    """
    if not Path(grid_config_path).exists():
        return {}
    frame = pd.read_csv(grid_config_path, dtype=str)
    required = {"strategy_id", "asset_id", "enabled"}
    missing = required.difference(set(frame.columns))
    if missing:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for _, row in frame.iterrows():
        strategy_id = str(row.get("strategy_id", "")).strip()
        enabled = str(row.get("enabled", "")).strip().lower()
        if strategy_id and enabled in {"1", "true", "yes"}:
            grouped.setdefault(strategy_id, []).append(row.to_dict())
    return grouped


def _read_contract_assets(strategy_contract_path: Path) -> dict[str, list[str]]:
    """读 A ``strategy_contract.json`` 的 grid 策略 enabled_assets（回退源）。

    返回 ``{strategy_id: [asset_id, ...]}``。
    """
    if not Path(strategy_contract_path).exists():
        return {}
    from qteasy_research.reference.backtest_engine import parse_contract

    try:
        contract = parse_contract(strategy_contract_path)
    except Exception:
        return {}
    result: dict[str, list[str]] = {}
    for strategy in contract.strategies:
        if strategy.decision_rule == "grid" and strategy.enabled:
            result[strategy.strategy_id] = list(strategy.enabled_assets)
    return result


def load_grid_instances(
    grid_config_path: Path | str = GRID_CONFIG_PATH,
    strategy_contract_path: Path | str = STRATEGY_CONTRACT_PATH,
    instances: tuple[str, ...] = GRID_INSTANCE_IDS,
) -> dict[str, dict[str, Any]]:
    """组装两个网格实例的 enabled 标的。

    ``grid_config.csv`` 优先；某实例在 config 中缺失时回退契约；两者皆无该实例
    → ``ValueError``（不虚构标的集合）。

    返回 ``{instance_id: {"assets": [code, ...], "rows": [raw row dict, ...],
    "source": "grid_config.csv" | "strategy_contract.json"}}``。
    """
    config = _read_grid_config(Path(grid_config_path))
    contract = _read_contract_assets(Path(strategy_contract_path))
    result: dict[str, dict[str, Any]] = {}
    for instance_id in instances:
        rows = config.get(instance_id, [])
        if rows:
            assets = [str(row.get("asset_id", "")).strip() for row in rows]
            assets = [code for code in assets if code]
            result[instance_id] = {
                "assets": assets,
                "rows": rows,
                "source": "grid_config.csv",
            }
            continue
        contract_assets = contract.get(instance_id, [])
        if contract_assets:
            result[instance_id] = {
                "assets": list(contract_assets),
                "rows": [],
                "source": "strategy_contract.json",
            }
            continue
        raise ValueError(
            f"网格实例 {instance_id} 在 grid_config.csv 与 strategy_contract.json 中都无 enabled 标的，"
            f"拒绝虚构标的集合"
        )
    return result


# ---------------------------------------------------------------------------
# 只读查找（幂等键）
# ---------------------------------------------------------------------------

def _projects(output_dir: Path | str | None) -> list[Any]:
    return list_research_projects(output_dir=output_dir)


def find_strategy_project(instance_id: str, output_dir: Path | str | None = None) -> Any | None:
    """按 strategy_name 找 STRATEGY_PORTFOLIO 项目（ACTIVE/DRAFT 优先）。"""
    candidates = [
        p for p in _projects(output_dir)
        if p.project_type == ResearchProjectType.STRATEGY_PORTFOLIO.value
        and p.strategy_name == instance_id
    ]
    if not candidates:
        return None
    order = {"ACTIVE": 0, "DRAFT": 1, "PAUSED": 2, "CLOSED": 3, "ARCHIVED": 4}
    return sorted(candidates, key=lambda p: order.get(p.status, 9))[0]


def find_asset_project(code: str, output_dir: Path | str | None = None) -> Any | None:
    """按规范化代码找 ASSET_PROFILE 项目（ACTIVE/DRAFT 优先）。"""
    normalized, _ = normalize_code(code)
    candidates = [
        p for p in _projects(output_dir)
        if p.project_type == ResearchProjectType.ASSET_PROFILE.value
        and p.code == normalized
    ]
    if not candidates:
        return None
    order = {"ACTIVE": 0, "DRAFT": 1, "PAUSED": 2, "CLOSED": 3, "ARCHIVED": 4}
    return sorted(candidates, key=lambda p: order.get(p.status, 9))[0]


def _has_note(project_id: str, title: str, output_dir: Path | str | None) -> bool:
    return any(note.title == title for note in list_project_notes(project_id, output_dir=output_dir))


def _has_reference(project_id: str, asset_project_id: str, output_dir: Path | str | None) -> bool:
    return any(ref.asset_project_id == asset_project_id for ref in list_asset_references(project_id, output_dir=output_dir))


# ---------------------------------------------------------------------------
# 初始记录内容
# ---------------------------------------------------------------------------

def _strategy_init_content(instance_id: str, info: dict[str, Any]) -> str:
    lines = [
        "# grid_pretrade_init",
        f"instance: {instance_id}",
        f"source: {info['source']}",
        "assets: " + ", ".join(info["assets"]),
    ]
    if info.get("rows"):
        lines.append("grid_config_rows:")
        for row in info["rows"]:
            lines.append("  " + json.dumps(row, ensure_ascii=False))
    return "\n".join(lines)


def _asset_init_content(code: str, instances: dict[str, dict[str, Any]]) -> str:
    memberships = [sid for sid in GRID_INSTANCE_IDS if code in instances[sid]["assets"]]
    return f"# grid_pretrade_init\nasset: {code}\nmembership: {', '.join(memberships)}"


# ---------------------------------------------------------------------------
# 计划 + 执行
# ---------------------------------------------------------------------------

def plan_init(
    instances: dict[str, dict[str, Any]],
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """只读计划：哪些项目会新建/复用，哪些笔记/引用会补上。"""
    plan: dict[str, Any] = {"strategy": {}, "asset": {}, "notes_to_add": [], "refs_to_add": []}
    strategy_projects: dict[str, Any] = {}
    for instance_id in GRID_INSTANCE_IDS:
        if instance_id not in instances:
            raise ValueError(f"缺少网格实例定义：{instance_id}")
        project = find_strategy_project(instance_id, output_dir)
        plan["strategy"][instance_id] = {
            "project_id": project.project_id if project else None,
            "created": project is None,
            "assets": instances[instance_id]["assets"],
        }
        strategy_projects[instance_id] = project
        if project is None or not _has_note(project.project_id, INIT_NOTE_TITLE, output_dir):
            plan["notes_to_add"].append(f"strategy:{instance_id}")
    unique_assets = sorted({code for info in instances.values() for code in info["assets"]})
    for code in unique_assets:
        project = find_asset_project(code, output_dir)
        plan["asset"][code] = {
            "project_id": project.project_id if project else None,
            "created": project is None,
        }
        if project is None or not _has_note(project.project_id, INIT_NOTE_TITLE, output_dir):
            plan["notes_to_add"].append(f"asset:{code}")
        for sid in GRID_INSTANCE_IDS:
            if code not in instances[sid]["assets"]:
                continue
            sp = strategy_projects[sid]
            if sp is not None and _has_reference(sp.project_id, project.project_id, output_dir):
                continue  # 已挂引用，跳过
            # 策略项目已存在或本次将新建 → 引用都会被补上，计入计划。
            plan["refs_to_add"].append(f"{sid}->{code}")
    return plan


def run_init(
    instances: dict[str, dict[str, Any]],
    output_dir: Path | str | None = None,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """初始化网格 pretrade 研究项目。

    ``apply=False``（默认）只返回计划，不写任何数据。``apply=True`` 时：
    - 为每个实例新建 STRATEGY_PORTFOLIO（缺失时）并 upsert 组合标的上下文；
    - 为首次出现的标的新建 ASSET_PROFILE，并挂资产引用到所属策略实例；
    - 给项目补初始记录笔记（幂等，按标题判断）。
    """
    plan = plan_init(instances, output_dir)
    if not apply:
        return plan

    strategy_projects: dict[str, Any] = {}
    for instance_id in GRID_INSTANCE_IDS:
        entry = plan["strategy"][instance_id]
        if entry["created"]:
            info = instances[instance_id]
            project = create_strategy_project(
                name=f"{instance_id}_pretrade",
                strategy_name=instance_id,
                objective=f"grid strategy {instance_id} pretrade research (P3 init, source={info['source']})",
                horizon="medium",
                output_dir=output_dir,
            )
            entry["project_id"] = project.project_id
        else:
            project = find_strategy_project(instance_id, output_dir)
        strategy_projects[instance_id] = project
        # 组合标的上下文（初始记录；upsert 不删除用户手动添加的其他标的）。
        for code in instances[instance_id]["assets"]:
            update_portfolio_asset_context(
                project.project_id,
                code,
                horizon="medium",
                weight=0.0,
                role="grid",
                enabled=True,
                output_dir=output_dir,
            )
        if not _has_note(project.project_id, INIT_NOTE_TITLE, output_dir):
            add_project_note(
                project.project_id,
                _strategy_init_content(instance_id, instances[instance_id]),
                title=INIT_NOTE_TITLE,
                output_dir=output_dir,
            )
        entry["project_id"] = project.project_id

    for code, entry in plan["asset"].items():
        if entry["created"]:
            project = create_research_project(
                name=f"{code}_grid_pretrade",
                code=code,
                objective="grid strategy asset pretrade research (P3 init)",
                project_type=ResearchProjectType.ASSET_PROFILE.value,
                output_dir=output_dir,
            )
            entry["project_id"] = project.project_id
        else:
            project = find_asset_project(code, output_dir)
        for sid in GRID_INSTANCE_IDS:
            if code not in instances[sid]["assets"]:
                continue
            sp = strategy_projects[sid]
            if not _has_reference(sp.project_id, project.project_id, output_dir):
                add_asset_reference(
                    sp.project_id,
                    project.project_id,
                    role="grid",
                    output_dir=output_dir,
                )
        if not _has_note(project.project_id, INIT_NOTE_TITLE, output_dir):
            add_project_note(
                project.project_id,
                _asset_init_content(code, instances),
                title=INIT_NOTE_TITLE,
                output_dir=output_dir,
            )
        entry["project_id"] = project.project_id
    return plan


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="网格策略两实例 pretrade 研究项目初始化（P3）")
    parser.add_argument("--apply", action="store_true", help="实际写入 research_store（默认仅预览计划）")
    parser.add_argument("--output-dir", type=Path, default=STORE_ROOT, help="研究存储目录（默认 research_store）")
    parser.add_argument("--grid-config", type=Path, default=GRID_CONFIG_PATH, help="系统A grid_config.csv 路径")
    parser.add_argument("--strategy-contract", type=Path, default=STRATEGY_CONTRACT_PATH, help="系统A strategy_contract.json 路径")
    return parser.parse_args(argv)


def _render(plan: dict[str, Any]) -> str:
    lines = ["[plan] 网格 pretrade 研究项目初始化预览"]
    for instance_id, entry in plan["strategy"].items():
        state = "NEW" if entry["created"] else "REUSE"
        lines.append(f"  strategy {instance_id}: {state} assets={', '.join(entry['assets'])}")
    for code, entry in plan["asset"].items():
        state = "NEW" if entry["created"] else "REUSE"
        lines.append(f"  asset {code}: {state}")
    for note in plan["notes_to_add"]:
        lines.append(f"  note+ {note}")
    for ref in plan["refs_to_add"]:
        lines.append(f"  ref+  {ref}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    instances = load_grid_instances(args.grid_config, args.strategy_contract)
    plan = run_init(instances, args.output_dir, apply=args.apply)
    print(_render(plan))
    if args.apply:
        created_strategy = [sid for sid, e in plan["strategy"].items() if e["created"]]
        created_asset = [code for code, e in plan["asset"].items() if e["created"]]
        print(f"[apply] 完成：新建策略项目 {created_strategy}，新建资产项目 {created_asset}，"
              f"补笔记 {len(plan['notes_to_add'])} 条，挂引用 {len(plan['refs_to_add'])} 条")
    else:
        print("[dry-run] 未写入任何数据；确认无误后加 --apply 实际创建。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
