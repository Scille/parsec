#!/usr/bin/env python
# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS


import sys
import re
import argparse
from pathlib import Path
from itertools import chain
from typing import Iterable, Iterator


THIS_YEAR = "2021"  # Change me when new year comes ;-)
PROJECT_DIR = Path(__file__).resolve().parent.parent


def extract_shabang_and_header_lines(fd):
    first_line = fd.readline()
    if first_line.startswith("#!"):
        shabang_line = first_line
        header_line = fd.readline()
    else:
        shabang_line = ""
        header_line = first_line
    return shabang_line, header_line


class Licenser:
    NAME: str
    HEADER: str
    HEADER_RE: re.Pattern

    @classmethod
    def check_header(cls, file: Path) -> bool:
        with open(file, "r", encoding="utf-8") as fd:
            shabang_line, header_line = extract_shabang_and_header_lines(fd)
            match = cls.HEADER_RE.match(header_line.strip())
            if not match:
                print(f"Missing {cls.NAME} header", file)
                return False

            elif match["year"] != THIS_YEAR:
                print(
                    f"Wrong year in the header (got {match['year']}, should be {THIS_YEAR})", file
                )
                return False

            for line, line_txt in enumerate(fd.read().split("\n"), 3 if shabang_line else 2):
                if cls.HEADER_RE.match(line_txt.strip()):
                    print("Header wrongly present at line", line, file)
                    return False

        return True

    @classmethod
    def add_header(cls, file: Path) -> bool:
        with open(file, "r", encoding="utf-8") as fd:
            shabang_line, header_line = extract_shabang_and_header_lines(fd)
            match = cls.HEADER_RE.match(header_line.strip())
            if not match:
                print(f"Add missing {cls.NAME} header", file)
                updated_data = f"{shabang_line}{cls.HEADER}\n\n{header_line}{fd.read()}"
                file.write_text(updated_data)
                return True

            elif match["year"] != THIS_YEAR:
                print("Correct copyright year", file)
                updated_data = f"{shabang_line}{cls.HEADER}\n{fd.read()}"
                file.write_text(updated_data)
                return True

        return False


class PythonAGPLLicenser(Licenser):
    NAME = "AGPLv3"
    HEADER = (
        f"# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-{THIS_YEAR} Scille SAS"
    )
    HEADER_RE = re.compile(
        r"^# Parsec Cloud \(https://parsec\.cloud\) Copyright \(c\) AGPLv3 2016-(?P<year>[0-9]{4}) Scille SAS$"
    )


class PythonBSLLicenser(Licenser):
    NAME = "BSL"
    HEADER = f"# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-{THIS_YEAR} Scille SAS"
    HEADER_RE = re.compile(
        r"^# Parsec Cloud \(https://parsec\.cloud\) Copyright \(c\) BSLv1.1 \(eventually AGPLv3\) 2016-(?P<year>[0-9]{4}) Scille SAS$"
    )


class SqlBSLLicenser(Licenser):
    NAME = "BSL"
    HEADER = f"-- Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-{THIS_YEAR} Scille SAS"
    HEADER_RE = re.compile(
        r"^-- Parsec Cloud \(https://parsec\.cloud\) Copyright \(c\) BSLv1.1 \(eventually AGPLv3\) 2016-(?P<year>[0-9]{4}) Scille SAS$"
    )


class SkipLicenser(Licenser):
    @classmethod
    def check_header(cls, file: Path) -> bool:
        return True

    @classmethod
    def add_header(cls, file: Path) -> bool:
        return False


LICENSERS_MAP = {
    # First match is used
    re.compile(r"^parsec/backend/.*\.sql$"): SqlBSLLicenser,
    re.compile(r"^parsec/backend/.*\.py"): PythonBSLLicenser,
    re.compile(r"^parsec/core/gui/_resources_rc.py$"): SkipLicenser,
    re.compile(r"^parsec/core/gui/ui/"): SkipLicenser,
    re.compile(r"^.*\.py"): PythonAGPLLicenser,
}


def get_files(pathes: Iterable[Path]) -> Iterator[Path]:
    for path in pathes:
        if path.is_dir():
            for f in chain(Path(path).glob("**/*.py"), Path(path).glob("**/*.sql")):
                yield f
        elif path.is_file():
            yield path
        elif not path.exists():
            raise SystemExit(f"Error: Path `{path}` doesn't exist !")


def get_licenser(path: Path) -> Licenser:
    for regex, licenser in LICENSERS_MAP.items():
        if regex.match(str(path.absolute().relative_to(PROJECT_DIR))):
            return licenser


def check_headers(files: Iterable[Path]) -> int:
    ret = 0
    for file in get_files(files):
        if not get_licenser(file).check_header(file):
            ret = 1
    return ret


def add_headers(files: Iterable[Path]) -> int:
    for file in get_files(files):
        get_licenser(file).add_header(file)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["check", "add"])
    parser.add_argument("files", nargs="*", type=Path, default=[Path("parsec"), Path("tests")])

    args = parser.parse_args()
    if args.cmd == "check":
        sys.exit(check_headers(args.files))
    else:
        sys.exit(add_headers(args.files))
