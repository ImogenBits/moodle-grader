from io import BytesIO
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING

from pypdf import PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

if TYPE_CHECKING:
    from reportlab.pdfbase.acroform import AcroForm

getLogger("pypdf").setLevel(50)
points_label = "Gesamtpunkte:"
text = """Korrigiert von {name}.<br/>
Bei Fragen gerne eine mail an <a color="blue" href="mailto:{email}">{email}</a> schicken."""
style = ParagraphStyle("Normal", linkUnderline=True, fontSize=12, leading=15)


def draw_grading_page(canvas: Canvas, name: str, email: str) -> None:
    x, y = 4 * cm, 25 * cm
    width, height = 20 * cm, 10 * cm
    canvas.translate(x, y)

    para = Paragraph(points_label, style)
    para.wrap(width, height)
    para.drawOn(canvas, 0, 0)
    indent = stringWidth(points_label, style.fontName, style.fontSize)
    form: AcroForm = canvas.acroForm
    form.textfield(
        relative=True,
        x=int(indent + 0.25 * cm),
        y=-2,
        height=int(style.fontSize) + 4,
        width=int(style.fontSize * 3),
        name="moodleGradeField",
        tooltip="total points achieved in this assignment",
    )

    para = Paragraph(text.format(name=name, email=email), style)
    _, height = para.wrap(width, height)
    para.drawOn(canvas, 0, - 0.1 * cm - height)


def add_grading_page(student_file: Path, name: str, email: str, output: Path | None = None) -> None:
    pdf_file = PdfWriter(clone_from=student_file)

    with BytesIO() as bytes_io:
        canvas = Canvas(bytes_io, pagesize=A4)
        draw_grading_page(canvas, name, email)
        canvas.save()
        bytes_io.seek(0)
        pdf_file.merge(0, fileobj=bytes_io)

    pdf_file.write(output or student_file)
