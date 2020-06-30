# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote_plus, urlsplit, urlunsplit
from uuid import UUID

from marshmallow import ValidationError

from parsec.api.data import EntryID
from parsec.api.protocol import DeviceID, InvitationType, OrganizationID, UserID
from parsec.core.types.base import FsPath
from parsec.crypto import VerifyKey, export_root_verify_key, import_root_verify_key
from parsec.serde import fields

PARSEC_SCHEME = "parsec"


class BackendAddr:
    """
    Represent the URL to reach a backend
    (e.g. ``parsec://parsec.example.com/``)
    """

    __slots__ = ("_hostname", "_port", "_use_ssl")

    def __eq__(self, other):
        if isinstance(other, BackendAddr):
            return self.to_url() == other.to_url()
        else:
            return False

    def __init__(self, hostname: str, port: Optional[int] = None, use_ssl=True):
        assert not hostname.startswith("parsec://")
        self._hostname = hostname
        self._port, _ = self._parse_port(port, use_ssl)
        self._use_ssl = use_ssl

    @staticmethod
    def _parse_port(port, use_ssl) -> Tuple[int, Optional[int]]:
        """Returns port to use and port to add in netloc if needed"""
        default_port = 443 if use_ssl else 80
        if port not in (None, default_port):
            return port, port
        else:
            return default_port, None

    def __str__(self):
        return self.to_url()

    def __repr__(self):
        return f"{type(self).__name__}(url={self.to_url()})"

    @classmethod
    def from_url(cls, url: str):
        split = urlsplit(url)

        if split.scheme != PARSEC_SCHEME:
            raise ValueError(f"Must start with `{PARSEC_SCHEME}://`")

        if split.query:
            # Note `parse_qs` takes care of percent-encoding
            params = parse_qs(
                split.query,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
            )
        else:
            params = {}

        path = unquote_plus(split.path)

        kwargs = {
            **cls._from_url_parse_and_consume_params(params),
            **cls._from_url_parse_path(path),
        }

        return cls(hostname=split.hostname or "localhost", port=split.port, **kwargs)

    @classmethod
    def _from_url_parse_path(cls, path):
        if path not in ("", "/"):
            raise ValueError("Cannot have path")
        return {}

    @classmethod
    def _from_url_parse_and_consume_params(cls, params):
        value = params.pop("no_ssl", ("false",))
        if len(value) != 1:
            raise ValueError("Multiple values for param `no_ssl`")
        value = value[0].lower()
        # param is no_ssl, but we store use_ssl (so invert the values)
        if value == "false":
            return {"use_ssl": True}
        elif value == "true":
            return {"use_ssl": False}
        else:
            raise ValueError("Invalid `no_ssl` param value (must be true or false)")

    def to_url(self) -> str:
        _, custom_port = self._parse_port(self._port, self._use_ssl)
        if custom_port:
            netloc = f"{self._hostname}:{custom_port}"
        else:
            netloc = self.hostname
        query = "&".join(f"{k}={quote_plus(v)}" for k, v in self._to_url_get_params())
        return urlunsplit((PARSEC_SCHEME, netloc, quote_plus(self._to_url_get_path()), query, None))

    def _to_url_get_path(self):
        return ""

    def _to_url_get_params(self):
        # Return a list to easily manage the order of params
        return [("no_ssl", "true")] if not self._use_ssl else []

    @property
    def hostname(self):
        return self._hostname

    @property
    def port(self):
        return self._port

    @property
    def use_ssl(self):
        return self._use_ssl


class OrganizationParamsFixture(BackendAddr):
    __slots__ = ("_root_verify_key", "_organization_id")

    def __init__(self, organization_id: OrganizationID, root_verify_key: VerifyKey, **kwargs):
        super().__init__(**kwargs)
        self._organization_id = organization_id
        self._root_verify_key = root_verify_key

    @classmethod
    def _from_url_parse_path(cls, path):
        return {"organization_id": OrganizationID(path[1:])}

    @classmethod
    def _from_url_parse_and_consume_params(cls, params):
        kwargs = super()._from_url_parse_and_consume_params(params)

        value = params.pop("rvk", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `rvk` param")
        try:
            kwargs["root_verify_key"] = import_root_verify_key(value[0])
        except ValueError as exc:
            raise ValueError("Invalid `rvk` param value") from exc

        return kwargs

    def _to_url_get_path(self):
        return str(self.organization_id)

    def _to_url_get_params(self):
        return [
            *super()._to_url_get_params(),
            ("rvk", export_root_verify_key(self.root_verify_key)),
        ]

    @property
    def organization_id(self) -> OrganizationID:
        return self._organization_id

    @property
    def root_verify_key(self) -> VerifyKey:
        return self._root_verify_key


class BackendOrganizationAddr(OrganizationParamsFixture, BackendAddr):
    """
    Represent the URL to access an organization within a backend
    (e.g. ``parsec://parsec.example.com/MyOrg?rvk=7NFDS4VQLP3XPCMTSEN34ZOXKGGIMTY2W2JI2SPIHB2P3M6K4YWAssss``)
    """

    @classmethod
    def build(
        cls, backend_addr: BackendAddr, organization_id: OrganizationID, root_verify_key: VerifyKey
    ) -> "BackendOrganizationAddr":
        return cls(
            hostname=backend_addr.hostname,
            port=backend_addr.port,
            use_ssl=backend_addr.use_ssl,
            organization_id=organization_id,
            root_verify_key=root_verify_key,
        )


class BackendActionAddr(BackendAddr):
    __slots__ = ()

    @classmethod
    def from_url(cls, url: str):
        if cls is not BackendActionAddr:
            return BackendAddr.from_url.__func__(cls, url)

        else:
            for type in (
                BackendOrganizationBootstrapAddr,
                BackendOrganizationClaimUserAddr,
                BackendOrganizationClaimDeviceAddr,
                BackendOrganizationFileLinkAddr,
                BackendInvitationAddr,
            ):
                try:
                    return BackendAddr.from_url.__func__(type, url)
                except ValueError:
                    pass

            raise ValueError("Invalid URL format")


class BackendOrganizationBootstrapAddr(BackendActionAddr):
    """
    Represent the URL to bootstrap an organization within a backend
    (e.g. ``parsec://parsec.example.com/my_org?action=bootstrap_organization&token=1234ABCD``)
    """

    __slots__ = ("_organization_id", "_token")

    def __init__(self, organization_id: OrganizationID, token: str, **kwargs):
        super().__init__(**kwargs)
        self._organization_id = organization_id
        self._token = token

    @classmethod
    def _from_url_parse_path(cls, path):
        return {"organization_id": OrganizationID(path[1:])}

    @classmethod
    def _from_url_parse_and_consume_params(cls, params):
        kwargs = super()._from_url_parse_and_consume_params(params)

        value = params.pop("action", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `action` param")
        if value[0] != "bootstrap_organization":
            raise ValueError("Expected `action=bootstrap_organization` value")

        value = params.pop("token", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `token` param")
        try:
            kwargs["token"] = value[0]
        except ValueError:
            raise ValueError("Invalid `token` param value")
        if not kwargs["token"]:
            raise ValueError("Empty value in `token` param")

        return kwargs

    def _to_url_get_path(self):
        return str(self.organization_id)

    def _to_url_get_params(self):
        return [
            ("action", "bootstrap_organization"),
            ("token", self._token),
            *super()._to_url_get_params(),
        ]

    @classmethod
    def build(
        cls, backend_addr: BackendAddr, organization_id: OrganizationID, token: str
    ) -> "BackendOrganizationBootstrapAddr":
        return cls(
            hostname=backend_addr.hostname,
            port=backend_addr.port,
            use_ssl=backend_addr.use_ssl,
            organization_id=organization_id,
            token=token,
        )

    def generate_organization_addr(self, root_verify_key: VerifyKey) -> BackendOrganizationAddr:
        return BackendOrganizationAddr.build(
            backend_addr=self, organization_id=self.organization_id, root_verify_key=root_verify_key
        )

    @property
    def organization_id(self) -> OrganizationID:
        return self._organization_id

    @property
    def token(self) -> Optional[str]:
        return self._token


class BackendOrganizationClaimUserAddr(OrganizationParamsFixture, BackendActionAddr):
    """
    Represent the URL to bootstrap claim a user
    (e.g. ``parsec://parsec.example.com/my_org?action=claim_user&user_id=John&token=1234ABCD&rvk=P25GRG3XPSZKBEKXYQFBOLERWQNEDY3AO43MVNZCLPXPKN63JRYQssss``)
    """

    __slots__ = ("_user_id", "_token")

    def __init__(self, user_id: UserID, token: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._user_id = user_id
        self._token = token

    @classmethod
    def _from_url_parse_and_consume_params(cls, params):
        kwargs = super()._from_url_parse_and_consume_params(params)

        value = params.pop("action", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `action` param")
        if value[0] != "claim_user":
            raise ValueError("Expected `action=claim_user` value")

        value = params.pop("user_id", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `user_id` param")
        try:
            kwargs["user_id"] = UserID(value[0])
        except ValueError as exc:
            raise ValueError("Invalid `user_id` param value") from exc

        value = params.pop("token", ())
        if len(value) > 0:
            try:
                kwargs["token"] = value[0]
            except ValueError:
                raise ValueError("Invalid `token` param value")

        return kwargs

    def _to_url_get_params(self):
        params = [("action", "claim_user"), ("user_id", self._user_id)]
        if self._token:
            params.append(("token", self._token))
        return [*params, *super()._to_url_get_params()]

    @classmethod
    def build(
        cls,
        organization_addr: BackendOrganizationAddr,
        user_id: UserID,
        token: Optional[str] = None,
    ) -> "BackendOrganizationClaimUserAddr":
        return cls(
            hostname=organization_addr.hostname,
            port=organization_addr.port,
            use_ssl=organization_addr.use_ssl,
            organization_id=organization_addr.organization_id,
            root_verify_key=organization_addr.root_verify_key,
            user_id=user_id,
            token=token,
        )

    def to_organization_addr(self) -> BackendOrganizationAddr:
        return BackendOrganizationAddr.build(
            backend_addr=self,
            organization_id=self.organization_id,
            root_verify_key=self.root_verify_key,
        )

    @property
    def user_id(self) -> UserID:
        return self._user_id

    @property
    def token(self) -> Optional[str]:
        return self._token


class BackendOrganizationClaimDeviceAddr(OrganizationParamsFixture, BackendActionAddr):
    """
    Represent the URL to bootstrap claim a device
    (e.g. ``parsec://parsec.example.com/my_org?action=claim_device&device_id=John%40pc&token=1234ABCD&rvk=P25GRG3XPSZKBEKXYQFBOLERWQNEDY3AO43MVNZCLPXPKN63JRYQssss``)
    """

    __slots__ = ("_device_id", "_token")

    def __init__(self, device_id: DeviceID, token: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._device_id = device_id
        self._token = token

    @classmethod
    def _from_url_parse_and_consume_params(cls, params):
        kwargs = super()._from_url_parse_and_consume_params(params)

        value = params.pop("action", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `action` param")
        if value[0] != "claim_device":
            raise ValueError("Expected `action=claim_device` value")

        value = params.pop("device_id", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `device_id` param")
        try:
            kwargs["device_id"] = DeviceID(value[0])
        except ValueError as exc:
            raise ValueError("Invalid `device_id` param value") from exc

        value = params.pop("token", ())
        if len(value) > 0:
            try:
                kwargs["token"] = value[0]
            except ValueError:
                raise ValueError("Invalid `token` param value")

        return kwargs

    def _to_url_get_params(self):
        params = [("action", "claim_device"), ("device_id", self._device_id)]
        if self._token:
            params.append(("token", self._token))
        return [*params, *super()._to_url_get_params()]

    @classmethod
    def build(
        cls,
        organization_addr: BackendOrganizationAddr,
        device_id: DeviceID,
        token: Optional[str] = None,
    ) -> "BackendOrganizationClaimDeviceAddr":
        return cls(
            hostname=organization_addr.hostname,
            port=organization_addr.port,
            use_ssl=organization_addr.use_ssl,
            organization_id=organization_addr.organization_id,
            root_verify_key=organization_addr.root_verify_key,
            device_id=device_id,
            token=token,
        )

    def to_organization_addr(self) -> BackendOrganizationAddr:
        return BackendOrganizationAddr.build(
            backend_addr=self,
            organization_id=self.organization_id,
            root_verify_key=self.root_verify_key,
        )

    @property
    def device_id(self) -> DeviceID:
        return self._device_id

    @property
    def token(self) -> Optional[str]:
        return self._token


class BackendOrganizationFileLinkAddr(OrganizationParamsFixture, BackendActionAddr):
    """
    Represent the URL to share a file link
    (e.g. ``parsec://parsec.example.com/my_org?action=file_link&workspace_id=xx&path=yy``)
    """

    __slots__ = ("_workspace_id", "_path")

    def __init__(self, workspace_id: EntryID, path: FsPath, **kwargs):
        super().__init__(**kwargs)
        self._workspace_id = workspace_id
        self._path = path

    @classmethod
    def _from_url_parse_and_consume_params(cls, params):
        kwargs = super()._from_url_parse_and_consume_params(params)

        value = params.pop("action", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `action` param")
        if value[0] != "file_link":
            raise ValueError("Expected `action=file_link` value")

        value = params.pop("workspace_id", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `workspace_id` param")
        try:
            kwargs["workspace_id"] = EntryID(value[0])
        except ValueError as exc:
            raise ValueError("Invalid `workspace_id` param value") from exc

        value = params.pop("path", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `path` param")
        try:
            kwargs["path"] = FsPath(value[0])
        except ValueError as exc:
            raise ValueError("Invalid `path` param value") from exc

        return kwargs

    def _to_url_get_params(self):
        params = [
            ("action", "file_link"),
            ("workspace_id", str(self._workspace_id)),
            ("path", str(self._path)),
        ]
        return [*params, *super()._to_url_get_params()]

    @classmethod
    def build(
        cls, organization_addr: BackendOrganizationAddr, workspace_id: EntryID, path: FsPath
    ) -> "BackendOrganizationFileLinkAddr":
        return cls(
            hostname=organization_addr.hostname,
            port=organization_addr.port,
            use_ssl=organization_addr.use_ssl,
            organization_id=organization_addr.organization_id,
            root_verify_key=organization_addr.root_verify_key,
            workspace_id=workspace_id,
            path=path,
        )

    def to_organization_addr(self) -> BackendOrganizationAddr:
        return BackendOrganizationAddr.build(
            backend_addr=self,
            organization_id=self.organization_id,
            root_verify_key=self.root_verify_key,
        )

    @property
    def workspace_id(self) -> EntryID:
        return self._workspace_id

    @property
    def path(self) -> FsPath:
        return self._path


class BackendOrganizationAddrField(fields.Field):
    def _deserialize(self, value, attr, data):
        try:
            return BackendOrganizationAddr.from_url(value)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    def _serialize(self, value, attr, data):
        if value is None:
            return None

        return value.to_url()


class BackendInvitationAddr(BackendActionAddr):
    """
    Represent the URL to invite a user or a device
    (e.g. ``parsec://parsec.example.com/my_org?action=claim_user&token=1234ABCD``)
    """

    __slots__ = ("_organization_id", "_invitation_type", "_token")

    def __init__(
        self,
        organization_id: OrganizationID,
        invitation_type: InvitationType,
        token: UUID,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._organization_id = organization_id
        self._invitation_type = invitation_type
        self._token = token

    @classmethod
    def _from_url_parse_path(cls, path):
        return {"organization_id": OrganizationID(path[1:])}

    @classmethod
    def _from_url_parse_and_consume_params(cls, params):
        kwargs = super()._from_url_parse_and_consume_params(params)

        value = params.pop("action", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `action` param")
        if value[0] == "claim_user":
            kwargs["invitation_type"] = InvitationType.USER
        elif value[0] == "claim_device":
            kwargs["invitation_type"] = InvitationType.DEVICE
        else:
            raise ValueError("Expected `action=claim_user` or `action=claim_device` value")

        value = params.pop("token", ())
        if len(value) != 1:
            raise ValueError("Missing mandatory `token` param")
        try:
            kwargs["token"] = UUID(value[0])
        except ValueError:
            raise ValueError("Invalid `token` param value")

        return kwargs

    def _to_url_get_path(self):
        return str(self.organization_id)

    def _to_url_get_params(self):
        action = "claim_user" if self._invitation_type == InvitationType.USER else "claim_device"
        return [("action", action), ("token", self._token.hex), *super()._to_url_get_params()]

    @classmethod
    def build(
        cls,
        backend_addr: BackendAddr,
        organization_id: OrganizationID,
        invitation_type: InvitationType,
        token: UUID,
    ) -> "BackendInvitationAddr":
        return cls(
            hostname=backend_addr.hostname,
            port=backend_addr.port,
            use_ssl=backend_addr.use_ssl,
            organization_id=organization_id,
            invitation_type=invitation_type,
            token=token,
        )

    def generate_organization_addr(self, root_verify_key: VerifyKey) -> BackendOrganizationAddr:
        return BackendOrganizationAddr.build(
            backend_addr=self, organization_id=self.organization_id, root_verify_key=root_verify_key
        )

    @property
    def organization_id(self) -> OrganizationID:
        return self._organization_id

    @property
    def invitation_type(self) -> InvitationType:
        return self._invitation_type

    @property
    def token(self) -> UUID:
        return self._token
