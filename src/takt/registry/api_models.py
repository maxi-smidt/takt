"""Pydantic models for the Registry HTTP boundary.

The Registry's persisted rows intentionally remain plain dictionaries for now.  These
models keep transport validation and OpenAPI generation at the edge without making
Pydantic part of the deployment, job, or storage state machines.
"""

from __future__ import annotations

import ipaddress
import math
import re
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from takt.fleet_actions import ALLOWED_ACTIONS
from takt.registry.deployment import validate_hostname, validate_registry_url

_TARGET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_DEVICE_ID_PATTERN = re.compile(r"^[0-9a-f-]{16,64}$")
_DEVICE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LoginRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    username: str = ""
    password: SecretStr = Field(min_length=1, max_length=4096)


class LabelRequest(ApiModel):
    label: Any = ""

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value: Any) -> str:
        return str(value or "")[:80]


class DeploymentCreateRequest(ApiModel):
    target: str
    port: StrictInt = 22
    ssh_user: str
    device_name: str
    hostname: str = ""
    confirm_hostname_change: StrictBool = False
    registry_url: str
    allow_insecure_http: StrictBool = False
    release_id: str

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        value = value.strip()
        if (
            not value
            or len(value) > 253
            or any(ord(character) < 33 for character in value)
            or any(character in "/\\@" for character in value)
        ):
            raise ValueError("Target must be a hostname or IP address.")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if not _TARGET_PATTERN.fullmatch(value):
                raise ValueError("Target must be a hostname or IP address.") from None
        return value

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("SSH port must be between 1 and 65535.")
        return value

    @field_validator("ssh_user")
    @classmethod
    def validate_ssh_user(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}", value):
            raise ValueError("SSH user is invalid.")
        return value

    @field_validator("device_name")
    @classmethod
    def validate_device_name(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9ÄÖÜäöüß._ -]{1,80}", value):
            raise ValueError("Device name is invalid.")
        return value

    @field_validator("hostname")
    @classmethod
    def validate_requested_hostname(cls, value: str) -> str:
        try:
            return validate_hostname(value, allow_empty=True)
        except ValueError as error:
            raise ValueError(str(error)) from error

    @model_validator(mode="after")
    def validate_hostname_confirmation(self) -> DeploymentCreateRequest:
        if bool(self.hostname) != self.confirm_hostname_change:
            raise ValueError(
                "An explicit hostname requires confirmation, and preservation cannot be confirmed."
            )
        try:
            self.registry_url = validate_registry_url(
                self.registry_url, self.allow_insecure_http
            )
        except ValueError as error:
            raise ValueError(str(error)) from error
        return self

    def store_values(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "port": self.port,
            "ssh_user": self.ssh_user,
            "device_name": self.device_name,
            "requested_hostname": self.hostname,
            "hostname_change_confirmed": self.confirm_hostname_change,
            "registry_url": self.registry_url,
            "allow_insecure_http": self.allow_insecure_http,
            "release_id": self.release_id,
        }


class DeploymentHostKeyRequest(ApiModel):
    fingerprint: str
    replace: StrictBool = False


class DeploymentCredentialsRequest(ApiModel):
    ssh_password: SecretStr = Field(default=SecretStr(""), max_length=1024)
    ssh_private_key: SecretStr = Field(default=SecretStr(""), max_length=64 * 1024)
    ssh_key_passphrase: SecretStr = Field(default=SecretStr(""), max_length=1024)
    sudo_password: SecretStr = Field(default=SecretStr(""), max_length=1024)

    def values(self) -> dict[str, str]:
        return {
            "ssh_password": self.ssh_password.get_secret_value(),
            "ssh_private_key": self.ssh_private_key.get_secret_value(),
            "ssh_key_passphrase": self.ssh_key_passphrase.get_secret_value(),
            "sudo_password": self.sudo_password.get_secret_value(),
        }


class EnrollmentRequest(ApiModel):
    enrollment_code: str
    device_id: str
    name: str
    hostname: str
    device_token: str | None = None

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, value: str) -> str:
        if not _DEVICE_ID_PATTERN.fullmatch(value):
            raise ValueError("Device ID is invalid.")
        return value

    @field_validator("device_token")
    @classmethod
    def validate_device_token(cls, value: str | None) -> str | None:
        if value is not None and not _DEVICE_TOKEN_PATTERN.fullmatch(value):
            raise ValueError("Device secret is invalid.")
        return value

    @field_validator("name")
    @classmethod
    def limit_name(cls, value: str) -> str:
        if not value:
            raise ValueError("Enrollment data is incomplete.")
        return value[:80]

    @field_validator("hostname")
    @classmethod
    def limit_hostname(cls, value: str) -> str:
        if not value:
            raise ValueError("Enrollment data is incomplete.")
        return value[:255]


def _normalize_heartbeat(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("A JSON object is required.")
    allowed = {
        "name",
        "hostname",
        "app_version",
        "agent_version",
        "health",
        "model",
        "os",
        "architecture",
        "uptime_seconds",
        "disk_free_bytes",
        "temperature_c",
        "protocol_version",
        "capabilities",
        "agent_session_id",
        "boot_id",
        "heartbeat_sequence",
        "poll_seconds",
        "registry_rtt_ms",
        "wifi_signal_dbm",
        "connection_recoveries",
        "registry_transport",
        "mirror_pending",
        "update_recovery",
    }
    payload = {key: item for key, item in value.items() if key in allowed}
    for key, limit in (
        ("name", 80),
        ("hostname", 255),
        ("app_version", 64),
        ("agent_version", 64),
        ("model", 255),
        ("os", 255),
        ("architecture", 32),
        ("agent_session_id", 64),
        ("boot_id", 64),
        ("registry_transport", 32),
    ):
        if key in payload and payload[key] is not None:
            payload[key] = str(payload[key])[:limit]

    health = payload.get("health")
    if health is not None and not isinstance(health, dict):
        payload["health"] = {"ok": False, "state": "invalid"}
    elif isinstance(health, dict):
        normalized_health: dict[str, Any] = {}
        if isinstance(health.get("ok"), bool):
            normalized_health["ok"] = health["ok"]
        if isinstance(health.get("ready"), bool):
            normalized_health["ready"] = health["ready"]
        for key, limit in (("state", 64), ("version", 64)):
            if health.get(key) is not None:
                if not isinstance(health[key], (str, int, float)) or isinstance(
                    health[key], bool
                ):
                    raise ValueError(f"Heartbeat health field {key} is invalid.")
                normalized_health[key] = str(health[key])[:limit]
        schema_version = health.get("database_schema_version")
        if schema_version is not None:
            if isinstance(schema_version, bool):
                raise ValueError("Heartbeat database schema is invalid.")
            try:
                normalized_health["database_schema_version"] = int(schema_version)
            except (TypeError, ValueError) as error:
                raise ValueError("Heartbeat database schema is invalid.") from error
        payload["health"] = normalized_health

    capabilities = payload.get("capabilities", [])
    payload["capabilities"] = (
        [str(item)[:64] for item in capabilities[:20]] if isinstance(capabilities, list) else []
    )
    recovery = payload.get("update_recovery")
    if recovery is not None:
        if not isinstance(recovery, dict):
            raise ValueError("Heartbeat update recovery is invalid.")
        payload["update_recovery"] = {
            "stuck": bool(recovery.get("stuck")),
            "error": str(recovery.get("error") or "")[:500],
            "phase": str(recovery.get("phase") or "unknown")[:64],
        }

    integer_ranges = {
        "uptime_seconds": (0, 10**10),
        "disk_free_bytes": (0, 10**16),
        "protocol_version": (0, 1000),
        "heartbeat_sequence": (0, 10**15),
        "connection_recoveries": (0, 10**12),
    }
    float_ranges = {
        "temperature_c": (-100.0, 250.0),
        "poll_seconds": (2.0, 3600.0),
        "registry_rtt_ms": (0.0, 3_600_000.0),
        "wifi_signal_dbm": (-200.0, 100.0),
    }
    for key, (minimum, maximum) in integer_ranges.items():
        if key in payload and payload[key] is not None:
            if isinstance(payload[key], bool):
                raise ValueError(f"Heartbeat field {key} is invalid.")
            try:
                numeric = int(payload[key])
            except (TypeError, ValueError) as error:
                raise ValueError(f"Heartbeat field {key} is invalid.") from error
            if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
                raise ValueError(f"Heartbeat field {key} is out of range.")
            payload[key] = numeric
    for key, (minimum, maximum) in float_ranges.items():
        if key in payload and payload[key] is not None:
            if isinstance(payload[key], bool):
                raise ValueError(f"Heartbeat field {key} is invalid.")
            try:
                numeric = float(payload[key])
            except (TypeError, ValueError) as error:
                raise ValueError(f"Heartbeat field {key} is invalid.") from error
            if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
                raise ValueError(f"Heartbeat field {key} is out of range.")
            payload[key] = numeric
    return payload


class HeartbeatRequest(ApiModel):
    name: str | None = None
    hostname: str | None = None
    app_version: str | None = None
    agent_version: str | None = None
    health: dict[str, Any] | None = None
    model: str | None = None
    os: str | None = None
    architecture: str | None = None
    uptime_seconds: int | None = None
    disk_free_bytes: int | None = None
    temperature_c: float | None = None
    protocol_version: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    agent_session_id: str | None = None
    boot_id: str | None = None
    heartbeat_sequence: int | None = None
    poll_seconds: float | None = None
    registry_rtt_ms: float | None = None
    wifi_signal_dbm: float | None = None
    connection_recoveries: int | None = None
    registry_transport: str | None = None
    update_recovery: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value: Any) -> dict[str, Any]:
        return _normalize_heartbeat(value)

    def payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class JobCreateRequest(ApiModel):
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    override: StrictBool = False

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        if value not in ALLOWED_ACTIONS:
            raise ValueError("Unsupported action.")
        return value


class JobOverrideRequest(ApiModel):
    override: StrictBool = False


class JobUpdateRequest(ApiModel):
    status: str
    progress: int = 0
    message: str = ""
    lease_id: str | None = None
    stage: str | None = None
    bytes_downloaded: int | None = None
    bytes_total: int | None = None
    result: dict[str, Any] | None = None


class WifiNetworkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssid: str
    password: SecretStr

    @field_validator("ssid")
    @classmethod
    def validate_ssid(cls, value: str) -> str:
        try:
            ssid_size = len(value.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ValueError("SSID must be valid UTF-8.") from error
        if not 1 <= ssid_size <= 32 or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("SSID must contain 1 to 32 UTF-8 bytes without controls.")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()
        raw_psk = re.fullmatch(r"[0-9A-Fa-f]{64}", password) is not None
        passphrase = 8 <= len(password) <= 63 and all(
            32 <= ord(character) <= 126 for character in password
        )
        if not raw_psk and not passphrase:
            raise ValueError(
                "Password must be 8 to 63 printable ASCII characters or 64 hexadecimal digits."
            )
        return value

    def values(self) -> tuple[str, str]:
        return self.ssid, self.password.get_secret_value()


class JsonResponse(ApiModel):
    """OpenAPI-friendly envelope for responses whose row fields are versioned data."""

    model_config = ConfigDict(extra="allow")


class HealthResponse(BaseModel):
    ok: bool
    service: str
    version: str
    schema_version: int
    database: str
    database_size: int
    disk_free_bytes: int
    last_backup_at: str | None = None
    bundled_release: dict[str, Any]
    protocol_version: int


class SessionStatusResponse(BaseModel):
    authenticated: bool
    csrf_token: str | None = None
    user: dict[str, Any] | None = None


class LoginResponse(BaseModel):
    ok: bool
    user: dict[str, Any] | None = None


class EnrollmentCodeResponse(BaseModel):
    code: str
    expires_in_minutes: int


class DeviceTokenResponse(BaseModel):
    device_token: str


class AgentHeartbeatResponse(BaseModel):
    job: dict[str, Any] | None = None
    protocol_version: int
    server_time: str


class AgentStatusResponse(BaseModel):
    protocol_version: int
    server_time: str
