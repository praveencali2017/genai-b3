from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from ai_travel_agent.schemas.package import PackageCreate, PackageUpdate


def _draft(**overrides: object) -> dict[str, object]:
    draft: dict[str, object] = {
        "title": "Kashmir Explorer",
        "destination": "Srinagar",
        "duration_days": 5,
        "price": "24000",
        "capacity": 8,
        "start_date": (date.today() + timedelta(days=30)).isoformat(),
    }
    return {**draft, **overrides}


def test_a_valid_draft_is_accepted() -> None:
    package = PackageCreate.model_validate(_draft())
    assert package.capacity == 8


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "ab"},
        {"title": "x" * 121},
        {"price": "0"},
        {"price": "-5"},
        {"capacity": -1},
        {"duration_days": 0},
        {"start_date": date.today().isoformat()},
        {"start_date": (date.today() - timedelta(days=1)).isoformat()},
        {"unexpected": True},
    ],
)
def test_invalid_drafts_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PackageCreate.model_validate(_draft(**overrides))


def test_update_may_clear_the_description_but_not_required_fields() -> None:
    assert PackageUpdate.model_validate({"description": None}).description is None
    with pytest.raises(ValidationError, match="can't be null"):
        PackageUpdate.model_validate({"price": None})
