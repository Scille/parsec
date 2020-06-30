# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.core.backend_connection.apiv1_administration import (
    APIV1_BackendAdministrationCmds,
    apiv1_backend_administration_cmds_factory,
)
from parsec.core.backend_connection.apiv1_annonymous import (
    APIV1_BackendAnonymousCmds,
    apiv1_backend_anonymous_cmds_factory,
)
from parsec.core.backend_connection.apiv1_authenticated import (
    APIV1_BackendAuthenticatedCmds,
    APIV1_BackendAuthenticatedConn,
    apiv1_backend_authenticated_cmds_factory,
)
from parsec.core.backend_connection.authenticated import (
    BackendAuthenticatedCmds,
    BackendAuthenticatedConn,
    BackendConnStatus,
    backend_authenticated_cmds_factory,
)
from parsec.core.backend_connection.exceptions import (
    BackendConnectionError,
    BackendConnectionRefused,
    BackendNotAvailable,
    BackendNotFoundError,
    BackendProtocolError,
)
from parsec.core.backend_connection.invited import BackendInvitedCmds, backend_invited_cmds_factory

__all__ = (
    # Exceptions
    "BackendConnectionError",
    "BackendProtocolError",
    "BackendNotAvailable",
    "BackendConnectionRefused",
    "BackendNotFoundError",
    # Authenticated
    "BackendAuthenticatedCmds",
    "BackendConnStatus",
    "BackendAuthenticatedConn",
    "backend_authenticated_cmds_factory",
    # Invited
    "BackendInvitedCmds",
    "backend_invited_cmds_factory",
    # APIv1 Authenticated
    "APIV1_BackendAuthenticatedCmds",
    "APIV1_BackendAuthenticatedConn",
    "apiv1_backend_authenticated_cmds_factory",
    # APIv1 Annonymous
    "APIV1_BackendAnonymousCmds",
    "apiv1_backend_anonymous_cmds_factory",
    # APIv1 Administration
    "APIV1_BackendAdministrationCmds",
    "apiv1_backend_administration_cmds_factory",
)
