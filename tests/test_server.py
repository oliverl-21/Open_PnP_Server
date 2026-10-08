import csv
import hashlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xmltodict

from main import create_app, main, parse_args, settings_from_args
from pnp import Settings

UDI = "PID:C9300-24P,VID:V01,SN:FCW1234"
DEVICE_INFO = "urn:cisco:pnp:device-info"
CONFIG = "urn:cisco:pnp:config-upgrade"
IMAGE = "urn:cisco:pnp:image-install"
INFO_BODY = """
    <hardwareInfo><hostname>sw1</hostname><platformName>C9300-24P</platformName></hardwareInfo>
    <imageInfo><versionString>17.9.4</versionString><imageFile>flash:cat9k.bin</imageFile></imageInfo>
"""
ERROR_BODY = """
    <errorInfo>
      <errorSeverity>ERROR</errorSeverity>
      <errorCode>PnP Service Error 1402</errorCode>
      <errorMessage>Invalid input detected</errorMessage>
    </errorInfo>
    <serviceLog>bad cli</serviceLog>
"""


def work_xml(udi=UDI, correlator="C1", auth="false"):
    return f"""<pnp xmlns="urn:cisco:pnp" version="1.0" udi="{udi}">
      <info xmlns="urn:cisco:pnp:work-info" correlator="{correlator}">
        <deviceId><udi>{udi}</udi><authRequired>{auth}</authRequired></deviceId>
      </info>
    </pnp>"""


def response_xml(udi=UDI, correlator="C1", success="1", xmlns=CONFIG, extra=""):
    return f"""<pnp xmlns="urn:cisco:pnp" version="1.0" udi="{udi}">
      <response correlator="{correlator}" success="{success}" xmlns="{xmlns}">{extra}</response>
    </pnp>"""


def parsed(response):
    try:
        return xmltodict.parse(response.data)
    finally:
        response.close()


def contents(response):
    try:
        return response.data
    finally:
        response.close()


def request_kind(document):
    return document["pnp"]["request"]["@xmlns"]


class ServerTests(unittest.TestCase):  # pylint: disable=too-many-public-methods
    """Flask conversation against the Open Plug-n-Play work-request flow."""

    def setUp(self):
        self.tmp = TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def settings(self, **overrides):
        values = {
            "advertise_ip": "198.18.1.10",
            "state_file": self.root / "state.json",
            "configs_dir": self.root / "configs",
            "images_dir": self.root / "sw_images",
            "image_map_dir": self.root / "image_map",
        }
        values.update(overrides)
        return Settings(**values)

    def client(self, **overrides):
        settings = self.settings(**overrides)
        return create_app(settings).test_client(), settings

    def write_config(self, settings, name, body=b"hostname sw1\n"):
        path = settings.configs_dir / name
        path.write_bytes(body)
        return path

    def ask(self, client, correlator, udi=UDI, auth="false", headers=None):
        response = client.post(
            "/pnp/WORK-REQUEST",
            data=work_xml(udi=udi, correlator=correlator, auth=auth),
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.mimetype, "text/xml")
        return parsed(response)

    def answer(self, client, correlator, xmlns, **options):
        response = client.post(
            "/pnp/WORK-RESPONSE",
            data=response_xml(
                udi=options.get("udi", UDI),
                correlator=correlator,
                success=options.get("success", "1"),
                xmlns=xmlns,
                extra=options.get("extra", ""),
            ),
        )
        self.assertEqual(response.status_code, 200, response.data)
        body = parsed(response)
        self.assertIn("bye", body["pnp"]["info"]["workInfo"])
        self.assertEqual(body["pnp"]["info"]["@correlator"], correlator)
        return body

    def test_hello_and_root(self):
        client, _settings = self.client()
        hello = client.get("/pnp/HELLO")
        self.assertEqual(hello.status_code, 200)
        self.assertEqual(hello.data, b"")
        self.assertIn(b"Open PnP Server", client.get("/").data)

    def test_day0_finishes_with_terminate(self):
        client, settings = self.client()
        self.write_config(settings, "FCW1234.cfg", b"hostname sw1\n")
        first = self.ask(client, "C1")
        self.assertEqual(request_kind(first), DEVICE_INFO)
        self.assertEqual(first["pnp"]["@udi"], UDI)
        self.assertEqual(first["pnp"]["request"]["@correlator"], "C1")
        self.assertEqual(first["pnp"]["request"]["deviceInfo"]["@type"], "all")
        self.answer(client, "C1", DEVICE_INFO, extra=INFO_BODY)

        second = self.ask(client, "C2")
        request = second["pnp"]["request"]
        self.assertEqual(request["@xmlns"], CONFIG)
        copy = request["config"]["copy"]
        self.assertEqual(copy["applyTo"], "startup")
        self.assertIn("noReload", request)
        self.assertNotIn("reload", request)
        location = copy["source"]["location"]
        self.assertEqual(location, "http://198.18.1.10:8080/configs/FCW1234.cfg")
        self.assertEqual(contents(client.get("/configs/FCW1234.cfg")), b"hostname sw1\n")
        self.answer(client, "C2", CONFIG)

        third = self.ask(client, "C3")
        backoff = third["pnp"]["request"]["backoff"]
        self.assertEqual(request_kind(third), "urn:cisco:pnp:backoff")
        self.assertIn("terminate", backoff)
        self.assertEqual(backoff["reason"], "provisioning complete")
        status = client.get("/status").get_json()
        self.assertEqual(status[0]["state"], "done")
        self.assertEqual(status[0]["hostname"], "sw1")

    def test_missing_config_waits_until_the_file_exists(self):
        client, settings = self.client(device_info=False)
        waiting = self.ask(client, "C1")
        backoff = waiting["pnp"]["request"]["backoff"]
        self.assertIn("callbackAfter", backoff)
        self.assertEqual(backoff["callbackAfter"]["minutes"], "15")
        self.assertNotIn("hours", backoff["callbackAfter"])
        self.write_config(settings, "FCW1234.cfg")
        offered = self.ask(client, "C2")
        self.assertEqual(request_kind(offered), CONFIG)

    def test_callback_splits_hours(self):
        client, _settings = self.client(device_info=False, callback_minutes=75)
        waiting = self.ask(client, "C1")
        callback = waiting["pnp"]["request"]["backoff"]["callbackAfter"]
        self.assertEqual(callback["hours"], "1")
        self.assertEqual(callback["minutes"], "15")

    def test_unrecognized_udi_is_backoff(self):
        client, _settings = self.client()
        document = self.ask(client, "C1", udi="not-a-udi")
        self.assertEqual(document["pnp"]["@udi"], "not-a-udi")
        self.assertEqual(document["pnp"]["request"]["backoff"]["reason"], "unrecognized udi")
        bad = client.post("/pnp/WORK-REQUEST", data=b"<pnp>")
        self.assertEqual(bad.status_code, 400)

    def test_empty_vid_and_slash_in_pid(self):
        udi = "PID:CISCO2921/K9,VID:,SN:FOC1234"
        client, settings = self.client(device_info=False)
        self.write_config(settings, "FOC1234.cfg", b"hostname edge\n")
        document = self.ask(client, "C1", udi=udi)
        location = document["pnp"]["request"]["config"]["copy"]["source"]["location"]
        self.assertTrue(location.endswith("/configs/FOC1234.cfg"))

    def test_config_name_falls_back_to_pid_then_default(self):
        client, settings = self.client(device_info=False)
        self.write_config(settings, "default.cfg", b"default\n")
        self.write_config(settings, "C9300-24P.cfg", b"pid\n")
        self.write_config(settings, "FCW9999.cfg", b"serial\n")
        default_offer = self.ask(client, "C1", udi="PID:OTHER-1,VID:V01,SN:FCW0001")
        self.assertTrue(default_offer["pnp"]["request"]["config"]["copy"]["source"]["location"].endswith("default.cfg"))
        pid_offer = self.ask(client, "C2", udi="PID:C9300-24P,VID:V01,SN:FCW0002")
        self.assertTrue(pid_offer["pnp"]["request"]["config"]["copy"]["source"]["location"].endswith("C9300-24P.cfg"))
        serial_offer = self.ask(client, "C3", udi="PID:C9300-24P,VID:V01,SN:FCW9999")
        self.assertTrue(serial_offer["pnp"]["request"]["config"]["copy"]["source"]["location"].endswith("FCW9999.cfg"))

    def test_reload_checksum_and_abort_flag(self):
        client, settings = self.client(
            device_info=False,
            reload=True,
            checksum=True,
            abort_on_syntax_fault=True,
            apply_to="running",
        )
        body = b"hostname sw1\n"
        self.write_config(settings, "FCW1234.cfg", body)
        document = self.ask(client, "C1")
        copy = document["pnp"]["request"]["config"]["copy"]
        reload = document["pnp"]["request"]["reload"]
        self.assertEqual(copy["applyTo"], "running")
        self.assertIn("abortOnSyntaxFault", copy)
        self.assertEqual(copy["source"]["checksum"], hashlib.md5(body, usedforsecurity=False).hexdigest())
        self.assertEqual(reload["saveConfig"], "true")
        self.assertEqual(reload["delayIn"], "0")

    def test_failures_then_terminate(self):
        client, settings = self.client(device_info=False, max_retries=2)
        self.write_config(settings, "FCW1234.cfg")
        self.ask(client, "C1")
        self.answer(client, "C1", CONFIG, success="0", extra=ERROR_BODY)
        self.assertEqual(request_kind(self.ask(client, "C2")), CONFIG)
        self.answer(client, "C2", CONFIG, success="0", extra=ERROR_BODY)
        finished = self.ask(client, "C3")
        self.assertIn("terminate", finished["pnp"]["request"]["backoff"])
        self.assertIn("PnP Service Error 1402", finished["pnp"]["request"]["backoff"]["reason"])

    def test_mismatched_correlator_does_not_advance(self):
        client, settings = self.client(device_info=False)
        self.write_config(settings, "FCW1234.cfg")
        self.ask(client, "C1")
        self.answer(client, "OTHER", CONFIG)
        self.assertEqual(request_kind(self.ask(client, "C1")), CONFIG)

    def test_device_info_failure_still_reaches_config(self):
        client, settings = self.client(max_retries=1)
        self.write_config(settings, "FCW1234.cfg")
        self.ask(client, "C1")
        self.answer(client, "C1", DEVICE_INFO, success="0", extra=ERROR_BODY)
        self.assertEqual(request_kind(self.ask(client, "C2")), CONFIG)

    def test_image_then_config_survives_restart(self):
        client, settings = self.client(device_info=False)
        (settings.images_dir / "ios.bin").write_bytes(b"image-bytes")
        (settings.image_map_dir / "FCW1234.txt").write_text("# comment\nios.bin\n", encoding="utf-8")
        image_offer = self.ask(client, "C1")
        self.assertEqual(request_kind(image_offer), IMAGE)
        location = image_offer["pnp"]["request"]["image"]["copy"]["source"]["location"]
        self.assertTrue(location.endswith("/images/ios.bin"))
        self.assertIn("reload", image_offer["pnp"]["request"])
        self.assertEqual(contents(client.get("/images/ios.bin")), b"image-bytes")
        self.answer(client, "C1", IMAGE)
        waiting = self.ask(client, "C2")
        self.assertIn("callbackAfter", waiting["pnp"]["request"]["backoff"])
        self.write_config(settings, "FCW1234.cfg", b"after-image\n")
        restarted = create_app(settings).test_client()
        config_offer = self.ask(restarted, "C3")
        self.assertEqual(request_kind(config_offer), CONFIG)
        self.answer(restarted, "C3", CONFIG)
        self.assertIn("terminate", self.ask(restarted, "C4")["pnp"]["request"]["backoff"])

    def test_restrict_downloads_to_the_offered_address(self):
        client, settings = self.client(device_info=False, restrict_downloads=True)
        self.write_config(settings, "FCW1234.cfg", b"secret\n")
        self.write_config(settings, "other.cfg", b"nope\n")
        headers = {"X-Real-IP": "10.1.1.9"}
        self.assertEqual(client.get("/configs/FCW1234.cfg", headers=headers).status_code, 403)
        self.ask(client, "C1", headers=headers)
        self.assertEqual(client.get("/configs/FCW1234.cfg").status_code, 403)
        self.assertEqual(contents(client.get("/configs/FCW1234.cfg", headers=headers)), b"secret\n")
        self.assertEqual(client.get("/configs/other.cfg", headers=headers).status_code, 403)

    def test_credentials_when_auth_required(self):
        client, settings = self.client(device_info=False, username="admin", password="p&ass")
        self.write_config(settings, "FCW1234.cfg")
        open_offer = self.ask(client, "C1", auth="false")
        self.assertNotIn("@usr", open_offer["pnp"])
        authed = self.ask(client, "C2", auth="true")
        self.assertEqual(authed["pnp"]["@usr"], "admin")
        self.assertEqual(authed["pnp"]["@pwd"], "p&ass")

    def test_inventory_and_trustpool(self):
        trustpool = self.root / "bundle.p7b"
        trustpool.write_bytes(b"bundle")
        inventory = self.root / "hosts.csv"
        client, _settings = self.client(inventory_csv=inventory, trustpool=trustpool)
        self.ask(client, "C1")
        self.answer(client, "C1", DEVICE_INFO, extra=INFO_BODY)
        self.assertEqual(contents(client.get("/ca/trustpool")), b"bundle")
        with inventory.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["host"], "sw1")
        self.assertEqual(rows[0]["serial"], "FCW1234")
        self.assertEqual(rows[0]["version"], "17.9.4")
        self.assertEqual(rows[0]["os"], "ios")

    def test_forget_allows_the_device_to_start_over(self):
        client, settings = self.client(device_info=False)
        self.write_config(settings, "FCW1234.cfg")
        self.ask(client, "C1")
        self.answer(client, "C1", CONFIG)
        code = main([
            "--forget", "FCW1234",
            "--ip", "198.18.1.10",
            "--state-file", str(settings.state_file),
            "--configs-dir", str(settings.configs_dir),
            "--images-dir", str(settings.images_dir),
            "--image-map-dir", str(settings.image_map_dir),
        ])
        self.assertEqual(code, 0)
        restarted = create_app(settings).test_client()
        self.assertEqual(request_kind(self.ask(restarted, "C2")), CONFIG)
        missing = main([
            "--forget", "NOPE",
            "--ip", "198.18.1.10",
            "--state-file", str(settings.state_file),
        ])
        self.assertEqual(missing, 1)

    def test_cli_defaults(self):
        args = parse_args(["--ip", "198.18.1.10", "--no-device-info", "--reload", "--port", "8081"])
        settings = settings_from_args(args)
        self.assertFalse(settings.device_info)
        self.assertTrue(settings.reload)
        self.assertEqual(settings.port, 8081)
        self.assertEqual(settings.advertise_ip, "198.18.1.10")
        self.assertEqual(settings.public_base, "http://198.18.1.10:8081")


if __name__ == "__main__":
    unittest.main()
