from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from job_apply_agent.apply.engine import ApplicationEngine
from job_apply_agent.discovery.company_discovery import DiscoveryService
from job_apply_agent.models import JobPosting, RankedJob
from job_apply_agent.ranking.scorer import JobRanker


class JobApplyOrchestrator:
    def __init__(
        self,
        profile: dict,
        targets: dict,
        output_dir: Path,
        headless: bool,
        auto_submit: bool,
        session_dir: Path | None = None,
        max_role_seconds: int = 210,
    ) -> None:
        self.profile = profile
        self.discovery = DiscoveryService(targets_config=targets)
        self.ranker = JobRanker()
        self.apply_engine = ApplicationEngine(
            profile=profile,
            output_dir=output_dir,
            headless=headless,
            auto_submit=auto_submit,
            session_dir=session_dir,
            max_role_seconds=max_role_seconds,
        )

    async def run(
        self,
        companies: list[str],
        tags: list[str],
        max_jobs: int,
        max_per_company: int,
        dry_run: bool,
    ) -> dict:
        discovered_jobs = self.discovery.discover_jobs(
            companies=companies,
            max_per_company=max_per_company,
        )

        ranked_jobs = self.ranker.rank(
            jobs=discovered_jobs,
            tags=tags,
            preferences=self.profile.get("preferences", {}),
        )

        selected_jobs = ranked_jobs[:max_jobs]

        if dry_run:
            return {
                "discovered_jobs": discovered_jobs,
                "selected_jobs": selected_jobs,
                "results": [],
                "results_file": None,
            }

        results, results_file = await self.apply_engine.apply(selected_jobs)
        return {
            "discovered_jobs": discovered_jobs,
            "selected_jobs": selected_jobs,
            "results": results,
            "results_file": results_file,
        }

    @staticmethod
    def _posting_from_url(url: str) -> JobPosting:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        company = "Morgan Stanley" if "morganstanley" in host or "ms.wd5.myworkdayjobs.com" in host else host

        slug = parsed.path.rsplit("/", 1)[-1]
        slug = re.sub(r"[_-]+", " ", slug).strip() or "Application"
        title = re.sub(r"\s+", " ", slug).strip()
        job_id = re.sub(r"[^a-zA-Z0-9]+", "", slug.lower())[:24] or "manual"

        return JobPosting(
            id=job_id,
            company=company,
            title=title,
            location="",
            url=url,
            source="manual",
        )

    async def run_job_urls(self, job_urls: list[str]) -> dict:
        postings = [self._posting_from_url(url) for url in job_urls]
        selected_jobs = [RankedJob(job=job, score=100.0, reasons=["manual resume target"]) for job in postings]
        results, results_file = await self.apply_engine.apply(selected_jobs)
        return {
            "discovered_jobs": postings,
            "selected_jobs": selected_jobs,
            "results": results,
            "results_file": results_file,
        }
