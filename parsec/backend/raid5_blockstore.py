# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

from uuid import UUID
import struct
from structlog import get_logger
from sys import byteorder
from typing import List, Optional

from parsec.utils import open_service_nursery
from parsec.api.protocol import OrganizationID
from parsec.backend.blockstore import BaseBlockStoreComponent
from parsec.backend.block import BlockAlreadyExistsError, BlockNotFoundError, BlockTimeoutError


logger = get_logger()


def _xor_buffers(*buffers):
    buff_len = len(buffers[0])
    xored = int.from_bytes(buffers[0], byteorder)
    for buff in buffers[1:]:
        assert len(buff) == buff_len
        xored ^= int.from_bytes(buff, byteorder)
    return xored.to_bytes(buff_len, byteorder)


def split_block_in_chunks(block: bytes, nb_chunks: int) -> List[bytes]:
    payload_size = len(block) + 4  # encode block len as a uint32
    chunk_len = payload_size // nb_chunks
    if nb_chunks * chunk_len < payload_size:
        chunk_len += 1
    padding_len = chunk_len * nb_chunks - payload_size

    payload = struct.pack("!I", len(block)) + block + b"\x00" * padding_len

    return [payload[chunk_len * i : chunk_len * (i + 1)] for i in range(nb_chunks)]


def generate_checksum_chunk(chunks: List[bytes]) -> bytes:
    return _xor_buffers(*chunks)


def rebuild_block_from_chunks(
    chunks: List[Optional[bytes]], checksum_chunk: Optional[bytes]
) -> bytes:
    valid_chunks = [chunk for chunk in chunks if chunk is not None]
    assert len(chunks) - len(valid_chunks) <= 1  # Cannot correct more than 1 chunk
    try:
        missing_chunk_id = next(index for index, chunk in enumerate(chunks) if chunk is None)
        assert checksum_chunk is not None
        chunks[missing_chunk_id] = _xor_buffers(*valid_chunks, checksum_chunk)
    except StopIteration:
        pass
    # By now, all chunks are valid
    chunks: List[bytes]
    payload = b"".join(chunks)
    block_len, = struct.unpack("!I", payload[:4])
    return payload[4 : 4 + block_len]


class RAID5BlockStoreComponent(BaseBlockStoreComponent):
    def __init__(self, blockstores):
        self.blockstores = blockstores

    async def read(self, organization_id: OrganizationID, id: UUID) -> bytes:
        timeout_count = 0
        fetch_results: List[Optional[bytes]] = [None] * len(self.blockstores)

        async def _partial_blockstore_read(nursery, blockstore_index):
            nonlocal timeout_count
            nonlocal fetch_results
            try:
                fetch_results[blockstore_index] = await self.blockstores[blockstore_index].read(
                    organization_id, id
                )

            except BlockNotFoundError as exc:
                # We don't know yet if this id doesn't exists globally or only in this blockstore...
                fetch_results[blockstore_index] = exc

            except BlockTimeoutError as exc:
                fetch_results[blockstore_index] = exc
                timeout_count += 1
                logger.warning(
                    f"Cannot reach RAID5 blockstore #{blockstore_index} to read block {id}",
                    exc_info=exc,
                )
                if timeout_count > 1:
                    nursery.cancel_scope.cancel()
                else:
                    # Try to fetch the checksum to rebuild the current missing chunk...
                    nursery.start_soon(_partial_blockstore_read, nursery, len(self.blockstores) - 1)

        async with open_service_nursery() as nursery:
            # Don't fetch the checksum by default
            for blockstore_index in range(len(self.blockstores) - 1):
                nursery.start_soon(_partial_blockstore_read, nursery, blockstore_index)

        if timeout_count == 0:
            # Sanity check: no errors and we didn't fetch the checksum
            assert len([res for res in fetch_results if res is None]) == 1
            assert fetch_results[-1] is None
            assert not len([res for res in fetch_results if isinstance(res, Exception)])

            return rebuild_block_from_chunks(fetch_results[:-1], None)

        elif timeout_count == 1:
            checksum = fetch_results[-1]
            # Sanity check: one error and we have fetched the checksum
            assert len([res for res in fetch_results if res is None]) == 0
            assert isinstance(checksum, (bytes, bytearray))
            assert len([res for res in fetch_results if isinstance(res, Exception)]) == 1

            return rebuild_block_from_chunks(
                [
                    res if isinstance(res, (bytes, bytearray)) else None
                    for res in fetch_results[:-1]
                ],
                checksum,
            )

        else:
            logger.error(
                f"Block {id} cannot be read: Too many failing blockstores in the RAID5 cluster"
            )
            raise BlockTimeoutError("More than 1 blockstores has failed in the RAID5 cluster")

    async def create(self, organization_id: OrganizationID, id: UUID, block: bytes) -> None:
        nb_chunks = len(self.blockstores) - 1
        chunks = split_block_in_chunks(block, nb_chunks)
        assert len(chunks) == nb_chunks
        checksum_chunk = generate_checksum_chunk(chunks)

        # Actually do the upload
        error_count = 0

        async def _subblockstore_create(nursery, blockstore_index, chunk_or_checksum):
            nonlocal error_count
            try:
                await self.blockstores[blockstore_index].create(
                    organization_id, id, chunk_or_checksum
                )
            except BlockAlreadyExistsError:
                # It's possible a previous tentative to upload this block has
                # failed due to another blockstore not available. In such case
                # a retrial will raise AlreadyExistsError on all the blockstores
                # that sucessfully uploaded the block during last attempt.
                # Only solution to solve this is to ignore AlreadyExistsError.
                pass
            except BlockTimeoutError as exc:
                error_count += 1
                logger.warning(
                    f"Cannot reach RAID5 blockstore #{blockstore_index} to create block {id}",
                    exc_info=exc,
                )
                if error_count > 1:
                    # Early exit
                    nursery.cancel_scope.cancel()

        async with open_service_nursery() as nursery:
            for i, chunk_or_checksum in enumerate([*chunks, checksum_chunk]):
                nursery.start_soon(_subblockstore_create, nursery, i, chunk_or_checksum)

        if error_count > 1:
            # Only a single blockstore is allowed to fail
            logger.error(
                f"Block {id} cannot be created: Too many failing blockstores in the RAID5 cluster"
            )
            raise BlockTimeoutError("More than 1 blockstores has failed in the RAID5 cluster")
