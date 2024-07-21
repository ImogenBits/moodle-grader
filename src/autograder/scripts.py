"""Autograder scripts."""

from pathlib import Path

from autograder.core import add_grading_page
from rich.console import Console
from rich.theme import Theme
from typer import Typer

theme = Theme({
    "success": "green",
    "warning": "orange3",
    "error": "red",
    "attention": "magenta2",
    "heading": "blue",
    "info": "dim cyan",
})
console = Console(theme=theme)
app = Typer(pretty_exceptions_show_locals=True)


@app.command()
def main(student_file: Path):
    add_grading_page(student_file, student_file.with_name(f"new_{student_file.name}"))


if __name__ == "__main__":
    app()
