"""B 行情刷新：Bern 数据中台（D）→ data/fund_daily.csv 增量 upsert（幂等 + 备份 + 校验）。

用法（从 qteasy_lab 根目录或任意目录均可）：
    python scripts/refresh_fund_daily.py                 # 仅刷新行情（默认，不发包）
    python scripts/refresh_fund_daily.py --dry           # 只打印将补哪些，不写盘
    python scripts/refresh_fund_daily.py --publish       # 刷新行情，校验通过后发包(--real)

说明：
    - 数据源：D 中台 http://127.0.0.1:8765/api/v1（X-API-Key 取 D_API_KEY 环境变量，
      DProvider 兼容 D_BASE_URL / D_API_KEY / BERN_DATA_* 覆盖）。
    - 标的白名单：fund_daily.csv 现有全部 ts_code（D 假指数码不会混入，防脏数据）。
    - 幂等：(ts_code, trade_date) 去重 keep=latest；补数前自动备份到 data/fund_daily.backup_*.csv。
    - 校验：与上一交易日 close 偏差 >30% 告警、close>0、最新日期上报；校验失败不写盘（--publish 不发包）。
    - --publish：校验 OK 后调用 scripts/run_reference_pipeline.py --real（B 发包写共享目录）。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from qteasy_research.pretrade.providers import DProvider  # noqa: E402

CSV_PATH = PROJECT_ROOT / "data" / "fund_daily.csv"
BACKUP_DIR = PROJECT_ROOT / "data"
CSV_COLUMNS = [
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "change", "pct_chg", "vol", "amount",
]
# D 脏数据防线：与上一交易日 close 偏差超过该比例视为可疑（ETF 单日极少 >30%）。
MAX_CLOSE_DIVERGENCE = 0.30
# D 分发接口未指定 limit 时仅返回最近 200 条；研究需全量历史，取接口上限。
DEFAULT_LIMIT = 100000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="B 行情刷新：D 中台 → fund_daily.csv 增量 upsert")
    parser.add_argument("--dry", dest="dry", action="store_true", default=False,
                        help="只打印将补的标的/日期区间，不写盘、不发包")
    parser.add_argument("--publish", dest="publish", action="store_true", default=False,
                        help="校验通过后调用 run_reference_pipeline.py --real 发包")
    parser.add_argument("--start-date", default=None,
                        help="D 拉取起点 YYYY-MM-DD（默认 = CSV 全局最新日期向前 7 天，确保衔接校验有重叠）")
    parser.add_argument("--codes", default=None,
                        help="逗号分隔标的白名单（默认 = CSV 现有全部 ts_code）")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data", help="B 数据目录")
    return parser.parse_args()


def _read_existing(data_root: Path) -> pd.DataFrame:
    path = data_root / "fund_daily.csv"
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype={"ts_code": str})
    if "trade_date" in frame:
        frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    return frame


def _dedupe(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    return (
        frame.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        .sort_values(["ts_code", "trade_date"])
        .reset_index(drop=True)
    )


def _align_columns(frame: pd.DataFrame, prev_close: float | None = None) -> pd.DataFrame:
    """补齐 CSV 固定列序；pre_close/change/pct_chg 缺失时滚动推导。

    prev_close = 该标的在本地 CSV 的最近收盘价（衔接 seed）：D 返回一般无 pre_close，
    若只在新增行内部 ffill，首日 pre_close 仍为 NaN（无前值可填）。用本地最新收盘
    做首行 seed，后续行用 close 滚动推导。
    """
    out = frame.copy()
    for column in CSV_COLUMNS:
        if column not in out.columns:
            out[column] = None
    for column in CSV_COLUMNS[2:]:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    if out["pre_close"].isna().any():
        out = out.sort_values("trade_date")
        # 滚动推导：pre_close[i] = close[i-1]；首日（无前值）用本地衔接 seed。
        derived = out["close"].shift(1)
        if prev_close is not None and not pd.isna(prev_close):
            derived.iloc[0] = prev_close
        out["pre_close"] = out["pre_close"].fillna(derived)
        out["change"] = (out["close"] - out["pre_close"]).round(3)
        out["pct_chg"] = (out["change"] / out["pre_close"].replace(0, pd.NA) * 100.0).round(2)
    return out[CSV_COLUMNS]


def _pull_from_d(
    provider: DProvider,
    code6: str,
    full_code: str,
    start_date: str,
    prev_close: float | None = None,
) -> pd.DataFrame:
    """从 D 中台拉单标的增量行情（含字段映射 + pre_close 衔接推导），失败返回空帧。"""
    try:
        records = provider._get_json(
            "/data/fund_etf_daily",
            {"code": code6, "limit": DEFAULT_LIMIT, "start_date": start_date.replace("-", "")},
        )
    except Exception as exc:
        print(f"    [跳过] {full_code}：D 拉取失败 {type(exc).__name__}: {exc}")
        return pd.DataFrame()
    if not records:
        print(f"    [无增量] {full_code}：D 自 {start_date} 起无数据")
        return pd.DataFrame()
    frame = provider._d_to_frame(records, full_code)
    if frame.empty:
        return pd.DataFrame()
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    frame = frame[frame["trade_date"] >= start_date]
    return _align_columns(frame, prev_close=prev_close)


def _check_continuity(existing: pd.DataFrame, new_rows: pd.DataFrame) -> list[str]:
    """new_rows 每标的首日 close 与 existing 该标的最新 close 偏差校验。"""
    warnings: list[str] = []
    if existing.empty or new_rows.empty:
        return warnings
    latest = (
        existing.sort_values("trade_date")
        .groupby("ts_code", as_index=False)
        .tail(1)
        .set_index("ts_code")["close"]
        .to_dict()
    )
    for code, group in new_rows.groupby("ts_code"):
        first = group.sort_values("trade_date").iloc[0]
        prev = latest.get(code)
        if prev is None or pd.isna(prev) or pd.isna(first["close"]) or prev <= 0:
            continue
        diverge = abs(first["close"] - prev) / prev
        if diverge > MAX_CLOSE_DIVERGENCE:
            warnings.append(
                f"{code} 衔接异常：前一交易日 {prev:.4f} → 新增首日 {first['close']:.4f}（Δ{diverge:.1%}，>30%）"
            )
    return warnings


def _validate(frame: pd.DataFrame, new_codes: list[str]) -> tuple[bool, list[str]]:
    """整体数据合理性校验：close>0、无 NaN close、每标的最新日期上报。"""
    ok = True
    messages: list[str] = []
    if frame.empty:
        return False, ["无任何行情数据，拒绝写盘"]
    if frame["close"].isna().any():
        ok = False
        messages.append(f"存在 {int(frame['close'].isna().sum())} 行 close 为空")
    if (frame["close"] <= 0).any():
        ok = False
        messages.append(f"存在 {int((frame['close'] <= 0).sum())} 行 close<=0")
    latest = (
        frame.sort_values("trade_date").groupby("ts_code")["trade_date"].max().to_dict()
    )
    for code in sorted(new_codes):
        messages.append(f"  {code} 最新 = {latest.get(code, '缺失')}")
    return ok, messages


def _read_a_pool_codes() -> list[str]:
    """读 A 侧资产池 active 标的完整代码（如 562800.SH），作为默认白名单。

    只读 A config/asset_pool.csv，不改任何 A 侧状态；A 池不可读时返回空列表，
    由 main() 明确报错（避免静默回退到全量标的）。
    """
    from qteasy_research.reference.config import SYSTEM_A_ASSET_POOL

    path = Path(SYSTEM_A_ASSET_POOL)
    if not path.exists():
        print(f"[错误] A 资产池不存在：{path}")
        return []
    frame = pd.read_csv(path)
    if not {"code", "status"}.issubset(frame.columns):
        print(f"[错误] A 资产池缺少 code/status 字段：{path}")
        return []
    active = frame.loc[frame["status"].astype(str).str.strip().str.lower() == "active"]
    codes: set[str] = set()
    for _, row in active.iterrows():
        code6 = str(row.get("code", "")).strip()
        exch = str(row.get("exchange", "")).strip().upper()
        if code6.isdigit() and exch:
            codes.add(f"{code6}.{exch}")
    if not codes:
        print(f"[错误] A 资产池 {path} 无 active 标的")
        return []
    return sorted(codes)


def _check_d_health(provider: DProvider) -> bool:
    """D 中台前置健康检查（/health），不可用立即返回 False，不逐标的碰运气。"""
    import requests
    try:
        response = requests.get(
            f"{provider.base_url}/health",
            headers={"X-API-Key": provider.api_key} if provider.api_key else {},
            timeout=5,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        print(f"[错误] D 数据中台不可用，无法刷新行情：{type(exc).__name__}: {exc}")
        print("       请先启动 D 中台 serve_api（@8765，计划任务 BernD_ServeAPI / D 侧启动脚本）。")
        return False


def main(args: argparse.Namespace) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    data_root = args.data_root
    csv_path = data_root / "fund_daily.csv"
    existing = _read_existing(data_root)
    if existing.empty:
        print(f"[错误] 本地 {csv_path} 不存在或为空；首次建立请先用 qteasy/人工导入基础数据。")
        return 2

    # 默认白名单 = A 池 active 标的（读 A config/asset_pool.csv，含网格 5 标的），
    # 只补发包实际消费的核心标的，避免对全量 2116 标的（整个 ETF 宇宙）逐一拉取。
    # --codes 可显式覆盖（如 --all 全量场景）。
    if args.codes:
        wanted = [c.strip().upper() for c in args.codes.split(",") if c.strip()]
        all_codes = sorted({str(c).strip() for c in existing["ts_code"].str.strip().unique() if c in wanted})
    else:
        all_codes = _read_a_pool_codes()
    if not all_codes:
        print("[错误] 没有可处理的标的：请用 --codes 显式指定，或确认 A 资产池可读（config/asset_pool.csv）。")
        return 2

    latest_global = existing["trade_date"].max()
    start_date = args.start_date or (datetime.strptime(latest_global, "%Y-%m-%d") - timedelta(days=7)).date().isoformat()
    print(f"[开始] 标的数={len(all_codes)} 拉取起点={start_date} 现有最新={latest_global} 模式={'dry-run' if args.dry else '写盘'}")

    provider = DProvider()
    print(f"[数据源] {provider.base_url}（D_API_KEY={'已配置' if provider.api_key else '未配置，可能被拒'}）")
    if not _check_d_health(provider):
        return 1

    # 各标的本地最新收盘价 → 做新增行 pre_close 的衔接 seed
    latest_close = (
        existing.sort_values("trade_date")
        .groupby("ts_code")
        .tail(1)
        .set_index("ts_code")["close"]
        .to_dict()
    )
    new_frames: list[pd.DataFrame] = []
    for full_code in all_codes:
        code6 = full_code.split(".", 1)[0]
        if not code6.isdigit():
            continue
        new_frames.append(
            _pull_from_d(provider, code6, full_code, start_date, latest_close.get(full_code))
        )
    new_rows = _dedupe(pd.concat(new_frames, ignore_index=True)) if new_frames else pd.DataFrame()
    print(f"[拉取] 新增 {len(new_rows)} 行（{len(all_codes)} 个标的）")

    # 衔接校验（对将新增行）
    continuity_warnings = _check_continuity(existing, new_rows)

    # 合并
    merged = _dedupe(pd.concat([existing, new_rows], ignore_index=True))
    ok, messages = _validate(merged, all_codes)
    print("[校验]")
    for message in messages:
        print(message)
    if continuity_warnings:
        print("[衔接告警]")
        for warning in continuity_warnings:
            print(f"  [告警] {warning}")
        ok = False  # 衔接异常视为脏数据风险，阻断写盘/发包

    if not ok:
        print("[结果] 校验未通过，拒绝写盘（未发包）。")
        return 1

    if args.dry:
        print(f"[dry-run] 将写入 {len(merged)} 行（新增 {len(new_rows)} 行）到 {csv_path}")
        return 0

    # 备份 + 写盘
    if not existing.empty:
        backup_path = BACKUP_DIR / f"fund_daily.backup_{date.today().strftime('%Y%m%d')}.csv"
        shutil.copy2(csv_path, backup_path)
        print(f"[备份] {backup_path}")
    merged.to_csv(csv_path, index=False, encoding="utf-8")
    print(f"[写盘] {csv_path}：{len(existing)} 行 → {len(merged)} 行（新增 {len(merged) - len(existing)} 行）")

    if args.publish:
        print("[发包] 调用 run_reference_pipeline.py --real ...")
        result = subprocess.run(
            [sys.executable, "scripts/run_reference_pipeline.py", "--real"],
            cwd=PROJECT_ROOT,
        )
        if result.returncode != 0:
            print(f"[发包] 失败 returncode={result.returncode}")
            return result.returncode
        print("[发包] 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main(parse_args()))
