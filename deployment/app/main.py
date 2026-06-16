import os
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from app.models import ApplicationData
from app.verifier import verify_label

load_dotenv()

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="TTB Label Verifier", version="1.0.0")

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Mount static files only if the folder exists
static_dir = BASE_DIR.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ── Allowed image types ────────────────────────────────────────────────────────
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/jpg"}
MAX_FILE_SIZE_MB = 10


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Landing page with upload form."""
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )


@app.post("/verify", response_class=HTMLResponse)
async def verify(
    request: Request,
    label_image: UploadFile = File(..., description="Label image (JPEG or PNG)"),
    brand_name: str = Form(...),
    class_type: str = Form(...),
    abv: str = Form(...),
    net_contents: str = Form(...),
    government_warning: bool = Form(default=True),
):
    """Receive label image + application data, return compliance report."""

    # Validate file type
    if label_image.content_type not in ALLOWED_TYPES:
        return templates.TemplateResponse(
            "result.html",
            {
                "request": request,
                "error": f"Unsupported file type '{label_image.content_type}'. "
                         f"Please upload a JPEG or PNG image.",
                "result": None,
                "app_data": None,
                "filename": label_image.filename,
            }
        )

    # Read image bytes
    image_bytes = await label_image.read()

    # Validate file size
    size_mb = len(image_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        return templates.TemplateResponse(
            "result.html",
            {
                "request": request,
                "error": f"File too large ({size_mb:.1f} MB). Maximum is {MAX_FILE_SIZE_MB} MB.",
                "result": None,
                "app_data": None,
                "filename": label_image.filename,
            }
        )

    # Build application data model
    app_data = ApplicationData(
        brand_name=brand_name.strip(),
        class_type=class_type.strip(),
        abv=abv.strip(),
        net_contents=net_contents.strip(),
        government_warning=government_warning,
    )

    # Run verification
    result = await verify_label(image_bytes, label_image.content_type, app_data)

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "result": result,
            "app_data": app_data,
            "filename": label_image.filename,
            "error": result.error if result.error else None,
        }
    )


@app.get("/health")
async def health():
    """Health check endpoint for HF Spaces."""
    return {
        "status": "ok",
        "gemini_key_set": bool(os.getenv("GEMINI_API_KEY"))
    }
