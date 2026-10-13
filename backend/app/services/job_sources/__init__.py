from app.services.job_sources import greenhouse, lever, smartrecruiters
from app.services.job_sources.base import NormalizedJob

ADAPTERS = {
    "greenhouse": greenhouse.fetch_jobs,
    "lever": lever.fetch_jobs,
    "smartrecruiters": smartrecruiters.fetch_jobs,
}


def fetch_jobs_for_source(platform: str, slug: str, company_name: str) -> list[NormalizedJob]:
    adapter = ADAPTERS.get(platform)
    if adapter is None:
        raise ValueError(f"Unsupported platform: {platform}")
    return adapter(slug, company_name)
