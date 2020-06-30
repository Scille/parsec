# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

"""
This `open_service_nursery` implementation is taken from @oremanj gist:
https://gist.github.com/oremanj/8c137d7b1f820d441fbd32fb584e06fd
"""

import collections
import weakref
from functools import partial
from typing import Any, AsyncIterator, Awaitable, Callable, MutableSet, Optional

import attr
import trio
from async_generator import asynccontextmanager


@attr.s(cmp=False)
class MultiCancelScope:
    """Manages a dynamic set of :class:`trio.CancelScope`s that can be
    shielded and cancelled as a unit.
    New cancel scopes are added to the managed set using
    :meth:`open_child`, which returns the child scope so you can enter
    it with a ``with`` statement. Calls to :meth:`cancel` and changes
    to :attr:`shield` apply to all existing children and set the
    initial state for future children. Each child scope has its own
    :attr:`~trio.CancelScope.deadline` and :attr:`~trio.CancelScope.shield`
    attributes; changes to these do not modify the parent.
    There is no :attr:`~trio.CancelScope.cancelled_caught` attribute
    on :class:`MultiCancelScope` because it would be ambiguous; some
    of the child scopes might exit via a :exc:`trio.Cancelled`
    exception and others not. Look at the child :attr:`trio.CancelScope`
    if you want to see whether it was cancelled or not.
    """

    _child_scopes: MutableSet[trio.CancelScope] = attr.ib(factory=weakref.WeakSet, init=False)
    _shield: bool = attr.ib(default=False, kw_only=True)
    _cancel_called: bool = attr.ib(default=False, kw_only=True)

    @property
    def cancel_called(self) -> bool:
        """Returns true if :meth:`cancel` has been called."""
        return self._cancel_called

    @property
    def shield(self) -> bool:
        """The overall shielding state for this :class:`MultiCancelScope`.
        Setting this attribute sets the :attr:`~trio.CancelScope.shield`
        attribute of all children, as well as the default initial shielding
        for future children. Individual children may modify their
        shield state to be different from the parent value, but further
        changes to the parent :attr:`MultiCancelScope.shield` will override
        their local choice.
        """
        return self._shield

    @shield.setter
    def shield(self, new_value: bool) -> None:
        self._shield = new_value
        for scope in self._child_scopes:
            scope.shield = new_value

    def cancel(self) -> None:
        """Cancel all child cancel scopes.
        Additional children created after a call to :meth:`cancel` will
        start out in the cancelled state.
        """
        if not self._cancel_called:
            for scope in self._child_scopes:
                scope.cancel()
            self._cancel_called = True

    def open_child(self, *, shield: Optional[bool] = None) -> trio.CancelScope:
        """Return a new child cancel scope.
        The child will start out cancelled if the parent
        :meth:`cancel` method has been called. Its initial shield state
        is given by the ``shield`` argument, or by the parent's
        :attr:`shield` attribute if the ``shield`` argument is not specified.
        """
        if shield is None:
            shield = self._shield
        new_scope = trio.CancelScope(shield=shield)
        if self._cancel_called:
            new_scope.cancel()
        self._child_scopes.add(new_scope)
        return new_scope


def _get_coroutine_or_flag_problem(
    async_fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any
) -> Awaitable[Any]:
    """Call async_fn(*args) to produce and return a coroutine. If that
    doesn't work or doesn't produce a coroutine, try to get help
    from trio in describing what went wrong.
    """
    try:
        # can we call it?
        coro = async_fn(*args, **kwargs)
    except TypeError:
        probe_fn = async_fn
    else:
        # did we get a coroutine object back?
        if isinstance(coro, collections.abc.Coroutine):
            return coro
        probe_fn = partial(async_fn, **kwargs)

    # TODO: upstream a change that lets us access just the nice
    # error detection logic without running the risk of starting a task

    # If we're not happy with this async_fn, trio won't be either,
    # and will tell us why in much greater detail.
    try:
        trio.hazmat.spawn_system_task(probe_fn, *args)
    except TypeError as ex:
        problem_with_async_fn = ex
    else:
        # we started the task successfully, wtf?
        raise trio.TrioInternalError(
            "tried to spawn a dummy task to figure out what was wrong with "
            "{async_fn!r} as an async function, but it seems to have started "
            "successfully -- all bets are off at this point"
        )
    raise problem_with_async_fn


@asynccontextmanager
async def open_service_nursery() -> AsyncIterator:
    """Provides a nursery augmented with a cancellation ordering constraint.
    If an entire service nursery becomes cancelled, either due to an
    exception raised by some task in the nursery or due to the
    cancellation of a scope that surrounds the nursery, the body of
    the nursery ``async with`` block will receive the cancellation
    first, and no other tasks in the nursery will be cancelled until
    the body of the ``async with`` block has been exited.
    This is intended to support the common pattern where the body of
    the ``async with`` block uses some service that the other
    task(s) in the nursery provide. For example, if you have::
        async with open_websocket(host, port) as conn:
            await communicate_with_websocket(conn)
    where ``open_websocket()`` enters a nursery and spawns some tasks
    into that nursery to manage the connection, you probably want
    ``conn`` to remain usable in any ``finally`` or ``__aexit__``
    blocks in ``communicate_with_websocket()``.  With a regular
    nursery, this is not guaranteed; with a service nursery, it is.
    Child tasks spawned using ``start()`` gain their protection from
    premature cancellation only at the point of their call to
    ``task_status.started()``.
    """

    async with trio.open_nursery() as nursery:
        child_task_scopes = MultiCancelScope(shield=True)

        def start_soon(
            async_fn: Callable[..., Awaitable[Any]], *args: Any, name: Optional[str] = None
        ) -> None:
            async def wrap_child(coro: Awaitable[Any]) -> None:
                with child_task_scopes.open_child():
                    await coro

            coro = _get_coroutine_or_flag_problem(async_fn, *args)
            type(nursery).start_soon(nursery, wrap_child, coro, name=name or async_fn)

        async def start(
            async_fn: Callable[..., Awaitable[Any]], *args: Any, name: Optional[str] = None
        ) -> Any:
            async def wrap_child(*, task_status) -> None:
                # For start(), the child doesn't get shielded until it
                # calls task_status.started().
                shield_scope = child_task_scopes.open_child(shield=False)

                def wrap_started(value: object = None) -> None:
                    type(task_status).started(task_status, value)
                    if trio.hazmat.current_task().parent_nursery is not nursery:
                        # started() didn't move the task due to a cancellation,
                        # so it doesn't get the shield
                        return
                    shield_scope.shield = child_task_scopes.shield

                task_status.started = wrap_started  # type: ignore
                with shield_scope:
                    await async_fn(*args, task_status=task_status)

            return await type(nursery).start(nursery, wrap_child, name=name or async_fn)

        nursery.start_soon = start_soon  # type: ignore
        nursery.start = start  # type: ignore
        nursery.child_task_scopes = child_task_scopes
        try:
            yield nursery
        finally:
            child_task_scopes.shield = False
