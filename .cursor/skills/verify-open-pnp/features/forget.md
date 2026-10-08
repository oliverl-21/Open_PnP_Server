# Forget a device

Forget lets the operator remove one serial so that device starts provisioning again instead of staying finished.

## Sub-features

- `forget-stop` stops only the server pid this run recorded.
- `forget-serial` removes that serial from the state file and prints `forgot <serial>`.
- `forget-restart` offers device-info again after the server starts on the same files.
- `forget-missing` exits `1` and prints `no state for <serial>` when the serial is absent.

## How to get to it (user POV)

- The operator stops the server.
- The operator runs forget for one serial.
- The operator starts the server again.
- The device polls and is offered device-info, not terminate.
- The operator runs forget for a serial that has no state and sees a failure.

## Driving it with pnp-verify

Preconditions:

- Complete [Day-0 config](./day0-config.md) on this same `RUN` so `FCW1234` is `done`.
- `pnp-verify doctor --run "$RUN"` still prints `doctor ok` before the stop below.

- **Stop.** Run `pnp-verify stop --run "$RUN"`. Stdout contains `stopped`. A following `pnp-verify doctor --run "$RUN"` prints `doctor fail` because the pid is gone.
- **Forget the serial.** Run `pnp-verify forget --run "$RUN" --serial FCW1234`. The command exits `0` and stdout contains `stdout=forgot FCW1234`.
- **Start again.** Run `pnp-verify launch --run "$RUN"`. Stdout contains `launched` and the same `run` path. Then run `pnp-verify doctor --run "$RUN"` and require `doctor ok`.
- **Poll as a new visit.** Run `pnp-verify work-request --run "$RUN" --correlator C4 --save c4-device-info.xml`. The summary has `xmlns=urn:cisco:pnp:device-info` and `terminate=no`.
- **Missing serial.** Run `pnp-verify stop --run "$RUN"`, then `pnp-verify forget --run "$RUN" --serial NOPE`. The command exits `1` and stdout contains `stdout=no state for NOPE`.
- **Proof.** `c4-device-info.xml` is a device-info request, not a backoff terminate. The forget command's exit codes are `0` for `FCW1234` and `1` for `NOPE`.

## Gotchas

- Forget against a live pid exits before it edits the state file. The running server would overwrite that edit on the next poll. Stop first.
- Relaunch reuses the same port, state file, and evidence directory. Do not pick a new `RUN` between forget and the next poll, or you will not be looking at the cleared state.
- `C4` is the correlator after day-0 used `C1`, `C2`, and `C3`. Reusing `C1` is still a valid device message, but keep `C4` so the evidence file is distinct.
- After the missing-serial check the server is stopped. Run `cleanup` when the recipe is finished, or `launch` again if another recipe continues on this `RUN`.
