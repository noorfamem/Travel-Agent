"""World location resolver: city / state / country / region / airport -> structured Location with airports.
Data lives in backend/data/*.csv (maintainable). No place is hard-coded in code."""
from __future__ import annotations

import bisect
import csv
import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..models.travel_models import Airport, Location
from ..tools.geo_tools import haversine_km
from ..utils.logging_config import get_logger

log = get_logger("location")
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
GENERIC_WORDS = {"airport", "international", "intl", "domestic", "aerodrome", "apt"}
COMMON_WORDS = {"nice", "turkey", "jordan", "chad", "reading", "mark", "may", "bath", "split", "mobile", "cork", "lima", "male", "victoria", "georgia"}
NOT_AIRPORT_CODES = {"THE", "AND", "FOR", "TWO", "USD", "INR", "EUR", "GBP", "BUT", "ALL", "NEW", "DAY", "AIR", "TRIP", "ANY", "OUR", "YOU", "CAN", "WAS", "ARE", "NOT", "HER", "HIS"}
KIND_RANK = {"airport": 0, "state": 1, "country": 1, "region": 2, "city": 3}


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    t = t.replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


@dataclass
class _Entry:
    kind: str
    name: str
    cc: str = ""
    state: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    pop: float = 0.0
    ref: Any = None


@dataclass
class Mention:
    start: int
    end: int
    key: str
    surface: str


class LocationService:
    def __init__(self, data_dir: Path | str | None = None):
        d = Path(data_dir) if data_dir else DATA_DIR
        self.countries: dict[str, dict] = {}
        self.airports: dict[str, Airport] = {}
        self._order: dict[str, int] = {}
        self.entries: dict[str, list[_Entry]] = {}
        self._by_cc: dict[str, list[Airport]] = {}
        self._by_city: dict[tuple[str, str], list[Airport]] = {}
        self._by_state: dict[tuple[str, str], list[Airport]] = {}
        self._load(d)
        self._keys = sorted(self.entries)
        log.info("location data loaded: %d airports, %d countries, %d keys", len(self.airports), len(self.countries), len(self.entries))

    @staticmethod
    def _rows(path: Path):
        with open(path, newline="", encoding="utf-8") as f:
            yield from csv.DictReader(f)

    def _add(self, key: str, entry: _Entry) -> None:
        if not key:
            return
        bucket = self.entries.setdefault(key, [])
        for e in bucket:
            if e.kind == entry.kind and e.name == entry.name and e.cc == entry.cc and e.state == entry.state:
                if entry.pop and not e.pop:
                    e.pop = entry.pop
                return
        bucket.append(entry)

    def _load(self, d: Path) -> None:
        for r in self._rows(d / "countries.csv"):
            cc = r["iso2"]
            self.countries[cc] = {"name": r["name"], "continent": r["continent"], "tier": int(r["tier"] or 3), "currency": r["currency"]}
            e = _Entry("country", r["name"], cc, ref=cc)
            self._add(norm(r["name"]), e)
            for a in filter(None, r["aliases"].split("|")):
                self._add(norm(a), e)
        for i, r in enumerate(self._rows(d / "airports.csv")):
            ap = Airport(iata=r["iata"], name=r["name"], city=r["city"], state=r["state"], country_code=r["country"], lat=float(r["lat"]), lon=float(r["lon"]), tz=r["tz"], pri=int(r["pri"]))
            self.airports[ap.iata] = ap
            self._order[ap.iata] = i
        for ap in self.airports.values():
            self._by_cc.setdefault(ap.country_code, []).append(ap)
            self._by_city.setdefault((norm(ap.city), ap.country_code), []).append(ap)
            if ap.state:
                self._by_state.setdefault((norm(ap.state), ap.country_code), []).append(ap)
            e = _Entry("airport", ap.city or ap.name, ap.country_code, ap.state, ap.lat, ap.lon, ref=ap)
            self._add(norm(ap.name), e)
            short = norm(re.sub(r"(?i)\bairport\b", "", ap.name))
            if short and short != norm(ap.name):
                self._add(short, e)
        key = lambda a: (a.pri, self._order[a.iata])  # noqa: E731
        for lst in list(self._by_cc.values()) + list(self._by_city.values()) + list(self._by_state.values()):
            lst.sort(key=key)
        for r in self._rows(d / "admin1.csv"):
            e = _Entry("state", r["name"], r["country"], r["name"], float(r["lat"]), float(r["lon"]), ref=r)
            self._add(norm(r["name"]), e)
            for a in filter(None, r["aliases"].split("|")):
                self._add(norm(a), e)
        for r in self._rows(d / "regions.csv"):
            e = _Entry("region", r["name"], ref=r["suggest_countries"].split("|"))
            self._add(norm(r["name"]), e)
            for a in filter(None, r["aliases"].split("|")):
                self._add(norm(a), e)
        seen: set[tuple[str, str]] = set()
        for r in self._rows(d / "cities.csv"):
            e = _Entry("city", r["name"], r["country"], r["state"], float(r["lat"]), float(r["lon"]), float(r["pop_k"] or 0))
            self._add(norm(r["name"]), e)
            for a in filter(None, r["aliases"].split("|")):
                self._add(norm(a), e)
            seen.add((norm(r["name"]), r["country"]))
        for (cn, cc), aps in self._by_city.items():
            if cn and (cn, cc) not in seen:
                self._add(cn, _Entry("city", aps[0].city, cc, aps[0].state, sum(a.lat for a in aps) / len(aps), sum(a.lon for a in aps) / len(aps)))

    # ------------------------------------------------------------------ helpers
    def country_info(self, cc: str) -> dict:
        return self.countries.get(cc, {"name": cc, "continent": "", "tier": 3, "currency": ""})

    def airport(self, iata: str) -> Airport | None:
        return self.airports.get((iata or "").upper())

    def _nearest(self, lat: float, lon: float, cc: str | None, limit: int = 2, max_km: float = 700) -> list[Airport]:
        scored = []
        for ap in self.airports.values():
            dist = haversine_km(lat, lon, ap.lat, ap.lon)
            if dist <= max_km:
                scored.append((dist + 25 * (ap.pri - 1) + (0 if (cc is None or ap.country_code == cc) else 150), dist, ap))
        scored.sort(key=lambda t: t[0])
        return [ap.model_copy(update={"distance_km": round(dist, 1)}) for _, dist, ap in scored[:limit]]

    def _airports_for_entry(self, e: _Entry) -> tuple[list[Airport], list[str]]:
        notes: list[str] = []
        if e.kind == "country":
            aps = self._by_cc.get(e.cc, [])[:3]
            notes.append(f"{e.name} is a whole country - using its main gateway airports ({', '.join(a.iata for a in aps)}). Tell me your exact city for a precise search." if aps else f"No airport data for {e.name}.")
            return [a.model_copy() for a in aps], notes
        if e.kind == "state":
            aps = self._by_state.get((norm(e.name), e.cc), [])[:3]
            if not aps and e.lat is not None:
                aps = self._nearest(e.lat, e.lon, e.cc, 2)
                notes.append(f"No airport inside {e.name}; nearest airports used.")
            else:
                notes.append(f"{e.name} is a state/UT - using its main airports ({', '.join(a.iata for a in aps)}).")
            return [a.model_copy() for a in aps], notes
        direct = self._by_city.get((norm(e.name), e.cc), [])
        if direct:
            return [a.model_copy() for a in direct[:4]], notes
        if e.lat is not None:
            aps = self._nearest(e.lat, e.lon, e.cc, 2)
            if aps:
                notes.append(f"{e.name} has no commercial airport in our data; nearest: " + ", ".join(f"{a.name} ({a.iata}, ~{a.distance_km:.0f} km)" for a in aps) + ".")
            return aps, notes
        return [], notes

    def _build(self, raw: str, e: _Entry, extra: list[str] | None = None) -> Location:
        cc = e.cc
        loc = Location(query=raw, kind=e.kind, name=e.name, state=e.state if e.kind != "state" else e.name, country=self.country_info(cc)["name"] if cc else "",
                       country_code=cc, lat=e.lat, lon=e.lon, notes=list(extra or []))
        if e.kind == "country" and self._by_cc.get(cc):
            loc.lat, loc.lon = self._by_cc[cc][0].lat, self._by_cc[cc][0].lon
        loc.airports, notes = self._airports_for_entry(e)
        loc.notes += notes
        if loc.lat is None and loc.airports:
            loc.lat, loc.lon = loc.airports[0].lat, loc.airports[0].lon
        if e.kind == "city":
            loc.city = e.name
        return loc

    def _airport_location(self, raw: str, ap: Airport) -> Location:
        return Location(query=raw, kind="airport", name=ap.city or ap.name, city=ap.city, state=ap.state, country=self.country_info(ap.country_code)["name"],
                        country_code=ap.country_code, lat=ap.lat, lon=ap.lon, airports=[ap.model_copy()], notes=[f"Using {ap.name} ({ap.iata})."])

    def label_for(self, e: _Entry) -> str:
        cn = self.country_info(e.cc)["name"] if e.cc else ""
        if e.kind == "country":
            return f"{e.name} (country)"
        if e.kind == "state":
            return f"{e.name}, {cn}"
        if e.kind == "airport":
            return f"{e.name} ({e.ref.iata})"
        return ", ".join(x for x in (e.name, e.state if e.state and e.cc in {"IN", "US"} else "", cn) if x)

    @staticmethod
    def _extract_iata(raw: str) -> str | None:
        s = raw.strip()
        m = re.fullmatch(r"[A-Z]{3}", s)
        if m:
            return m.group(0)
        m = re.search(r"\(([A-Z]{3})\)", s)
        return m.group(1) if m else None

    # ------------------------------------------------------------------ resolve
    def resolve(self, text: str) -> Location:
        """Never raises. Returns kind 'unknown' / 'ambiguous' / 'region' with suggestions when it cannot decide."""
        raw = (text or "").strip()
        if not raw:
            return Location(query=raw, notes=["Empty location."])
        code = self._extract_iata(raw)
        if code and code in self.airports and code not in NOT_AIRPORT_CODES:
            return self._airport_location(raw, self.airports[code])
        parts = [p for p in (norm(x) for x in re.split(r"[,/|]", raw)) if p]
        if not parts:
            return Location(query=raw, notes=["Could not read this location."])
        primary = parts[0]
        stripped = " ".join(w for w in primary.split() if w not in GENERIC_WORDS)
        wants_airport = stripped != primary
        qual_cc = next((self.entries[p][0].cc for p in parts[1:] if p in self.entries and self.entries[p][0].kind == "country"), None)
        qual_state = next((norm(self.entries[p][0].name) for p in parts[1:] if p in self.entries and self.entries[p][0].kind == "state"), None)
        cands = list(self.entries.get(primary, []))
        if wants_airport and not any(c.kind == "airport" for c in cands):
            cands = list(self.entries.get(stripped, [])) or cands
        note: list[str] = []
        if not cands:
            fuzzy = difflib.get_close_matches(primary, self._keys, n=1, cutoff=0.84)
            if fuzzy:
                cands = list(self.entries[fuzzy[0]])
                note.append(f"Interpreted '{raw}' as '{cands[0].name}'.")
        if not cands:
            sugg: list[str] = []
            for k in difflib.get_close_matches(primary, self._keys, n=6, cutoff=0.6):
                lab = self.label_for(sorted(self.entries[k], key=lambda x: KIND_RANK[x.kind])[0])
                if lab not in sugg:
                    sugg.append(lab)
            return Location(query=raw, kind="unknown", name=raw.title(), candidates=sugg[:5], notes=[f"I couldn't find '{raw}'. Check the spelling or try a nearby major city."])
        if qual_cc:
            cands = [c for c in cands if c.cc == qual_cc] or cands
        if qual_state:
            cands = [c for c in cands if norm(c.state) == qual_state] or cands
        airports = [c for c in cands if c.kind == "airport"]
        non_airport = [c for c in cands if c.kind != "airport"]
        if airports and (wants_airport or not non_airport):
            return self._airport_location(raw, airports[0].ref)
        regions = [c for c in non_airport if c.kind == "region"]
        if regions and len(regions) == len(non_airport):
            r = regions[0]
            return Location(query=raw, kind="region", name=r.name, candidates=[self.country_info(cc)["name"] for cc in r.ref],
                            notes=[f"'{r.name}' is a region, not a single destination. Which country or city would you like?"])
        top = [c for c in non_airport if c.kind in ("state", "country")]
        if top:
            return self._build(raw, sorted(top, key=lambda c: KIND_RANK[c.kind])[0], note)
        cities = sorted(non_airport, key=lambda c: -c.pop)
        if len({c.cc for c in cities}) > 1:
            best = cities[0]
            second = next((c for c in cities[1:] if c.cc != best.cc), None)
            if second and best.pop and second.pop / best.pop >= 0.3:
                return Location(query=raw, kind="ambiguous", name=best.name, candidates=[self.label_for(c) for c in cities[:4]], notes=[f"'{raw}' matches more than one place. Please choose one."])
            if second:
                note.append(f"Interpreted '{raw}' as {self.label_for(best)}.")
        return self._build(raw, cities[0], note)

    # ------------------------------------------------------------------ suggestions & text mining
    def suggest(self, query: str, limit: int = 8) -> list[dict]:
        q = norm(query)
        if len(q) < 2:
            return []
        keys: list[str] = []
        i = bisect.bisect_left(self._keys, q)
        while i < len(self._keys) and self._keys[i].startswith(q) and len(keys) < 80:
            keys.append(self._keys[i])
            i += 1
        if len(keys) < limit:
            keys += [k for k in self._keys if (" " + q) in k and k not in keys][:40]
        out, seen = [], set()
        for k in keys:
            for e in self.entries[k]:
                if e.kind == "region":
                    continue
                label = self.label_for(e)
                if label in seen:
                    continue
                seen.add(label)
                value = e.ref.iata if e.kind == "airport" else (f"{e.name}, {self.country_info(e.cc)['name']}" if e.kind == "city" else e.name)
                out.append((KIND_RANK[e.kind], 0 if k.startswith(q) else 1, -e.pop, len(k), {"label": label, "value": value, "kind": e.kind}))
        out.sort(key=lambda t: t[:4])
        return [t[4] for t in out[:limit]]

    def find_mentions(self, text: str) -> list[Mention]:
        toks = [(m.group(0), m.start()) for m in re.finditer(r"[A-Za-z\u00c0-\u024f0-9'.]+", text or "")]
        norm_toks = [norm(t) for t, _ in toks]
        used = [False] * len(toks)
        found: list[Mention] = []
        for n in (4, 3, 2, 1):
            for i in range(len(toks) - n + 1):
                if any(used[i:i + n]) or not all(norm_toks[i:i + n]):
                    continue
                key = " ".join(norm_toks[i:i + n])
                if key not in self.entries or (n == 1 and len(key) < 3 and key != "kl"):
                    continue
                if key in COMMON_WORDS and not toks[i][0][:1].isupper():
                    continue
                if n == 1 and all(e.kind == "airport" for e in self.entries[key]):
                    continue
                for j in range(i, i + n):
                    used[j] = True
                found.append(Mention(i, i + n, key, " ".join(t for t, _ in toks[i:i + n]).strip(".,'")))
        for i, (t, _) in enumerate(toks):
            if not used[i] and re.fullmatch(r"[A-Z]{3}", t) and t in self.airports and t not in NOT_AIRPORT_CODES and (norm_toks[i - 1] if i else "") in {"from", "to", "at", "via", "into"}:
                used[i] = True
                found.append(Mention(i, i + 1, t, t))
        return sorted(found, key=lambda m: m.start)
