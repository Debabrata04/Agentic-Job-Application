import httpx

from app.services.job_sources.base import NormalizedJob

LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"

PAGE_SIZE = 100


def fetch_job_description(slug: str, posting_id: str) -> str | None:
    """Fetch one posting's full description on demand (not during bulk discovery -
    SmartRecruiters companies can have thousands of postings, and fetching each one's
    detail during a poll would mean thousands of extra requests per cycle)."""
    url = DETAIL_URL.format(slug=slug, posting_id=posting_id)
    response = httpx.get(url, timeout=20.0)
    if response.status_code != 200:
        return None
    detail = response.json()
    sections = (detail.get("jobAd") or {}).get("sections") or {}
    description = sections.get("jobDescription") or {}
    return description.get("text")


def fetch_jobs(slug: str, company_name: str) -> list[NormalizedJob]:
    jobs = []
    offset = 0

    while True:
        response = httpx.get(
            LIST_URL.format(slug=slug),
            params={"offset": offset, "limit": PAGE_SIZE},
            timeout=20.0,
        )
        response.raise_for_status()
        data = response.json()

        postings = data.get("content", [])
        if not postings:
            break

        for raw in postings:
            posting_id = raw["id"]
            location = raw.get("location") or {}
            location_parts = [p for p in [location.get("city"), location.get("country")] if p]

            jobs.append(
                NormalizedJob(
                    source="smartrecruiters",
                    external_id=str(posting_id),
                    company=company_name,
                    title=raw.get("name", ""),
                    description=None,  # fetched lazily later via fetch_job_description()
                    location=", ".join(location_parts) if location_parts else None,
                    url=f"https://jobs.smartrecruiters.com/{slug}/{posting_id}",
                )
            )

        offset += PAGE_SIZE
        if offset >= data.get("totalFound", 0):
            break

    return jobs
