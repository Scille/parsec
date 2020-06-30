# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import xml.etree.ElementTree as ET

IGNORE_LIST = ["Dialog", "Form"]


def get_location(elem):
    loc = elem.find("location")
    return loc.attrib["filename"], int(loc.attrib["line"])


def get_translation_string(elem):
    tr = elem.find("source")
    return tr.text


def extract_qt(fileobj, keywords, comment_tags, options):
    tree = ET.parse(fileobj)
    root = tree.getroot()
    for message in root.iter("message"):
        loc = get_location(message)
        s = get_translation_string(message)
        if s in IGNORE_LIST:
            continue
        yield (loc[1], None, s, [])
