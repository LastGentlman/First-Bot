from tabla_parser import _normalize_hour, _parse_line

def test_normalize_hour():
    assert _normalize_hour("2:5") == "02:05"
    assert _normalize_hour("14.30") == "14:30"
    assert _normalize_hour("24:00") is None


def test_parse_line_good():
    row = _parse_line("?164172415 02:35 si")
    assert row["folio"] == "?164172415"
    assert row["hora"] == "02:35"
    assert row["status"] == "✅"


def test_parse_line_bad():
    assert _parse_line("texto incompleto") is None
