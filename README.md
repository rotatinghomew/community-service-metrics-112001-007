# 服务区域调整前后覆盖分析

用于比较区域边界变化、居民位置精度和服务承办关系对覆盖指标的影响。所有位置为合成片区编码。

`fixtures/` 保存 UTF-8 合成业务资料，字段、关系和时间含义见 `DATA.md`。项目使用 Python 3.11 标准库；资料结构检查可用 `python3 -m unittest discover -s tests -v` 运行。
