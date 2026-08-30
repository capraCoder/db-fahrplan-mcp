# Security

Supported: the latest release only.

This server makes outbound HTTPS requests to `www.bahn.de` and, by default, listens on
nothing (stdio). With `--transport streamable-http` it binds to `127.0.0.1` unless you
change `--host`; do not expose it to the internet without a reverse proxy and auth.

No credentials are read, stored or sent. The only user data leaving the machine is the
station names, times and traveller options you pass to the tools.

Report vulnerabilities via GitHub's private vulnerability reporting on this repository.
