from pydantic import BaseModel
from typing import Optional
from enum import Enum


class ConfidenceLevel(str, Enum):
    CONFIRMED = "confirmed"
    LIKELY = "likely_match"
    NEEDS_REVIEW = "needs_manual_review"
    UNREADABLE = "unreadable"


class ApplicationData(BaseModel):
    """Fields from the COLA application submitted by the importer."""
    brand_name: str
    class_type: str                  # e.g. "American Whiskey", "Malt Beverage"
    abv: str                         # e.g. "40%" or "40.0"
    net_contents: str                # e.g. "750 mL" or "12 fl oz"
    government_warning: bool = True  # must always be present


class FieldResult(BaseModel):
    """Compliance result for a single label field."""
    field: str
    expected: str
    extracted: str
    confidence: ConfidenceLevel
    note: Optional[str] = None


class VerificationResult(BaseModel):
    """Full compliance report for one label image."""
    overall_status: str              # "PASS", "FAIL", "NEEDS REVIEW"
    fields: list[FieldResult]
    raw_extraction: Optional[str] = None
    error: Optional[str] = None
