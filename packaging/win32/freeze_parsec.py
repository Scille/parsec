# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import argparse
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from urllib.request import urlopen

BUILD_DIR = Path("build").resolve()

TOOLS_VENV_DIR = BUILD_DIR / "tools_venv"
WHEELS_DIR = BUILD_DIR / "wheels"

CPYTHON_DIR = Path(sysconfig.get_paths()["data"]).resolve()
CPYTHON_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
CPYTHON_ARCHSLUG = "win32" if platform.architecture()[0] == "32bit" else "amd64"
CPYTHON_DISTRIB_NAME = f"python-{CPYTHON_VERSION}-embed-{CPYTHON_ARCHSLUG}"
CPYTHON_DISTRIB_URL = (
    f"https://www.python.org/ftp/python/{CPYTHON_VERSION}/{CPYTHON_DISTRIB_NAME}.zip"
)
CPYTHON_DISTRIB_ARCHIVE = BUILD_DIR / f"{CPYTHON_DISTRIB_NAME}.zip"


def get_archslug():
    bits, _ = platform.architecture()
    return "win32" if bits == "32bit" else "win64"


def run(cmd, **kwargs):
    print(f">>> {cmd}")
    ret = subprocess.run(cmd, shell=True, **kwargs)
    assert ret.returncode == 0, ret.returncode
    return ret


def main(parsec_source):
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # Retrieve parsec version
    global_dict = {}
    exec((parsec_source / "parsec/_version.py").read_text(), global_dict)
    parsec_version = global_dict.get("__version__")
    print(f"### Detected Parsec version {parsec_version} ###")

    # Fetch CPython distrib
    if not CPYTHON_DISTRIB_ARCHIVE.is_file():
        print(f"### Fetch CPython {CPYTHON_VERSION} build ###")
        req = urlopen(CPYTHON_DISTRIB_URL)
        CPYTHON_DISTRIB_ARCHIVE.write_bytes(req.read())

    # Bootstrap tools virtualenv
    if not TOOLS_VENV_DIR.is_dir():
        print(f"### Create tool virtualenv ###")
        run(f"python -m venv {TOOLS_VENV_DIR}")
        run(f"{ TOOLS_VENV_DIR / 'Scripts/python' } -m pip install wheel")

    if not WHEELS_DIR.is_dir():
        print(f"### Generate wheels from Parsec&dependencies ###")
        run(
            f"{ TOOLS_VENV_DIR / 'Scripts/python' } -m pip wheel {parsec_source}[core] --wheel-dir {WHEELS_DIR}"
        )

    # Now we actually generate the build target

    target_dir = BUILD_DIR / f"parsec-{parsec_version}-{get_archslug()}"
    if target_dir.exists():
        raise SystemExit(f"{target_dir} already exists, exiting...")

    # Extract CPython distrib
    print(f"### Extracting CPython embedded distribution ###")
    shutil.unpack_archive(str(CPYTHON_DISTRIB_ARCHIVE), extract_dir=str(target_dir))

    # Bootstrap build virtualenv
    build_venv_dir = target_dir / "build_venv"
    print(f"### Installing wheels in temporary virtualenv ###")
    run(f"python -m venv {build_venv_dir}")
    wheels = " ".join(map(str, WHEELS_DIR.glob("*.whl")))
    run(f"{ build_venv_dir / 'Scripts/python' } -m pip install {wheels}")

    # Move build virtualenv's site-packages to the build and patch imports
    print(f"### Move site-packages to embedded distribution ###")
    shutil.move(build_venv_dir / "Lib/site-packages", target_dir / "site-packages")
    shutil.rmtree(build_venv_dir)
    pth_file, = target_dir.glob("*._pth")
    pth_file.write_text(pth_file.read_text() + "site-packages\n")

    # Include LICENSE file
    (target_dir / "LICENSE.txt").write_text((parsec_source / "LICENSE").read_text())

    # Build parsec.exe
    resource_rc = BUILD_DIR / "resource.rc"
    resource_res = BUILD_DIR / "resource.res"
    versioninfo = (*re.match(r"^.*([0-9]+)\.([0-9]+)\.([0-9]+)", parsec_version).groups(), "0")
    escaped_parsec_ico = str(Path("parsec.ico").resolve()).replace("\\", "\\\\")
    escaped_parsec_manifest = str(Path("parsec.manifest").resolve()).replace("\\", "\\\\")
    resource_rc.write_text(
        f"""
#include <windows.h>

1 RT_MANIFEST "{escaped_parsec_manifest}"
2 ICON "{escaped_parsec_ico}"

VS_VERSION_INFO VERSIONINFO
FILEVERSION     {','.join(versioninfo)}
PRODUCTVERSION  {','.join(versioninfo)}
FILEFLAGSMASK 0x3fL
FILEFLAGS 0x0L
FILEOS VOS__WINDOWS32
FILETYPE VFT_APP
FILESUBTYPE 0x0L
BEGIN
    BLOCK "StringFileInfo"
    BEGIN
        BLOCK "000004b0"
        BEGIN
            VALUE "CompanyName",      "Scille SAS\\0"
            VALUE "FileDescription",  "Parsec Secure Cloud Storage\\0"
            VALUE "FileVersion",      "{parsec_version}\\0"
            VALUE "InternalName",     "Parsec GUI Bootstrapper\\0"
            VALUE "LegalCopyright",   "Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS\\0"
            VALUE "OriginalFilename", "parsec.exe\\0"
            VALUE "ProductName",      "Parsec\\0"
            VALUE "ProductVersion",   "{parsec_version}\\0"
        END
    END
    BLOCK "VarFileInfo"
    BEGIN
        VALUE "Translation", 0x0, 1200
    END
END
"""
    )
    run(f"rc.exe /i. /fo {resource_res} {resource_rc}")
    # Must make sure /Fo option ends with a "\", otherwise it is not considered as a folder...
    run(f"cl.exe parsec-bootstrap.c /c /I { CPYTHON_DIR / 'include' } /Fo{BUILD_DIR}\\")
    run(
        f"link.exe { BUILD_DIR / 'parsec-bootstrap.obj' } {resource_res} "
        f"/LIBPATH:{ CPYTHON_DIR / 'libs' } /OUT:{ target_dir / 'parsec.exe' } "
        f"/subsystem:windows /entry:mainCRTStartup"
    )

    # Create build info file for NSIS installer
    (BUILD_DIR / "BUILD.tmp").write_text(
        f'target = "{target_dir}"\n'
        f'parsec_version = "{parsec_version}"\n'
        f'python_version = "{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"\n'
        f'platform = "{get_archslug()}"\n'
    )

    # Create the install and uninstall file list for NSIS installer
    target_files = []

    def _recursive_collect_target_files(curr_dir):
        subdirs = []
        for entry in curr_dir.iterdir():
            if entry.is_dir():
                subdirs.append(entry)
            else:
                target_files.append((False, entry.relative_to(target_dir)))
        for subdir in subdirs:
            target_files.append((True, subdir.relative_to(target_dir)))
            _recursive_collect_target_files(subdir)

    _recursive_collect_target_files(target_dir)

    install_files_lines = ["; Files to install", 'SetOutPath "$INSTDIR\\"']
    curr_dir = Path(".")
    for target_is_dir, target_file in target_files:
        if target_is_dir:
            install_files_lines.append(f'SetOutPath "$INSTDIR\\{target_file}"')
            curr_dir = target_file
        else:
            assert curr_dir == target_file.parent
            install_files_lines.append(f'File "${{PARSEC_FREEZE_BUILD_DIR}}\\{target_file}"')
    (BUILD_DIR / "install_files.nsh").write_text("\n".join(install_files_lines))

    uninstall_files_lines = ["; Files to uninstall"]
    for target_is_dir, target_file in reversed(target_files):
        if target_is_dir:
            uninstall_files_lines.append(f'RMDir "$INSTDIR\\{target_file}"')
        else:
            uninstall_files_lines.append(f'Delete "$INSTDIR\\{target_file}"')
    (BUILD_DIR / "uninstall_files.nsh").write_text("\n".join(uninstall_files_lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Freeze Parsec")
    parser.add_argument("parsec_source")
    args = parser.parse_args()
    main(Path(args.parsec_source))
