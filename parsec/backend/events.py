# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from functools import partial

import trio

from parsec.api.protocol import APIEvent, events_listen_serializer, events_subscribe_serializer
from parsec.backend.backend_events import BackendEvent
from parsec.backend.realm import BaseRealmComponent
from parsec.backend.utils import api, catch_protocol_errors, run_with_breathing_transport


class EventsComponent:
    def __init__(self, realm_component: BaseRealmComponent):
        self._realm_component = realm_component

    @api("events_subscribe")
    @catch_protocol_errors
    async def api_events_subscribe(self, client_ctx, msg):
        msg = events_subscribe_serializer.req_load(msg)

        def _on_roles_updated(event, backend_event, organization_id, author, realm_id, user, role):
            if organization_id != client_ctx.organization_id or user != client_ctx.user_id:
                return

            if role is None:
                client_ctx.realms.discard(realm_id)
            else:
                client_ctx.realms.add(realm_id)

            # Note for this event we don't filter out the ones sent by the client's
            # device, there is two reason for this:
            # 1) A user cannot change it own role, so this case should never occur
            # 2) Returning this event inform the peer we are ready to send it
            #    `realm.vlobs_updated` events on this realm (especially useful during tests)
            try:
                client_ctx.send_events_channel.send_nowait(
                    {"event": event, "realm_id": realm_id, "role": role}
                )
            except trio.WouldBlock:
                client_ctx.logger.warning(f"event queue is full for {client_ctx}")

        def _on_pinged(event, backend_event, organization_id, author, ping):
            if organization_id != client_ctx.organization_id or author == client_ctx.device_id:
                return

            try:
                client_ctx.send_events_channel.send_nowait({"event": event, "ping": ping})
            except trio.WouldBlock:
                client_ctx.logger.warning(f"event queue is full for {client_ctx}")

        def _on_realm_events(event, backend_event, organization_id, author, realm_id, **kwargs):
            if (
                organization_id != client_ctx.organization_id
                or author == client_ctx.device_id
                or realm_id not in client_ctx.realms
            ):
                return

            try:
                client_ctx.send_events_channel.send_nowait(
                    {"event": event, "realm_id": realm_id, **kwargs}
                )
            except trio.WouldBlock:
                client_ctx.logger.warning(f"event queue is full for {client_ctx}")

        def _on_message_received(event, backend_event, organization_id, author, recipient, index):
            if organization_id != client_ctx.organization_id or recipient != client_ctx.user_id:
                return

            try:
                client_ctx.send_events_channel.send_nowait({"event": event, "index": index})
            except trio.WouldBlock:
                client_ctx.logger.warning(f"event queue is full for {client_ctx}")

        def _on_invite_status_changed(
            event, backend_event, organization_id, greeter, token, status
        ):
            print("EVENT STATUS CHANGED ====>", organization_id, greeter, token, status)
            if organization_id != client_ctx.organization_id or greeter != client_ctx.user_id:
                return

            try:
                client_ctx.send_events_channel.send_nowait(
                    {"event": event, "token": token, "invitation_status": status}
                )
            except trio.WouldBlock:
                client_ctx.logger.warning(f"event queue is full for {client_ctx}")

        # Drop previous event callbacks if any
        client_ctx.event_bus_ctx.clear()

        # Connect the new callbacks
        client_ctx.event_bus_ctx.connect(BackendEvent.PINGED, partial(_on_pinged, APIEvent.PINGED))
        client_ctx.event_bus_ctx.connect(
            BackendEvent.REALM_VLOBS_UPDATED,
            partial(_on_realm_events, APIEvent.REALM_VLOBS_UPDATED),
        )
        client_ctx.event_bus_ctx.connect(
            BackendEvent.REALM_MAINTENANCE_STARTED,
            partial(_on_realm_events, APIEvent.REALM_MAINTENANCE_STARTED),
        )
        client_ctx.event_bus_ctx.connect(
            BackendEvent.REALM_MAINTENANCE_FINISHED,
            partial(_on_realm_events, APIEvent.REALM_MAINTENANCE_FINISHED),
        )
        client_ctx.event_bus_ctx.connect(
            BackendEvent.MESSAGE_RECEIVED, partial(_on_message_received, APIEvent.MESSAGE_RECEIVED)
        )
        client_ctx.event_bus_ctx.connect(
            BackendEvent.INVITE_STATUS_CHANGED,
            partial(_on_invite_status_changed, APIEvent.INVITE_STATUS_CHANGED),
        )

        # Final event to keep up to date the list of realm we should listen on
        client_ctx.event_bus_ctx.connect(
            BackendEvent.REALM_ROLES_UPDATED,
            partial(_on_roles_updated, APIEvent.REALM_ROLES_UPDATED),
        )

        # Finally populate the list of realm we should listen on
        realms_for_user = await self._realm_component.get_realms_for_user(
            client_ctx.organization_id, client_ctx.user_id
        )
        client_ctx.realms = set(realms_for_user.keys())

        return events_subscribe_serializer.rep_dump({"status": "ok"})

    @api("events_listen")
    @catch_protocol_errors
    async def api_events_listen(self, client_ctx, msg):
        msg = events_listen_serializer.req_load(msg)

        if msg["wait"]:
            event_data = await run_with_breathing_transport(
                client_ctx.transport, client_ctx.receive_events_channel.receive
            )

            if not event_data:
                return {"status": "cancelled", "reason": "Client cancelled the listening"}

        else:
            try:
                event_data = client_ctx.receive_events_channel.receive_nowait()
            except trio.WouldBlock:
                return {"status": "no_events"}

        return events_listen_serializer.rep_dump({"status": "ok", **event_data})
