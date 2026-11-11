"""Demo podaci za isprobavanje aplikacije (svi nazivi i brojevi su izmišljeni)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .database import DatabaseManager
from .models import (
    ARTICLE_GOODS,
    ARTICLE_SERVICE,
    DOC_ADVANCE,
    DOC_INVOICE,
    DOC_PROFORMA,
    VAT_REDUCED,
    Article,
    CASH_IN,
    CASH_OUT,
    INVOICE_IN,
    INVOICE_OUT,
    ENTITY_FOREIGN,
    ENTITY_PERSON,
    PARTNER_BOTH,
    PARTNER_CUSTOMER,
    PARTNER_SUPPLIER,
    PAY_BANK,
    PAY_CASH,
    STATUS_PAID,
    CashEntry,
    Invoice,
    InvoiceItem,
    Partner,
)
from .repositories import ArticleRepository, CashRepository, InvoiceRepository, PartnerRepository
from .validators import make_account, make_pib

D = Decimal


def _partners() -> list[Partner]:
    return [
        Partner(name="Agro Plus d.o.o.", pib=make_pib("10234567"), address="Vojvode Stepe 45",
                city="Novi Sad", account=make_account("160", "5100000012345"),
                partner_type=PARTNER_CUSTOMER, email="office@agroplus.example", phone="021 555 101"),
        Partner(name="Tehno Servis d.o.o.", pib=make_pib("10345678"), address="Bulevar Nemanjića 12",
                city="Niš", account=make_account("205", "9000000067890"),
                partner_type=PARTNER_CUSTOMER, email="racuni@tehnoservis.example", phone="018 444 202"),
        Partner(name="Pekara Zlatno Klasje", pib=make_pib("10456789"), address="Kralja Petra 8",
                city="Kragujevac", account=make_account("325", "9500000011122"),
                partner_type=PARTNER_CUSTOMER, email="", phone="034 333 303"),
        Partner(name="Papirus Veleprodaja d.o.o.", pib=make_pib("10567890"), address="Industrijska zona bb",
                city="Subotica", account=make_account("265", "1100000033344"),
                partner_type=PARTNER_SUPPLIER, email="narudzbe@papirus.example", phone="024 222 404"),
        Partner(name="Energo Net d.o.o.", pib=make_pib("10678901"), address="Cara Dušana 101",
                city="Beograd", account=make_account("170", "0030000055566"),
                partner_type=PARTNER_SUPPLIER, email="", phone="011 777 505"),
        Partner(name="Dunav Logistika d.o.o.", pib=make_pib("10789012"), address="Dunavska 3",
                city="Smederevo", account=make_account("160", "6000000077788"),
                partner_type=PARTNER_BOTH, email="info@dunavlog.example", phone="026 111 606"),
        Partner(name="Marko Petrović", entity_type=ENTITY_PERSON, address="Njegoševa 7",
                city="Zrenjanin", partner_type=PARTNER_CUSTOMER, phone="064 123 4567"),
        Partner(name="Alpen Software GmbH", entity_type=ENTITY_FOREIGN, pib="ATU12345678",
                address="Hauptstraße 12", city="Graz, Austrija",
                account="AT61 1904 3002 3457 3201", partner_type=PARTNER_SUPPLIER),
    ]


def _articles() -> list[Article]:
    return [
        Article(code="U-001", name="Održavanje web prodavnice", kind=ARTICLE_SERVICE, unit="mj",
                price=D("85000")),
        Article(code="U-002", name="Konsultantske usluge", kind=ARTICLE_SERVICE, unit="h", price=D("4300")),
        Article(code="U-003", name="Dodatni razvoj modula", kind=ARTICLE_SERVICE, unit="h", price=D("4500")),
        Article(code="U-004", name="Izrada izveštaja", kind=ARTICLE_SERVICE, unit="kom", price=D("48000")),
        Article(code="U-005", name="Kasa - instalacija i obuka", kind=ARTICLE_SERVICE, unit="kom",
                price=D("62000")),
        Article(code="L-001", name="Godišnja licenca", kind=ARTICLE_GOODS, unit="kom", price=D("36000")),
        Article(code="R-001", name="Štampa uputstava", kind=ARTICLE_GOODS, unit="kom", price=D("1200"),
                vat_category=VAT_REDUCED),
        Article(code="R-002", name="Bar-kod čitač", kind=ARTICLE_GOODS, unit="kom", price=D("9800")),
    ]


def seed_demo_data(db: DatabaseManager) -> bool:
    """Popunjava praznu bazu demo podacima. Vraća ``False`` ako baza već ima podatke."""
    partners = PartnerRepository(db)
    invoices = InvoiceRepository(db)
    cash = CashRepository(db)
    if partners.count() > 0:
        return False

    ids: dict[str, int] = {}
    for partner in _partners():
        ids[partner.name] = partners.add(partner)
    articles = ArticleRepository(db)
    if articles.count() == 0:
        for article in _articles():
            articles.add(article)

    today = date.today()
    seq = 0

    def out(partner: str, days_ago: int, terms: int, items: list[tuple], paid: bool, note: str = "",
            method: str = PAY_BANK) -> None:
        nonlocal seq
        seq += 1
        issued = today - timedelta(days=days_ago)
        invoice = Invoice(
            invoice_type=INVOICE_OUT, number=f"{issued.year}/{seq:04d}", issue_date=issued,
            due_date=issued + timedelta(days=terms), partner_id=ids[partner],
            status=STATUS_PAID if paid else "Neplaćeno", note=note, payment_method=method,
            paid_date=(issued + timedelta(days=min(terms, 10))) if paid else None,
            items=[InvoiceItem(d, D(q), u, D(p), D(v)) for d, q, u, p, v in items],
        )
        if invoice.paid_date and invoice.paid_date > today:
            invoice.paid_date = today
        invoices.add(invoice)

    def incoming(partner: str, number: str, days_ago: int, terms: int, items: list[tuple], paid: bool) -> None:
        issued = today - timedelta(days=days_ago)
        invoice = Invoice(
            invoice_type=INVOICE_IN, number=number, issue_date=issued,
            due_date=issued + timedelta(days=terms), partner_id=ids[partner],
            status=STATUS_PAID if paid else "Neplaćeno",
            paid_date=min(today, issued + timedelta(days=terms)) if paid else None,
            items=[InvoiceItem(d, D(q), u, D(p), D(v)) for d, q, u, p, v in items],
        )
        invoices.add(invoice)

    # Izlazne fakture (prihodi) raspoređene na poslednjih ~5 meseci
    out("Agro Plus d.o.o.", 150, 15, [("Održavanje web prodavnice", 1, "kom", 85000, 20)], True)
    out("Tehno Servis d.o.o.", 135, 15, [("Konsultantske usluge", 24, "h", 4200, 20)], True)
    out("Pekara Zlatno Klasje", 120, 8, [("Kasa - instalacija i obuka", 1, "kom", 62000, 20)], True,
        method=PAY_CASH)   # naplaćeno gotovinom
    out("Agro Plus d.o.o.", 105, 15, [("Održavanje web prodavnice", 1, "kom", 85000, 20),
                                      ("Dodatni razvoj modula", 10, "h", 4500, 20)], True)
    out("Dunav Logistika d.o.o.", 90, 15, [("Izrada izveštaja", 1, "kom", 48000, 20)], True)
    out("Tehno Servis d.o.o.", 70, 15, [("Konsultantske usluge", 30, "h", 4200, 20)], True)
    out("Agro Plus d.o.o.", 45, 15, [("Održavanje web prodavnice", 1, "kom", 85000, 20)], True)
    out("Pekara Zlatno Klasje", 38, 8, [("Godišnja licenca", 1, "kom", 36000, 20),
                                        ("Štampa uputstava", 5, "kom", 1200, 10)], False,
        "Račun je dospeo, poslati opomenu.")
    out("Tehno Servis d.o.o.", 20, 15, [("Konsultantske usluge", 18, "h", 4300, 20)], False)
    # Predračun -> avansni račun (pola iznosa, plaćeno) -> konačna faktura sa odbijenim avansom
    def proforma(partner: str, days_ago: int, items: list[tuple], note: str = "") -> int:
        issued = today - timedelta(days=days_ago)
        doc = Invoice(invoice_type=INVOICE_OUT, doc_kind=DOC_PROFORMA, issue_date=issued,
                      number=invoices.next_number(INVOICE_OUT, issued.year, DOC_PROFORMA),
                      due_date=issued + timedelta(days=8), partner_id=ids[partner], note=note,
                      items=[InvoiceItem(d, D(q), u, D(p), D(v)) for d, q, u, p, v in items])
        return invoices.add(doc)

    kasa = proforma("Pekara Zlatno Klasje", 30, [("Kasa - instalacija i obuka", 1, "kom", 62000, 20),
                                                  ("Bar-kod čitač", 2, "kom", 9800, 20)])
    on = today - timedelta(days=27)
    adv = invoices.draft_from_proforma(kasa, DOC_ADVANCE, gross=D("50000"), on=on)
    adv.number, adv.payment_method = invoices.next_number(INVOICE_OUT, on.year, DOC_ADVANCE), PAY_BANK
    invoices.add(adv)
    on = today - timedelta(days=16)
    final = invoices.draft_from_proforma(kasa, DOC_INVOICE, on=on)
    seq += 1
    final.number = f"{on.year}/{seq:04d}"
    invoices.add(final)
    proforma("Tehno Servis d.o.o.", 3, [("Dodatni razvoj modula", 40, "h", 4500, 20)],
             "Ponuda važi 15 dana.")

    out("Agro Plus d.o.o.", 12, 15, [("Održavanje web prodavnice", 1, "kom", 85000, 20),
                                     ("Izmene korisničkog interfejsa", 6, "h", 4500, 20)], False)
    out("Dunav Logistika d.o.o.", 4, 30, [("Integracija sa magacinom", 1, "kom", 120000, 20)], False)

    # Ulazne fakture (rashodi)
    incoming("Papirus Veleprodaja d.o.o.", "PV-1184/26", 100, 15,
             [("Kancelarijski materijal", 1, "kom", 14500, 20)], True)
    incoming("Energo Net d.o.o.", "E-77201", 62, 10, [("Električna energija", 1, "mj", 18200, 10)], True)
    incoming("Papirus Veleprodaja d.o.o.", "PV-1342/26", 33, 15,
             [("Papir A4 (paket)", 20, "kom", 640, 20), ("Toneri", 2, "kom", 6800, 20)], True)
    incoming("Energo Net d.o.o.", "E-80466", 28, 10, [("Električna energija", 1, "mj", 19400, 10)], False)
    incoming("Dunav Logistika d.o.o.", "DL-2026-318", 9, 20,
             [("Transportne usluge", 1, "kom", 22000, 20)], False)

    # Ručni gotovinski unosi u blagajni (fakture automatski prave svoje unose)
    manual = [
        (150, CASH_IN, "Početno stanje blagajne", 50000),
        (88, CASH_OUT, "Gorivo", 6200),
        (61, CASH_OUT, "Poštanske usluge", 1450),
        (35, CASH_OUT, "Kafa i zalihe za kancelariju", 3980),
        (14, CASH_IN, "Povraćaj kaucije", 4000),
        (6, CASH_OUT, "Taksi do klijenta", 1100),
    ]
    for days_ago, kind, text, amount in manual:
        cash.add(CashEntry(entry_date=today - timedelta(days=days_ago), entry_type=kind,
                           description=text, amount=D(amount)))
    return True
