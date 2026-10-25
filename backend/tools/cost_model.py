"""Reference ground-cost model (ESTIMATES, never presented as live prices). Data: data/reference/cost_tiers.json."""
from __future__ import annotations

import json
from pathlib import Path

from ..models.travel_models import TravelStyle

_PATH = Path(__file__).resolve().parents[1] / "data" / "reference" / "cost_tiers.json"
INDIA_METROS = {"mumbai", "new delhi", "delhi", "bengaluru", "chennai", "kolkata", "hyderabad", "pune", "ahmedabad"}
STYLE_KEY = {TravelStyle.BUDGET: "budget", TravelStyle.COMFORTABLE: "standard", TravelStyle.LUXURY: "luxury"}


class CostModel:
    def __init__(self, path: Path | str | None = None):
        self.data = json.loads(Path(path or _PATH).read_text(encoding="utf-8"))
        self.reviewed = self.data.get("reviewed", "")

    def rates(self, tier: int, style: TravelStyle, country_code: str = "", city: str = "") -> dict:
        t = self.data["tiers"].get(str(tier)) or self.data["tiers"]["3"]
        r = dict(t[STYLE_KEY[style]])
        if country_code == "IN" and city.lower() in INDIA_METROS:
            m = self.data.get("india_metro_multiplier", 1.0)
            r = {k: round(v * m) for k, v in r.items()}
        return r
