"""Small in-memory PDF and DOCX files for the ingestion tests."""

import io

from docx import Document
from fpdf import FPDF
from fpdf.enums import XPos, YPos


def make_pdf(page_texts: list[str]) -> bytes:
    """A PDF with one page per entry; an empty string gives a blank page."""
    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    for text in page_texts:
        pdf.add_page()
        if text:
            pdf.multi_cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


def make_docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    if table:
        docx_table = document.add_table(rows=0, cols=len(table[0]))
        for row in table:
            for cell, text in zip(docx_table.add_row().cells, row, strict=True):
                cell.text = text
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
