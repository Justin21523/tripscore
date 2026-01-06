from datetime import datetime
from zoneinfo import ZoneInfo

from tripscore.config.settings import get_settings
from tripscore.domain.models import ComponentWeights, Destination, GeoPoint, TimeWindow, UserPreferences
from tripscore.ingestion.tdx_client import BikeStationStatus
from tripscore.ingestion.tdx_client import MetroStation
from tripscore.ingestion.weather_client import WeatherSummary
from tripscore.recommender.recommend import recommend


class StubTdxClient:
    def get_bus_stops(self, *, city: str | None = None):
        return []

    def get_youbike_station_statuses(self, *, city: str | None = None):
        return []

    def get_metro_stations(self, *, operators: list[str] | None = None):
        return []

    def get_parking_lot_statuses(self, *, city: str | None = None):
        return []


class StubWeatherClient:
    def get_summary(self, *, lat: float, lon: float, start: datetime, end: datetime) -> WeatherSummary:
        if lat >= 25.08:
            return WeatherSummary(max_precipitation_probability=10, mean_temperature_c=26.0)
        return WeatherSummary(max_precipitation_probability=80, mean_temperature_c=26.0)


def test_mvp_recommendation_ranking_smoke():
    settings = get_settings()
    tz = ZoneInfo(settings.app.timezone)
    start = datetime(2026, 1, 5, 10, 0, tzinfo=tz)
    end = datetime(2026, 1, 5, 18, 0, tzinfo=tz)

    destinations = [
        Destination(
            id="a",
            name="A (good weather, indoor)",
            location=GeoPoint(lat=25.10, lon=121.50),
            tags=["indoor", "culture"],
            city="Taipei",
        ),
        Destination(
            id="b",
            name="B (bad weather, indoor)",
            location=GeoPoint(lat=25.01, lon=121.50),
            tags=["indoor", "culture"],
            city="Taipei",
        ),
        Destination(
            id="c",
            name="C (good weather, outdoor)",
            location=GeoPoint(lat=25.10, lon=121.50),
            tags=["outdoor"],
            city="Taipei",
        ),
    ]

    prefs = UserPreferences(
        origin=GeoPoint(lat=25.0478, lon=121.5170),
        time_window=TimeWindow(start=start, end=end),
        max_results=3,
        component_weights=ComponentWeights(accessibility=0.0, weather=0.7, preference=0.3, context=0.0),
        weather_rain_importance=1.0,
        tag_weights={"indoor": 1.0, "outdoor": 0.0, "culture": 0.2},
    )

    result = recommend(
        prefs,
        settings=settings,
        destinations=destinations,
        tdx_client=StubTdxClient(),
        weather_client=StubWeatherClient(),
    )

    ids = [r.destination.id for r in result.results]
    assert ids == ["a", "c", "b"]
    assert all(r.breakdown.components for r in result.results)


def test_accessibility_uses_origin_distance_when_transit_missing():
    settings = get_settings()
    tz = ZoneInfo(settings.app.timezone)
    start = datetime(2026, 1, 5, 10, 0, tzinfo=tz)
    end = datetime(2026, 1, 5, 18, 0, tzinfo=tz)

    destinations = [
        Destination(
            id="near",
            name="Near",
            location=GeoPoint(lat=25.0478, lon=121.5170),
            tags=["indoor"],
            city="Taipei",
        ),
        Destination(
            id="far",
            name="Far",
            location=GeoPoint(lat=25.2000, lon=121.7000),
            tags=["indoor"],
            city="Taipei",
        ),
    ]

    prefs = UserPreferences(
        origin=GeoPoint(lat=25.0478, lon=121.5170),
        time_window=TimeWindow(start=start, end=end),
        max_results=2,
        component_weights=ComponentWeights(accessibility=1.0, weather=0.0, preference=0.0, context=0.0),
        weather_rain_importance=1.0,
        tag_weights={"indoor": 1.0},
    )

    result = recommend(
        prefs,
        settings=settings,
        destinations=destinations,
        tdx_client=StubTdxClient(),
        weather_client=StubWeatherClient(),
    )

    ids = [r.destination.id for r in result.results]
    assert ids == ["near", "far"]

    near = result.results[0]
    accessibility = next(c for c in near.breakdown.components if c.name == "accessibility")
    assert any("from origin" in r for r in accessibility.reasons)


def test_accessibility_prefers_more_bikes_when_enabled():
    settings = get_settings()
    tz = ZoneInfo(settings.app.timezone)
    start = datetime(2026, 1, 5, 10, 0, tzinfo=tz)
    end = datetime(2026, 1, 5, 18, 0, tzinfo=tz)

    # Override settings to isolate the bike signal (local transit only, bike only).
    acc = settings.ingestion.tdx.accessibility.model_copy(
        update={
            "blend_weights": {"local_transit": 1.0, "origin_proximity": 0.0},
            "local_transit_signal_weights": {"bus": 0.0, "bike": 1.0},
        }
    )
    tdx = settings.ingestion.tdx.model_copy(update={"accessibility": acc})
    ingestion = settings.ingestion.model_copy(update={"tdx": tdx})
    settings = settings.model_copy(update={"ingestion": ingestion})

    destinations = [
        Destination(
            id="bikes",
            name="Bikes nearby",
            location=GeoPoint(lat=25.0478, lon=121.5170),
            tags=["indoor"],
            city="Taipei",
        ),
        Destination(
            id="no_bikes",
            name="No bikes nearby",
            location=GeoPoint(lat=25.0478, lon=121.5300),
            tags=["indoor"],
            city="Taipei",
        ),
    ]

    prefs = UserPreferences(
        origin=GeoPoint(lat=25.0478, lon=121.5170),
        time_window=TimeWindow(start=start, end=end),
        max_results=2,
        component_weights=ComponentWeights(accessibility=1.0, weather=0.0, preference=0.0, context=0.0),
        tag_weights={"indoor": 1.0},
    )

    class BikeOnlyTdxClient(StubTdxClient):
        def get_youbike_station_statuses(self, *, city: str | None = None):
            return [
                BikeStationStatus(
                    station_uid="s1",
                    name="Station 1",
                    lat=25.0479,
                    lon=121.5171,
                    available_rent_bikes=20,
                    available_return_bikes=10,
                )
            ]

    result = recommend(
        prefs,
        settings=settings,
        destinations=destinations,
        tdx_client=BikeOnlyTdxClient(),
        weather_client=StubWeatherClient(),
    )

    ids = [r.destination.id for r in result.results]
    assert ids == ["bikes", "no_bikes"]


def test_accessibility_prefers_metro_when_enabled():
    settings = get_settings()
    tz = ZoneInfo(settings.app.timezone)
    start = datetime(2026, 1, 5, 10, 0, tzinfo=tz)
    end = datetime(2026, 1, 5, 18, 0, tzinfo=tz)

    # Override settings to isolate the metro signal (local transit only, metro only).
    acc = settings.ingestion.tdx.accessibility.model_copy(
        update={
            "blend_weights": {"local_transit": 1.0, "origin_proximity": 0.0},
            "local_transit_signal_weights": {"bus": 0.0, "metro": 1.0, "bike": 0.0},
        }
    )
    tdx = settings.ingestion.tdx.model_copy(update={"accessibility": acc})
    ingestion = settings.ingestion.model_copy(update={"tdx": tdx})
    settings = settings.model_copy(update={"ingestion": ingestion})

    destinations = [
        Destination(
            id="near_metro",
            name="Near metro",
            location=GeoPoint(lat=25.0478, lon=121.5170),
            tags=["indoor"],
            city="Taipei",
        ),
        Destination(
            id="far_metro",
            name="Far from metro",
            location=GeoPoint(lat=25.0478, lon=121.5300),
            tags=["indoor"],
            city="Taipei",
        ),
    ]

    prefs = UserPreferences(
        origin=GeoPoint(lat=25.0478, lon=121.5170),
        time_window=TimeWindow(start=start, end=end),
        max_results=2,
        component_weights=ComponentWeights(accessibility=1.0, weather=0.0, preference=0.0, context=0.0),
        tag_weights={"indoor": 1.0},
    )

    class MetroOnlyTdxClient(StubTdxClient):
        def get_metro_stations(self, *, operators: list[str] | None = None):
            return [
                MetroStation(
                    station_uid="m1",
                    name="Metro 1",
                    lat=25.0479,
                    lon=121.5171,
                    operator="TRTC",
                )
            ]

    result = recommend(
        prefs,
        settings=settings,
        destinations=destinations,
        tdx_client=MetroOnlyTdxClient(),
        weather_client=StubWeatherClient(),
    )

    ids = [r.destination.id for r in result.results]
    assert ids == ["near_metro", "far_metro"]


def test_context_crowd_risk_ranking_smoke():
    settings = get_settings()
    tz = ZoneInfo(settings.app.timezone)
    start = datetime(2026, 1, 5, 12, 0, tzinfo=tz)
    end = datetime(2026, 1, 5, 14, 0, tzinfo=tz)

    destinations = [
        Destination(
            id="xinyi",
            name="High crowd district",
            location=GeoPoint(lat=25.033968, lon=121.564468),
            tags=["indoor"],
            city="Taipei",
            district="Xinyi",
        ),
        Destination(
            id="wenshan",
            name="Lower crowd district",
            location=GeoPoint(lat=24.998472, lon=121.581121),
            tags=["indoor"],
            city="Taipei",
            district="Wenshan",
        ),
    ]

    prefs = UserPreferences(
        origin=GeoPoint(lat=25.0478, lon=121.5170),
        time_window=TimeWindow(start=start, end=end),
        max_results=2,
        component_weights=ComponentWeights(accessibility=0.0, weather=0.0, preference=0.0, context=1.0),
        avoid_crowds_importance=1.0,
        family_friendly_importance=0.0,
        tag_weights={"indoor": 1.0},
    )

    result = recommend(
        prefs,
        settings=settings,
        destinations=destinations,
        tdx_client=StubTdxClient(),
        weather_client=StubWeatherClient(),
    )

    ids = [r.destination.id for r in result.results]
    assert ids == ["wenshan", "xinyi"]


def test_context_parking_availability_can_reduce_crowd_risk():
    settings = get_settings()
    tz = ZoneInfo(settings.app.timezone)
    start = datetime(2026, 1, 5, 12, 0, tzinfo=tz)
    end = datetime(2026, 1, 5, 14, 0, tzinfo=tz)

    # Isolate context + parking influence.
    parking_cfg = settings.features.parking.model_copy(
        update={"radius_m": 500, "lot_cap": 1, "available_spaces_cap": 20}
    )
    crowd_cfg = settings.features.context.crowd.model_copy(update={"parking_risk_weight": 1.0})
    context_cfg = settings.features.context.model_copy(update={"crowd": crowd_cfg})
    features = settings.features.model_copy(update={"parking": parking_cfg, "context": context_cfg})
    settings = settings.model_copy(update={"features": features})

    destinations = [
        Destination(
            id="parking_ok",
            name="Parking available",
            location=GeoPoint(lat=25.0478, lon=121.5170),
            tags=["indoor"],
            city="Taipei",
            district="Zhongzheng",
        ),
        Destination(
            id="parking_none",
            name="No parking nearby",
            location=GeoPoint(lat=25.0478, lon=121.5300),
            tags=["indoor"],
            city="Taipei",
            district="Zhongzheng",
        ),
    ]

    prefs = UserPreferences(
        origin=GeoPoint(lat=25.0478, lon=121.5170),
        time_window=TimeWindow(start=start, end=end),
        max_results=2,
        component_weights=ComponentWeights(accessibility=0.0, weather=0.0, preference=0.0, context=1.0),
        avoid_crowds_importance=1.0,
        family_friendly_importance=0.0,
        tag_weights={"indoor": 1.0},
    )

    class ParkingTdxClient(StubTdxClient):
        def get_parking_lot_statuses(self, *, city: str | None = None):
            from tripscore.ingestion.tdx_client import ParkingLotStatus

            return [
                ParkingLotStatus(
                    parking_lot_uid="p1",
                    name="Lot 1",
                    lat=25.0479,
                    lon=121.5171,
                    available_spaces=20,
                    total_spaces=20,
                )
            ]

    result = recommend(
        prefs,
        settings=settings,
        destinations=destinations,
        tdx_client=ParkingTdxClient(),
        weather_client=StubWeatherClient(),
    )

    ids = [r.destination.id for r in result.results]
    assert ids == ["parking_ok", "parking_none"]
