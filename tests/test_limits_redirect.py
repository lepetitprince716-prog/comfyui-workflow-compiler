"""A ``--nodedefs`` URL follows a few redirects to http(s) and no downgrade from https to http.

The caller that can pass the URL is every command with ``--nodedefs`` (``node``, ``compile``,
``learn``, ``render``): ``read_source`` fetches ``<url>/api/object_info`` and the server named
there answers. The standard opener followed up to ten redirects to any of http, https and ftp,
and an https URL could be sent on to plain http. The address itself is not judged: a
ComfyUI server on this machine or the local network is the usual target, so a redirect to one
stays allowed.
"""

import http.server
import json
import threading
import unittest
import urllib.request

from tests import support  # noqa: F401  (puts src/ on sys.path)

from cwc.nodedefs import registry
from cwc.nodedefs.registry import MAX_REDIRECTS, read_source
from cwc.report import CwcError, ExitCode

INFO = {"Plain": {"input": {"required": {}}, "output": [], "output_name": [], "output_is_list": []}}


class Redirecting(http.server.BaseHTTPRequestHandler):
    """``/hop/<n>`` redirects to ``/hop/<n-1>``; ``/hop/0`` redirects to the document; ``/ftp`` to an ftp URL."""

    seen: list[str] = []

    def do_GET(self):  # noqa: N802 (http.server API)
        self.seen.append(self.path)
        if self.path == "/api/object_info":
            self.send_response(302)
            self.send_header("Location", "/hop/" + str(self.server.hops))
            self.end_headers()
        elif self.path.startswith("/hop/"):
            n = int(self.path.rsplit("/", 1)[1])
            self.send_response(302)
            self.send_header("Location", f"/hop/{n - 1}" if n else "/final")
            self.end_headers()
        elif self.path == "/final":
            body = json.dumps(INFO).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


class FtpRedirecting(Redirecting):
    def do_GET(self):  # noqa: N802
        self.seen.append(self.path)
        self.send_response(302)
        self.send_header("Location", "ftp://127.0.0.1:1/object_info.json")
        self.end_headers()


class serving:
    def __init__(self, handler, hops=0):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.hops = hops
        handler.seen = []
        self.handler = handler

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class RedirectTest(unittest.TestCase):
    def test_a_redirect_chain_within_the_limit_is_followed(self):
        with serving(Redirecting, hops=MAX_REDIRECTS - 2) as url:
            self.assertEqual(read_source(url), INFO)

    def test_a_chain_past_the_limit_is_a_d1_error(self):
        with serving(Redirecting, hops=MAX_REDIRECTS + 3) as url, self.assertRaises(CwcError) as caught:
            read_source(url)
        self.assertEqual(caught.exception.exit_code, ExitCode.NODEDEFS_MISSING)
        self.assertEqual([f.code.value for f in caught.exception.findings], ["D1"])
        self.assertNotIn("/final", Redirecting.seen)

    def test_a_redirect_to_another_scheme_is_refused(self):
        with serving(FtpRedirecting) as url, self.assertRaises(CwcError) as caught:
            read_source(url)
        self.assertEqual(caught.exception.exit_code, ExitCode.NODEDEFS_MISSING)
        self.assertIn("redirect", caught.exception.findings[0].message)
        self.assertIn("ftp", caught.exception.findings[0].message)


class DowngradeTest(unittest.TestCase):
    """The handler's decision, without a TLS server: ``redirect_request`` of the opener ``read_source`` uses."""

    def decide(self, old: str, new: str):
        handler = registry._SafeRedirect()
        request = urllib.request.Request(old)
        return handler.redirect_request(request, None, 302, "Found", {}, new)

    def test_https_to_http_is_refused(self):
        with self.assertRaisesRegex(Exception, "https to http"):
            self.decide("https://example.test/api/object_info", "http://example.test/x")

    def test_http_to_https_and_same_scheme_are_followed(self):
        for old, new in (
            ("http://example.test/a", "https://example.test/b"),
            ("https://example.test/a", "https://other.test/b"),
            ("http://example.test/a", "http://127.0.0.1:8188/b"),
        ):
            with self.subTest(old=old, new=new):
                self.assertEqual(self.decide(old, new).full_url, new)


if __name__ == "__main__":
    unittest.main()
