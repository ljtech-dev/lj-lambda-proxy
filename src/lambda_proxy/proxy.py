import signal
import socket
import sys
import traceback
from types import FrameType
from typing import Literal, Optional

ResponseLen = int | Literal["chunked"]

HEADER_END = b"\r\n\r\n"
# the last chunk of a chunked body (no trailers)
CHUNKED_END = b"0\r\n\r\n"


class LambdaProxy:
    """
    Serves one connection at a time: reads a whole request, forwards it to the Lambda, relays
    the whole response and closes the connection. Clients waiting meanwhile queue in the listen
    backlog, so the Lambda never sees two requests at once. Every response says
    `Connection: close`, so keep-alive clients open a new connection for their next request
    instead of reusing one this proxy has closed.
    """

    max_request_len: int
    serverSocket: socket.socket

    def __init__(
        self,
        host_name: str,
        bind_port: int,
        dest_host_name: str,
        dest_port: int,
        max_request_len: int = 1024 * 16,
        conn_timeout_seconds: int = 300,
        client_timeout_seconds: int = 30,
    ) -> None:
        self.max_request_len = max_request_len
        # handle ctrl-c
        signal.signal(signal.SIGINT, self.shutdown)
        # Create a TCP socket
        self.serverSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Re-use the socket
        self.serverSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.serverSocket.bind((host_name, bind_port))
        self.serverSocket.listen(64)  # become a server socket; waiting clients queue here

        # run everything on main thread
        # only accept one connection at a time
        while True:
            (client_conn, client_address) = self.serverSocket.accept()
            print(f"NewClientConnection: {client_address}")
            dest_socket: socket.socket | None = None
            try:
                # a client that stops sending must not hold up the queue
                client_conn.settimeout(client_timeout_seconds)
                request = self.read_request(client_conn)
                if request is None:
                    print("ClientClosedWithoutRequest")
                    continue
                print(f"request: {request[:200]!r}{'…' if len(request) > 200 else ''}")
                dest_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                dest_socket.settimeout(conn_timeout_seconds)
                dest_socket.connect((dest_host_name, dest_port))
                dest_socket.sendall(request)
                response = self.read_response(dest_socket)
                client_conn.sendall(self.with_connection_close(response))
                print(f"HttpResponseSent: {len(response)} bytes")
            except TimeoutError:
                print("ConnectionTimedOut: dropped the connection")
            except Exception:
                traceback.print_exc()
            finally:
                if dest_socket is not None:
                    dest_socket.close()
                client_conn.close()

    def read_request(self, client_conn: socket.socket) -> Optional[bytes]:
        """The whole request: its header, then Content-Length bytes of body (or a chunked body)."""
        data = self.read_header(client_conn)
        if data is None:
            return None
        header, body = self.parse_header_from_body(data)
        return data + self.read_body(client_conn, header, body or b"", until_close=False)

    def read_response(self, dest_socket: socket.socket) -> bytes:
        data = self.read_header(dest_socket)
        if data is None:
            raise ConnectionError("the Lambda closed the connection without a response")
        header, body = self.parse_header_from_body(data)
        return data + self.read_body(dest_socket, header, body or b"", until_close=True)

    def read_header(self, conn: socket.socket) -> Optional[bytes]:
        """Bytes up to and past the end of the header; None when the peer sent nothing."""
        data = b""
        while HEADER_END not in data:
            more = conn.recv(self.max_request_len)
            if not more:
                if not data:
                    return None
                raise ConnectionError("connection closed inside the HTTP header")
            data += more
        return data

    def read_body(
        self, conn: socket.socket, header: str, body: bytes, until_close: bool
    ) -> bytes:
        """The rest of the body after `body` (already read). Lengths are in bytes."""
        length = self.parse_response_len(header)
        received = body
        if length == "chunked":
            while not received.endswith(CHUNKED_END):
                more = conn.recv(self.max_request_len)
                if not more:
                    raise ConnectionError("connection closed inside a chunked body")
                received += more
        elif length > 0:
            while len(received) < length:
                more = conn.recv(max(length - len(received), self.max_request_len))
                if not more:
                    raise ConnectionError(
                        f"connection closed after {len(received)} of {length} body bytes"
                    )
                received += more
        elif until_close and not self.is_bodyless(header):
            # a response without a length ends when the server closes the connection
            while True:
                more = conn.recv(self.max_request_len)
                if not more:
                    break
                received += more
        return received[len(body) :]

    def is_bodyless(self, header: str) -> bool:
        """Responses that never have a body (1xx, 204, 304) or say Content-Length: 0."""
        status_line = header.split("\r\n", 1)[0].split(" ")
        status = status_line[1] if len(status_line) > 1 else ""
        return (
            status.startswith("1")
            or status in ("204", "304")
            or any(
                line.lower().replace(" ", "") == "content-length:0"
                for line in header.split("\r\n")
            )
        )

    def with_connection_close(self, response: bytes) -> bytes:
        """The response with its Connection header replaced by `Connection: close`."""
        head, sep, body = response.partition(HEADER_END)
        lines = [
            line
            for line in head.split(b"\r\n")
            if not line.lower().startswith((b"connection:", b"keep-alive:"))
        ]
        lines.append(b"Connection: close")
        return b"\r\n".join(lines) + sep + body

    def parse_header_from_body(self, data: bytes) -> tuple[str, Optional[bytes]]:
        head, sep, body = data.partition(HEADER_END)
        if not sep:
            return head.decode("latin-1"), None
        return head.decode("latin-1"), body

    def parse_response_len(self, header: str) -> ResponseLen:
        for line in header.split("\r\n")[1:]:
            name, _, value = line.partition(":")
            name = name.strip().lower()
            if name == "transfer-encoding" and "chunked" in value.lower():
                return "chunked"
            if name == "content-length":
                return int(value.strip())
        return 0

    def shutdown(self, signum: int, frame: FrameType | None) -> None:
        print(f"Received signal {signum}. Shutting down...")
        self.serverSocket.close()
        sys.exit(0)
