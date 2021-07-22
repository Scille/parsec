#!/usr/bin/env python3
# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS

"""
This script is used to ease the parsec release process.

The produced tag is going to be signed so make sure you have a GPG key available.
If not, it can be generated using the following command:

    $ gpg --gen-key

A typical release process looks as follow:

    # Switch to master and make sure all the latest commits are pulled
    $ git checkout master
    $ git pull master

    # Run the releaser script with the expected version
    $ ./misc/releaser.py build v1.2.3

    # Push the produced tag and commits
    $ git push --follow-tags


"""

import sys
import argparse
import pathlib
from copy import copy
from datetime import date
from collections import defaultdict
import subprocess
import re
import textwrap
import math


PROJECT_DIR = pathlib.Path(__file__).resolve().parent.parent
HISTORY_FILE = PROJECT_DIR / "HISTORY.rst"
BSL_LICENSE_FILE = PROJECT_DIR / "licenses/BSL-Scille.txt"
VERSION_FILE = PROJECT_DIR / "parsec/_version.py"
FRAGMENTS_DIR = PROJECT_DIR / "newsfragments"
FRAGMENT_TYPES = {
    "feature": "Features",
    "bugfix": "Bugfixes",
    "doc": "Improved Documentation",
    "removal": "Deprecations and Removals",
    "api": "Client/Backend API evolutions",
    "misc": "Miscellaneous internal changes",
    "empty": "Miscellaneous internal changes that shouldn't even be collected",
}


RELEASE_REGEX = (
    r"([0-9]+)\.([0-9]+)\.([0-9]+)" r"(?:-((?:a|b|rc)[0-9]+))?" r"(\+dev|(?:-[0-9]+-g[0-9a-f]+))?"
)


class ReleaseError(Exception):
    pass


class Version:
    def __init__(self, raw):
        raw = raw.strip()
        if raw.startswith("v"):
            raw = raw[1:]
        # Git describe (e.g. `-g3b5f5762`) show our position relative to and
        # existing release, hence this is equivalent to the `+dev` suffix
        match = re.match(f"^{RELEASE_REGEX}$", raw)
        if not match:
            raise ValueError(
                f"Invalid version format {raw!r}, should be `[v]<major>.<minor>.<patch>[-<post>][+dev|-<X>-g<commit>]` (e.g. `v1.0.0`, `1.2.3+dev`, `1.6.7-rc1`)"
            )

        major, minor, patch, pre, dev = match.groups()
        self.major = int(major)
        self.minor = int(minor)
        self.patch = int(patch)
        self.is_dev = bool(dev)
        if pre:
            match = re.match(r"^(a|b|rc)([0-9]+)$", pre)
            self.pre_type = match.group(1)
            self.pre_index = int(match.group(2))
        else:
            self.pre_type = self.pre_index = None

    def evolve(self, **kwargs):
        new = copy(self)
        new.__dict__.update(kwargs)
        return new

    def __str__(self):
        base = f"v{self.major}.{self.minor}.{self.patch}"
        if self.is_preversion:
            base += f"-{self.pre_type}{self.pre_index}"
        if self.is_dev:
            base += "+dev"
        return base

    @property
    def is_preversion(self):
        return self.pre_type is not None

    def __eq__(self, other):
        if type(other) == tuple:
            other = (
                "v"
                + ".".join([str(i) for i in other[:3]])
                + (f"-{str().join(other[3:])}" if len(other) > 3 else "")
            )
        return str(self) == str(other)

    def __lt__(self, other):
        k = (self.major, self.minor, self.patch)
        other_k = (other.major, other.minor, other.patch)

        def _pre_type_to_val(pre_type):
            return {"a": 1, "b": 2, "rc": 3}[pre_type]

        if k == other_k:
            # Must take into account dev and pre info
            if self.is_preversion:
                pre_type = _pre_type_to_val(self.pre_type)
                pre_index = self.pre_index
            else:
                pre_type = pre_index = math.inf

            if other.is_preversion:
                other_pre_type = _pre_type_to_val(other.pre_type)
                other_pre_index = other.pre_index
            else:
                other_pre_type = other_pre_index = math.inf

            dev = 1 if self.is_dev else 0
            other_dev = 1 if other.is_dev else 0

            return (pre_type, pre_index, dev) < (other_pre_type, other_pre_index, other_dev)

        else:
            return k < other_k

    def __le__(self, other):
        if self == other:
            return True
        else:
            return self < other


# Version is non-trivial code, so let's run some pseudo unit tests...
def _test(raw, is_dev=False, pre=(None, None)):
    v = Version(raw)
    assert v.is_dev is is_dev
    pre_type, pre_index = pre
    assert v.is_preversion is (pre_type is not None)
    assert v.pre_type == pre_type
    assert v.pre_index == pre_index


_test("1.2.3")
_test("1.2.3+dev", is_dev=True)
_test("1.2.3-10-g3b5f5762", is_dev=True)
_test("1.2.3-b42", pre=("b", 42))
_test("1.2.3-rc1+dev", is_dev=True, pre=("rc", 1))
assert Version("1.2.3") < Version("1.2.4")
assert Version("1.2.3-a1") < Version("1.2.3-a2")
assert Version("1.2.3-a10") < Version("1.2.3-b1")
assert Version("1.2.3-b10") < Version("1.2.3-rc1")
assert Version("1.2.3-b1") < Version("1.2.3-b1+dev")
assert Version("1.2.3-rc1") < Version("1.2.3")
assert Version("1.2.3") < Version("1.2.3+dev")
assert Version("1.2.3+dev") < Version("1.2.4-rc1")
assert Version("1.2.4-rc1") < Version("1.2.4-rc1+dev")
assert Version("1.2.4-rc1+dev") < Version("1.2.4")
assert Version("1.2.3") < Version("1.2.3-10-g3b5f5762")
assert Version("1.2.3-10-g3b5f5762") < Version("1.2.4-b42")
assert Version("1.2.4-b42") < Version("1.2.4-b42-10-g3b5f5762")
assert Version("1.2.4-b42-10-g3b5f5762") < Version("1.2.4")
assert Version("1.2.3-rc10+dev") < Version("1.2.3")
assert Version("1.2.3-b10+dev") < Version("1.2.3-rc1+dev")
assert Version("1.2.3-b10+dev") < Version("1.2.3+dev")


def run_git(cmd, verbose=False):
    cmd = f"git {cmd}"
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Error while running `{cmd}`: returned {proc.returncode}\n"
            f"stdout:\n{proc.stdout.decode()}\n"
            f"stderr:\n{proc.stderr.decode()}\n"
        )
    stderr = proc.stderr.decode()
    if verbose and stderr:
        print(f"[Stderr stream from `{cmd}`]\n{stderr}[End stderr stream]", file=sys.stderr)
    return proc.stdout.decode()


def get_version_from_repo_describe_tag(verbose=False):
    # Note we only search for annotated tags
    return Version(run_git("describe --debug", verbose=verbose))


def get_version_from_code():
    global_dict = {}
    exec((VERSION_FILE).read_text(), global_dict)
    __version__ = global_dict.get("__version__")
    if not __version__:
        raise ReleaseError(f"Cannot find __version__ in {VERSION_FILE!s}")
    return Version(__version__)


def update_version_file(new_version: Version) -> None:
    version_txt = VERSION_FILE.read_text()
    updated_version_txt = re.sub(
        r'__version__\W=\W".*"', f'__version__ = "{new_version}"', version_txt
    )
    assert updated_version_txt != version_txt
    VERSION_FILE.write_text(updated_version_txt)


def update_license_file(new_version: Version, new_release_date: str) -> None:
    license_txt = BSL_LICENSE_FILE.read_text()
    half_updated_license_txt = re.sub(
        r"Change Date:.*", f"Change Date:  {new_release_date}", license_txt
    )
    assert half_updated_license_txt != license_txt
    full_updated_version_txt = re.sub(
        r"Licensed Work:.*", f"Licensed Work:  Parsec {new_version}", half_updated_license_txt
    )
    assert full_updated_version_txt != half_updated_license_txt
    BSL_LICENSE_FILE.write_text(full_updated_version_txt)


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


def build_release(version, stage_pause):
    if version.is_dev:
        raise ReleaseError(f"Invalid release version: {version}")
    print(f"Build release {version}")
    old_version = get_version_from_code()
    if version < old_version:
        raise ReleaseError(
            f"Previous version incompatible with new one ({old_version} vs {version})"
        )

    release_date = date.today().isoformat()

    # Check repo is clean
    stdout = run_git("status --porcelain --untracked-files=no")
    if stdout.strip():
        raise ReleaseError("Repository is not clean, aborting")

    # Update __version__
    update_version_file(version)

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
    new_entry_title = f"Parsec {version} ({release_date})"
    new_entry = f"\n\n{new_entry_title}\n{len(new_entry_title) * '-'}\n"
    issues_per_type = defaultdict(list)
    for fragment in newsfragments:
        issue_id, type, _ = fragment.name.split(".")
        # Don't add empty fragments. Still needed to be collected as they will be deleted later
        if type == "empty":
            continue
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

    # Update BSL license date marker & version info
    update_license_file(version, release_date)

    # Make git commit
    commit_msg = f"Bump version {old_version} -> {version}"
    print(f"Create commit `{commit_msg}`")
    if stage_pause:
        input("Pausing, press enter when ready")
    run_git(f"add {HISTORY_FILE.absolute()} {VERSION_FILE.absolute()}")
    if newsfragments:
        fragments_pathes = [str(x.absolute()) for x in newsfragments]
        run_git(f"rm {' '.join(fragments_pathes)}")
    # Disable pre-commit hooks given this commit wouldn't pass `releaser check`
    run_git(f"commit -m '{commit_msg}' --no-verify")

    print(f"Create tag {version}")
    if stage_pause:
        input("Pausing, press enter when ready")
    run_git(f"tag {version} -m 'Release version {version}' -a -s")

    # Update __version__ with dev suffix
    dev_version = version.evolve(is_dev=True)
    commit_msg = f"Bump version {version} -> {dev_version}"
    print(f"Create commit `{commit_msg}`")
    if stage_pause:
        input("Pausing, press enter when ready")
    update_version_file(dev_version)
    update_license_file(dev_version, release_date)
    run_git(f"add {VERSION_FILE.absolute()}")
    # Disable pre-commit hooks given this commit wouldn't pass `releaser check`
    run_git(f"commit -m '{commit_msg}' --no-verify")


def check_release(version):
    print(f"Checking release {version}")

    # Check __version__
    code_version = get_version_from_code()
    if code_version != version:
        raise ReleaseError(
            f"Invalid __version__ in parsec/_version.py: expected `{version}`, got `{code_version}`"
        )

    # Check newsfragments
    fragments = collect_newsfragments()
    if fragments:
        fragments_names = [fragment.name for fragment in fragments]
        raise ReleaseError(
            f"newsfragments still contains fragments files ({', '.join(fragments_names)})"
        )

    # Check tag exist and is an annotated&signed one
    show_info = run_git(f"show --quiet {version}")
    tag_type = show_info.split(" ", 1)[0]
    if tag_type != "tag":
        raise ReleaseError(f"{version} is not an annotated tag (type: {tag_type})")
    if "BEGIN PGP SIGNATURE" not in show_info:
        raise ReleaseError(f"{version} is not signed")


def check_non_release(version):
    print(f"Checking non-release {version}")
    # Check __version__
    code_version = get_version_from_code()
    if code_version != version:
        raise ReleaseError(
            f"Invalid __version__ in parsec/_version.py: expected `{version}`, got `{code_version}`"
        )

    # Force newsfragments format sanity check
    collect_newsfragments()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("build", "check"))
    parser.add_argument("version", type=Version, nargs="?")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-P", "--stage-pause", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "build":
            if not args.version:
                raise SystemExit("version is required for build command")
            current_version = get_version_from_repo_describe_tag(args.verbose)
            check_non_release(current_version)
            build_release(args.version, args.stage_pause)

        else:  # Check

            if args.version is None:
                version = get_version_from_repo_describe_tag(args.verbose)
            else:
                version = args.version

            if version.is_dev:
                check_non_release(version)

            else:
                check_release(version)

    except ReleaseError as exc:
        raise SystemExit(str(exc)) from exc
