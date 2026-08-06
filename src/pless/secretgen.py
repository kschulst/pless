"""Generating secrets, in two formats chosen by who has to type them.

A machine secret is never typed by a person. It is generated once, pasted into
a password manager, and read by software from then on, so it should be as long
as the receiving software tolerates.

A human secret is typed by a person, from a vault or from a sheet of paper,
sometimes at a console after a power cut. Length is worth trading for
transcribability: Crockford's base32 alphabet drops the four characters people
misread — I, L, O and U — and grouping in fives keeps the eye in place.

The LUKS passphrase and RESTIC_PASSWORD are human secrets, because they are the
two that cannot be recovered and therefore the two most likely to be read off
paper at the worst moment. Everything else is a machine secret.
"""

from __future__ import annotations

import secrets

# Crockford base32: the digits and the uppercase letters, minus I, L, O and U.
# Case-insensitive by design, so a phone keyboard cannot get it wrong.
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

MACHINE_TOKEN_BYTES = 32  # 256 bits, 43 characters of base64url
HUMAN_GROUPS = 5
HUMAN_GROUP_SIZE = 5  # 25 characters, 125 bits


def machine_token(nbytes: int = MACHINE_TOKEN_BYTES) -> str:
    """A secret no person will type: 32 random bytes as unpadded base64url."""
    if nbytes < 16:
        raise ValueError("A machine secret must be at least 16 bytes (128 bits).")
    return secrets.token_urlsafe(nbytes)


def human_passphrase(groups: int = HUMAN_GROUPS, group_size: int = HUMAN_GROUP_SIZE) -> str:
    """A secret a person will type: Crockford base32 in hyphenated groups.

    Five groups of five is 125 bits, which is far past anything that will be
    brute-forced, and short enough to copy from paper without losing your place.
    """
    if groups < 1 or group_size < 1:
        raise ValueError("groups and group_size must both be at least 1.")
    if groups * group_size * 5 < 64:
        raise ValueError(
            f"{groups}x{group_size} Crockford characters is only "
            f"{groups * group_size * 5} bits — too few to be worth generating."
        )
    chunks = [
        "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(group_size))
        for _ in range(groups)
    ]
    return "-".join(chunks)
