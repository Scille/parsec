# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import hashlib
import json
from http.client import HTTPResponse
from unittest.mock import Mock, patch

import trio

from parsec.core.gui.new_version import _do_check_new_version

gui_check_version_url = "https://github.com/Scille/parsec-cloud/releases/latest"
gui_web_releases_url = gui_check_version_url[:-7]
gui_check_version_api_url = "https://api.github.com/repos/Scille/parsec-cloud/releases"
gui_api_url = gui_check_version_api_url[:-9]


def quick_hash(name: str):
    m = hashlib.sha256()
    m.update(name.encode("utf-8"))
    return str(int.from_bytes(m.digest(), byteorder="big"))[0:8]


def generate_brower_download_url(version, arch):
    return f"{gui_web_releases_url}/download/{version}/parsec-{version}-win{str(arch)}-setup.exe"


def generate_json_asset(version, arch):
    name = f"parsec-{version}-win{str(arch)}-setup.exe"
    hash_str = quick_hash(name)
    hash_int = int(hash_str)
    return {
        "url": f"{gui_check_version_api_url}/assets/{hash_str}",
        "id": hash_int,
        "node_id": "MDEyOlJlbGVhc2VBc3NldDE2OTI3Njg2",
        "name": name,
        "label": None,
        "uploader": {},
        "content_type": "application/x-ms-dos-executable",
        "state": "uploaded",
        "size": 51896616,
        "download_count": 11,
        "created_at": "2019-12-20T14:47:11Z",
        "updated_at": "2019-12-20T14:47:22Z",
        "browser_download_url": generate_brower_download_url(version, arch),
    }


def generate_json_assets(version, is_32_bit_present, is_64_bit_present):
    assets = []
    if is_32_bit_present:
        assets.append(generate_json_asset(version, 32))
    if is_64_bit_present:
        assets.append(generate_json_asset(version, 64))
    return assets


def generate_json_release(name, draft, prerelease, is_32_bit_present, is_64_bit_present):
    hash_str = quick_hash(name)
    hash_int = int(hash_str)
    return {
        "url": f"{gui_check_version_api_url}/{hash_str}",
        "assets_url": f"{gui_check_version_api_url}/{hash_str}/assets",
        "upload_url": f"https://uploads.github.com/repos/Scille/parsec-cloud/releases/{hash_str}/assets{{?name,label}}",
        "html_url": f"{gui_web_releases_url}/tag/v1.4.0",
        "id": hash_int,
        "node_id": "MDc6UmVsZWFzZTIyMTUzODQz",
        "tag_name": name,
        "target_commitish": "master",
        "name": "",  # As it is most of the times empty
        "draft": draft,
        "author": {},  # No need for those tests
        "prerelease": prerelease,
        "created_at": "2019-12-06T22:38:00Z",
        "published_at": "2019-12-11T18:11:22Z",
        "assets": generate_json_assets(name, is_32_bit_present, is_64_bit_present),
        "tarball_url": "https://api.github.com/repos/Scille/parsec-cloud/tarball/v1.4.0",
        "zipball_url": f"{gui_check_version_api_url}/zipball/v1.4.0",
        "body": "",
    }


def generate_malformed_json_release():
    release = generate_json_release("6.6.6", False, False, True, True)
    for asset in release["assets"]:
        del asset["browser_download_url"]
    return release


def generate_json_data(json_generator):
    r = []
    for release_name, data in json_generator.items():
        r.append(
            generate_json_release(
                release_name, data["draft"], data["prerelease"], data["32"], data["64"]
            )
            if data != "malformed"
            else generate_malformed_json_release()
        )
    return r


def urlopener(head_version="", api_json={}):
    def urlopen(the_request):
        the_response = Mock(spec=HTTPResponse)
        head_version_url = f"{gui_web_releases_url}/tag/v{head_version}"
        if the_request.full_url == gui_check_version_url and the_request.method == "HEAD":
            the_response.geturl = lambda: head_version_url
        the_response.read = lambda: json.dumps(generate_json_data(api_json))
        return the_response

    return urlopen


def smallcheck():
    return trio.run(_do_check_new_version, gui_check_version_url, gui_check_version_api_url)


def mocked_CPU(arch):
    if arch == 64:
        return lambda _: "x86_64"
    elif arch == 32:
        return lambda _: "i386"
    else:
        raise ValueError(f"Bad arch {arch}")


@patch("parsec.core.gui.new_version.__version__", new="1.8.0")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_updateable():
    assert smallcheck() == ((1, 9, 0), generate_brower_download_url("v1.9.0", 64))


@patch("parsec.core.gui.new_version.__version__", new="1.8.0+dev")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_updateable_with_dev():
    assert smallcheck() == ((1, 9, 0), generate_brower_download_url("v1.9.0", 64))


@patch("parsec.core.gui.new_version.__version__", new="1.9.0")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_non_updateable():
    assert smallcheck() is None


@patch("parsec.core.gui.new_version.__version__", new="1.9.0+dev")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_non_updateable_with_dev():
    assert smallcheck() is None


@patch("parsec.core.gui.new_version.__version__", new="1.8.0+dev")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_newest_announced_not_in_json_not_updateable():
    assert smallcheck() is None


@patch("parsec.core.gui.new_version.__version__", new="1.7.0+dev")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_newest_announced_not_in_json_but_updateable():
    assert smallcheck() == ((1, 8, 0), generate_brower_download_url("v1.8.0", 64))


@patch("parsec.core.gui.new_version.__version__", new="1.8.0+dev")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": False, "32": False, "64": False},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_newest_announced_no_compatible_asset():
    assert smallcheck() is None


@patch("parsec.core.gui.new_version.__version__", new="1.7.0+dev")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": False, "32": False, "64": False},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_newest_announced_no_compatible_asset_but_newer_available():
    assert smallcheck() == ((1, 8, 0), generate_brower_download_url("v1.8.0", 64))


@patch("parsec.core.gui.new_version.__version__", new="1.7.0")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": False, "32": False, "64": True},
            "v1.8.0": {"draft": False, "prerelease": False, "32": False, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(32))
def test_windows_update_newest_announced_no_x86_compatible_asset():
    assert smallcheck() is None


@patch("parsec.core.gui.new_version.__version__", new="1.7.0")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": False, "32": False, "64": True},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(32))
def test_windows_update_newest_announced_no_x86_compatible_asset_but_newer_available():
    assert smallcheck() == ((1, 8, 0), generate_brower_download_url("v1.8.0", 32))


@patch("parsec.core.gui.new_version.__version__", new="1.7.0")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": False, "32": True, "64": False},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": False},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_newest_announced_no_x64_compatible_asset():
    assert smallcheck() is None


@patch("parsec.core.gui.new_version.__version__", new="1.7.0")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": False, "32": True, "64": False},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_newest_announced_no_x64_compatible_asset_but_newer_available():
    assert smallcheck() == ((1, 8, 0), generate_brower_download_url("v1.8.0", 64))


@patch("parsec.core.gui.new_version.__version__", new="1.7.0")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
def test_windows_update_newest_announced_no_cpu_mock_dont_raise_exception():
    assert smallcheck() in (
        ((1, 9, 0), generate_brower_download_url("v1.9.0", 32)),
        ((1, 9, 0), generate_brower_download_url("v1.9.0", 64)),
    )


@patch("parsec.core.gui.new_version.__version__", new="1.7.0")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": False, "prerelease": True, "32": True, "64": True},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_newest_announced_is_prerelease():
    assert smallcheck() == ((1, 8, 0), generate_brower_download_url("v1.8.0", 64))


@patch("parsec.core.gui.new_version.__version__", new="1.7.0")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": {"draft": True, "prerelease": False, "32": True, "64": True},
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_newest_announced_is_draft():
    assert smallcheck() == ((1, 8, 0), generate_brower_download_url("v1.8.0", 64))


@patch("parsec.core.gui.new_version.__version__", new="1.7.0")
@patch(
    "parsec.core.gui.new_version.urlopen",
    new=urlopener(
        head_version="1.9.0",
        api_json={
            "v1.9.0": "malformed",
            "v1.8.0": {"draft": False, "prerelease": False, "32": True, "64": True},
            "v1.7.0": {"draft": False, "prerelease": False, "32": True, "64": True},
        },
    ),
)
@patch("parsec.core.gui.new_version.QSysInfo.currentCpuArchitecture", new=mocked_CPU(64))
def test_windows_update_newest_announced_is_malformed():
    assert smallcheck() == ((1, 9, 0), "https://github.com/Scille/parsec-cloud/releases/latest")
