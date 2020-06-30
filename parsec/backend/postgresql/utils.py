# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from copy import copy
from functools import wraps

from pypika import Table
from pypika.dialects import PostgreQueryBuilder, PostgreSQLQuery
from pypika.enums import Comparator, JoinType
from pypika.terms import BasicCriterion, Function, Term
from pypika.utils import builder


def query(in_transaction=False):
    if in_transaction:

        def decorator(fn):
            @wraps(fn)
            async def wrapper(conn, *args, **kwargs):
                async with conn.transaction():
                    return await fn(conn, *args, **kwargs)

            return wrapper

    else:

        def decorator(fn):
            return fn

    return decorator


def fn_exists(q):
    if not q.get_sql():
        q = q.select(True)
    return Function("EXISTS", q)


def fn_unnest(q):
    return Function("UNNEST", q)


def fn_array_cat(a, b, *cs):
    q = BasicCriterion("||", a, b)
    for c in cs:
        q = BasicCriterion("||", q, c)
    return q


def fn_array_append(a, b):
    return Function("array_append", a, b)


class AnyInList(Term):
    def __init__(self, param, item_type):
        assert isinstance(item_type, str)
        super().__init__()
        self.param = param
        self.item_type = item_type

    def fields(self):
        return []

    def get_sql(self, **kwargs):
        return f"ANY({self.param}::{self.item_type}[])"


class Matching(Comparator):
    not_like = " !~~* "
    like = " ~~* "
    not_ilike = " !~~* "
    ilike = " ~~* "
    not_regex = " !~* "
    regex = " ~* "
    not_iregex = " !~* "
    iregex = " ~* "


class IRegex(BasicCriterion):
    def __init__(self, field, regex, alias=None):
        super().__init__(Matching.iregex, field, regex, alias=alias)


class Table(Table):
    def as_(self, name):
        table = copy(self)
        table.alias = name
        return table


class Query(PostgreSQLQuery):
    @classmethod
    def _builder(cls):
        return QueryBuilder()

    @classmethod
    def with_recursive(cls, table, name):
        return cls._builder().with_recursive(table, name)


class QueryBuilder(PostgreQueryBuilder):
    @builder
    def with_recursive(self, selectable, name):
        return self.with_(selectable, f"RECURSIVE {name}")

    def left_join(self, item):
        return self.join(item, how=JoinType.left)
