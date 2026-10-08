# Day-0 config

Day-0 config lets a blank device answer device-info, download its startup config, and receive terminate when provisioning is finished.

## Sub-features

- `day0-info` answers the device-info request with a hostname.
- `day0-download` fetches the exact config bytes named in the offer.
- `day0-done` receives backoff terminate after the config response succeeds.
- `day0-status` shows that serial as `done` with the reported hostname.

## How to get to it (user POV)

- The operator writes `configs/<serial>.cfg` before the device polls.
- The device posts `POST /pnp/WORK-REQUEST`, answers `POST /pnp/WORK-RESPONSE`, and downloads the config URL from the offer.
- The device polls once more and receives terminate.
- The operator reads `GET /status`.

## Driving it with pnp-verify

Preconditions:

- `pnp-verify doctor --run "$RUN"` prints `doctor ok`.
- This run has no file named `FCW1234.cfg` yet.
- `GET /status` is `[]`.

- **Place the config.** Run `pnp-verify seed-config --run "$RUN" --name FCW1234.cfg --text "hostname sw1"`. Stdout names the scratch config path and `bytes=12`.
- **Poll for work.** Run `pnp-verify work-request --run "$RUN" --correlator C1 --save c1-device-info.xml`. The summary has `http=200`, `xmlns=urn:cisco:pnp:device-info`, and `correlator=C1`.
- **Answer device-info.** Run `pnp-verify work-response --run "$RUN" --correlator C1 --xmlns urn:cisco:pnp:device-info --success 1 --hostname sw1 --platform C9300-24P --version 17.9.4 --save c1-bye.xml`. The summary has `bye=yes` and `correlator=C1`.
- **Poll for config.** Run `pnp-verify work-request --run "$RUN" --correlator C2 --save c2-config.xml`. The summary has `xmlns=urn:cisco:pnp:config-upgrade`, `apply_to=startup`, `noreload=yes`, `reload=no`, and `location` equal to the launch `base` plus `/configs/FCW1234.cfg`.
- **Download the config.** Run `pnp-verify download --run "$RUN" --path /configs/FCW1234.cfg --save config-body.txt`. The summary has `http=200`, `bytes=12`, and `text=hostname sw1`.
- **Accept the config.** Run `pnp-verify work-response --run "$RUN" --correlator C2 --xmlns urn:cisco:pnp:config-upgrade --success 1 --save c2-bye.xml`. The summary has `bye=yes` and `correlator=C2`.
- **Poll for the end.** Run `pnp-verify work-request --run "$RUN" --correlator C3 --save c3-terminate.xml`. The summary has `xmlns=urn:cisco:pnp:backoff`, `terminate=yes`, and `reason=provisioning complete`.
- **Read status.** Run `pnp-verify status --run "$RUN" --save status.json`. The JSON list has one object whose `serial` is `FCW1234`, `state` is `done`, and `hostname` is `sw1`.
- **Proof.** Evidence contains `c1-device-info.xml`, `c1-device-info.request.xml`, `c2-config.xml`, `config-body.txt`, `c3-terminate.xml`, and `status.json`. `config-body.txt` is `hostname sw1`. `status.json` shows `done`.

## Gotchas

- The first work request is device-info. A config offer on `C1` means this run was started with device-info off, which launch does not do.
- The config response correlator must be `C2`, the correlator on the offer. A mismatched correlator still returns bye and leaves the device waiting.
- `bytes=12` matches the text `hostname sw1` with no added newline. Do not assert a trailing newline unless the seed text contains one.
- Status `done` without the downloaded bytes is not proof that the device could fetch the file.
