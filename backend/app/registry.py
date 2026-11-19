"""Read-only lookup against the public search used by NMC's IMR page.

The page currently calls a JSON search endpoint, but this is not a documented
verification API. Unexpected responses always require admin review.
"""

import re
from dataclasses import dataclass
from typing import Literal
from unicodedata import normalize

import httpx

NMC_URL = "https://nmc.org.in/information-desk/indian-medical-register"
SEARCH_URL = "https://nmc.org.in/indian-medical-register/search"


@dataclass
class RegistryRecord:
    full_name: str
    council: str
    registration_number: str
    qualifications: str | None = None


@dataclass
class RegistryResult:
    status: Literal["matched", "review", "unavailable"]
    source_url: str = NMC_URL
    matched_name: str = ""
    detail: str = ""
    record: RegistryRecord | None = None


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def match_key(value: str) -> str:
    return " ".join(normalize("NFC", value).casefold().split())


def interpret_result(payload: object, number: str, council: str, full_name: str) -> RegistryResult:
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("data"), list):
        return RegistryResult("unavailable", detail="Unexpected response from official search")
    rows = payload["data"]
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict) or type(pagination.get("total")) is not int or pagination["total"] != len(rows):
        return RegistryResult("review", detail="Search results could not be checked in full")
    fields = ("registration_no", "state_medical_council", "name")
    if any(not isinstance(row, dict) or any(not isinstance(row.get(field), str) or not row[field].strip()
                                           for field in fields) for row in rows):
        return RegistryResult("review", detail="Search results could not be checked in full")
    expected = tuple(map(match_key, (number, council, full_name)))
    matches = [row for row in rows if tuple(match_key(row[field]) for field in fields) == expected]
    if len(matches) == 1:
        row = matches[0]
        if row.get("removed_status") not in (None, "", "0", 0):
            return RegistryResult("review", detail="The official record requires manual review of registration status")
        qualifications = [row.get("qualification")]
        additional = row.get("additional_qualifications")
        if isinstance(additional, list):
            qualifications.extend(item.get("qualification") for item in additional if isinstance(item, dict))
        listed_degrees = dict.fromkeys(value.strip() for value in qualifications if isinstance(value, str) and value.strip())
        record = RegistryRecord(full_name=row["name"], council=row["state_medical_council"],
                                registration_number=row["registration_no"], qualifications=", ".join(listed_degrees) or None)
        return RegistryResult("matched", matched_name=record.full_name,
                              detail="Exact registration, council, and name match", record=record)
    return RegistryResult("review", detail="No unique exact public-register match")


def lookup_registration(number: str, council: str, full_name: str) -> RegistryResult:
    try:
        response = httpx.get(SEARCH_URL, params={"search_type": "reg_no", "reg_no": number,
                                                   "page": 1, "per_page": 100},
                             timeout=7, follow_redirects=True,
                             headers={"User-Agent": "MediConnect student project registry lookup", "Accept": "application/json"})
        response.raise_for_status()
        return interpret_result(response.json(), number, council, full_name)
    except (httpx.HTTPError, ValueError):
        return RegistryResult("unavailable", detail="Official search unavailable")
