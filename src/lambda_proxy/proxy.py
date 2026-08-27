import signal
import socket
import sys
import traceback
from types import FrameType
from typing import Literal, Optional

ResponseLen = int | Literal["chunked"]


class LambdaProxy:
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
    ) -> None:
        self.max_request_len = max_request_len
        # handle ctrl-c
        signal.signal(signal.SIGINT, self.shutdown)
        # Create a TCP socket
        self.serverSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Re-use the socket
        self.serverSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.serverSocket.bind((host_name, bind_port))
        self.serverSocket.listen(10)  # become a server socket

        # run everything on main thread
        # only accept one connection at a time
        while True:
            (client_conn, client_address) = self.serverSocket.accept()
            print(f"NewClientConnection: {client_address}")
            chunk = client_conn.recv(self.max_request_len)

            while True:
                print(f"Initial request chunk length: {len(chunk)}")
                header, mb_body = self.parse_header_from_body(chunk)
                content_length = self.parse_response_len(header)
                # we have to check the utf8 encoded char length, not the byte length
                if isinstance(content_length, int) and (
                    (content_length > 0 and mb_body is None)
                    or (
                        mb_body is not None
                        and content_length > 0
                        and len(mb_body.decode("utf-8")) < content_length
                    )
                ):
                    chunk += client_conn.recv(content_length)
                else:
                    request = chunk
                    break

            print(f"request: {request!r}")
            dest_socket: socket.socket | None = None
            try:
                dest_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                dest_socket.settimeout(conn_timeout_seconds)
                dest_socket.connect((dest_host_name, dest_port))
                dest_socket.sendall(request)
                self.handle_http(client_conn, dest_socket)
                print("HttpResponseSent")
            except Exception:
                traceback.print_exc()
                if dest_socket is not None:
                    dest_socket.close()
            client_conn.close()

    def handle_http(
        self, client_conn: socket.socket, dest_socket: socket.socket
    ) -> None:
        # receive initial data including at least the header
        data = dest_socket.recv(self.max_request_len)
        # body may be incomplete here
        header, body = self.parse_header_from_body(data)
        response_len = self.parse_response_len(header)
        print(f"response_len: {response_len}")

        if response_len == "chunked":
            print("HandleChunkedTransferEncoding")
            pending = body if body is not None else b""
            done = False
            data_sent = False
            while not done:
                lines = pending.split(b"\r\n")
                i = 0
                while i < len(lines):
                    line = lines[i]
                    if len(line) == 1:
                        chunk_len = int(line.decode("utf-8"), 16)
                        if chunk_len == 0:
                            done = True
                            break
                    i += 1
                if not data_sent:
                    client_conn.send(data)
                    data_sent = True
                else:
                    client_conn.send(pending)
                if done:
                    break
                pending = dest_socket.recv(self.max_request_len)
            return

        if body is not None and response_len > 0:
            while body is None or len(body) < response_len:
                more = dest_socket.recv(self.max_request_len)
                data += more
                header, body = self.parse_header_from_body(data)
                if body is not None and len(body) >= response_len:
                    break
        else:
            body = None

        if body is None or response_len == 0:
            print("HandleNoBody")
            client_conn.send(data)
            dest_socket.close()
            return

        print("HandleContentLength")
        pending = body
        total_sent = 0
        while total_sent < response_len:
            client_conn.send(data)
            total_sent += len(pending)
            if total_sent < response_len:
                pending = dest_socket.recv(self.max_request_len)
        dest_socket.close()

    def parse_header_from_body(self, data: bytes) -> tuple[str, Optional[bytes]]:
        parts = data.split(b"\r\n\r\n")
        if len(parts) < 2:
            return parts[0].decode("utf-8"), None
        return parts[0].decode("utf-8"), parts[1]

    def parse_response_len(self, header: str) -> ResponseLen:
        lines = header.split("\n")
        for line in lines:
            s = line.lower()
            print(f"header: {s}")
            if s.startswith("transfer-encoding:") and "chunked" in s:
                return "chunked"
            if s.startswith("content-length: "):
                return int(s.split(" ")[1])
        return 0

    def shutdown(self, signum: int, frame: FrameType | None) -> None:
        print(f"Received signal {signum}. Shutting down...")
        self.serverSocket.close()
        sys.exit(0)
