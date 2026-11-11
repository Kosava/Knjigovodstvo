import pytest

from core import validators as v


@pytest.mark.parametrize("prefix", ["10234567", "10000001", "99999999", "00000000", "12345678"])
def test_make_pib_is_valid(prefix):
    pib = v.make_pib(prefix)
    assert v.is_valid_pib(pib)
    # MOD 11,10: za svakih 8 cifara tačno jedna kontrolna cifra je ispravna
    assert sum(v.is_valid_pib(prefix + str(d)) for d in range(10)) == 1


@pytest.mark.parametrize("pib", ["", "12345678", "1234567890", "10234567a", " "])
def test_invalid_pib_format(pib):
    assert not v.is_valid_pib(pib)


def test_pib_with_wrong_check_digit():
    good = v.make_pib("10234567")
    bad = good[:8] + str((int(good[8]) + 1) % 10)
    assert not v.is_valid_pib(bad)


def test_account_roundtrip():
    acc = v.make_account("160", "5100000012345")
    assert v.is_valid_account(acc)
    assert v.normalize_account(acc.replace("-", "")) == acc          # 18 cifara bez crtica
    assert v.normalize_account(" " + acc + " ") == acc


def test_account_short_middle_is_padded():
    acc = v.make_account("265", "1234")
    bank, middle, control = acc.split("-")
    assert middle == "0000000001234"
    assert v.normalize_account(f"{bank}-1234-{control}") == acc
    assert v.is_valid_account(f"{bank}-1234-{control}")


def test_account_wrong_control():
    acc = v.make_account("160", "5100000012345")
    control = int(acc[-2:])
    bad = acc[:-2] + f"{(control + 1) % 100:02d}"
    assert not v.is_valid_account(bad)
    assert not v.is_valid_account("160-123")


@pytest.mark.parametrize("email, ok", [
    ("office@firma.rs", True), ("a.b@c.co", True), ("nema-at.rs", False),
    ("x@y", False), ("x @y.rs", False), ("", False),
])
def test_email(email, ok):
    assert v.is_valid_email(email) is ok
