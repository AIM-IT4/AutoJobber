from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from job_apply_agent.discovery.providers import (
    extract_eightfold_hint,
    extract_greenhouse_token,
    extract_lever_company_slug,
    extract_oracle_ce_hint,
    extract_talentbrew_hint,
    fetch_eightfold_jobs,
    fetch_greenhouse_jobs,
    fetch_lever_jobs,
    fetch_oracle_ce_jobs,
    fetch_talentbrew_jobs,
    fetch_workday_jobs,
)
from job_apply_agent.models import JobPosting

logger = logging.getLogger(__name__)


def _decode_search_link(href: str) -> str:
    if not href:
        return ""
    if "duckduckgo.com/l/?" in href:
        parsed = urlparse(href)
        raw_target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(raw_target)
    return href


def _duckduckgo_search_links(company: str, session: requests.Session) -> list[str]:
    queries = [
        f"{company} careers workday",
        f"{company} careers greenhouse",
        f"{company} careers lever",
        f"{company} careers eightfold",
    ]

    found: list[str] = []
    for query in queries:
        response = session.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            timeout=25,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.select("a.result__a"):
            href = _decode_search_link(anchor.get("href", "").strip())
            if href:
                found.append(href)
    return found


def discover_provider_hints(company: str, session: requests.Session) -> list[dict[str, str]]:
    links = _duckduckgo_search_links(company, session)

    hints: list[dict[str, str]] = []
    seen = set()

    for link in links:
        lower = link.lower()
        if "greenhouse.io" in lower:
            token = extract_greenhouse_token(link)
            if token:
                key = ("greenhouse", token)
                if key not in seen:
                    hints.append({"provider": "greenhouse", "board_token": token})
                    seen.add(key)

        if "lever.co" in lower:
            slug = extract_lever_company_slug(link)
            if slug:
                key = ("lever", slug)
                if key not in seen:
                    hints.append({"provider": "lever", "company_slug": slug})
                    seen.add(key)

        if "myworkdayjobs.com" in lower or "/wday/cxs/" in lower:
            key = ("workday", link)
            if key not in seen:
                hints.append({"provider": "workday", "careers_url": link})
                seen.add(key)

        if "eightfold.ai" in lower:
            parsed = extract_eightfold_hint(link)
            if parsed:
                key = ("eightfold", parsed.get("domain", parsed.get("base_url", "")))
                if key not in seen:
                    hints.append(parsed)
                    seen.add(key)

        if "search.jobs." in lower or "/search-jobs" in lower or "/job/" in lower:
            parsed = extract_talentbrew_hint(link)
            if parsed:
                key = ("talentbrew", parsed.get("base_url", link))
                if key not in seen:
                    hints.append(parsed)
                    seen.add(key)

        if "oraclecloud.com" in lower and "candidateexperience" in lower:
            parsed = extract_oracle_ce_hint(link)
            if parsed:
                key = ("oracle_ce", parsed.get("careers_url", link))
                if key not in seen:
                    hints.append(parsed)
                    seen.add(key)

    return hints


class DiscoveryService:
    def __init__(self, targets_config: dict | None = None) -> None:
        self.targets_config = targets_config or {}

    def _configured_hint_for(self, company: str) -> dict[str, Any] | None:
        raw_companies = self.targets_config.get("companies", [])

        for item in raw_companies:
            name = str(item.get("name", "")).strip().lower()
            if name == company.strip().lower():
                return {str(k): v for k, v in item.items() if v is not None}

        return None

    def _jobs_from_hint(
        self,
        company: str,
        hint: dict[str, Any],
        session: requests.Session,
        max_per_company: int,
    ) -> list[JobPosting]:
        provider = str(hint.get("provider", "")).lower().strip()

        if provider == "greenhouse":
            token = str(hint.get("board_token", ""))
            return fetch_greenhouse_jobs(token, company, session=session)[:max_per_company]

        if provider == "lever":
            slug = str(hint.get("company_slug", ""))
            return fetch_lever_jobs(slug, company, session=session)[:max_per_company]

        if provider == "workday":
            careers_url = str(hint.get("careers_url", ""))
            query = str(hint.get("query", "")).strip()
            location = str(hint.get("location", "")).strip()
            return fetch_workday_jobs(
                careers_url,
                company,
                session=session,
                max_jobs=max_per_company,
                query=query,
                location=location,
            )

        if provider == "eightfold":
            domain = str(hint.get("domain", "")).strip()
            base_url = str(hint.get("base_url", "https://app.eightfold.ai")).strip()
            query = str(hint.get("query", "")).strip()
            location = str(hint.get("location", "")).strip()
            filters = hint.get("filters")
            return fetch_eightfold_jobs(
                domain=domain,
                company=company,
                session=session,
                base_url=base_url,
                query=query,
                location=location,
                filters=filters,
                max_jobs=max_per_company,
            )

        if provider == "oracle_ce":
            careers_url = str(hint.get("careers_url", ""))
            query = str(hint.get("query", "")).strip()
            location = str(hint.get("location", "")).strip()
            return fetch_oracle_ce_jobs(
                careers_url,
                company,
                session=session,
                max_jobs=max_per_company,
                query=query,
                location=location,
            )

        if provider == "talentbrew":
            base_url = str(hint.get("base_url", "")).strip()
            query = str(hint.get("query", "")).strip()
            location = str(hint.get("location", "")).strip()
            org_ids = str(hint.get("org_ids", "")).strip()
            return fetch_talentbrew_jobs(
                base_url=base_url,
                company=company,
                session=session,
                query=query,
                location=location,
                org_ids=org_ids,
                max_jobs=max_per_company,
            )

        return []

    def discover_jobs(self, companies: list[str], max_per_company: int = 60) -> list[JobPosting]:
        session = requests.Session()
        collected: list[JobPosting] = []

        for company in companies:
            logger.info("Discovering jobs for %s", company)

            hints: list[dict[str, str]] = []
            configured = self._configured_hint_for(company)
            if configured:
                hints.append(configured)
            else:
                try:
                    hints.extend(discover_provider_hints(company, session=session))
                except Exception as exc:
                    logger.warning("Search hint discovery failed for %s: %s", company, exc)

            company_jobs: list[JobPosting] = []
            for hint in hints:
                try:
                    provider_jobs = self._jobs_from_hint(
                        company,
                        hint,
                        session=session,
                        max_per_company=max_per_company,
                    )
                    if provider_jobs:
                        company_jobs.extend(provider_jobs)
                except Exception as exc:
                    logger.warning(
                        "Provider fetch failed for %s using %s: %s",
                        company,
                        hint,
                        exc,
                    )

            unique_by_url: dict[str, JobPosting] = {}
            for job in company_jobs:
                if job.url and job.url not in unique_by_url:
                    unique_by_url[job.url] = job

            selected = list(unique_by_url.values())[:max_per_company]
            logger.info("Found %s jobs for %s", len(selected), company)
            collected.extend(selected)

        return collected
