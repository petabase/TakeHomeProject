---
title: TTB Label Verifier
emoji: 🍺
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# TTB Label Verifier — Deployment

This folder is the complete, self-contained application. It's deployed as-is to Hugging Face Spaces (the front matter above is HF Space metadata) and can also be run locally with no other setup beyond Python and a free Gemini API key.

For background on what this tool does and why it's built this way, see the [root README](../README.md).

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Tested on 3.12 and 3.13 |
| Backend | FastAPI 0.115.5 | Async-first, built-in request validation via Pydantic |
| AI / Vision | Google Gemini (`gemini-2.5-flash`) via `google-genai` SDK | Free tier (15 req/min, 1,500/day); multimodal image input |
| Frontend | Jinja2 templates + HTMX | No build step, no Node dependency |
| Server | Uvicorn | Standard ASGI server for FastAPI |
| Deployment | Hugging Face Spaces (Docker SDK) | Free, public URL, native Docker support |

## Setup (local development)

### Prerequisites
- Python 3.12 or newer
- A free Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

### Fastest path

From the repo root:

```bash
./run.sh
```

or

```bash
make
```

This creates a virtual environment, installs dependencies, prompts for a `GEMINI_API_KEY` if one isn't already set in `deployment/.env`, and starts the server on `http://localhost:8000`. It's safe to re-run.

### Manual setup

```bash
git clone https://github.com/petabase/TakeHomeProject.git
cd TakeHomeProject/deployment

python3.12 -m venv ../venv
source ../venv/bin/activate

pip install -r requirements.txt

cp .env.sample .env
# edit .env and set GEMINI_API_KEY=your-actual-key

uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` in your browser.

## Environment variables

| Variable | Description |
|---|---|
| `GEMINI_API_KEY` | Free API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (required) |

On Hugging Face Spaces, this is set as a **Secret** (Settings → Variables and Secrets) rather than a `.env` file — encrypted, and never visible in the public repo or Space files. Locally, it's read from `deployment/.env`, which is excluded from git via `.gitignore`.

## A note on dependency pinning

During development, an unpinned `starlette` dependency resolved to a new major version (1.x) with a breaking change to FastAPI's Jinja2 `TemplateResponse` call signature, causing a `TypeError: unhashable type: 'dict'` on every page load. The fix was pinning `fastapi==0.115.5`, which declares an explicit `starlette<0.42.0,>=0.40.0` requirement, guaranteeing a known-compatible pair rather than whatever the resolver picks up that day. `requirements.txt` pins exact versions throughout for the same reason — this is a real example of why `>=` ranges aren't sufficient for a reproducible deployment.

## Cold start note

The free Hugging Face Spaces tier may sleep after 48 hours of inactivity. If the app takes 20-30 seconds to load on first visit, that's a cold start — subsequent requests are fast.

For the full project structure, see the [root README](../README.md#project-structure).
