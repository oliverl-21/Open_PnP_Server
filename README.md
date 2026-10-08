# Cisco PnP without APIC/DNAC

A small HTTP Plug and Play server for a blank IOS-XE device. It answers the Cisco Open Plug-n-Play work-request exchange. The exchange identifies the device, installs an image when one is mapped, copies a config, and tells the agent to stop.

Credits:

- https://github.com/dmfigol/cisco-pnp-server
- https://github.com/pmeffre/ciscopnp

The protocol this follows is the archived [Cisco Open Plug-n-Play specification](https://developer.cisco.com/site/open-plug-n-play/learn/learn-open-pnp-protocol/).

## Run

```shell
pip install -r requirements.txt
python3 main.py
```

The process listens on `0.0.0.0:8080`. URLs sent to the device use the autodetected local address. `--ip` changes that advertised address when NAT sits between the device and the server. `--bind` changes the listen address.

```shell
./main.py --ip 198.18.168.3 --port 8080
./main.py --bind 192.0.2.10 --ip 198.18.168.3
```

`--no-device-info` skips the device-info query and offers config on the first work request.

## What the device sees

1. `GET /pnp/HELLO` returns `200`.
2. The first `POST /pnp/WORK-REQUEST` gets a device-info request.
3. If `image_map/<serial>.txt` or `image_map/<pid>.txt` names a file in `sw_images/`, the next poll gets an image-install request. Image install always asks the device to reload.
4. The next poll gets a config-upgrade for the first file that exists: `configs/<serial>.cfg`, then `configs/<pid>.cfg`, then `configs/default.cfg`.
5. Each `POST /pnp/WORK-RESPONSE` gets a bye. On success the server moves to the next step. On failure it retries that step, then sends backoff terminate. A failed device-info query is skipped after the retry limit so config can still run.
6. After config-upgrade succeeds, later polls get backoff `<terminate/>`, which tells the agent to drop the PnP profile. Until a config file exists, the server sends `<callbackAfter>` and waits.

State is stored in `state.json`, including across the reload an image install triggers. `./main.py --forget <serial>` clears one device so provisioning can start over.

Default config-upgrade options match the previous behavior. `applyTo` is startup and the request includes `<noReload/>`. The spec default for a missing `applyTo` is startup, so the copy lands in startup-config and the running config stays at the factory config until a reload. Pass `--reload` to boot that file. `--apply-to running` applies it immediately. `--apply-to running --reload` also sets `saveConfig` so the running config is written before the reload. `--abort-on-syntax-fault` stops the copy on the first CLI error. `--checksum` sends an MD5 of the file.

```shell
./main.py --reload --checksum --abort-on-syntax-fault --max-retries 3
```

`--username` and `--password` are added to later messages only when the agent sets `authRequired`. `--restrict-downloads` serves a file only to the address that was just offered that file. The server honors `X-Real-IP` when a proxy sets it.

## Files

```text
configs/FCW1234.cfg          CLI config for that serial
configs/C9300-24P.cfg        used when no serial file exists
configs/default.cfg          used when neither of the above exists
image_map/FCW1234.txt        one filename inside sw_images/, comments allowed
sw_images/cat9k.bin          image bytes served at /images/cat9k.bin
```

A PID that contains `/` is not used as a filename.

## DHCP

Point a blank device at the server with option 43:

```text
5A1D;K4;B2;I198.18.168.3;J8080
```

- `5` PnP DHCP sub-option
- `A` active
- `1` version
- `D` debug on. `N` turns debug off.
- `K4` HTTP. `K5` is HTTPS.
- `B2` IPv4
- `I` server address
- `J` server port

Leave options 66, 67, and 150 empty for these clients so they do not fight ZTP.

The agent identifies itself in option 60. The archived spec says `CiscoPnP`, one step on that same page says `Cisco PnP`, and current DNA Center pools match `ciscopnp`. Match the string your IOS-XE release actually sends.

HTTPS:

```shell
./main.py --cert server.pem --key server.key --trustpool bundle.p7b --port 443 --ip 198.18.168.3
```

`bundle.p7b` is served at `/ca/trustpool`. Option 43 then needs `K5`, `T` set to that URL, and `Z` set to an NTP server. The certificate name has to match the address or hostname in option 43.

## Ansible

`--inventory-csv ./ansible/inventory/hosts.csv` records devices as they answer device-info. Columns are `host,os,serial,hostname,platform,version,udi`. `host` is the learned hostname, or the serial when device-info was skipped. `os` is `ios`.

`ansible/plugins/csv.py` and `ansible/inventory/csv.yaml` read a CSV inventory. Point `source` at the generated file and drop keyed groups for columns that file does not have.

## Status

`GET /status` shows each device, the step it is on, and the last error.

## Tested on hardware

- Catalyst 8000v, IOS-XE 17.5.1 and 17.4.2
- Catalyst 9200L and 9300 are listed without a version in the original notes

The protocol conversation is covered by `python3 -m unittest discover -s tests`.

## Not implemented

Capability, certificate install, CLI exec, licensing, SMU, script, topology, NAPP, DHCP snooping, syslog, SNMP, redirection, and XMPP are part of the Cisco spec and are not this server.
