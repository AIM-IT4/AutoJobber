from __future__ import annotations

from job_apply_agent.models import JobPosting, RankedJob


class JobRanker:
    def rank(
        self,
        jobs: list[JobPosting],
        tags: list[str],
        preferences: dict | None = None,
    ) -> list[RankedJob]:
        prefs = preferences or {}
        preferred_locations = [str(item).lower() for item in prefs.get("preferred_locations", [])]
        preferred_titles = [str(item).lower() for item in prefs.get("preferred_titles", [])]
        exclude_words = [str(item).lower() for item in prefs.get("exclude_words", [])]
        remote_only = bool(prefs.get("remote_only", False))

        normalized_tags = [tag.lower() for tag in tags]

        ranked: list[RankedJob] = []
        for job in jobs:
            title = job.title.lower()
            desc = job.description.lower()
            location = job.location.lower()

            score = 0.0
            reasons: list[str] = []

            for tag in normalized_tags:
                if tag in title:
                    score += 6.0
                    reasons.append(f"title has '{tag}'")
                elif tag in desc:
                    score += 3.0
                    reasons.append(f"description has '{tag}'")

            for token in preferred_titles:
                if token and token in title:
                    score += 4.0
                    reasons.append(f"preferred title '{token}'")

            for token in preferred_locations:
                if token and token in location:
                    score += 3.0
                    reasons.append(f"preferred location '{token}'")

            if remote_only:
                if "remote" in location:
                    score += 5.0
                    reasons.append("remote location")
                else:
                    score -= 8.0
                    reasons.append("not remote")

            for bad_word in exclude_words:
                if bad_word and bad_word in title:
                    score -= 10.0
                    reasons.append(f"excluded '{bad_word}'")

            ranked.append(RankedJob(job=job, score=score, reasons=reasons))

        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked
