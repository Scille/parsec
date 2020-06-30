# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import pytest

from parsec.api.protocol import DeviceID, DeviceName, OrganizationID, UserID
from parsec.core.types import (
    BackendAddr,
    BackendOrganizationAddr,
    BackendOrganizationBootstrapAddr,
    BackendOrganizationClaimDeviceAddr,
    BackendOrganizationClaimUserAddr,
)
from parsec.crypto import PrivateKey, SecretKey, SigningKey, export_root_verify_key


@pytest.mark.parametrize("raw", ["foo42", "FOO", "f", "f-o-o", "f_o_o", "x" * 32, "三国"])
def test_organization_id_user_id_and_device_name(raw):
    organization_id = OrganizationID(raw)
    assert organization_id == raw

    user_id = UserID(raw)
    assert user_id == raw

    device_name = DeviceName(raw)
    assert device_name == raw


@pytest.mark.parametrize("raw", ["x" * 33, "F~o", "f o"])
def test_bad_organization_id_user_id_and_device_name(raw):
    with pytest.raises(ValueError):
        OrganizationID(raw)
    with pytest.raises(ValueError):
        UserID(raw)
    with pytest.raises(ValueError):
        DeviceName(raw)


@pytest.mark.parametrize(
    "raw", ["ali-c_e@d-e_v", "ALICE@DEV", "a@x", "a" * 32 + "@" + "b" * 32, "关羽@三国"]
)
def test_device_id(raw):
    user_id, device_name = raw.split("@")
    device_id = DeviceID(raw)
    assert device_id == raw
    assert device_id.user_id == user_id
    assert device_id.device_name == device_name


@pytest.mark.parametrize(
    "raw", ["a", "a" * 33 + "@" + "x" * 32, "a" * 32 + "@" + "x" * 33, "a@@x", "a@1@x"]
)
def test_bad_device_id(raw):
    with pytest.raises(ValueError):
        DeviceID(raw)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("parsec://foo", {"port": 443, "ssl": True}),
        ("parsec://foo?no_ssl=false", {"port": 443, "ssl": True}),
        ("parsec://foo?no_ssl=true", {"port": 80, "ssl": False}),
        ("parsec://foo:42", {"port": 42, "ssl": True}),
        ("parsec://foo:42?dummy=", {"port": 42, "ssl": True}),
        ("parsec://foo:42?no_ssl=true", {"port": 42, "ssl": False}),
        ("parsec://foo:42?no_ssl=false&dummy=foo", {"port": 42, "ssl": True}),
    ],
)
def test_backend_addr_good(url, expected):
    addr = BackendAddr.from_url(url)
    assert addr.hostname == "foo"
    assert addr.port == expected["port"]
    assert addr.use_ssl == expected["ssl"]


@pytest.mark.parametrize(
    "url,exc_msg",
    [
        ("", "Must start with `parsec://`"),
        ("foo", "Must start with `parsec://`"),
        (
            # bad scheme
            "xx://foo:42",
            "Must start with `parsec://`",
        ),
        (
            # path not allowed
            "parsec://foo:42/dummy",
            "Cannot have path",
        ),
        (
            # bad parsing in unknown param
            "parsec://foo:42?dummy",
            "bad query field: 'dummy'",
        ),
        (
            # bad parsing in valid param
            "parsec://foo:42?no_ssl",
            "bad query field: 'no_ssl'",
        ),
        (
            # missing value for param
            "parsec://foo:42?no_ssl=",
            "Invalid `no_ssl` param value (must be true or false)",
        ),
        (
            # bad value for param
            "parsec://foo:42?no_ssl=nop",
            "Invalid `no_ssl` param value (must be true or false)",
        ),
    ],
)
def test_backend_addr_bad_value(url, exc_msg):
    with pytest.raises(ValueError) as exc:
        BackendAddr.from_url(url)
    assert str(exc.value) == exc_msg


@pytest.fixture(scope="session")
def verify_key():
    return SigningKey.generate().verify_key


@pytest.fixture(scope="session")
def exported_verify_key(verify_key):
    return export_root_verify_key(verify_key)


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("parsec://foo", {"port": 443, "ssl": True}),
        ("parsec://foo?no_ssl=false", {"port": 443, "ssl": True}),
        ("parsec://foo?no_ssl=true", {"port": 80, "ssl": False}),
        ("parsec://foo:42", {"port": 42, "ssl": True}),
        ("parsec://foo:42?dummy=", {"port": 42, "ssl": True}),
        ("parsec://foo:42?no_ssl=true", {"port": 42, "ssl": False}),
        ("parsec://foo:42?no_ssl=false", {"port": 42, "ssl": True}),
        ("parsec://foo:42?no_ssl=false&dummy=foo", {"port": 42, "ssl": True}),
    ],
)
def test_backend_organization_addr_good(base_url, expected, verify_key):
    org = OrganizationID("org")
    backend_addr = BackendAddr.from_url(base_url)
    addr = BackendOrganizationAddr.build(
        backend_addr, organization_id=org, root_verify_key=verify_key
    )
    assert addr.hostname == "foo"
    assert addr.port == expected["port"]
    assert addr.use_ssl == expected["ssl"]
    assert addr.organization_id == org
    assert addr.root_verify_key == verify_key

    addr2 = BackendOrganizationAddr.from_url(addr.to_url())
    assert addr == addr2


@pytest.mark.parametrize(
    "url,exc_msg",
    [
        ("", "Must start with `parsec://`"),
        ("foo", "Must start with `parsec://`"),
        (
            # bad scheme
            "xx://foo:42/org?rvk=<rvk>",
            "Must start with `parsec://`",
        ),
        (
            # bad parsing in unknown param
            "parsec://foo:42/org?rvk=<rvk>&dummy",
            "bad query field: 'dummy'",
        ),
        (
            # missing mandatory rvk param
            "parsec://foo:42/org",
            "Missing mandatory `rvk` param",
        ),
        (
            # missing value for param
            "parsec://foo:42/org?rvk=",
            "Invalid `rvk` param value",
        ),
        (
            # bad value for param
            "parsec://foo:42/org?rvk=nop",
            "Invalid `rvk` param value",
        ),
        (
            # missing org name
            "parsec://foo:42?rvk=<rvk>",
            "Invalid organization ID",
        ),
        (
            # missing org name
            "parsec://foo:42/?rvk=<rvk>",
            "Invalid organization ID",
        ),
        (
            # bad org name
            "parsec://foo:42/bad/org?rvk=<rvk>",
            "Invalid organization ID",
        ),
        (
            # bad org name
            "parsec://foo:42/~org?rvk=<rvk>",
            "Invalid organization ID",
        ),
    ],
)
def test_backend_organization_addr_bad_value(url, exc_msg, exported_verify_key):
    url = url.replace("<rvk>", exported_verify_key)
    with pytest.raises(ValueError) as exc:
        BackendOrganizationAddr.from_url(url)
    assert str(exc.value) == exc_msg


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("parsec://foo", {"port": 443, "ssl": True}),
        ("parsec://foo?no_ssl=false", {"port": 443, "ssl": True}),
        ("parsec://foo?no_ssl=true", {"port": 80, "ssl": False}),
        ("parsec://foo:42", {"port": 42, "ssl": True}),
        ("parsec://foo:42?dummy=foo", {"port": 42, "ssl": True}),
        ("parsec://foo:42?no_ssl=true", {"port": 42, "ssl": False}),
        ("parsec://foo:42?no_ssl=true&dummy=", {"port": 42, "ssl": False}),
        ("parsec://foo:42?no_ssl=false", {"port": 42, "ssl": True}),
    ],
)
def test_backend_organization_bootstrap_addr_good(base_url, expected, verify_key):
    org = OrganizationID("org")
    backend_addr = BackendAddr.from_url(base_url)
    addr = BackendOrganizationBootstrapAddr.build(backend_addr, org, "token-123")
    assert addr.hostname == "foo"
    assert addr.port == expected["port"]
    assert addr.use_ssl == expected["ssl"]
    assert addr.organization_id == org
    assert addr.token == "token-123"

    addr2 = BackendOrganizationBootstrapAddr.from_url(str(addr))
    assert addr == addr2

    org_addr = addr.generate_organization_addr(verify_key)
    assert isinstance(org_addr, BackendOrganizationAddr)
    assert org_addr.root_verify_key == verify_key
    assert org_addr.hostname == addr.hostname
    assert org_addr.port == addr.port
    assert org_addr.use_ssl == addr.use_ssl
    assert org_addr.organization_id == addr.organization_id


@pytest.mark.parametrize(
    "url,exc_msg",
    [
        ("", "Must start with `parsec://`"),
        ("foo", "Must start with `parsec://`"),
        (
            # bad scheme
            "xx://foo:42/org?action=bootstrap_organization&token=123",
            "Must start with `parsec://`",
        ),
        (
            # bad parsing in unknown param
            "parsec://foo:42/org?action=bootstrap_organization&token=123&dummy",
            "bad query field: 'dummy'",
        ),
        (
            # missing action param
            "parsec://foo:42/org?token=123",
            "Missing mandatory `action` param",
        ),
        (
            # bad action param
            "parsec://foo:42/org?action=dummy&token=123",
            "Expected `action=bootstrap_organization` value",
        ),
        (
            # missing mandatory token param
            "parsec://foo:42/org?action=bootstrap_organization&",
            "bad query field: ''",
        ),
        (
            # missing value for param
            "parsec://foo:42/org?action=bootstrap_organization&token=",
            "Empty value in `token` param",
        ),
        (
            # missing org name
            "parsec://foo:42?action=bootstrap_organization&token=123",
            "Invalid organization ID",
        ),
        (
            # missing org name
            "parsec://foo:42/?action=bootstrap_organization&token=123",
            "Invalid organization ID",
        ),
        (
            # bad org name
            "parsec://foo:42/bad/org?action=bootstrap_organization&token=123",
            "Invalid organization ID",
        ),
        (
            # bad org name
            "parsec://foo:42/~org?action=bootstrap_organization&token=123",
            "Invalid organization ID",
        ),
    ],
)
def test_backend_organization_bootstrap_addr_bad_value(url, exc_msg):
    with pytest.raises(ValueError) as exc:
        BackendOrganizationBootstrapAddr.from_url(url)
    assert str(exc.value) == exc_msg


@pytest.fixture(scope="session")
def organization_addr(exported_verify_key):
    url = "parsec://foo/org?rvk=<rvk>".replace("<rvk>", exported_verify_key)
    return BackendOrganizationAddr.from_url(url)


@pytest.mark.parametrize(
    "user_id,token", [(UserID("alice"), "123"), (UserID("alice"), None)]  # Token is not mandatory
)
def test_backend_organization_claim_user_addr_good(organization_addr, user_id, token):
    addr = BackendOrganizationClaimUserAddr.build(organization_addr, user_id, token)

    assert addr.hostname == organization_addr.hostname
    assert addr.port == organization_addr.port
    assert addr.use_ssl == organization_addr.use_ssl
    assert addr.organization_id == organization_addr.organization_id
    assert addr.root_verify_key == organization_addr.root_verify_key

    assert isinstance(addr.user_id, UserID)
    assert addr.user_id == user_id
    assert addr.token == token

    addr2 = BackendOrganizationClaimUserAddr.from_url(addr.to_url())
    assert isinstance(addr2.user_id, UserID)
    assert addr == addr2


@pytest.mark.parametrize(
    "url,exc_msg",
    [
        (
            # bad parsing in unknown param
            "parsec://foo:42/org?action=claim_user&user_id=alice&token=123&rvk=<rvk>&dummy",
            "bad query field: 'dummy'",
        ),
        (
            # missing mandatory user_id param
            "parsec://foo:42/org?action=claim_user&token=123&rvk=<rvk>",
            "Missing mandatory `user_id` param",
        ),
        (
            # bad user_id param
            "parsec://foo:42/org?action=claim_user&user_id=&token=123&rvk=<rvk>",
            "Invalid `user_id` param value",
        ),
        (
            # bad user_id param
            "parsec://foo:42/org?action=claim_user&user_id=~Foo&token=123&rvk=<rvk>",
            "Invalid `user_id` param value",
        ),
        (
            # bad rvk param
            "parsec://foo:42/org?action=claim_user&user_id=alice&token=123&rvk=dummy",
            "Invalid `rvk` param value",
        ),
    ],
)
def test_backend_organization_claim_user_addr_bad_value(url, exc_msg, exported_verify_key):
    url = url.replace("<rvk>", exported_verify_key)
    with pytest.raises(ValueError) as exc:
        BackendOrganizationClaimUserAddr.from_url(url)
    assert str(exc.value) == exc_msg


@pytest.mark.parametrize(
    "device_id,token",
    [(DeviceID("alice@dev"), "123"), (DeviceID("alice@dev"), None)],  # Token is not mandatory
)
def test_backend_organization_claim_device_addr_good(organization_addr, device_id, token):
    addr = BackendOrganizationClaimDeviceAddr.build(organization_addr, device_id, token)

    assert addr.hostname == organization_addr.hostname
    assert addr.port == organization_addr.port
    assert addr.use_ssl == organization_addr.use_ssl
    assert addr.organization_id == organization_addr.organization_id
    assert addr.root_verify_key == organization_addr.root_verify_key

    assert isinstance(addr.device_id, DeviceID)
    assert addr.device_id == device_id
    assert addr.token == token

    addr2 = BackendOrganizationClaimDeviceAddr.from_url(addr.to_url())
    assert isinstance(addr2.device_id, DeviceID)
    assert addr == addr2


@pytest.mark.parametrize(
    "url,exc_msg",
    [
        (
            # bad parsing in unknown param
            "parsec://foo:42/org?action=claim_device&device_id=alice%40dev&token=123&rvk=<rvk>&dummy",
            "bad query field: 'dummy'",
        ),
        (
            # missing mandatory device_id param
            "parsec://foo:42/org?action=claim_device&token=123&rvk=<rvk>",
            "Missing mandatory `device_id` param",
        ),
        (
            # bad device_id param
            "parsec://foo:42/org?action=claim_device&device_id=&token=123&rvk=<rvk>",
            "Invalid `device_id` param value",
        ),
        (
            # bad device_id param
            "parsec://foo:42/org?action=claim_device&device_id=~Foo%40dev&token=123&rvk=<rvk>",
            "Invalid `device_id` param value",
        ),
        (
            # bad device_id param
            "parsec://foo:42/org?action=claim_device&device_id=alice&token=123&rvk=<rvk>",
            "Invalid `device_id` param value",
        ),
        (
            # bad rvk param
            "parsec://foo:42/org?action=claim_device&device_id=alice%40dev&token=123&rvk=dummy",
            "Invalid `rvk` param value",
        ),
    ],
)
def test_backend_organization_claim_device_addr_bad_value(url, exc_msg, exported_verify_key):
    url = url.replace("<rvk>", exported_verify_key)
    with pytest.raises(ValueError) as exc:
        BackendOrganizationClaimDeviceAddr.from_url(url)
    assert str(exc.value) == exc_msg


@pytest.mark.parametrize("key_type", (SigningKey, PrivateKey, SecretKey))
def test_keys_dont_leak_on_repr(key_type):
    key = key_type.generate()
    assert repr(key).startswith(f"<{key_type.__module__}.{key_type.__qualname__} object at ")
