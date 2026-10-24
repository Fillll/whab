"""Reviewed fictional CA transport overlay followed by the official host CLI.

This is a conformance launcher, not a product CA configuration feature. Only the
public host-owned RemoteStore transport constructor is adapted; all commands,
selection, authorization and storage behavior run through the official services.
"""

import json
import os
import runpy
import ssl
import sys
from pathlib import Path

import httpx

DROP_RESULT = b"drop-add-account-result\n"


class ResultLossTransport(httpx.BaseTransport):
    """Drop one explicitly armed acknowledged public result in a fictional home."""

    def __init__(self, transport, home):
        self.transport = transport
        self.marker = Path(home) / "devkit-remote-drop-result"

    def handle_request(self, request):
        response = self.transport.handle_request(request)
        if (response.status_code == 200 and request.method == "POST"
                and request.url.path.endswith("/commands") and self.marker.is_file()
                and not self.marker.is_symlink() and self.marker.stat().st_size == len(DROP_RESULT)
                and self.marker.read_bytes() == DROP_RESULT):
            if json.loads(request.content)["operation"] == "add_account":
                # Read the real public acknowledgment before losing only its
                # caller-facing result. No private commit boundary is injected.
                response.read()
                response.close()
                self.marker.unlink()
                raise httpx.ReadError("Fictional controlled public result loss.", request=request)
        return response

    def close(self):
        self.transport.close()


def main():
    from whab.plugins.remote_transport import RemoteStore

    context = ssl.create_default_context(cafile=os.environ["WHAB_DEVKIT_REMOTE_CA"])
    original = RemoteStore.__init__

    def initialize(self, url, token, budget_id, *, transport=None):
        if transport is not None:
            raise ValueError("The fictional launcher owns its explicitly verified transport.")
        verified = httpx.HTTPTransport(verify=context, trust_env=False)
        original(self, url, token, budget_id,
                 transport=ResultLossTransport(verified, os.environ["WHAB_HOME"]))

    RemoteStore.__init__ = initialize
    try:
        sys.argv[0] = "whab"
        runpy.run_module("whab", run_name="__main__")
    finally:
        RemoteStore.__init__ = original


if __name__ == "__main__":
    main()
