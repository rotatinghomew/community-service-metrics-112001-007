"""领域模型：四类来源记录的类型化表示与空值约定。

来源中的空字符串表示“来源未给出该事实”，模型中统一表示为 None，
绝不等同于 0、False 或业务终态。所有记录只读，分析过程不改写来源。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


def parse_date(raw: object) -> date | None:
    """解析 ISO 日期；空值返回 None（来源未给出）。"""
    if raw is None or str(raw).strip() == "":
        return None
    return date.fromisoformat(str(raw).strip())


def parse_text(raw: object) -> str | None:
    """解析文本；空字符串返回 None（来源未给出该事实）。"""
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw).strip()


def parse_count(raw: object) -> int | None:
    """解析计数；空值返回 None。None 是“未知”，不是 0。"""
    if raw is None or str(raw).strip() == "":
        return None
    return int(raw)


@dataclass(frozen=True)
class BoundaryRecord:
    """网格边界版本。

    有效期为半开区间 [valid_from, valid_to)。valid_to 为空按“持续有效”
    处理（区间开放），valid_from 为空按“自古有效”处理；两者都会在
    数据质量清单中标注，因为这是来源缺事实而非业务结论。
    """

    id: str
    grid: str
    version: str
    valid_from: date | None
    valid_to: date | None
    areas: tuple[str, ...]

    @classmethod
    def from_raw(cls, raw: dict) -> "BoundaryRecord":
        areas = tuple(
            a.strip() for a in str(raw.get("areas") or "").split(";") if a.strip()
        )
        return cls(
            id=str(raw["id"]),
            grid=str(raw["grid"]),
            version=str(raw["version"]),
            valid_from=parse_date(raw.get("valid_from")),
            valid_to=parse_date(raw.get("valid_to")),
            areas=areas,
        )

    def covers(self, day: date) -> bool:
        start = self.valid_from or date.min
        end = self.valid_to or date.max
        return start <= day < end

    def overlaps(self, start: date, end: date) -> bool:
        """有效期是否与 [start, end]（闭区间）相交。"""
        s = self.valid_from or date.min
        e = self.valid_to or date.max
        return s <= end and e > start


@dataclass(frozen=True)
class PopulationRecord:
    """人口基数：count 为 None 表示来源未给出，不等于 0，更不等于无人居住。"""

    area: str
    period: str
    count: int | None
    source_version: str | None

    @classmethod
    def from_raw(cls, raw: dict) -> "PopulationRecord":
        return cls(
            area=str(raw["area"]),
            period=str(raw["period"]),
            count=parse_count(raw.get("count")),
            source_version=parse_text(raw.get("source_version")),
        )


@dataclass(frozen=True)
class RequestRecord:
    """居民诉求。area 为匿名居住片区；serving_grid 为实际承办网格（事实记录）。

    location_precision=range 表示地址只能定位到某个范围，归属并不唯一，
    不允许当作精确网格处理。
    """

    id: str
    area: str | None
    precision: str | None
    occurred_at: date | None
    serving_grid: str | None

    @classmethod
    def from_raw(cls, raw: dict) -> "RequestRecord":
        return cls(
            id=str(raw["id"]),
            area=parse_text(raw.get("area")),
            precision=parse_text(raw.get("location_precision")),
            occurred_at=parse_date(raw.get("occurred_at")),
            serving_grid=parse_text(raw.get("serving_grid")),
        )


@dataclass(frozen=True)
class TransferRecord:
    """网格责任移交。accepted 保留来源原值；只有明确的 yes 才视为责任已转移。

    待确认（pending）或其它任何值都不等于责任已转移。
    """

    id: str
    area: str
    from_grid: str
    to_grid: str
    effective_at: date | None
    accepted: str | None

    @classmethod
    def from_raw(cls, raw: dict) -> "TransferRecord":
        return cls(
            id=str(raw["id"]),
            area=str(raw["area"]),
            from_grid=str(raw["from_grid"]),
            to_grid=str(raw["to_grid"]),
            effective_at=parse_date(raw.get("effective_at")),
            accepted=parse_text(raw.get("accepted")),
        )

    @property
    def is_accepted(self) -> bool:
        return (self.accepted or "").strip().lower() == "yes"
