"""云端契约：消费边的 edge-report.v1，产出 ai.report.v1。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RISK = Literal["info", "watch", "alert"]
RAW_KEYS = {"user_id", "userId", "user_name", "userName", "subject",
            "values", "raw", "raw_samples", "samples", "records"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ReportDevice(Strict):
    device_id: str
    scene: str
    modules: list[int] = Field(default_factory=list)
    last_seen: int | None = None
    online: bool = False


class DailyAggregate(Strict):
    device_id: str
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    metric: str
    n_subjects: int
    n_samples: int
    mean: float
    slope: float
    z: float
    k: int
    # 端/边带来的元信息；缺失时云端按 metric 名兜底
    module_id: int = 0
    label: str = ""
    unit: str = ""
    table: str = "samples"


class ReportAdvice(Strict):
    device_id: str
    text: str
    risk: RISK
    confidence: float
    source: str
    created_at: int


class WordFreq(Strict):
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    word: str
    count: int


class ReportPrivacy(Strict):
    k_min: int = 5
    suppressed_buckets: int = 0
    raw_rejected: int = 0
    subjects_total: int = 0


class Period(Strict):
    from_: int = Field(alias="from")
    to: int


class EdgeReport(Strict):
    schema_: Literal["orpi.edge.report.v1"] = Field(alias="schema")
    report_id: str = Field(min_length=8, max_length=64)
    edge_id: str
    edge_name: str = ""
    sent_at: int
    period: Period
    devices: list[ReportDevice] = Field(default_factory=list, max_length=64)
    telemetry_daily: list[DailyAggregate] = Field(default_factory=list, max_length=4096)
    advice: list[ReportAdvice] = Field(default_factory=list, max_length=256)
    word_freq: list[WordFreq] = Field(default_factory=list, max_length=512)
    privacy: ReportPrivacy

    @model_validator(mode="after")
    def _no_low_k(self):
        low = [r for r in self.telemetry_daily if r.k < 5]
        if low:
            raise ValueError(f"k<5 的聚合不允许上云（{len(low)} 行）")
        return self


# ---------------------------------------------------------------- AI 报告

class Finding(Strict):
    label: str
    detail: str
    risk: RISK = "info"


class AiReportContent(Strict):
    schema_: Literal["ai.report.v1"] = Field(alias="schema", default="ai.report.v1")
    task: str = "weekly_narrative"
    scope: Literal["fleet", "edge", "device"]
    scope_id: str | None = None
    period: dict[str, str]
    headline: str = Field(min_length=1, max_length=80)
    narrative: str = Field(min_length=1, max_length=1200)
    findings: list[Finding] = Field(default_factory=list, max_length=8)
    actions: list[str] = Field(default_factory=list, max_length=5)
    confidence: Literal["high", "low"] = "high"
