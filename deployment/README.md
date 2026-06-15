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

An AI-powered prototype that helps TTB compliance agents verify alcohol beverage labels against application data, using Google Gemini Vision (gemini-1.5-flash).

## What it does

Upload a label image alongside key application fields (brand name, ABV, class/type, net contents, Government Warning). Gemini Vision extracts what is actually printed on the label and compares it field-by-field against the submitted application data, returning a structured compliance report with confidence tiers: **confirmed**, **likely match**, or **needs manual review**.

## Setup (local development)

### Prerequisites
- Python 3.12
- A free Gemini API key from [aistudio.google.com](https://aistudio.google.com)

### Install and run

```bash
git clone https://github.com/petabase/TakeHomeProject.git
cd TakeHomeProject
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and add your GEMINI_API_KEY
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` in your browser.

### Environment variables

| Variable | Description |
|---|---|
| `GEMINI_API_KEY` | Free API key from aistudio.google.com (required) |

## Approach

- **Backend:** FastAPI (Python 3.12) with async request handling
- **AI:** Google Gemini 1.5 Flash multimodal — label image sent as base64, structured JSON compliance report extracted via prompted output
- **Frontend:** Jinja2 templates + HTMX for dynamic updates, no JS framework required
- **Deployment:** Hugging Face Spaces (Docker SDK), port 7860

## Assumptions made

1. Label images are JPEG or PNG only (PDF not supported in this prototype)
2. Application metadata is entered manually via form fields — no COLA database integration
3. Batch processing supports up to 300 images per session; no cross-session persistence
4. The Government Warning text is validated against the exact statutory wording (exact case and punctuation match required)
5. Duplicate images in a batch are detected client-side by filename + file size and skipped automatically
6. Deployment runs on Hugging Face Spaces; Gemini API outbound access is available from that environment

## Cold start note

The free HF Spaces tier may sleep after 48 hours of inactivity. If the app takes 20–30 seconds to load initially, that is a cold start — subsequent requests will be fast.

## Source code

[github.com/petabase/TakeHomeProject](https://github.com/petabase/TakeHomeProject)

## Live demo

[huggingface.co/spaces/OttoDC/TTB-Label-Verifier](https://huggingface.co/spaces/OttoDC/TTB-Label-Verifier)
