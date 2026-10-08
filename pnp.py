import csv
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote
from xml.parsers.expat import ExpatError

import xmltodict

log = logging.getLogger("openpnp")

UDI_RE = re.compile(r"^PID:(?P<pid>[^,]*),VID:(?P<vid>[^,]*),SN:(?P<sn>\S+)$")
INVENTORY_COLUMNS = ["host", "os", "serial", "hostname", "platform", "version", "udi"]


class Udi(NamedTuple):
    """Product id, version id, and serial from a Cisco UDI string."""

    pid: str
    vid: str
    serial: str


class ImageJob(NamedTuple):
    """Image file to offer, or a reason the mapped file is not ready."""

    path: Path | None
    reason: str


@dataclass
class Settings:  # pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments,too-many-locals
    """Listen address, advertised URL, and provisioning options."""
    bind: str = "0.0.0.0"
    port: int = 8080
    advertise_ip: str = "127.0.0.1"
    apply_to: str = "startup"
    reload: bool = False
    abort_on_syntax_fault: bool = False
    checksum: bool = False
    device_info: bool = True
    max_retries: int = 3
    callback_minutes: int = 15
    username: str = ""
    password: str = ""
    restrict_downloads: bool = False
    inventory_csv: Path | None = None
    cert: Path | None = None
    key: Path | None = None
    trustpool: Path | None = None
    state_file: Path = Path("state.json")
    configs_dir: Path = Path("configs")
    images_dir: Path = Path("sw_images")
    image_map_dir: Path = Path("image_map")

    @property
    def scheme(self) -> str:
        if self.cert and self.key:
            return "https"
        return "http"

    @property
    def public_base(self) -> str:
        return f"{self.scheme}://{url_host(self.advertise_ip)}:{self.port}"


def url_host(value: str) -> str:
    text = str(value)
    if ":" in text:
        return f"[{text}]"
    return text


def parse_udi(value: str) -> Udi | None:
    match = UDI_RE.match(value.strip())
    if match is None:
        return None
    return Udi(match.group("pid"), match.group("vid"), match.group("sn"))


def safe_component(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return "/" not in value and "\\" not in value and "\x00" not in value


def delay_parts(total_minutes: int) -> tuple[int, int]:
    total = min(max(int(total_minutes), 1), 47 * 60 + 59)
    return divmod(total, 60)


def md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_config(configs_dir: Path, serial: str, pid: str) -> Path | None:
    names = []
    if safe_component(serial):
        names.append(f"{serial}.cfg")
    if safe_component(pid):
        names.append(f"{pid}.cfg")
    names.append("default.cfg")
    for name in names:
        path = configs_dir / name
        if path.is_file():
            return path
    return None


def resolve_image(image_map_dir: Path, images_dir: Path, serial: str, pid: str) -> ImageJob:
    pointer = _image_pointer(image_map_dir, serial, pid)
    if pointer is None:
        return ImageJob(None, "")
    filename = _pointer_target(pointer)
    if filename is None:
        return ImageJob(None, f"image map {pointer.name} is empty or unsafe")
    path = images_dir / filename
    if path.is_file():
        return ImageJob(path, "")
    return ImageJob(None, f"image file missing: {filename}")


def parse_message(raw: bytes) -> dict:
    try:
        data = xmltodict.parse(raw)
    except ExpatError as exc:
        raise ValueError("malformed xml") from exc
    pnp = data.get("pnp") if isinstance(data, dict) else None
    if not isinstance(pnp, dict):
        raise ValueError("missing pnp element")
    info = _as_dict(pnp.get("info"))
    response = _as_dict(pnp.get("response"))
    device_id = _as_dict(info.get("deviceId"))
    correlator = info.get("@correlator") or response.get("@correlator") or ""
    udi = pnp.get("@udi") or ""
    if not udi or not correlator:
        raise ValueError("udi and correlator are required")
    return {
        "udi": udi,
        "correlator": correlator,
        "auth_required": _is_true(device_id.get("authRequired")),
        "success": str(response.get("@success", "0")) == "1",
        "has_response": bool(response),
        "error": _error_text(response),
        "info": _profile(response),
        "source_ip": "",
    }


def new_device(udi: str, pid: str, vid: str, serial: str) -> dict:
    return {
        "udi": udi,
        "pid": pid,
        "vid": vid,
        "serial": serial,
        "info_done": False,
        "image_done": False,
        "config_done": False,
        "finished": "",
        "pending": "",
        "correlator": "",
        "failures": 0,
        "last_error": "",
        "info": {},
        "source_ip": "",
        "auth_required": False,
        "inventory_written": False,
    }


def write_inventory(path: Path, device: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _read_inventory(path)
    row = _inventory_row(device)
    _replace_row(rows, row)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=INVENTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


class Provisioner:
    """Per-device work queue for the HTTP Plug and Play exchange."""
    def __init__(self, settings: Settings):
        self.settings = settings
        self.devices = {}
        self.allowed = {}
        self._lock = threading.Lock()
        self._load()

    def work_request(self, raw: bytes, source_ip: str) -> dict:
        try:
            message = parse_message(raw)
        except ValueError as exc:
            self._log_message(None, {"source_ip": source_ip}, "work-request", "reject", str(exc))
            return {"status": 400, "body": str(exc)}
        message["source_ip"] = source_ip
        with self._lock:
            result = self._work_request(message)
            self._save()
            return result

    def work_response(self, raw: bytes, source_ip: str) -> dict:
        try:
            message = parse_message(raw)
        except ValueError as exc:
            self._log_message(None, {"source_ip": source_ip}, "work-response", "reject", str(exc))
            return {"status": 400, "body": str(exc)}
        if not message["has_response"]:
            return {"status": 400, "body": "missing response element"}
        message["source_ip"] = source_ip
        with self._lock:
            result = self._work_response(message)
            self._save()
            return result

    def permits(self, source_ip: str, kind: str, filename: str) -> bool:
        if not self.settings.restrict_downloads:
            return True
        with self._lock:
            return (kind, filename) in self.allowed.get(source_ip, set())

    def snapshot(self) -> list:
        with self._lock:
            return [public_device(device) for device in self.devices.values()]

    def forget(self, serial: str) -> bool:
        with self._lock:
            removed = self.devices.pop(serial, None) is not None
            self._save()
            return removed

    def _work_request(self, message: dict) -> dict:
        device = self._device_for(message)
        if device is None:
            return self._backoff(message, "callback", "unrecognized udi", None)
        self._touch(device, message)
        if device["finished"]:
            return self._backoff(message, "terminate", _terminal_reason(device), device)
        if device["pending"]:
            return self._render_pending(device, message)
        return self._advance(device, message)

    def _work_response(self, message: dict) -> dict:
        device = self._existing(message)
        if device is None or not device["pending"]:
            self._log_message(device, message, "bye", "ack", "no pending work")
            return self._bye(message, device)
        if message["correlator"] != device["correlator"]:
            self._log_message(device, message, "bye", "ack", "correlator mismatch")
            return self._bye(message, device)
        if message["success"]:
            self._mark_success(device, message)
        else:
            self._mark_failure(device, message)
        return self._bye(message, device)

    def _advance(self, device: dict, message: dict) -> dict:
        if self.settings.device_info and not device["info_done"]:
            return self._offer_info(device, message)
        image_result = self._image_step(device, message)
        if image_result is not None:
            return image_result
        config = find_config(self.settings.configs_dir, device["serial"], device["pid"])
        if config is not None and not device["config_done"]:
            return self._offer_config(device, message, config)
        if device["config_done"]:
            device["finished"] = "done"
            return self._backoff(message, "terminate", "provisioning complete", device)
        reason = f"no config for {device['serial']}"
        return self._backoff(message, "callback", reason, device)

    def _image_step(self, device: dict, message: dict) -> dict | None:
        if device["image_done"]:
            return None
        image = resolve_image(
            self.settings.image_map_dir,
            self.settings.images_dir,
            device["serial"],
            device["pid"],
        )
        if image.reason:
            return self._backoff(message, "callback", image.reason, device)
        if image.path is None:
            return None
        return self._offer_image(device, message, image.path)

    def _render_pending(self, device: dict, message: dict) -> dict:
        pending = device["pending"]
        if pending == "device-info":
            return self._offer_info(device, message)
        if pending == "image-install":
            image = resolve_image(
                self.settings.image_map_dir,
                self.settings.images_dir,
                device["serial"],
                device["pid"],
            )
            if image.path is not None:
                return self._offer_image(device, message, image.path)
        if pending == "config-upgrade":
            config = find_config(self.settings.configs_dir, device["serial"], device["pid"])
            if config is not None:
                return self._offer_config(device, message, config)
        device["pending"] = ""
        return self._advance(device, message)

    def _mark_success(self, device: dict, message: dict) -> None:
        pending = device["pending"]
        self._log_message(device, message, pending or "-", "success", "")
        if pending == "device-info":
            device["info"] = message["info"]
            device["info_done"] = True
            self._write_inventory(device)
        elif pending == "image-install":
            device["image_done"] = True
        elif pending == "config-upgrade":
            device["config_done"] = True
            device["finished"] = "done"
            self._write_inventory(device)
        device["pending"] = ""
        device["failures"] = 0
        device["last_error"] = ""

    def _mark_failure(self, device: dict, message: dict) -> None:
        device["failures"] += 1
        device["last_error"] = message["error"] or "work response failed"
        self._log_message(device, message, device["pending"] or "-", "fail", device["last_error"])
        gave_up_on_info = self._abandon_device_info(device)
        if gave_up_on_info:
            return
        if device["failures"] >= self.settings.max_retries:
            device["finished"] = "give_up"
            device["pending"] = ""

    def _abandon_device_info(self, device: dict) -> bool:
        if device["pending"] != "device-info":
            return False
        if device["failures"] < self.settings.max_retries:
            return False
        device["info_done"] = True
        device["pending"] = ""
        device["failures"] = 0
        return True

    def _offer_info(self, device: dict, message: dict) -> dict:
        device["pending"] = "device-info"
        self._log_message(device, message, "device-info", "offer", "all")
        return {"template": "device_info.xml", "context": self._envelope(message, device)}

    def _offer_config(self, device: dict, message: dict, config: Path) -> dict:
        device["pending"] = "config-upgrade"
        self._allow(message["source_ip"], "configs", config.name)
        context = self._envelope(message, device)
        context.update(self._config_fields(config))
        self._log_message(device, message, "config-upgrade", "offer", config.name)
        return {"template": "load_config.xml", "context": context}

    def _offer_image(self, device: dict, message: dict, image: Path) -> dict:
        device["pending"] = "image-install"
        self._allow(message["source_ip"], "images", image.name)
        context = self._envelope(message, device)
        context.update({
            "file_base": self.settings.public_base,
            "image_filename": quote(image.name),
            "checksum": self._checksum(image),
        })
        self._log_message(device, message, "image-install", "offer", image.name)
        return {"template": "image_install.xml", "context": context}

    def _config_fields(self, config: Path) -> dict:
        reload = self.settings.reload
        return {
            "file_base": self.settings.public_base,
            "config_filename": quote(config.name),
            "checksum": self._checksum(config),
            "apply_to": self.settings.apply_to,
            "reload": reload,
            "abort_on_syntax_fault": self.settings.abort_on_syntax_fault,
            "save_config": reload and self.settings.apply_to == "running",
        }

    def _backoff(self, message: dict, mode: str, reason: str, device: dict | None) -> dict:
        hours, minutes = delay_parts(self.settings.callback_minutes)
        context = self._envelope(message, device)
        context.update({
            "mode": mode,
            "reason": reason,
            "hours": hours,
            "minutes": minutes,
        })
        self._log_message(device, message, "backoff", mode, reason)
        return {"template": "backoff.xml", "context": context}

    def _bye(self, message: dict, device: dict | None) -> dict:
        return {"template": "bye.xml", "context": self._envelope(message, device)}

    def _envelope(self, message: dict, device: dict | None) -> dict:
        udi = message["udi"] if device is None else device["udi"]
        auth_required = message["auth_required"] if device is None else device["auth_required"]
        username, password = self._credentials(auth_required)
        return {
            "udi": udi,
            "correlator_id": message["correlator"],
            "usr": username,
            "pwd": password,
        }

    def _credentials(self, auth_required: bool) -> tuple[str, str]:
        if not auth_required:
            return "", ""
        if self.settings.username and self.settings.password:
            return self.settings.username, self.settings.password
        log.warning("agent set authRequired and no --username/--password is configured")
        return "", ""

    def _checksum(self, path: Path) -> str:
        if not self.settings.checksum:
            return ""
        return md5_file(path)

    def _allow(self, source_ip: str, kind: str, filename: str) -> None:
        self.allowed.setdefault(source_ip, set()).add((kind, filename))

    def _touch(self, device: dict, message: dict) -> None:
        device["auth_required"] = message["auth_required"]
        device["source_ip"] = message["source_ip"]
        device["correlator"] = message["correlator"]
        device["udi"] = message["udi"]

    def _device_for(self, message: dict) -> dict | None:
        parsed = parse_udi(message["udi"])
        if parsed is None:
            return None
        device = self.devices.get(parsed.serial)
        if device is None:
            device = new_device(message["udi"], parsed.pid, parsed.vid, parsed.serial)
            self.devices[parsed.serial] = device
            return device
        device["pid"] = parsed.pid
        device["vid"] = parsed.vid
        return device

    def _existing(self, message: dict) -> dict | None:
        parsed = parse_udi(message["udi"])
        if parsed is None:
            return None
        return self.devices.get(parsed.serial)

    def _write_inventory(self, device: dict) -> None:
        path = self.settings.inventory_csv
        if path is None or device["inventory_written"]:
            return
        try:
            write_inventory(path, device)
        except (OSError, csv.Error) as exc:
            self._log_message(device, {"source_ip": device.get("source_ip", "-"), "correlator": device.get("correlator", "-")}, "inventory", "fail", str(exc))
            return
        device["inventory_written"] = True

    def _load(self) -> None:
        path = self.settings.state_file
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("ignoring unreadable state file %s: %s", path, exc)
            return
        if not isinstance(data, dict):
            log.warning("ignoring state file %s: expected an object", path)
            return
        for serial, raw in data.items():
            if isinstance(raw, dict):
                self.devices[str(serial)] = _coerce(str(serial), raw)

    def _save(self) -> None:
        path = self.settings.state_file
        temporary = path.parent / f"{path.name}.tmp"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(self.devices, indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            log.warning("could not save state %s: %s", path, exc)

    def _log_message(self, device, message, service, result, detail) -> None:
        ip, correlator = "-", "-"
        if isinstance(message, dict):
            ip = message.get("source_ip") or "-"
            correlator = message.get("correlator") or "-"
        serial = "-" if not device else device.get("serial") or "-"
        log.info(
            "serial=%s ip=%s correlator=%s service=%s result=%s detail=%s",
            serial, ip, correlator, service, result, detail,
        )


def public_device(device: dict) -> dict:
    info = device.get("info") if isinstance(device.get("info"), dict) else {}
    return {
        "serial": device.get("serial", ""),
        "udi": device.get("udi", ""),
        "pid": device.get("pid", ""),
        "state": _state_name(device),
        "pending": device.get("pending", ""),
        "failures": device.get("failures", 0),
        "last_error": device.get("last_error", ""),
        "hostname": info.get("hostname", ""),
        "version": info.get("version", ""),
        "source_ip": device.get("source_ip", ""),
    }


def ensure_dirs(settings: Settings) -> None:
    for directory in (settings.configs_dir, settings.images_dir, settings.image_map_dir):
        directory.mkdir(parents=True, exist_ok=True)


def validate_settings(settings: Settings) -> None:
    if bool(settings.cert) != bool(settings.key):
        raise SystemExit("HTTPS needs both --cert and --key")
    _require_file(settings.cert, "certificate")
    _require_file(settings.key, "private key")
    _require_file(settings.trustpool, "trustpool")


def _image_pointer(directory: Path, serial: str, pid: str) -> Path | None:
    for key in (serial, pid):
        if not safe_component(key):
            continue
        path = directory / f"{key}.txt"
        if path.is_file():
            return path
    return None


def _pointer_target(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        if safe_component(item):
            return item
        return None
    return None


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    return {}


def _is_true(value) -> bool:
    return str(value).strip().lower() in {"true", "1"}


def _error_text(response: dict) -> str:
    info = _as_dict(response.get("errorInfo"))
    parts = [str(info.get("errorCode") or ""), str(info.get("errorMessage") or "")]
    service_log = response.get("serviceLog") or ""
    if isinstance(service_log, str) and service_log.strip():
        parts.append(service_log.strip()[:400])
    return " ".join(part for part in parts if part).strip()


def _profile(response: dict) -> dict:
    hardware = _as_dict(response.get("hardwareInfo"))
    image = _as_dict(response.get("imageInfo"))
    return {
        "hostname": hardware.get("hostname") or "",
        "platform": hardware.get("platformName") or "",
        "version": image.get("versionString") or "",
        "image_file": image.get("imageFile") or "",
    }


def _terminal_reason(device: dict) -> str:
    if device["finished"] == "give_up" and device["last_error"]:
        return str(device["last_error"])[:200]
    return "provisioning complete"


def _coerce(serial: str, raw: dict) -> dict:
    device = new_device(
        str(raw.get("udi") or ""),
        str(raw.get("pid") or ""),
        str(raw.get("vid") or ""),
        str(raw.get("serial") or serial),
    )
    for key, value in raw.items():
        if key in device:
            device[key] = value
    device["serial"] = serial
    if not isinstance(device["info"], dict):
        device["info"] = {}
    device["failures"] = _as_int(device["failures"])
    return device


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_inventory(path: Path) -> list:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _inventory_row(device: dict) -> dict:
    info = device.get("info") if isinstance(device.get("info"), dict) else {}
    hostname = info.get("hostname") or ""
    return {
        "host": hostname or device["serial"],
        "os": "ios",
        "serial": device["serial"],
        "hostname": hostname,
        "platform": info.get("platform") or device.get("pid") or "",
        "version": info.get("version") or "",
        "udi": device.get("udi") or "",
    }


def _replace_row(rows: list, row: dict) -> None:
    for index, existing in enumerate(rows):
        if existing.get("serial") == row["serial"]:
            rows[index] = row
            return
    rows.append(row)


def _require_file(path: Path | None, label: str) -> None:
    if path is not None and not path.is_file():
        raise SystemExit(f"{label} not found: {path}")


def _state_name(device: dict) -> str:
    if device.get("finished"):
        return device["finished"]
    if device.get("pending"):
        return "waiting-" + str(device["pending"])
    return "idle"
