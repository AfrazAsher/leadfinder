from pathlib import Path

from app.providers.sos_fl import parse_address_lines, parse_fl_detail_html, parse_fl_results_html

FIXTURES = Path(__file__).parent / "fixtures" / "sos"


def test_fl_results_parses_rolator():
    html = (FIXTURES / "fl_results_rolator.html").read_text()
    results = parse_fl_results_html(html)
    assert len(results) == 1
    assert "ROLATOR" in results[0]["name"]
    assert results[0]["number"] == "L15000123456"
    assert results[0]["status"] == "Active"
    assert "SearchResultDetail" in results[0]["detail_url"]


def test_fl_results_empty_returns_empty_list():
    html = (FIXTURES / "fl_results_empty.html").read_text()
    assert parse_fl_results_html(html) == []


def test_fl_detail_parses_rolator():
    html = (FIXTURES / "fl_detail_rolator.html").read_text()
    parsed = parse_fl_detail_html(html)
    assert parsed["entity_name"] == "ROLATOR & INDEPENDENCE LLC"
    assert parsed["filing_number"] == "L15000123456"
    assert parsed["status"] == "Active"
    assert len(parsed["officers"]) >= 1
    officer = parsed["officers"][0]
    assert "KONDRU" in officer["name"]


def test_fl_detail_extracts_principal_address():
    html = (FIXTURES / "fl_detail_rolator.html").read_text()
    parsed = parse_fl_detail_html(html)
    assert parsed["principal_address"] is not None
    assert parsed["principal_address"]["state"] == "TX"
    assert parsed["principal_address"]["zip"] == "75035"


def test_parse_address_lines_standard_us():
    result = parse_address_lines(["10967 GRINDSTONE MNR", "FRISCO, TX 75035"])
    assert result["street"] == "10967 GRINDSTONE MNR"
    assert result["city"] == "FRISCO"
    assert result["state"] == "TX"
    assert result["zip"] == "75035"
