# Open PnP verification map

This directory is the maintained source for verifying the user-facing behavior of Open PnP Server. Read the index before driving the server, then use the matching feature file as the recipe.

## Baseline preconditions

- The shell is at the Open PnP Server repo, and `PATH` includes `.cursor/skills/verify-open-pnp/scripts`.
- `RUN` is an absolute directory outside the repo, such as `/tmp/open-pnp-verify-$RANDOM`.
- `pnp-verify launch --run "$RUN"` has printed `launched`.
- `pnp-verify doctor --run "$RUN"` has printed `doctor ok`.
- The fixture device is `PID:C9300-24P,VID:V01,SN:FCW1234`.
- Never launch on port `8080`, and never point `--state-file` or `--configs-dir` at the checkout. The helper refuses both.
- Never drive a pid that this launch did not record.

## Driving conventions

- Start every recipe from the baseline state unless its preconditions say otherwise.
- Treat every command as literal. Keep quoted names, correlators, and flags unchanged.
- Run device and operator actions through `pnp-verify`.
- Pass `--save` on every response or download you claim as proof.
- A new feature needs its own `RUN` when an earlier feature left a device in `done`.
- Do not remove proof files during cleanup.

## Proof and skip reporting

- Capture the posted request and the XML response, not only the final status row.
- A config or image proof includes the downloaded bytes.
- Record the feature file name and the `base` URL with every artifact.
- Report an unreachable path with the command you ran and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with pnp-verify` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

Keep implementation details out of the map. Name only user paths, stable handles, required state, commands, and observable proof.

## Features

- [Hello](./hello.md) covers `GET /` and `GET /pnp/HELLO`.
- [Day-0 config](./day0-config.md) covers device-info, config download, terminate, and status.
- [Wait for config](./wait-for-config.md) covers backoff while the config file is absent, then the offer once the operator adds it.
- [Image install](./image-install.md) covers the image offer, the image download, and the reload element.
- [Forget a device](./forget.md) covers stopping the server, clearing one serial, and starting that device over.
