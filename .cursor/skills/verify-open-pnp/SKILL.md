---
name: verify-open-pnp
description: >
  Drive the Open PnP Server the way a blank IOS-XE device does. Launch an
  isolated HTTP instance, post the work-request exchange, and keep the response
  bodies. Use when the user runs /verify-open-pnp, asks to prove the PnP
  server, or needs to check hello, day-0 config, image install, config backoff,
  or forget.
---

# Verify Open PnP Server

The operator starts `main.py` and drops config and image files. A device polls `GET /pnp/HELLO`, then `POST /pnp/WORK-REQUEST` and `POST /pnp/WORK-RESPONSE`, and downloads the URL in the offer. Prove those calls against a server this run started. Unit tests and `GET /status` alone are not the proof.

Other real paths are not in the feature map. They are `GET /ca/trustpool`, `--inventory-csv`, `--restrict-downloads`, and TLS via `--cert` and `--key`. Do not report those as verified through the mapped features.

## Launch

From the Open PnP Server repo, put the helper on `PATH` for this shell.

```bash
export PATH="$PWD/.cursor/skills/verify-open-pnp/scripts:$PATH"
export RUN="/tmp/open-pnp-verify-$RANDOM"
pnp-verify launch --run "$RUN"
```

`launch` creates `$RUN` outside the repo, creates `.venv` on first use, and installs `Flask==3.1.3` and `xmltodict==1.0.4` when those imports are missing. It listens on `127.0.0.1` and an ephemeral port. It refuses port `8080` and any run directory inside the repo, so it cannot write the checkout's `state.json` or `configs/`.

Ready when both are true. The log `$RUN/server.log` contains `listening on 127.0.0.1:<port> advertising http://127.0.0.1:<port>`. `GET /` returns `Open PnP Server` and a newline.

The launch line prints `pid`, `base`, `run`, and `evidence`. Two runs need two `--run` directories. A second `launch` on a live run exits without starting another process.

There is no dry-run mode. Every `launch` binds a real port and writes a real state file under `$RUN`.

## Doctor

Run this before the first drive, and again after anything unexpected.

```bash
pnp-verify doctor --run "$RUN"
```

`doctor ok` means the recorded pid is alive, its command line is this repo's `main.py` with this run's `--state-file` and `--port`, the log has the listening line, `GET /` is `Open PnP Server`, `GET /pnp/HELLO` is an empty `200`, and `GET /status` is a JSON list. Anything else prints `doctor fail` and the reason. Do not drive that run.

## Drive

The fixture device is `PID:C9300-24P,VID:V01,SN:FCW1234`. Serial `FCW1234`, pid `C9300-24P`. Launch leaves device-info on, so the first work request asks for device-info before image or config.

Commands, all keyed by `--run "$RUN"`.

- `pnp-verify hello --run "$RUN" --save hello.txt` checks `GET /` and `GET /pnp/HELLO`.
- `pnp-verify seed-config --run "$RUN" --name FCW1234.cfg --text "hostname sw1"` writes the config the device will download.
- `pnp-verify seed-image --run "$RUN" --name ios.bin --text "image-bytes" --map FCW1234.txt` writes the image and the map line.
- `pnp-verify work-request --run "$RUN" --correlator C1 --save c1.xml` posts one work request. Add `--auth true` only when the recipe says the agent set `authRequired`.
- `pnp-verify work-response --run "$RUN" --correlator C1 --xmlns urn:cisco:pnp:device-info --success 1 --hostname sw1 --platform C9300-24P --version 17.9.4 --save c1-bye.xml` answers that correlator. Config and image replies use `urn:cisco:pnp:config-upgrade` and `urn:cisco:pnp:image-install` and omit the hostname flags.
- `pnp-verify download --run "$RUN" --path /configs/FCW1234.cfg --save config-body.txt` fetches the offered file. Image paths start with `/images/`.
- `pnp-verify status --run "$RUN" --save status.json` reads the operator status list.
- `pnp-verify stop --run "$RUN"` then `pnp-verify forget --run "$RUN" --serial FCW1234` clears one serial. Forget refuses while the pid is alive.
- `pnp-verify launch --run "$RUN"` starts the same directories again after a stop.

A work-request or work-response line looks like this.

```text
http=200 xmlns=urn:cisco:pnp:config-upgrade correlator=C2 terminate=no callback=no minutes= location=http://127.0.0.1:9/configs/FCW1234.cfg apply_to=startup reason= bye=no reload=no noreload=yes
```

Match `location` to the `base` value from launch plus the path. `--save` writes the response and a sibling `.request.xml` under the evidence directory. The request file is the action. The response file is the result.

Sequences for each feature are in `features/`. A pass that drives one entry point does not cover the others listed there.

## Evidence

Proof files go to the `evidence=` directory printed by launch. That path is `.cursor/skills/verify-open-pnp/evidence/<run-name>/`. Cleanup never deletes it.

Capture the posted request, the XML response, and the side effect. For a config offer, the side effect is the downloaded file bytes and a later `GET /status` row whose `state` is `done`. For an image offer, the side effect is the downloaded image bytes. For forget, the side effect is the next work request after relaunch, which must not be terminate for that serial.

Do not point the server at a mock device. The helper posts the same messages a device posts, to the real routes.

## Cleanup

```bash
pnp-verify cleanup --run "$RUN"
```

Cleanup sends `SIGTERM` to the pid recorded in `$RUN/session.json`, then `SIGKILL` if it is still alive. It deletes `$RUN` only. It does not kill by process name. After cleanup, the evidence directory from the launch line must still exist. If a drive fails, run cleanup before the next attempt so the port and scratch directory are not left behind.

## Helpers

`scripts/pnp-verify` is the only helper. It is executable. The commands above are the whole interface. `pnp-verify --help` lists them.
