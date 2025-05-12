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
Bei Fragen könnt ihr gerne eine mail an <a color="blue" href="mailto:{email}\
?subject=Korrektur Blatt {week} {group}">{email}</a> schicken"""
style = ParagraphStyle("Normal", linkUnderline=True, fontSize=12, leading=15)


def draw_grading_page(canvas: Canvas, name: str, email: str, group: str, week: str, image: Path | None) -> None:
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

    para = Paragraph(text.format(name=name, email=email, group=group.title(), week=week), style)
    print(para)
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


def add_grading_page(
    student_file: Path, name: str, email: str, group: str, week: str, image: Path | None, output: Path | None = None
) -> None:
    pdf_file = PdfWriter(clone_from=student_file)

    with BytesIO() as bytes_io:
        canvas = Canvas(bytes_io, pagesize=A4)
        draw_grading_page(canvas, name, email, group, week, image)
        canvas.save()
        bytes_io.seek(0)
        pdf_file.merge(0, fileobj=bytes_io)

    pdf_file.write(output or student_file)


def get_points(student_file: Path) -> float | None:
    file = PdfReader(student_file)
    data = file.get_form_text_fields().get("moodleGradeField")
    if not data:
        return None
    try:
        return float(data)
    except ValueError:
        try:
            return float(data.replace(",", "."))
        except ValueError:
            return None


def modify_pdf(student_file: Path, points: float | None = None, bonus_image: Path | None = None) -> None:
    if not points and not bonus_image:
        return
    pdf = PdfWriter(clone_from=student_file)
    if points:
        pdf.update_page_form_field_values(pdf.pages[0], {"moodleGradeField": str(points)}, auto_regenerate=False)
    if bonus_image:
        page = pdf.get_page(0)
        with BytesIO() as bytes_io:
            canvas = Canvas(bytes_io, pagesize=A4)
            canvas.drawImage(
                bonus_image,
                x=0.5 * cm,
                y=0.5 * cm,
                width=20 * cm,
                height=11.25 * cm,
                preserveAspectRatio=True,
                anchor="n",
            )
            canvas.save()
            bytes_io.seek(0)
            new_reader = PdfReader(bytes_io)
            page.merge_page(new_reader.get_page(0))
    pdf.write(student_file)
