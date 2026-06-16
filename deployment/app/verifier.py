import os
import json
import re
from dotenv import load_dotenv
from google import genai
from google.genai import types
from app.models import (
    ApplicationData, VerificationResult,
    FieldResult, ConfidenceLevel
)

load_dotenv()

# ── Gemini setup ──────────────────────────────────────────────────────────────
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
GEMINI_MODEL = "gemini-2.5-flash"

# ── Exact statutory Government Warning text (27 CFR 16.21) ───────────────────
GOVERNMENT_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, "
    "women should not drink alcoholic beverages during pregnancy "
    "because of the risk of birth defects. (2) Consumption of "
    "alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)


def _build_prompt(app: ApplicationData) -> str:
    return f"""
You are a TTB (Alcohol and Tobacco Tax and Trade Bureau) label compliance expert.

Carefully examine this alcohol beverage label image and extract the following fields exactly as they appear printed on the label:

1. brand_name — the primary brand or product name
2. class_type — the class and type designation (e.g. "Bourbon Whiskey", "Malt Beverage")
3. abv — alcohol by volume percentage (e.g. "40%" or "ALCOHOL 40% BY VOLUME")
4. net_contents — the net contents (e.g. "750 mL", "12 fl oz", "1 L")
5. government_warning — the full Government Warning text exactly as printed

Return ONLY a valid JSON object with this exact structure, no markdown, no explanation:
{{
  "brand_name": "extracted text or UNREADABLE",
  "class_type": "extracted text or UNREADABLE",
  "abv": "extracted text or UNREADABLE",
  "net_contents": "extracted text or UNREADABLE",
  "government_warning": "extracted text or UNREADABLE"
}}

If a field is present but partially obscured, extract what you can and note it.
If a field is completely missing or unreadable, use the value "UNREADABLE".
"""


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation for fuzzy comparison."""
    return re.sub(r"[^a-z0-9]", "", text.lower().strip())


def _compare_field(
    field_name: str,
    expected: str,
    extracted: str
) -> FieldResult:
    """Compare expected vs extracted value and assign confidence tier."""

    if extracted == "UNREADABLE":
        return FieldResult(
            field=field_name,
            expected=expected,
            extracted=extracted,
            confidence=ConfidenceLevel.UNREADABLE,
            note="Field could not be read from image"
        )

    # Government Warning requires exact match (statutory requirement)
    if field_name == "government_warning":
        if extracted.strip() == GOVERNMENT_WARNING:
            return FieldResult(
                field=field_name,
                expected=expected,
                extracted=extracted,
                confidence=ConfidenceLevel.CONFIRMED
            )
        elif _normalize(extracted) == _normalize(GOVERNMENT_WARNING):
            return FieldResult(
                field=field_name,
                expected=expected,
                extracted=extracted,
                confidence=ConfidenceLevel.LIKELY,
                note="Warning present but capitalization or punctuation may differ"
            )
        else:
            return FieldResult(
                field=field_name,
                expected=expected,
                extracted=extracted,
                confidence=ConfidenceLevel.NEEDS_REVIEW,
                note="Government Warning text does not match statutory requirement"
            )

    # Exact match
    if extracted.strip() == expected.strip():
        return FieldResult(
            field=field_name,
            expected=expected,
            extracted=extracted,
            confidence=ConfidenceLevel.CONFIRMED
        )

    # Fuzzy match — normalize both sides
    if _normalize(extracted) == _normalize(expected):
        return FieldResult(
            field=field_name,
            expected=expected,
            extracted=extracted,
            confidence=ConfidenceLevel.LIKELY,
            note="Match after normalization — check capitalization or punctuation"
        )

    # Partial match — extracted contains expected or vice versa
    if (
        _normalize(expected) in _normalize(extracted) or
        _normalize(extracted) in _normalize(expected)
    ):
        return FieldResult(
            field=field_name,
            expected=expected,
            extracted=extracted,
            confidence=ConfidenceLevel.LIKELY,
            note="Partial match — please verify manually"
        )

    # No match
    return FieldResult(
        field=field_name,
        expected=expected,
        extracted=extracted,
        confidence=ConfidenceLevel.NEEDS_REVIEW,
        note="Value does not match application data"
    )


def _overall_status(fields: list[FieldResult]) -> str:
    """Derive overall pass/fail/review from field results."""
    confidences = [f.confidence for f in fields]
    if any(c == ConfidenceLevel.NEEDS_REVIEW for c in confidences):
        return "FAIL"
    if any(c in (ConfidenceLevel.LIKELY, ConfidenceLevel.UNREADABLE)
           for c in confidences):
        return "NEEDS REVIEW"
    return "PASS"


async def verify_label(
    image_bytes: bytes,
    content_type: str,
    app_data: ApplicationData
) -> VerificationResult:
    """Main entry point — send image to Gemini, compare to application data."""
    try:
        # Encode image for Gemini
        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type=content_type
        )

        prompt = _build_prompt(app_data)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, image_part]
        )
        raw_text = response.text.strip()

        # Strip markdown fences if Gemini wraps in ```json
        raw_text = re.sub(r"^```json\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)

        extracted = json.loads(raw_text)

        # Compare each field
        gov_warning_expected = GOVERNMENT_WARNING if app_data.government_warning else "NOT REQUIRED"
        gov_warning_extracted = extracted.get("government_warning", "UNREADABLE")

        fields = [
            _compare_field("brand_name",
                           app_data.brand_name,
                           extracted.get("brand_name", "UNREADABLE")),
            _compare_field("class_type",
                           app_data.class_type,
                           extracted.get("class_type", "UNREADABLE")),
            _compare_field("abv",
                           app_data.abv,
                           extracted.get("abv", "UNREADABLE")),
            _compare_field("net_contents",
                           app_data.net_contents,
                           extracted.get("net_contents", "UNREADABLE")),
            _compare_field("government_warning",
                           gov_warning_expected,
                           gov_warning_extracted),
        ]

        return VerificationResult(
            overall_status=_overall_status(fields),
            fields=fields,
            raw_extraction=raw_text
        )

    except json.JSONDecodeError as e:
        return VerificationResult(
            overall_status="FAIL",
            fields=[],
            error=f"Could not parse Gemini response as JSON: {str(e)}",
            raw_extraction=raw_text if "raw_text" in dir() else None
        )
    except Exception as e:
        return VerificationResult(
            overall_status="FAIL",
            fields=[],
            error=f"Verification failed: {str(e)}"
        )
