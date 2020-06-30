#!/usr/bin/env python
# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import os

from setuptools import Command, distutils, find_packages, setup
from setuptools.command.build_py import build_py

# Awesome hack to load `__version__`
__version__ = None
exec(open("parsec/_version.py", encoding="utf-8").read())


def fix_pyqt_import():
    # PyQt5-sip is a distinct pip package that provides PyQt5.sip
    # However it setuptools handles `setup_requires` by downloading the
    # dependencies in the `./.eggs` directory without really installing
    # them. This causes `import PyQt5.sip` to fail given the `PyQt5` folder
    # doesn't contains `sip.so` (or `sip.pyd` on windows)...
    import sys
    import glob
    import importlib

    for module_name, path_glob in (
        ("PyQt5", ".eggs/*PyQt5*/PyQt5/__init__.py"),
        ("PyQt5.sip", ".eggs/*PyQt5_sip*/PyQt5/sip.*"),
    ):
        # If the module has already been installed in the environment
        # setuptools won't populate the `.eggs` directory and we have
        # nothing to do
        try:
            importlib.import_module(module_name)
        except ImportError:
            pass
        else:
            continue

        for path in glob.glob(path_glob):

            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                break

        else:
            raise RuntimeError("Cannot found module `%s` in .eggs" % module_name)


class GeneratePyQtResourcesBundle(Command):
    description = "Generates `parsec.core.gui._resource_rc` bundle module"

    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        fix_pyqt_import()
        try:
            from PyQt5.pyrcc_main import processResourceFile

            self.announce("Generating `parsec.core.gui._resources_rc`", level=distutils.log.INFO)
            processResourceFile(
                ["parsec/core/gui/rc/resources.qrc"], "parsec/core/gui/_resources_rc.py", False
            )
        except ImportError:
            print("PyQt5 not installed, skipping `parsec.core.gui._resources_rc` generation.")


class GenerateChangelog(Command):
    description = "Convert HISTORY.rst to HTML"

    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        import docutils.core

        destination_folder = "parsec/core/gui/rc/generated_misc"
        self.announce(
            f"Converting HISTORY.rst to {destination_folder}/history.html", level=distutils.log.INFO
        )
        os.makedirs(destination_folder, exist_ok=True)
        docutils.core.publish_file(
            source_path="HISTORY.rst",
            destination_path=f"{destination_folder}/history.html",
            writer_name="html",
        )


class GeneratePyQtForms(Command):
    description = "Generates `parsec.core.ui.*` forms module"

    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        import os
        import pathlib
        from collections import namedtuple

        fix_pyqt_import()
        try:
            from PyQt5.uic.driver import Driver
        except ImportError:
            print("PyQt5 not installed, skipping `parsec.core.gui.ui` generation.")
            return

        self.announce("Generating `parsec.core.gui.ui`", level=distutils.log.INFO)
        Options = namedtuple(
            "Options",
            ["output", "import_from", "debug", "preview", "execute", "indent", "resource_suffix"],
        )
        ui_dir = pathlib.Path("parsec/core/gui/forms")
        ui_path = "parsec/core/gui/ui"
        os.makedirs(ui_path, exist_ok=True)
        for f in ui_dir.iterdir():
            o = Options(
                output=os.path.join(ui_path, "{}.py".format(f.stem)),
                import_from="parsec.core.gui",
                debug=False,
                preview=False,
                execute=False,
                indent=4,
                resource_suffix="_rc",
            )
            d = Driver(o, str(f))
            d.invoke()


class ExtractTranslations(Command):
    description = "Extract translation strings"

    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        import os
        import pathlib
        from unittest.mock import patch
        from babel.messages.frontend import CommandLineInterface

        fix_pyqt_import()
        try:
            from PyQt5.pylupdate_main import main as pylupdate_main
        except ImportError:
            print("PyQt5 not installed, skipping `parsec.core.gui.ui` generation.")
            return

        self.announce("Generating ui translation files", level=distutils.log.INFO)
        ui_dir = pathlib.Path("parsec/core/gui")
        tr_dir = ui_dir / "tr"
        os.makedirs(tr_dir, exist_ok=True)

        new_args = ["pylupdate", str(ui_dir / "parsec-gui.pro")]
        with patch("sys.argv", new_args):
            pylupdate_main()

        files = [str(f) for f in ui_dir.iterdir() if f.is_file() and f.suffix == ".py"]
        files.append(str(tr_dir / "parsec_en.ts"))
        args = [
            "_",
            "extract",
            "-s",
            "--no-location",
            "-F",
            ".babel.cfg",
            "--omit-header",
            "-o",
            str(tr_dir / "translation.pot"),
            *files,
        ]
        CommandLineInterface().run(args)
        languages = ["fr", "en"]
        for lang in languages:
            po_file = tr_dir / f"parsec_{lang}.po"
            if not po_file.is_file():
                po_file.touch()
            args = [
                "_",
                "update",
                "-i",
                str(tr_dir / "translation.pot"),
                "-o",
                str(po_file),
                "-l",
                lang,
            ]
            CommandLineInterface().run(args)


class CompileTranslations(Command):
    description = "Compile translations"

    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        import os
        import pathlib
        from babel.messages.frontend import CommandLineInterface

        self.announce("Compiling ui translation files", level=distutils.log.INFO)
        ui_dir = pathlib.Path("parsec/core/gui")
        tr_dir = ui_dir / "tr"
        rc_dir = ui_dir / "rc" / "translations"
        os.makedirs(rc_dir, exist_ok=True)
        languages = ["fr", "en"]
        for lang in languages:
            args = [
                "_",
                "compile",
                "-i",
                str(tr_dir / f"parsec_{lang}.po"),
                "-o",
                str(rc_dir / f"parsec_{lang}.mo"),
            ]
            CommandLineInterface().run(args)


class build_py_with_pyqt(build_py):
    def run(self):
        self.run_command("generate_pyqt_forms")
        self.run_command("compile_translations")
        self.run_command("generate_changelog")
        self.run_command("generate_pyqt_resources_bundle")
        return super().run()


class build_py_with_pyqt_resource_bundle_generation(build_py):
    def run(self):
        self.run_command("generate_pyqt_resources_bundle")
        return super().run()


with open("README.rst") as readme_file:
    readme = readme_file.read()

with open("HISTORY.rst") as history_file:
    history = history_file.read()


requirements = [
    "attrs==19.2.0",
    "click==7.0",
    "msgpack==0.6.0",
    "wsproto==0.15.0",
    # Can use marshmallow or the toasted flavour as you like ;-)
    # "marshmallow==2.14.0",
    "toastedmarshmallow==0.2.6",
    "pendulum==1.3.1",
    "PyNaCl==1.4.0",
    "trio==0.13.0",
    "python-interface==1.4.0",
    "async_generator>=1.9",
    'contextvars==2.1;python_version<"3.7"',
    "sentry-sdk==0.14.3",
    "structlog==19.2.0",
    "importlib_resources==1.0.2",
    "colorama==0.4.0",  # structlog colored output
    "PyPika==0.36.5",
    "async_exit_stack==1.0.1",
    "outcome==1.0.0",
]


test_requirements = [
    "pytest==5.4.3",
    "pytest-cov==2.10.0",
    "pytest-xdist==1.32.0",
    "pytest-trio==0.5.2",
    "pytest-qt==3.3.0",
    "pytest-rerunfailures==9.0",
    "hypothesis==5.3.0",
    "hypothesis-trio==0.5.0",
    "trustme==0.6.0",
    # Winfsptest requirements
    # We can't use `winfspy[test]` because of some pip limitations
    # - see pip issues #7096/#6239/#4391/#988
    # Looking forward to the new pip dependency resolver!
    'pywin32==227;platform_system=="Windows"',
    # Fix botocore and sphinx conflicting requirements on docutils
    "docutils>=0.12,<0.16",
    # Documentation generation requirements
    "sphinx==2.4.3",
    "sphinx-intl==2.0.0",
    "sphinx-rtd-theme==0.4.3",
]


PYQT_DEP = "PyQt5==5.13.1"
BABEL_DEP = "Babel==2.6.0"
WHEEL_DEP = "wheel==0.34.2"
DOCUTILS_DEP = "docutils==0.15"
extra_requirements = {
    "core": [
        PYQT_DEP,
        BABEL_DEP,
        'fusepy==3.0.1;platform_system=="Linux"',
        'winfspy==0.7.5;platform_system=="Windows"',
        "zxcvbn==4.4.27",
        "psutil==5.6.3",
    ],
    "backend": [
        # PostgreSQL
        "triopg==0.3.0",
        "trio-asyncio==0.11.0",
        # S3
        "boto3==1.12.34",
        "botocore==1.15.34",
        # Swift
        "python-swiftclient==3.5.0",
        "pbr==4.0.2",
    ],
    "dev": test_requirements,
}
extra_requirements["all"] = sum(extra_requirements.values(), [])
extra_requirements["oeuf-jambon-fromage"] = extra_requirements["all"]

setup(
    name="parsec-cloud",
    version=__version__,
    description="Secure cloud framework",
    long_description=readme + "\n\n" + history,
    author="Scille SAS",
    author_email="contact@scille.fr",
    url="https://github.com/Scille/parsec-cloud",
    packages=find_packages(include=["parsec", "parsec.*"]),
    package_dir={"parsec": "parsec"},
    setup_requires=[WHEEL_DEP, PYQT_DEP, BABEL_DEP, DOCUTILS_DEP],  # To generate resources bundle
    install_requires=requirements,
    extras_require=extra_requirements,
    cmdclass={
        "generate_pyqt_resources_bundle": GeneratePyQtResourcesBundle,
        "generate_changelog": GenerateChangelog,
        "generate_pyqt_forms": GeneratePyQtForms,
        "extract_translations": ExtractTranslations,
        "compile_translations": CompileTranslations,
        "generate_pyqt": build_py_with_pyqt,
        "build_py": build_py_with_pyqt,
    },
    # Omitting GUI resources given they end up packaged in `parsec/core/gui/_resources_rc.py`
    package_data={
        "parsec.backend.postgresql.migrations": ["*.sql"],
        "parsec.core.resources": ["*.ico"],
    },
    entry_points={
        "console_scripts": ["parsec = parsec.cli:cli"],
        "babel.extractors": ["extract_qt = misc.babel_qt_extractor.extract_qt"],
    },
    license="AGPLv3",
    zip_safe=False,
    keywords="parsec",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Operating System :: POSIX :: Linux",
        "Operating System :: Microsoft :: Windows",
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
        "Natural Language :: English",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
    ],
    test_suite="tests",
    tests_require=test_requirements,
    long_description_content_type="text/x-rst",
)
