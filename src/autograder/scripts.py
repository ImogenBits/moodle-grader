"""Autograder scripts."""

from __future__ import annotations

import re
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Annotated, ClassVar, NewType, Optional, Self  # pyright: ignore[reportDeprecated]
from zipfile import ZipFile

from pydantic import BaseModel, EmailStr, Field, ValidationError
from rich.console import Console
from rich.progress import track
from rich.prompt import Confirm, Prompt
from rich.theme import Theme
from typer import Abort, Argument, Option, Typer, get_app_dir, launch

from autograder.core import add_grading_page, get_points, set_points

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


Group = NewType("Group", int)
Tut = NewType("Tut", int)


class GroupInfo(BaseModel):
    tutorial: Tut | None = None
    group: Group | None = None
    points: float | None = None
    pdf_location: Path
    feedback_location: Path


class StudentData(BaseModel):
    identifier_column: str
    data: dict[str, GroupInfo] = Field(default_factory=dict)

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2, exclude_defaults=True), encoding="utf-8")


@dataclass
class MoodleFileData:
    name: str
    identifier: str
    moodle_type: str
    moodle_file: str
    file_name: str
    suffix: str

    group_pattern: ClassVar[re.Pattern[str]] = re.compile(r"[gG]ruppe (\d+)")
    tut_pattern: ClassVar[re.Pattern[str]] = re.compile(r"[tT]ut(?:orium)? (\d+)")

    @classmethod
    def from_path(cls, path: Path) -> Self:
        name, identifier, moodle_type, moodle_file, *file_name = path.stem.split("_")
        return cls(
            name=name,
            identifier=identifier,
            moodle_type=moodle_type,
            moodle_file=moodle_file,
            file_name="_".join(file_name),
            suffix=path.suffix,
        )

    @property
    def feedback_path(self) -> Path:
        return Path(
            f"{self.name}_{self.identifier}_{self.moodle_type}_{self.moodle_file}_{self.file_name}_Feedback.pdf"
        )

    @cached_property
    def group(self) -> Group | None:
        group_match = self.group_pattern.search(self.name)
        return Group(int(group_match.group(1))) if group_match else None

    @cached_property
    def tutorial(self) -> Tut | None:
        tut_match = self.tut_pattern.search(self.name)
        return Tut(int(tut_match.group(1))) if tut_match else None

    def short_id(self, every_group: Iterable[Self | GroupInfo]) -> str:
        if not self.group:
            return self.identifier
        out = f"gruppe {self.group}"
        if self.tutorial and any(o.group == self.group and o.tutorial != self.tutorial for o in every_group):
            out += f" tut {self.tutorial}"
        return out


def find_pdf(path: Path) -> Path | None:
    if path.is_file() and path.suffix == ".pdf":
        return path
    elif path.is_dir() and not path.name.startswith(".") and path.name != "__MACOSX":
        for child in path.iterdir():
            if found := find_pdf(child):
                return found


@app.command(help="Unpacks a zip file containing the student's submissions.")
def unpack(
    student_file: Annotated[
        Path, Argument(help="the file containing the student's submissions", exists=True, dir_okay=False)
    ],
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
            exists=True,
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
    student_file.unlink()

    all_file_data = {path: MoodleFileData.from_path(path) for path in output.iterdir()}
    assignment_data = StudentData(identifier_column=config.default_identifier_column)
    for path, file_data in track(all_file_data.items(), description="Formatting student files"):
        if path.suffix == ".zip":
            with ZipFile(path) as unzipped_path:
                if len(unzipped_path.filelist) == 1:
                    inner_file = unzipped_path.filelist[0]
                    new_path = path.with_suffix(Path(inner_file.orig_filename).suffix)
                    unzipped_path.extract(inner_file, new_path)
                else:
                    new_path = path.with_suffix("")
                    unzipped_path.extractall(new_path)
            path.unlink()
            path = new_path

        short_id = file_data.short_id(all_file_data.values())
        new_path = path.with_name(short_id).with_suffix(path.suffix)
        path.rename(new_path)

        pdf_path = find_pdf(new_path)
        if not pdf_path:
            continue
        if pdf_path != new_path:
            pdf_path.rename(pdf_path := new_path.with_suffix(".pdf"))

        assignment_data.data[file_data.name] = GroupInfo(
            tutorial=file_data.tutorial,
            group=file_data.group,
            points=None,
            pdf_location=pdf_path.relative_to(output),
            feedback_location=file_data.feedback_path,
        )
        add_grading_page(pdf_path, config.name, config.email, insert_image)

    assignment_data.save(output / "assignment_data.json")


@app.command(name="add", help="Add an individual submission to an existing assignment folder.")
def add_pdf(
    file: Annotated[
        Path, Argument(help="the PDF file containing the student's submission", exists=True, dir_okay=False)
    ],
    data_file: Annotated[
        Path,
        Option(
            "--assignment",
            "-a",
            help="the data file for an existing assignment",
            exists=True,
            dir_okay=False,
            writable=True,
        ),
    ] = Path() / "assignments" / "assignment_data.json",
    insert_image: Annotated[
        Optional[Path],  # noqa: UP007 # pyright: ignore[reportDeprecated]
        Option(
            "--insert-image",
            "-i",
            help="an image that will be included in the front page",
            exists=True,
            file_okay=True,
            dir_okay=False,
        ),
    ] = None,
):
    if file.suffix != ".pdf":
        raise Abort("[error]The added input file is not a PDF.")
    config = AppConfig.get()
    assignment_data = StudentData.model_validate_json(data_file.read_text())
    file_data = MoodleFileData.from_path(file)

    short_id = file_data.short_id([file_data, *assignment_data.data.values()])
    new_path = data_file.parent.joinpath(short_id).with_suffix(".pdf")
    file.rename(new_path)

    add_grading_page(new_path, config.name, config.email, insert_image)
    assignment_data.data[file_data.name] = GroupInfo(
        tutorial=file_data.tutorial,
        group=file_data.group,
        points=None,
        pdf_location=new_path.relative_to(data_file.parent),
        feedback_location=file_data.feedback_path,
    )
    assignment_data.save(data_file)


@app.command()
def finalize(
    data_file: Annotated[
        Path, Option(help="Path to the `student_data.json` file.", exists=True, dir_okay=False)
    ] = Path() / "assignments" / "assignment_data.json",
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


class PointsInfo(BaseModel):
    points: float | None = None


class InteractiveData(BaseModel):
    identifier_column: str
    data: dict[str, PointsInfo] = Field(default_factory=dict)

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2, exclude_defaults=True))


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
    data = None
    if output.is_file():
        try:
            data = InteractiveData.model_validate_json(output.read_text())
        except ValidationError as e:
            delete = Confirm.ask(
                f"[error]There already exists a file at '{output}' that doesn't contain grading info.[/]\n"
                "Do you want to delete that file?",
                default=True,
            )
            if delete:
                output.unlink()
            else:
                raise Abort from e
        keep = Prompt.ask(
            f"[info]There already exists grading data at '{output}'.[/]\n"
            "Do you want to keep that data or delete it and start fresh?",
            choices=["keep", "delete"],
            default="keep",
        )
        if keep == "delete":
            data = None
    if data is None:
        column = Prompt.ask("What type of identifier do you want to use?", default=config.default_identifier_column)
        data = InteractiveData(identifier_column=column)

    with suppress(SystemExit):
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
            data.data[identifier] = PointsInfo(points=points)
    console.print(f"[success]Finished grading process.[/] Writing data to '{output}'.")
    data.save(output)


if __name__ == "__main__":
    app()
