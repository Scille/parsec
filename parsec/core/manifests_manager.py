from parsec.core.encryption_manager import encrypt_for_local, decrypt_for_local
from parsec.core.base import BaseAsyncComponent
from parsec.core.schemas import TypedManifestSchema


class ManifestSignatureError(Exception):
    status = "invalid_signature"


class ManifestDecryptionError(Exception):
    status = "decryption_error"


class ManifestsManager(BaseAsyncComponent):
    def __init__(self, local_storage, backend_storage, encryption_manager):
        super().__init__()
        self._encryption_manager = encryption_manager
        self._local_storage = local_storage
        self._backend_storage = backend_storage

    async def _init(self, nursery):
        pass

    async def _teardown(self):
        pass

    def fetch_user_manifest_from_local(self):
        ciphered_msg = self._local_storage.fetch_user_manifest()
        if ciphered_msg:
            key = b"0" * 32  # TODO: of course I'm kidding...
            msg = decrypt_for_local(key, ciphered_msg)
            manifest, _ = TypedManifestSchema.load(msg)
            del manifest["format"]
            return manifest

    async def fetch_user_manifest_from_backend(self, version=None):
        ciphered_msg = await self._backend_storage.fetch_user_manifest(version=version)
        if ciphered_msg:
            msg = await self._encryption_manager.decrypt(ciphered_msg)
            manifest, _ = TypedManifestSchema.load(msg)
            del manifest["format"]
            return manifest

    def flush_user_manifest_on_local(self, manifest):
        msg, _ = TypedManifestSchema.dump(manifest)
        key = b"0" * 32  # TODO: of course I'm kidding...
        ciphered_msg = encrypt_for_local(key, msg)
        self._local_storage.flush_user_manifest(ciphered_msg)

    async def sync_user_manifest_with_backend(self, manifest):
        msg, _ = TypedManifestSchema.dump(manifest)
        ciphered_msg = await self._encryption_manager.encrypt_for_self(msg)
        await self._backend_storage.sync_user_manifest(manifest["version"], ciphered_msg)

    def fetch_from_local(self, id, key):
        ciphered_msg = self._local_storage.fetch_manifest(id)
        if ciphered_msg:
            msg = decrypt_for_local(key, ciphered_msg)
            manifest, _ = TypedManifestSchema.load(msg)
            return manifest

    def remove_from_local(self, id):
        self._local_storage.remove_manifest_local_data(id)

    async def fetch_from_backend(self, id, rts, key, version=None):
        ciphered_msg = await self._backend_storage.fetch_manifest(id, rts, version=version)
        if ciphered_msg:
            # TODO: store cache in local ?
            msg = await self._encryption_manager.decrypt_with_secret_key(key, ciphered_msg)
            manifest, _ = TypedManifestSchema.load(msg)
            del manifest["format"]
            return manifest

    def flush_on_local(self, id, key, manifest):
        msg, _ = TypedManifestSchema.dump(manifest)
        ciphered_msg = encrypt_for_local(key, msg)
        self._local_storage.flush_manifest(id, ciphered_msg)

    async def sync_new_entry_with_backend(self, key, manifest):
        msg, _ = TypedManifestSchema.dump(manifest)
        ciphered_msg = await self._encryption_manager.encrypt_with_secret_key(key, msg)
        return await self._backend_storage.sync_new_manifest(ciphered_msg)

    async def sync_with_backend(self, id, wts, key, manifest):
        version = manifest["version"]
        msg, _ = TypedManifestSchema.dump(manifest)
        ciphered_msg = await self._encryption_manager.encrypt_with_secret_key(key, msg)
        await self._backend_storage.sync_manifest(id, wts, version, ciphered_msg)
