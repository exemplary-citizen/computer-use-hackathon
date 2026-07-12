"""Minimal public-SDK proof that an H agent can control this Mac desktop."""

from __future__ import annotations

import importlib.util
import logging
import os
import platform
import sys
from dataclasses import dataclass
from typing import Literal

import tyro
from hai_agents import Client, HaiAgentsEnvironment
from hai_agents.polling import SessionHandle
from hai_agents.types.agent import Agent
from hai_agents.types.environment import Environment_Desktop
from hai_agents.types.session_changes_answer import SessionChangesAnswer

DEFAULT_TASK = (
    "Open TextEdit and create a new untitled document. Type exactly: "
    "Automation Foundry desktop control works. Do not save the document and do not interact with any other app. "
    "Then report completion."
)
SUCCESS_STATUSES = ("completed", "idle")
Region = Literal["auto", "us", "eu"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreflightCheck:
    """One credential-safe environment check."""

    status: Literal["PASS", "FAIL", "SKIP"]
    name: str
    detail: str


def doctor(require_api_key: bool = False) -> None:
    """Check local-control prerequisites without moving the mouse or reading the screen.

    Args:
        require_api_key: Fail when `HAI_API_KEY` is not exported. Leave false for a credential-free dependency check.
    """
    checks = _preflight_checks(require_api_key=require_api_key)
    if require_api_key and not any(check.status == "FAIL" for check in checks):
        checks += (_authentication_check(),)
    for check in checks:
        print(f"{check.status:4} {check.name}: {check.detail}")
    failures = [check for check in checks if check.status == "FAIL"]
    if failures:
        raise SystemExit(1)
    print("desktop-smoke doctor PASSED")


def run(
    task: str = DEFAULT_TASK,
    confirm_control: bool = False,
    max_steps: int = 20,
    max_time_seconds: int = 180,
    region: Region = "auto",
) -> None:
    """Run one bounded, non-persistent task through H's public local-desktop API.

    Args:
        task: Plain-language desktop task. The default only creates an unsaved TextEdit document.
        confirm_control: Explicit acknowledgement that the agent will control the real mouse, keyboard, and screen.
        max_steps: Maximum agent steps allowed for the session.
        max_time_seconds: Maximum session duration in seconds.
        region: H agent-platform region. Auto tries the US endpoint, then EU only after an authentication rejection.
    """
    if not confirm_control:
        logger.error("Refusing to control the desktop without --confirm-control.")
        raise SystemExit(2)
    if max_steps < 1 or max_time_seconds < 1:
        logger.error("max_steps and max_time_seconds must both be positive.")
        raise SystemExit(2)

    failures = [check for check in _preflight_checks(require_api_key=True) if check.status == "FAIL"]
    if failures:
        for check in failures:
            logger.error("%s: %s", check.name, check.detail)
        raise SystemExit(1)

    agent = Agent(
        name="automation-foundry-desktop-smoke",
        description="Runs one bounded, non-persistent task on the local demo Mac.",
        environments=[Environment_Desktop(id="demo-mac", host="user_device")],
        instructions=(
            "Control only the application named in the task. Do not open Terminal, browsers, messaging apps, "
            "password managers, or system settings. Never save, submit, send, purchase, delete, or install anything."
        ),
    )
    session, selected_environment = _start_session(
        agent,
        task=task,
        max_steps=max_steps,
        max_time_seconds=max_time_seconds,
        region=region,
    )
    print(f"region: {selected_environment.name.lower()}")
    print(f"session: {session.id}")
    try:
        result = session.wait_for_completion(timeout_seconds=float(max_time_seconds + 30))
    except KeyboardInterrupt:
        logger.warning("Cancelling H session after keyboard interrupt.")
        session.cancel()
        raise SystemExit(130) from None

    print(f"status: {result.status}")
    print(f"outcome: {result.outcome}")
    if result.answer is not None:
        print(f"answer: {result.answer}")
    if result.status not in SUCCESS_STATUSES or result.outcome != "success":
        if result.error:
            logger.error("H session failed: %s", result.error)
        elif result.outcome != "success":
            logger.error("H session did not complete the requested task (outcome=%s).", result.outcome)
        raise SystemExit(1)
    print("desktop-smoke PASSED")


def _start_session(
    agent: Agent,
    *,
    task: str,
    max_steps: int,
    max_time_seconds: int,
    region: Region,
) -> tuple[SessionHandle[SessionChangesAnswer], HaiAgentsEnvironment]:
    environments = _region_environments(region)
    for index, environment in enumerate(environments):
        logger.info("Trying H %s agent endpoint.", environment.name)
        client = Client(environment=environment)
        try:
            session = client.start_session(
                agent=agent,
                messages=task,
                max_steps=max_steps,
                max_time_s=float(max_time_seconds),
            )
        except Exception as error:
            if not _is_auth_failure(error):
                raise
            if index + 1 < len(environments):
                logger.warning("H %s rejected the key; trying %s.", environment.name, environments[index + 1].name)
                continue
            tried = " and ".join(item.name for item in environments)
            logger.error(
                "HAI_API_KEY was rejected by the H %s agent endpoint%s. The key may be invalid, region-bound, or "
                "missing Computer-Use Agents access.",
                tried,
                "s" if len(environments) > 1 else "",
            )
            raise SystemExit(1) from None
        return session, environment
    raise RuntimeError("region selection produced no H endpoints")


def _region_environments(region: Region) -> tuple[HaiAgentsEnvironment, ...]:
    if region == "us":
        return (HaiAgentsEnvironment.US,)
    if region == "eu":
        return (HaiAgentsEnvironment.EU,)
    return (HaiAgentsEnvironment.US, HaiAgentsEnvironment.EU)


def _is_auth_failure(error: BaseException) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ == "AuthError" or getattr(current, "status_code", None) in (401, 403):
            return True
        message = str(current).casefold()
        if "auth error" in message or "401 unauthorized" in message or "403 forbidden" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def _authentication_check() -> PreflightCheck:
    for environment in _region_environments("auto"):
        client = Client(environment=environment)
        try:
            client.quota.get_token_quota()
        except Exception as error:
            if _is_auth_failure(error):
                continue
            return PreflightCheck("FAIL", "H agent authentication", f"{environment.name} connectivity failed")
        return PreflightCheck("PASS", "H agent authentication", f"{environment.name} endpoint accepted the key")
    return PreflightCheck("FAIL", "H agent authentication", "US and EU endpoints rejected the key")


def _preflight_checks(*, require_api_key: bool) -> tuple[PreflightCheck, ...]:
    python_ok = sys.version_info >= (3, 12)
    macos_ok = platform.system() == "Darwin"
    sdk_ok = importlib.util.find_spec("hai_agents") is not None
    driver_ok = importlib.util.find_spec("hai_drivers.desktop.local") is not None
    api_key_present = bool(os.environ.get("HAI_API_KEY"))
    key_status: Literal["PASS", "FAIL", "SKIP"]
    if api_key_present:
        key_status = "PASS"
    elif require_api_key:
        key_status = "FAIL"
    else:
        key_status = "SKIP"
    return (
        PreflightCheck("PASS" if python_ok else "FAIL", "Python", platform.python_version()),
        PreflightCheck("PASS" if macos_ok else "FAIL", "macOS", platform.platform()),
        PreflightCheck("PASS" if sdk_ok else "FAIL", "hai-agents", "public SDK import available"),
        PreflightCheck("PASS" if driver_ok else "FAIL", "desktop driver", "local driver import available"),
        PreflightCheck(key_status, "HAI_API_KEY", "present" if api_key_present else "not exported; value not read"),
    )


def main() -> None:
    """Run the doctor or live smoke subcommand."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    tyro.extras.subcommand_cli_from_dict({"doctor": doctor, "run": run})


if __name__ == "__main__":
    main()
