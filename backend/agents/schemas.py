"""Typed request models for the three agents. All user input is validated here."""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from ..models.travel_models import Cabin, TravelStyle

_CUR = re.compile(r"^[A-Za-z]{3}$")


def _today() -> dt.date:
    return dt.date.today()


class _CurrencyMixin(BaseModel):
    currency: str = "INR"

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        if not _CUR.match(v or ""):
            raise ValueError("currency must be a 3-letter code such as INR")
        return v.upper()


class FlightAgentRequest(_CurrencyMixin):
    origin: str = Field(min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)
    departure_date: dt.date
    return_date: Optional[dt.date] = None
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    cabin: Cabin = Cabin.ECONOMY
    budget: Optional[float] = Field(default=None, gt=0, le=1e8)
    overall_budget: Optional[float] = Field(default=None, gt=0, le=1e9)
    budget_scope: str = "per_passenger"  # per_passenger | total
    nonstop_only: bool = False
    preferred_airlines: list[str] = Field(default_factory=list, max_length=5)
    style: TravelStyle = TravelStyle.COMFORTABLE

    @field_validator("budget_scope")
    @classmethod
    def _scope(cls, v: str) -> str:
        if v not in ("per_passenger", "total"):
            raise ValueError("budget_scope must be 'per_passenger' or 'total'")
        return v

    @model_validator(mode="after")
    def _dates(self):
        if self.departure_date < _today():
            raise ValueError("departure_date cannot be in the past")
        if self.return_date and self.return_date < self.departure_date:
            raise ValueError("return_date must be on or after departure_date")
        if self.adults + self.children > 9:
            raise ValueError("at most 9 passengers per search")
        return self

    @property
    def passengers(self) -> int:
        return self.adults + self.children


class HotelAgentRequest(_CurrencyMixin):
    destination: str = Field(min_length=2, max_length=100)
    check_in: Optional[dt.date] = None  # may be derived from a selected flight's arrival date
    check_out: dt.date
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    rooms: int = Field(default=1, ge=1, le=6)
    budget: Optional[float] = Field(default=None, gt=0, le=1e8)
    overall_budget: Optional[float] = Field(default=None, gt=0, le=1e9)
    budget_scope: str = "per_night"  # per_night | total_stay
    stars: list[int] = Field(default_factory=list)
    amenities: list[str] = Field(default_factory=list, max_length=8)
    free_cancellation: bool = False
    style: TravelStyle = TravelStyle.COMFORTABLE
    flight_id: Optional[str] = None

    @field_validator("budget_scope")
    @classmethod
    def _scope(cls, v: str) -> str:
        if v not in ("per_night", "total_stay"):
            raise ValueError("budget_scope must be 'per_night' or 'total_stay'")
        return v

    @field_validator("stars")
    @classmethod
    def _stars(cls, v: list[int]) -> list[int]:
        if any(s < 1 or s > 5 for s in v):
            raise ValueError("stars must be between 1 and 5")
        return v

    @model_validator(mode="after")
    def _dates(self):
        if self.check_in is None and not self.flight_id:
            raise ValueError("check_in is required unless a flight_id is supplied")
        if self.check_in:
            if self.check_in < _today():
                raise ValueError("check_in cannot be in the past")
            if self.check_out <= self.check_in:
                raise ValueError("check_out must be after check_in")
            if (self.check_out - self.check_in).days > 30:
                raise ValueError("stays longer than 30 nights are not supported")
        if self.rooms > self.adults + self.children:
            raise ValueError("rooms cannot exceed the number of guests")
        return self


class ItineraryAgentRequest(_CurrencyMixin):
    destination: str = Field(min_length=2, max_length=100)
    plan_date: Optional[dt.date] = None
    start_time: str = "08:00"
    window_hours: int = Field(default=12, ge=4, le=16)
    interests: list[str] = Field(default_factory=list, max_length=12)
    pace: str = "balanced"  # relaxed | balanced | packed
    mobility_needs: str = Field(default="", max_length=200)
    dietary: str = Field(default="", max_length=100)
    adults: int = Field(default=1, ge=1, le=9)
    daily_activity_budget: Optional[float] = Field(default=None, gt=0, le=1e7)  # per person, activities+local transport+meals
    flight_id: Optional[str] = None
    hotel_id: Optional[str] = None

    @field_validator("start_time")
    @classmethod
    def _t(cls, v: str) -> str:
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("start_time must be HH:MM (24-hour)")
        return v

    @field_validator("pace")
    @classmethod
    def _p(cls, v: str) -> str:
        if v not in ("relaxed", "balanced", "packed"):
            raise ValueError("pace must be relaxed, balanced or packed")
        return v


class OverallTripRequest(_CurrencyMixin):
    origin: str = Field(min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)
    departure_date: dt.date
    return_date: Optional[dt.date] = None
    check_in: Optional[dt.date] = None
    check_out: Optional[dt.date] = None
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    rooms: int = Field(default=1, ge=1, le=6)
    budget: Optional[float] = Field(default=None, gt=0, le=1e8)
    overall_budget: Optional[float] = Field(default=None, gt=0, le=1e9)
    interests: list[str] = Field(default_factory=list, max_length=12)
    start_time: str = "08:00"
    cabin: Cabin = Cabin.ECONOMY

    @field_validator("start_time")
    @classmethod
    def _t(cls, v: str) -> str:
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("start_time must be HH:MM (24-hour)")
        return v

    @model_validator(mode="after")
    def _dates(self):
        if self.departure_date < _today():
            raise ValueError("departure_date cannot be in the past")
        if self.return_date and self.return_date < self.departure_date:
            raise ValueError("return_date must be on or after departure_date")
        cout = self.check_out or self.return_date or (self.departure_date + dt.timedelta(days=3))
        cin = self.check_in or self.departure_date
        if cout <= cin:
            raise ValueError("check_out must be after check_in")
        object.__setattr__(self, "check_in", cin)
        object.__setattr__(self, "check_out", cout)
        return self


class TripSummaryRequest(_CurrencyMixin):
    flight_id: Optional[str] = None
    hotel_id: Optional[str] = None
    itinerary_id: Optional[str] = None
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    rooms: int = Field(default=1, ge=1, le=6)
    overall_budget: Optional[float] = Field(default=None, gt=0, le=1e9)
