"""Artifact storage.

LocalStore: files under the data root (same keys as clearance_core.storage). Presigned uploads are signed
`PUT /upload/{token}` URLs served by this API (one successful write per key).
S3Store: Nebius Object Storage (S3-compatible) via boto3 when CLEARANCE_S3_BUCKET + credentials are set. The
local data root stays a working cache (jobs read local paths); `publish*` mirrors finished artifacts to the
bucket and `ensure_local` pulls objects on demand. Uploads are boto3 presigned POSTs whose policy carries a
content-length-range (VERIFY Nebius endpoint and presign support in docs/VERIFIED.md).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Iterable

from services.api.security import TokenSigner, resolve_under, safe_key


class LocalStore:
    backend = "local"

    def __init__(self, root: Path, signer: TokenSigner):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.signer = signer

    # ---- paths ----
    def path(self, key: str, *, mkdir: bool = True) -> Path:
        p = resolve_under(self.root, key)
        if mkdir:
            p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def dir(self, key: str) -> Path:
        p = resolve_under(self.root, key)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def exists(self, key: str) -> bool:
        return resolve_under(self.root, key).is_file()

    def ensure_local(self, key: str) -> Path:
        p = resolve_under(self.root, key)
        if not p.is_file():
            raise FileNotFoundError(key)
        return p

    def list_keys(self, prefix: str) -> list[str]:
        base = resolve_under(self.root, prefix.rstrip("/"))
        if not base.is_dir():
            return []
        root = self.root.resolve()
        return sorted(p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file())

    # ---- read / write ----
    def write_bytes(self, key: str, data: bytes) -> str:
        p = self.path(key)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp_")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        _replace(tmp, p)
        self.publish(key)
        return key

    def write_json(self, key: str, obj) -> str:
        if hasattr(obj, "model_dump"):
            obj = obj.model_dump(mode="json", by_alias=True)
        return self.write_bytes(key, json.dumps(obj, indent=2).encode("utf-8"))

    def read_bytes(self, key: str) -> bytes:
        return self.ensure_local(key).read_bytes()

    def read_json(self, key: str, *, retries: int = 5):
        """Tolerates a writer that is mid-write (other modules write JSON non-atomically)."""
        last: Exception | None = None
        for i in range(retries):
            try:
                return json.loads(self.ensure_local(key).read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                last = e
                time.sleep(0.02 * (i + 1))
            except PermissionError as e:  # Windows: file briefly locked by a writer
                last = e
                time.sleep(0.02 * (i + 1))
        raise last  # type: ignore[misc]

    def move_tree(self, src_prefix: str, dst_prefix: str) -> list[str]:
        src = resolve_under(self.root, src_prefix.rstrip("/"))
        dst = resolve_under(self.root, dst_prefix.rstrip("/"))
        moved = []
        if not src.is_dir():
            return moved
        for p in sorted(src.rglob("*")):
            if p.is_file():
                rel = p.relative_to(src)
                target = dst / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(target))
                moved.append((Path(dst_prefix.rstrip("/")) / rel).as_posix())
        shutil.rmtree(src, ignore_errors=True)
        return moved

    def copy_file_in(self, src: Path, key: str) -> bool:
        """Copy an external file into the store if missing or changed. Returns True if copied."""
        dst = self.path(key)
        st = src.stat()
        if dst.is_file():
            dt = dst.stat()
            if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
                return False
        shutil.copy2(src, dst)
        self.publish(key)
        return True

    # ---- presign / mirror ----
    def presign_put(self, key: str, *, api_base: str, ttl_s: int, capture_id: str, max_bytes: int) -> dict:
        safe_key(key)
        token = self.signer.sign({"t": "up", "k": key, "c": capture_id, "m": int(max_bytes)}, ttl_s)
        return {"key": key, "method": "PUT", "url": f"{api_base.rstrip('/')}/upload/{token}",
                "headers": {"Content-Type": "application/octet-stream"}, "expires_in_s": ttl_s}

    def publish(self, key: str) -> None:
        return None

    def publish_many(self, keys: Iterable[str]) -> None:
        for k in keys:
            self.publish(k)


class S3Store(LocalStore):
    backend = "s3"
    # keys that remote jobs keep rewriting: always fetch the current object
    mutable_prefixes = ("analyses/", "jobs/")

    def __init__(self, root: Path, signer: TokenSigner, *, bucket: str, client, prefix: str = ""):
        super().__init__(root, signer)
        self.bucket = bucket
        self.client = client
        self.prefix = prefix.strip("/")

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def exists(self, key: str) -> bool:
        if super().exists(key):
            return True
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(safe_key(key)))
            return True
        except Exception:  # noqa: BLE001 - botocore ClientError (404) or network
            return False

    def ensure_local(self, key: str) -> Path:
        p = resolve_under(self.root, key)
        refresh = key.startswith(self.mutable_prefixes)
        if refresh or not p.is_file():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f".tmp_dl_{p.name}")
            try:
                self.client.download_file(self.bucket, self._k(key), str(tmp))
                _replace(str(tmp), p)
            except Exception as e:  # noqa: BLE001 - missing object or network
                tmp.unlink(missing_ok=True)
                if not p.is_file():
                    raise FileNotFoundError(key) from e
        return p

    def list_keys(self, prefix: str) -> list[str]:
        keys = set(super().list_keys(prefix))
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": self._k(prefix)}
            if token:
                kw["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kw)
            for obj in resp.get("Contents", []):
                k = obj["Key"]
                keys.add(k[len(self.prefix) + 1:] if self.prefix else k)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return sorted(keys)

    def presign_put(self, key: str, *, api_base: str, ttl_s: int, capture_id: str, max_bytes: int) -> dict:
        safe_key(key)
        # Presigned POST: the policy caps the object size (a presigned PUT cannot). The client sends a
        # multipart/form-data body with `fields` first and the file last, in a form field named "file".
        post = self.client.generate_presigned_post(
            Bucket=self.bucket, Key=self._k(key),
            Conditions=[["content-length-range", 0, int(max_bytes)]], ExpiresIn=int(ttl_s))
        return {"key": key, "method": "POST", "url": post["url"], "fields": dict(post.get("fields") or {}),
                "file_field": "file", "headers": {}, "expires_in_s": ttl_s, "max_bytes": int(max_bytes)}

    def publish(self, key: str) -> None:
        p = resolve_under(self.root, key)
        if p.is_file():
            self.client.upload_file(str(p), self.bucket, self._k(key))


def make_store(settings, signer: TokenSigner) -> LocalStore:
    if not settings.s3_bucket:
        return LocalStore(settings.data_dir, signer)
    try:
        import boto3  # type: ignore
    except ImportError as e:
        raise RuntimeError("CLEARANCE_S3_BUCKET is set but boto3 is not installed (pip install boto3)") from e
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )
    return S3Store(settings.data_dir, signer, bucket=settings.s3_bucket, client=client, prefix=settings.s3_prefix)


def _replace(tmp: str, dst: Path, attempts: int = 10) -> None:
    for i in range(attempts):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:  # Windows: reader holds the file open
            if i == attempts - 1:
                raise
            time.sleep(0.02 * (i + 1))
