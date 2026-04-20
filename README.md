# LeadFinder

Tool that takes a CSV of property ownership records and enriches LLC entities with decision-maker contact info.

## Quickstart

From the project root (with `./venv` already activated):

```bash
cd backend
pip install -e ".[dev]"
cp .env.example .env
pytest
uvicorn app.main:app --reload
# visit http://localhost:8000/health
```

## Stage

Currently implemented: **Stage 0 — Data Cleaning**. Reads a CSV of property records, classifies rows (individual vs. entity vs. government/religious/probate/sentinel), normalizes entity names, deduplicates, and emits a `CleaningReport` of cleaned `CleanedEntity` records with source parcels.
