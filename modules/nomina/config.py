from __future__ import annotations

from decimal import Decimal

# UMI can be extended per year without touching parser logic.
UMI_BY_YEAR: dict[int, Decimal] = {
    2026: Decimal("100.81"),
}


def get_umi_for_year(year: int | None) -> Decimal | None:
    if year is None:
        return None
    return UMI_BY_YEAR.get(int(year))
