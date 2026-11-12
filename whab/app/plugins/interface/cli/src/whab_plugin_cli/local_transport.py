"""Small verified HTTPS transport for the name-addressed local console."""

import http.client
import json
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit


class Response:
    def __init__(self, response):
        self.response = response
        self.status_code = response.status

    def _read(self, size=None):
        try:
            return self.response.read() if size is None else self.response.read(size)
        except (OSError, http.client.HTTPException):
            import httpx
            raise httpx.ReadError('Local HTTPS response was interrupted.') from None

    def iter_bytes(self):
        while chunk := self._read(65536):
            yield chunk

    def json(self):
        return json.loads(self._read())


class LocalClient:
    def __init__(self, *, base_url, verify, headers, timeout, trust_env, follow_redirects):
        target = urlsplit(base_url)
        if target.scheme != 'https' or trust_env or follow_redirects:
            raise ValueError('Local console requires direct verified HTTPS.')
        self.connection = http.client.HTTPSConnection(target.hostname, target.port, context=verify, timeout=timeout)
        self.headers = headers

    @contextmanager
    def stream(self, method, path, *, params=None, json=None, content=None, headers=None):
        request_headers = {**self.headers, **(headers or {})}
        body = content
        if json is not None:
            from json import dumps
            body = dumps(json, allow_nan=False).encode()
            request_headers['Content-Type'] = 'application/json'
        if params:
            path += ('&' if '?' in path else '?') + urlencode(params, doseq=True)
        if self.connection.sock is None:
            try:
                self.connection.connect()
            except (OSError, http.client.HTTPException):
                import httpx
                self.connection.close()
                raise httpx.ConnectError('Local HTTPS connection failed.') from None
        try:
            self.connection.request(method, path, body=body, headers=request_headers)
            response = self.connection.getresponse()
        except (OSError, http.client.HTTPException) as failure:
            # Match the public host adapter's startup/error contract. Transport
            # imports are deferred until failure and operations are never replayed.
            import httpx
            self.connection.close()
            if isinstance(failure, ConnectionRefusedError):
                raise httpx.ConnectError('Local host is unavailable.') from None
            raise httpx.TransportError('Local HTTPS request failed.') from None
        try:
            yield Response(response)
        finally:
            if not response.isclosed():
                self.connection.close()
            response.close()

    def post(self, path, *, json):
        with self.stream('POST', path, json=json) as response:
            value = response.json()
            return _JSONResponse(response.status_code, value)

    def delete(self, path):
        with self.stream('DELETE', path) as response:
            return _JSONResponse(response.status_code, response.json())

    def close(self):
        self.connection.close()


class _JSONResponse:
    def __init__(self, status_code, value):
        self.status_code = status_code
        self.value = value

    def json(self):
        return self.value
