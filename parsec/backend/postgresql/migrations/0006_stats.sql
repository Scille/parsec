CREATE TABLE device_realm_stats(
    _id SERIAL PRIMARY KEY,
    organization INTEGER REFERENCES organization (_id) NOT NULL,
    user_ INTEGER REFERENCES user_ (_id) NOT NULL,
    device INTEGER REFERENCES device (_id) NOT NULL,
    realm INTEGER REFERENCES realm (_id) NOT NULL,
    vlobs_size INTEGER NOT NULL,
    vlobs_count INTEGER NOT NULL,
    blocks_size INTEGER NOT NULL,
    blocks_count INTEGER NOT NULL,

    UNIQUE(device, realm)
);


-- TODO: initialize device_realm_stats table

-- CREATE TABLE device_last_connection (
--     device INTEGER REFERENCES device (_id) NOT NULL,
--     last_connected_on TIMESTAMPTZ NOT NULL
-- );
ALTER TABLE device ADD last_connected_on TIMESTAMPTZ;
UPDATE device SET last_connected_on = now();
ALTER TABLE device ALTER COLUMN last_connected_on SET NOT NULL;
