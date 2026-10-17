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
    CatalogEntry("Stripe", "greenhouse", "stripe"),
    CatalogEntry("Airbnb", "greenhouse", "airbnb"),
    CatalogEntry("Coinbase", "greenhouse", "coinbase"),
    CatalogEntry("Robinhood", "greenhouse", "robinhood"),
    CatalogEntry("DoorDash", "greenhouse", "doordash"),
    CatalogEntry("Databricks", "greenhouse", "databricks"),
    CatalogEntry("Netflix", "lever", "netflix"),
    CatalogEntry("Figma", "lever", "figma"),
    CatalogEntry("Plaid", "lever", "plaid"),
    CatalogEntry("Notion", "lever", "notion"),
    CatalogEntry("Spotify", "smartrecruiters", "Spotify"),
    CatalogEntry("Visa", "smartrecruiters", "Visa"),
]


def list_catalog() -> list[CatalogEntry]:
    return KNOWN_COMPANIES
