"""NBS IPS QR kod za plaćanje računa (oznaka "PR"), po preporukama Narodne banke Srbije.

Primer sadržaja::

    K:PR|V:01|C:1|R:160000000012345690|N:Moja firma d.o.o.\\r\\nNovi Sad|I:RSD1234,5|
    P:Kupac d.o.o.|SF:221|S:Faktura 2026/0001|RO:002026-0001

Pravila (ips.nbs.rs, "Preporuke za validaciju"):

* polja se odvajaju znakom ``|``; prazno opciono polje se izostavlja; na kraju nema ``|``;
* R: račun primaoca kao 18 cifara bez crtica;
* N: naziv (i mesto) primaoca, najviše 70 znakova i 3 reda;
* I: ``RSD`` + iznos sa decimalnim zarezom, bez suvišnih nula na kraju (``RSD1000,`` / ``RSD10,5``);
* SF: šifra plaćanja, tri cifre, prva je 1 ili 2 (npr. 221 za promet robe i usluga);
* S: svrha plaćanja, najviše 35 znakova u jednom redu;
* RO: model (``00`` ili ``97``) + poziv na broj, najviše 25 znakova.

Sam QR kod (PNG kao data-URI za HTML) pravi biblioteka ``segno``; ako nije instalirana,
faktura se štampa bez QR koda.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Mapping

from . import tax, validators
from .models import ENTITY_PERSON, INVOICE_OUT, Invoice, Partner

log = logging.getLogger(__name__)

PAYMENT_CODES = ("221", "289", "220", "290", "121")
DEFAULT_PAYMENT_CODE = "221"   # promet robe i usluga – međufazna potrošnja (pravna lica)
MODEL_00, MODEL_97 = "00", "97"
_LINE = "\r\n"                 # NBS primeri koriste CR LF između redova


def _clean(text: str, single_line: bool = True) -> str:
    """Uklanja znak ``|`` (razdvaja polja) i suvišne razmake."""
    text = (text or "").replace("|", "/")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    return " ".join(lines) if single_line else _LINE.join(lines)


def _lines(parts: list[str], limit: int = 70) -> str:
    """Do tri reda, ukupno najviše ``limit`` znakova (CR LF se ne broji kao znak sadržaja)."""
    lines = [_clean(p) for p in parts if _clean(p)][:3]
    out: list[str] = []
    used = 0
    for line in lines:
        room = limit - used
        if room <= 0:
            break
        out.append(line[:room])
        used += len(out[-1])
    return _LINE.join(out)


def account_digits(account: str) -> str:
    """``160-5100000012345-09`` -> ``160510000001234509`` (18 cifara) ili ``""`` ako nije ispravan."""
    normalized = validators.normalize_account(account or "")
    if not normalized or not validators.is_valid_account(normalized):
        return ""
    digits = normalized.replace("-", "")
    return digits if len(digits) == 18 else ""


def format_amount(amount: Decimal) -> str:
    """``Decimal('1234.50')`` -> ``RSD1234,5``; ``1000`` -> ``RSD1000,``."""
    text = f"{tax.round_money(amount):.2f}".rstrip("0")
    return "RSD" + text.replace(".", ",")


def model97(reference_digits: str) -> str:
    """Poziv na broj po modelu 97: kontrolni broj (ISO 7064, MOD 97-10) + broj."""
    digits = re.sub(r"\D", "", reference_digits)
    if not digits:
        return ""
    control = 98 - int(digits + "00") % 97
    return f"{control:02d}{digits}"


def reference(number: str, model: str = MODEL_00) -> str:
    """Poziv na broj iz broja dokumenta: ``2026/0001`` -> ``002026-0001`` ili ``97…20260001``."""
    groups = re.findall(r"\d+", number or "")
    if not groups:
        return ""
    if model == MODEL_97:
        ref = model97("".join(groups))
        return f"97{ref}"[:25] if ref else ""
    return ("00" + "-".join(groups))[:25]


def purpose(invoice: Invoice) -> str:
    # Svrha plaćanja ostaje na srpskom bez obzira na jezik programa (čita je banka primaoca).
    label = "Predračun" if invoice.is_proforma else "Faktura"
    return _clean(f"{label} {invoice.number}")[:35]


def payload(*, account: str, payee_name: str, payee_city: str, amount: Decimal,
            payment_code: str = DEFAULT_PAYMENT_CODE, payer: list[str] | None = None,
            purpose_text: str = "", ref: str = "") -> str:
    """Tekst IPS QR koda. Baca ``ValueError`` ako obavezni podaci nisu ispravni."""
    r = account_digits(account)
    if not r:
        raise ValueError("tekući račun primaoca nije ispravan")
    name = _lines([payee_name, payee_city])
    if not name:
        raise ValueError("naziv primaoca je obavezan")
    if not (Decimal("0.01") <= tax.round_money(amount) <= Decimal("999999999999.99")):
        raise ValueError("iznos je van dozvoljenog opsega")
    if not re.fullmatch(r"[12]\d\d", payment_code or ""):
        raise ValueError("šifra plaćanja mora imati tri cifre i počinjati sa 1 ili 2")
    fields = [("K", "PR"), ("V", "01"), ("C", "1"), ("R", r), ("N", name), ("I", format_amount(amount))]
    payer_text = _lines(payer or [])
    if payer_text:
        fields.append(("P", payer_text))
    fields.append(("SF", payment_code))
    if purpose_text:
        fields.append(("S", _clean(purpose_text)[:35]))
    if ref:
        fields.append(("RO", ref[:25]))
    return "|".join(f"{tag}:{value}" for tag, value in fields)


def invoice_payload(invoice: Invoice, partner: Partner | None, company: Mapping[str, object]) -> str | None:
    """IPS QR tekst za izlazni dokument koji tek treba platiti, ili ``None`` kad QR nema smisla.

    QR se ne štampa za ulazne fakture, plaćene ili stornirane dokumente, avansni račun
    (izdaje se na već primljen novac), za valutu različitu od RSD i kad iznos za uplatu nije > 0.
    """
    if not company.get("ips_qr", True):
        return None
    if invoice.invoice_type != INVOICE_OUT or invoice.is_paid or invoice.is_cancelled \
            or invoice.is_storno or invoice.is_advance:
        return None
    if str(company.get("currency", "RSD")).upper() != "RSD":
        return None
    due = invoice.amount_due
    if due <= 0:
        return None
    payer = [partner.name, partner.address, partner.city] if partner else []
    model = str(company.get("payment_model", MODEL_00))
    # Građanin plaća bezgotovinski šifrom 289; pravna lica šifrom iz podešavanja (podrazumevano 221).
    code = "289" if partner is not None and partner.entity_type == ENTITY_PERSON \
        else str(company.get("payment_code", DEFAULT_PAYMENT_CODE))
    try:
        return payload(
            account=str(company.get("account", "")), payee_name=str(company.get("name", "")),
            payee_city=str(company.get("city", "")), amount=due,
            payment_code=code, payer=payer,
            purpose_text=purpose(invoice), ref=reference(invoice.number, model),
        )
    except ValueError as exc:
        log.info("IPS QR kod se ne štampa: %s", exc)
        return None


def qr_data_uri(text: str, scale: int = 8) -> str | None:
    """PNG QR koda kao ``data:`` URI (za ``<img>`` u HTML-u fakture); ``None`` bez biblioteke."""
    try:
        import segno
    except ImportError:            # pragma: no cover - zavisi od instalacije
        log.warning("Biblioteka 'segno' nije instalirana – IPS QR kod se ne štampa.")
        return None
    # Uvek UTF-8 (C:1), i kad bi tekst stao u Latin-1; bez ECI oznake, kako čitači očekuju.
    return segno.make(text, error="m", micro=False, encoding="utf-8").png_data_uri(scale=scale, border=4)


__all__ = ["payload", "invoice_payload", "qr_data_uri", "format_amount", "account_digits",
           "reference", "model97", "PAYMENT_CODES", "DEFAULT_PAYMENT_CODE", "MODEL_00", "MODEL_97"]
