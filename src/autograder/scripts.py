"""Autograder scripts."""

from pathlib import Path
from typing import ClassVar, Self

from autograder.core import add_grading_page
from pydantic import BaseModel, EmailStr
from rich.console import Console
from rich.theme import Theme
from typer import Typer, confirm, get_app_dir, launch, prompt

APP_NAME = "moodle_pdf_autograder"
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


class AppConfig(BaseModel):
    name: str
    email: EmailStr

    location: ClassVar[Path] = Path(get_app_dir(APP_NAME)) / "config.json"

    @classmethod
    def get(cls) -> Self:
        path = cls.location
        if path.is_file():
            return cls.model_validate_json(path.read_text())
        name = prompt("What name do you want to use?")
        email = prompt(
            "What email address do you want to use?", default=f"{".".join(name.lower().split())}@rwth-aachen.de"
        )
        config = cls(name=name, email=email)
        config.save()
        return config

    def save(self) -> None:
        self.location.parent.mkdir(parents=True, exist_ok=True)
        self.location.write_text(self.model_dump_json(indent=2))


@app.command()
def config():
    if not AppConfig.location.is_file():
        confirm("The config file does not exist yet, do you want to create it?", default=True, abort=True)
        AppConfig.get()
    launch(str(AppConfig.location))


@app.command()
def unpack(student_file: Path):
    config = AppConfig.get()
    add_grading_page(student_file, config.name, config.email, student_file.with_name(f"new_{student_file.name}"))


if __name__ == "__main__":
    app()
