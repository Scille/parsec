# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

# The parsec.utils module includes a bit of patching, let's make sure it is imported
import parsec.utils  # noqa
from parsec._version import __version__

__all__ = ("__version__",)
