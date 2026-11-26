import pytest
from pydantic import ValidationError

from ai_travel_agent.schemas.booking import BookingCreate
from ai_travel_agent.schemas.chat import MAX_MESSAGE_LENGTH, MessageCreate


def test_the_capstone_contract_field_name_item_id_is_accepted() -> None:
    request = BookingCreate.model_validate({"lines": [{"item_id": 101, "quantity": 1}]})
    assert request.quantities() == {101: 1}


def test_package_id_is_the_canonical_field_name() -> None:
    request = BookingCreate.model_validate(
        {"lines": [{"package_id": 3, "quantity": 2}, {"package_id": 4, "quantity": 1}]}
    )
    assert request.quantities() == {3: 2, 4: 1}


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [{"package_id": 1, "quantity": 0}],
        [{"package_id": 1, "quantity": 11}],
        [{"package_id": 0, "quantity": 1}],
        [{"package_id": 1, "quantity": 1}, {"package_id": 1, "quantity": 2}],
        [{"package_id": 1, "quantity": 1, "unit_price": "1"}],
    ],
)
def test_invalid_lines_are_rejected(lines: list[dict[str, object]]) -> None:
    with pytest.raises(ValidationError):
        BookingCreate.model_validate({"lines": lines})


def test_chat_messages_are_limited_to_4000_characters() -> None:
    assert MAX_MESSAGE_LENGTH == 4000
    MessageCreate.model_validate({"content": "x" * 4000})
    with pytest.raises(ValidationError):
        MessageCreate.model_validate({"content": "x" * 4001})
    with pytest.raises(ValidationError):
        MessageCreate.model_validate({"content": "   "})
