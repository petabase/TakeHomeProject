---
title: TTB Label Verifier
emoji: 🍺
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# TTB Label Verifier

An AI-powered prototype built for the TTB Brewery/Label Compliance take-home assignment. It helps TTB compliance agents verify alcohol beverage label images against COLA application data, using Google Gemini Vision (`gemini-2.5-flash`) for OCR-style field extraction.

**Live demo:** [huggingface.co/spaces/OttoDC/TTB-Label-Verifier](https://huggingface.co/spaces/OttoDC/TTB-Label-Verifier)
**Source code:** [github.com/petabase/TakeHomeProject](https://github.com/petabase/TakeHomeProject)

---

## Background

This prototype responds to a TTB internal tip from a brewery investigator: mislabeled or non-compliant kegs and bottles are a recurring compliance gap, and manual visual review of label images against application data is slow and error-prone. The tool automates the first-pass comparison so agents can focus their attention on flagged discrepancies rather than re-reading every field on every label.

## What it does

1. The agent uploads a label image (JPEG/PNG) and manually enters the key application fields submitted with the COLA application: brand name, class/type, alcohol by volume (ABV), net contents, and whether a Government Warning is required.
2. The image is sent to Gemini Vision, which extracts what is actually printed on the label as structured JSON.
3. Each field is compared against the submitted application data using exact, normalized, and fuzzy matching.
4. The agent receives a field-by-field compliance report with one of four confidence tiers per field:

   | Tier | Meaning |
   |---|---|
   | ✅ **Confirmed** | Exact match between label and application |
   | ⚠️ **Likely Match** | Matches after normalizing case/punctuation, or a partial match — worth a quick glance |
   | ❌ **Needs Manual Review** | Values disagree, or required Government Warning text doesn't match the statutory wording |
   | 🔲 **Unreadable** | Gemini could not read this field from the image (blur, glare, angle, etc.) |

   The overall result is **PASS** only if every field is Confirmed; any Needs Review tier fails the label outright; any Likely Match or Unreadable tier (with no outright failures) returns **NEEDS REVIEW**.

## Architecture

```
Browser (HTML + HTMX)
        │  POST /verify (multipart form: image + fields)
        ▼
FastAPI server (app/main.py)
        │  reads image bytes + Pydantic-validated form data
        ▼
verifier.py
        │  builds prompt, calls Gemini Vision (server-side only)
        ▼
Google Gemini API (gemini-2.5-flash)
        │  returns structured JSON field extraction
        ▼
Field comparison logic (exact → normalized → fuzzy)
        ▼
result.html — rendered compliance report
```

### Why this shape, specifically

**Server-side AI calls only.** The browser never talks to Gemini directly — it only ever talks to our own FastAPI server. The Gemini API key lives in an environment variable / HF Secret and is never exposed to the client. This was a deliberate response to a stated pain point in the assignment interviews: a prior compliance tool made AI calls from the browser, and TTB's network firewall blocked those outbound calls entirely, breaking the tool for end users. Routing every AI call through our own backend means the only outbound dependency is *our server* reaching `generativelanguage.googleapis.com` — a single, predictable egress path that's far easier for a network team to allowlist than dozens of client-side ML endpoints.

**No persistent storage.** The app is stateless by design — nothing is written to disk or a database between requests. This avoids any question of where label images or compliance results are retained, which matters for a government compliance tool.

**Confidence tiers instead of binary pass/fail.** Real-world labels have benign formatting variance (e.g., `Stone's Throw` vs `STONE'S THROW`), but the Government Warning statement has zero tolerance for wording drift since it's a statutory requirement (27 CFR 16.21). The matching logic treats these differently on purpose — see `app/verifier.py`.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Required to support free-tier AI usage cleanly; team familiarity |
| Backend | FastAPI 0.115.5 | Async-first, fast, minimal boilerplate, built-in request validation via Pydantic |
| AI / Vision | Google Gemini (`gemini-2.5-flash`) via `google-genai` SDK | Free tier with generous limits (15 req/min, 1,500/day); multimodal image input |
| Frontend | Jinja2 templates + HTMX | No build step, no Node dependency — keeps the whole stack pure Python |
| Server | Uvicorn | Standard ASGI server for FastAPI |
| Deployment | Hugging Face Spaces (Docker SDK) | Free, public URL, native Docker support |

## Setup (local development)

### Prerequisites
- Python 3.12 (a virtual environment is strongly recommended — see the dependency note below)
- A free Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

### Install and run

```bash
git clone https://github.com/petabase/TakeHomeProject.git
cd TakeHomeProject/deployment

python3.12 -m venv venv
source venv/bin/activate

pip install -r requirements.txt

cp .env.sample .env
# edit .env and set GEMINI_API_KEY=your-actual-key

uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` in your browser.

### Environment variables

| Variable | Description |
|---|---|
| `GEMINI_API_KEY` | Free API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (required) |

On Hugging Face Spaces, this is set as a **Secret** (Settings → Variables and Secrets) rather than a `.env` file — it is encrypted and never visible in the public repo or Space files.

## Assumptions made

Per the assignment instructions, the goal was to complete this from the written brief without back-and-forth clarification wherever a reasonable assumption could be made. These are the assumptions baked into this build:

1. **Image formats:** JPEG and PNG only. PDF label submissions (sometimes used in COLA filings) are out of scope for this prototype.
2. **Application data entry:** Manual form fields rather than a COLA database integration or JSON upload — there was no specified source system to integrate against, so a direct-entry form was the most representative simulation of "what's in the application."
3. **Government Warning validation:** Checked against the exact statutory text in 27 CFR 16.21, with exact-match required for a "Confirmed" result. Case/punctuation drift is downgraded to "Likely Match" rather than an automatic fail, since OCR-style extraction can introduce minor formatting noise.
4. **Single-label workflow in this build:** The current implementation verifies one label per request. The architecture (stateless, single Gemini call per image) is intentionally suited to batch processing as a follow-up — a queue of N images, each independently verified — but a UI for batch upload was deprioritized in favor of getting the core verification logic correct within the time available.
5. **No authentication:** This is an open prototype for evaluation purposes, not a production deployment behind TTB's identity system.
6. **Outbound network access:** Gemini API calls require outbound HTTPS access to `generativelanguage.googleapis.com`. This is available on Hugging Face Spaces. In a real TTB deployment behind a restrictive firewall, this single domain would need to be allowlisted for the application server (not end-user browsers, since all AI calls are server-side).

## A note on dependency pinning

During development, an unpinned `starlette` dependency resolved to a brand-new major version (1.x) that has a breaking change incompatible with FastAPI's Jinja2 `TemplateResponse` call signature, causing a `TypeError: unhashable type: 'dict'` on every page load. The fix was pinning `fastapi==0.115.5`, which has an explicit `starlette<0.42.0,>=0.40.0` requirement, ensuring a known-compatible pair. This is reflected in the pinned `requirements.txt` and is a good example of why exact version pins (not just `>=` ranges) matter for reproducible deployments.

## Testing

Test labels were sourced from two places: real approved label images from TTB's public COLA registry (positive cases — these should pass), and a small set of deliberately modified label images with known defects (negative cases — wrong ABV, missing Government Warning, lowercase warning text) to confirm the verifier correctly flags non-compliant labels.

## Cold start note

The free Hugging Face Spaces tier may sleep after 48 hours of inactivity. If the app takes 20–30 seconds to load on first visit, that's a cold start — subsequent requests are fast.

## Project structure

```
deployment/
├── app/
│   ├── main.py           # FastAPI routes
│   ├── verifier.py        # Gemini Vision call + field comparison logic
│   ├── models.py          # Pydantic schemas
│   └── templates/
│       ├── base.html
│       ├── index.html     # upload form
│       └── result.html    # compliance report
├── Dockerfile
├── requirements.txt
├── .env.sample
└── README.md              # this file (also serves as HF Spaces metadata)
```