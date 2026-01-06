# TripScore: Personalized Destination Evaluation & Recommendation (Taiwan)

## 1. Project Overview

TripScore is a data-driven travel decision engine that recommends destinations based on a user's preferences and a target time window.

The system integrates multiple data sources to compute a composite score for each candidate destination, including:
- Transportation accessibility and real-time conditions (TDX)
- Weather conditions and forecast
- Area/context factors (POI density, land use proxies, crowd/traffic proxies)
- Optional predictive signals (expected congestion, expected rainfall impact, ETA reliability)

The output is a ranked list of recommended destinations with transparent score breakdowns and explanations.

---

## 2. Key User Scenarios

1. "I have 4 hours this afternoon. Where should I go within Taipei?"
2. "I want outdoor nature spots this weekend, but avoid rain and heavy traffic."
3. "I prefer family-friendly attractions, easy transit, and low walking distance."
4. "I want night markets tonight; prioritize places with good public transit and nearby parking."

---

## 3. Inputs & Preferences

### 3.1 User Inputs
- Start location (lat/lon or administrative area)
- Time window (start time, end time)
- Travel mode preferences (public transit / bike / walk / car)
- Budget sensitivity (optional)
- Tolerance thresholds (walking distance, transfers, max travel time)

### 3.2 Preference Weights (Personalization)
Users can set weights (0~1) or choose presets:
- Accessibility weight
- Weather comfort weight
- Crowd/traffic avoidance weight
- Activity type preference (outdoor / indoor / shopping / culture / food)
- Family-friendly, mobility-friendly, etc.

---

## 4. Data Sources

### 4.1 Transportation Data (TDX)
Potential datasets to use:
- Public transit route/stop metadata (bus, metro, rail where applicable)
- Real-time transit status (delays, disruptions if available)
- Bike-sharing station status (availability, docks)
- Parking availability (where supported)
- Road traffic proxies / incidents (where supported)

### 4.2 Weather Data
- Current observations
- Short-term forecast (hourly / daily)
- Rain probability, precipitation intensity, temperature, feels-like
- Weather warnings (optional)

### 4.3 Area & Context Factors
- Administrative boundaries (GIS)
- POI categories and density (food, parks, museums, shopping, etc.)
- Land use proxies (residential/commercial/mixed) where available
- Optional: event calendars (future extension)

---

## 5. Scoring Model

### 5.1 Candidate Generation
Given user constraints (time window, max travel time):
1) Select candidate destinations from a curated destination catalog (seed list)
2) Filter by reachable within time window
3) Expand by category if needed

### 5.2 Feature Engineering (Per destination, per time window)
- Travel time estimate (min/median), number of transfers
- Reliability score (based on disruptions / typical variability)
- Bike availability confidence (if last-mile uses bike-sharing)
- Weather comfort score (temp, rain, wind) aligned to user preference
- Crowd/traffic risk score (proxy signals if available)
- Activity match score (destination tags vs user preferences)

### 5.3 Final Score
A weighted composite score:
Score = w1*Accessibility + w2*Weather + w3*ActivityMatch + w4*Comfort + w5*Reliability - w6*Risk

Each component should be normalized to 0~100 and accompanied by explanations.

---

## 6. Predictions (Optional, Phase 2+)
- Predict expected crowd/traffic risk based on historical patterns (weekday/time/season)
- Predict bike availability risk by time-of-day
- Predict "rain impact" on outdoor suitability

Models can start simple:
- Baseline: historical average by hour/day
- ML: gradient boosting / regression
- Later: time-series models

---

## 7. System Architecture

```

tripscore/
├─ data/
│  ├─ raw/
│  ├─ processed/
│  └─ catalogs/                 # curated destination list + tags
├─ src/
│  ├─ ingestion/
│  │  ├─ tdx_client.py
│  │  ├─ weather_client.py
│  │  └─ poi_client.py
│  ├─ domain/
│  │  ├─ models.py              # Pydantic data models / schemas
│  │  └─ enums.py
│  ├─ features/
│  │  ├─ accessibility.py
│  │  ├─ weather.py
│  │  ├─ context.py
│  │  └─ risk.py
│  ├─ scoring/
│  │  ├─ normalize.py
│  │  ├─ composite.py
│  │  └─ explain.py
│  ├─ recommender/
│  │  ├─ candidate_generation.py
│  │  ├─ ranker.py
│  │  └─ presets.py
│  ├─ api/                      # optional FastAPI service
│  └─ utils/
├─ notebooks/
├─ configs/
│  ├─ config.yaml
│  └─ secrets.env.example
├─ outputs/
│  ├─ reports/
│  └─ figures/
├─ PROJECT_DESCRIPTION.md
└─ README.md

```

---

## 8. Deliverables

- A reproducible pipeline that produces ranked destination recommendations
- Transparent score breakdown for each destination
- Config-driven preference presets
- A minimal demo (CLI or notebook) and optional API service

---

## 9. Design Principles

- Deterministic, explainable scoring first (Phase 1)
- Prediction modules are optional (Phase 2+)
- Strong separation: ingestion → features → scoring → ranking → explain
- No hard-coded constants; use configs
- Production mindset: logging, tests, input validation

---

## 10. Roadmap

### Phase 1 (MVP)
- Destination catalog + tags
- TDX + Weather ingestion
- Feature engineering (accessibility, weather, match)
- Composite scoring + explanations
- CLI demo / notebook report

### Phase 2 (Quality)
- Add context features (POI density, land use proxies)
- Add basic predictive signals (hour-of-day baselines)
- Evaluation: offline sanity checks, case studies

### Phase 3 (Product)
- FastAPI service + simple UI
- User profiles, saved preferences, feedback loop
- Monitoring and caching
```
