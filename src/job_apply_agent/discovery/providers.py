from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from job_apply_agent.models import JobPosting

DEFAULT_TIMEOUT = 25
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
WORKDAY_LOCALE_PATTERN = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def _build_session(session: requests.Session | None = None) -> requests.Session:
    if session:
        return session
    local = requests.Session()
    local.headers.update({"User-Agent": USER_AGENT})
    return local


def _strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    return " ".join(soup.stripped_strings)


def extract_greenhouse_token(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]

    if "boards.greenhouse.io" in host or "job-boards.greenhouse.io" in host:
        return parts[0] if parts else ""

    if host.endswith(".greenhouse.io") and not host.startswith("boards."):
        return host.split(".")[0]

    return ""


def extract_lever_company_slug(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]

    if "jobs.lever.co" in host:
        return parts[0] if parts else ""

    if host.endswith(".lever.co"):
        return host.split(".")[0]

    return ""


def extract_eightfold_hint(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower().strip()

    if "eightfold.ai" not in host:
        return {}

    query = parse_qs(parsed.query)
    domain = (query.get("domain") or [""])[0].strip()
    location = (query.get("location") or [""])[0].strip()

    hint = {
        "provider": "eightfold",
        "base_url": f"{parsed.scheme}://{parsed.netloc}",
    }
    if domain:
        hint["domain"] = domain
    if location:
        hint["location"] = location
    return hint


def extract_talentbrew_hint(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower().strip()
    path = (parsed.path or "").lower()
    if not host:
        return {}

    is_talentbrew_host = host.startswith("search.jobs.") or "talentbrew.com" in host
    is_talentbrew_path = "/search-jobs" in path or "/job/" in path
    if not is_talentbrew_host and not is_talentbrew_path:
        return {}

    base_url = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return {
        "provider": "talentbrew",
        "base_url": base_url,
    }


def extract_oracle_ce_hint(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower().strip()
    path = parsed.path.strip()
    lower_path = path.lower()
    if "oraclecloud.com" not in host or "candidateexperience" not in lower_path:
        return {}

    path_parts = [part for part in path.split("/") if part]
    locale = "en"
    site_number = ""
    for idx, part in enumerate(path_parts):
        if part.lower() == "candidateexperience":
            if idx + 1 < len(path_parts):
                locale_candidate = path_parts[idx + 1]
                if re.match(r"^[a-z]{2}(?:-[A-Z]{2})?$", locale_candidate):
                    locale = locale_candidate
            break
    for idx, part in enumerate(path_parts):
        if part.lower() == "sites" and idx + 1 < len(path_parts):
            site_number = path_parts[idx + 1]
            break
    if not site_number:
        for part in path_parts:
            if re.match(r"^CX_\d+$", part, flags=re.IGNORECASE):
                site_number = part
                break
    if not site_number:
        return {}

    base_url = f"{parsed.scheme}://{parsed.netloc}"
    careers_url = f"{base_url}/hcmUI/CandidateExperience/{locale}/sites/{site_number}/jobs"
    return {
        "provider": "oracle_ce",
        "careers_url": careers_url,
        "site_number": site_number,
        "locale": locale,
    }


def _snake_case(raw: str) -> str:
    lowered = raw.strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")


def _normalize_eightfold_filters(raw_filters: Any) -> dict[str, list[str]]:
    if not isinstance(raw_filters, dict):
        return {}

    normalized: dict[str, list[str]] = {}
    for key, value in raw_filters.items():
        filter_name = str(key).strip()
        if not filter_name:
            continue

        values: list[str] = []
        if isinstance(value, list):
            values = [str(item).strip() for item in value if str(item).strip()]
        elif value is not None:
            raw_text = str(value).strip()
            if raw_text:
                values = [part.strip() for part in raw_text.split(",") if part.strip()]

        if values:
            normalized[filter_name] = values

    return normalized


def _workday_parts(careers_url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(careers_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    tenant = parsed.netloc.split(".")[0]

    path_parts = [part for part in parsed.path.split("/") if part]
    locale = "en-US"
    site = "External"

    if path_parts:
        if WORKDAY_LOCALE_PATTERN.match(path_parts[0]):
            locale = path_parts[0]
            if len(path_parts) > 1:
                site = path_parts[-1]
        else:
            site = path_parts[-1]

    endpoint = f"{base_url}/wday/cxs/{tenant}/{site}/jobs"
    return endpoint, base_url, locale, site


def _oracle_ce_parts(careers_url: str) -> tuple[str, str, str]:
    parsed = urlparse(careers_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    path_parts = [part for part in parsed.path.split("/") if part]

    locale = "en"
    site_number = ""
    for idx, part in enumerate(path_parts):
        if part.lower() == "candidateexperience":
            if idx + 1 < len(path_parts):
                locale_candidate = path_parts[idx + 1]
                if re.match(r"^[a-z]{2}(?:-[A-Z]{2})?$", locale_candidate):
                    locale = locale_candidate
            break

    for idx, part in enumerate(path_parts):
        if part.lower() == "sites" and idx + 1 < len(path_parts):
            site_number = path_parts[idx + 1]
            break
    if not site_number:
        for part in path_parts:
            if re.match(r"^CX_\d+$", part, flags=re.IGNORECASE):
                site_number = part
                break

    endpoint = (
        f"{base_url}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        "?onlyData=true"
        "&expand=requisitionList.workLocation,requisitionList.otherWorkLocations,"
        "requisitionList.secondaryLocations,requisitionList.requisitionFlexFields"
        "&finder=findReqs"
    )
    job_url_template = f"{base_url}/hcmUI/CandidateExperience/{locale}/sites/{site_number}/job/{{job_id}}"
    return endpoint, job_url_template, site_number


def fetch_greenhouse_jobs(
    board_token: str,
    company: str,
    session: requests.Session | None = None,
) -> list[JobPosting]:
    token = board_token.strip()
    if not token:
        return []

    s = _build_session(session)
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    response = s.get(url, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    jobs = []
    for job in data.get("jobs", []):
        jobs.append(
            JobPosting(
                id=str(job.get("id", "")),
                company=company,
                title=job.get("title", ""),
                location=(job.get("location") or {}).get("name", "Unknown"),
                url=job.get("absolute_url", ""),
                description=_strip_html((job.get("content") or "")),
                source=f"greenhouse:{token}",
                metadata={"updated_at": str(job.get("updated_at", ""))},
            )
        )
    return jobs


def fetch_talentbrew_jobs(
    base_url: str,
    company: str,
    session: requests.Session | None = None,
    query: str = "",
    location: str = "",
    org_ids: str = "",
    max_jobs: int = 400,
) -> list[JobPosting]:
    cleaned_base = base_url.strip().rstrip("/")
    if not cleaned_base:
        return []

    s = _build_session(session)
    search_url = f"{cleaned_base}/search-jobs"
    params: dict[str, str] = {}
    if query.strip():
        params["k"] = query.strip()
    if location.strip():
        params["l"] = location.strip()
    if org_ids.strip():
        params["orgIds"] = org_ids.strip()

    response = s.get(search_url, params=params or None, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    jobs: list[JobPosting] = []
    seen_urls: set[str] = set()

    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        if not href:
            continue
        if "/job/" not in href.lower():
            continue

        absolute_url = urljoin(cleaned_base + "/", href)
        if absolute_url in seen_urls:
            continue

        parsed = urlparse(absolute_url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 5:
            continue
        if parts[0].lower() != "job":
            continue

        location_slug = parts[1]
        title_slug = parts[2]
        job_id = parts[-1]

        title_text = " ".join(anchor.get_text(" ", strip=True).split())
        if not title_text:
            title_text = title_slug.replace("-", " ").strip()

        location_text = location_slug.replace("-", " ").strip()
        if not location_text:
            location_text = "Unknown"

        jobs.append(
            JobPosting(
                id=job_id,
                company=company,
                title=title_text,
                location=location_text,
                url=absolute_url,
                description="",
                source=f"talentbrew:{parsed.netloc}",
                metadata={
                    "title_slug": title_slug,
                    "location_slug": location_slug,
                },
            )
        )
        seen_urls.add(absolute_url)
        if len(jobs) >= max_jobs:
            break

    return jobs


def fetch_lever_jobs(
    company_slug: str,
    company: str,
    session: requests.Session | None = None,
) -> list[JobPosting]:
    slug = company_slug.strip()
    if not slug:
        return []

    s = _build_session(session)
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    response = s.get(url, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()

    jobs = []
    for job in response.json() or []:
        categories = job.get("categories") or {}
        location = categories.get("location", "Unknown")
        team = categories.get("team", "")
        description = job.get("descriptionPlain") or _strip_html(job.get("description") or "")

        jobs.append(
            JobPosting(
                id=str(job.get("id", "")),
                company=company,
                title=job.get("text", ""),
                location=location,
                url=job.get("hostedUrl", ""),
                description=description,
                source=f"lever:{slug}",
                metadata={"team": team},
            )
        )
    return jobs


def fetch_eightfold_jobs(
    domain: str,
    company: str,
    session: requests.Session | None = None,
    base_url: str = "https://app.eightfold.ai",
    query: str = "",
    location: str = "",
    filters: Any = None,
    max_jobs: int = 400,
) -> list[JobPosting]:
    cleaned_domain = domain.strip()
    if not cleaned_domain:
        return []

    cleaned_base = base_url.strip().rstrip("/")
    if not cleaned_base:
        cleaned_base = "https://app.eightfold.ai"

    normalized_filters = _normalize_eightfold_filters(filters)

    s = _build_session(session)
    s.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{cleaned_base}/careers?domain={cleaned_domain}&hl=en",
        }
    )

    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    offset = 0
    page_size = 10

    while len(jobs) < max_jobs:
        params: list[tuple[str, str]] = [("domain", cleaned_domain), ("start", str(offset))]
        if query.strip():
            params.append(("query", query.strip()))
        if location.strip():
            params.append(("location", location.strip()))

        for filter_name, values in normalized_filters.items():
            filter_key = f"filter_{_snake_case(filter_name)}"
            for filter_value in values:
                params.append((filter_key, filter_value))

        response = s.get(f"{cleaned_base}/api/pcsx/search", params=params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()

        payload = response.json() or {}
        data = payload.get("data") or {}
        positions = data.get("positions") or []
        total_count = int(data.get("count") or 0)
        if not positions:
            break

        for position in positions:
            position_id = str(position.get("id") or "")
            if not position_id or position_id in seen_ids:
                continue

            raw_position_url = str(position.get("positionUrl") or "").strip()
            public_url = str(position.get("publicUrl") or "").strip()
            if not public_url and raw_position_url:
                if raw_position_url.startswith("/"):
                    public_url = f"{cleaned_base}{raw_position_url}"
                else:
                    public_url = raw_position_url
            if not public_url:
                public_url = f"{cleaned_base}/careers/job/{position_id}"

            locations = position.get("locations") or []
            location_text = ", ".join(str(item).strip() for item in locations if str(item).strip())

            jobs.append(
                JobPosting(
                    id=position_id,
                    company=company,
                    title=str(position.get("name") or ""),
                    location=location_text or "Unknown",
                    url=public_url,
                    description=str(position.get("department") or ""),
                    source=f"eightfold:{cleaned_domain}",
                    metadata={
                        "display_job_id": str(position.get("displayJobId") or ""),
                        "ats_job_id": str(position.get("atsJobId") or ""),
                        "posted_ts": str(position.get("postedTs") or ""),
                        "job_level": str(position.get("efcustomTextPcsPostingJobLevel") or ""),
                    },
                )
            )
            seen_ids.add(position_id)
            if len(jobs) >= max_jobs:
                break

        offset += len(positions)
        if len(positions) < page_size:
            break
        if total_count and offset >= total_count:
            break

    return jobs


def fetch_workday_jobs(
    careers_url: str,
    company: str,
    session: requests.Session | None = None,
    max_jobs: int = 400,
    query: str = "",
    location: str = "",
) -> list[JobPosting]:
    if not careers_url.strip():
        return []

    endpoint, base_url, locale, site = _workday_parts(careers_url)
    s = _build_session(session)
    search_text = query.strip()
    location_tokens = [
        token.strip().lower()
        for token in re.split(r"[,;/|]", location or "")
        if token.strip()
    ]

    jobs: list[JobPosting] = []
    offset = 0
    limit = 20

    while len(jobs) < max_jobs:
        payload: dict[str, Any] = {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": search_text,
        }
        response = s.post(endpoint, json=payload, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()

        data = response.json() or {}
        postings = data.get("jobPostings") or []
        if not postings:
            break

        for posting in postings:
            external_path = str(posting.get("externalPath") or "").strip()
            normalized_external_path = external_path.lstrip("/")
            if normalized_external_path:
                if normalized_external_path.lower().startswith("job/"):
                    path_suffix = normalized_external_path
                else:
                    path_suffix = f"job/{normalized_external_path}"
                job_url = f"{base_url}/{locale}/{site}/{path_suffix}"
            else:
                job_url = careers_url

            bullet_fields = posting.get("bulletFields") or []
            description = " | ".join(str(item) for item in bullet_fields if item)
            location_text = str(posting.get("locationsText") or posting.get("location") or "Unknown")
            if location_tokens and not any(token in location_text.lower() for token in location_tokens):
                continue
            posting_id = normalized_external_path or str(posting.get("title", ""))

            jobs.append(
                JobPosting(
                    id=posting_id,
                    company=company,
                    title=str(posting.get("title") or ""),
                    location=location_text,
                    url=job_url,
                    description=description,
                    source=f"workday:{site}",
                    metadata={"locale": locale, "site": site},
                )
            )
            if len(jobs) >= max_jobs:
                break

        if len(postings) < limit:
            break

        offset += limit

    return jobs


def fetch_oracle_ce_jobs(
    careers_url: str,
    company: str,
    session: requests.Session | None = None,
    max_jobs: int = 400,
    query: str = "",
    location: str = "",
) -> list[JobPosting]:
    if not careers_url.strip():
        return []

    endpoint, job_url_template, site_number = _oracle_ce_parts(careers_url)
    if not site_number:
        return []

    s = _build_session(session)
    jobs: list[JobPosting] = []
    seen_ids: set[str] = set()
    offset = 0
    limit = 24

    while len(jobs) < max_jobs:
        finder_parts = [
            ("siteNumber", site_number),
            ("limit", str(limit)),
            ("offset", str(offset)),
        ]
        keyword = str(query or "").strip()
        if keyword:
            # Oracle finder uses comma as parameter delimiter; avoid raw commas in values.
            keyword = keyword.replace(",", " ")
            finder_parts.append(("keyword", keyword))
        location_value = str(location or "").strip()
        if location_value:
            location_value = location_value.replace(",", " ")
            finder_parts.append(("location", location_value))

        finder = ",".join(f"{key}={quote(value, safe='')}" for key, value in finder_parts)
        url = f"{endpoint};{finder}"
        response = s.get(url, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()

        payload = response.json() or {}
        items = payload.get("items") or []
        if not items:
            break

        first = items[0] if isinstance(items[0], dict) else {}
        requisitions = first.get("requisitionList") or []
        if not requisitions:
            break

        for posting in requisitions:
            job_id = str(posting.get("Id") or "").strip()
            if not job_id or job_id in seen_ids:
                continue

            title = str(posting.get("Title") or "").strip()
            primary_location = str(posting.get("PrimaryLocation") or "").strip()
            location = primary_location or "Unknown"
            secondary_locations = posting.get("secondaryLocations") or []
            if isinstance(secondary_locations, list):
                names = [
                    str(item.get("Name") or "").strip()
                    for item in secondary_locations
                    if isinstance(item, dict) and str(item.get("Name") or "").strip()
                ]
                if names:
                    dedup_names = [name for name in names if name.lower() != location.lower()]
                    if dedup_names:
                        location = f"{location}; " + ", ".join(dedup_names[:2]) if location else ", ".join(dedup_names[:2])

            description_parts = [
                posting.get("ShortDescriptionStr"),
                posting.get("ExternalQualificationsStr"),
                posting.get("ExternalResponsibilitiesStr"),
                posting.get("JobFamily"),
                posting.get("JobFunction"),
                posting.get("Organization"),
            ]
            description = " | ".join(
                str(part).strip()
                for part in description_parts
                if str(part or "").strip()
            )

            jobs.append(
                JobPosting(
                    id=job_id,
                    company=company,
                    title=title,
                    location=location or "Unknown",
                    url=job_url_template.format(job_id=job_id),
                    description=description,
                    source=f"oracle_ce:{site_number}",
                    metadata={
                        "posted_date": str(posting.get("PostedDate") or ""),
                        "posting_end_date": str(posting.get("PostingEndDate") or ""),
                        "job_family": str(posting.get("JobFamily") or ""),
                        "job_function": str(posting.get("JobFunction") or ""),
                        "site_number": site_number,
                    },
                )
            )
            seen_ids.add(job_id)
            if len(jobs) >= max_jobs:
                break

        if len(requisitions) < limit:
            break
        if not bool(payload.get("hasMore")):
            break
        offset += limit

    return jobs
