from tripscore.ingestion.tdx_city_match import to_tdx_city


def test_to_tdx_city_common_chinese_names():
    assert to_tdx_city("臺北市") == "Taipei"
    assert to_tdx_city("台中市") == "Taichung"
    assert to_tdx_city("新北市") == "NewTaipei"
    assert to_tdx_city("嘉義縣") == "ChiayiCounty"


def test_to_tdx_city_passthrough_and_unknown():
    assert to_tdx_city("Taipei") == "Taipei"
    assert to_tdx_city("  NewTaipei ") == "NewTaipei"
    assert to_tdx_city("UnknownCity") is None

