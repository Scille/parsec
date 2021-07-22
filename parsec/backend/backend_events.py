# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from enum import Enum

__all__ = ("BackendEvent",)


class BackendEvent(Enum):
    """ Backend internal events"""

    DEVICE_CLAIMED = "device.claimed"
    DEVICE_CREATED = "device.created"
    DEVICE_INVITATION_CANCELLED = "device.invitation.cancelled"
    INVITE_CONDUIT_UPDATED = "invite.conduit_updated"
    USER_CLAIMED = "user.claimed"
    USER_CREATED = "user.created"
    USER_REVOKED = "user.revoked"
    USER_INVITATION_CANCELLED = "user.invitation.cancelled"
    ORGANIZATION_EXPIRED = "organization.expired"
    # api Event mirror
    PINGED = "pinged"
    MESSAGE_RECEIVED = "message.received"
    INVITE_STATUS_CHANGED = "invite.status_changed"
    REALM_MAINTENANCE_FINISHED = "realm.maintenance_finished"
    REALM_MAINTENANCE_STARTED = "realm.maintenance_started"
    REALM_VLOBS_UPDATED = "realm.vlobs_updated"
    REALM_ROLES_UPDATED = "realm.roles_updated"
