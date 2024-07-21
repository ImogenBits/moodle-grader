from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

text = """Korrigiert von {name}.<br/>
Bei Fragen gerne eine mail an <a color="blue" href="mailto:{email}">{email}</a> schicken."""


def draw_grading_page(canvas: Canvas, name: str, email: str) -> None:
    canvas.translate(4 * cm, 25 * cm)
    para = Paragraph(text.format(name=name, email=email), ParagraphStyle('Normal', linkUnderline=True))
    _, height = para.wrap(20 * cm, 10 * cm)
    para.drawOn(canvas, 0, -height)



def add_grading_page(student_file: Path, name: str, email: str, output: Path | None = None) -> None:
    pdf_file = PdfWriter(clone_from=student_file)
    pdf_file.insert_blank_page()

    with BytesIO() as bytes_io:
        canvas = Canvas(bytes_io, pagesize=A4)
        draw_grading_page(canvas, name, email)
        canvas.save()
        bytes_io.seek(0)
        new_pdf = PdfReader(bytes_io)
        pdf_file.pages[0].merge_page(new_pdf.pages[0])

    pdf_file.write(output or student_file)
