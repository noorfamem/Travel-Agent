-- TravelAI Pro: PostgreSQL schema (canonical). Create tables with:  python -m backend.database.init
-- Relationship chain: travel_sessions 1-N trips; trips 1-1 selected_flights / selected_hotels / budgets / final_recommendations; trips 1-N itinerary_days / trip_places / search_runs / agent_runs
CREATE TABLE IF NOT EXISTS travel_sessions (
    id               TEXT PRIMARY KEY,
    client_label     TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trips (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES travel_sessions(id) ON DELETE CASCADE,
    origin_text      TEXT NOT NULL,
    destination_text TEXT NOT NULL,
    origin_label     TEXT NOT NULL DEFAULT '',
    destination_label TEXT NOT NULL DEFAULT '',
    origin_iata      TEXT NOT NULL DEFAULT '',
    destination_iata TEXT NOT NULL DEFAULT '',
    trip_type        TEXT NOT NULL DEFAULT '',
    departure_date   DATE NOT NULL,
    return_date      DATE NOT NULL,
    adults           INTEGER NOT NULL,
    children         INTEGER NOT NULL DEFAULT 0,
    rooms            INTEGER NOT NULL DEFAULT 1,
    budget_total     DOUBLE PRECISION,
    currency         TEXT NOT NULL DEFAULT 'INR',
    cabin            TEXT NOT NULL DEFAULT 'economy',
    travel_style     TEXT NOT NULL DEFAULT 'comfortable',
    status           TEXT NOT NULL DEFAULT 'created',
    request          JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_trips_session ON trips(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS travel_preferences (
    trip_id             TEXT PRIMARY KEY REFERENCES trips(id) ON DELETE CASCADE,
    interests           JSONB NOT NULL,
    custom_interest     TEXT NOT NULL DEFAULT '',
    food_preference     TEXT NOT NULL DEFAULT '',
    activity_preference TEXT NOT NULL DEFAULT '',
    hotel_stars         JSONB NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS search_runs (            -- data-origin proof for every external flight/hotel search
    id               BIGSERIAL PRIMARY KEY,
    trip_id          TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL,                 -- flights | hotels
    provider         TEXT NOT NULL,
    status           TEXT NOT NULL,
    message          TEXT NOT NULL DEFAULT '',
    meta             JSONB NOT NULL,                -- endpoint, sanitized params, provider search id, counts, latency (never API keys)
    options          JSONB NOT NULL,                -- normalized ranked results shown to the user
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_search_runs_trip ON search_runs(trip_id, kind);

CREATE TABLE IF NOT EXISTS selected_flights (
    trip_id          TEXT PRIMARY KEY REFERENCES trips(id) ON DELETE CASCADE,
    flight_id        TEXT NOT NULL,
    origin           TEXT NOT NULL,
    destination      TEXT NOT NULL,
    travel_date      DATE NOT NULL,
    airline          TEXT NOT NULL DEFAULT '',
    flight_number    TEXT NOT NULL DEFAULT '',
    departure_at     TEXT NOT NULL,                 -- ISO-8601 with offset, local to the origin airport
    arrival_at       TEXT NOT NULL,                 -- ISO-8601 with offset, local to the destination airport
    duration_min     INTEGER,
    stops            INTEGER NOT NULL DEFAULT 0,
    price_per_person DOUBLE PRECISION,
    total_price      DOUBLE PRECISION,
    currency         TEXT NOT NULL DEFAULT 'INR',
    booking_url      TEXT,
    source           TEXT NOT NULL DEFAULT '',
    flight           JSONB NOT NULL,                -- full normalized outbound option
    return_flight    JSONB,                         -- matching return option (when available)
    selected_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS selected_hotels (
    trip_id          TEXT PRIMARY KEY REFERENCES trips(id) ON DELETE CASCADE,
    skipped          BOOLEAN NOT NULL DEFAULT FALSE,
    hotel_id         TEXT,
    name             TEXT,
    destination      TEXT,
    check_in         DATE,
    check_out        DATE,
    nights           INTEGER,
    price_per_night  DOUBLE PRECISION,
    total_price      DOUBLE PRECISION,
    currency         TEXT,
    rating           DOUBLE PRECISION,
    booking_url      TEXT,
    source           TEXT,
    hotel            JSONB,
    selected_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trip_places (
    trip_id          TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    place_id         TEXT NOT NULL,
    name             TEXT NOT NULL,
    category         TEXT NOT NULL DEFAULT '',
    place            JSONB NOT NULL,
    PRIMARY KEY (trip_id, place_id)
);

CREATE TABLE IF NOT EXISTS itinerary_days (
    trip_id             TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    day_number          INTEGER NOT NULL,
    day_date            DATE NOT NULL,
    title               TEXT NOT NULL DEFAULT '',
    food_estimate       DOUBLE PRECISION NOT NULL DEFAULT 0,
    transport_estimate  DOUBLE PRECISION NOT NULL DEFAULT 0,
    activities_estimate DOUBLE PRECISION NOT NULL DEFAULT 0,
    daily_total         DOUBLE PRECISION NOT NULL DEFAULT 0,
    day                 JSONB NOT NULL,             -- all slots with times, costs, links
    PRIMARY KEY (trip_id, day_number)
);

CREATE TABLE IF NOT EXISTS budgets (
    trip_id          TEXT PRIMARY KEY REFERENCES trips(id) ON DELETE CASCADE,
    currency         TEXT NOT NULL,
    total            DOUBLE PRECISION NOT NULL,
    budget           DOUBLE PRECISION,
    remaining        DOUBLE PRECISION,
    over_budget      BOOLEAN NOT NULL DEFAULT FALSE,
    report           JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS final_recommendations (
    trip_id          TEXT PRIMARY KEY REFERENCES trips(id) ON DELETE CASCADE,
    summary          TEXT NOT NULL DEFAULT '',
    plan             JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id               BIGSERIAL PRIMARY KEY,
    trip_id          TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    agent            TEXT NOT NULL,
    status           TEXT NOT NULL,
    duration_ms      INTEGER NOT NULL DEFAULT 0,
    input_summary    TEXT NOT NULL DEFAULT '',
    output_summary   TEXT NOT NULL DEFAULT '',
    errors           JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_trip ON agent_runs(trip_id, id);
