"""Generate the starter policy documents in `knowledge_base/`.

    uv run python scripts/make_knowledge_base.py

The content is fictional. Every document states its version and effective date
on page 1, and each PDF section starts on its own page so citations point to a
specific page. The cancellation tiers here must match
`services/cancellation_policy.py`, which is what the app actually applies.

Output is byte-for-byte deterministic (fixed creation date), so running the
script again leaves the committed files unchanged unless the content changed.
The PDFs use a core font, so amounts are written as "INR" rather than the rupee
sign.
"""

from datetime import UTC, datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parents[1] / "knowledge_base"
FIXED_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)
# After each block of text, return to the left margin on the next line.
NEXT_LINE = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}

AGENCY = "Praveen's Travels"
VERSION_LINE = "Version 1.0 - Effective 2026-01-01"

Section = tuple[str, list[str]]

CANCELLATION_POLICY: list[Section] = [
    (
        "1. Cancellation by the traveller",
        [
            f"This policy applies to every tour package booked with {AGENCY}, "
            "whether the booking was made on the website or through the AI travel "
            "assistant. Days are counted as whole calendar days from the date of "
            "cancellation to the departure date of the package.",
            "Full refund: cancel at least 7 days before departure and 100% of the "
            "amount paid is refunded.",
            "Partial refund: cancel between 2 and 6 days before departure and 50% "
            "of the amount paid is refunded.",
            "No refund: cancel less than 2 days before departure (that is, on the "
            "day before departure) and nothing is refunded.",
            "A booking cannot be cancelled on or after the departure date. Missed "
            "departures are treated as no-shows and are not refunded.",
            "Example: a booking of INR 24000 for a trip departing on 20 March and "
            "cancelled on 16 March (4 days before departure) is refunded INR 12000.",
        ],
    ),
    (
        "2. Cancellation by the operator",
        [
            f"If {AGENCY} cancels a departure (for example because of weather, a "
            "road closure, a government advisory or too few travellers), every "
            "traveller on that departure receives a full refund of the amount "
            "paid, whatever the date of cancellation.",
            "We will offer an alternative departure where one is available. "
            "Accepting it is optional.",
        ],
    ),
    (
        "3. How refunds are paid",
        [
            "Refunds are paid to the original payment method within 7 business days "
            "of the cancellation.",
            "Partial cancellations are not possible: a booking is cancelled as a "
            "whole, including every traveller on it. To travel with fewer people, "
            "cancel the booking and book again for the new group size.",
            "Changing the departure date of an existing booking is treated as a "
            "cancellation followed by a new booking at the current price.",
        ],
    ),
]

TRAVEL_POLICY: list[Section] = [
    (
        "1. Booking and payment",
        [
            "Prices are per traveller and in Indian rupees (INR). The price of a "
            "booking is fixed when the booking is confirmed; later price changes do "
            "not affect confirmed bookings.",
            "A single booking may include up to 10 travellers per package. Groups "
            "larger than 10 should contact us for a group quotation.",
            "Bookings are confirmed instantly while seats are available. Seats are "
            "never held without a confirmed booking.",
        ],
    ),
    (
        "2. Identity documents and permits",
        [
            "Every traveller must carry a valid government photo ID (Aadhaar, "
            "passport, voter ID or driving licence) for hotel check-in and domestic "
            "flights.",
            "Ladakh packages: the Inner Line Permit for Nubra Valley and Pangong "
            "Lake is arranged by us; send a scanned photo ID at least 5 days before "
            "departure.",
            "Andaman packages: foreign nationals need a Restricted Area Permit, "
            "issued on arrival at Port Blair airport.",
        ],
    ),
    (
        "3. Baggage, health and conduct",
        [
            "Baggage: one check-in bag of up to 15 kg and one cabin bag of up to "
            "7 kg per traveller are included on all road transfers. Airline "
            "allowances on flights booked separately are set by the airline.",
            "Health: high-altitude packages (Ladakh, Kashmir Valley Deluxe "
            "Sonamarg excursion) are not recommended for travellers with heart or "
            "respiratory conditions. Travel insurance is strongly recommended and is "
            "not included in package prices.",
            "Children: children under 5 travel free without a seat of their own; "
            "children aged 5 and over need a seat and pay the full package price.",
            "Conduct: travellers are expected to respect local customs and the "
            "instructions of their tour leader. The tour leader may refuse to carry "
            "a traveller whose conduct endangers the group, without refund.",
        ],
    ),
]

DESTINATION_GUIDE: list[Section] = [
    (
        "1. Kashmir (Srinagar, Gulmarg, Pahalgam)",
        [
            "Best season: April to October for meadows and lakes; December to "
            "February for snow in Gulmarg.",
            "Highlights: a shikara ride on Dal Lake, the Gulmarg gondola, the Betaab "
            "valley in Pahalgam and the Mughal gardens of Srinagar.",
            "Packing: light woollens even in summer; heavy jackets, gloves and "
            "waterproof boots in winter.",
        ],
    ),
    (
        "2. Goa and the Andaman Islands",
        [
            "Best season: November to March. The monsoon (June to September) brings "
            "rough seas; some water sports and island ferries are suspended.",
            "Highlights: South Goa beaches, spice farms and Old Goa churches; "
            "Radhanagar beach, snorkelling at Elephant beach and Neil island in the "
            "Andamans.",
            "Packing: sunscreen, a hat and reef-safe footwear.",
        ],
    ),
    (
        "3. Kerala, Ladakh and the North-East",
        [
            "Kerala backwaters are pleasant from September to March; houseboats run "
            "all year.",
            "Ladakh is open from May to September. Spend the first day in Leh "
            "resting to acclimatise to the altitude of 3500 m.",
            "Meghalaya is greenest in the monsoon, when the living root bridges and "
            "waterfalls are at their best; carry rain gear and good walking shoes.",
        ],
    ),
]

TRAVEL_FAQ = f"""\
{AGENCY} - Frequently Asked Questions
{VERSION_LINE}

Q: How do I book a package?
A: Sign in, choose a package, pick the number of travellers and confirm. You can
also ask the AI travel assistant to find and book a package; it always asks you
to press Confirm before anything is booked.

Q: Is the price shown the final price?
A: Yes. Prices are per traveller and include accommodation, local transfers and
the activities listed in the package description. Flights to the starting city
and travel insurance are not included.

Q: How many travellers can I book at once?
A: Up to 10 travellers per package in one booking.

Q: Can I cancel my booking?
A: Yes, before the departure date. The refund depends on how many days before
departure you cancel; see the Cancellation Policy for the exact rules.

Q: When will I receive my refund?
A: Within 7 business days, to the original payment method.

Q: Can I change the travel date?
A: A date change is treated as a cancellation and a new booking. See the
Cancellation Policy.

Q: Do children pay?
A: Children under 5 travel free without their own seat. Children aged 5 and over
pay the full price.

Q: Is travel insurance included?
A: No. We strongly recommend buying travel insurance separately.

Q: How do I contact support?
A: Email support@praveens-travels.example or call +91 80 0000 0000 between
09:00 and 18:00 IST, Monday to Saturday.
"""


def build_pdf(title: str, sections: list[Section]) -> bytes:
    """A PDF with a title block on page 1 and one section per page."""
    pdf = FPDF(format="A4")
    pdf.set_creation_date(FIXED_TIMESTAMP)
    pdf.set_title(title)
    pdf.set_author(AGENCY)
    pdf.set_margins(20, 20, 20)
    for index, (heading, paragraphs) in enumerate(sections):
        pdf.add_page()
        if index == 0:
            pdf.set_font("Helvetica", style="B", size=18)
            pdf.multi_cell(0, 10, f"{AGENCY} - {title}", **NEXT_LINE)
            pdf.set_font("Helvetica", style="I", size=11)
            pdf.multi_cell(0, 7, VERSION_LINE, **NEXT_LINE)
            pdf.ln(6)
        pdf.set_font("Helvetica", style="B", size=13)
        pdf.multi_cell(0, 9, heading, **NEXT_LINE)
        pdf.ln(2)
        pdf.set_font("Helvetica", size=11)
        for paragraph in paragraphs:
            pdf.multi_cell(0, 6, paragraph, **NEXT_LINE)
            pdf.ln(4)
    return bytes(pdf.output())


def main() -> None:
    KNOWLEDGE_BASE_DIR.mkdir(exist_ok=True)
    outputs = {
        "Cancellation_Policy.pdf": build_pdf(
            "Cancellation Policy", CANCELLATION_POLICY
        ),
        "Travel_Policy.pdf": build_pdf("Travel Policy", TRAVEL_POLICY),
        "Destination_Guide.pdf": build_pdf("Destination Guide", DESTINATION_GUIDE),
        "Travel_FAQ.txt": TRAVEL_FAQ.encode("utf-8"),
    }
    for filename, content in outputs.items():
        path = KNOWLEDGE_BASE_DIR / filename
        path.write_bytes(content)
        print(
            f"wrote {path.relative_to(KNOWLEDGE_BASE_DIR.parent)} "
            f"({len(content):,} bytes)"
        )


if __name__ == "__main__":
    main()
