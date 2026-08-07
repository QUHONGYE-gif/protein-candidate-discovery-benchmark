"""Narrow Tencent COS XML client for immutable Issue-scoped publications."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
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
        if role_name != "PCDBIssue58GateBRunner":
            raise ContractValidationError("unexpected Gate B CVM role")
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
                request_size = 0 if payload is None else len(payload)
                if (
                    self._transferred_bytes + request_size + response_size
                    > self._max_transfer_bytes
                ):
                    raise ContractValidationError("COS transfer cap would be exceeded")
                body = response.read()
                if response_size and len(body) != response_size:
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
