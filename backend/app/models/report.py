from pydantic import BaseModel, computed_field

from .entity import CleanedEntity


class CleaningReport(BaseModel):
    input_rows: int
    unique_entities: int
    individuals_skipped: int
    government_skipped: int
    religious_skipped: int
    probate_skipped: int
    sentinel_skipped: int
    data_error_skipped: int
    misclassified_individual_skipped: int
    entities: list[CleanedEntity]
    skip_summary_text: str

    @computed_field
    @property
    def total_skipped(self) -> int:
        return (
            self.individuals_skipped
            + self.government_skipped
            + self.religious_skipped
            + self.probate_skipped
            + self.sentinel_skipped
            + self.data_error_skipped
            + self.misclassified_individual_skipped
        )
