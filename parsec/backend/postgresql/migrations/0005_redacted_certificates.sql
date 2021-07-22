-- Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS


-------------------------------------------------------
--  Migration
-------------------------------------------------------

ALTER TABLE user_ ADD redacted_user_certificate BYTEA;
UPDATE user_ SET redacted_user_certificate = user_certificate;
ALTER TABLE user_ ALTER COLUMN redacted_user_certificate SET NOT NULL;

CREATE TYPE user_profile AS ENUM ('ADMIN', 'STANDARD', 'OUTSIDER');
ALTER TABLE user_ ADD profile user_profile;
UPDATE user_ SET profile = (CASE WHEN is_admin IS TRUE THEN 'ADMIN' ELSE 'STANDARD' END)::user_profile;
ALTER TABLE user_ DROP COLUMN is_admin;

ALTER TABLE device ADD redacted_device_certificate BYTEA;
UPDATE device SET redacted_device_certificate = device_certificate;
ALTER TABLE device ALTER COLUMN redacted_device_certificate SET NOT NULL;

ALTER TABLE device ADD device_label VARCHAR(254);
