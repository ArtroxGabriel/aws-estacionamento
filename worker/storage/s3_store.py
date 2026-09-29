"""S3_Connector — downloads vehicle photos from Amazon S3.

Part of the I/O connector layer (`storage/`). The connector receives its boto3
S3 client and the target bucket name (from :class:`worker.config.Config`) via the
constructor, matching the design's "connectors receive their boto3 client"
convention. See design.md "S3_Connector (`storage/s3_store.py`)" and
Requirement 3.

Domain failures surface as a single :class:`RetrievalError` carrying a ``kind``
discriminator (``not_found`` | ``too_large`` | ``invalid_params`` |
``unavailable``) so the Poller can map each to a retain/poison outcome without
inspecting boto3 internals.
"""

from __future__ import annotations

import io
import socket
from typing import Any, Literal

from botocore.exceptions import BotoCoreError, ClientError

__all__ = ["RetrievalError", "S3Connector", "RetrievalKind"]

# The four retrieval outcomes the Poller distinguishes (Req 3.2, 3.4, 3.6, 3.7).
RetrievalKind = Literal["not_found", "too_large", "invalid_params", "unavailable"]

# Maximum in-memory image size held for a single message (Req 3.3, 3.4).
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# Per-object download budget (Req 3.1).
_DOWNLOAD_TIMEOUT_S = 10.0

# Read granularity for the bounded streaming read (Req 3.3/3.4).
_CHUNK_SIZE = 256 * 1024  # 256 KB

# Attempts on transient/network errors before giving up (Req 3.7). One initial
# attempt plus retries, capped at three total tries.
_MAX_ATTEMPTS = 3

# S3/boto3 error codes that mean "the object is not there" (Req 3.6).
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404", "NotFound"})


class RetrievalError(Exception):
    """Raised by :class:`S3Connector` when a photo cannot be retrieved.

    ``kind`` is one of ``not_found``, ``too_large``, ``invalid_params`` or
    ``unavailable`` (Req 3.2, 3.4, 3.6, 3.7). The Poller maps the kind to an
    outcome; the caller never needs to inspect the underlying boto3 exception.
    """

    def __init__(self, kind: RetrievalKind, message: str | None = None) -> None:
        self.kind: RetrievalKind = kind
        super().__init__(message or kind)


class S3Connector:
    """Downloads objects from a single S3 bucket with size and retry bounds."""

    def __init__(self, s3_client: Any, bucket_name: str) -> None:
        """Store the injected boto3 S3 client and the target bucket.

        The bucket name comes from ``Config.s3_bucket_name``. It is not
        validated here so that construction never fails; an empty bucket is
        guarded per-download and reported as ``invalid_params`` (Req 3.2).
        """

        self._s3 = s3_client
        self._bucket = bucket_name

    def download(self, key: str) -> bytes:
        """Download the object at ``key`` from the configured bucket.

        Returns the object bytes on success (Req 3.1, 3.3). Raises
        :class:`RetrievalError` with:
          - ``invalid_params`` when the bucket or key is absent/empty; no
            download is attempted (Req 3.2).
          - ``too_large`` when the object exceeds 10 MB, discarding the buffer
            (Req 3.4).
          - ``not_found`` when the object does not exist (Req 3.6).
          - ``unavailable`` on S3/network failure after 3 attempts (Req 3.7).

        The in-memory buffer is always released, on success and on failure
        (Req 3.5).
        """

        # Guard empty/absent parameters *before* touching S3 (Req 3.2).
        if not self._bucket or not key:
            raise RetrievalError(
                "invalid_params",
                "bucket name and object key must both be non-empty",
            )

        last_error: Exception | None = None
        for _attempt in range(_MAX_ATTEMPTS):
            buffer: io.BytesIO | None = None
            try:
                buffer = io.BytesIO()
                self._download_into(key, buffer)
                return buffer.getvalue()
            except RetrievalError as err:
                # not_found / too_large are terminal — no point retrying.
                if err.kind in ("not_found", "too_large"):
                    raise
                last_error = err
            except (ClientError, BotoCoreError, OSError, socket.timeout) as err:
                # Transient S3/network failure: classify not-found vs retry.
                if _is_not_found(err):
                    raise RetrievalError("not_found", str(err)) from err
                last_error = err
            finally:
                # Release in-memory content on success or failure (Req 3.5).
                if buffer is not None:
                    buffer.close()

        # Retries exhausted on a transient error (Req 3.7).
        raise RetrievalError("unavailable", str(last_error)) from last_error

    def _download_into(self, key: str, buffer: io.BytesIO) -> None:
        """Fetch ``key`` into ``buffer``, enforcing the 10 MB cap.

        Checks the advertised ``ContentLength`` up front and bounds the actual
        streamed read so an object exceeding 10 MB is aborted and its partial
        buffer discarded (Req 3.3, 3.4). Raises ``RetrievalError('too_large')``
        on overflow; lets S3/network errors propagate to :meth:`download`.
        """

        response = self._s3.get_object(Bucket=self._bucket, Key=key)

        # Reject early when S3 advertises an oversized object (Req 3.4).
        content_length = response.get("ContentLength")
        if content_length is not None and content_length > _MAX_BYTES:
            _close_body(response)
            buffer.seek(0)
            buffer.truncate(0)
            raise RetrievalError(
                "too_large",
                f"object {key} is {content_length} bytes (> {_MAX_BYTES})",
            )

        body = response["Body"]
        total = 0
        try:
            while True:
                chunk = body.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                # Bounded read: a mis-reported or streamed oversize still aborts
                # here, and the partial buffer is discarded (Req 3.3, 3.4).
                if total > _MAX_BYTES:
                    buffer.seek(0)
                    buffer.truncate(0)
                    raise RetrievalError(
                        "too_large",
                        f"object {key} exceeds {_MAX_BYTES} bytes while reading",
                    )
                buffer.write(chunk)
        finally:
            _close_body(response)


def _is_not_found(error: Exception) -> bool:
    """True when a boto3 exception denotes a missing object/bucket (Req 3.6)."""

    if isinstance(error, ClientError):
        code = str(error.response.get("Error", {}).get("Code", ""))
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in _NOT_FOUND_CODES or status == 404:
            return True
    return False


def _close_body(response: dict[str, Any]) -> None:
    """Best-effort close of the streaming ``Body`` to free the connection."""

    body = response.get("Body") if isinstance(response, dict) else None
    close = getattr(body, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 — closing must never mask the outcome
            pass
