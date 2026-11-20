# 服务区域调整前后覆盖分析：资料说明

用于比较区域边界变化、居民位置精度和服务承办关系对覆盖指标的影响。所有位置为合成片区编码。

每个 JSON 文件均为 UTF-8 编码。空字符串表示来源没有给出该事实，不等同于零、否或业务终态。标识仅在本项目资料内有意义。

## `boundaries.json`

字段：id, grid, version, valid_from, valid_to, areas。共 4 条合成记录。

## `population.json`

字段：area, period, count, source_version。共 5 条合成记录。

## `requests.json`

字段：id, area, location_precision, occurred_at, serving_grid。共 5 条合成记录。

## `transfers.json`

字段：id, area, from_grid, to_grid, effective_at, accepted。共 2 条合成记录。

## 关系与口径提示

boundaries 按有效区间定义网格覆盖；requests.area 对应人口片区，serving_grid 记录实际承办；location_precision=range 表示归属并不唯一。transfers 的待确认移交不等于责任已转移。
