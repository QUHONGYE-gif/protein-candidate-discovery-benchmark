"""Narrow Tencent COS XML client for immutable Issue-scoped publications."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pcdb.contracts.experiment import ContractValidationError


@dataclass(frozen=True, slots=True)
class CosCredentials:
    """One temporary CVM-role credential set kept only in memory."""

    secret_id: str
    secret_key: str
    token: str
    expires_at: int | None = None


def _quoted(value: str) -> str:
    return urllib.parse.quote(value, safe="-_.~")


def _canonical(values: Mapping[str, str]) -> tuple[str, str]:
    lowered = {str(key).lower(): str(value).strip() for key, value in values.items()}
    names = sorted(lowered)
    encoded = "&".join(f"{_quoted(name)}={_quoted(lowered[name])}" for name in names)
    return encoded, ";".join(names)


def sign_cos_request(
    *,
    method: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    credentials: CosCredentials,
    start_time: int | None = None,
    expires_seconds: int = 600,
) -> dict[str, str]:
    """Return COS V5 authorization and security-token headers."""

    start = int(time.time()) if start_time is None else start_time
    key_time = f"{start};{start + expires_seconds}"
    canonical_query, query_names = _canonical(query)
    headers_to_sign = {**headers, "x-cos-security-token": credentials.token}
    canonical_headers, header_names = _canonical(headers_to_sign)
    http_string = f"{method.lower()}\n{path}\n{canonical_query}\n{canonical_headers}\n"
    string_to_sign = (
        "sha1\n" + key_time + "\n" + hashlib.sha1(http_string.encode()).hexdigest() + "\n"
    )
    sign_key = hmac.new(
        credentials.secret_key.encode(), key_time.encode(), hashlib.sha1
    ).hexdigest()
    signature = hmac.new(sign_key.encode(), string_to_sign.encode(), hashlib.sha1).hexdigest()
    authorization = (
        "q-sign-algorithm=sha1"
        f"&q-ak={credentials.secret_id}"
        f"&q-sign-time={key_time}"
        f"&q-key-time={key_time}"
        f"&q-header-list={header_names}"
        f"&q-url-param-list={query_names}"
        f"&q-signature={signature}"
    )
    return {
        "authorization": authorization,
        "x-cos-security-token": credentials.token,
    }


class CvmRoleCredentialsProvider:
    """Fetch and cache credentials for one exact CVM instance role."""

    _BASE = "http://metadata.tencentyun.com/latest/meta-data/cam/security-credentials"

    def __init__(self, role_name: str = "PCDBIssue58GateBRunner") -> None:
        if role_name not in {
            "PCDBIssue58GateBRunner",
            "PCDBIssue68GateAReader",
            "PCDBIssue68GateBWriter",
            "PCDBIssue68GateBReader",
        }:
            raise ContractValidationError("unexpected PCDB CVM role")
        self._role_name = role_name
        self._cached: CosCredentials | None = None

    def get(self) -> CosCredentials:
        now = int(time.time())
        if self._cached is not None and (
            self._cached.expires_at is None or self._cached.expires_at - now > 300
        ):
            return self._cached
        url = f"{self._BASE}/{urllib.parse.quote(self._role_name, safe='')}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(url, timeout=2) as response:
                value = json.loads(response.read())
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise ContractValidationError("CVM role credential retrieval failed") from exc
        try:
            if value["Code"] not in {0, "0", "Success"}:
                raise ValueError
            raw_expires = value["ExpiredTime"]
            if isinstance(raw_expires, str) and not raw_expires.isdigit():
                expires = int(
                    datetime.fromisoformat(raw_expires.replace("Z", "+00:00")).timestamp()
                )
            else:
                expires = int(raw_expires)
            credentials = CosCredentials(
                secret_id=str(value["TmpSecretId"]),
                secret_key=str(value["TmpSecretKey"]),
                token=str(value["Token"]),
                expires_at=expires,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractValidationError("CVM role credential response drifted") from exc
        if not credentials.secret_id or not credentials.secret_key or not credentials.token:
            raise ContractValidationError("CVM role returned empty credentials")
        self._cached = credentials
        return credentials


class EnvironmentCredentialsProvider:
    """Read the existing CAM sub-user credential source without persisting secrets."""

    _SECRET_ID = "TENCENTCLOUD_SECRET_ID"
    _SECRET_KEY = "TENCENTCLOUD_SECRET_KEY"
    _TOKEN = "TENCENTCLOUD_SESSION_TOKEN"

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = os.environ if environment is None else environment

    def get(self) -> CosCredentials:
        secret_id = self._environment.get(self._SECRET_ID, "")
        secret_key = self._environment.get(self._SECRET_KEY, "")
        token = self._environment.get(self._TOKEN, "")
        if not secret_id or not secret_key:
            raise ContractValidationError("CAM issuer environment credentials are absent")
        return CosCredentials(
            secret_id=secret_id,
            secret_key=secret_key,
            token=token,
        )


_ISSUE76_INPUT_ROLE = "qcs::cam::uin/100046646417:roleName/PCDBIssue76M4InputReader"
_STS_HOST = "sts.tencentcloudapi.com"
_STS_SERVICE = "sts"
_STS_VERSION = "2018-08-13"
_ISSUE76_SESSION_NAME = "pcdb-issue76-m4-pilot"
_ISSUE76_CREDENTIAL_SECONDS = 21_600
_ISSUE76_TRANSFER_BYTES = 2 * 1024**3


def _tc3_assume_role_headers(
    *,
    payload: bytes,
    credentials: CosCredentials,
    timestamp: int,
) -> dict[str, str]:
    """Sign one Tencent Cloud API 3.0 STS request without exposing key material."""

    content_type = "application/json"
    canonical_headers = f"content-type:{content_type}\nhost:{_STS_HOST}\n"
    signed_headers = "content-type;host"
    canonical_request = (
        f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{hashlib.sha256(payload).hexdigest()}"
    )
    date = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")
    credential_scope = f"{date}/{_STS_SERVICE}/tc3_request"
    string_to_sign = (
        "TC3-HMAC-SHA256\n"
        f"{timestamp}\n"
        f"{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    def sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode(), hashlib.sha256).digest()

    secret_date = sign(("TC3" + credentials.secret_key).encode(), date)
    secret_service = sign(secret_date, _STS_SERVICE)
    secret_signing = sign(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing,
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "authorization": (
            "TC3-HMAC-SHA256 "
            f"Credential={credentials.secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
        "content-type": content_type,
        "host": _STS_HOST,
        "x-tc-action": "AssumeRole",
        "x-tc-region": "ap-chengdu",
        "x-tc-timestamp": str(timestamp),
        "x-tc-version": _STS_VERSION,
    }
    if credentials.token:
        headers["x-tc-token"] = credentials.token
    return headers


class AssumeRoleCredentialsProvider:
    """Exchange the exact Lighthouse issuer role for the Issue #76 input-reader role."""

    def __init__(
        self,
        role_arn: str,
        *,
        issuer_credentials: Callable[[], CosCredentials] | None = None,
        now: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        if role_arn != _ISSUE76_INPUT_ROLE:
            raise ContractValidationError("unexpected Issue #76 input-reader role ARN")
        if issuer_credentials is None:
            issuer = EnvironmentCredentialsProvider()
            issuer_credentials = issuer.get
        self._role_arn = role_arn
        self._issuer_credentials = issuer_credentials
        self._now = now
        self._cached: CosCredentials | None = None

    def get(self) -> CosCredentials:
        """Return one cached short-lived reader credential, refreshing only near expiry."""

        timestamp = self._now()
        if self._cached is not None and (
            self._cached.expires_at is None or self._cached.expires_at - timestamp > 300
        ):
            return self._cached
        payload = json.dumps(
            {
                "DurationSeconds": _ISSUE76_CREDENTIAL_SECONDS,
                "RoleArn": self._role_arn,
                "RoleSessionName": _ISSUE76_SESSION_NAME,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        headers = _tc3_assume_role_headers(
            payload=payload,
            credentials=self._issuer_credentials(),
            timestamp=timestamp,
        )
        request = urllib.request.Request(
            f"https://{_STS_HOST}/",
            data=payload,
            headers=headers,
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=10) as response:
                value = json.loads(response.read())
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise ContractValidationError("Issue #76 STS AssumeRole failed") from exc
        try:
            result = value["Response"]
            if not isinstance(result, dict) or "Error" in result:
                raise ValueError
            raw = result["Credentials"]
            expires_at = result["ExpiredTime"]
            if (
                not isinstance(raw, dict)
                or isinstance(expires_at, bool)
                or not isinstance(expires_at, int)
                or expires_at - timestamp <= 300
                or expires_at - timestamp > _ISSUE76_CREDENTIAL_SECONDS + 300
            ):
                raise ValueError
            credentials = CosCredentials(
                secret_id=str(raw["TmpSecretId"]),
                secret_key=str(raw["TmpSecretKey"]),
                token=str(raw["Token"]),
                expires_at=expires_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractValidationError("Issue #76 STS credential response drifted") from exc
        if not credentials.secret_id or not credentials.secret_key or not credentials.token:
            raise ContractValidationError("Issue #76 STS returned empty credentials")
        self._cached = credentials
        return credentials


class CosXmlObjectClient:
    """No-retry COS client exposing only HEAD, GET, immutable PUT, and exact LIST."""

    def __init__(
        self,
        *,
        region: str = "ap-beijing",
        credentials: Callable[[], CosCredentials] | None = None,
        max_transfer_bytes: int = 10_737_418_240,
    ) -> None:
        if region != "ap-beijing":
            raise ContractValidationError("Issue #58 COS region drifted")
        provider = CvmRoleCredentialsProvider()
        self._credentials = credentials or provider.get
        self._region = region
        self._max_transfer_bytes = max_transfer_bytes
        self._transferred_bytes = 0

    @property
    def transferred_bytes(self) -> int:
        """Return exact request/response payload bytes counted by this client."""

        return self._transferred_bytes

    def _request(
        self,
        method: str,
        bucket: str,
        key: str,
        *,
        query: Mapping[str, str] | None = None,
        payload: bytes | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> tuple[bytes, Mapping[str, str]]:
        query = {} if query is None else dict(query)
        request_size = 0 if payload is None else len(payload)
        if self._transferred_bytes + request_size > self._max_transfer_bytes:
            raise ContractValidationError("COS transfer cap would be exceeded")
        host = f"{bucket}.cos.{self._region}.myqcloud.com"
        path = "/" + urllib.parse.quote(key, safe="/-_.~")
        signed_headers = {"host": host}
        if extra_headers is not None:
            signed_headers.update({key.lower(): value for key, value in extra_headers.items()})
        signed = sign_cos_request(
            method=method,
            path=path,
            query=query,
            headers=signed_headers,
            credentials=self._credentials(),
        )
        headers = {**signed_headers, **signed}
        url = f"https://{host}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=60) as response:
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                response_size = int(response_headers.get("content-length", "0"))
                transferred_response_size = 0 if method == "HEAD" else response_size
                if (
                    self._transferred_bytes + request_size + transferred_response_size
                    > self._max_transfer_bytes
                ):
                    raise ContractValidationError("COS transfer cap would be exceeded")
                body = b"" if method == "HEAD" else response.read()
                if transferred_response_size and len(body) != transferred_response_size:
                    raise ContractValidationError("COS response content-length drifted")
                if self._transferred_bytes + request_size + len(body) > self._max_transfer_bytes:
                    raise ContractValidationError("COS transfer cap exceeded")
                self._transferred_bytes += request_size + len(body)
                return body, response_headers
        except urllib.error.HTTPError as exc:
            if method == "PUT" and exc.code in {409, 412}:
                raise FileExistsError("COS object already exists") from exc
            raise ContractValidationError(f"COS {method} failed with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ContractValidationError(f"COS {method} transport failed") from exc

    def head(self, bucket: str, key: str) -> Any:
        from pcdb.m2.gate_b_runtime import ObjectMetadata

        _, headers = self._request("HEAD", bucket, key)
        try:
            size = int(headers["content-length"])
        except (KeyError, ValueError) as exc:
            raise ContractValidationError("COS HEAD omitted valid content-length") from exc
        return ObjectMetadata(size_bytes=size, etag=headers.get("etag"))

    def get(self, bucket: str, key: str) -> bytes:
        payload, _ = self._request("GET", bucket, key)
        return payload

    def put_forbid_overwrite(self, bucket: str, key: str, payload: bytes) -> None:
        content_md5 = base64.b64encode(
            hashlib.md5(payload, usedforsecurity=False).digest()
        ).decode()
        self._request(
            "PUT",
            bucket,
            key,
            payload=payload,
            extra_headers={
                "content-md5": content_md5,
                "x-cos-forbid-overwrite": "true",
            },
        )

    def list_keys(self, bucket: str, prefix: str) -> tuple[str, ...]:
        payload, _ = self._request(
            "GET",
            bucket,
            "",
            query={"encoding-type": "url", "max-keys": "1000", "prefix": prefix},
        )
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise ContractValidationError("COS LIST response is invalid XML") from exc

        def children(name: str) -> list[ET.Element]:
            return [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == name]

        truncated = children("IsTruncated")
        if len(truncated) != 1 or truncated[0].text not in {"false", "False"}:
            raise ContractValidationError("COS LIST exceeded the reviewed one-page inventory")
        keys = []
        for element in children("Key"):
            if element.text is None:
                raise ContractValidationError("COS LIST returned an empty key")
            keys.append(urllib.parse.unquote(element.text))
        if len(keys) != len(set(keys)):
            raise ContractValidationError("COS LIST returned duplicate keys")
        return tuple(keys)


class Issue68GateAReadOnlyCosClient:
    """Capability wrapper exposing only the exact Gate A HEAD/GET surface."""

    def __init__(
        self,
        *,
        credentials: Callable[[], CosCredentials] | None = None,
        max_transfer_bytes: int = 10_737_418_240,
    ) -> None:
        if credentials is None:
            provider = CvmRoleCredentialsProvider("PCDBIssue68GateAReader")
            credentials = provider.get
        self.__client = CosXmlObjectClient(
            credentials=credentials,
            max_transfer_bytes=max_transfer_bytes,
        )

    @property
    def transferred_bytes(self) -> int:
        return self.__client.transferred_bytes

    def head(self, bucket: str, key: str) -> Any:
        return self.__client.head(bucket, key)

    def get(self, bucket: str, key: str) -> bytes:
        return self.__client.get(bucket, key)


class Issue68GateBReadOnlyCosClient:
    """Capability-separated Gate B reader with no PUT method."""

    def __init__(
        self,
        *,
        credentials: Callable[[], CosCredentials] | None = None,
        max_transfer_bytes: int = 10_737_418_240,
    ) -> None:
        if credentials is None:
            provider = CvmRoleCredentialsProvider("PCDBIssue68GateBReader")
            credentials = provider.get
        self.__client = CosXmlObjectClient(
            credentials=credentials,
            max_transfer_bytes=max_transfer_bytes,
        )

    @property
    def transferred_bytes(self) -> int:
        return self.__client.transferred_bytes

    def head(self, bucket: str, key: str) -> Any:
        return self.__client.head(bucket, key)

    def get(self, bucket: str, key: str) -> bytes:
        return self.__client.get(bucket, key)

    def list_keys(self, bucket: str, prefix: str) -> tuple[str, ...]:
        return self.__client.list_keys(bucket, prefix)


class Issue68GateBWriterCosClient:
    """Issue-scoped Gate B writer exposing immutable PUT but no delete operation."""

    def __init__(
        self,
        *,
        credentials: Callable[[], CosCredentials] | None = None,
        max_transfer_bytes: int = 10_737_418_240,
    ) -> None:
        if credentials is None:
            provider = CvmRoleCredentialsProvider("PCDBIssue68GateBWriter")
            credentials = provider.get
        self.__client = CosXmlObjectClient(
            credentials=credentials,
            max_transfer_bytes=max_transfer_bytes,
        )

    @property
    def transferred_bytes(self) -> int:
        return self.__client.transferred_bytes

    def head(self, bucket: str, key: str) -> Any:
        return self.__client.head(bucket, key)

    def get(self, bucket: str, key: str) -> bytes:
        return self.__client.get(bucket, key)

    def list_keys(self, bucket: str, prefix: str) -> tuple[str, ...]:
        return self.__client.list_keys(bucket, prefix)

    def put_forbid_overwrite(self, bucket: str, key: str, payload: bytes) -> None:
        self.__client.put_forbid_overwrite(bucket, key, payload)


class Issue76M4ReadOnlyCosClient:
    """HEAD/GET-only COS surface backed by the exact Issue #76 assumed role."""

    def __init__(
        self,
        role_arn: str,
        *,
        credentials: Callable[[], CosCredentials] | None = None,
        max_transfer_bytes: int = _ISSUE76_TRANSFER_BYTES,
    ) -> None:
        if max_transfer_bytes != _ISSUE76_TRANSFER_BYTES:
            raise ContractValidationError("Issue #76 COS transfer cap must remain exactly 2 GiB")
        if credentials is None:
            provider = AssumeRoleCredentialsProvider(role_arn)
            credentials = provider.get
        elif role_arn != _ISSUE76_INPUT_ROLE:
            raise ContractValidationError("unexpected Issue #76 input-reader role ARN")
        self.__client = CosXmlObjectClient(
            credentials=credentials,
            max_transfer_bytes=max_transfer_bytes,
        )

    @property
    def transferred_bytes(self) -> int:
        return self.__client.transferred_bytes

    def head(self, bucket: str, key: str) -> Any:
        return self.__client.head(bucket, key)

    def get(self, bucket: str, key: str) -> bytes:
        return self.__client.get(bucket, key)
