"""LIVE integration tests. They run ONLY when you put real keys in your environment/.env AND set RUN_LIVE_TESTS=1.
They make a few tiny real requests (no bookings, no payments). Without keys they are SKIPPED, which is not the same as passing.
"""
import os

import pytest
from dotenv import load_dotenv

load_dotenv()
LIVE = os.getenv("RUN_LIVE_TESTS") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="set RUN_LIVE_TESTS=1 (and real keys in .env) to run live provider tests")


@pytest.mark.skipif(not os.getenv("TAVILY_API_KEY"), reason="TAVILY_API_KEY not set")
def test_live_tavily():
    from backend.main import services
    assert services.tavily.search("Taj Mahal Agra opening hours", 2).results


@pytest.mark.skipif(not os.getenv("GROQ_API_KEY"), reason="GROQ_API_KEY not set")
def test_live_groq():
    from backend.main import services
    assert services.llm.complete("Reply with the single word OK.", "ping", max_tokens=5).strip()
