# 测试与验证

## 全量离线测试

必须从项目目录执行：

```powershell
cd D:\Project DPS\research_engines\qteasy_lab
.\.venv\Scripts\python.exe -B -m unittest discover `
  -s "D:\Project DPS\tests" -q
```

当前结果：

```text
Ran 60 tests
OK
```

如果从 `D:\Project DPS` 上级目录执行，可能出现：

```text
ModuleNotFoundError: No module named 'qteasy_research'
```

这表示工作目录没有把 `qteasy_lab` 放入 Python 模块路径。

## GlobalEtfEngine fixture 测试

`tests/test_global_etf_engine.py` 包含 5 项测试：

1. DGS30 存在时优先使用 DGS30；
2. DGS30 缺失时回退 DGS10；
3. 没有 APPROVED 宏观规则时不产生最终分数；
4. 目标日期之后的数据不会被读取；
5. 指向 A 股 `factor_values` 目录时直接拒绝运行。

这些测试使用离线 fixture，不代表真实 FRED 数据已经连接成功。

## 典型运行方式

```python
from qteasy_research.core import GlobalEtfEngine

engine = GlobalEtfEngine(
    data_root="data",
    store_root="research_store",
)

result = engine.calculate_scores(
    target_date="2026-08-03",
    assets=["SPY", "TLT", "GLD"],
    persist=False,
)
```

FRED 数据缺失或没有 APPROVED 规则时，预期为：

```text
status = PARTIAL
macro_modifier = null
final_score = null
```
