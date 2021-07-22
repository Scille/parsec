-- Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS


-------------------------------------------------------
--  Organization
-------------------------------------------------------


CREATE TABLE organization (
    _id SERIAL PRIMARY KEY,
    organization_id VARCHAR(32) UNIQUE NOT NULL,
    bootstrap_token TEXT NOT NULL,
    root_verify_key BYTEA,
    expiration_date TIMESTAMPTZ
);


-------------------------------------------------------
--  User
-------------------------------------------------------


CREATE TABLE user_ (
    _id SERIAL PRIMARY KEY,
    organization INTEGER REFERENCES organization (_id) NOT NULL,
    user_id VARCHAR(32) NOT NULL,
    is_admin BOOLEAN NOT NULL,
    user_certificate BYTEA NOT NULL,
    -- NULL if certifier is the Root Verify Key
    user_certifier INTEGER,
    created_on TIMESTAMPTZ NOT NULL,
    -- NULL if not yet revoked
    revoked_on TIMESTAMPTZ,
    -- NULL if not yet revoked
    revoked_user_certificate BYTEA,
    -- NULL if certifier is the Root Verify Key
    revoked_user_certifier INTEGER,

    UNIQUE(organization, user_id)
);


CREATE TABLE device (
    _id SERIAL PRIMARY KEY,
    organization INTEGER REFERENCES organization (_id) NOT NULL,
    user_ INTEGER REFERENCES user_ (_id) NOT NULL,
    device_id VARCHAR(65) NOT NULL,
    device_certificate BYTEA NOT NULL,
    -- NULL if certifier is the Root Verify Key
    device_certifier INTEGER REFERENCES device (_id),
    created_on TIMESTAMPTZ NOT NULL,

    UNIQUE(organization, device_id),
    UNIQUE(user_, device_id)
);


ALTER TABLE user_
ADD CONSTRAINT FK_user_device_user_certifier FOREIGN KEY (user_certifier) REFERENCES device (_id);
ALTER TABLE user_
ADD CONSTRAINT FK_user_device_revoked_user_certifier FOREIGN KEY (revoked_user_certifier) REFERENCES device (_id);


CREATE TABLE user_invitation (
    _id SERIAL PRIMARY KEY,
    organization INTEGER REFERENCES organization (_id) NOT NULL,
    user_id VARCHAR(32) NOT NULL,
    creator INTEGER REFERENCES device (_id) NOT NULL,
    created_on TIMESTAMPTZ NOT NULL,

    UNIQUE(organization, user_id)
);


CREATE TABLE device_invitation (
    _id SERIAL PRIMARY KEY,
    organization INTEGER REFERENCES organization (_id) NOT NULL,
    device_id VARCHAR(65) NOT NULL,
    creator INTEGER REFERENCES device (_id) NOT NULL,
    created_on TIMESTAMPTZ NOT NULL,

    UNIQUE(organization, device_id)
);


-------------------------------------------------------
--  Message
-------------------------------------------------------


CREATE TABLE message (
    _id SERIAL PRIMARY KEY,
    organization INTEGER REFERENCES organization (_id) NOT NULL,
    recipient INTEGER REFERENCES user_ (_id) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    index INTEGER NOT NULL,
    sender INTEGER REFERENCES device (_id) NOT NULL,
    body BYTEA NOT NULL
);


-------------------------------------------------------
--  Realm
-------------------------------------------------------


CREATE TYPE maintenance_type AS ENUM ('REENCRYPTION', 'GARBAGE_COLLECTION');


CREATE TABLE realm (
    _id SERIAL PRIMARY KEY,
    organization INTEGER REFERENCES organization (_id) NOT NULL,
    realm_id UUID NOT NULL,
    encryption_revision INTEGER NOT NULL,
    -- NULL if not currently in maintenance
    maintenance_started_by INTEGER REFERENCES device (_id),
    maintenance_started_on TIMESTAMPTZ,
    maintenance_type maintenance_type,

    UNIQUE(organization, realm_id)
);


CREATE TYPE realm_role AS ENUM ('OWNER', 'MANAGER', 'CONTRIBUTOR', 'READER');


CREATE TABLE realm_user_role (
    _id SERIAL PRIMARY KEY,
    realm INTEGER REFERENCES realm (_id) NOT NULL,
    user_ INTEGER REFERENCES user_ (_id) NOT NULL,
    -- NULL if access revocation
    role realm_role,
    certificate BYTEA NOT NULL,
    certified_by INTEGER REFERENCES device(_id) NOT NULL,
    certified_on TIMESTAMPTZ NOT NULL
);


-------------------------------------------------------
--  Vlob
-------------------------------------------------------


CREATE TABLE vlob_encryption_revision (
    _id SERIAL PRIMARY KEY,
    realm INTEGER REFERENCES realm (_id),
    encryption_revision INTEGER NOT NULL,

    UNIQUE(realm, encryption_revision)
);


CREATE TABLE vlob_atom (
    _id SERIAL PRIMARY KEY,
    organization INTEGER REFERENCES organization (_id) NOT NULL,
    vlob_encryption_revision INTEGER REFERENCES vlob_encryption_revision (_id) NOT NULL,
    vlob_id UUID NOT NULL,
    version INTEGER NOT NULL,
    blob BYTEA NOT NULL,
    size INTEGER NOT NULL,
    author INTEGER REFERENCES device (_id) NOT NULL,
    created_on TIMESTAMPTZ NOT NULL,
    -- NULL if not deleted
    deleted_on TIMESTAMPTZ,

    UNIQUE(vlob_encryption_revision, vlob_id, version)
);


CREATE TABLE realm_vlob_update (
    _id SERIAL PRIMARY KEY,
    realm INTEGER REFERENCES realm (_id) NOT NULL,
    index INTEGER NOT NULL,
    vlob_atom INTEGER REFERENCES vlob_atom (_id) NOT NULL,

    UNIQUE(realm, index)
);


-------------------------------------------------------
--  Block
-------------------------------------------------------


CREATE TABLE block (
    _id SERIAL PRIMARY KEY,
    organization INTEGER REFERENCES organization (_id) NOT NULL,
    block_id UUID NOT NULL,
    realm INTEGER REFERENCES realm (_id) NOT NULL,
    author INTEGER REFERENCES device (_id) NOT NULL,
    size INTEGER NOT NULL,
    created_on TIMESTAMPTZ NOT NULL,
    -- NULL if not deleted
    deleted_on TIMESTAMPTZ,

    UNIQUE(organization, block_id)
);


-- Only used if we store blocks' data in database
CREATE TABLE block_data (
    _id SERIAL PRIMARY KEY,
    -- No reference with organization&block tables given this table
    -- should stay isolated
    organization_id VARCHAR NOT NULL,
    block_id UUID NOT NULL,
    data BYTEA NOT NULL,

    UNIQUE(organization_id, block_id)
);
