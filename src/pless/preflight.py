"""Readiness gate: is this installation fit to be trusted with documents?

The dangerous moment in setting up an archive is the one just before you put
real documents into it. Everything looks finished, nothing has been proven, and
the cost of being wrong rises the instant the first file lands.

Preflight answers one question — *can I get my documents back?* — by exercising
the machinery rather than inspecting it. Most notably it can run a lock/unlock
drill: the only way to prove you know the passphrase is to use it, and doing
that on an empty volume is free.

Pure analysis lives here; the checks that need a machine take a runner callable
so they can be tested without one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


class Readiness(StrEnum):
    READY = "ready"
    NOT_READY = "not ready"


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    blocking: bool = True


@dataclass
class PreflightReport:
    checks: list[Check] = field(default_factory=list)
    drill_performed: bool = False

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.failures if c.blocking]

    @property
    def readiness(self) -> Readiness:
        return Readiness.READY if not self.blockers else Readiness.NOT_READY

    @property
    def verdict(self) -> str:
        if self.blockers:
            return (
                f"NOT READY for documents — {len(self.blockers)} blocking issue(s). "
                "Fix them before importing anything you cannot afford to lose."
            )
        if not self.drill_performed:
            return (
                "Checks passed, but the passphrase was never exercised. "
                "Run `pless preflight --drill` to prove you can unlock the volume."
            )
        return "Ready. The volume locks and unlocks, the stack recovers, and nothing is exposed."


# Capabilities that must exist before an archive can be the only copy of
# anything. Absent features are listed explicitly rather than passed over,
# because silence here reads as approval.
def check_recoverability(backup_configured: bool, backup_verified: bool) -> list[Check]:
    return [
        Check(
            name="off-site backup configured",
            passed=backup_configured,
            detail=(
                "Configured."
                if backup_configured
                else "Not configured. Backup is not implemented yet, so this machine holds "
                "the only copy of whatever you import. Keep your originals."
            ),
        ),
        Check(
            name="restore verified",
            passed=backup_verified,
            detail=(
                "A restore has been performed and inspected."
                if backup_verified
                else "Never performed. A backup that has not been restored is a belief."
            ),
        ),
    ]


def analyse(
    target_reachable: bool,
    storage_ready: bool,
    paperless_healthy: bool,
    audit_clean: bool,
    drill_passed: bool | None,
    backup_configured: bool = False,
    backup_verified: bool = False,
) -> PreflightReport:
    """Turn observations into a verdict. `drill_passed` is None if not attempted."""
    checks = [
        Check(
            name="target reachable",
            passed=target_reachable,
            detail="Responds over SSH." if target_reachable else "No response over SSH.",
        ),
        Check(
            name="encrypted storage",
            passed=storage_ready,
            detail=(
                "LUKS volume formatted and mounted."
                if storage_ready
                else "The volume is not formatted or not mounted."
            ),
        ),
        Check(
            name="Paperless responding",
            passed=paperless_healthy,
            detail=(
                "Answering on the target's localhost."
                if paperless_healthy
                else "Not answering. See `pless deploy logs`."
            ),
        ),
        Check(
            name="exposure audit",
            passed=audit_clean,
            detail=(
                "Nothing exposed to the local network."
                if audit_clean
                else "Findings present. Run `pless audit` for details."
            ),
        ),
    ]

    if drill_passed is not None:
        checks.append(
            Check(
                name="lock/unlock drill",
                passed=drill_passed,
                detail=(
                    "The volume locked and unlocked with the passphrase you supplied, "
                    "and the stack came back."
                    if drill_passed
                    else "The drill failed. Either the passphrase is wrong or the volume "
                    "did not come back — investigate before importing anything."
                ),
            )
        )

    checks.extend(check_recoverability(backup_configured, backup_verified))
    return PreflightReport(checks=checks, drill_performed=drill_passed is True)


def run_drill(
    lock: Callable[[], bool],
    unlock: Callable[[], bool],
    health: Callable[[], bool],
) -> bool:
    """Lock the volume, unlock it again, and confirm the stack recovers.

    Proves three things at once: that the passphrase is the one that works,
    that the volume survives a lock cycle, and that systemd brings the stack
    back afterwards. Run it while the volume is empty — it costs nothing then.
    """
    if not lock():
        return False
    if not unlock():
        return False
    return health()
