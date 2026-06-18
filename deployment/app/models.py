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
    confidence_pct: int               # 0-100, blended Gemini-read confidence + match-rule adjustment
    reason: str                       # human-readable explanation, always populated
    note: Optional[str] = None        # deprecated alias, kept for backward compatibility


class VerificationResult(BaseModel):
    """Full compliance report for one label image."""
    overall_status: str              # "PASS", "FAIL", "NEEDS REVIEW"
    overall_confidence_pct: int       # average of all field confidence_pct values
    fields: list[FieldResult]
    raw_extraction: Optional[str] = None
    error: Optional[str] = None


class BatchRow(BaseModel):
    """One row of the metadata CSV — maps a filename to its application data."""
    filename: str
    brand_name: str
    class_type: str
    abv: str
    net_contents: str
    government_warning: bool = True


class BatchItemResult(BaseModel):
    """Result for one image+metadata pair within a batch run."""
    filename: str
    status: str                      # "PASS", "FAIL", "NEEDS REVIEW", "ERROR"
    result: Optional[VerificationResult] = None
    error: Optional[str] = None


class BatchSummary(BaseModel):
    """Aggregate summary across an entire batch run."""
    total: int
    passed: int
    failed: int
    needs_review: int
    errored: int
    items: list[BatchItemResult]

