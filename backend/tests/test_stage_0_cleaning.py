import pytest

from app.pipeline.stage_0_cleaning import clean_csv


def test_input_row_count(report):
    assert report.input_rows == 14


def test_final_entity_count(report):
    assert report.unique_entities == 4
    assert len(report.entities) == 4


def test_skip_counts(report):
    assert report.individuals_skipped == 1
    assert report.government_skipped == 1
    assert report.religious_skipped == 1
    assert report.probate_skipped == 1
    assert report.sentinel_skipped == 1
    assert report.data_error_skipped == 1
    assert report.misclassified_individual_skipped == 1
    assert report.total_skipped == 7


def test_robertson_crossing_dedup(report):
    robertson = next(e for e in report.entities if "ROBERTSON CROSSING" in e.entity_name_normalized)
    assert len(robertson.source_parcels) == 3
    apns = {p.apn for p in robertson.source_parcels}
    assert apns == {"APN-WAKE-01", "APN-WAKE-02", "APN-WAKE-03"}


def test_long_game_merge(report):
    long_games = [e for e in report.entities if "LONG GAME" in e.entity_name_normalized]
    assert len(long_games) == 1
    assert len(long_games[0].source_parcels) == 2


def test_treasure_valley_untruncated(report):
    tv = next(e for e in report.entities if "TREASURE VALLEY" in e.entity_name_normalized)
    assert tv.entity_name_cleaned == "TREASURE VALLEY INVESTMENTS LLC"
    assert tv.entity_type.value == "LLC"
    assert "truncated_source_name" in tv.quality_flags


def test_rolator_is_priority(report):
    rolator = next(e for e in report.entities if "ROLATOR" in e.entity_name_normalized)
    assert rolator.is_priority is True
    assert rolator.entity_type.value == "LLC"


def test_all_entities_have_parcels(report):
    for e in report.entities:
        assert len(e.source_parcels) >= 1


def test_long_game_normalization_matches(report):
    long_game = next(e for e in report.entities if "LONG GAME" in e.entity_name_normalized)
    assert "LL & C" in long_game.entity_name_normalized


def test_missing_required_columns_raises(tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("name,address\nFoo LLC,123 Main St")
    with pytest.raises(ValueError):
        clean_csv(str(bad_csv))
