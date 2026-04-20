from app.pipeline.officer_selection import select_best_officer


def test_sole_member_wins_over_assistant_secretary():
    officers = [
        {"name": "Asst Sec", "title": "Assistant Secretary"},
        {"name": "The Owner", "title": "Sole Member"},
    ]
    result = select_best_officer(officers)
    assert result["name"] == "The Owner"


def test_manager_wins_over_member():
    officers = [
        {"name": "Joe Member", "title": "Member"},
        {"name": "Jane Manager", "title": "Manager"},
    ]
    assert select_best_officer(officers)["name"] == "Jane Manager"


def test_mgr_abbreviation_matched():
    officers = [{"name": "Jane", "title": "MGR"}]
    assert select_best_officer(officers)["name"] == "Jane"


def test_ties_broken_by_document_order():
    officers = [
        {"name": "First MGR", "title": "Manager"},
        {"name": "Second MGR", "title": "Manager"},
    ]
    assert select_best_officer(officers)["name"] == "First MGR"


def test_exotic_title_defaults_to_first():
    officers = [
        {"name": "First Exotic", "title": "Chief Revenue Wizard"},
        {"name": "Second Exotic", "title": "Head of Vibes"},
    ]
    assert select_best_officer(officers)["name"] == "First Exotic"


def test_empty_officers_returns_none():
    assert select_best_officer([]) is None


def test_case_insensitive_matching():
    officers = [{"name": "J", "title": "manager"}]
    assert select_best_officer(officers)["name"] == "J"


def test_general_partner_beats_secretary():
    officers = [
        {"name": "Sec", "title": "Secretary"},
        {"name": "GP", "title": "General Partner"},
    ]
    assert select_best_officer(officers)["name"] == "GP"
