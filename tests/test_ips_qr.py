"""NBS IPS QR kod i logo na štampanoj fakturi."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from conftest import make_invoice
from core import DOC_ADVANCE, DOC_PROFORMA, ENTITY_PERSON, PAY_BANK, STATUS_PAID, Partner, ips_qr
from core.printing import image_data_uri, render_invoice_html
from core.validators import make_account

D = Decimal
ACCOUNT = make_account("160", "5100000012345")
COMPANY = {"name": "Moja firma d.o.o.", "city": "Novi Sad", "account": ACCOUNT, "currency": "RSD"}


def test_amount_format_follows_nbs_rules():
    assert ips_qr.format_amount(D("3596.13")) == "RSD3596,13"
    assert ips_qr.format_amount(D("1025.10")) == "RSD1025,1"
    assert ips_qr.format_amount(D("1000")) == "RSD1000,"
    assert ips_qr.format_amount(D("0.5")) == "RSD0,5"


def test_account_is_18_digits():
    assert ips_qr.account_digits("840-955845-10") == "840000000095584510"   # primer iz preporuka NBS
    assert ips_qr.account_digits("840-955845-11") == ""                  # neispravna kontrolna cifra
    assert ips_qr.account_digits(ACCOUNT) == ACCOUNT.replace("-", "") and len(ips_qr.account_digits(ACCOUNT)) == 18
    short = make_account("165", "55")                                   # 165-0000000000055-xx
    assert ips_qr.account_digits(short) == short.replace("-", "")


def test_reference_models():
    assert ips_qr.model97("123412") == "14123412"                       # primer iz preporuka NBS
    assert ips_qr.reference("2026/0001") == "002026-0001"
    assert ips_qr.reference("PR-2026/0007") == "002026-0007"
    ref = ips_qr.reference("2026/0001", ips_qr.MODEL_97)
    assert ref.startswith("97") and ref.endswith("20260001")
    assert int(ref[4:] + ref[2:4]) % 97 == 1                             # ISO 7064 MOD 97-10
    assert ips_qr.reference("bez broja") == ""


def test_payload_structure():
    text = ips_qr.payload(account=ACCOUNT, payee_name="Moja | firma", payee_city="Novi Sad",
                          amount=D("1234.50"), payer=["Kupac d.o.o.", "", "Niš"],
                          purpose_text="Faktura 2026/0001", ref="002026-0001")
    assert text == ("K:PR|V:01|C:1|R:" + ACCOUNT.replace("-", "") + "|N:Moja / firma\r\nNovi Sad"
                    "|I:RSD1234,5|P:Kupac d.o.o.\r\nNiš|SF:221|S:Faktura 2026/0001|RO:002026-0001")
    assert not text.endswith("|") and "|S:|" not in text


def test_payload_limits_and_errors():
    text = ips_qr.payload(account=ACCOUNT, payee_name="A" * 80, payee_city="Grad", amount=D("1"),
                          purpose_text="x" * 50)
    name = text.split("|N:")[1].split("|")[0]
    assert len(name.replace("\r\n", "")) <= 70
    assert "|S:" + "x" * 35 + "|" not in text and "S:" + "x" * 35 in text
    assert "|P:" not in text and "|RO:" not in text
    with pytest.raises(ValueError):
        ips_qr.payload(account="123", payee_name="A", payee_city="", amount=D("1"))
    with pytest.raises(ValueError):
        ips_qr.payload(account=ACCOUNT, payee_name="A", payee_city="", amount=D("0"))
    with pytest.raises(ValueError):
        ips_qr.payload(account=ACCOUNT, payee_name="A", payee_city="", amount=D("1"), payment_code="321")


def test_invoice_payload_rules(invoices, customer, partners):
    inv = invoices.get(invoices.add(make_invoice(customer.id, number="2026/0001", price="1000")))
    text = ips_qr.invoice_payload(inv, customer, COMPANY)
    assert "|I:RSD1200,|" in text and "|SF:221|" in text and "|S:Faktura 2026/0001|" in text
    assert text.endswith("|RO:002026-0001")
    # Isključeno, druga valuta, bez računa
    assert ips_qr.invoice_payload(inv, customer, {**COMPANY, "ips_qr": False}) is None
    assert ips_qr.invoice_payload(inv, customer, {**COMPANY, "currency": "EUR"}) is None
    assert ips_qr.invoice_payload(inv, customer, {**COMPANY, "account": ""}) is None
    # Fizičko lice plaća šifrom 289
    person = Partner(id=99, name="Petar Petrović", entity_type=ENTITY_PERSON)
    assert "|SF:289|" in ips_qr.invoice_payload(inv, person, {**COMPANY, "payment_code": "221"})
    # Plaćena faktura: nema QR koda
    invoices.set_paid(inv.id, True, date(2026, 3, 12), PAY_BANK)
    assert ips_qr.invoice_payload(invoices.get(inv.id), customer, COMPANY) is None


def test_proforma_has_qr_and_advance_not(invoices, customer):
    p = make_invoice(customer.id, number="PR-2026/0001")
    p.doc_kind = DOC_PROFORMA
    pid = invoices.add(p)
    assert "|S:Predračun PR-2026/0001|" in ips_qr.invoice_payload(invoices.get(pid), customer, COMPANY)
    adv = invoices.draft_from_proforma(pid, DOC_ADVANCE, gross=D("600"), on=date(2026, 3, 10))
    adv.number = "AV-2026/0001"
    aid = invoices.add(adv)
    assert invoices.get(aid).status == STATUS_PAID
    assert ips_qr.invoice_payload(invoices.get(aid), customer, COMPANY) is None


def test_qr_amount_is_net_of_advance(invoices, customer):
    p = make_invoice(customer.id, number="PR-2026/0001", price="10000")
    p.doc_kind = DOC_PROFORMA
    pid = invoices.add(p)
    adv = invoices.draft_from_proforma(pid, DOC_ADVANCE, gross=D("6000"), on=date(2026, 3, 10))
    adv.number = "AV-2026/0001"
    invoices.add(adv)
    final = invoices.draft_from_proforma(pid, on=date(2026, 3, 20))
    final.number = "2026/0001"
    fid = invoices.add(final)
    assert "|I:RSD6000,|" in ips_qr.invoice_payload(invoices.get(fid), customer, COMPANY)


def test_printed_invoice_has_qr_and_logo(invoices, customer, tmp_path):
    pytest.importorskip("segno")
    logo = tmp_path / "logo.svg"
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>', encoding="utf-8")
    inv = invoices.get(invoices.add(make_invoice(customer.id, number="2026/0001")))
    html = render_invoice_html(inv, customer, {**COMPANY, "logo": str(logo)})
    assert 'src="data:image/png;base64,' in html                        # QR kod
    assert 'src="data:image/svg+xml;base64,' in html                    # logo
    assert "Poziv na broj: <b>00 2026-0001</b>" in html and ACCOUNT in html
    # Bez računa firme nema ni QR koda ni uputstva za uplatu
    html = render_invoice_html(inv, customer, {"name": "Moja firma"})
    assert "Poziv na broj" not in html and "data:image/png" not in html


def test_image_data_uri(tmp_path):
    assert image_data_uri(None) is None and image_data_uri(tmp_path / "nema.png") is None
    bad = tmp_path / "logo.txt"
    bad.write_text("x")
    assert image_data_uri(bad) is None
    png = tmp_path / "l.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert image_data_uri(png).startswith("data:image/png;base64,")
