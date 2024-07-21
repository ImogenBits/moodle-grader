from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas


def draw_grading_page(canvas: Canvas) -> None:
    canvas.drawString(100, 100, "yay")



def add_grading_page(student_file: Path, output: Path | None = None) -> None:
    pdf_file = PdfWriter(clone_from=student_file)
    pdf_file.insert_blank_page()

    with BytesIO() as bytes_io:
        canvas = Canvas(bytes_io)
        draw_grading_page(canvas)
        canvas.save()
        bytes_io.seek(0)
        new_pdf = PdfReader(bytes_io)
        pdf_file.pages[0].merge_page(new_pdf.pages[0])

    pdf_file.write(output or student_file)
