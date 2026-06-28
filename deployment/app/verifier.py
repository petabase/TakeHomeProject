import os
import json
import re
import csv
import io
import asyncio
from dotenv import load_dotenv
from google import genai
from google.genai import types
from app.models import (
    ApplicationData, VerificationResult,
    FieldResult, ConfidenceLevel,
    BatchRow, BatchItemResult, BatchSummary
)

load_dotenv()

# ── Gemini setup ──────────────────────────────────────────────────────────────
# Use client.aio.models.generate_content() for async calls — this is
# awaitable so the event loop can properly interleave the rate-limit sleep
# between requests. client.models.generate_content() is synchronous and
# blocks the whole event loop, making asyncio.sleep() ineffective.
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
GEMINI_MODEL = "gemini-2.5-flash-lite"  # 15 RPM free tier (flash is only 10 RPM)

# ── Batch tuning ───────────────────────────────────────────────────────────────
# gemini-2.5-flash-lite free tier: 15 RPM = 1 request every 4 seconds.
# We serialize all Gemini calls through a lock with a 4.2s sleep after each
# one, staying comfortably under the ceiling with a small margin.
# Total batch time for 14 images: 14 × 4.2s ≈ 59s.
# If a 429 still occurs (quota used by prior runs in the same minute window),
# affected rows are marked ERROR with a plain-English message — no silent retries.
MAX_BATCH_SIZE = 300
MAX_CONCURRENT_REQUESTS = 10  # rate limiter below is the real throttle

REQUEST_INTERVAL_SECONDS = 4.2

# Shared lock — created lazily inside the running event loop, not at import
# time. Creating asyncio.Lock() at module level binds it to the wrong loop
# on Python 3.10+ and causes silent failures where the lock never blocks.
_request_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _request_lock
    if _request_lock is None:
        _request_lock = asyncio.Lock()
    return _request_lock


async def _rate_limited_call(image_part, prompt) -> object:
    """Call Gemini with guaranteed minimum spacing between requests.

    The asyncio.Lock serialises all calls so only one runs at a time.
    The sleep AFTER the call (while still holding the lock) ensures the
    next caller waits the full interval before the lock is released.
    client.aio.models.generate_content() is a true coroutine so the
    event loop can actually yield and run the sleep between requests.
    """
    async with _get_lock():
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, image_part]
        )
        await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
        return response

QUOTA_EXCEEDED_MESSAGE = (
    "Gemini API free-tier quota exceeded (15 requests/minute). "
    "This label was skipped — wait 60 seconds and re-run the batch, "
    "or split it into smaller batches. This is a known free-tier limit, "
    "not a bug. See testcase/README.md for details."
)

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

Carefully examine this alcohol beverage label image and extract the following fields exactly as they appear printed on the label. For each field, also rate how confident you are in your reading, as a whole number from 0 to 100, based on image clarity, legibility, and how certain the text is (not on whether it matches any expected value — you don't know the expected value).

1. brand_name — the primary brand or product name
2. class_type — the class and type designation (e.g. "Bourbon Whiskey", "Malt Beverage")
3. abv — alcohol by volume percentage (e.g. "40%" or "ALCOHOL 40% BY VOLUME")
4. net_contents — the net contents (e.g. "750 mL", "12 fl oz", "1 L")
5. government_warning — the full Government Warning text exactly as printed

Return ONLY a valid JSON object with this exact structure, no markdown, no explanation:
{{
  "brand_name": {{"value": "extracted text or UNREADABLE", "confidence": 0-100}},
  "class_type": {{"value": "extracted text or UNREADABLE", "confidence": 0-100}},
  "abv": {{"value": "extracted text or UNREADABLE", "confidence": 0-100}},
  "net_contents": {{"value": "extracted text or UNREADABLE", "confidence": 0-100}},
  "government_warning": {{"value": "extracted text or UNREADABLE", "confidence": 0-100}}
}}

If a field is present but partially obscured, extract what you can, note it, and lower the confidence score accordingly. If a field is completely missing or unreadable, use the value "UNREADABLE" and a confidence of 0.
"""


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation for fuzzy comparison."""
    return re.sub(r"[^a-z0-9]", "", text.lower().strip())


def _blend_confidence(read_confidence: int, match_score: int) -> int:
    """
    The confidence_pct on a field answers: "how confident are we that this
    field is correct?" That is fundamentally a question about the MATCH,
    not about how clearly the image was photographed.

    A label photographed in perfect, crisp focus that shows the wrong brand
    name is not "60% confident" — it's a clear, certain mismatch, so it
    should read close to 0%. Conversely, a blurry photo where the model
    still managed to read a value with low confidence makes a confirmed
    match somewhat less certain than a crisp one.

    So: match_score is the dominant signal and sets the baseline. Read
    clarity only nudges the result — it can shave a few points off an
    otherwise-confirmed match (because the underlying read was shaky), but
    it never pulls a confirmed match below "Confirmed" territory, and it
    never pulls a clear mismatch up out of "wrong" territory.
    """
    read_confidence = max(0, min(100, read_confidence))
    match_score = max(0, min(100, match_score))

    if match_score >= 95:
        # Confirmed match — read clarity can only trim a little, never
        # drag this down into ambiguous territory.
        return round(max(85, match_score - (100 - read_confidence) * 0.15))

    if match_score == 0:
        # Clear mismatch — this is certain regardless of how clearly the
        # (wrong) text was read. Confidence in correctness is 0.
        return 0

    # Ambiguous / partial cases (normalized or substring matches) — here
    # read clarity legitimately matters, since the comparison itself is
    # uncertain. Blend with a modest weight on read clarity.
    return round(match_score * 0.75 + read_confidence * 0.25)


def _compare_field(
    field_name: str,
    expected: str,
    extracted: str,
    read_confidence: int
) -> FieldResult:
    """Compare expected vs extracted value, blend confidence, and explain why."""

    if extracted == "UNREADABLE" or read_confidence == 0:
        return FieldResult(
            field=field_name,
            expected=expected,
            extracted=extracted,
            confidence=ConfidenceLevel.UNREADABLE,
            confidence_pct=0,
            reason="Field could not be read from the image — check image quality, "
                   "angle, lighting, or resolution and re-upload.",
            note="Field could not be read from image"
        )

    # Government Warning requires exact match (statutory requirement)
    if field_name == "government_warning":
        if extracted.strip() == GOVERNMENT_WARNING:
            pct = _blend_confidence(read_confidence, 100)
            return FieldResult(
                field=field_name, expected=expected, extracted=extracted,
                confidence=ConfidenceLevel.CONFIRMED, confidence_pct=pct,
                reason="Government Warning text matches the statutory wording exactly."
            )
        elif _normalize(extracted) == _normalize(GOVERNMENT_WARNING):
            pct = _blend_confidence(read_confidence, 75)
            return FieldResult(
                field=field_name, expected=expected, extracted=extracted,
                confidence=ConfidenceLevel.LIKELY, confidence_pct=pct,
                reason="Government Warning text matches once case and punctuation "
                       "are normalized — verify capitalization/formatting manually.",
                note="Warning present but capitalization or punctuation may differ"
            )
        else:
            pct = _blend_confidence(read_confidence, 0)
            return FieldResult(
                field=field_name, expected=expected, extracted=extracted,
                confidence=ConfidenceLevel.NEEDS_REVIEW, confidence_pct=pct,
                reason="Government Warning text does not match the statutory "
                       "requirement (27 CFR 16.21). This is a hard compliance "
                       "failure regardless of other fields.",
                note="Government Warning text does not match statutory requirement"
            )

    # Exact match
    if extracted.strip() == expected.strip():
        pct = _blend_confidence(read_confidence, 100)
        return FieldResult(
            field=field_name, expected=expected, extracted=extracted,
            confidence=ConfidenceLevel.CONFIRMED, confidence_pct=pct,
            reason=f"Label text matches the application value exactly."
        )

    # Fuzzy match — normalize both sides
    if _normalize(extracted) == _normalize(expected):
        pct = _blend_confidence(read_confidence, 80)
        return FieldResult(
            field=field_name, expected=expected, extracted=extracted,
            confidence=ConfidenceLevel.LIKELY, confidence_pct=pct,
            reason=f"Label shows \"{extracted}\" vs application \"{expected}\" — "
                   f"matches after ignoring case/punctuation. Likely just a "
                   f"formatting difference, but worth a quick visual check.",
            note="Match after normalization — check capitalization or punctuation"
        )

    # Partial match — extracted contains expected or vice versa
    if (
        _normalize(expected) in _normalize(extracted) or
        _normalize(extracted) in _normalize(expected)
    ):
        pct = _blend_confidence(read_confidence, 55)
        return FieldResult(
            field=field_name, expected=expected, extracted=extracted,
            confidence=ConfidenceLevel.LIKELY, confidence_pct=pct,
            reason=f"Label shows \"{extracted}\" which partially overlaps with "
                   f"application value \"{expected}\" — one may be a substring "
                   f"of the other. Needs a manual look to confirm.",
            note="Partial match — please verify manually"
        )

    # No match
    pct = _blend_confidence(read_confidence, 0)
    return FieldResult(
        field=field_name, expected=expected, extracted=extracted,
        confidence=ConfidenceLevel.NEEDS_REVIEW, confidence_pct=pct,
        reason=f"Label shows \"{extracted}\" but the application states "
               f"\"{expected}\" — these do not match. This field fails "
               f"compliance unless corrected.",
        note="Value does not match application data"
    )


# Fields with confidence_pct at or above this threshold auto-pass even if
# their match required normalization (case/punctuation differences) — per
# Dave's interview feedback that "STONE'S THROW" vs "Stone's Throw" shouldn't
# stop an otherwise-correct label. Below this threshold (e.g. a partial/
# substring match), the field still routes to NEEDS REVIEW since the
# comparison itself is genuinely uncertain, not just a formatting quirk.
AUTO_PASS_CONFIDENCE_THRESHOLD = 75


def _overall_status(fields: list[FieldResult]) -> str:
    """Derive overall pass/fail/review from field results.

    NEEDS_REVIEW tier is always a hard fail — these are real value
    mismatches or exact-match-required fields (Government Warning) that
    failed. LIKELY tier is only escalated to NEEDS REVIEW if its confidence
    is below the auto-pass threshold; a high-confidence normalized match
    (e.g. case/punctuation only) is treated as a pass.
    """
    if any(f.confidence == ConfidenceLevel.NEEDS_REVIEW for f in fields):
        return "FAIL"

    if any(f.confidence == ConfidenceLevel.UNREADABLE for f in fields):
        return "NEEDS REVIEW"

    if any(
        f.confidence == ConfidenceLevel.LIKELY
        and f.confidence_pct < AUTO_PASS_CONFIDENCE_THRESHOLD
        for f in fields
    ):
        return "NEEDS REVIEW"

    return "PASS"


def _overall_confidence(fields: list[FieldResult]) -> int:
    """Average confidence_pct across all fields, rounded to nearest integer."""
    if not fields:
        return 0
    return round(sum(f.confidence_pct for f in fields) / len(fields))


async def verify_label(
    image_bytes: bytes,
    content_type: str,
    app_data: ApplicationData
) -> VerificationResult:
    """Main entry point — send image to Gemini, compare to application data.

    On a 429 RESOURCE_EXHAUSTED (free-tier quota exceeded), returns a clean
    user-facing error immediately rather than retrying — retrying would stall
    the UI for 60+ seconds with no feedback. The caller sees an ERROR status
    with an actionable message. See testcase/README.md for details.
    """
    raw_text = None
    try:
        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type=content_type
        )
        prompt = _build_prompt(app_data)
        response = await _rate_limited_call(image_part, prompt)
        raw_text = response.text.strip()

    except Exception as e:
        error_str = str(e)
        is_quota_error = (
            "429" in error_str or
            "RESOURCE_EXHAUSTED" in error_str or
            "quota" in error_str.lower()
        )
        return VerificationResult(
            overall_status="ERROR",
            overall_confidence_pct=0,
            fields=[],
            error=QUOTA_EXCEEDED_MESSAGE if is_quota_error
                  else f"Verification failed: {error_str}"
        )

    # Parse Gemini's JSON response and compare fields
    try:
        clean_text = re.sub(r"^```json\s*", "", raw_text.strip())
        clean_text = re.sub(r"\s*```$", "", clean_text)
        extracted = json.loads(clean_text)

        def _get(field_name: str) -> tuple[str, int]:
            """Pull {value, confidence} tolerating old flat-string shape.
            Embedded newlines are collapsed to single spaces so values stay
            on one line in CSV exports opened in Excel/Sheets/a database."""
            raw = extracted.get(field_name, {"value": "UNREADABLE", "confidence": 0})
            if isinstance(raw, str):
                value, confidence = raw, 50
            else:
                value = raw.get("value", "UNREADABLE")
                confidence = int(raw.get("confidence", 0))
            value = " ".join(value.split()) if value else value
            return value, confidence

        gov_warning_expected = GOVERNMENT_WARNING if app_data.government_warning else "NOT REQUIRED"
        gov_value, gov_conf   = _get("government_warning")
        brand_value, brand_conf = _get("brand_name")
        class_value, class_conf = _get("class_type")
        abv_value, abv_conf   = _get("abv")
        net_value, net_conf   = _get("net_contents")

        fields = [
            _compare_field("brand_name",        app_data.brand_name,  brand_value, brand_conf),
            _compare_field("class_type",         app_data.class_type,  class_value, class_conf),
            _compare_field("abv",                app_data.abv,         abv_value,   abv_conf),
            _compare_field("net_contents",       app_data.net_contents,net_value,   net_conf),
            _compare_field("government_warning", gov_warning_expected, gov_value,   gov_conf),
        ]

        return VerificationResult(
            overall_status=_overall_status(fields),
            overall_confidence_pct=_overall_confidence(fields),
            fields=fields,
            raw_extraction=raw_text
        )

    except json.JSONDecodeError as e:
        return VerificationResult(
            overall_status="FAIL",
            overall_confidence_pct=0,
            fields=[],
            error=f"Could not parse Gemini response as JSON: {str(e)}",
            raw_extraction=raw_text
        )
    except Exception as e:
        return VerificationResult(
            overall_status="FAIL",
            overall_confidence_pct=0,
            fields=[],
            error=f"Verification failed: {str(e)}"
        )


REQUIRED_CSV_COLUMNS = {
    "filename", "brand_name", "class_type", "abv", "net_contents"
}


def parse_metadata_csv(csv_bytes: bytes) -> tuple[list[BatchRow], list[str]]:
    """
    Parse the uploaded metadata CSV into BatchRow objects.
    Returns (rows, errors) — errors is a list of human-readable problems
    found while parsing (missing columns, bad boolean values, etc).
    """
    errors: list[str] = []
    rows: list[BatchRow] = []

    try:
        text = csv_bytes.decode("utf-8-sig")  # handles Excel's BOM-prefixed UTF-8
    except UnicodeDecodeError:
        return [], ["CSV file is not valid UTF-8 text. Please re-export and try again."]

    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        return [], ["CSV file appears to be empty."]

    header = {h.strip().lower() for h in reader.fieldnames}
    missing = REQUIRED_CSV_COLUMNS - header
    if missing:
        errors.append(
            f"CSV is missing required column(s): {', '.join(sorted(missing))}. "
            f"Required columns: {', '.join(sorted(REQUIRED_CSV_COLUMNS))}"
        )
        return [], errors

    for i, raw_row in enumerate(reader, start=2):  # row 1 is the header
        normalized = {k.strip().lower(): (v or "").strip() for k, v in raw_row.items()}

        if not normalized.get("filename"):
            errors.append(f"Row {i}: missing filename, skipped.")
            continue

        gov_warning_raw = normalized.get("government_warning", "true").lower()
        gov_warning = gov_warning_raw not in ("false", "0", "no", "")

        try:
            rows.append(BatchRow(
                filename=normalized["filename"],
                brand_name=normalized.get("brand_name", ""),
                class_type=normalized.get("class_type", ""),
                abv=normalized.get("abv", ""),
                net_contents=normalized.get("net_contents", ""),
                government_warning=gov_warning,
            ))
        except Exception as e:
            errors.append(f"Row {i} ({normalized.get('filename', '?')}): {str(e)}")

    if len(rows) > MAX_BATCH_SIZE:
        errors.append(
            f"CSV contains {len(rows)} rows, exceeding the {MAX_BATCH_SIZE}-image "
            f"batch limit. Only the first {MAX_BATCH_SIZE} rows will be processed."
        )
        rows = rows[:MAX_BATCH_SIZE]

    return rows, errors


async def _verify_one_batch_item(
    filename: str,
    image_bytes: bytes,
    content_type: str,
    row: BatchRow,
    semaphore: asyncio.Semaphore
) -> BatchItemResult:
    """Verify a single batch item. Rate limiting is handled inside
    verify_label via _rate_limited_call — no per-item throttle needed here."""
    async with semaphore:
        app_data = ApplicationData(
            brand_name=row.brand_name,
            class_type=row.class_type,
            abv=row.abv,
            net_contents=row.net_contents,
            government_warning=row.government_warning,
        )
        try:
            result = await verify_label(image_bytes, content_type, app_data)
            return BatchItemResult(
                filename=filename,
                status=result.overall_status,
                result=result,
                error=result.error
            )
        except Exception as e:
            return BatchItemResult(
                filename=filename,
                status="ERROR",
                error=str(e)
            )


async def verify_batch(
    rows: list[BatchRow],
    images: dict[str, tuple[bytes, str]]   # filename -> (bytes, content_type)
) -> BatchSummary:
    """
    Run verification across an entire batch, with bounded concurrency so we
    don't blow through the Gemini free-tier rate limit (15 req/min).

    Filename matching between the CSV and the uploaded images is
    case-insensitive — "WheatBeer.png" in the CSV will match an uploaded
    file named "WheatBeer.PNG", "wheatbeer.png", etc. This avoids a common
    real-world papercut where an OS or export tool changes file extension
    casing but the visual filename looks identical to a human.
    """
    # Build a lowercase-keyed lookup so case differences don't cause false
    # "no matching image" errors, while still reporting back the original
    # filename casing from the image that was actually uploaded.
    images_by_lower = {fname.lower(): (fname, data) for fname, data in images.items()}

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    tasks = []

    for row in rows:
        match = images_by_lower.get(row.filename.lower())
        if match is None:
            tasks.append(asyncio.sleep(0, result=BatchItemResult(
                filename=row.filename,
                status="ERROR",
                error=f"No matching image uploaded for '{row.filename}'"
            )))
            continue

        actual_filename, (image_bytes, content_type) = match
        tasks.append(_verify_one_batch_item(
            actual_filename, image_bytes, content_type, row, semaphore
        ))

    items = await asyncio.gather(*tasks)

    passed = sum(1 for i in items if i.status == "PASS")
    failed = sum(1 for i in items if i.status == "FAIL")
    needs_review = sum(1 for i in items if i.status == "NEEDS REVIEW")
    errored = sum(1 for i in items if i.status == "ERROR")

    return BatchSummary(
        total=len(items),
        passed=passed,
        failed=failed,
        needs_review=needs_review,
        errored=errored,
        items=list(items)
    )

