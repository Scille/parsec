-- Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS


-------------------------------------------------------
--  Migration
-------------------------------------------------------


CREATE TYPE invitation_type AS ENUM ('USER', 'DEVICE');
CREATE TYPE invitation_deleted_reason AS ENUM ('FINISHED', 'CANCELLED', 'ROTTEN');
CREATE TYPE invitation_conduit_state AS ENUM (
    '1_WAIT_PEERS',
    '2_1_CLAIMER_HASHED_NONCE',
    '2_2_GREETER_NONCE',
    '2_3_CLAIMER_NONCE',
    '3_1_CLAIMER_TRUST',
    '3_2_GREETER_TRUST',
    '4_COMMUNICATE'
);

CREATE TABLE invitation (
    _id SERIAL PRIMARY KEY,
    organization INTEGER REFERENCES organization (_id) NOT NULL,
    token UUID NOT NULL,
    type invitation_type NOT NULL,

    greeter INTEGER REFERENCES user_ (_id) NOT NULL,
    -- greeter_human INTEGER REFERENCES human (_id),
    claimer_email VARCHAR(255),  -- Required for when type=USER
    created_on TIMESTAMPTZ NOT NULL,

    deleted_on TIMESTAMPTZ,
    deleted_reason invitation_deleted_reason,

    conduit_state invitation_conduit_state NOT NULL DEFAULT '1_WAIT_PEERS',
    conduit_greeter_payload BYTEA,
    conduit_claimer_payload BYTEA,

    UNIQUE(organization, token)
);
