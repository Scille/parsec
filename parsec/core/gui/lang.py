# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import gettext
import io

import pendulum
from PyQt5.QtCore import QCoreApplication, QDataStream, QFile, QIODevice, QLocale
from structlog import get_logger

from parsec.core.gui.desktop import get_locale_language

LANGUAGES = {"English": "en", "Français": "fr"}

_current_translator = None
_current_locale_language = None

logger = get_logger()


def format_datetime(dt, full=False, seconds=False):
    fmt = "L LT"
    if seconds:
        fmt = "L LTS"
    if full:
        fmt = "LLLL"
    return dt.in_tz(pendulum.local_timezone()).format(
        fmt, locale=_current_locale_language, formatter="alternative"
    )


def qt_translate(_, string):
    return translate(string)


def translate(string):
    if _current_translator:
        return _current_translator.gettext(string)
    return gettext.gettext(string)


def get_qlocale():
    q = QLocale(_current_locale_language)
    return q


def switch_language(core_config, lang_key=None):
    global _current_translator
    global _current_locale_language

    QCoreApplication.translate = qt_translate

    if not lang_key:
        lang_key = core_config.gui_language
    if not lang_key:
        lang_key = get_locale_language()
        logger.info(f"No language in settings, trying local language '{lang_key}'")
    if lang_key not in LANGUAGES.values():
        if lang_key != "en":
            logger.info(f"Language '{lang_key}' unavailable, defaulting to English")
        lang_key = "en"

    _current_locale_language = lang_key

    rc_file = QFile(f":/translations/translations/parsec_{lang_key}.mo")
    if not rc_file.open(QIODevice.ReadOnly):
        logger.warning(f"Unable to read the translations for language '{lang_key}'")
        return None

    try:
        data_stream = QDataStream(rc_file)
        out_stream = io.BytesIO()
        content = data_stream.readRawData(rc_file.size())
        out_stream.write(content)
        out_stream.seek(0)
        _current_translator = gettext.GNUTranslations(out_stream)
        _current_translator.install()
    except OSError:
        logger.warning(f"Unable to load the translations for language '{lang_key}'")
        return None
    finally:
        rc_file.close()
    return lang_key
