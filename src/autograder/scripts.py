"""Autograder scripts."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Annotated, ClassVar, Optional, Self  # pyright: ignore[reportDeprecated]
from zipfile import ZipFile

from autograder.core import add_grading_page, get_points, set_points
from pydantic import BaseModel, EmailStr, Field
from rich.console import Console
from rich.progress import track
from rich.prompt import Confirm, Prompt
from rich.theme import Theme
from typer import Abort, Argument, Option, Typer, get_app_dir, launch

APP_NAME = "moodle_pdf_autograder"
theme = Theme({
    "success": "green",
    "warning": "orange3",
    "error": "red",
    "attention": "purple3",
    "heading": "blue",
    "info": "dim cyan",
})
console = Console(theme=theme)
app = Typer(pretty_exceptions_show_locals=True)


class AppConfig(BaseModel):
    name: str
    email: EmailStr
    default_identifier_column: str = "Group"

    location: ClassVar[Path] = Path(get_app_dir(APP_NAME)) / "config.json"

    @classmethod
    def get(cls) -> Self:
        path = cls.location
        if path.is_file():
            return cls.model_validate_json(path.read_text())
        name = Prompt.ask("What name do you want to use?", console=console)
        email = Prompt.ask(
            "What email address do you want to use?",
            default=f"{".".join(name.lower().split())}@rwth-aachen.de",
            console=console,
        )
        config = cls(name=name, email=email)
        config.save()
        return config

    def save(self) -> None:
        self.location.parent.mkdir(parents=True, exist_ok=True)
        self.location.write_text(self.model_dump_json(indent=2))


def rmtree(path: Path) -> None:
    if path.is_file():
        path.unlink()
    else:
        for child in path.iterdir():
            rmtree(child)
        path.rmdir()


@app.command(help="Opens the config file.")
def config():
    if not AppConfig.location.is_file():
        res = Confirm.ask(
            "[attention]The config file does not exist yet, do you want to create it?", default=True, console=console
        )
        if not res:
            raise Abort
        AppConfig.get()
    launch(str(AppConfig.location))


class BaseStudentInfo(BaseModel):
    points: float | None


class StudentInfo(BaseStudentInfo):
    pdf_location: Path
    feedback_location: Path


class _BaseData(BaseModel):
    identifier_column: str

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2))


class BaseStudentData(_BaseData):
    data: dict[str, BaseStudentInfo] = Field(default_factory=dict)


class StudentData(_BaseData):
    data: dict[str, StudentInfo] = Field(default_factory=dict)


@dataclass(frozen=True)
class MoodleFileData:
    name: str
    identifier: str
    file_type: str
    file_name: str
    suffix: str

    group_pattern: ClassVar[re.Pattern[str]] = re.compile(r"[gG]ruppe (\d+)")
    tut_pattern: ClassVar[re.Pattern[str]] = re.compile(r"[tT]ut(?:orium)? (\d+)")

    @classmethod
    def from_path(cls, path: Path) -> Self:
        name, identifier, *rest, file_name = path.stem.split("_")
        return cls(
            name=name,
            identifier=identifier,
            file_type="_".join(rest),
            file_name=file_name,
            suffix=path.suffix,
        )

    @property
    def feedback_path(self) -> Path:
        return Path(f"{self.name}_{self.identifier}_{self.file_type}_{self.file_name}_Feedback.pdf")

    @cached_property
    def group_num(self) -> str | None:
        group_match = self.group_pattern.search(self.name)
        return group_match.group(1) if group_match else None

    @cached_property
    def tut_num(self) -> str | None:
        tut_match = self.tut_pattern.search(self.name)
        return tut_match.group(1) if tut_match else None

    def short_id(self, others: Iterable[Self]) -> str:
        if not self.group_num:
            return self.identifier
        out = f"gruppe {self.group_num}"
        if self.tut_num and any(o.group_num == self.group_num and o.tut_num != self.tut_num for o in others):
            out += f" tut {self.tut_num}"
        return out


@app.command(help="Unpacks a zip file containing the student's submissions.")
def unpack(
    student_file: Annotated[Path, Argument(help="the file containing the student's submissions")],
    output: Annotated[
        Path,
        Option("--out", "-o", help="the output folder", file_okay=False, writable=True),
    ] = Path() / "assignments",
    insert_image: Annotated[
        Optional[Path],  # noqa: UP007 # pyright: ignore[reportDeprecated]
        Option(
            "--insert-image",
            "-i",
            help="an image that will be included in the front page",
            file_okay=True,
            dir_okay=False,
        ),
    ] = None,
):
    config = AppConfig.get()
    if not output.exists():
        output.mkdir(parents=True)
    elif output.is_file():
        raise Abort("The chosen output folder already exists and is a file")
    elif next(output.iterdir(), None):
        res = Confirm.ask(
            f"[attention]The chosen output folder ({output}) already exists![/]\nDo you want to replace it?",
            default=False,
            console=console,
        )
        if not res:
            raise Abort
        rmtree(output)
        output.mkdir(exist_ok=True)

    with ZipFile(student_file) as file:
        file.extractall(output)

    all_file_data = {path: MoodleFileData.from_path(path) for path in output.iterdir()}
    student_data = StudentData(identifier_column=config.default_identifier_column)
    for path, file_data in track(all_file_data.items(), description="Formatting student files"):
        if path.suffix == ".zip":
            with ZipFile(path) as unzipped_path:
                if len(unzipped_path.filelist) == 1:
                    inner_file = unzipped_path.filelist[0]
                    path = path.with_suffix(Path(inner_file.orig_filename).suffix)
                    unzipped_path.extract(inner_file, path)
                else:
                    path = path.with_suffix("")
                    unzipped_path.extractall(path)

        short_id = file_data.short_id(all_file_data.values())
        new_path = path.with_name(short_id).with_suffix(path.suffix)
        path.rename(new_path)
        if new_path.is_file() and path.suffix == ".pdf":
            pdf_path = new_path
        elif new_path.is_dir():
            for file in new_path.iterdir():
                if file.is_file() and file.suffix == ".pdf":
                    pdf_path = new_path / file.name
                    break
            else:
                continue
        else:
            continue
        student_data.data[file_data.name] = StudentInfo(
            points=None, pdf_location=pdf_path.relative_to(output), feedback_location=file_data.feedback_path
        )
        add_grading_page(pdf_path, config.name, config.email, insert_image)

    student_data.save(output / "student_data.json")


@app.command()
def finalize(
    data_file: Annotated[
        Path, Option(help="Path to the `student_data.json` file.", exists=True, dir_okay=False)
    ] = Path("assignments/student_data.json"),
    output: Annotated[
        Optional[Path],  # noqa: UP007 # pyright: ignore[reportDeprecated]
        Option("--output", "-o", help="Path to the created feedback file zip.", exists=False),
    ] = None,
):
    data = StudentData.model_validate_json(data_file.read_text())
    output = output or data_file.with_name("feedback_files.zip")
    if output.exists():
        res = Confirm.ask(
            f"[attention]There already is a file at '{output}'[/], do you want to replace it?",
            console=console,
            default=False,
        )
        if not res:
            raise Abort
        rmtree(output)
    with ZipFile(output, "x") as feedback_zip:
        for identifier, info in data.data.items():
            pdf_path = data_file.parent / info.pdf_location
            pdf_points = get_points(pdf_path)
            if info.points is not None and info.points != pdf_points:
                info.points = float(
                    Prompt.ask(
                        f"[attention]Group '{identifier}' has {info.points} points in the data file, but {pdf_points} in the pdf.[/] "
                        "Which value do you want to use?",
                        choices=[str(info.points), str(pdf_points)],
                        default=str(pdf_points),
                        console=console,
                    )
                )
                if pdf_points != info.points:
                    set_points(pdf_path, info.points)
            else:
                info.points = pdf_points
            feedback_zip.write(pdf_path, info.feedback_location)
    data_file.write_text(data.model_dump_json(indent=2))


@app.command(help="Interactively create a grading file to use with the moodle plugin.")
def interactive(
    output: Annotated[
        Path,
        Option(
            "--output",
            "-o",
            help="Path to the created feedback file zip.",
            exists=False,
        ),
    ] = Path("grading_data.json"),
):
    config = AppConfig.get()
    column = Prompt.ask("What type of identifier do you want to use?", default=config.default_identifier_column)
    data = BaseStudentData(identifier_column=column)

    while True:
        identifier = Prompt.ask("Enter an identifier (or an empty string to finish grading)")
        if not identifier:
            break
        while True:
            points = Prompt.ask("Enter the number of points the student achieved")
            try:
                points = float(points) if points else None
            except ValueError:
                console.print("[error]The entered value is not a valid number of points.")
            else:
                break
        data.data[identifier] = BaseStudentInfo(points=points)
    data.save(output)


if __name__ == "__main__":
    app()
