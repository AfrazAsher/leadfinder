from pathlib import Path

import pytest

from app.pipeline.stage_0_cleaning import clean_csv

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def report():
    csv_path = FIXTURES_DIR / "sample_input.csv"
    return clean_csv(str(csv_path))
