# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from hashlib import sha256
from pathlib import Path
from typing import List, Optional

import attr

from parsec.api.data import DataError, UserProfile
from parsec.api.protocol import DeviceID, HumanHandle, HumanHandleField, OrganizationID
from parsec.core.types import BackendOrganizationAddr, EntryID, LocalDevice
from parsec.crypto import (
    CryptoError,
    PrivateKey,
    SecretKey,
    SigningKey,
    derivate_secret_key_from_password,
)
from parsec.serde import BaseSchema, MsgpackSerializer, fields


class LocalDeviceError(Exception):
    pass


class LocalDeviceCryptoError(LocalDeviceError):
    pass


class LocalDeviceNotFoundError(LocalDeviceError):
    pass


class LocalDeviceAlreadyExistsError(LocalDeviceError):
    pass


class LocalDeviceValidationError(LocalDeviceError):
    pass


class LocalDevicePackingError(LocalDeviceError):
    pass


class DeviceFileSchema(BaseSchema):
    type = fields.CheckedConstant("password", required=True)
    salt = fields.Bytes(required=True)
    ciphertext = fields.Bytes(required=True)

    # Since human_handle/device_label has been introduced, device_id is
    # redacted (i.e. user_id and device_name are 2 random uuids), hence
    # those fields have been added to the device file so the login page in
    # the GUI can use them to provide useful information.
    human_handle = HumanHandleField(allow_none=True, missing=None)
    device_label = fields.String(allow_none=True, missing=None)


key_file_serializer = MsgpackSerializer(
    DeviceFileSchema, validation_exc=LocalDeviceValidationError, packing_exc=LocalDevicePackingError
)


def generate_new_device(
    device_id: DeviceID,
    organization_addr: BackendOrganizationAddr,
    profile: UserProfile = UserProfile.STANDARD,
    human_handle: Optional[HumanHandle] = None,
    device_label: Optional[str] = None,
    signing_key: Optional[SigningKey] = None,
    private_key: Optional[PrivateKey] = None,
) -> LocalDevice:
    return LocalDevice(
        organization_addr=organization_addr,
        device_id=device_id,
        device_label=device_label,
        human_handle=human_handle,
        signing_key=signing_key or SigningKey.generate(),
        private_key=private_key or PrivateKey.generate(),
        profile=profile,
        user_manifest_id=EntryID(),
        user_manifest_key=SecretKey.generate(),
        local_symkey=SecretKey.generate(),
    )


def get_key_file(config_dir: Path, device: LocalDevice) -> Path:
    slug = device.slug
    return get_devices_dir(config_dir) / slug / f"{slug}.keys"


def get_devices_dir(config_dir: Path) -> Path:
    return config_dir / "devices"


@attr.s(slots=True, frozen=True, auto_attribs=True)
class AvailableDevice:
    key_file_path: Path
    organization_id: OrganizationID
    device_id: DeviceID
    human_handle: Optional[HumanHandle]
    device_label: Optional[str]

    @property
    def user_display(self) -> str:
        return str(self.human_handle or self.device_id.user_id)

    @property
    def device_display(self) -> str:
        return self.device_label or str(self.device_id.device_name)

    @property
    def slug(self) -> str:
        # Drop the `.keys` suffix
        return self.key_file_path.stem

    @property
    def slughash(self) -> str:
        return sha256(self.slug.encode()).hexdigest()


def list_available_devices(config_dir: Path) -> List[AvailableDevice]:
    try:
        candidate_pathes = list(get_devices_dir(config_dir).iterdir())
    except FileNotFoundError:
        return []

    # Sanity checks
    devices = []
    for device_folder_path in candidate_pathes:
        slug = device_folder_path.name
        key_file_path = device_folder_path / f"{slug}.keys"

        try:
            organization_id, device_id = LocalDevice.load_slug(slug)
            # Not a valid slug, ignore this folder
        except ValueError:
            continue

        try:
            data = key_file_serializer.loads(key_file_path.read_bytes())
        except (FileNotFoundError, LocalDeviceError):
            # Not a valid device file, ignore this folder
            continue

        devices.append(
            AvailableDevice(
                key_file_path=key_file_path,
                organization_id=organization_id,
                device_id=device_id,
                human_handle=data["human_handle"],
                device_label=data["device_label"],
            )
        )

    return devices


def load_device_with_password(key_file: Path, password: str) -> LocalDevice:
    """
        LocalDeviceNotFoundError
        LocalDeviceCryptoError
        LocalDeviceValidationError
        LocalDevicePackingError
    """
    try:
        ciphertext = key_file.read_bytes()
    except OSError as exc:
        raise LocalDeviceNotFoundError(f"Config file `{key_file}` is missing") from exc

    try:
        data = key_file_serializer.loads(ciphertext)
    except DataError as exc:
        raise LocalDeviceValidationError(f"Cannot load local device: {exc}") from exc

    try:
        key, _ = derivate_secret_key_from_password(password, data["salt"])
        plaintext = key.decrypt(data["ciphertext"])
    except CryptoError as exc:
        raise LocalDeviceCryptoError(str(exc)) from exc

    try:
        return LocalDevice.load(plaintext)

    except DataError as exc:
        raise LocalDeviceValidationError(f"Cannot load local device: {exc}") from exc


def save_device_with_password(
    config_dir: Path, device: LocalDevice, password: str, force: bool = False
) -> AvailableDevice:
    """
        LocalDeviceError
        LocalDeviceNotFoundError
        LocalDeviceCryptoError
        LocalDeviceValidationError
        LocalDevicePackingError
    """
    key_file = get_key_file(config_dir, device)
    _save_device_with_password(key_file, device, password, force=force)
    return key_file


def _save_device_with_password(
    key_file: Path, device: LocalDevice, password: str, force: bool = False
) -> None:
    if key_file.exists() and not force:
        raise LocalDeviceAlreadyExistsError(f"Device key file `{key_file}` already exists")

    try:
        key, salt = derivate_secret_key_from_password(password)
        ciphertext = key.encrypt(device.dump())

    except (CryptoError, DataError) as exc:
        raise LocalDeviceValidationError(f"Cannot dump local device: {exc}") from exc

    key_file_content = key_file_serializer.dumps(
        {
            "salt": salt,
            "ciphertext": ciphertext,
            "human_handle": device.human_handle,
            "device_label": device.device_label,
        }
    )

    try:
        key_file.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
        key_file.write_bytes(key_file_content)

    except OSError as exc:
        raise LocalDeviceError(f"Cannot save {key_file}: {exc}") from exc


def change_device_password(key_file: Path, old_password: str, new_password: str) -> None:
    """
        LocalDeviceError
        LocalDeviceNotFoundError
        LocalDeviceCryptoError
        LocalDeviceValidationError
        LocalDevicePackingError
    """
    device = load_device_with_password(key_file, password=old_password)
    _save_device_with_password(key_file, device, password=new_password, force=True)
