"""The two secret formats, and the properties that make each one worth having."""

from __future__ import annotations

import pytest

from pless import secretgen


class TestMachineToken:
    def test_is_long_enough_to_be_pointless_to_attack(self) -> None:
        # 32 bytes of base64url without padding is 43 characters.
        assert len(secretgen.machine_token()) == 43

    def test_uses_only_url_safe_characters(self) -> None:
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert set(secretgen.machine_token()) <= allowed

    def test_has_no_padding_that_would_confuse_an_env_file(self) -> None:
        assert "=" not in secretgen.machine_token()

    def test_every_call_differs(self) -> None:
        assert len({secretgen.machine_token() for _ in range(100)}) == 100

    def test_refuses_to_generate_something_weak(self) -> None:
        with pytest.raises(ValueError, match="at least 16 bytes"):
            secretgen.machine_token(8)


class TestHumanPassphrase:
    def test_is_grouped_for_reading_off_paper(self) -> None:
        passphrase = secretgen.human_passphrase()
        groups = passphrase.split("-")
        assert len(groups) == 5
        assert all(len(group) == 5 for group in groups)

    def test_omits_the_characters_people_misread(self) -> None:
        # I and L look like 1, O looks like 0, U looks like V. Someone will
        # copy one of these from a sheet of paper on the worst day of the year.
        body = secretgen.human_passphrase(groups=40).replace("-", "")
        assert not set(body) & set("ILOU")

    def test_uses_only_the_crockford_alphabet(self) -> None:
        body = secretgen.human_passphrase(groups=40).replace("-", "")
        assert set(body) <= set(secretgen.CROCKFORD_ALPHABET)

    def test_default_carries_125_bits(self) -> None:
        body = secretgen.human_passphrase().replace("-", "")
        assert len(body) * 5 == 125

    def test_every_call_differs(self) -> None:
        assert len({secretgen.human_passphrase() for _ in range(100)}) == 100

    def test_shape_is_configurable(self) -> None:
        passphrase = secretgen.human_passphrase(groups=6, group_size=4)
        assert len(passphrase.split("-")) == 6
        assert all(len(group) == 4 for group in passphrase.split("-"))

    def test_refuses_a_shape_too_short_to_be_worth_generating(self) -> None:
        with pytest.raises(ValueError, match="too few to be worth generating"):
            secretgen.human_passphrase(groups=2, group_size=5)

    def test_refuses_nonsense_shapes(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            secretgen.human_passphrase(groups=0)
