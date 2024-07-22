"""Autograder scripts."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Self
from zipfile import ZipFile

from autograder.core import add_grading_page
from pydantic import BaseModel, EmailStr, TypeAdapter
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
    "attention": "magenta2",
    "heading": "blue",
    "info": "dim cyan",
})
console = Console(theme=theme)
app = Typer(pretty_exceptions_show_locals=True)


class AppConfig(BaseModel):
    name: str
    email: EmailStr
    id_pattern: str = r"(?:[tT]ut(?:orium)? (?P<tutorial>\d+))?.*[gG]ruppe (?P<group>\d+)"

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


@app.command()
def config():
    if not AppConfig.location.is_file():
        res = Confirm.ask(
            "[attention]The config file does not exist yet, do you want to create it?", default=True, console=console
        )
        if not res:
            raise Abort
        AppConfig.get()
    launch(str(AppConfig.location))


class StudentInfo(BaseModel):
    points: float | None
    original_name: str
    pdf_location: Path | None = None


StudentData = TypeAdapter(dict[str, StudentInfo])


@dataclass
class MatchInfo:
    group: str
    tutorial: str | None

    @classmethod
    def from_match(cls, match: re.Match[str]) -> Self:
        if len(match.groups()) == 1:
            return cls(match.group(0), None)
        groups = match.groupdict()
        return cls(groups["group"], groups.get("tutorial"))

    def get_id(self, all_infos: Iterable[Self]) -> str:
        out = f"gruppe {self.group}"
        if self.tutorial is not None and any(i != self and i.group == self.group for i in all_infos):
            out += f" tut {self.tutorial}"
        return out


@app.command()
def unpack(
    student_file: Annotated[
        Path, Argument(help="the zip file containing the student's submissions, as downloaded from moodle")
    ],
    output: Annotated[
        Path,
        Option("--out", "-o", help="the output folder", file_okay=False, writable=True),
    ] = Path() / "assignments",
):
    config = AppConfig.get()
    if not output.exists():
        output.mkdir(parents=True)
    elif output.is_file():
        raise Abort("The chosen output folder already exists and is a file")
    elif next(output.iterdir(), None):
        res = Confirm.ask(
            f"[warning]The chosen output folder ({output}) already exists![/]\nDo you want to replace it?",
            default=False,
            console=console,
        )
        if not res:
            raise Abort
        rmtree(output)
        output.mkdir(exist_ok=True)
    regex = re.compile(config.id_pattern)

    with ZipFile(student_file) as file:
        file.extractall(output)

    infos: dict[str, MatchInfo] = {}
    for path in output.iterdir():
        while not (match := regex.search(path.name)):
            new_pattern = Prompt.ask(
                f"[error]Could not find a student identifier in '{path.name}'[/], do you want to update the identifier "
                "pattern?\nIt must be a regex string with either a single capture group uniquely identifying the "
                "submission or two named groups 'group' and (optionally) 'tutorial'.",
                default=config.id_pattern,
                console=console,
            )
            config.id_pattern = new_pattern
            config.save()
            regex = re.compile(new_pattern)
        infos[path.name] = MatchInfo.from_match(match)

    student_data = dict[str, StudentInfo]()
    for path in track(list(output.iterdir()), description="Formatting student files"):
        identifier = infos[path.name].get_id(infos.values())
        student_data[identifier] = StudentInfo(original_name=path.name, points=None)

        if path.suffix == ".zip":
            with ZipFile(path) as unzipped_path:
                if len(unzipped_path.filelist) == 1:
                    inner_file = unzipped_path.filelist[0]
                    path = path.with_suffix(Path(inner_file.orig_filename).suffix)
                    unzipped_path.extract(inner_file, path)
                else:
                    path = path.with_suffix("")
                    unzipped_path.extractall(path)

        new_path = path.with_name(identifier).with_suffix(path.suffix)
        if path.is_file() and path.suffix == ".pdf":
            add_grading_page(path, config.name, config.email, new_path)
            if new_path.name != path.name:
                path.unlink()
        elif path.is_dir():
            path.rename(new_path)
            for file in new_path.iterdir():
                if file.is_file() and file.suffix == ".pdf":
                    add_grading_page(file, config.name, config.email)
                    student_data[identifier].pdf_location = file.relative_to(path)
                    break

    output.joinpath("student_data.json").write_bytes(
        StudentData.dump_json(student_data, indent=2, exclude_defaults=True)
    )


if __name__ == "__main__":
    app()
