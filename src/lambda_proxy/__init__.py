import argparse

from lambda_proxy.proxy import LambdaProxy

__all__ = ["LambdaProxy", "main"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Queue HTTP requests and proxy them one at a time to a local Lambda backend."
    )
    parser.add_argument(
        "--bind-port",
        type=int,
        default=6769,
        help="Local port to listen on (default: 6769)",
    )
    parser.add_argument(
        "--dest-port",
        type=int,
        default=9000,
        help="Destination Lambda runtime port (default: 9000)",
    )
    args = parser.parse_args()

    LambdaProxy(
        host_name="0.0.0.0",  # expose to local
        bind_port=args.bind_port,
        dest_host_name="localhost",  # connect to a locally running lambda docker container
        dest_port=args.dest_port,
    )
