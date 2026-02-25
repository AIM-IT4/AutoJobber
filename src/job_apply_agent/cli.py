from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
import asyncio
import re

from job_apply_agent.config import ensure_output_dir, load_yaml, parse_csv_arg
from job_apply_agent.orchestrator import JobApplyOrchestrator


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover, rank, and auto-apply to jobs across Workday-style portals."
    )
    parser.add_argument("--company", required=False, help="Comma-separated company names")
    parser.add_argument("--tags", required=False, help="Comma-separated job tags for ranking")
    parser.add_argument("--profile", default="profile.yaml", help="Path to profile yaml")
    parser.add_argument("--targets", default="targets.yaml", help="Path to targets yaml")
    parser.add_argument("--output-dir", default="outputs", help="Output directory")
    parser.add_argument(
        "--session-dir",
        default=".session/chromium",
        help="Persistent browser session directory (reuses login state)",
    )

    parser.add_argument("--max-jobs", type=int, default=20, help="Top ranked jobs to process")
    parser.add_argument(
        "--max-per-company",
        type=int,
        default=60,
        help="Max jobs to fetch per company",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only discover and rank, do not apply",
    )
    parser.add_argument(
        "--auto-submit",
        action="store_true",
        help="Submit application after filling detected fields",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Run browser with UI visible",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--max-role-seconds",
        type=int,
        default=90,
        help="Max seconds spent per role before moving on",
    )
    parser.add_argument(
        "--job-urls",
        default="",
        help="Comma-separated job apply/job posting URLs to resume directly (skips discovery/ranking)",
    )
    parser.add_argument(
        "--resume-from-results",
        type=int,
        default=0,
        help="Pick N most recent unfinished job URLs from outputs/application_results_*.json",
    )

    return parser


def _print_ranked_jobs(selected_jobs: list) -> None:
    if not selected_jobs:
        print("No matching jobs found.")
        return

    print("\nTop ranked jobs:")
    for idx, item in enumerate(selected_jobs, start=1):
        reasons = "; ".join(item.reasons[:3])
        print(
            f"{idx:>2}. score={item.score:>5.1f} | {item.job.company} | "
            f"{item.job.title} | {item.job.location}"
        )
        if reasons:
            print(f"    reasons: {reasons}")


def _print_apply_results(results: list, results_file: str | None) -> None:
    if not results:
        print("\nNo application actions were executed.")
        return

    print("\nApplication results:")
    for result in results:
        print(
            f"- {result.status.upper():<15} | {result.company} | "
            f"{result.title} | {result.message}"
        )
    if results_file:
        print(f"\nResult JSON: {results_file}")


def _collect_resume_urls(output_dir: Path, limit: int) -> list[str]:
    if limit <= 0:
        return []

    urls: list[str] = []
    seen: set[str] = set()
    result_files = sorted(output_dir.glob("application_results_*.json"), reverse=True)
    for file_path in result_files:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).lower()
            raw_job_url = str(item.get("job_url", "")).strip()
            message = str(item.get("message", "") or "")
            final_url = ""
            match = re.search(r"final_url=([^;]+)", message)
            if match:
                final_url = str(match.group(1)).strip()
            if final_url.lower() in {"about:blank", "about:srcdoc"}:
                final_url = ""
            if final_url and not final_url.lower().startswith("http"):
                final_url = ""
            url = final_url or raw_job_url
            if not url:
                continue
            if status in {"applied", "already_applied"}:
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= limit:
                return urls
    return urls


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    profile = load_yaml(args.profile)
    if not profile:
        print(
            "Profile not found or empty. Create profile.yaml from profile.example.yaml.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    output_dir = ensure_output_dir(args.output_dir)
    targets = load_yaml(args.targets, default={})
    companies = parse_csv_arg(args.company or "")
    tags = parse_csv_arg(args.tags or "")
    direct_urls = parse_csv_arg(args.job_urls or "")
    if not direct_urls and args.resume_from_results > 0:
        direct_urls = _collect_resume_urls(output_dir, args.resume_from_results)

    if not direct_urls:
        if not companies:
            print("No company names provided.", file=sys.stderr)
            raise SystemExit(2)
        if not tags:
            print("No tags provided.", file=sys.stderr)
            raise SystemExit(2)

    orchestrator = JobApplyOrchestrator(
        profile=profile,
        targets=targets,
        output_dir=output_dir,
        headless=not args.headful,
        auto_submit=args.auto_submit,
        session_dir=Path(args.session_dir),
        max_role_seconds=args.max_role_seconds,
    )

    if direct_urls:
        if args.dry_run:
            print("Dry-run with --job-urls is not supported. Remove --dry-run.", file=sys.stderr)
            raise SystemExit(2)
        summary = asyncio.run(orchestrator.run_job_urls(direct_urls[: args.max_jobs]))
    else:
        summary = asyncio.run(orchestrator.run(
            companies=companies,
            tags=tags,
            max_jobs=args.max_jobs,
            max_per_company=args.max_per_company,
            dry_run=args.dry_run,
        ))

    discovered_count = len(summary["discovered_jobs"])
    selected_jobs = summary["selected_jobs"]

    print(f"Discovered jobs: {discovered_count}")
    _print_ranked_jobs(selected_jobs)

    if args.dry_run:
        print("\nDry run complete. No forms were submitted.")
        return

    _print_apply_results(summary["results"], str(summary["results_file"]))


if __name__ == "__main__":
    main()
