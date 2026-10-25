"""Original API tests, updated to the corrected contract.

Changed deliberately: the old test asserted HTTP 200 when no fare provider was configured, which let a failure look like success.
Unavailable providers now return 503 with an explicit status, and the provider-status payload no longer names SerpAPI as the only option.
"""
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health_does_not_expose_secrets():
    r = client.get('/api/health')
    assert r.status_code == 200
    assert r.json()['status'] == 'ok'
    assert 'GROQ_API_KEY' not in r.text


def test_provider_status_lists_providers_and_labels():
    r = client.get('/api/providers')
    assert r.status_code == 200
    body = r.json()
    assert {'groq', 'tavily', 'aviationstack', 'flight_offers', 'hotel_offers', 'sandbox_flights'} <= set(body['providers'])
    assert 'UNAVAILABLE' in body['labels']


def test_flight_search_without_fare_provider_is_not_fake():
    r = client.post('/api/flights/search', json={'origin': 'Chennai', 'destination': 'Goa', 'departure_date': '2099-01-10', 'return_date': '2099-01-13'})
    body = r.json()
    assert r.status_code == 503, 'a missing provider must not be reported as HTTP 200'
    assert body['status'] == 'unavailable' and body['options'] == [] and body['data_kind'] == 'UNAVAILABLE'
    assert 'not configured' in body['message']


def test_invalid_hotel_dates_rejected():
    r = client.post('/api/hotels/search', json={'destination': 'Goa', 'check_in': '2099-01-13', 'check_out': '2099-01-10'})
    assert r.status_code == 422
