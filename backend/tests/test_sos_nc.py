from pathlib import Path

from app.providers.sos_nc import parse_nc_detail_html, parse_nc_results_html

FIXTURES = Path(__file__).parent / "fixtures" / "sos"


def test_nc_results_parses_acme():
    html = (FIXTURES / "nc_results_acme.html").read_text()
    results = parse_nc_results_html(html)
    assert len(results) == 1
    assert results[0]["name"] == "ACME HOLDINGS LLC"
    assert results[0]["number"] == "123456"


def test_nc_results_empty_returns_empty_list():
    html = (FIXTURES / "nc_results_empty.html").read_text()
    assert parse_nc_results_html(html) == []


def test_nc_detail_parses_acme():
    html = (FIXTURES / "nc_detail_acme.html").read_text()
    parsed = parse_nc_detail_html(html)
    assert parsed["entity_name"] == "ACME HOLDINGS LLC"
    assert parsed["filing_number"] == "123456"
    assert parsed["status"] == "Current-Active"
    assert len(parsed["officers"]) >= 1
    assert parsed["officers"][0]["name"] == "SMITH, JANE"
    assert parsed["officers"][0]["title"] == "Manager"


def test_nc_detail_extracts_principal_address():
    html = (FIXTURES / "nc_detail_acme.html").read_text()
    parsed = parse_nc_detail_html(html)
    assert parsed["principal_address"] is not None
    assert parsed["principal_address"]["state"] == "NC"
