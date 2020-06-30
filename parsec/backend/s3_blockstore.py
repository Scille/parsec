# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from functools import partial
from uuid import UUID

import boto3
import trio
from botocore.exceptions import ClientError as S3ClientError
from botocore.exceptions import EndpointConnectionError as S3EndpointConnectionError

from parsec.api.protocol import OrganizationID
from parsec.backend.block import BlockAlreadyExistsError, BlockNotFoundError, BlockTimeoutError
from parsec.backend.blockstore import BaseBlockStoreComponent


class S3BlockStoreComponent(BaseBlockStoreComponent):
    def __init__(self, s3_region, s3_bucket, s3_key, s3_secret, s3_endpoint_url=None):
        self._s3 = None
        self._s3_bucket = None
        self._s3 = boto3.client(
            "s3",
            region_name=s3_region,
            aws_access_key_id=s3_key,
            aws_secret_access_key=s3_secret,
            endpoint_url=s3_endpoint_url,
        )
        self._s3_bucket = s3_bucket
        self._s3.head_bucket(Bucket=s3_bucket)

    async def read(self, organization_id: OrganizationID, id: UUID) -> bytes:
        slug = f"{organization_id}/{id}"
        try:
            obj = self._s3.get_object(Bucket=self._s3_bucket, Key=slug)

        except S3ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                raise BlockNotFoundError() from exc

            else:
                raise BlockTimeoutError() from exc

        except S3EndpointConnectionError as exc:
            raise BlockTimeoutError() from exc

        return obj["Body"].read()

    async def create(self, organization_id: OrganizationID, id: UUID, block: bytes) -> None:
        slug = f"{organization_id}/{id}"
        try:
            await trio.to_thread.run_sync(
                partial(self._s3.head_object, Bucket=self._s3_bucket, Key=slug)
            )
        except S3ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                try:
                    await trio.to_thread.run_sync(
                        partial(self._s3.put_object, Bucket=self._s3_bucket, Key=slug, Body=block)
                    )
                except (S3ClientError, S3EndpointConnectionError) as exc:
                    raise BlockTimeoutError() from exc
            else:
                raise BlockTimeoutError() from exc

        except S3EndpointConnectionError as exc:
            raise BlockTimeoutError() from exc
        else:
            raise BlockAlreadyExistsError()
