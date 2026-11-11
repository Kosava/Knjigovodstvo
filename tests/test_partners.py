"""Vrste partnera: domaće pravno lice (PIB obavezan), fizičko lice, strani partner."""

import sqlite3

import pytest

from conftest import make_invoice
from core import (
    ENTITY_FOREIGN,
    ENTITY_LEGAL,
    ENTITY_PERSON,
    DatabaseManager,
    Partner,
    PartnerRepository,
    ValidationError,
)
from core.database import SCHEMA_VERSION
from core.printing import render_invoice_html
from core.validators import make_account, make_pib


def test_legal_entity_requires_valid_pib(partners):
    with pytest.raises(ValidationError, match="obavezan za pravno lice"):
        partners.add(Partner(name="Firma", entity_type=ENTITY_LEGAL))
    with pytest.raises(ValidationError, match="nije ispravan"):
        partners.add(Partner(name="Firma", pib="123456789"))


def test_persons_need_no_pib_and_can_be_many(partners):
    a = partners.add(Partner(name="Marko Marković", entity_type=ENTITY_PERSON))
    b = partners.add(Partner(name="Jovana Jovanović", entity_type=ENTITY_PERSON,
                             pib="111"))                       # PIB se kod fizičkog lica briše
    assert partners.get(a).pib == "" and partners.get(b).pib == ""


def test_person_account_is_still_checked(partners):
    with pytest.raises(ValidationError, match="Tekući račun"):
        partners.add(Partner(name="Ana", entity_type=ENTITY_PERSON, account="160-123-45"))
    partners.add(Partner(name="Ana", entity_type=ENTITY_PERSON, account=make_account("160", "42")))


def test_foreign_partner_free_tax_id_and_iban(partners):
    p = Partner(name="Muster GmbH", entity_type=ENTITY_FOREIGN, pib="DE123456789",
                account="DE89 3704 0044 0532 0130 00", partner_type="Dobavljač")
    pid = partners.add(p)
    loaded = partners.get(pid)
    assert loaded.pib == "DE123456789" and loaded.account.startswith("DE89 3704")
    partners.add(Partner(name="Bez broja Ltd", entity_type=ENTITY_FOREIGN))
    with pytest.raises(ValidationError, match="već koristi"):
        partners.add(Partner(name="Duplikat", entity_type=ENTITY_FOREIGN, pib="DE123456789"))


def test_switching_to_person_clears_pib(partners):
    p = Partner(name="Preduzetnik", pib=make_pib("20000001"))
    partners.add(p)
    p.entity_type = ENTITY_PERSON
    partners.update(p)
    assert partners.get(p.id).pib == ""


def test_invoice_print_for_person_and_foreign(invoices, partners):
    person = Partner(name="Petar Petrović", entity_type=ENTITY_PERSON, address="Glavna 1")
    foreign = Partner(name="Muster GmbH", entity_type=ENTITY_FOREIGN, pib="DE123")
    partners.add(person)
    partners.add(foreign)
    inv = invoices.get(invoices.add(make_invoice(person.id)))
    html = render_invoice_html(inv, partners.get(person.id), {"name": "Moja firma", "pib": "100000016"})
    buyer = html[html.index("Kupac"):]
    assert "Petar Petrović" in buyer and "PIB:" not in buyer
    inv2 = invoices.get(invoices.add(make_invoice(foreign.id, number="2026/0002")))
    html2 = render_invoice_html(inv2, partners.get(foreign.id), {"name": "Moja firma"})
    assert "Poreski broj: DE123" in html2


def test_migration_v2_to_v3_keeps_partners(tmp_path):
    path = tmp_path / "v2.db"
    db = DatabaseManager(path)
    db.open()
    PartnerRepository(db).add(Partner(name="Stara firma", pib=make_pib("10000001")))
    db.close()
    # Vraćamo tabelu partnera u oblik iz verzije 2 (PIB obavezan i UNIQUE, bez vrste).
    con = sqlite3.connect(path)
    con.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE p2 (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, pib TEXT NOT NULL UNIQUE,
          address TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '', account TEXT NOT NULL DEFAULT '',
          partner_type TEXT NOT NULL DEFAULT 'Kupac', email TEXT NOT NULL DEFAULT '',
          phone TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO p2 (id, name, pib, address, city, account, partner_type, email, phone, created_at)
          SELECT id, name, pib, address, city, account, partner_type, email, phone, created_at FROM partners;
        DROP TABLE partners; ALTER TABLE p2 RENAME TO partners;
        PRAGMA user_version = 2;
    """)
    con.close()

    db.open()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert (tmp_path / "v2.pre-v3.db").exists()
    repo = PartnerRepository(db)
    assert repo.get(1).entity_type == ENTITY_LEGAL and repo.get(1).name == "Stara firma"
    repo.add(Partner(name="A", entity_type=ENTITY_PERSON))
    repo.add(Partner(name="B", entity_type=ENTITY_PERSON))     # dva prazna PIB-a: dozvoljeno
    db.close()
