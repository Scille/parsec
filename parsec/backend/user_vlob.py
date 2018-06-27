import attr

from parsec.utils import to_jsonb64
from parsec.schema import _BaseCmdSchema, fields


@attr.s
class UserVlobAtom:
    user_id = attr.ib()
    version = attr.ib(default=0)
    blob = attr.ib(default=b"")


class _cmd_READ_Schema(_BaseCmdSchema):
    version = fields.Integer(validate=lambda n: n >= 0)


class _cmd_UPDATE_Schema(_BaseCmdSchema):
    version = fields.Integer(validate=lambda n: n > 0)
    blob = fields.Base64Bytes(required=True)


cmd_READ_Schema = _cmd_READ_Schema()
cmd_UPDATE_Schema = _cmd_UPDATE_Schema()


class BaseUserVlobComponent:
    def __init__(self, signal_ns):
        self._signal_user_vlob_updated = signal_ns.signal("user_vlob_updated")

    async def api_user_vlob_read(self, client_ctx, msg):
        msg = cmd_READ_Schema.load_or_abort(msg)
        atom = await self.read(client_ctx.user_id, **msg)
        return {"status": "ok", "blob": to_jsonb64(atom.blob), "version": atom.version}

    async def api_user_vlob_update(self, client_ctx, msg):
        msg = cmd_UPDATE_Schema.load_or_abort(msg)
        await self.update(client_ctx.user_id, **msg)
        return {"status": "ok"}

    async def read(self, id, version):
        raise NotImplementedError()

    async def update(self, id, version, blob):
        raise NotImplementedError()
