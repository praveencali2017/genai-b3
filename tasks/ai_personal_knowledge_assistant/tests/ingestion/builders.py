"""Small in-memory PDF and DOCX files for the ingestion tests."""

import io

from docx import Document
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from PIL import Image, ImageDraw, ImageFont


def make_pdf(page_texts: list[str]) -> bytes:
    """A PDF with one page per entry; an empty string gives a blank page."""
    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    for text in page_texts:
        pdf.add_page()
        if text:
            pdf.multi_cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


def make_scanned_pdf(page_texts: list[str]) -> bytes:
    """A PDF whose pages are only pictures of `page_texts`, with no text layer,
    like the output of a scanner."""
    pdf = FPDF()
    for text in page_texts:
        pdf.add_page()
        pdf.image(_picture_of_text(text), x=10, y=10, w=190)
    return bytes(pdf.output())


def _picture_of_text(text: str) -> Image.Image:
    # Large, black-on-white type, so OCR reads it reliably.
    image = Image.new("L", (1900, 300), color=255)
    font = ImageFont.load_default(size=64)
    ImageDraw.Draw(image).text((40, 100), text, fill=0, font=font)
    return image


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
