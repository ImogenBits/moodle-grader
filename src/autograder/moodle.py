import json
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Self, TypedDict

import requests


class _AppConfig(Protocol):
    moodle_token: str


class _CourseConfig(Protocol):
    moodle_url: str
    course_id: str
    tutorials: list[str]


type ParamAtom = int | str | float | bool
type ParamDataInner = dict[str, ParamDataInner] | list[ParamDataInner] | ParamAtom
type ParamData = dict[str, ParamDataInner]
type ParamEncoded = dict[str, ParamAtom]


def _encode_inner(data: ParamDataInner) -> Iterator[tuple[Sequence[str], ParamAtom]]:
    match data:
        case dict():
            for key, elem in data.items():
                for names, encoded in _encode_inner(elem):
                    yield (key, *names), encoded
        case list():
            for i, elem in enumerate(data):
                for names, encoded in _encode_inner(elem):
                    yield (str(i), *names), encoded
        case _:
            yield (), data


def encode_params(data: ParamData) -> ParamEncoded:
    return {names[0] + "".join(f"[{name}]" for name in names[1:]): elem for names, elem in _encode_inner(data)}


class Group(TypedDict):
    id: str
    name: str
    groupimageurl: str


@dataclass
class MoodleConnection:
    url: str
    token: str
    course: str

    @classmethod
    def from_configs(cls, app: _AppConfig, course: _CourseConfig) -> Self:
        return cls(url=course.moodle_url, token=app.moodle_token, course=course.course_id)

    def send(self, function: str, **params: ParamDataInner) -> dict[str, Any]:
        res = requests.post(
            f"{self.url}/webservice/rest/server.php?wstoken={self.token}&moodlewsrestformat=json&wsfunction={function}",
            params=encode_params(params),
        )
        return json.loads(res.content)

    def upload_files(self, paths: Iterable[Path]) -> list[dict[str, Any]]:
        itemid: int | None = None
        response: list[dict[str, Any]] = []
        for path in paths:
            file_data = path.read_bytes()
            res = requests.post(
                f"{self.url}/webservice/upload.php",
                files={path.name: file_data},
                params={"token": self.token} | ({"itemid": itemid} if itemid else {}),
            )
            res = json.loads(res.content)
            response.extend(res)
            itemid = itemid or res[0]["itemid"]
        return response

    def get_assignments(self) -> list[dict[str, Any]]:
        data = self.send("mod_assign_get_assignments", courseids=[self.course])
        return data["courses"][0]["assignments"]

    def get_assignment(self, assignment_name: str) -> dict[str, Any]:
        for assignment in self.get_assignments():
            if assignment["name"].find(assignment_name) >= 0:
                return assignment
        raise ValueError

    def get_assignment_submissions(self, assignment_id: int) -> list[dict[str, Any]]:
        data = self.send("mod_assign_get_submissions", assignmentids=[assignment_id])
        return data["assignments"][0]["submissions"]

    def get_groups(self, tutorials: list[str]) -> dict[int, str]:
        pattern = re.compile(fr"tut(orium|orial)? ({'|'.join(tutorials)})", flags=re.IGNORECASE)
        data = self.send("core_group_get_groups_for_selector", courseid=self.course)["groups"]
        parsed: dict[int, str] = {}
        for group in data:
            if pattern.match(group["name"]):
                parsed[int(group["id"])] = group["name"]
        return parsed


def get_submission_files(
    app: _AppConfig,
    course: _CourseConfig,
    assignment_name: str,
) -> dict[str, list[str]]:
    moodle = MoodleConnection.from_configs(app, course)
    groups = moodle.get_groups(course.tutorials)
    assignment_id: int = moodle.get_assignment(assignment_name)["id"]
    submissions = moodle.get_assignment_submissions(assignment_id)
    files: dict[str, list[str]] = {}
    for submission in submissions:
        if submission["groupid"] in groups and submission["status"] == "submitted":
            try:
                file_areas = next(p for p in submission["plugins"] if p["name"] == "File submissions")["fileareas"]
                submission_files = next(a for a in file_areas if a["area"] == "submission_files")["files"]
            except StopIteration, KeyError:
                continue
            file_urls = [file["fileurl"] for file in submission_files]
            files[groups[submission["groupid"]]] = file_urls
    return files


























"""
    def submit_files(self, assignment_id: int, files: Iterable[Path]) -> None:
        file_info = self.upload_files(files)
        assert len({info["itemid"] for info in file_info}) == 1
        itemid = file_info[0]["itemid"]
        res = self.send(
            "mod_assign_save_submission",
            {
                "assignmentid": assignment_id,
                "plugindata": {
                    "files_filemanager": itemid,
                    "onlinetext_editor": {
                        "text": "test",
                        "format": 1,
                        "itemid": itemid,
                    },
                },
            },
        )
        assert not res

    def test(self) -> None:
        assignment = self.get_assignment(2)
        self.submit_files(assignment["id"], [Path("blep.txt"), Path("bloop.txt")])

        print("yay")
"""
