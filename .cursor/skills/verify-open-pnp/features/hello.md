# Hello

Hello lets a device confirm this Open PnP Server is the process under test before any work request.

## Sub-features

- `hello-root` returns the server identity from `GET /`.
- `hello-pnp` returns an empty body from `GET /pnp/HELLO`.

## How to get to it (user POV)

- An operator opens `GET /`.
- A device opens `GET /pnp/HELLO` before it posts a work request.

## Driving it with pnp-verify

Preconditions:

- `pnp-verify doctor --run "$RUN"` prints `doctor ok`.

- **Read both routes.** Run `pnp-verify hello --run "$RUN" --save hello.txt`. The command exits `0`. Stdout contains `root_http=200`, `root_body=Open PnP Server`, `hello_http=200`, and `hello_body_bytes=0`.
- **Proof.** The evidence file `hello.txt` contains those same four lines.

## Gotchas

- `GET /` includes a trailing newline in the body. The summary strips that newline. The saved proof keeps the summary lines, not a second copy of the raw body.
- An empty `200` from `/pnp/HELLO` is the pass. XML on that route is a different server.
- Do not send hello to port `8080`. That port is the operator default and is not this run.
