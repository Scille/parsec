# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from parsec.api.data import UserProfile
from parsec.api.protocol import (
    InvitationDeletedReason,
    InvitationStatus,
    InvitationType,
    MaintenanceType,
    RealmRole,
)
from parsec.backend.backend_events import BackendEvent
from parsec.backend.invite import ConduitState
from parsec.backend.postgresql.utils import Query, Table, fn_exists

### Organization ###


t_organization = Table("organization")


def q_organization(organization_id=None, _id=None):
    assert organization_id is not None or _id is not None
    q = Query.from_(t_organization)
    if _id is not None:
        return q.where(t_organization._id == _id)
    else:
        return q.where(t_organization.organization_id == organization_id)


def q_organization_internal_id(organization_id):
    return q_organization(organization_id=organization_id).select("_id")


### User ###


t_human = Table("human")
t_user = Table("user_")
t_device = Table("device")
t_user_invitation = Table("user_invitation")
t_device_invitation = Table("device_invitation")


def q_human(organization_id=None, organization=None, email=None, _id=None, table=t_human):
    q = Query.from_(table)
    if _id is not None:
        return q.where(table._id == _id)
    else:
        assert email is not None
        assert organization_id is not None or organization is not None
        _q_organization = (
            organization
            if organization is not None
            else q_organization_internal_id(organization_id)
        )
        return q.where((table.organization == _q_organization) & (table.email == email))


def q_human_internal_id(email, organization_id=None, organization=None, **kwargs):
    return q_human(
        organization_id=organization_id, organization=organization, email=email, **kwargs
    ).select("_id")


def q_user(organization_id=None, organization=None, user_id=None, _id=None, table=t_user):
    q = Query.from_(table)
    if _id is not None:
        return q.where(table._id == _id)
    else:
        assert user_id is not None
        assert organization_id is not None or organization is not None
        _q_organization = (
            organization
            if organization is not None
            else q_organization_internal_id(organization_id)
        )
        return q.where((table.organization == _q_organization) & (table.user_id == user_id))


def q_user_internal_id(user_id, organization_id=None, organization=None, **kwargs):
    return q_user(
        organization_id=organization_id, organization=organization, user_id=user_id, **kwargs
    ).select("_id")


def q_device(organization_id=None, organization=None, device_id=None, _id=None, table=t_device):
    q = Query.from_(table)
    if _id is not None:
        return q.where(table._id == _id)
    else:
        assert device_id is not None
        assert organization_id is not None or organization is not None
        _q_organization = (
            organization
            if organization is not None
            else q_organization_internal_id(organization_id)
        )
        return q.where((table.organization == _q_organization) & (table.device_id == device_id))


def q_device_internal_id(device_id, organization_id=None, organization=None, **kwargs):
    return q_device(
        organization_id=organization_id, organization=organization, device_id=device_id, **kwargs
    ).select("_id")


def q_user_invitation(
    organization_id=None, organization=None, user_id=None, _id=None, table=t_user_invitation
):
    q = Query.from_(table)
    if _id is not None:
        return q.where(table._id == _id)
    else:
        assert user_id is not None
        assert organization_id is not None or organization is not None
        _q_organization = (
            organization
            if organization is not None
            else q_organization_internal_id(organization_id)
        )
        return q.where((table.organization == _q_organization) & (table.user_id == user_id))


def q_device_invitation(
    organization_id=None, organization=None, device_id=None, _id=None, table=t_device_invitation
):
    q = Query.from_(table)
    if _id is not None:
        return q.where(table._id == _id)
    else:
        assert device_id is not None
        assert organization_id is not None or organization is not None
        _q_organization = (
            organization
            if organization is not None
            else q_organization_internal_id(organization_id)
        )
        return q.where((table.organization == _q_organization) & (table.device_id == device_id))


def q_insert_user_invitation(
    user_id, created_on, organization=None, organization_id=None, creator=None, creator_id=None
):
    assert organization is not None or organization_id is not None
    assert creator is not None or creator_id is not None

    _q_organization = (
        organization if organization is not None else q_organization_internal_id(organization_id)
    )
    _q_creator = (
        creator
        if creator is not None
        else q_device_internal_id(organization=_q_organization, device_id=creator_id)
    )

    return (
        Query.into(t_user_invitation)
        .columns("organization", "creator", "user_id", "created_on")
        .insert(_q_organization, _q_creator, user_id, created_on)
        # .on_conflict(Raw("organization, user_id"))
        .on_conflict(t_user_invitation.organization)
        .do_update("organization", "excluded.organization")
        # .do_update("creator", "excluded.creator")
        # .do_update("created_on", "excluded.created_on")
    )


### Invitation ###


STR_TO_INVITATION_TYPE = {x.value: x for x in InvitationType}
STR_TO_INVITATION_STATUS = {x.value: x for x in InvitationStatus}
STR_TO_INVITATION_DELETED_REASON = {x.value: x for x in InvitationDeletedReason}
STR_TO_INVITATION_CONDUIT_STATE = {x.value: x for x in ConduitState}
STR_TO_BACKEND_EVENTS = {x.value: x for x in BackendEvent}


t_invitation = Table("invitation")


### Message ###


t_message = Table("message")


### Realm ###


t_realm = Table("realm")
t_realm_user_role = Table("realm_user_role")


STR_TO_USER_PROFILE = {profile.value: profile for profile in UserProfile}
STR_TO_REALM_ROLE = {role.value: role for role in RealmRole}
STR_TO_REALM_MAINTENANCE_TYPE = {type.value: type for type in MaintenanceType}


def q_realm(organization_id=None, organization=None, realm_id=None, _id=None, table=t_realm):
    q = Query.from_(table)
    if _id is not None:
        return q.where(table._id == _id)
    else:
        assert realm_id is not None
        assert organization_id is not None or organization is not None
        _q_organization = (
            organization
            if organization is not None
            else q_organization_internal_id(organization_id)
        )
        return q.where((table.organization == _q_organization) & (table.realm_id == realm_id))


def q_realm_internal_id(realm_id, organization_id=None, organization=None, **kwargs):
    return q_realm(
        organization_id=organization_id, organization=organization, realm_id=realm_id, **kwargs
    ).select("_id")


def q_realm_user_role(
    organization_id=None,
    organization=None,
    realm_id=None,
    realm=None,
    user_id=None,
    user=None,
    _id=None,
    table=t_realm_user_role,
):
    q = Query.from_(table)

    if _id is not None:
        return q.where(table._id == _id)

    if organization is None:
        assert organization_id is not None
        _q_organization = q_organization_internal_id(organization_id)
    else:
        _q_organization = organization

    if realm is not None:
        _q_realm = realm
    else:
        assert realm_id is not None
        assert _q_organization is not None
        _q_realm = q_realm_internal_id(organization=_q_organization, realm_id=realm_id)

    if user is not None:
        _q_user = user
    else:
        assert user_id is not None
        assert _q_organization is not None
        _q_user = q_user_internal_id(organization=_q_organization, user_id=user_id)

    return q.where((table.realm == _q_realm) & (table.user_ == _q_user))


def q_realm_in_maintenance(realm=None, realm_id=None, organization_id=None, table=t_realm):
    q = Query.from_(table).select(table.maintenance_type.notnull())
    if realm:
        q = q.where(table._id == realm)
    else:
        assert organization_id is not None and realm_id is not None
        q = q.where(
            (table.organization == q_organization_internal_id(organization_id))
            & (table.realm_id == realm_id)
        )
    return q


### Vlob ###


t_vlob_encryption_revision = Table("vlob_encryption_revision")
t_vlob_atom = Table("vlob_atom")
t_realm_vlob_update = Table("realm_vlob_update")


def q_user_can_read_vlob(user=None, user_id=None, realm=None, realm_id=None, organization_id=None):
    if user is None:
        assert organization_id is not None and user_id is not None
        _q_user = (
            user
            if user is not None
            else q_user_internal_id(organization_id=organization_id, user_id=user_id)
        )
    else:
        _q_user = user

    if realm is None:
        assert organization_id is not None and realm_id is not None
        _q_realm = (
            realm
            if realm is not None
            else q_realm_internal_id(organization_id=organization_id, realm_id=realm_id)
        )
    else:
        _q_realm = realm

    return fn_exists(
        Query.from_(t_realm_user_role)
        .where((t_realm_user_role.realm == _q_realm) & (t_realm_user_role.user_ == _q_user))
        .limit(1)
    )


def q_user_can_write_vlob(user, realm):
    return fn_exists(
        Query.from_(t_realm_user_role)
        .where(
            (t_realm_user_role.realm == realm)
            & (t_realm_user_role.user_ == user)
            & (t_realm_user_role.role != "READER")
        )
        .limit(1)
    )


def q_vlob_atom(organization_id=None, organization=None, vlob_id=None, _id=None):
    q = Query.from_(t_vlob_atom)
    if _id is not None:
        return q.where(t_vlob_atom._id == _id)
    else:
        assert vlob_id is not None
        assert organization_id is not None or organization is not None
        _q_organization = (
            organization
            if organization is not None
            else q_organization_internal_id(organization_id)
        )
        return q.where(
            (t_vlob_atom.organization == _q_organization) & (t_vlob_atom.vlob_id == vlob_id)
        )


def q_vlob_atom_internal_id(vlob_id, organization_id=None, organization=None, table=t_vlob_atom):
    q = Query.from_(t_vlob_atom).select("_id")
    assert organization_id is not None or organization is not None
    _q_organization = (
        organization if organization is not None else q_organization_internal_id(organization_id)
    )
    return q.where((t_vlob_atom.organization == _q_organization) & (t_vlob_atom.vlob_id == vlob_id))


def q_vlob_encryption_revision_internal_id(
    encryption_revision,
    organization_id=None,
    organization=None,
    realm_id=None,
    realm=None,
    table=t_vlob_encryption_revision,
):
    q = Query.from_(table).select("_id")
    if realm is None:
        assert realm_id is not None
        assert organization_id is not None or organization is not None
        if organization is None:
            _q_realm = q_realm_internal_id(organization_id=organization_id, realm_id=realm_id)
        else:
            _q_realm = q_realm_internal_id(organization=organization, realm_id=realm_id)
    else:
        _q_realm = realm
    return q.where((table.realm == _q_realm) & (table.encryption_revision == encryption_revision))


### Block ###


t_block = Table("block")
t_block_data = Table("block_data")


def q_block(organization_id=None, organization=None, block_id=None, _id=None):
    q = Query.from_(t_block)
    if _id is not None:
        return q.where(t_block._id == _id)
    else:
        assert block_id is not None
        assert organization_id is not None or organization is not None
        _q_organization = (
            organization
            if organization is not None
            else q_organization_internal_id(organization_id)
        )
        return q.where((t_block.organization == _q_organization) & (t_block.block_id == block_id))


def q_block_internal_id(block_id, organization_id=None, organization=None):
    return q_block(
        organization_id=organization_id, organization=organization, block_id=block_id
    ).select("_id")


def q_insert_block(
    block_id,
    size,
    created_on,
    organization=None,
    organization_id=None,
    realm=None,
    realm_id=None,
    author=None,
    author_id=None,
):
    assert organization is not None or organization_id is not None
    assert realm is not None or realm_id is not None
    assert author is not None or author_id is not None

    _q_organization = (
        organization if organization is not None else q_organization_internal_id(organization_id)
    )
    _q_realm = (
        realm
        if realm is not None
        else q_realm_internal_id(organization=_q_organization, realm_id=realm_id)
    )
    _q_author = (
        author
        if author is not None
        else q_device_internal_id(organization=_q_organization, device_id=author_id)
    )

    return (
        Query.into(t_block)
        .columns("organization", "block_id", "realm", "author", "size", "created_on")
        .insert(_q_organization, block_id, _q_realm, _q_author, size, created_on)
    )
