"""One bounded, DNS-pinned OpenAI-compatible chat protocol."""

import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from contextlib import nullcontext, suppress
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread, Timer
from urllib.parse import urlsplit

from documind.errors import AppError

PROTOCOL = "openai_chat_completions"
MAX_RESPONSE_BYTES = 65536
DNS_SLOTS = BoundedSemaphore(4)


def endpoint_target(url, local_url, suffix="/chat/completions"):
    try:
        if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
            raise ValueError("Unsafe URL")
        if not url.isascii() or any(ch in url for ch in ("\\", "?", "#", "%")):
            raise ValueError("Unsafe URL")
        target = urlsplit(url)
        host = target.hostname
        port = target.port if target.port is not None else (443 if target.scheme == "https" else 80)
        if (
            not host
            or port == 0
            or target.username is not None
            or target.password is not None
            or not target.path.endswith(suffix)
            or target.path.startswith("//")
            or not re.fullmatch(r"[a-zA-Z0-9.:\-]+", host)
        ):
            raise ValueError("Unsafe URL")
        local = (
            url == local_url and host in {"127.0.0.1", "::1"} and target.scheme in {"http", "https"}
        )
        if not local and (target.scheme != "https" or port != 443):
            raise ValueError("Unsafe URL")
        if not local:
            try:
                literal = ipaddress.ip_address(host)
            except ValueError:
                literal = None
            if (literal is not None and not public_address(literal)) or host.lower() == "localhost":
                raise ValueError("Unsafe address")
        return target, host, port, local
    except ValueError as exc:
        raise AppError("ENDPOINT_NOT_ALLOWED", 422) from exc


def public_address(ip):
    return ip.is_global and not (
        ip.is_multicast
        or ip.is_reserved
        or (
            ip.version == 6
            and (
                ip.is_site_local
                or ip.ipv4_mapped
                or ip.sixtofour
                or ip.teredo
                or ip in ipaddress.ip_network("64:ff9b::/96")
                or ip in ipaddress.ip_network("64:ff9b:1::/48")
            )
        )
    )


def time_left(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AppError("INFERENCE_TIMEOUT", 504)
    return remaining


def resolve(host, port, local, *, deadline=None):
    if local:
        return host
    if deadline is None:
        deadline = time.monotonic() + 15
    if not DNS_SLOTS.acquire(timeout=time_left(deadline)):
        raise AppError("INFERENCE_TIMEOUT", 504)
    result = Queue(maxsize=1)

    def lookup():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception as exc:
            result.put(exc)
        finally:
            DNS_SLOTS.release()

    try:
        Thread(target=lookup, daemon=True).start()
    except RuntimeError as exc:
        DNS_SLOTS.release()
        raise AppError("CONNECTION_UNAVAILABLE", 503) from exc
    try:
        rows = result.get(timeout=time_left(deadline))
    except Empty as exc:
        raise AppError("INFERENCE_TIMEOUT", 504) from exc
    if isinstance(rows, Exception):
        raise AppError("CONNECTION_UNAVAILABLE", 503) from rows
    try:
        addresses = sorted({row[4][0] for row in rows})
        if any(not public_address(ipaddress.ip_address(address)) for address in addresses):
            raise AppError("ENDPOINT_NOT_ALLOWED", 422)
        if addresses:
            return addresses[0]
    except (OSError, ValueError) as exc:
        raise AppError("CONNECTION_UNAVAILABLE", 503) from exc
    raise AppError("CONNECTION_UNAVAILABLE", 503)


class PinnedTLS(http.client.HTTPSConnection):
    def __init__(self, host, address, port, deadline):
        super().__init__(
            host, port, timeout=time_left(deadline), context=ssl.create_default_context()
        )
        self.address = address
        self.deadline = deadline

    def connect(self):
        raw = socket.create_connection((self.address, self.port), time_left(self.deadline))
        try:
            raw.settimeout(time_left(self.deadline))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def json_request(
    url,
    method,
    credential,
    body,
    *,
    local_url=None,
    timeout=15,
    send_guard=nullcontext,
    suffix="/chat/completions",
):
    started = time.monotonic()
    target, host, port, local = endpoint_target(url, local_url, suffix)
    if not credential and not local:
        raise AppError("ENDPOINT_CREDENTIAL_REQUIRED", 422)
    deadline = started + timeout
    address = resolve(host, port, local, deadline=deadline)
    if target.scheme == "https":
        connection = PinnedTLS(host, address, port, deadline)
    else:
        connection = http.client.HTTPConnection(address, port, timeout=time_left(deadline))
    headers = {"Host": target.netloc, "Content-Type": "application/json"}
    if credential:
        headers["Authorization"] = "Bearer " + credential
    body = json.dumps(body).encode() if body is not None else None
    timer = None
    try:
        connection.connect()
        remaining = time_left(deadline)
        connected = connection.sock

        def interrupt():
            with suppress(OSError):
                connected.shutdown(socket.SHUT_RDWR)

        timer = Timer(remaining, interrupt)
        timer.daemon = True
        timer.start()
        # Serialize authorization and the bounded request write, then release before reading.
        with send_guard():
            if time.monotonic() - started >= timeout:
                raise AppError("INFERENCE_TIMEOUT", 504)
            connection.request(method, target.path, body=body, headers=headers)
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise AppError("ENDPOINT_REDIRECT_REJECTED", 502)
        if response.status == 409 and suffix.startswith("/runtime/v1/"):
            raise AppError("RUNTIME_STEP_CONFLICT", 409)
        if response.status == 429:
            if suffix.startswith("/runtime/v1/"):
                raise AppError("RUNTIME_BUSY", 503)
            if not local:
                raise AppError("INFERENCE_QUOTA_EXCEEDED", 503)
        elif response.status != 200:
            raise AppError("CONNECTION_UNAVAILABLE", 503)
        data = bytearray()
        while len(data) <= MAX_RESPONSE_BYTES:
            if time.monotonic() - started > timeout:
                raise AppError("INFERENCE_TIMEOUT", 504)
            block = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        if len(data) > MAX_RESPONSE_BYTES:
            raise AppError("INVALID_PROVIDER_RESPONSE", 502)
        if time.monotonic() - started >= timeout:
            raise AppError("INFERENCE_TIMEOUT", 504)
        if response.status == 429:
            error = None
            with suppress(ValueError):
                error = json.loads(data)
            if isinstance(error, dict) and error.get("detail") == "GENERATOR_BUSY":
                raise AppError("GENERATOR_BUSY", 503)
            raise AppError("INFERENCE_QUOTA_EXCEEDED", 503)
        return json.loads(data)
    except TimeoutError as exc:
        raise AppError("INFERENCE_TIMEOUT", 504) from exc
    except (OSError, http.client.HTTPException) as exc:
        if time.monotonic() - started >= timeout:
            raise AppError("INFERENCE_TIMEOUT", 504) from exc
        raise AppError("CONNECTION_UNAVAILABLE", 503) from exc
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AppError("INVALID_PROVIDER_RESPONSE", 502) from exc
    finally:
        if timer:
            timer.cancel()
        connection.close()


def complete(
    url, model, credential, messages, *, local_url=None, timeout=15, send_guard=nullcontext
):
    payload = json_request(
        url,
        "POST",
        credential,
        {"model": model, "messages": messages, "max_tokens": 48, "temperature": 0, "stream": False},
        local_url=local_url,
        timeout=timeout,
        send_guard=send_guard,
    )
    try:
        reported = payload["model"]
        text = payload["choices"][0]["message"]["content"]
        if (
            not isinstance(reported, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}", reported)
            or (
                credential
                and (reported == credential or (len(credential) >= 8 and credential in reported))
            )
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise AppError("INVALID_PROVIDER_RESPONSE", 502)
        return reported, text
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AppError("INVALID_PROVIDER_RESPONSE", 502) from exc
