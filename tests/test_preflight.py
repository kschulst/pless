"""Preflight answers one question: can I get my documents back?

The dangerous moment is just before the first real document lands, so a green
verdict must mean something. These tests pin down what it takes to earn one.
"""

from pless.preflight import Readiness, analyse, run_drill


def healthy(**overrides):
    args = {
        "target_reachable": True,
        "storage_ready": True,
        "paperless_healthy": True,
        "audit_clean": True,
        "drill_passed": True,
        "backup_configured": True,
        "backup_verified": True,
    }
    args.update(overrides)
    return analyse(**args)


class TestVerdict:
    def test_everything_working_is_ready(self) -> None:
        assert healthy().readiness is Readiness.READY

    def test_unreachable_target_blocks(self) -> None:
        assert healthy(target_reachable=False).readiness is Readiness.NOT_READY

    def test_locked_storage_blocks(self) -> None:
        assert healthy(storage_ready=False).readiness is Readiness.NOT_READY

    def test_audit_findings_block(self) -> None:
        report = healthy(audit_clean=False)
        assert report.readiness is Readiness.NOT_READY
        assert "pless audit" in next(c.detail for c in report.failures)

    def test_missing_backup_blocks(self) -> None:
        # Silence here would read as approval, so an unbuilt feature must fail loudly.
        report = healthy(backup_configured=False, backup_verified=False)
        assert report.readiness is Readiness.NOT_READY
        assert any("only copy" in c.detail for c in report.blockers)

    def test_unverified_restore_blocks_even_when_backup_is_configured(self) -> None:
        report = healthy(backup_verified=False)
        assert report.readiness is Readiness.NOT_READY
        assert any("belief" in c.detail for c in report.blockers)


class TestDrill:
    def test_skipped_drill_is_not_a_failure_but_is_called_out(self) -> None:
        report = healthy(drill_passed=None)
        # Nothing blocks, but the verdict refuses to claim the passphrase works.
        assert report.readiness is Readiness.READY
        assert not report.drill_performed
        assert "never exercised" in report.verdict

    def test_failed_drill_blocks(self) -> None:
        report = healthy(drill_passed=False)
        assert report.readiness is Readiness.NOT_READY

    def test_passed_drill_is_reflected_in_the_verdict(self) -> None:
        report = healthy()
        assert report.drill_performed
        assert "locks and unlocks" in report.verdict


class TestRunDrill:
    def test_full_cycle_passes(self) -> None:
        assert run_drill(lock=lambda: True, unlock=lambda: True, health=lambda: True)

    def test_stops_when_lock_fails(self) -> None:
        called = []

        def unlock() -> bool:
            called.append("unlock")
            return True

        # Never attempt an unlock we have no reason to trust the state of.
        assert not run_drill(lock=lambda: False, unlock=unlock, health=lambda: True)
        assert not called

    def test_failing_unlock_fails_the_drill(self) -> None:
        assert not run_drill(lock=lambda: True, unlock=lambda: False, health=lambda: True)

    def test_volume_returning_without_the_stack_fails(self) -> None:
        # Unlocking is not enough; the stack has to come back too.
        assert not run_drill(lock=lambda: True, unlock=lambda: True, health=lambda: False)
