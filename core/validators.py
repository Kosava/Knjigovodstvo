"""Validacija poslovnih podataka: PIB, tekući račun, e-pošta.

* PIB (poreski identifikacioni broj) ima 9 cifara; poslednja je kontrolna i
  računa se algoritmom ISO 7064 (MOD 11,10).
* Tekući račun se zapisuje kao ``BBB-RRRRRRRRRRRRR-KK`` (banka, račun na 13
  cifara, kontrolni broj). Ispravan je kad je ``int(BBB+R+KK) % 97 == 1``.
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_ACCOUNT_RE = re.compile(r"^\d{3}-\d{13}-\d{2}$")


# --------------------------------------------------------------------------- #
# PIB
# --------------------------------------------------------------------------- #
def pib_check_digit(first_eight: str) -> int:
    """Kontrolna cifra za prvih osam cifara PIB-a (ISO 7064, MOD 11,10)."""
    total = 10
    for ch in first_eight:
        total = (int(ch) + total) % 10
        if total == 0:
            total = 10
        total = (total * 2) % 11
    return (11 - total) % 10


def make_pib(first_eight: str) -> str:
    """Pravi ispravan PIB od prvih osam cifara (koristi se za demo podatke)."""
    return f"{first_eight}{pib_check_digit(first_eight)}"


def is_valid_pib(pib: str) -> bool:
    pib = (pib or "").strip()
    if not (len(pib) == 9 and pib.isdigit()):
        return False
    return pib_check_digit(pib[:8]) == int(pib[8])


# --------------------------------------------------------------------------- #
# Tekući račun
# --------------------------------------------------------------------------- #
def normalize_account(value: str) -> str:
    """Dovodi račun u oblik ``BBB-RRRRRRRRRRRRR-KK`` (dopunjava nulama)."""
    text = (value or "").strip().replace(" ", "")
    if not text:
        return ""
    parts = [p for p in re.split(r"[-–—]", text) if p]
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{parts[0]}-{parts[1].zfill(13)}-{parts[2]}"
    if len(parts) == 1 and parts[0].isdigit() and len(parts[0]) == 18:
        return f"{parts[0][:3]}-{parts[0][3:16]}-{parts[0][16:]}"
    return text


def make_account(bank: str, number: str) -> str:
    """Pravi ispravan račun sa izračunatim kontrolnim brojem (demo podaci)."""
    base = bank + number.zfill(13)
    control = 98 - (int(base) * 100) % 97
    return f"{bank}-{number.zfill(13)}-{control:02d}"


def is_valid_account(value: str) -> bool:
    account = normalize_account(value)
    if not _ACCOUNT_RE.match(account):
        return False
    return int(account.replace("-", "")) % 97 == 1


# --------------------------------------------------------------------------- #
# Ostalo
# --------------------------------------------------------------------------- #
def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match((value or "").strip()))
