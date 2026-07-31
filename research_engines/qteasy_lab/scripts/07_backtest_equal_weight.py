# -*- coding: utf-8 -*-

"""
qteasy第一个组合回测：5只ETF月度等权配置。

主要用途：
1. 验证qteasy是否能够读取本地fund_daily数据；
2. 验证多只ETF是否能够执行组合回测；
3. 验证月度调仓、手续费、交易批量和交易日志；
4. 建立后续风险平价、波动率倒数等策略的比较基准。

注意：
本策略只是基础测试策略，不代表正式投资建议。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import qteasy as qt


# ============================================================
# 一、回测资产池
# ============================================================

# 第一轮使用历史数据相对较长的5只ETF。
# 暂时不加入上市时间太短的ETF，避免共同回测区间过短。
PILOT_CODES = [
    "518880.SH",  # 黄金ETF华安
    "159941.SZ",  # 纳指ETF广发
    "513050.SH",  # 中概互联网ETF
    "513520.SH",  # 日经ETF华夏
    "512890.SH",  # 红利低波ETF华泰柏瑞
]


# ============================================================
# 二、回测基础参数
# ============================================================

# 初始投入资金，单位：人民币元
INITIAL_CASH = 100_000

# 5只ETF的共同历史数据开始于2019年6月以后。
# 回测从2019年8月开始，预留一定历史数据作为策略预热期。
BACKTEST_START = "20190801"

# 暂时按照净佣金万1.6计算买入费用
BUY_FEE_RATE = 0.00016

# 暂时按照净佣金万1.6计算卖出费用
SELL_FEE_RATE = 0.00016

# 场内ETF一般以100份作为最小交易单位
TRADE_BATCH_SIZE = 100


# ============================================================
# 三、自定义月度等权策略
# ============================================================

class MonthlyEqualWeightStrategy(qt.GeneralStg):
    """
    月度等权目标仓位策略。

    每次策略运行时：
    1. 检查各ETF最新收盘价是否有效；
    2. 对有有效价格的ETF平均分配组合权重；
    3. 输出PT型目标仓位信号。

    例如5只ETF均有效时，每只目标权重为20%。
    """

    def __init__(self, **kwargs) -> None:
        """定义策略名称、说明和需要使用的历史数据。"""

        super().__init__(
            # 策略名称
            name="MONTHLY_EQUAL_WEIGHT",

            # 策略说明
            description="5只ETF月度等权目标仓位策略",

            # 策略需要最近2个交易日的基金收盘价
            data_types=[
                qt.StgData(
                    "close",

                    # 使用日线行情
                    freq="d",

                    # FD表示基金，包括ETF和LOF
                    asset_type="FD",

                    # 每次策略运行读取最近2个交易日
                    window_length=2,
                )
            ],

            **kwargs,
        )

    def realize(self) -> np.ndarray:
        """
        生成所有ETF的等权目标仓位。

        Returns
        -------
        np.ndarray
            每只ETF对应的目标权重。
            所有有效ETF的目标权重之和为1。
        """

        # 获取基金日线收盘价。
        # GeneralStg接收到的数据通常为：
        # 行 = 历史交易日，列 = 资产池中的ETF。
        close_data = self.get_data("close_FD_d")

        # 转换为numpy数组，便于进行有效值检查
        close_array = np.asarray(
            close_data,
            dtype=float,
        )

        # 获取当前数据窗口最后一个交易日的收盘价
        latest_close = close_array[-1]

        # 有效价格必须同时满足：
        # 1. 不是NaN或无穷值；
        # 2. 收盘价大于0。
        valid_mask = (
            np.isfinite(latest_close)
            & (latest_close > 0)
        )

        # 默认所有ETF目标权重均为0
        target_weights = np.zeros(
            latest_close.shape[0],
            dtype=float,
        )

        # 统计当前有有效价格的ETF数量
        valid_count = int(valid_mask.sum())

        # 只对有有效价格的ETF进行等权配置
        if valid_count > 0:
            target_weights[valid_mask] = (
                1.0 / valid_count
            )

        return target_weights


# ============================================================
# 四、本地数据读取和检查
# ============================================================

def load_fund_daily() -> pd.DataFrame:
    """
    从qteasy本地数据源读取基金日线数据。

    Returns
    -------
    pd.DataFrame
        包含ts_code、trade_date和close字段的基金日线数据。
    """

    # 从本地CSV数据源读取fund_daily表
    fund_data = qt.QT_DATA_SOURCE.read_table_data(
        "fund_daily"
    )

    # 数据表为空时立即停止
    if fund_data is None or fund_data.empty:
        raise RuntimeError(
            "fund_daily表为空，请先下载ETF历史行情。"
        )

    # qteasy读取后，ts_code和trade_date可能位于索引中。
    # reset_index()将其恢复为普通列。
    fund_data = fund_data.reset_index()

    # 定义回测必需字段
    required_columns = {
        "ts_code",
        "trade_date",
        "close",
    }

    # 检查是否缺少必需字段
    missing_columns = required_columns.difference(
        fund_data.columns
    )

    if missing_columns:
        raise RuntimeError(
            "fund_daily缺少必要字段："
            f"{sorted(missing_columns)}"
        )

    # 将交易日期转换为统一的时间格式
    fund_data["trade_date"] = pd.to_datetime(
        fund_data["trade_date"]
    )

    return fund_data


def determine_backtest_end(
    fund_data: pd.DataFrame,
) -> str:
    """
    确定5只ETF共同拥有行情的最后日期。

    Parameters
    ----------
    fund_data:
        本地基金日线数据。

    Returns
    -------
    str
        YYYYMMDD格式的回测结束日期。
    """

    end_dates: list[pd.Timestamp] = []

    for symbol in PILOT_CODES:
        # 提取单只ETF的数据
        symbol_data = fund_data[
            fund_data["ts_code"] == symbol
        ]

        # 任意ETF没有数据时停止运行
        if symbol_data.empty:
            raise RuntimeError(
                f"{symbol}在fund_daily中没有数据。"
            )

        # 保存该ETF的最后行情日期
        end_dates.append(
            symbol_data["trade_date"].max()
        )

    # 使用所有ETF结束日期中的最小值，
    # 保证回测结束日所有ETF均有数据。
    common_end_date = min(end_dates)

    return common_end_date.strftime("%Y%m%d")


def validate_backtest_data(
    fund_data: pd.DataFrame,
    backtest_end: str,
) -> None:
    """
    检查每只ETF在回测区间内的数据数量和收盘价缺失情况。

    Parameters
    ----------
    fund_data:
        本地基金日线数据。
    backtest_end:
        YYYYMMDD格式的回测结束日期。
    """

    start_date = pd.to_datetime(BACKTEST_START)
    end_date = pd.to_datetime(backtest_end)

    print("\n回测区间内的数据检查：")

    for symbol in PILOT_CODES:
        # 提取该ETF在回测区间内的数据
        symbol_data = fund_data[
            (fund_data["ts_code"] == symbol)
            & (fund_data["trade_date"] >= start_date)
            & (fund_data["trade_date"] <= end_date)
        ]

        # 统计收盘价缺失数量
        missing_close = int(
            symbol_data["close"].isna().sum()
        )

        print(
            f"- {symbol}："
            f"{len(symbol_data)}行，"
            f"收盘价缺失{missing_close}行"
        )

        if symbol_data.empty:
            raise RuntimeError(
                f"{symbol}在回测区间内没有数据。"
            )

        if missing_close > 0:
            raise RuntimeError(
                f"{symbol}在回测区间内存在"
                f"{missing_close}个收盘价缺失值。"
            )


# ============================================================
# 五、创建qteasy策略容器
# ============================================================

def create_equal_weight_operator() -> qt.Operator:
    """
    创建并返回月度等权策略容器。

    Returns
    -------
    qt.Operator
        包含月度等权策略的Operator对象。
    """

    # Operator是qteasy的策略容器。
    # PT表示策略信号是目标持仓比例。
    operator = qt.Operator(
        strategies=[
            MonthlyEqualWeightStrategy()
        ],

        # PT：Position Target，目标持仓比例信号
        signal_type="PT",

        # ME：每个月月末运行一次策略
        run_freq="ME",

        # 在月末收盘时生成新的目标仓位
        run_timing="close",
    )

    return operator


# ============================================================
# 六、执行回测
# ============================================================

def main() -> None:
    """执行数据检查、策略创建和历史回测。"""

    print("=" * 70)
    print("qteasy：5只ETF月度等权组合回测")
    print("=" * 70)

    # 读取本地基金行情
    fund_data = load_fund_daily()

    # 自动确定所有ETF共同拥有数据的最后日期
    backtest_end = determine_backtest_end(
        fund_data
    )

    print(f"回测资产池：{PILOT_CODES}")
    print(
        f"回测区间："
        f"{BACKTEST_START} 至 {backtest_end}"
    )
    print(
        f"初始资金："
        f"{INITIAL_CASH:,.2f}元"
    )
    print(
        f"买入费率：{BUY_FEE_RATE:.5%}"
    )
    print(
        f"卖出费率：{SELL_FEE_RATE:.5%}"
    )

    # 检查回测区间内的数据完整性
    validate_backtest_data(
        fund_data=fund_data,
        backtest_end=backtest_end,
    )

    # 显示回测所依赖的数据表状态
    # fund_basic用于识别ETF属于基金资产FD
    # fund_daily用于提供ETF历史行情
    print("\n回测所需数据表状态：")

    data_overview = qt.get_table_overview(
        tables=[
            "fund_basic",
            "fund_daily",
        ]
    )

    print(data_overview.to_string())




    # 创建自定义月度等权策略
    operator = create_equal_weight_operator()

    print("\n策略配置信息：")
    operator.info()

    print("\n开始运行历史回测……")

    # qteasy官方回测入口。
    # mode=1表示历史回测模式。
    result = qt.run(
        operator,

        # 历史回测模式
        mode=1,

        # 回测资产池
        asset_pool=PILOT_CODES,

        # FD表示基金类型
        # 明确指定组合中的交易标的是场内基金
        asset_type="FD",

        # 使用沪深300指数作为组合业绩比较基准
        # 沪深300行情保存在index_daily表中
        benchmark_asset="000300.SH",

        # 初始投入资金
        invest_cash_amounts=[
            INITIAL_CASH
        ],

        # 回测起止时间
        invest_start=BACKTEST_START,
        invest_end=backtest_end,

        # 买入和卖出费率
        cost_rate_buy=BUY_FEE_RATE,
        cost_rate_sell=SELL_FEE_RATE,

        # ETF最小买入和卖出交易单位
        trade_batch_size=TRADE_BATCH_SIZE,
        sell_batch_size=TRADE_BATCH_SIZE,

        # 输出文字回测报告
        report=True,

        # 第一轮关闭图形结果，降低排错复杂度
        visual=False,

        # 保存策略运行和交易明细日志
        trade_log=True,
    )

    print("\n" + "=" * 70)
    print("回测执行完成")
    print("=" * 70)
    print(f"返回结果类型：{type(result)}")


# ============================================================
# 七、脚本入口和异常提示
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        # 显示中文错误摘要，同时保留完整Traceback。
        print(
            "\n回测脚本执行失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise