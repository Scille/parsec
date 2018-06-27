from parsec.schema import _BaseCmdSchema, fields
from parsec.core.app import Core, ClientContext
from parsec.core.sharing import (
    SharingUnknownRecipient,
    SharingInvalidRecipient,
    SharingBackendMessageError,
)


class _cmd_SHARE_Schema(_BaseCmdSchema):
    path = fields.String(required=True)
    recipient = fields.String(required=True)


cmd_SHARE_Schema = _cmd_SHARE_Schema()


async def share(req: dict, client_ctx: ClientContext, core: Core) -> dict:
    if not core.auth_device:
        return {"status": "login_required", "reason": "Login required"}

    req = cmd_SHARE_Schema.load(req)
    try:
        await core.sharing.share(req["path"], req["recipient"])
    except SharingInvalidRecipient as exc:
        return {"status": "invalid_recipient", "reason": str(exc)}
    except SharingUnknownRecipient as exc:
        return {"status": "unknown_recipient", "reason": str(exc)}
    except SharingBackendMessageError as exc:
        return {"status": "backend_error", "reason": str(exc)}
    return {"status": "ok"}
