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

def analyze_work_metadata(text: str, location: str = "") -> WorkMetadata:
    combined = f"{location}\n{text}".casefold()
    blocked = re.search(r"\b(?:us|usa|canada|uk|australia|[a-z]+)\s+only\b|must reside in\s+\w+|candidates must be located in\s+\w+|must be authorized to work in\s+\w+|not available in russia|russia(?:n federation)? excluded", combined)
    allowed = re.search(r"\brussia\b|russian federation|worldwide|anywhere(?: in the world)?|\bglobal\b", combined)
    countries = [value.strip().casefold() for value in re.split(r"[,;/]", location) if value.strip()]
    closed_list = len(countries) >= 2 and all(value in {"usa", "us", "canada", "uk", "germany", "poland", "australia", "france", "spain", "italy"} for value in countries)
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
