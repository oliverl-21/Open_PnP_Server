# Wait for config

Wait for config lets a device back off while the operator has no config file, then receive the config on a later poll after the file appears.

## Sub-features

- `wait-backoff` returns `callbackAfter` when no config file matches the serial, pid, or `default.cfg`.
- `wait-offer` returns a config-upgrade once the operator adds `FCW1234.cfg`.
- `wait-download` serves the new file bytes.

## How to get to it (user POV)

- The device polls before any config file exists and is told to call back.
- The operator adds `configs/<serial>.cfg`.
- The device polls again and is offered that file.

## Driving it with pnp-verify

Preconditions:

- `pnp-verify doctor --run "$RUN"` prints `doctor ok`.
- This run has no `FCW1234.cfg`, no `C9300-24P.cfg`, and no `default.cfg`.
- `GET /status` is `[]`.

- **Answer device-info with no config present.** Run `pnp-verify work-request --run "$RUN" --correlator C1 --save c1-device-info.xml`. The summary has `xmlns=urn:cisco:pnp:device-info`. Then run `pnp-verify work-response --run "$RUN" --correlator C1 --xmlns urn:cisco:pnp:device-info --success 1 --hostname sw1 --platform C9300-24P --version 17.9.4 --save c1-bye.xml`. The summary has `bye=yes`.
- **Poll while the file is missing.** Run `pnp-verify work-request --run "$RUN" --correlator C2 --save c2-backoff.xml`. The summary has `xmlns=urn:cisco:pnp:backoff`, `callback=yes`, `terminate=no`, `minutes=1`, and `reason=no config for FCW1234`.
- **Add the file.** Run `pnp-verify seed-config --run "$RUN" --name FCW1234.cfg --text "hostname sw1"`.
- **Poll again.** Run `pnp-verify work-request --run "$RUN" --correlator C3 --save c3-config.xml`. The summary has `xmlns=urn:cisco:pnp:config-upgrade` and `location` equal to the launch `base` plus `/configs/FCW1234.cfg`.
- **Download.** Run `pnp-verify download --run "$RUN" --path /configs/FCW1234.cfg --save config-body.txt`. The summary has `http=200` and `text=hostname sw1`.
- **Proof.** `c2-backoff.xml` contains `callbackAfter` and does not contain `terminate`. `config-body.txt` is `hostname sw1`.

## Gotchas

- Launch sets the callback delay to one minute. Assert `minutes=1`. Do not sleep for that minute. The next poll is valid immediately.
- A backoff of `unrecognized udi` means the UDI was not `PID:C9300-24P,VID:V01,SN:FCW1234`.
- Seeding `default.cfg` or `C9300-24P.cfg` before the waiting poll makes the server offer a config instead of waiting.
