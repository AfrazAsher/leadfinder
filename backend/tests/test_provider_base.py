from app.providers.base import ScraperBlocked, ScraperError, SOSResult


def test_sos_result_to_dict_roundtrip():
    r = SOSResult(
        filing_number="L15000123456",
        entity_name="ROLATOR & INDEPENDENCE LLC",
        status="Active",
        officers=[{"name": "KONDRU, SIVARAMAIAH", "title": "MGR"}],
    )
    d = r.to_dict()
    assert d["filing_number"] == "L15000123456"
    assert d["entity_name"] == "ROLATOR & INDEPENDENCE LLC"
    assert len(d["officers"]) == 1


def test_sos_result_defaults():
    r = SOSResult(filing_number="X", entity_name="Y", status="Z")
    assert r.officers == []
    assert r.principal_address == {}


def test_scraper_error_hierarchy():
    assert issubclass(ScraperBlocked, ScraperError)


def test_scraper_blocked_is_exception():
    try:
        raise ScraperBlocked("test")
    except Exception:
        pass
