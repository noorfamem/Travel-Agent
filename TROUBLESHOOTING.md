# Troubleshooting (Windows)

## Flight search says fares are unavailable
This is expected when no live fare provider is configured. The app will still show a Google Flights route-search page and, if `TAVILY_API_KEY` is configured, up to five pages returned by Tavily that may help you search the route. These are external search/research pages, not confirmed individual flights, prices, or availability.

Aviationstack flight status/schedule data is not a ticket-fare API. Future schedule access depends on your Aviationstack subscription and `AVIATIONSTACK_FUTURE_ENABLED=true`; it still does not provide fares.

## Tavily links are missing
1. Check `TAVILY_API_KEY` in `.env` (never share the key).
2. Restart the backend after editing `.env`.
3. Use `POST /api/providers/check` or the UI provider check to confirm Tavily authentication.
4. If Tavily returns no relevant pages, the Google Flights route-search link remains available.

## Backend does not start
- Open PowerShell in the extracted project root, where `requirements.txt` is located.
- Activate your virtual environment and run `python -m pip install -r requirements.txt`.
- Start with `python -m uvicorn backend.main:app --reload --port 8000`.
- Check `/api/health` and `/docs`.

## Security
Keep `.env` private. Do not paste API keys into screenshots, source files, or chat. If a key has been exposed, revoke it with the provider and generate a replacement.
