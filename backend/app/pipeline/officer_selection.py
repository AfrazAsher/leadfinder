"""Title-priority-based officer selection for Stage 5."""
import re
from typing import Optional

TITLE_PRIORITY: list[tuple[str, int]] = [
    # Member-managed LLC: the SOLE MEMBER is the owner
    ("sole member", 100),
    ("sole mgr", 100),
    ("member/manager", 95),
    ("managing member", 95),

    # Manager-managed LLC
    ("mgrm", 90),
    ("manager", 90),
    ("mgr", 90),
    ("ambr", 85),
    ("authorized member", 85),
    ("member", 80),

    # Corporation
    ("ceo", 75),
    ("president", 70),
    ("pres", 70),
    ("chairman", 65),
    ("chair", 65),

    # LP / LLP
    ("general partner", 88),
    ("gen ptr", 88),
    ("gp", 88),

    # Officers
    ("cfo", 55),
    ("coo", 55),
    ("vp", 50),
    ("vice president", 50),
    ("treasurer", 45),
    ("secretary", 40),
    ("sec", 40),

    # Admin
    ("assistant secretary", 20),
    ("asst sec", 20),
    ("trustee", 35),
    ("director", 30),
]

_DEFAULT_PRIORITY = 10


def _normalize_title(title: Optional[str]) -> str:
    if not title:
        return ""
    t = title.lower().replace("&", "and")
    t = re.sub(r"[.,;:!?]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def title_priority_score(title: Optional[str]) -> int:
    """Return the highest priority score for any priority phrase that appears
    as a whole-word substring of the normalized title. Zero if no match."""
    norm = _normalize_title(title)
    if not norm:
        return 0
    best = 0
    for phrase, score in TITLE_PRIORITY:
        if re.search(rf"\b{re.escape(phrase)}\b", norm):
            if score > best:
                best = score
    return best


def select_best_officer(officers: list[dict]) -> Optional[dict]:
    """Return the officer whose title has the highest priority score.

    Ties → first in document order. If no officer matches any priority phrase,
    return the first officer with effective priority 10. None if `officers`
    is empty.
    """
    if not officers:
        return None

    best_score = -1
    best_officer: Optional[dict] = None
    for officer in officers:
        score = title_priority_score(officer.get("title"))
        if score > best_score:
            best_score = score
            best_officer = officer

    if best_score > 0:
        return best_officer

    return officers[0]
