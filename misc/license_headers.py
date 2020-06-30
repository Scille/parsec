#!/usr/bin/env python
# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS


import argparse
import pathlib
import re
import sys

HEADER = "# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS\n\n"
HEADER_RE = re.compile(
    r"^# Parsec Cloud \(https://parsec\.cloud\) Copyright \(c\) AGPLv3 2019 Scille SAS$"
)
SKIP_PATHES = (
    pathlib.Path("parsec/core/gui/_resources_rc.py"),
    pathlib.Path("parsec/core/gui/ui/"),
)


def need_skip(path):
    for skip_path in SKIP_PATHES:
        try:
            path.relative_to(skip_path)
            return True

        except ValueError:
            pass
    return False


def get_files(scans=("parsec", "tests")):
    for scan in scans:
        path = pathlib.Path(scan)
        if path.is_dir():
            for f in pathlib.Path(path).glob("**/*.py"):
                if need_skip(f):
                    continue
                yield f
        elif path.is_file():
            yield path


def extract_shabang_and_header_lines(fd):
    first_line = fd.readline()
    if first_line.startswith("#!"):
        shabang_line = first_line
        header_line = fd.readline()
    else:
        shabang_line = ""
        header_line = first_line
    return shabang_line, header_line


def check_headers(files):
    ret = 0
    for file in get_files(files):
        with open(file, "r") as fd:
            shabang_line, header_line = extract_shabang_and_header_lines(fd)
            if not HEADER_RE.match(header_line.strip()):
                print("Missing header", file)
                ret = 1
            for line, line_txt in enumerate(fd.read().split("\n"), 3 if shabang_line else 2):
                if HEADER_RE.match(line_txt.strip()):
                    print("Header wrongly present at line", line, file)
                    ret = 1
    return ret


def add_headers(files):
    for file in get_files(files):
        with open(file, "r") as fd:
            shabang_line, header_line = extract_shabang_and_header_lines(fd)
            if HEADER_RE.match(header_line.strip()):
                continue
            print("Add missing header", file)
            updated_data = f"{shabang_line}{HEADER}{header_line}{fd.read()}".strip() + "\n"
            file.write_text(updated_data)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["check", "add"])
    parser.add_argument("files", nargs="*")

    args = parser.parse_args()
    if args.cmd == "check":
        sys.exit(check_headers(args.files))
    else:
        sys.exit(add_headers(args.files))
