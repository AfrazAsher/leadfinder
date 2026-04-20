from typing import Optional, Protocol

from app.models.entity import CleanedEntity


class SOSResult:
    def __init__(
        self,
        filing_number: str,
        entity_name: str,
        status: str,
        principal_address: Optional[dict] = None,
        mailing_address: Optional[dict] = None,
        registered_agent: Optional[dict] = None,
        officers: Optional[list[dict]] = None,
        filing_date: Optional[str] = None,
        source_url: Optional[str] = None,
    ):
        self.filing_number = filing_number
        self.entity_name = entity_name
        self.status = status
        self.principal_address = principal_address or {}
        self.mailing_address = mailing_address or {}
        self.registered_agent = registered_agent or {}
        self.officers = officers or []
        self.filing_date = filing_date
        self.source_url = source_url

    def to_dict(self) -> dict:
        return {
            "filing_number": self.filing_number,
            "entity_name": self.entity_name,
            "status": self.status,
            "principal_address": self.principal_address,
            "mailing_address": self.mailing_address,
            "registered_agent": self.registered_agent,
            "officers": self.officers,
            "filing_date": self.filing_date,
            "source_url": self.source_url,
        }


class SOSProvider(Protocol):
    state_code: str
    display_name: str

    async def search(self, entity: CleanedEntity) -> Optional[SOSResult]:
        ...


class ScraperError(Exception):
    pass


class ScraperBlocked(ScraperError):
    pass
