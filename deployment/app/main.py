import os
import csv
import io
import json
import uuid
import zipfile
import asyncio
from pathlib import Path
from typing import Optional, AsyncGenerator
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from app.verifier import parse_metadata_csv, verify_label, MAX_BATCH_SIZE
from app.models import ApplicationData, BatchRow, BatchItemResult, BatchSummary

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
MAX_BATCH_IMAGES = MAX_BATCH_SIZE

CSV_TEMPLATE_HEADER = "filename,brand_name,class_type,abv,net_contents,government_warning\n"
CSV_TEMPLATE_EXAMPLE_ROW = "label_001.jpg,,,,,true\n"

# ── Simple in-memory job store ────────────────────────────────────────────────
# Stores parsed job data (rows + images) keyed by a UUID.
# Entries are cleaned up after the stream completes.
_jobs: dict[str, dict] = {}


# ── Shared image parsing helper ───────────────────────────────────────────────
async def _parse_images(
    label_images: Optional[list[UploadFile]],
    label_images_zip: Optional[UploadFile],
    csv_errors: list[str],
) -> dict[str, tuple[bytes, str]]:
    """Parse uploaded files or zip into a filename→(bytes, content_type) map."""
    raw_files: list[tuple[str, bytes, str]] = []

    if label_images_zip is not None and label_images_zip.filename:
        zip_bytes = await label_images_zip.read()
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    base_name = Path(info.filename).name
                    if base_name.startswith("."):
                        continue
                    ext = base_name.lower().rsplit(".", 1)[-1] if "." in base_name else ""
                    content_type = {
                        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"
                    }.get(ext)
                    if content_type is None:
                        csv_errors.append(f"Skipped '{base_name}' inside zip: unsupported file type.")
                        continue
                    raw_files.append((base_name, zf.read(info), content_type))
        except zipfile.BadZipFile:
            csv_errors.append("The uploaded file could not be read as a .zip archive. Please re-zip and try again.")
    elif label_images:
        for f in label_images:
            content = await f.read()
            raw_files.append((f.filename, content, f.content_type))

    if len(raw_files) > MAX_BATCH_IMAGES:
        csv_errors.append(f"{len(raw_files)} images found — only the first {MAX_BATCH_IMAGES} will be processed.")
        raw_files = raw_files[:MAX_BATCH_IMAGES]

    images: dict[str, tuple[bytes, str]] = {}
    seen: set[tuple[str, int]] = set()
    dupes = 0

    for filename, content, content_type in raw_files:
        if content_type not in ALLOWED_TYPES:
            csv_errors.append(f"Skipped '{filename}': unsupported type '{content_type}'.")
            continue
        size_mb = len(content) / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            csv_errors.append(f"Skipped '{filename}': {size_mb:.1f} MB exceeds the {MAX_FILE_SIZE_MB} MB limit.")
            continue
        sig = (filename, len(content))
        if sig in seen:
            dupes += 1
            continue
        seen.add(sig)
        images[filename] = (content, content_type)

    if dupes:
        csv_errors.append(f"Skipped {dupes} duplicate file(s).")

    return images


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "max_batch": MAX_BATCH_IMAGES}
    )


@app.get("/template.csv")
async def download_template():
    content = CSV_TEMPLATE_HEADER + CSV_TEMPLATE_EXAMPLE_ROW
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ttb_label_template.csv"}
    )


@app.post("/verify", response_class=HTMLResponse)
async def verify(
    request: Request,
    metadata_csv: UploadFile = File(...),
    label_images: Optional[list[UploadFile]] = File(None),
    label_images_zip: Optional[UploadFile] = File(None),
):
    """
    Parse the uploaded files, store the job, and show the progress page.
    Actual verification runs as the browser streams /verify/progress/{job_id}.
    """
    csv_bytes = await metadata_csv.read()
    rows, csv_errors = parse_metadata_csv(csv_bytes)

    if not rows:
        return templates.TemplateResponse(
            "batch_result.html",
            {"request": request, "summary": None,
             "csv_errors": csv_errors or ["No usable rows found in CSV."],
             "is_single": False}
        )

    if not label_images and (not label_images_zip or not label_images_zip.filename):
        return templates.TemplateResponse(
            "batch_result.html",
            {"request": request, "summary": None,
             "csv_errors": ["No label images were uploaded — select file(s) or a .zip."],
             "is_single": False}
        )

    images = await _parse_images(label_images, label_images_zip, csv_errors)

    # Store job for the SSE stream to pick up
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "rows": rows,
        "images": images,
        "csv_errors": csv_errors,
        "total": len(rows),
    }

    return templates.TemplateResponse(
        "progress.html",
        {
            "request": request,
            "job_id": job_id,
            "total": len(rows),
            "csv_errors": csv_errors,
            "is_single": len(rows) == 1,
        }
    )


@app.get("/verify/progress/{job_id}")
async def verify_progress(job_id: str):
    """
    SSE endpoint — streams one JSON event per label as it completes,
    then a final 'done' event with the full summary.
    """
    job = _jobs.get(job_id)
    if not job:
        async def error_stream():
            yield "data: {\"error\": \"Job not found or already completed.\"}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    rows: list[BatchRow] = job["rows"]
    images: dict[str, tuple[bytes, str]] = job["images"]
    images_by_lower = {fname.lower(): (fname, data) for fname, data in images.items()}

    async def event_stream() -> AsyncGenerator[str, None]:
        items: list[BatchItemResult] = []
        passed = failed = needs_review = errored = 0

        for i, row in enumerate(rows):
            # Send "processing" event so the UI can show which label is running
            yield f"data: {json.dumps({'type': 'processing', 'index': i, 'filename': row.filename, 'total': len(rows)})}\n\n"

            match = images_by_lower.get(row.filename.lower())
            if match is None:
                item = BatchItemResult(
                    filename=row.filename, status="ERROR",
                    error=f"No matching image uploaded for '{row.filename}'"
                )
            else:
                actual_filename, (image_bytes, content_type) = match
                app_data = ApplicationData(
                    brand_name=row.brand_name, class_type=row.class_type,
                    abv=row.abv, net_contents=row.net_contents,
                    government_warning=row.government_warning,
                )
                result = await verify_label(image_bytes, content_type, app_data)
                item = BatchItemResult(
                    filename=actual_filename,
                    status=result.overall_status,
                    result=result,
                    error=result.error
                )

            items.append(item)
            if item.status == "PASS":         passed += 1
            elif item.status == "FAIL":       failed += 1
            elif item.status == "NEEDS REVIEW": needs_review += 1
            else:                              errored += 1

            # Send result event for this label
            yield f"data: {json.dumps({'type': 'result', 'index': i, 'item': item.model_dump()})}\n\n"

        summary = BatchSummary(
            total=len(items), passed=passed, failed=failed,
            needs_review=needs_review, errored=errored, items=items
        )

        yield f"data: {json.dumps({'type': 'done', 'summary': summary.model_dump()})}\n\n"

        # Clean up the job
        _jobs.pop(job_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disables nginx buffering on HF Spaces
        }
    )


@app.post("/render_result", response_class=HTMLResponse)
async def render_result(request: Request):
    """Called by the progress page JS after SSE completes to render the result table."""
    body = await request.json()
    summary_dict = body.get("summary", {})
    is_single = body.get("is_single", False)

    # Reconstruct the BatchSummary from the dict
    summary = BatchSummary(**summary_dict)

    return templates.TemplateResponse(
        "batch_result.html",
        {
            "request": request,
            "summary": summary,
            "csv_errors": [],
            "is_single": is_single,
        }
    )


@app.post("/export")
async def export_results(request: Request):
    """Export results as a CSV — one row per label, issues-only detail column."""
    form = await request.form()
    raw_json = form.get("summary_json", "")

    if not raw_json:
        return HTMLResponse("No results to export.", status_code=400)

    summary = json.loads(raw_json)

    def _flatten(text: str) -> str:
        return " ".join(text.split()) if text else ""

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["filename", "status", "issues_found"])

    for item in summary.get("items", []):
        filename = item.get("filename", "")
        status = item.get("status", "")
        result = item.get("result")

        if result and result.get("fields"):
            problem_fields = [f for f in result["fields"] if f.get("confidence") != "confirmed"]
            if problem_fields:
                issues = " | ".join(
                    f"{f.get('field','').replace('_',' ').title()}: {_flatten(f.get('reason',''))}"
                    for f in problem_fields
                )
            else:
                issues = "All fields confirmed — no issues found."
        else:
            issues = _flatten(item.get("result", {}).get("error", "") if item.get("result") else "") \
                     or _flatten(item.get("error", "")) \
                     or "Unknown error during verification."

        writer.writerow([filename, status, issues])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ttb_verification_results.csv"}
    )


@app.get("/health")
async def health():
    return {"status": "ok", "gemini_key_set": bool(os.getenv("GEMINI_API_KEY"))}
