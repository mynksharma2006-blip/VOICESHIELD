from pydantic import BaseModel


class AnalysisResponse(BaseModel):
    label: str
    reason: str
    clip_duration_s: float
    model_id: str
    score_raw: float | None
    risk_percentage: float | None = None
    risk_percentage_reason: str
    warnings: list[str]
    limitation: str


class DemoExample(BaseModel):
    id: str
    label: str
    description: str


class PerformanceResponse(BaseModel):
    available: bool
    model: str | None
    threshold: float | None
    phone_stress_description: str | None
    reports: list[dict[str, int | float | str]]
    message: str
