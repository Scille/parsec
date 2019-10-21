#!/usr/bin/env python
# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import sys
import argparse
import pathlib
from datetime import date
from collections import namedtuple, defaultdict
import subprocess
import re
import textwrap


PROJECT_DIR = pathlib.Path(__file__).resolve().parent.parent
HISTORY_FILE = PROJECT_DIR / "HISTORY.rst"
VERSION_FILE = PROJECT_DIR / "parsec/_version.py"
FRAGMENTS_DIR = PROJECT_DIR / "newsfragments"
FRAGMENT_TYPES = {
    "feature": "Features",
    "bugfix": "Bugfixes",
    "doc": "Improved Documentation",
    "removal": "Deprecations and Removals",
    "misc": "Miscellaneous internal changes",
}


class ReleaseError(Exception):
    pass


Version = namedtuple("Version", "full,short,major,minor,patch,extra")


def parse_version(raw):
    raw = raw.strip()
    if not raw.startswith("v"):
        raw = f"v{raw}"
    match = re.match(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)(([\-+]\w+)*)$", raw)
    if match:
        major, minor, patch, extra, *_ = match.groups()
        return Version(raw, f"{major}.{minor}.{patch}", major, minor, patch, extra)
    else:
        raise ValueError(
            "Invalid version format, should be `[v]<major>.<minor>.<path>[-<extra>]` (e.g. `v1.0.0`, `1.2.3-dev`)"
        )


def run_git(cmd, verbose=False):
    cmd = f"git {cmd}"
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Error while running `{cmd}`: returned {proc.returncode}\n"
            f"stdout:\n{proc.stdout.decode()}\n"
            f"stdout:\n{proc.stdout.decode()}\n"
        )
    stderr = proc.stderr.decode()
    if verbose and stderr:
        print(f"[Stderr stream from `{cmd}`]\n{stderr}[End stderr stream]", file=sys.stderr)
    return proc.stdout.decode()


def get_version_from_repo_describe_tag(verbose=False):
    return parse_version(run_git("describe --tag --debug", verbose=verbose))


def get_version_from_code():
    global_dict = {}
    exec((VERSION_FILE).read_text(), global_dict)
    __version__ = global_dict.get("__version__")
    if not __version__:
        raise ReleaseError(f"Cannot find __version__ in {VERSION_FILE!s}")
    return parse_version(__version__)


def replace_code_version(new_version_str):
    version_txt = (VERSION_FILE).read_text()
    updated_version_txt = re.sub(
        r'__version__\W=\W".*"', f'__version__ = "{new_version_str}"', version_txt
    )
    (VERSION_FILE).write_text(updated_version_txt)


def collect_newsfragments():
    fragments = []
    fragment_regex = r"^[0-9]+\.(" + "|".join(FRAGMENT_TYPES.keys()) + r")\.rst$"
    for entry in FRAGMENTS_DIR.iterdir():
        if entry.name in (".gitkeep", "README.rst"):
            continue
        # Sanity check
        if not re.match(fragment_regex, entry.name) or not entry.is_file():
            raise ReleaseError(f"Invalid entry detected in newsfragments dir: `{entry.name}`")
        fragments.append(entry)

    return fragments


def build_release(version):
    if version.extra:
        raise ReleaseError(f"Invalid release version: `{version.full}`")
    print(f"Build release {version.short}")
    old_version = get_version_from_code()
    if version.short < old_version.short:
        raise ReleaseError(
            f"Previous version incompatible with new one ({old_version.short} vs {version.short})"
        )

    # Check repo is clean
    stdout = run_git("status --porcelain --untracked-files=no")
    if stdout.strip():
        raise ReleaseError("Repository is not clean, aborting")

    # Update __version__
    replace_code_version(version.short)

    # Update HISTORY.rst
    history_txt = HISTORY_FILE.read_text()
    header_split = ".. towncrier release notes start\n"
    if header_split in history_txt:
        header, history_body = history_txt.split(header_split, 1)
        history_header = header + header_split
    else:
        history_header = ""
        history_body = history_txt

    newsfragments = collect_newsfragments()
    new_entry_title = f"Parsec {version.short} ({date.today().isoformat()})"
    new_entry = f"\n\n{new_entry_title}\n{len(new_entry_title) * '-'}\n"
    issues_per_type = defaultdict(list)
    for fragment in newsfragments:
        issue_id, type, _ = fragment.name.split(".")
        issue_txt = f"{fragment.read_text()} (`#{issue_id} <https://github.com/Scille/parsec-cloud/issues/{issue_id}>`__)\n"
        wrapped_issue_txt = textwrap.fill(
            issue_txt, width=80, break_long_words=False, initial_indent="* ", subsequent_indent="  "
        )
        issues_per_type[type].append(wrapped_issue_txt)

    if not issues_per_type:
        new_entry += "\nNo significant changes.\n"
    else:
        for fragment_type, fragment_title in FRAGMENT_TYPES.items():
            if fragment_type not in issues_per_type:
                continue
            new_entry += f"\n{fragment_title}\n{len(fragment_title) * '~'}\n\n"
            new_entry += "\n".join(issues_per_type[fragment_type])
            new_entry += "\n"

    updated_history_txt = f"{history_header}{new_entry}{history_body}".strip() + "\n"
    HISTORY_FILE.write_text(updated_history_txt)

    # Make git commit
    commit_msg = f"Bump version {old_version.full} -> {version.full}"
    print(f"Create commit `{commit_msg}`")
    run_git(f"add {HISTORY_FILE.absolute()} {VERSION_FILE.absolute()}")
    fragments_pathes = [str(x.absolute()) for x in newsfragments]
    run_git(f"rm {' '.join(fragments_pathes)}")
    # Disable pre-commit hooks given this commit wouldn't pass `releaser check`
    run_git(f"commit -m '{commit_msg}' --no-verify")
    print(f"Create tag {version.full}")
    run_git(f"tag {version.full}")

    # Update __version__ with dev suffix
    commit_msg = f"Bump version {version.full} -> {version.full}+dev"
    print(f"Create commit `{commit_msg}`")
    replace_code_version(version.short + "+dev")
    run_git(f"add {VERSION_FILE.absolute()}")
    # Disable pre-commit hooks given this commit wouldn't pass `releaser check`
    run_git(f"commit -m '{commit_msg}' --no-verify")


def check_release(version):
    print(f"Checking release {version.full}")

    # Check __version__
    code_version = get_version_from_code()
    if code_version.full != version.full:
        raise ReleaseError(
            f"Invalid __version__ in parsec/_version.py: expected `{version.full}`, got `{code_version.full}`"
        )

    # Check newsfragments
    fragments = collect_newsfragments()
    if fragments:
        fragments_names = [fragment.name for fragment in fragments]
        raise ReleaseError(
            f"newsfragments still contains fragments files ({', '.join(fragments_names)})"
        )


def check_non_release(version):
    print(f"Checking non-release {version.full}")
    # Check __version__
    expected_version = f"v{version.short}+dev"
    code_version = get_version_from_code()
    if code_version.full != expected_version:
        raise ReleaseError(
            f"Invalid __version__ in parsec/_version.py: expected `{expected_version}`, got `{code_version.full}`"
        )

    # Force newsfragments format sanity check
    collect_newsfragments()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Handle release & related checks")
    parser.add_argument("command", choices=("build", "check"))
    parser.add_argument("version", type=parse_version, nargs="?")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "build":
            if not args.version:
                raise SystemExit("version is required build command")
            current_version = get_version_from_repo_describe_tag(args.verbose)
            check_non_release(current_version)
            build_release(args.version)

        else:

            if args.version is None:
                version = get_version_from_repo_describe_tag(args.verbose)
            else:
                version = args.version

            if version.extra:
                check_non_release(version)

            else:
                check_release(version)

    except ReleaseError as exc:
        raise SystemExit(str(exc)) from exc
