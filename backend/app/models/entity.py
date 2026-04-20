from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EntityType(str, Enum):
    LLC = "LLC"
    LP = "LP"
    LLLP = "LLLP"
    INC = "INC"
    CORP = "CORP"
    LTD = "LTD"
    TRUST = "TRUST"
    PARTNERSHIP = "PARTNERSHIP"
    OTHER = "OTHER"


class MailingAddress(BaseModel):
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    complete: bool = False


class SourceParcel(BaseModel):
    apn: str
    property_address: Optional[str] = None
    property_city: Optional[str] = None
    property_state: str
    county: Optional[str] = None


class CleanedEntity(BaseModel):
    entity_id: UUID = Field(default_factory=uuid4)
    entity_name_raw: str
    entity_name_cleaned: str
    entity_name_normalized: str
    entity_name_search: Optional[str] = None
    search_name_variants: list[str] = []
    entity_type: EntityType
    mailing_address: MailingAddress
    source_parcels: list[SourceParcel]
    filing_state_candidates: list[str] = []
    is_priority: bool = False
    quality_flags: list[str] = []
