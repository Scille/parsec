-- Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS


-------------------------------------------------------
--  Migration
-------------------------------------------------------

ALTER TABLE organization ADD user_profile_outsider_allowed BOOLEAN;
UPDATE organization SET user_profile_outsider_allowed = FALSE;
ALTER TABLE organization ALTER COLUMN user_profile_outsider_allowed SET NOT NULL;
