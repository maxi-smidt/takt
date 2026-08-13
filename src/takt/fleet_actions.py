from __future__ import annotations

from dataclasses import dataclass, field

LEASED_JOBS_CAPABILITY = "leased-jobs"
WIFI_PROFILE_CAPABILITY = "wifi-profile-v1"
SERVICE_CONTROL_CAPABILITY = "service-control-v1"
POWER_CONTROL_CAPABILITY = "power-control-v1"
DIAGNOSTICS_CAPABILITY = "diagnostics-v1"
HEALTH_CHECKS_CAPABILITY = "health-checks-v1"

INSTALL_STAGES = (
    "queued",
    "waiting_for_safe_state",
    "downloading",
    "verifying",
    "staging",
    "activating",
    "restarting",
    "health_checking",
    "succeeded",
    "rolled_back",
    "retryable_failure",
    "intervention_required",
    "cancelled",
)

# Stages every action can reach through the shared job lifecycle: deferral while
# the timer is busy, a transient-failure requeue, and the terminal states.
_COMMON_STAGES = (
    "queued",
    "waiting_for_safe_state",
    "retryable_failure",
    "intervention_required",
    "succeeded",
    "failed",
    "cancelled",
)
_SERVICE_STAGES = (*_COMMON_STAGES, "applying", "verifying")
_POWER_STAGES = (*_COMMON_STAGES, "applying", "rebooting", "powering_off")
_DIAGNOSTICS_STAGES = (*_COMMON_STAGES, "collecting", "redacting", "uploading")
_HEALTH_CHECK_STAGES = (*_COMMON_STAGES, "checking")


@dataclass(frozen=True, slots=True)
class FleetAction:
    """Declares one Fleet Manager job action.

    Registry (job creation + gating), agent (dispatch + capability probing) and
    the frontend (button gating/labels) all read this table so they cannot drift
    apart. Actions with a bespoke request shape or an encrypted payload (Wi-Fi
    profiles today) are not listed here and keep their own creation endpoint.
    """

    name: str
    capability: str
    disruptive: bool = False
    requires_ready: bool = False
    overridable: bool = False
    stages: tuple[str, ...] = field(default_factory=tuple)
    timeout_seconds: int = 120
    # Minimum takt.protocol.PROTOCOL_VERSION the device must have last reported.
    # Not imported from takt.protocol to keep this module dependency-free; kept
    # in sync by hand when an action needs a newer base protocol than 1.
    min_protocol: int = 1


FLEET_ACTIONS: dict[str, FleetAction] = {
    "install_release": FleetAction(
        name="install_release",
        capability=LEASED_JOBS_CAPABILITY,
        disruptive=True,
        requires_ready=True,
        stages=INSTALL_STAGES,
        timeout_seconds=900,
    ),
    "mirror_now": FleetAction(
        name="mirror_now",
        capability=LEASED_JOBS_CAPABILITY,
        timeout_seconds=120,
    ),
    "restart_takt": FleetAction(
        name="restart_takt",
        capability=LEASED_JOBS_CAPABILITY,
        disruptive=True,
        requires_ready=True,
        overridable=True,
        stages=_SERVICE_STAGES,
        timeout_seconds=120,
    ),
    "start_takt": FleetAction(
        name="start_takt",
        capability=SERVICE_CONTROL_CAPABILITY,
        disruptive=True,
        stages=_SERVICE_STAGES,
        timeout_seconds=60,
    ),
    "stop_takt": FleetAction(
        name="stop_takt",
        capability=SERVICE_CONTROL_CAPABILITY,
        disruptive=True,
        requires_ready=True,
        overridable=True,
        stages=_SERVICE_STAGES,
        timeout_seconds=60,
    ),
    "reboot_device": FleetAction(
        name="reboot_device",
        capability=POWER_CONTROL_CAPABILITY,
        disruptive=True,
        requires_ready=True,
        overridable=True,
        stages=_POWER_STAGES,
        timeout_seconds=90,
    ),
    "shutdown_device": FleetAction(
        name="shutdown_device",
        capability=POWER_CONTROL_CAPABILITY,
        disruptive=True,
        requires_ready=True,
        overridable=True,
        stages=_POWER_STAGES,
        timeout_seconds=90,
    ),
    "collect_diagnostics": FleetAction(
        name="collect_diagnostics",
        capability=DIAGNOSTICS_CAPABILITY,
        stages=_DIAGNOSTICS_STAGES,
        timeout_seconds=120,
    ),
    "run_health_checks": FleetAction(
        name="run_health_checks",
        capability=HEALTH_CHECKS_CAPABILITY,
        stages=_HEALTH_CHECK_STAGES,
        timeout_seconds=60,
    ),
}

ALLOWED_ACTIONS = frozenset(FLEET_ACTIONS)
DISRUPTIVE_ACTIONS = frozenset(
    name for name, action in FLEET_ACTIONS.items() if action.disruptive
)
OVERRIDABLE_ACTIONS = frozenset(
    name for name, action in FLEET_ACTIONS.items() if action.overridable
)
REQUIRES_READY_ACTIONS = frozenset(
    name for name, action in FLEET_ACTIONS.items() if action.requires_ready
)
# Power actions kill the agent process before it can renew its lease. Requeuing
# them on lease expiry (the default behavior for every other action) would make
# the device reboot/power off again once it reconnects — so these are instead
# marked failed on expiry and must be re-issued deliberately.
NO_REQUEUE_ON_LEASE_EXPIRY = frozenset({"reboot_device", "shutdown_device"})
# Actions supported by every agent that has ever reported a heartbeat, even one
# that predates capability reporting entirely.
BASELINE_ACTIONS = frozenset(
    name for name, action in FLEET_ACTIONS.items() if action.capability == LEASED_JOBS_CAPABILITY
)


def get_action(name: str) -> FleetAction | None:
    return FLEET_ACTIONS.get(name)


def capability_for(name: str) -> str | None:
    action = FLEET_ACTIONS.get(name)
    return action.capability if action else None
