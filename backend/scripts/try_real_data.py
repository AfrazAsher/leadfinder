"""Quick check: run Stage 0 on the real main_data.csv and print a summary."""
import sys
from pathlib import Path

# Add app to path when running as script
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.pipeline.stage_0_cleaning import clean_csv

# Adjust this path to wherever main_data.csv lives on your machine
CSV_PATH = Path(__file__).parent.parent.parent / "data" / "main_data.csv"


report = clean_csv(CSV_PATH)

print(f"\n=== CLEANING REPORT ===")
print(f"Input rows:                      {report.input_rows}")
print(f"Unique entities (kept):          {report.unique_entities}")
print(f"")
print(f"Individuals skipped:             {report.individuals_skipped}")
print(f"Government skipped:              {report.government_skipped}")
print(f"Religious skipped:               {report.religious_skipped}")
print(f"Probate skipped:                 {report.probate_skipped}")
print(f"Sentinel skipped:                {report.sentinel_skipped}")
print(f"Data error skipped:              {report.data_error_skipped}")
print(f"Misclassified individual:        {report.misclassified_individual_skipped}")
print(f"Total skipped:                   {report.total_skipped}")
print(f"")
print(f"Summary: {report.skip_summary_text}")
print(f"")
print(f"=== FIRST 10 ENTITIES ===")
for e in report.entities[:10]:
    flags = f" [{', '.join(e.quality_flags)}]" if e.quality_flags else ""
    print(f"  {e.entity_type.value:12s} {e.entity_name_cleaned}{flags}")
print(f"")
print(f"=== ENTITIES WITH QUALITY FLAGS ===")
flagged = [e for e in report.entities if e.quality_flags]
print(f"  {len(flagged)} of {len(report.entities)} entities have quality flags")
for e in flagged[:20]:
    print(f"  {e.entity_name_cleaned}: {', '.join(e.quality_flags)}")
    
    