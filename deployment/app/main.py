import os
import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from app.verifier import parse_metadata_csv, verify_batch, MAX_BATCH_SIZE

load_dotenv()

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="TTB Label Verifier", version="2.0.0")

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

static_dir = BASE_DIR.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/jpg"}
MAX_FILE_SIZE_MB = 10
MAX_BATCH_IMAGES = MAX_BATCH_SIZE  # 300, shared constant from verifier.py

CSV_TEMPLATE_HEADER = "filename,brand_name,class_type,abv,net_contents,government_warning\n"
CSV_TEMPLATE_EXAMPLE_ROW = "label_001.jpg,,,,,true\n"


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """
    Single upload page. Works for one label or up to MAX_BATCH_IMAGES —
    the form always takes a CSV (1+ rows) plus matching image file(s).
    Single-image verification is simply a 1-row CSV under the hood.
    """
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "max_batch": MAX_BATCH_IMAGES}
    )


@app.get("/template.csv")
async def download_template():
    """Download a 1-row CSV template pre-filled with the correct headers."""
    content = CSV_TEMPLATE_HEADER + CSV_TEMPLATE_EXAMPLE_ROW
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ttb_label_template.csv"}
    )


@app.post("/verify", response_class=HTMLResponse)
async def verify(
    request: Request,
    metadata_csv: UploadFile = File(..., description="CSV with one row per image"),
    label_images: Optional[list[UploadFile]] = File(
        None, description="One or more label images (small batches)"
    ),
    label_images_zip: Optional[UploadFile] = File(
        None, description="A .zip of label images (large batches)"
    ),
):
    """
    Unified verification endpoint. Accepts a metadata CSV (1 or many rows)
    plus label images supplied EITHER as raw multipart files (label_images,
    good for small batches where a visible file list matters) OR as a
    single .zip archive (label_images_zip, recommended at scale — e.g. 300
    images as 300 separate multipart parts is unwieldy for both the browser
    and the form; a zip is one upload, server-unpacked).

    Either way, images are paired to CSV rows by filename and run through
    the same verification path. A single-image check is just a 1-row CSV
    with one image — same code path, rendered with is_single for a simpler
    single-result view instead of a results table.
    """
    csv_bytes = await metadata_csv.read()
    rows, csv_errors = parse_metadata_csv(csv_bytes)

    if not rows:
        return templates.TemplateResponse(
            "batch_result.html",
            {
                "request": request,
                "summary": None,
                "csv_errors": csv_errors or ["No usable rows found in CSV."],
            }
        )

    # ── Collect raw (filename, bytes, content_type) tuples from whichever
    #    upload path was used, before applying shared validation below. ────
    raw_files: list[tuple[str, bytes, str]] = []

    if label_images_zip is not None and label_images_zip.filename:
        zip_bytes = await label_images_zip.read()
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    # Use just the base filename — ignore any folder structure
                    # inside the zip, since the CSV matches on filename only.
                    base_name = Path(info.filename).name
                    if base_name.startswith("."):
                        continue  # skip macOS junk like .DS_Store, __MACOSX
                    ext = base_name.lower().rsplit(".", 1)[-1] if "." in base_name else ""
                    content_type = {
                        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"
                    }.get(ext)
                    if content_type is None:
                        csv_errors.append(
                            f"Skipped '{base_name}' inside zip: unsupported file type."
                        )
                        continue
                    content = zf.read(info)
                    raw_files.append((base_name, content, content_type))
        except zipfile.BadZipFile:
            csv_errors.append(
                "The uploaded file could not be read as a .zip archive. "
                "Please re-zip your images and try again."
            )
    elif label_images:
        for f in label_images:
            content = await f.read()
            raw_files.append((f.filename, content, f.content_type))
    else:
        return templates.TemplateResponse(
            "batch_result.html",
            {
                "request": request,
                "summary": None,
                "csv_errors": ["No label images were uploaded — select file(s) or a .zip."],
            }
        )

    if len(raw_files) > MAX_BATCH_IMAGES:
        csv_errors.append(
            f"{len(raw_files)} images found, exceeding the "
            f"{MAX_BATCH_IMAGES}-image batch limit. "
            f"Only the first {MAX_BATCH_IMAGES} will be processed."
        )
        raw_files = raw_files[:MAX_BATCH_IMAGES]

    images: dict[str, tuple[bytes, str]] = {}
    seen_signatures: set[tuple[str, int]] = set()
    duplicate_count = 0

    for filename, content, content_type in raw_files:
        if content_type not in ALLOWED_TYPES:
            csv_errors.append(
                f"Skipped '{filename}': unsupported type '{content_type}'."
            )
            continue

        size_mb = len(content) / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            csv_errors.append(
                f"Skipped '{filename}': {size_mb:.1f} MB exceeds the "
                f"{MAX_FILE_SIZE_MB} MB per-image limit."
            )
            continue

        signature = (filename, len(content))
        if signature in seen_signatures:
            duplicate_count += 1
            continue
        seen_signatures.add(signature)

        images[filename] = (content, content_type)

    if duplicate_count:
        csv_errors.append(
            f"Skipped {duplicate_count} duplicate file(s) "
            f"(same filename + file size already seen in this batch)."
        )

    summary = await verify_batch(rows, images)

    return templates.TemplateResponse(
        "batch_result.html",
        {
            "request": request,
            "summary": summary,
            "csv_errors": csv_errors,
            "is_single": summary.total == 1,
        }
    )


@app.post("/export")
async def export_results(request: Request):
    """
    Export batch (or single) results as a CSV download — one row per label.
    Only fields that did NOT cleanly confirm are surfaced in the "issues"
    column, so an agent scanning the export immediately sees what needs
    attention rather than wading through every field on every row. Text is
    flattened to single lines so the file opens cleanly in Excel, Google
    Sheets, or a database import without embedded line breaks splitting a
    field across multiple visual rows.
    """
    form = await request.form()
    raw_json = form.get("summary_json", "")

    if not raw_json:
        return HTMLResponse("No results to export.", status_code=400)

    summary = json.loads(raw_json)

    def _flatten(text: str) -> str:
        """Collapse any newlines/extra whitespace so the field stays on one
        visual line when opened in a spreadsheet."""
        if not text:
            return ""
        return " ".join(text.split())

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["filename", "status", "issues_found"])

    for item in summary.get("items", []):
        filename = item.get("filename", "")
        status = item.get("status", "")
        result = item.get("result")

        if result and result.get("fields"):
            problem_fields = [
                f for f in result["fields"] if f.get("confidence") != "confirmed"
            ]
            if problem_fields:
                issue_parts = [
                    f"{f.get('field', '').replace('_', ' ').title()}: {_flatten(f.get('reason', ''))}"
                    for f in problem_fields
                ]
                issues = " | ".join(issue_parts)
            else:
                issues = "All fields confirmed — no issues found."
        else:
            issues = _flatten(item.get("error", "")) or "Unknown error during verification."

        writer.writerow([filename, status, issues])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ttb_verification_results.csv"}
    )


@app.get("/health")
async def health():
    """Health check endpoint for HF Spaces."""
    return {
        "status": "ok",
        "gemini_key_set": bool(os.getenv("GEMINI_API_KEY"))
    }
