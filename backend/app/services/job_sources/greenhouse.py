import httpx

from app.services.job_sources.base import NormalizedJob

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def fetch_jobs(slug: str, company_name: str) -> list[NormalizedJob]:
    url = BASE_URL.format(slug=slug)
    response = httpx.get(url, params={"content": "true"}, timeout=20.0)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for raw in data.get("jobs", []):
        location = raw.get("location") or {}
        jobs.append(
            NormalizedJob(
                source="greenhouse",
                external_id=str(raw["id"]),
                company=company_name,
                title=raw.get("title", ""),
                description=raw.get("content"),
                location=location.get("name"),
                url=raw.get("absolute_url"),
            )
        )
    return jobs
