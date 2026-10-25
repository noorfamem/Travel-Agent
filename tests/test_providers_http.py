"""Provider failure handling at the HTTP layer (injected fake transport: no network, no real keys)."""
import datetime as dt

import pytest

from backend.config.settings import Settings
from backend.services.aviationstack_service import AviationStackService
from backend.services.llm_service import LLMService
from backend.services.location_service import LocationService
from backend.services.serpapi_service import SerpApiService
from backend.services.tavily_service import TavilyService
from backend.utils.cache import TTLCache
from backend.utils.errors import ErrorKind, ProviderError
from backend.utils.http_client import HttpClient
from .conftest import future


class Resp:
    def __init__(self, code=200, body=None, bad_json=False):
        self.status_code, self._b, self._bad = code, body if body is not None else {}, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._b


def client(*responses):
    calls = []
    seq = list(responses)

    def transport(method, url, **kw):
        calls.append(url)
        r = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(r, Exception):
            raise r
        return r
    return HttpClient(retries=2, transport=transport, backoff=0.0), calls


def test_timeout_is_retried_then_raised():
    http, calls = client(TimeoutError("slow"))
    with pytest.raises(ProviderError) as e:
        http.request_json("P", "GET", "https://x.example/a")
    assert e.value.kind == ErrorKind.TIMEOUT and len(calls) == 3


def test_connection_error_classified_network():
    http, _ = client(ConnectionError("down"))
    with pytest.raises(ProviderError) as e:
        http.request_json("P", "GET", "https://x.example/a")
    assert e.value.kind == ErrorKind.NETWORK


def test_auth_error_not_retried():
    http, calls = client(Resp(401))
    with pytest.raises(ProviderError) as e:
        http.request_json("P", "GET", "https://x.example/a")
    assert e.value.kind == ErrorKind.AUTH and len(calls) == 1


def test_rate_limit_retried_then_raised():
    http, calls = client(Resp(429))
    with pytest.raises(ProviderError) as e:
        http.request_json("P", "GET", "https://x.example/a")
    assert e.value.kind == ErrorKind.RATE_LIMITED and len(calls) == 3


def test_bad_request_not_retried_and_server_error_retried():
    http, calls = client(Resp(400, {"error": "bad"}))
    with pytest.raises(ProviderError) as e:
        http.request_json("P", "GET", "https://x.example/a")
    assert e.value.kind == ErrorKind.BAD_REQUEST and len(calls) == 1
    http, calls = client(Resp(503))
    with pytest.raises(ProviderError) as e:
        http.request_json("P", "GET", "https://x.example/a")
    assert e.value.kind == ErrorKind.SERVER and len(calls) == 3


def test_malformed_json_reported():
    http, _ = client(Resp(200, bad_json=True))
    with pytest.raises(ProviderError) as e:
        http.request_json("P", "GET", "https://x.example/a")
    assert e.value.kind == ErrorKind.INVALID_RESPONSE


def test_error_messages_never_contain_keys():
    http, _ = client(Resp(401))
    with pytest.raises(ProviderError) as e:
        http.request_json("P", "GET", "https://x.example/a", headers={"Authorization": "Bearer SECRET-KEY-123"})
    assert "SECRET-KEY-123" not in str(e.value)


def settings(**kw):
    return Settings(**kw)


def test_llm_missing_key_and_failure_modes():
    http, _ = client(Resp(200, {"choices": []}))
    assert LLMService(settings(), http).configured is False
    with pytest.raises(ProviderError) as e:
        LLMService(settings(), http).complete("s", "u")
    assert e.value.kind == ErrorKind.NOT_CONFIGURED
    llm = LLMService(settings(groq_api_key="gsk_test_value_123456"), http)
    with pytest.raises(ProviderError) as e:
        llm.complete("s", "u")
    assert e.value.kind == ErrorKind.INVALID_RESPONSE  # malformed provider payload
    for code, kind in ((401, ErrorKind.AUTH), (429, ErrorKind.RATE_LIMITED)):
        http, _ = client(Resp(code))
        with pytest.raises(ProviderError) as e:
            LLMService(settings(groq_api_key="gsk_test_value_123456"), http).complete("s", "u")
        assert e.value.kind == kind


def test_tavily_failures_and_empty_results():
    s = settings(tavily_api_key="tvly-test-123456")
    with pytest.raises(ProviderError) as e:
        TavilyService(settings(), client(Resp())[0], TTLCache()).search("x")
    assert e.value.kind == ErrorKind.NOT_CONFIGURED
    with pytest.raises(ProviderError) as e:
        TavilyService(s, client(TimeoutError())[0], TTLCache()).search("x")
    assert e.value.kind == ErrorKind.TIMEOUT
    with pytest.raises(ProviderError) as e:
        TavilyService(s, client(Resp(200, {"unexpected": 1}))[0], TTLCache()).search("x")
    assert e.value.kind == ErrorKind.INVALID_RESPONSE
    assert TavilyService(s, client(Resp(200, {"results": []}))[0], TTLCache()).search("x").results == []


def test_tavily_drops_unsafe_urls():
    body = {"results": [{"title": "ok", "url": "https://good.example/p", "content": "c"}, {"title": "bad", "url": "file:///etc/passwd", "content": "c"}, {"title": "js", "url": "javascript:alert(1)", "content": "c"}]}
    r = TavilyService(settings(tavily_api_key="tvly-test-123456"), client(Resp(200, body))[0], TTLCache()).search("x")
    assert [x.url for x in r.results] == ["https://good.example/p"]


def test_aviationstack_failures():
    loc = LocationService()
    d = future()
    with pytest.raises(ProviderError) as e:
        AviationStackService(settings(), client(Resp())[0], loc).future_schedule("MAA", "GOI", d)
    assert e.value.kind == ErrorKind.NOT_CONFIGURED
    s = settings(aviationstack_api_key="avs-test-123456", aviationstack_future=True)
    with pytest.raises(ProviderError) as e:
        AviationStackService(s, client(Resp(200, {"error": {"code": "invalid_access_key"}}))[0], loc).future_schedule("MAA", "GOI", d)
    assert e.value.kind == ErrorKind.AUTH
    with pytest.raises(ProviderError) as e:
        AviationStackService(s, client(ConnectionError())[0], loc).future_schedule("MAA", "GOI", d)
    assert e.value.kind == ErrorKind.NETWORK


def test_aviationstack_never_returns_prices():
    loc = LocationService()
    s = settings(aviationstack_api_key="avs-test-123456", aviationstack_future=True)
    data = {"data": [{"airline": {"name": "IndiGo"}, "flight": {"iataNumber": "6E123"}, "departure": {"scheduledTime": "09:00"}, "arrival": {"iataCode": "GOI", "scheduledTime": "11:00"}}]}
    for o in AviationStackService(s, client(Resp(200, data))[0], loc).future_schedule("MAA", "GOI", future()):
        assert o.price_per_person is None and o.total_price is None


def test_serpapi_malformed_and_empty_payloads_do_not_crash():
    svc = SerpApiService(settings(serpapi_api_key="serp-test-123456"), client(Resp())[0], TTLCache(), LocationService())
    assert svc.parse_flights({}, "outbound", "INR", __import__("backend.models.travel_models", fromlist=["Cabin"]).Cabin.ECONOMY, False, None) == []
    junk = {"best_flights": [{"flights": [{"departure_airport": {}, "arrival_airport": {}}]}, {"price": "oops"}, "string", None], "other_flights": 5}
    try:
        out = svc.parse_flights(junk, "outbound", "INR", __import__("backend.models.travel_models", fromlist=["Cabin"]).Cabin.ECONOMY, False, None)
    except Exception as exc:  # pragma: no cover - the test documents the contract
        pytest.fail(f"malformed provider flight data crashed the parser: {type(exc).__name__}")
    assert out == []
    assert svc.parse_hotels({"properties": [{"name": "X", "type": "hotel", "rate_per_night": "bad"}, None, 5]}, "INR", future(), future(2), 1, None) is not None
