# LJ Lambda Proxy

This is a utility to use locally when testing AWS Lambda docker images. AWS Lambda
is designed to only handle a single request at a time. If you run a single docker
container locally (e.g. to handle website requests) you may see the lambda process
crash with an opaque segfault [see this github issue](https://github.com/aws/aws-lambda-runtime-interface-emulator/issues/97).

A quick work around is to use this module to put the requests into a queue and
serve them to the lambda docker image sequentially. This solution is for quick
local testing only and should never be used in production.

## Useful commands

Requires uv. The bind port is where the proxy will expose itself to client requests. The
destination port should be the port exposed by the lambda docker container.

```
uv sync                  # install deps
uv run mypy              # typecheck
uv run lambda-proxy      # run CLI on default bind port 6769 and destination port 9000
uv run lambda-proxy --bind-port 6770 --dest-port 9001
uv run python -m lambda_proxy
```

License MIT
