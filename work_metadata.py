"""Доступность из России и способ выплаты; не влияет на 3D relevance."""
from __future__ import annotations
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class WorkMetadata:
    russia_eligibility: str
    russia_eligibility_reason: str
    payment_method: str
    payment_details: str

def analyze_work_metadata(text: str, location: str | list[str] = "") -> WorkMetadata:
    structured = location if isinstance(location, list) else None
    location_text = ", ".join(location) if structured else str(location)
    combined = f"{location_text}\n{text}".casefold()
    geo_only = r"(?:us|usa|united states|canada|uk|united kingdom|australia|eu|european union|eea)\s+only"
    blocked = re.search(rf"\b{geo_only}\b|must (?:reside|be located) in\s+[a-z ]+|candidates must be located in\s+[a-z ]+|must be authorized to work in\s+[a-z ]+|not available in russia|russia(?:n federation)? excluded", combined)
    allowed = re.search(r"\brussia\b|russian federation|worldwide|anywhere(?: in the world)?|\bglobal\b", combined)
    countries = [value.strip().casefold() for value in (structured or []) if value.strip()]
    closed_list = len(countries) >= 2 and all(value in {"usa", "us", "united states", "canada", "uk", "united kingdom", "germany", "poland", "australia", "france", "spain", "italy"} for value in countries)
    if blocked or (closed_list and "russia" not in countries):
        eligibility, reason = "blocked", "явное географическое ограничение"
    elif allowed:
        eligibility, reason = "allowed", "Россия или worldwide/anywhere указаны явно"
    else:
        eligibility, reason = "unknown", "нет достоверного ограничения или разрешения"
    crypto = re.findall(r"\b(?:crypto(?:currency)?|usdt|usdc|btc|bitcoin|eth|ethereum|stablecoin)\b", combined, re.I)
    fiat = re.findall(r"\b(?:bank transfer|wire transfer|paypal|wise|sepa)\b", combined, re.I)
    if crypto and fiat: payment, details = "mixed", " / ".join(dict.fromkeys(crypto + fiat))
    elif crypto: payment, details = "crypto_explicit", " / ".join(dict.fromkeys(crypto))
    elif fiat: payment, details = "fiat_explicit", " / ".join(dict.fromkeys(fiat))
    else: payment, details = "unknown", ""
    return WorkMetadata(eligibility, reason, payment, details)
