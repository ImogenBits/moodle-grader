"""Autograder scripts."""

from collections.abc import Iterable
from pathlib import Path
from random import choice
from typing import Annotated, ClassVar, Self
from urllib.parse import parse_qs, urlparse, urlunsplit
from urllib.request import urlretrieve
from zipfile import ZipFile

import tomlkit
from pydantic import BaseModel, EmailStr
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.prompt import Confirm, Prompt
from rich.theme import Theme
from typer import Abort, Argument, Option, Typer, get_app_dir, launch

from autograder.core import add_grading_page, get_points, modify_pdf
from autograder.moodle import get_submission_files

APP_NAME = "moodle_pdf_autograder"
COURSE_CONFIG_NAME = "moodle_grader.toml"
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


def track[T](sequence: Iterable[T], description: str, *, transient: bool = False) -> Iterable[T]:
    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(elapsed_when_finished=True),
        console=console,
        transient=transient,
    )

    with progress:
        yield from progress.track(
            sequence,
            description=description,
        )


class AppConfig(BaseModel):
    name: str
    email: EmailStr
    moodle_token: str

    location: ClassVar[Path] = Path(get_app_dir(APP_NAME)) / "config.json"

    @classmethod
    def get(cls) -> Self:
        path = cls.location
        if path.is_file():
            return cls.model_validate_json(path.read_text())
        else:
            raise Abort

    def save(self) -> None:
        self.location.parent.mkdir(parents=True, exist_ok=True)
        self.location.write_text(self.model_dump_json(indent=2))


class CourseConfig(BaseModel):
    moodle_url: str
    course_id: str
    tutorials: list[str]
    max_points: float

    @classmethod
    def get(cls) -> Self:
        path = Path().absolute()
        while not path.joinpath(COURSE_CONFIG_NAME).exists():
            parent = path.parent
            if path == parent:
                console.print(
                    "[error]Could not find course config file in any parent folder.[/]\n"
                    "Please run the 'init' command in the course folder you want to use."
                )
                raise Abort
        data = tomlkit.loads(path.joinpath(COURSE_CONFIG_NAME).read_text())
        return cls.model_validate(data)

    def save(self, path: Path) -> None:
        path.write_text(tomlkit.dumps(self.model_dump()))


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
        Path | None,
        Option(
            "--insert-image",
            "-i",
            help="an image that will be included in the front page",
            exists=True,
            file_okay=True,
            dir_okay=True,
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

    add_grading_page(
        new_path, config.name, config.email, short_id, data_file.parent.parent.name, select_image(insert_image)
    )
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
        Path | None,  # noqa: UP007 # pyright: ignore[reportDeprecated]
        Option("--output", "-o", help="Path to the created feedback file zip.", exists=False),
    ] = None,
    image_path: Annotated[
        Path | None,  # noqa: UP007 # pyright: ignore[reportDeprecated]
        Option(
            "--bonus-image",
            "-i",
            help="an image that will be included in the front page if at least half of the maximum number of points "
            "were scored, or path to a folder from which a random image will be selected.",
            exists=True,
        ),
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
                new_points = float(
                    Prompt.ask(
                        f"[attention]Group '{identifier}' has {info.points} points in the data file, but {pdf_points} "
                        "in the pdf.[/] Which value do you want to use?",
                        choices=[str(info.points), str(pdf_points)],
                        default=str(pdf_points),
                        console=console,
                    )
                )
                old_points = info.points if new_points == pdf_points else pdf_points or -1
            else:
                new_points, old_points = pdf_points or -1, info.points or -1
            info.points = new_points

            if old_points <= (AppConfig.get().max_points / 2) <= new_points:
                bonus_image = select_image(image_path)
            else:
                bonus_image = None
            write_points = new_points if new_points != pdf_points else None
            modify_pdf(pdf_path, write_points, bonus_image)
            feedback_zip.write(pdf_path, info.feedback_location)
    data_file.write_text(data.model_dump_json(indent=2))


class PointsInfo(BaseModel):
    points: float | None = None


@app.command()
def init():
    app_config = AppConfig.get()
    if not app_config:
        name = Prompt.ask("What name do you want to use?", console=console)
        email = Prompt.ask(
            "What email address do you want to use?",
            default=f"{'.'.join(name.lower().split())}@rwth-aachen.de",
            console=console,
        )
        moodle_token = Prompt.ask("Please enter a moodle API token.")
        app_config = AppConfig(name=name, email=email, moodle_token=moodle_token)
        app_config.save()

    full_url = urlparse(Prompt.ask("Please enter the URL to moodle page of the course you want to work with"))
    base_url = urlunsplit((full_url.scheme, full_url.netloc, "", "", ""))
    queries = parse_qs(full_url.query)
    if not full_url.path.startswith("/course") or "id" not in queries:
        console.print("[error]The URL you entered does not point to a moodle course page.")
        raise Abort
    course_id = queries["id"][0]
    max_points = float(Prompt.ask("How many points does each assignment award?").replace(",", "."))
    tutorial_string = Prompt.ask(
        "Which tutorials do you grade for?\nYou can enter any number of identifiers seperated by commas"
    )
    tutorials = [id.strip() for id in tutorial_string.split(",")]
    course_config = CourseConfig(
        moodle_url=base_url,
        course_id=course_id,
        tutorials=tutorials,
        max_points=max_points,
    )
    course_config.save(Path(COURSE_CONFIG_NAME))


def _write_file(token: str, url: str, target: Path) -> None:
    suffix = url.split(".")[-1]
    urlretrieve(f"{url}?token={token}", target.with_suffix(f".{suffix}"))


def find_pdf(path: Path) -> Path | None:
    if path.is_file() and path.suffix == ".pdf":
        return path
    elif path.is_dir() and not path.name.startswith(".") and path.name != "__MACOSX":
        for child in path.iterdir():
            if found := find_pdf(child):
                return found


def select_image(path: Path | None) -> Iterable[Path | None]:
    if path is None or path.is_file():
        while True:
            yield path
    images = list(path.iterdir())
    while True:
        yield choice(images)


@app.command()
def download(
    assignment_week: Annotated[
        int,
        Argument(help="the assignment number."),
    ],
    output: Annotated[
        Path | None,
        Option(
            "--output",
            "-o",
            help="The folder where downloaded files are placed. Defaults to './{assignment}/assignments'",
        ),
    ] = None,
    insert_image: Annotated[
        Path | None,
        Option(
            "--insert-image",
            "-i",
            help="an image that will be included in the front page, "
            "or path to a folder from which a random image will be selected.",
            exists=True,
        ),
    ] = None,
):
    assignment = f"{assignment_week:02}"
    app = AppConfig.get()
    course = CourseConfig.get()
    if output is None:
        output = Path(f"{assignment}/assignments")
    if output.exists():
        res = Confirm.ask(
            f"[attention]The chosen output folder ({output}) already exists![/]\nDo you want to replace it?",
            default=False,
            console=console,
        )
        if not res:
            raise Abort
        rmtree(output)
    output.mkdir(exist_ok=True, parents=True)

    with console.status("Getting assignment info"):
        files = get_submission_files(app, course, assignment)
    for name, urls in track(files.items(), "Downloading submissions..."):
        if len(urls) == 1:
            _write_file(app.moodle_token, urls[0], output.joinpath(name))
        else:
            group_folder = output.joinpath(name)
            group_folder.mkdir()
            for url in urls:
                file_name = url.split("/")[-1]
                _write_file(app.moodle_token, url, group_folder.joinpath(file_name))

    for path, image in zip(
        track(list(output.iterdir()), "Formatting submissions..."), select_image(insert_image), strict=False
    ):
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

        pdf_path = find_pdf(path)
        if not pdf_path:
            continue
        if pdf_path != path:
            pdf_path.rename(pdf_path := path.with_suffix(".pdf"))

        add_grading_page(
            pdf_path,
            app.name,
            app.email,
            path.stem,
            assignment,
            image,
        )


if __name__ == "__main__":
    app()
