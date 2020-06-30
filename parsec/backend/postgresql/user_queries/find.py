# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import itertools
from functools import lru_cache
from typing import List, Tuple

from pendulum import now as pendulum_now
from pypika import Parameter
from pypika import PostgreSQLQuery as Query
from pypika.functions import Concat

from parsec.api.protocol import HumanHandle, OrganizationID, UserID
from parsec.backend.postgresql.tables import q_organization_internal_id, t_human, t_user
from parsec.backend.postgresql.utils import IRegex, query
from parsec.backend.user import HumanFindResultItem


@lru_cache()
def _q_factory(query, omit_revoked):
    _param_count = itertools.count(1)

    def _next_param():
        return Parameter(f"${next(_param_count)}")

    q = (
        Query.from_(t_user)
        .select(t_user.user_id)
        .where((t_user.organization == q_organization_internal_id(_next_param())))
        .orderby(t_user.user_id)
    )
    if query:
        q = q.where(IRegex(t_user.user_id, _next_param()))
    if omit_revoked:
        q = q.where(t_user.revoked_on.isnull() | t_user.revoked_on > _next_param())

    return q.get_sql()


@lru_cache()
def _q_human_factory(query, omit_revoked, omit_non_human):
    _param_count = itertools.count(1)

    def _next_param():
        return Parameter(f"${next(_param_count)}")

    q_revoked = t_user.revoked_on.notnull() & (t_user.revoked_on <= _next_param())
    q = (
        Query.from_(t_user)
        .left_join(t_human)
        .on(t_user.human == t_human._id)
        .select(t_user.user_id, t_human.email, t_human.label, q_revoked.as_("revoked"))
        .where((t_user.organization == q_organization_internal_id(_next_param())))
    )
    if query:
        q = q.where(IRegex(Concat(t_human.label, t_human.email, t_user.user_id), _next_param()))
    if omit_revoked:
        q = q.where(q_revoked.negate())
    if omit_non_human:
        q = q.where(t_user.human.notnull())

    return q.get_sql()


@query()
async def query_find(
    conn, organization_id: OrganizationID, query: str, page: int, per_page: int, omit_revoked: bool
) -> Tuple[List[UserID], int]:
    if query:
        try:
            UserID(query)
        except ValueError:
            # Contains invalid caracters, no need to go further
            return ([], 0)

        if omit_revoked:
            q = _q_factory(query=True, omit_revoked=True)
            args = (organization_id, query, pendulum_now())

        else:
            q = _q_factory(query=True, omit_revoked=False)
            args = (organization_id, query)

    else:

        if omit_revoked:
            q = _q_factory(query=False, omit_revoked=True)
            args = (organization_id, pendulum_now())

        else:
            q = _q_factory(query=False, omit_revoked=False)
            args = (organization_id,)

    all_results = await conn.fetch(q, *args)
    # TODO: should user LIMIT and OFFSET in the SQL query instead
    results = [UserID(x[0]) for x in all_results[(page - 1) * per_page : page * per_page]]
    return results, len(all_results)


@query()
async def query_find_humans(
    conn,
    organization_id: OrganizationID,
    query: str,
    page: int,
    per_page: int,
    omit_revoked: bool,
    omit_non_human: bool,
) -> Tuple[List[HumanFindResultItem], int]:
    if query:

        if omit_revoked:
            q = _q_human_factory(query=True, omit_revoked=True, omit_non_human=omit_non_human)
            args = (pendulum_now(), organization_id, query)

        else:
            q = _q_human_factory(query=True, omit_revoked=False, omit_non_human=omit_non_human)
            args = (pendulum_now(), organization_id, query)

    else:

        if omit_revoked:
            q = _q_human_factory(query=False, omit_revoked=True, omit_non_human=omit_non_human)
            args = (pendulum_now(), organization_id)

        else:
            q = _q_human_factory(query=False, omit_revoked=False, omit_non_human=omit_non_human)
            args = (pendulum_now(), organization_id)

    raw_results = await conn.fetch(q, *args)

    humans = [
        HumanFindResultItem(
            user_id=UserID(user_id),
            human_handle=HumanHandle(email=email, label=label),
            revoked=revoked,
        )
        for user_id, email, label, revoked in raw_results
        if email is not None
    ]
    non_humans = [
        HumanFindResultItem(user_id=UserID(user_id), human_handle=None, revoked=revoked)
        for user_id, email, label, revoked in raw_results
        if email is None
    ]
    results = [
        *sorted(humans, key=lambda x: (x.human_handle.label.lower(), x.user_id.lower())),
        *sorted(non_humans, key=lambda x: x.user_id.lower()),
    ]
    # TODO: should user LIMIT and OFFSET in the SQL query instead
    return results[(page - 1) * per_page : page * per_page], len(results)
