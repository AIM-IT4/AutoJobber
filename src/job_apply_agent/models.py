from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class JobPosting:
    id: str
    company: str
    title: str
    location: str
    url: str
    description: str = ""
    source: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RankedJob:
    job: JobPosting
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ApplicationResult:
    job_id: str
    job_url: str
    company: str
    title: str
    status: str
    message: str = ""
    screenshot_path: str = ""
