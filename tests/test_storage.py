from pless.storage import detect_cipher_args

PI4_CPUINFO = """\
processor\t: 0
BogoMIPS\t: 108.00
Features\t: fp asimd evtstrm crc32 cpuid
CPU implementer\t: 0x41
CPU part\t: 0xd08
"""

PI5_CPUINFO = """\
processor\t: 0
BogoMIPS\t: 108.00
Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid
CPU implementer\t: 0x41
CPU part\t: 0xd0b
"""

X86_CPUINFO = """\
processor\t: 0
vendor_id\t: GenuineIntel
flags\t\t: fpu vme de pse tsc msr pae aes xsave avx
"""


def test_pi4_without_aes_gets_adiantum() -> None:
    args = detect_cipher_args(PI4_CPUINFO)
    assert "xchacha20,aes-adiantum-plain64" in args


def test_pi5_with_aes_gets_aes_xts() -> None:
    args = detect_cipher_args(PI5_CPUINFO)
    assert "aes-xts-plain64" in args


def test_x86_flags_line_gets_aes_xts() -> None:
    args = detect_cipher_args(X86_CPUINFO)
    assert "aes-xts-plain64" in args


def test_adiantum_never_matches_substring_of_other_words() -> None:
    # "aes" skal matches som eget token, ikke som del av f.eks. "aesthetics"
    cpuinfo = "Features\t: fp asimd aesthetics crc32\n"
    args = detect_cipher_args(cpuinfo)
    assert "xchacha20,aes-adiantum-plain64" in args
