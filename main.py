#!/usr/bin/env python3

import sys
from pathlib import Path

sys.path.append("./configs")
import argparse
import asyncio
import logging
import re
from ipaddress import ip_address

import flask.cli
import requests
import xmltodict
from flask import Flask, Response, render_template, request, send_from_directory

# disable default Flask logging to stdout
logging.getLogger("werkzeug").disabled = True
flask.cli.show_server_banner = lambda *args: None


# get local IP logic
async def get_local_ip():
    loop = asyncio.get_event_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, remote_addr=("8.8.8.8", 80)
    )
    localip = transport.get_extra_info("sockname")[0]
    transport.close()
    return localip


# ArgumentParser
parser = argparse.ArgumentParser(prog="OpenPnPServer")
parser.add_argument(
    "-i",
    "--ip",
    type=ip_address,
    help="IP the Server should Listen on, Default local IP",
)
parser.add_argument(
    "-p",
    "--port",
    type=int,
    default="8080",
    help="Port the Server should Listen on, Default 8080",
)
args = parser.parse_args()

if not args.ip:
    LOCAL_IP = asyncio.run(get_local_ip())
else:
    LOCAL_IP = str(args.ip)

LOCAL_PORT = str(args.port)

# build var from args or local values/defaults
HTTP_SERVER = LOCAL_IP + ":" + LOCAL_PORT


# we are ready
print(f"\nServer will run on IP:{LOCAL_IP} and Port:{LOCAL_PORT}\n")


# Flask
app = Flask(__name__, template_folder="./templates")
current_dir = Path(__file__)

SERIAL_NUM_RE = re.compile(
    r"PID:(?P<product_id>\w+(?:-\w+)*),VID:(?P<hw_version>\w+),SN:(?P<serial_number>\w+)"
)


def work_request(host, call="device_info"):
    url = f"http://{host}/pnp/WORK-REQUEST"
    with open(current_dir / f"{call}.xml", encoding="ascii") as f:
        data = f.read()
    return requests.post(url, data, timeout=3)


def get_device_info(host):
    url = f"http://{host}/pnp/WORK-REQUEST"


@app.route("/test-xml")
def test_xml():
    jinja_context = {
        "http_server": HTTP_SERVER,
        "config_filename": "test.cfg",
        "udi": 123,
        "correlator_id": 123,
    }
    result = render_template("load_config.xml", **jinja_context)
    return Response(result, mimetype="text/xml")


@app.route("/")
def root():
    src_add = request.environ.get("HTTP_X_REAL_IP", request.remote_addr)
    print(f"SOURCE-ADDRESS: {src_add}")
    return "Hello!"


@app.route("/configs/<path:path>")
def serve_configs(path):
    return send_from_directory("configs", path)


@app.route("/images/<path:path>")
def serve_sw_images(path):
    return send_from_directory("sw_images", path)


@app.route("/pnp/HELLO")
def pnp_hello():
    return "", 200


@app.route("/pnp/WORK-REQUEST", methods=["POST"])
def pnp_work_request():
    # src_add = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
    data = xmltodict.parse(request.data)
    correlator_id = data["pnp"]["info"]["@correlator"]
    udi = data["pnp"]["@udi"]
    udi_match = SERIAL_NUM_RE.match(udi)
    serial_number = udi_match.group("serial_number")
    try:
        config_file = serial_number
        # config_file = DEVICES[serial_number]["config-filename"]
        jinja_context = {
            "udi": udi,
            "correlator_id": correlator_id,
            "config_filename": config_file,
            "http_server": HTTP_SERVER,
        }
        result_data = render_template("load_config.xml", **jinja_context)
        sys.stderr.write(
            "Loading " + config_file + " on " + request.environ["REMOTE_ADDR"] + "\n"
        )
        return Response(result_data, mimetype="text/xml")
    except Exception:
        sys.stderr.write(
            "Unable to load "
            + config_file
            + ".cfg"
            + " on "
            + request.environ["REMOTE_ADDR"]
            + " ("
            + serial_number
            + ")\n"
        )
        return ""


@app.route("/pnp/WORK-RESPONSE", methods=["POST"])
def pnp_work_response():
    print(request.data)
    data = xmltodict.parse(request.data)
    correlator_id = data["pnp"]["response"]["@correlator"]
    udi = data["pnp"]["@udi"]
    jinja_context = {
        "udi": udi,
        "correlator_id": correlator_id,
    }
    result_data = render_template("bye.xml", **jinja_context)
    return Response(result_data, mimetype="text/xml")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=LOCAL_PORT)
