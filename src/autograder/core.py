from io import BytesIO
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING

from pypdf import PdfReader, PdfWriter
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
text = """Korrigiert von {name}<br/>
Bei Fragen könnt ihr gerne eine mail an <a color="blue" href="mailto:{email}">{email}</a> schicken"""
style = ParagraphStyle("Normal", linkUnderline=True, fontSize=12, leading=15)


def draw_grading_page(canvas: Canvas, name: str, email: str, image: Path | None) -> None:
    x, y = 2.5 * cm, 25 * cm
    width, height = 16 * cm, 10 * cm
    canvas.translate(x, y)

    para = Paragraph(points_label, style)
    para.wrap(width, height)
    para.drawOn(canvas, 0, 0)
    indent = stringWidth(points_label, style.fontName, style.fontSize)
    form: AcroForm = canvas.acroForm
    form.textfield(
        relative=True,
        x=int(indent + 0.25 * cm),
        y=0,
        height=int(style.fontSize) + 2,
        width=int(style.fontSize * 3),
        name="moodleGradeField",
        tooltip="total points achieved in this assignment",
    )

    para = Paragraph(text.format(name=name, email=email), style)
    _, height = para.wrap(width, height)
    canvas.translate(0, -0.1 * cm - height)
    para.drawOn(canvas, 0, 0)

    if image:
        canvas.drawImage(
            image,
            x=-2 * cm,
            y=-16 * cm,
            width=width + 4 * cm,
            height=15 * cm,
            preserveAspectRatio=True,
            anchor="n",
        )


def add_grading_page(student_file: Path, name: str, email: str, image: Path | None, output: Path | None = None) -> None:
    pdf_file = PdfWriter(clone_from=student_file)

    with BytesIO() as bytes_io:
        canvas = Canvas(bytes_io, pagesize=A4)
        draw_grading_page(canvas, name, email, image)
        canvas.save()
        bytes_io.seek(0)
        pdf_file.merge(0, fileobj=bytes_io)

    pdf_file.write(output or student_file)


def get_points(student_file: Path) -> float | None:
    file = PdfReader(student_file)
    data = file.get_form_text_fields().get("moodleGradeField")
    return float(data) if data else None


def set_points(student_file: Path, points: float) -> None:
    file = PdfWriter(clone_from=student_file)
    file.update_page_form_field_values(file.pages[0], {"moodleGradeField": str(points)}, auto_regenerate=False)
    file.write(student_file)
