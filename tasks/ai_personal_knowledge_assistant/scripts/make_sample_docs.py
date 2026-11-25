"""Generate the sample documents in `sample_docs/`.

    uv run python scripts/make_sample_docs.py

Output is byte-for-byte deterministic (fixed timestamps everywhere), so running
the script again leaves the committed files unchanged unless the content here
changed. The content is fictional and written so that retrieval has to tell
similar rules apart: casual, sick and earned leave all appear on the same page
with different numbers.
"""

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from docx import Document
from fpdf import FPDF
from fpdf.enums import XPos, YPos

SAMPLE_DOCS_DIR = Path(__file__).resolve().parents[1] / "sample_docs"
FIXED_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
# After each block of text, return to the left margin on the next line.
NEXT_LINE = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}

COMPANY = "Northwind Analytics"

HR_POLICY_PAGES: list[tuple[str, list[str]]] = [
    (
        "1. Introduction and Scope",
        [
            f"This Human Resources Policy describes the rules that apply to all "
            f"employees of {COMPANY}. It covers leave entitlements, working hours, "
            "remote work and the expected standard of conduct.",
            "The policy applies to all full-time and part-time employees on a "
            "permanent contract. Contractors and interns are covered by their own "
            "agreements unless stated otherwise.",
            "The Human Resources department owns this policy and reviews it once a "
            "year. Questions about any section should be sent to hr@northwind.example.",
        ],
    ),
    (
        "2. Leave Policy",
        [
            "Casual leave: Employees are entitled to 12 days of casual leave per "
            "calendar year. Casual leave is meant for personal matters and short "
            "absences. It cannot be carried forward to the next year and is not "
            "paid out when unused. No more than 3 days of casual leave may be taken "
            "in a row.",
            "Sick leave: Employees are entitled to 10 days of paid sick leave per "
            "calendar year. A medical certificate is required for any sick leave "
            "longer than 2 consecutive days. Unused sick leave does not carry over.",
            "Earned leave: Employees accrue 1.5 days of earned leave for every "
            "completed month of service, up to 18 days per year. Earned leave must "
            "be requested at least 2 weeks in advance. Up to 30 days of earned leave "
            "may be carried forward to the next year.",
            "Public holidays: The company observes 10 public holidays each year. The "
            "list is published by Human Resources every December.",
        ],
    ),
    (
        "3. Working Hours, Remote Work and Conduct",
        [
            "Working hours: The standard working week is 40 hours, Monday to Friday. "
            "Core hours, when every employee is expected to be available, are 10:00 "
            "to 16:00 local time.",
            "Remote work: Employees may work remotely up to 2 days per week with "
            "their manager's approval. Remote work from another country requires "
            "written approval from Human Resources.",
            "Code of conduct: Employees must treat colleagues, customers and partners "
            "with respect. Harassment and discrimination of any kind are not "
            "tolerated and should be reported to Human Resources or through the "
            "anonymous ethics hotline.",
        ],
    ),
]

HANDBOOK_SECTIONS: list[tuple[str, list[str]]] = [
    (
        "Onboarding",
        [
            "Your first week starts with a welcome session on Monday morning. Your "
            "manager assigns an onboarding buddy who answers day-to-day questions "
            "during your first 90 days.",
            "Complete the mandatory security awareness training within your first "
            "5 working days.",
        ],
    ),
    (
        "IT and Security Policy",
        [
            "Laptops are issued with full-disk encryption enabled. Never disable it.",
            "Passwords must be at least 14 characters long. Multi-factor "
            "authentication is required for email, the VPN and all internal tools.",
            "Report a lost or stolen device to the IT service desk within 1 hour.",
        ],
    ),
    (
        "Benefits",
        [
            "The company offers the following benefits to permanent employees:",
        ],
    ),
]

BENEFITS_TABLE: list[tuple[str, str]] = [
    ("Benefit", "Details"),
    ("Health insurance", "Covers the employee, spouse and up to 2 children"),
    ("Learning budget", "USD 1,000 per year for courses and books"),
    ("Wellness allowance", "USD 50 per month for gym or fitness apps"),
    ("Home office stipend", "One-time USD 300 for remote work equipment"),
]

TRAVEL_POLICY = f"""\
{COMPANY} - Travel and Expense Policy

1. Booking travel
All business travel must be approved by your manager before booking. Book flights
and hotels through the company travel portal at least 14 days in advance whenever
possible.

2. Flights
Economy class is used for flights shorter than 6 hours. Premium economy is allowed
for flights of 6 hours or longer.

3. Accommodation
The nightly hotel limit is USD 180 in most cities and USD 250 in high-cost cities
such as New York, London and Tokyo.

4. Meals and daily allowance
Employees receive a daily meal allowance of USD 60 while travelling. Alcohol is not
reimbursed.

5. Submitting expenses
Submit expense claims with itemised receipts within 30 days of returning from a
trip. Claims submitted later than 60 days are not reimbursed.
"""


def build_hr_policy_pdf() -> bytes:
    pdf = FPDF()
    pdf.set_creation_date(FIXED_TIMESTAMP)
    pdf.set_title("HR Policy")
    pdf.set_author(COMPANY)
    for heading, paragraphs in HR_POLICY_PAGES:
        pdf.add_page()
        pdf.set_font("Helvetica", style="B", size=16)
        pdf.multi_cell(0, 10, f"{COMPANY} - HR Policy", **NEXT_LINE)
        pdf.set_font("Helvetica", style="B", size=13)
        pdf.multi_cell(0, 9, heading, **NEXT_LINE)
        pdf.ln(2)
        pdf.set_font("Helvetica", size=11)
        for paragraph in paragraphs:
            pdf.multi_cell(0, 6, paragraph, **NEXT_LINE)
            pdf.ln(4)
    return bytes(pdf.output())


def build_employee_handbook_docx() -> bytes:
    document = Document()
    document.core_properties.title = "Employee Handbook"
    document.core_properties.author = COMPANY
    document.core_properties.last_modified_by = COMPANY
    document.core_properties.created = FIXED_TIMESTAMP
    document.core_properties.modified = FIXED_TIMESTAMP
    document.core_properties.revision = 1
    document.add_heading(f"{COMPANY} Employee Handbook", level=0)
    for heading, paragraphs in HANDBOOK_SECTIONS:
        document.add_heading(heading, level=1)
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
    table = document.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for benefit, details in BENEFITS_TABLE:
        cells = table.add_row().cells
        cells[0].text = benefit
        cells[1].text = details
    buffer = io.BytesIO()
    document.save(buffer)
    return _with_fixed_zip_timestamps(buffer.getvalue())


def _with_fixed_zip_timestamps(archive_bytes: bytes) -> bytes:
    """Rewrite a ZIP so every entry has the same timestamp.

    python-docx stamps entries with the current time, which would make every
    run produce a different file.
    """
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(archive_bytes)) as source,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for entry in source.infolist():
            fixed = zipfile.ZipInfo(entry.filename, date_time=ZIP_EPOCH)
            fixed.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(fixed, source.read(entry.filename))
    return output.getvalue()


def main() -> None:
    SAMPLE_DOCS_DIR.mkdir(exist_ok=True)
    outputs = {
        "HR_Policy.pdf": build_hr_policy_pdf(),
        "Employee_Handbook.docx": build_employee_handbook_docx(),
        "Travel_Policy.txt": TRAVEL_POLICY.encode("utf-8"),
    }
    for filename, content in outputs.items():
        path = SAMPLE_DOCS_DIR / filename
        path.write_bytes(content)
        print(
            f"wrote {path.relative_to(SAMPLE_DOCS_DIR.parent)} ({len(content):,} bytes)"
        )


if __name__ == "__main__":
    main()
