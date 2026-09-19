from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from conftest import EXPIRED, SOURCE_URL, case_html

from datajud_scraper.batch import BatchService
from datajud_scraper.runtime import PersistentRateLimiter


@pytest.mark.parametrize("connection_change", [False, True])
def test_real_http11_cookie_and_connection_context(
    monkeypatch,
    dataset_factory,
    test_config,
    valid_pdf,
    connection_change,
):
    events = []
    sessions = {}
    disconnected = False

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            nonlocal disconnected
            port = self.client_address[1]
            cookie = self.headers.get("Cookie")
            headers = {"Content-Type": "text/html"}
            close = False
            if "/AcessoPublico?" in self.path:
                phase = "bootstrap"
                sessions[port] = f"JSESSIONID=connection-{port}"
                headers["Set-Cookie"] = sessions[port] + "; Path=/projudi; HttpOnly"
                body = case_html()
            elif "/DadosProcesso?" in self.path:
                phase = "page"
                body = (
                    case_html(include_download=False) if cookie == sessions.get(port) else EXPIRED
                )
                if connection_change and not disconnected:
                    disconnected = close = True
            else:
                phase = "pdf"
                assert self.headers["Referer"] == SOURCE_URL
                if cookie and cookie == sessions.get(port):
                    headers["Content-Type"] = "application/pdf"
                    body = valid_pdf
                else:
                    body = EXPIRED
            events.append((phase, port, cookie))
            self.send_response(200)
            for key, value in headers.items():
                self.send_header(key, value)
            if close:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_handle = httpx.HTTPTransport.handle_request
    original_wait = PersistentRateLimiter.wait
    waited = False

    def loopback(transport, request):
        # Preserve the production client's pool and cookie handling. Only the network
        # destination is replaced; every response travels through a real TCP socket.
        original = request.url
        request.url = original.copy_with(scheme="http", host="127.0.0.1", port=server.server_port)
        try:
            assert transport._pool._max_connections == 1
            assert transport._pool._max_keepalive_connections == 1
            assert transport._pool._keepalive_expiry is None
            return original_handle(transport, request)
        finally:
            request.url = original

    def idle_over_default_expiry(limiter):
        nonlocal waited
        if events and not waited and not connection_change:
            time.sleep(5.1)  # The default HTTPX pool would retire the bootstrap connection.
            waited = True
        return original_wait(limiter)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", loopback)
    monkeypatch.setattr(PersistentRateLimiter, "wait", idle_over_default_expiry)
    try:
        summary = BatchService(test_config).start(*dataset_factory())
        assert summary["counts"] == {"downloaded": 1}
        phases = [event[0] for event in events]
        if connection_change:
            assert phases == ["bootstrap", "page", "pdf"] * 2
            assert events[0][1] == events[1][1] != events[2][1]
            assert events[3][1] == events[4][1] == events[5][1]
            assert events[3][1] != events[2][1]
            assert summary["metrics"]["session_recoveries"] == 1
        else:
            assert phases == ["bootstrap", "page", "pdf"]
            assert len({event[1] for event in events}) == 1
            assert summary["metrics"]["session_recoveries"] == 0
        assert events[-1][2] == sessions[events[-1][1]]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
