#! /usr/bin/env python3
# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS


import argparse
import csv
import os

from babel.messages.pofile import read_po

TR_DIR = "parsec/core/gui/tr"
TR_FILES = {"en": "parsec_en.po", "fr": "parsec_fr.po"}


def parse_po_file(in_file):
    with open(in_file, "r") as in_fd:
        catalog = read_po(in_fd)
        return catalog


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    contents = {}
    with open(args.output, "w+", newline="") as csv_file:
        writer = csv.writer(csv_file, dialect="excel")
        contents = {
            lang: parse_po_file(os.path.join(TR_DIR, po_file)) for lang, po_file in TR_FILES.items()
        }
        for message in contents[list(TR_FILES.keys())[0]]:
            if message.id == "":
                continue
            row = [message.id]
            for lang in TR_FILES.keys():
                row.append(contents[lang][message.id].string)
            writer.writerow(row)
