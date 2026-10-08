#!/usr/bin/env python3

import argparse
import logging
import socket
from ipaddress import ip_address
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from pnp import Provisioner, Settings, ensure_dirs, validate_settings

log = logging.getLogger("openpnp")
ROOT = Path(__file__).resolve().parent


def create_app(settings: Settings, provisioner: Provisioner | None = None) -> Flask:
    ensure_dirs(settings)
    if provisioner is None:
        provisioner = Provisioner(settings)
    app = Flask(__name__, template_folder=str(ROOT / "templates"))

    @app.route("/")
    def root():
        return "Open PnP Server\n"

    @app.route("/status")
    def status():
        return jsonify(provisioner.snapshot())

    @app.route("/ca/trustpool")
    def trustpool():
        path = settings.trustpool
        if path is None or not path.is_file():
            return "trustpool is not configured\n", 404
        return send_from_directory(path.parent, path.name)

    @app.route("/configs/<path:filename>")
    def serve_configs(filename):
        return _download(settings.configs_dir, filename, "configs", provisioner)

    @app.route("/images/<path:filename>")
    def serve_images(filename):
        return _download(settings.images_dir, filename, "images", provisioner)

    @app.route("/pnp/HELLO")
    def pnp_hello():
        return "", 200

    @app.route("/pnp/WORK-REQUEST", methods=["POST"])
    def pnp_work_request():
        return _render(provisioner.work_request(request.data, _client_ip()))

    @app.route("/pnp/WORK-RESPONSE", methods=["POST"])
    def pnp_work_response():
        return _render(provisioner.work_response(request.data, _client_ip()))

    return app


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="OpenPnPServer")
    parser.add_argument("-i", "--ip", type=ip_address, help="Address embedded in config and image URLs. Set this when NAT hides the server. Default: autodetected local address.")
    parser.add_argument("--bind", default="0.0.0.0", help="Local address to listen on. Default: 0.0.0.0.")
    parser.add_argument("-p", "--port", type=_tcp_port, default=8080, help="Listen port. Default: 8080.")
    parser.add_argument("--apply-to", choices=["startup", "running", "AP"], default="startup", help="Where config-upgrade copies the file. Default: startup.")
    parser.add_argument("--reload", action="store_true", help="Reload after config-upgrade. Default is noReload. Image install always reloads.")
    parser.add_argument("--abort-on-syntax-fault", action="store_true", help="Abort config-upgrade on the first CLI error.")
    parser.add_argument("--checksum", action="store_true", help="Send an MD5 checksum with config and image copies.")
    parser.add_argument("--device-info", action=argparse.BooleanOptionalAction, default=True, help="Ask for device-info before other work. Default: on. --no-device-info skips it.")
    parser.add_argument("--max-retries", type=_positive_int, default=3, help="Failed attempts for one job before backoff terminate. A failed device-info query is skipped instead. Default: 3.")
    parser.add_argument("--callback-minutes", type=_positive_int, default=15, help="Delay told to the agent when no config or image is ready. Default: 15.")
    parser.add_argument("--username", default="", help="Device login sent when the agent sets authRequired.")
    parser.add_argument("--password", default="", help="Device password sent when the agent sets authRequired.")
    parser.add_argument("--restrict-downloads", action="store_true", help="Serve a file only to the address that was just offered that file.")
    parser.add_argument("--inventory-csv", type=Path, help="Append learned devices to this CSV.")
    parser.add_argument("--cert", type=Path, help="TLS certificate. Requires --key.")
    parser.add_argument("--key", type=Path, help="TLS private key. Requires --cert.")
    parser.add_argument("--trustpool", type=Path, help="CA bundle served at /ca/trustpool.")
    parser.add_argument("--state-file", type=Path, default=ROOT / "state.json")
    parser.add_argument("--configs-dir", type=Path, default=ROOT / "configs")
    parser.add_argument("--images-dir", type=Path, default=ROOT / "sw_images")
    parser.add_argument("--image-map-dir", type=Path, default=ROOT / "image_map")
    parser.add_argument("--forget", metavar="SERIAL", help="Remove one device from the state file and exit.")
    return parser.parse_args(argv)


def settings_from_args(args) -> Settings:
    advertise = str(args.ip) if args.ip else detect_local_ip()
    settings = Settings(
        bind=args.bind,
        port=args.port,
        advertise_ip=advertise,
        apply_to=args.apply_to,
        reload=args.reload,
        abort_on_syntax_fault=args.abort_on_syntax_fault,
        checksum=args.checksum,
        device_info=args.device_info,
        max_retries=args.max_retries,
        callback_minutes=args.callback_minutes,
        username=args.username,
        password=args.password,
        restrict_downloads=args.restrict_downloads,
        inventory_csv=args.inventory_csv,
        cert=args.cert,
        key=args.key,
        trustpool=args.trustpool,
        state_file=args.state_file,
        configs_dir=args.configs_dir,
        images_dir=args.images_dir,
        image_map_dir=args.image_map_dir,
    )
    validate_settings(settings)
    return settings


def detect_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        log.warning("could not detect a local IP; advertising 127.0.0.1")
        return "127.0.0.1"


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    args = parse_args(argv)
    settings = settings_from_args(args)
    provisioner = Provisioner(settings)
    if args.forget:
        return _forget(provisioner, args.forget)
    _log_startup(settings)
    app = create_app(settings, provisioner)
    app.run(host=settings.bind, port=settings.port, threaded=True, ssl_context=_ssl_context(settings))
    return 0


def _render(result: dict):
    if result.get("status") == 400:
        return result.get("body", "bad request") + "\n", 400
    body = render_template(result["template"], **result["context"])
    return Response(body, mimetype="text/xml")


def _download(directory: Path, filename: str, kind: str, provisioner: Provisioner):
    address = _client_ip()
    if not provisioner.permits(address, kind, filename):
        log.info("serial=- ip=%s correlator=- service=download result=deny detail=%s/%s", address, kind, filename)
        return "forbidden\n", 403
    log.info("serial=- ip=%s correlator=- service=download result=ok detail=%s/%s", address, kind, filename)
    return send_from_directory(directory, filename)


def _client_ip() -> str:
    return request.headers.get("X-Real-IP", request.remote_addr) or ""


def _forget(provisioner: Provisioner, serial: str) -> int:
    if provisioner.forget(serial):
        print(f"forgot {serial}")
        return 0
    print(f"no state for {serial}")
    return 1


def _log_startup(settings: Settings) -> None:
    log.info(
        "listening on %s:%s advertising %s apply_to=%s reload=%s device_info=%s",
        settings.bind,
        settings.port,
        settings.public_base,
        settings.apply_to,
        settings.reload,
        settings.device_info,
    )


def _ssl_context(settings: Settings):
    if settings.cert and settings.key:
        return str(settings.cert), str(settings.key)
    return None


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def _tcp_port(value: str) -> int:
    number = int(value)
    if number < 1 or number > 65535:
        raise argparse.ArgumentTypeError("port must be 1-65535")
    return number


if __name__ == "__main__":
    raise SystemExit(main())
