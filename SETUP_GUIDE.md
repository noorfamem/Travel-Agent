# TravelAI Pro v4 — Setup & Run Guide

## What's Fixed in This Version

| Issue | Status |
|---|---|
| Hotel agent SerpAPI error (empty destination) | ✅ Fixed |
| Hotel fallback chain (OSM + Tavily) | ✅ Added |
| Hotel destination auto-filled from flight | ✅ Fixed |
| Live budget tracker bar | ✅ Added |
| Flight booking URLs | ✅ Fixed |
| 152 unit tests | ✅ All passing |

---

## Quick Start (Windows)

### Step 1 — Extract ZIP
Extract `TravelAI_Pro_v4_Final.zip` to any folder, e.g.:
```
C:\TravelAI_Pro\
```

### Step 2 — Create Python virtual environment
```powershell
cd C:\TravelAI_Pro\7FEDDAEE-294F-45C6-8BAD-4B620D7F0D52-export
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Step 3 — Create your `.env` file
Copy `.env.example` to `.env` and fill in your API keys:
```powershell
Copy-Item .env.example .env
notepad .env
```

Required keys:
```env
GROQ_API_KEY=gsk_...          # Free at console.groq.com
SERPAPI_API_KEY=...            # At serpapi.com (free plan: 100 searches/month)
TAVILY_API_KEY=tvly-dev-...   # Free at tavily.com
LLM_MODEL=qwen/qwen3.8-27b
DOMESTIC_ONLY=false
```

### Step 4 — Run the server
```powershell
cd C:\TravelAI_Pro\7FEDDAEE-294F-45C6-8BAD-4B620D7F0D52-export
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### Step 5 — Open in browser
Go to: **http://127.0.0.1:8000/ui/**

---

## How to Use

1. **Flights tab** — Enter origin/destination (any city in the world), dates, passengers → Search
2. **Hotels tab** — Destination auto-fills from your selected flight → adjust dates → Search
3. **Day Plan tab** — Enter destination → Generate 12-hour itinerary
4. **Budget Tracker** — Set your total trip budget at the top bar; it shows remaining budget live

---

## Run Tests
```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
# Expected: 152 passed, 2 skipped
```
