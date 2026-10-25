# TravelAI Pro

Three cooperating agents for Indian trips:

1. **Flight Search Agent** – up to 5 real flight offers within your budget (over-budget alternatives shown separately).
2. **Hotel Search Agent** – up to 5 real hotel offers within your budget.
3. **12-hour Day-Plan Agent** – a timed itinerary using Tavily web research + Groq, your arrival time, and your selected flight/hotel.

Prices **only** come from a fare/hotel provider. The AI (Groq) never produces prices, flight numbers, availability or booking links. If the fare provider is unavailable, the Flight Agent asks Tavily for up to five relevant flight-search pages and displays them as external research links; these are not represented as individual flight offers, confirmed availability, or fares.

## 1. What works without any key, and what needs one

| Feature | Needs | Without it |
|---|---|---|
| App starts, UI loads, tests pass | nothing | – |
| Flight fares / hotel prices | `SERPAPI_API_KEY` (optional; not required) | Flight search shows up to five Tavily-researched external flight-search pages plus Google Flights route search when Tavily is configured; prices remain unavailable. Hotel prices remain unavailable plus a labelled search link. |
| Day plan | `GROQ_API_KEY` (+ `TAVILY_API_KEY` for sourced research) | Day plan unavailable; with Groq but no Tavily the plan is marked "not backed by sources" |
| Flight status / schedules | `AVIATIONSTACK_API_KEY` | Not used for fares, ever |
| Sandbox flight provider | exact provider name + docs needed | **Not implemented yet** (see "Known limitations") |

## 2. Quickest way on Windows
Right-click the ZIP > **Extract All**. The new folder contains the project directly (`backend`, `frontend`, `wheels`...). **Double-click `START_HERE.bat`**. It stops any older server on port 8000, sets up the environment, installs dependencies, opens `.env` for your keys on first run, and opens the app. The page header must say **v3.0.0 · backend connected**; if it shows something else you are looking at an older copy.

## 2b. Manual Windows setup (PowerShell + VS Code)

Prerequisites: Python 3.12 or 3.13 (tested on 3.12; if `pip install` fails on a newer Python such as 3.14, install 3.12 from python.org), VS Code.

```powershell
# 1. Extract the ZIP, then open the TravelAI_Pro folder in VS Code (File > Open Folder).
# 2. Open a terminal in VS Code (Terminal > New Terminal) and go to the project:
cd path\to\TravelAI_Pro

# 3. Create/activate a virtual environment. Yours can live in the PARENT folder:
python -m venv ..\.venv
..\.venv\Scripts\Activate.ps1
#    If PowerShell blocks scripts, run once:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 4. Install dependencies
python -m pip install -r requirements.txt

# 5. Create your private settings file and edit it in VS Code
Copy-Item .env.example .env
code .env
```

Put each key after its `=` sign in `.env` (no quotes, no spaces): `GROQ_API_KEY`, `TAVILY_API_KEY`, `AVIATIONSTACK_API_KEY`, and optionally `SERPAPI_API_KEY`. **Never paste keys in chat, code or screenshots.** If a key was ever exposed, revoke it in the provider's dashboard and make a new one.

### Start the app
```powershell
python -m uvicorn backend.main:app --reload --port 8000
```
Open **http://127.0.0.1:8000/ui/** (the frontend is served by the backend; there is no separate frontend server or Node build). API docs: http://127.0.0.1:8000/docs

### Run the tests
```powershell
python -m pytest            # whole suite, no keys or internet needed
python -m pytest -rs        # also lists skipped tests with reasons
```
Optional live tests (only if you have real keys in `.env`; tiny requests, no bookings):
```powershell
$env:RUN_LIVE_TESTS="1"; python -m pytest tests/test_live_optional.py -rs
```

### Check provider status
* In the UI, the **Data sources & status** card shows which keys are present. “KEY PRESENT” does **not** prove the key works.
* Click **Test provider connections** (or `POST /api/providers/check`) to make one tiny real request each to Tavily and Groq.

## 3. Data labels
* **LIVE** – real provider request made just now. Still verify before buying.
* **SANDBOX** – test data from a sandbox; not real offers.
* **CACHED** – an earlier provider response.
* **ESTIMATED** – AI/approximate figure, not a quote (all day-plan costs are estimates).
* **UNAVAILABLE** – provider missing or failed. The API returns HTTP 503/502 in this case, never 200.

“DIRECT OFFER LINK” appears only when the provider supplied a link for that exact offer. Otherwise you see “SEARCH PAGE — not a booking link”.

## 4. Common errors
| Message / symptom | Fix |
|---|---|
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, reopen terminal |
| `ModuleNotFoundError: backend` | Run commands from the `TravelAI_Pro` folder |
| `ModuleNotFoundError: requests` | `python -m pip install -r requirements.txt` (requests was missing from the old file) |
| “… rejected the API key” | Key wrong/expired: regenerate it and update `.env`, restart the server |
| “… rate limit or quota reached” | Free-tier quota used up; wait or reduce searches |
| “… could not be reached” / “took too long” | Internet/firewall/VPN problem; retry. Nothing is faked when this happens |
| Port 8000 busy | `--port 8001` |
| Edited `.env` but nothing changed | Stop (Ctrl+C) and restart uvicorn |

## 5. Architecture
```
backend/main.py                 routes (HTTP status mirrors real success/failure)
backend/agents/                 flight_agent, hotel_agent, itinerary_agent, orchestrator, schemas, store
backend/providers/              flight/hotel provider interfaces (SerpAPI adapter; sandbox placeholder)
backend/services/               Groq, Tavily, SerpAPI, Aviationstack, location data, FX, link checks
backend/tools/                  budget.py (currency-aware filtering/totals), ranking.py, link_tools.py (URL safety)
backend/data/                   airports.csv (122 Indian airports), cities.csv (4,200+ Indian places)
scripts/build_india_reference.py  rebuilds the India data from OurAirports + GeoNames-derived lists
```
Agents pass **IDs**, not objects: a selected flight/hotel is looked up in a server-side store, so its provider, price and timestamp cannot be altered by the browser.

## 6. Known limitations (honest list)
* **No fare provider is verified end-to-end.** The SerpAPI adapter is optional and covered by mocked tests only; no real SerpAPI request has been made. When it is absent, Tavily provides relevant external flight-search pages, not individual flight offers or fares.
* **"SandAPI" is unidentified.** A placeholder exists; no endpoints were guessed. Tell the developer the exact provider name and docs URL.
* Aviationstack is not a fare source. Its free tier is plain `http` only.
* Travel times and opening hours in the day plan are **not** verified (no routing/hours provider). Weather is not checked. Landing-to-ready buffer (150 min) is an assumption.
* Airport coverage: 122 Indian airports with scheduled service; places without an airport use the nearest airports (distance shown). Small localities missing from the dataset (e.g. Hosur) return a clear "not found" message.
* Currency conversion is not performed: offers in another currency are excluded and counted.
* Children are counted for the passenger total; fares are per-person adult quotes, so child fares are approximated by the same figure.
* The frontend was syntax-checked and served, but not exercised in a real browser during development.

## 7. Installing with no internet access to PyPI (offline)
The `wheels` folder contains every package for **Windows 64-bit, Python 3.14**. If `pip install -r requirements.txt` times out:
```powershell
python -m pip install --no-index --find-links wheels -r requirements.txt
```
(Other Python versions need the normal online install.)
