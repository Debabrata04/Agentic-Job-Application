import httpx

from app.services.job_sources.base import NormalizedJob

BASE_URL = "https://api.lever.co/v0/postings/{slug}"


def fetch_jobs(slug: str, company_name: str) -> list[NormalizedJob]:
    url = BASE_URL.format(slug=slug)
    response = httpx.get(url, params={"mode": "json"}, timeout=20.0)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for raw in data:
        categories = raw.get("categories") or {}
        jobs.append(
            NormalizedJob(
                source="lever",
                external_id=str(raw["id"]),
                company=company_name,
                title=raw.get("text", ""),
                description=raw.get("description") or raw.get("descriptionPlain"),
                location=categories.get("location"),
                url=raw.get("hostedUrl"),
            )
        )
    return jobs
