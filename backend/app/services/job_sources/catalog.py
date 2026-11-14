"""Seed list of known companies and the ATS platform/slug their career page uses.

Lets the UI offer a dropdown of companies instead of asking the user to know
which platform a company's board runs on and what slug it uses there.
"""

from dataclasses import dataclass


@dataclass
class CatalogEntry:
    company_name: str
    platform: str
    slug: str


KNOWN_COMPANIES: list[CatalogEntry] = [
    # Verified reachable against the live platform APIs on 2026-10-24.
    # 7 of the previous 12 entries (DoorDash, Netflix, Figma, Plaid, Notion,
    # Spotify, Visa) turned out to be wrong - either 404s or valid-but-empty
    # responses, meaning those companies simply aren't on that platform under
    # any guessable slug. Replaced with confirmed-working alternatives rather
    # than left in, since a broken catalog entry fails silently at discovery
    # time, not at catalog-selection time.
    CatalogEntry("Stripe", "greenhouse", "stripe"),
    CatalogEntry("Airbnb", "greenhouse", "airbnb"),
    CatalogEntry("Coinbase", "greenhouse", "coinbase"),
    CatalogEntry("Robinhood", "greenhouse", "robinhood"),
    CatalogEntry("Databricks", "greenhouse", "databricks"),
    CatalogEntry("Instacart", "greenhouse", "instacart"),
    CatalogEntry("Asana", "greenhouse", "asana"),
    CatalogEntry("Reddit", "greenhouse", "reddit"),
    CatalogEntry("GitLab", "greenhouse", "gitlab"),
    CatalogEntry("Palantir", "lever", "palantir"),
    CatalogEntry("Bosch Group", "smartrecruiters", "BoschGroup"),
]


def list_catalog() -> list[CatalogEntry]:
    return KNOWN_COMPANIES
