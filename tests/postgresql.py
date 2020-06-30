# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import asyncio
import atexit
import os

import asyncpg
from asyncpg.cluster import TempCluster

from parsec.backend.postgresql.handler import _apply_migrations, retrieve_migrations


def _patch_url_if_xdist(url):
    xdist_worker = os.environ.get("PYTEST_XDIST_WORKER")
    if xdist_worker:
        return f"{url}_{xdist_worker}"
    else:
        return url


_pg_db_url = None


async def run_migrations(conn) -> None:
    result = await _apply_migrations(conn, retrieve_migrations(), dry_run=False)
    if result.error:
        migration, msg = result.error
        raise RuntimeError(f"Error while applying migration {migration.file_name}: {msg}")


async def _execute_pg_query(url, query):
    conn = await asyncpg.connect(url)
    if callable(query):
        await query(conn)
    else:
        await conn.execute(query)
    await conn.close()


def bootstrap_postgresql_testbed():
    global _pg_db_url

    provided_db = os.environ.get("PG_URL")
    if not provided_db:
        print("Creating PostgreSQL cluster...")
        _pg_db_url = bootstrap_pg_cluster()

    else:
        _pg_db_url = _patch_url_if_xdist(provided_db)

    print("PostgreSQL url: ", _pg_db_url)
    # Finally initialiaze the database
    # In theory we should use TrioPG here to do db init, but:
    # - Duck typing and similar api makes `_init_db` compatible with both
    # - AsyncPG should be slightly faster than TrioPG
    # - Most important: a trio loop is potentially already started inside this
    #   thread (i.e. if the test is mark as trio). Hence we would have to spawn
    #   another thread just to run the new trio loop.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_execute_pg_query(_pg_db_url, run_migrations))
    return _pg_db_url


async def asyncio_reset_postgresql_testbed():
    await _execute_pg_query(
        _pg_db_url,
        """
TRUNCATE TABLE
    organization,

    user_,
    device,
    user_invitation,
    device_invitation,

    message,

    realm,
    realm_user_role,
    vlob_encryption_revision,
    vlob_atom,
    realm_vlob_update,

    block,
    block_data
RESTART IDENTITY CASCADE
""",
    )


def reset_postgresql_testbed():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(asyncio_reset_postgresql_testbed())


def get_postgresql_url():
    return _pg_db_url


_pg_cluster = None


def bootstrap_pg_cluster():
    global _pg_cluster

    if _pg_cluster:
        raise RuntimeError("Postgresql cluster already bootstrapped !")

    _pg_cluster = TempCluster()
    # Make the default superuser name stable.
    _pg_cluster.init(username="postgres")
    _pg_cluster.trust_local_connections()
    _pg_cluster.start(port="dynamic", server_settings={})

    def _shutdown_pg_cluster():
        if _pg_cluster.get_status() == "running":
            _pg_cluster.stop()
        if _pg_cluster.get_status() != "not-initialized":
            _pg_cluster.destroy()

    atexit.register(_shutdown_pg_cluster)
    spec = _pg_cluster.get_connection_spec()
    return f"postgresql://postgres@localhost:{spec['port']}/postgres"
