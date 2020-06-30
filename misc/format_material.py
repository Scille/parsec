#!/usr/bin/env python
# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import argparse
import pathlib
import shutil
from xml.etree import ElementTree


def move_files(input_dir, output_dir, pattern):
    for img in input_dir.rglob(pattern):
        if img.parent.name == "production":
            shutil.copy(str(img), str(output_dir))


def resize_imgs(input_dir):
    ElementTree.register_namespace("", "http://www.w3.org/2000/svg")
    for img in input_dir.iterdir():
        tree = ElementTree.parse(str(img))
        root = tree.getroot()
        root.attrib["width"] = "144"
        root.attrib["height"] = "144"
        with open(img, "w+") as fd:
            fd.write(ElementTree.tostring(root, encoding="unicode", method="xml"))
        dst = img.parent / img.name.replace("_48px", "").replace("ic_", "")
        shutil.move(str(img), str(dst))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Move material SVG files and resize them")
    parser.add_argument(
        "-i", "--input", type=pathlib.Path, help="Input material folder", required=True
    )
    parser.add_argument("-o", "--output", type=pathlib.Path, help="Output folder", required=True)
    parser.add_argument("--pattern", default="*_48px.svg", required=False)
    parser.add_argument("--no-copy", action="store_true", help="Does not copy the files")
    parser.add_argument("--no-resize", action="store_true", help="Does not resize the files")

    args = parser.parse_args()

    if not args.no_copy:
        move_files(args.input, args.output, args.pattern)
    if not args.no_resize:
        resize_imgs(args.output)
