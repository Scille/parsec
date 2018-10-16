#!/usr/bin/env python
# -*- coding: utf-8 -*-


from setuptools import setup, find_packages

try:
    from cx_Freeze import setup, Executable
except ImportError:
    Executable = lambda x, **kw: x


def _extract_libs_cffi_backend():
    try:
        import nacl
    except ImportError:
        return []

    import pathlib

    cffi_backend_dir = pathlib.Path(nacl.__file__).parent / "../.libs_cffi_backend"
    return [(lib.as_posix(), lib.name) for lib in cffi_backend_dir.glob("*")]


build_exe_options = {
    "packages": [
        "idna",
        "trio._core",
        "nacl._sodium",
        "html.parser",
        "pkg_resources._vendor",
        "swiftclient",
        "setuptools.msvc",
        "unittest.mock",
    ],
    # nacl store it cffi shared lib in a very strange place...
    "include_files": _extract_libs_cffi_backend(),
}


with open("README.rst") as readme_file:
    readme = readme_file.read()

with open("HISTORY.rst") as history_file:
    history = history_file.read()

requirements = [
    "attrs==18.1.0",
    "click==6.7",
    "huepy==0.9.6",
    "Logbook==1.2.1",
    # Can use marshmallow or the toasted flavour as you like ;-)
    # "marshmallow==2.14.0",
    "toastedmarshmallow==0.2.6",
    "pendulum==1.3.1",
    "PyNaCl==1.2.0",
    "simplejson==3.10.0",
    "python-decouple==3.1",
    "trio==0.9.0",
    "python-interface==1.4.0",
    "async_generator>=1.9",
    'contextvars==2.1;python_version<"3.7"',
    "raven==6.8.0",  # Sentry support
]
dependency_links = [
    # need to use --process-dependency-links option for this
    "git+https://github.com/fusepy/fusepy.git#egg=fusepy-3.0.0"
]

test_requirements = [
    # https://github.com/python-trio/pytest-trio/issues/64
    # "pytest>=3.6",
    "pytest==3.8.0",
    "pytest-cov",
    "pytest-trio",
    "pytest-logbook",
    "tox",
    "wheel",
    "Sphinx",
    "flake8",
    "hypothesis",
    "hypothesis-trio>=0.2.1",
    "bumpversion",
    "black==18.6b1",  # Pin black to avoid flaky style check
]

extra_requirements = {
    "core": ["PyQt5==5.11.2", "hurry.filesize==0.9", "fusepy==3.0.0"],
    "backend": [
        # PostgreSQL
        "triopg==0.3.0",
        "trio-asyncio==0.9.1",
        # S3
        "boto3==1.4.4",
        "botocore==1.5.46",
        # Swift
        "python-swiftclient==3.5.0",
        "pbr==4.0.2",
        "futures==3.1.1",
    ],
    "dev": test_requirements,
}
extra_requirements["all"] = sum(extra_requirements.values(), [])
extra_requirements["oeuf-jambon-fromage"] = extra_requirements["all"]

setup(
    name="parsec-cloud",
    version="0.7.0",
    description="Secure cloud framework",
    long_description=readme + "\n\n" + history,
    author="Scille SAS",
    author_email="contact@scille.fr",
    url="https://github.com/Scille/parsec-cloud",
    packages=find_packages(),
    package_data={"parsec": ["resources"]},
    package_dir={"parsec": "parsec"},
    include_package_data=True,
    install_requires=requirements,
    dependency_links=dependency_links,
    extras_require=extra_requirements,
    entry_points={"console_scripts": ["parsec = parsec.cli:cli"]},
    options={"build_exe": build_exe_options},
    executables=[Executable("parsec/cli.py", targetName="parsec")],
    license="AGPLv3",
    zip_safe=False,
    keywords="parsec",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
        "Natural Language :: English",
        "Programming Language :: Python :: 3.6",
    ],
    test_suite="tests",
    tests_require=test_requirements,
)
