"""Booking status rules: which status changes are allowed, and why one wasn't.

Pure logic with no I/O, so the API and its tests share one definition.

    Booked --check in--> Checked In --check out--> Checked Out
    Booked --cancel----> Cancelled
"""

from dataclasses import dataclass

from schemas import BookingStatus

# Bookings that still occupy a room, and so may be moved to another one.
# Checked Out and Cancelled bookings are kept unchanged as history.
ACTIVE_STATUSES = frozenset({BookingStatus.BOOKED, BookingStatus.CHECKED_IN})


@dataclass(frozen=True)
class StatusTransition:
    action: str
    from_status: BookingStatus
    to_status: BookingStatus


CHECK_IN = StatusTransition("check in", BookingStatus.BOOKED, BookingStatus.CHECKED_IN)
CHECK_OUT = StatusTransition("check out", BookingStatus.CHECKED_IN, BookingStatus.CHECKED_OUT)
CANCEL = StatusTransition("cancel", BookingStatus.BOOKED, BookingStatus.CANCELLED)


def conflict_detail(transition: StatusTransition, current: BookingStatus) -> str:
    """Explain why `transition` can't be applied to a booking in `current` status."""
    if current == transition.to_status:
        return f"Booking is already {current}."
    if transition is CHECK_OUT and current == BookingStatus.BOOKED:
        return "Cannot check out: the guest has not checked in yet."
    return f"Cannot {transition.action}: booking is {current}."


def room_change_conflict_detail(current: BookingStatus) -> str:
    return f"Cannot change the room: booking is {current}."
