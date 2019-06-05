# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from typing import List, Tuple
from pathlib import Path
from uuid import uuid4

from parsec.types import DeviceID, OrganizationID, BackendOrganizationAddr
from parsec.serde import SerdeValidationError, SerdePackingError
from parsec.crypto import SecretKey, SigningKey, PrivateKey
from parsec.core.types import EntryID, LocalDevice, local_device_serializer
from parsec.core.local_device.exceptions import (
    LocalDeviceError,
    LocalDeviceCryptoError,
    LocalDeviceNotFoundError,
    LocalDeviceAlreadyExistsError,
    LocalDeviceValidationError,
    LocalDevicePackingError,
)
from parsec.core.local_device.cipher import (
    BaseLocalDeviceDecryptor,
    BaseLocalDeviceEncryptor,
    PasswordDeviceEncryptor,
    PasswordDeviceDecryptor,
)
from parsec.core.local_device.pkcs11_cipher import PKCS11DeviceEncryptor, PKCS11DeviceDecryptor


def generate_new_device(
    device_id: DeviceID, organization_addr: BackendOrganizationAddr
) -> LocalDevice:
    return LocalDevice(
        organization_addr=organization_addr,
        device_id=device_id,
        signing_key=SigningKey.generate(),
        private_key=PrivateKey.generate(),
        user_manifest_id=EntryID(uuid4().hex),
        user_manifest_key=SecretKey.generate(),
        local_symkey=SecretKey.generate(),
    )


def get_key_file(config_dir: Path, device: LocalDevice) -> Path:
    slug = device.slug
    return get_devices_dir(config_dir) / slug / f"{slug}.keys"


def get_devices_dir(config_dir: Path) -> Path:
    return config_dir / "devices"


def list_available_devices(config_dir: Path) -> List[Tuple[OrganizationID, DeviceID, str, Path]]:
    try:
        candidate_pathes = list(get_devices_dir(config_dir).iterdir())
    except FileNotFoundError:
        return []

    # Sanity checks
    devices = []
    for device_path in candidate_pathes:
        slug = device_path.name
        try:
            organization_id, device_id = LocalDevice.load_slug(slug)

        except ValueError:
            continue

        key_file = device_path / f"{slug}.keys"
        try:
            cipher = get_cipher_info(key_file)
            devices.append((organization_id, device_id, cipher, key_file))

        except (LocalDeviceNotFoundError, LocalDeviceCryptoError):
            continue

    return devices


def get_cipher_info(key_file: Path) -> str:
    """
    Raises:
        LocalDeviceNotFoundError
        LocalDeviceCryptoError
    """
    from .pkcs11_cipher import PKCS11DeviceDecryptor
    from .cipher import PasswordDeviceDecryptor

    try:
        ciphertext = key_file.read_bytes()
    except OSError:
        raise LocalDeviceNotFoundError(f"Config file {key_file} is missing")

    for decryptor_cls, cipher in (
        (PKCS11DeviceDecryptor, "pkcs11"),
        (PasswordDeviceDecryptor, "password"),
    ):
        if decryptor_cls.can_decrypt(ciphertext):
            return cipher

    raise LocalDeviceCryptoError(f"Unknown cipher for {key_file}")


def load_device_with_password(key_file: Path, password: str) -> LocalDevice:
    """
        LocalDeviceNotFoundError
        LocalDeviceCryptoError
        LocalDeviceValidationError
        LocalDevicePackingError
    """
    decryptor = PasswordDeviceDecryptor(password)
    return _load_device(key_file, decryptor)


def save_device_with_password(
    config_dir: Path, device: LocalDevice, password: str, force: bool = False
) -> None:
    """
        LocalDeviceError
        LocalDeviceNotFoundError
        LocalDeviceCryptoError
        LocalDeviceValidationError
        LocalDevicePackingError
    """
    encryptor = PasswordDeviceEncryptor(password)
    _save_device(config_dir, device, encryptor, force)


def load_device_with_pkcs11(key_file: Path, token_id: int, key_id: int, pin: str) -> LocalDevice:
    """
        LocalDeviceNotFoundError
        LocalDeviceCryptoError
        LocalDeviceValidationError
        LocalDevicePackingError
    """
    decryptor = PKCS11DeviceDecryptor(token_id, key_id, pin)
    return _load_device(key_file, decryptor)


def save_device_with_pkcs11(
    config_dir: Path, device: LocalDevice, token_id: int, key_id: int
) -> None:
    """
        LocalDeviceError
        LocalDeviceNotFoundError
        LocalDeviceCryptoError
        LocalDeviceValidationError
        LocalDevicePackingError
    """
    encryptor = PKCS11DeviceEncryptor(token_id, key_id)
    _save_device(config_dir, device, encryptor)


def _load_device(key_file: Path, decryptor: BaseLocalDeviceDecryptor) -> LocalDevice:
    """
    Raises:
        LocalDeviceNotFoundError
        LocalDeviceCryptoError
        LocalDeviceValidationError
        LocalDevicePackingError
    """
    try:
        ciphertext = key_file.read_bytes()
    except OSError as exc:
        raise LocalDeviceNotFoundError(f"Config file {key_file} is missing") from exc

    raw = decryptor.decrypt(ciphertext)
    try:
        return local_device_serializer.loads(raw)

    except SerdeValidationError as exc:
        raise LocalDeviceValidationError(str(exc)) from exc
    except SerdePackingError as exc:
        raise LocalDevicePackingError(str(exc)) from exc


def _save_device(
    config_dir: Path, device: LocalDevice, encryptor: BaseLocalDeviceEncryptor, force: bool = False
) -> None:
    """
    Raises:
        LocalDeviceError
        LocalDeviceAlreadyExistsError
        LocalDeviceCryptoError
        LocalDeviceValidationError
        LocalDevicePackingError
    """
    key_file = get_key_file(config_dir, device)
    if key_file.exists() and not force:
        raise LocalDeviceAlreadyExistsError(
            f"Device `{device.organization_id}:{device.device_id}` already exists"
        )

    try:
        raw = local_device_serializer.dumps(device)

    except SerdeValidationError as exc:
        raise LocalDeviceValidationError(str(exc)) from exc

    except SerdePackingError as exc:
        raise LocalDevicePackingError(str(exc)) from exc

    ciphertext = encryptor.encrypt(raw)
    try:
        key_file.parent.mkdir(exist_ok=True, parents=True)
        key_file.write_bytes(ciphertext)

    except OSError as exc:
        raise LocalDeviceError(f"Cannot save {key_file}: {exc}") from exc
