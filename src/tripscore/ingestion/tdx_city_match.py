"""
Best-effort mapping from human-readable city names to TDX city codes.

TDX uses English-ish identifiers (e.g., "NewTaipei", "HsinchuCounty").
Catalog data may contain Chinese city names (e.g., "臺北市") or variations.
"""

from __future__ import annotations

from tripscore.ingestion.tdx_cities import ALL_CITIES


_ALIASES: dict[str, str] = {
    # Municipalities
    "台北": "Taipei",
    "臺北": "Taipei",
    "台北市": "Taipei",
    "臺北市": "Taipei",
    "新北": "NewTaipei",
    "新北市": "NewTaipei",
    "桃園": "Taoyuan",
    "桃園市": "Taoyuan",
    "台中": "Taichung",
    "臺中": "Taichung",
    "台中市": "Taichung",
    "臺中市": "Taichung",
    "台南": "Tainan",
    "臺南": "Tainan",
    "台南市": "Tainan",
    "臺南市": "Tainan",
    "高雄": "Kaohsiung",
    "高雄市": "Kaohsiung",
    # Cities / counties
    "基隆": "Keelung",
    "基隆市": "Keelung",
    "新竹市": "Hsinchu",
    "新竹縣": "HsinchuCounty",
    "苗栗縣": "MiaoliCounty",
    "彰化縣": "ChanghuaCounty",
    "南投縣": "NantouCounty",
    "雲林縣": "YunlinCounty",
    "嘉義市": "Chiayi",
    "嘉義縣": "ChiayiCounty",
    "屏東縣": "PingtungCounty",
    "宜蘭縣": "YilanCounty",
    "花蓮縣": "HualienCounty",
    "台東縣": "TaitungCounty",
    "臺東縣": "TaitungCounty",
    "金門縣": "KinmenCounty",
    "澎湖縣": "PenghuCounty",
    "連江縣": "LienchiangCounty",
}


def to_tdx_city(city: str | None) -> str | None:
    """Map a city string to a TDX city code (or return None if unknown)."""
    if not city:
        return None
    s = str(city).strip()
    if not s:
        return None

    # Exact match to known TDX codes.
    if s in ALL_CITIES:
        return s

    # Normalize common separators.
    compact = s.replace(" ", "").replace("_", "")
    if compact in ALL_CITIES:
        return compact

    if compact in _ALIASES:
        return _ALIASES[compact]

    # Try stripping suffixes (市/縣) if present.
    if compact.endswith("市") or compact.endswith("縣"):
        base = compact[:-1]
        if base in _ALIASES:
            return _ALIASES[base]

    return None

