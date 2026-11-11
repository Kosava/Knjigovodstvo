"""Generisanje HTML-a fakture za štampu / izvoz u PDF.

Core sloj samo proizvodi HTML (podskup koji razume Qt-ov ``QTextDocument``);
sam ispis i čuvanje PDF-a rade se u GUI sloju. Podaci o firmi dolaze iz
podešavanja i prosleđuju se kao običan rečnik.
"""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path
from typing import Mapping

from i18n import tr, tr_noop

from . import ips_qr, tax
from .models import INVOICE_OUT, Invoice, Partner

_BORDER = "#c9ced6"
_HEAD_BG = "#eef1f5"
_MUTED = "#5f6673"


def _cell(text: str, align: str = "left", bold: bool = False, bg: str | None = None,
          nowrap: bool | None = None) -> str:
    nowrap = align == "right" if nowrap is None else nowrap
    style = f"padding:5px 5px; border-bottom:1px solid {_BORDER};"
    if nowrap:                           # iznosi i količine se nikad ne prelamaju usred broja
        style += " white-space:nowrap;"
    if bg:
        style += f" background-color:{bg};"
    text = f"<b>{text}</b>" if bold else text
    return f'<td align="{align}" style="{style}">{text}</td>'


_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                ".bmp": "image/bmp", ".svg": "image/svg+xml", ".webp": "image/webp"}
_MAX_LOGO_BYTES = 3 * 1024 * 1024


def image_data_uri(path: str | Path | None) -> str | None:
    """Slika (logo) kao ``data:`` URI, da HTML ne zavisi od putanje; ``None`` ako je nema."""
    if not path:
        return None
    path = Path(path)
    mime = _IMAGE_TYPES.get(path.suffix.lower())
    try:
        if mime is None or not path.is_file() or path.stat().st_size > _MAX_LOGO_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _party_block(title: str, name: str, lines: list[str]) -> str:
    body = "<br>".join(escape(l) for l in lines if l)
    return (
        f'<p style="margin:0;"><span style="color:{_MUTED}; font-size:9pt;">{escape(title)}</span><br>'
        f"<b>{escape(name)}</b><br>{body}</p>"
    )


def render_invoice_html(invoice: Invoice, partner: Partner, company: Mapping[str, object]) -> str:
    """Vraća HTML fakture. ``company`` je rečnik iz podešavanja (naziv, PIB...)."""
    invoice.recalculate()
    currency = escape(str(company.get("currency", "RSD")))
    is_out = invoice.invoice_type == INVOICE_OUT

    company_lines = [
        str(company.get("address", "")),
        str(company.get("city", "")),
        f"{tr('PIB')}: {company.get('pib')}" if company.get("pib") else "",
        f"{tr('MB')}: {company.get('registration_number')}" if company.get("registration_number") else "",
        f"{tr('Tekući račun')}: {company.get('account')}" if company.get("account") else "",
        str(company.get("email", "")),
        str(company.get("phone", "")),
    ]
    partner_lines = [
        partner.address,
        partner.city,
        f"{partner.tax_id_label}: {partner.pib}" if partner.pib and partner.tax_id_label else "",
        (f"{tr('IBAN / račun') if partner.is_foreign else tr('Tekući račun')}: {partner.account}"
         if partner.account else ""),
    ]
    company_name = str(company.get("name") or tr("(naziv firme nije unet)"))
    seller = (company_name, company_lines) if is_out else (partner.name, partner_lines)
    buyer = (partner.name, partner_lines) if is_out else (company_name, company_lines)

    # Naslovi i brojevi se ne prelamaju (nowrap), a širine u % sprečavaju da QTextDocument
    # ravnomerno podeli prostor; kolona "Opis" je jedina koja sme da se prelama.
    head = "".join(
        f'<th align="{a}" width="{w}%" style="padding:6px 5px; background-color:{_HEAD_BG}; '
        f'border-bottom:1px solid {_BORDER}; white-space:nowrap;">{escape(tr(t))}</th>'
        for t, a, w in [
            (tr_noop("R.br."), "left", 4), (tr_noop("Opis"), "left", 32), (tr_noop("Količina"), "right", 7),
            (tr_noop("JM"), "left", 5), (tr_noop("Cena"), "right", 10), (tr_noop("PDV %"), "right", 6),
            (tr_noop("Osnovica"), "right", 12), (tr_noop("PDV"), "right", 11), (tr_noop("Ukupno"), "right", 13),
        ]
    )
    rows = []
    for pos, item in enumerate(invoice.items, start=1):
        a = item.amounts
        rows.append(
            "<tr>"
            + _cell(str(pos), nowrap=True)
            + _cell(escape(item.description))
            + _cell(tax.format_quantity(item.quantity), "right")
            + _cell(escape(item.unit), nowrap=True)
            + _cell(tax.format_money(item.unit_price), "right")
            + _cell(tax.format_quantity(item.vat_rate), "right")
            + _cell(tax.format_money(a.net), "right")
            + _cell(tax.format_money(a.vat), "right")
            + _cell(tax.format_money(a.total), "right")
            + "</tr>"
        )

    recap_rows = "".join(
        "<tr>"
        + _cell(tr("PDV {rate} %", rate=tax.format_quantity(rate)))
        + _cell(tax.format_money(a.net), "right")
        + _cell(tax.format_money(a.vat), "right")
        + "</tr>"
        for rate, a in invoice.vat_breakdown().items()
    )
    # Odbijeni avansi po stopama (negativno), da se vidi osnovica i PDV koji ostaju.
    recap_rows += "".join(
        "<tr>"
        + _cell(tr("Avans – PDV {rate} %", rate=tax.format_quantity(rate)))
        + _cell(tax.format_money(-a.net), "right")
        + _cell(tax.format_money(-a.vat), "right")
        + "</tr>"
        for rate, a in invoice.advance_breakdown().items()
    )

    due = f"<br>{tr('Rok plaćanja')}: <b>{tax.format_date(invoice.due_date)}</b>" if invoice.due_date else ""
    dates = f"<br>{tr('Datum prometa')}: <b>{tax.format_date(invoice.effective_supply_date)}</b>{due}"
    if invoice.is_proforma:
        dates = due                   # predračun nema promet; rok je rok za uplatu
    elif invoice.is_advance:          # avansni račun se izdaje na dan prijema avansa
        dates = (f"<br>{tr('Datum uplate')}: <b>{tax.format_date(invoice.paid_date)}</b>"
                 if invoice.paid_date else "")
    note = (
        f'<p style="margin-top:14px;"><span style="color:{_MUTED};">{tr('Napomena')}:</span> '
        f"{escape(invoice.note)}</p>" if invoice.note.strip() else ""
    )
    title = tr("FAKTURA") if is_out else tr("ULAZNA FAKTURA")
    if invoice.is_proforma:
        title = tr("PREDRAČUN")
    elif invoice.is_advance:
        title = tr("AVANSNI RAČUN")
    # Status je interna informacija: na fakturi koja ide kupcu se ne štampa.
    status_line = ("" if is_out else
                   f'<p style="margin:0; color:{_MUTED};">{tr('Status')}: <b>{escape(tr(invoice.status))}</b></p>')
    storno_ref = ""
    if invoice.is_storno:
        title = tr("STORNO AVANSNOG RAČUNA") if invoice.is_advance else tr("STORNO FAKTURA")
        if invoice.storno_of_number:
            storno_ref = f"<br>{tr('Stornira fakturu br.')} <b>{escape(invoice.storno_of_number)}</b>"
    elif invoice.source_number:
        storno_ref = f"<br>{tr('Po predračunu br.')} <b>{escape(invoice.source_number)}</b>"
    if invoice.is_proforma:
        note += (f'<p style="margin-top:14px; color:{_MUTED};">'
                 f'{tr("Predračun nije poreska isprava. Po uplati se izdaje račun.")}</p>')

    # Logo firme iznad podataka izdavaoca (samo na dokumentima koje firma izdaje).
    logo_uri = image_data_uri(company.get("logo")) if is_out else None
    logo = (f'<p style="margin:0 0 8px 0;"><img src="{logo_uri}" height="56"></p>' if logo_uri else "")

    # Uplata: račun i poziv na broj + NBS IPS QR kod za skeniranje u aplikaciji banke.
    payment = ""
    qr_text = ips_qr.invoice_payload(invoice, partner, company)
    if qr_text:
        model = str(company.get("payment_model", ips_qr.MODEL_00))
        ref = ips_qr.reference(invoice.number, model)
        lines = [f"{tr('Račun za uplatu')}: <b>{escape(str(company.get('account', '')))}</b>"]
        if ref:
            lines.append(f"{tr('Poziv na broj')}: <b>{escape(ref[:2])} {escape(ref[2:])}</b>")
        qr_uri = ips_qr.qr_data_uri(qr_text)
        qr_img = (f'<img src="{qr_uri}" width="128" height="128">' if qr_uri else "")
        payment = f"""
<table cellspacing="0" cellpadding="0" style="margin-top:14px;"><tr>
  <td valign="middle">{qr_img}</td>
  <td valign="middle" style="padding-left:10px;">
    <span style="color:{_MUTED}; font-size:9pt;">{tr('Plaćanje putem IPS QR koda – skenirajte aplikacijom banke')}</span><br>
    {'<br>'.join(lines)}</td>
</tr></table>"""

    money = lambda value: tax.format_money(value) + " " + currency   # noqa: E731
    if invoice.advances or invoice.advance_total:
        lines = [(tr("Ukupno bez PDV-a"), money(invoice.net_amount)),
                 (tr("PDV"), money(invoice.vat_amount)),
                 (tr("Ukupno"), money(invoice.total_amount))]
        if invoice.advances:
            lines += [(tr("Umanjenje za avans {number}", number=a.number), money(-a.amounts.total))
                      for a in invoice.advances]
        else:                         # storno dokument: avansi bez veza, samo zbir
            lines.append((tr("Umanjenje za avans"), money(-invoice.advance_total)))
        totals = "".join(f"<tr>{_cell(escape(label))}{_cell(value, 'right')}</tr>" for label, value in lines)
        totals += (f"<tr>{_cell(tr('ZA UPLATU'), bold=True, bg=_HEAD_BG)}"
                   f"{_cell(money(invoice.amount_due), 'right', True, _HEAD_BG)}</tr>")
    else:
        final = tr("UKUPNO") if invoice.is_advance else tr("UKUPNO ZA UPLATU")
        totals = (f"<tr>{_cell(tr('Ukupno bez PDV-a'))}{_cell(money(invoice.net_amount), 'right')}</tr>"
                  f"<tr>{_cell(tr('PDV'))}{_cell(money(invoice.vat_amount), 'right')}</tr>"
                  f"<tr>{_cell(final, bold=True, bg=_HEAD_BG)}"
                  f"{_cell(money(invoice.total_amount), 'right', True, _HEAD_BG)}</tr>")

    return f"""
<html><body style="font-family:'Segoe UI','DejaVu Sans',sans-serif; font-size:10pt; color:#1f2430;">
<table width="100%" cellspacing="0" cellpadding="0"><tr>
  <td width="55%" valign="top">{logo}{_party_block(tr("Izdavalac"), *seller)}</td>
  <td width="45%" valign="top" align="right">
    <h1 style="margin:0; font-size:20pt;">{title}</h1>
    <p style="margin:4px 0 0 0;">{tr('Broj')}: <b>{escape(invoice.number)}</b><br>
    {tr('Datum')}: <b>{tax.format_date(invoice.issue_date)}</b>{dates}{storno_ref}</p>
  </td>
</tr></table>
<p></p>
<table width="100%" cellspacing="0" cellpadding="0"><tr>
  <td width="55%" valign="top">{_party_block(tr("Kupac"), *buyer)}</td>
  <td width="45%" valign="top" align="right">
    {status_line}
  </td>
</tr></table>
<p></p>
<table width="100%" cellspacing="0" cellpadding="0" style="font-size:9pt;"><tr>{head}</tr>{''.join(rows)}</table>
<p></p>
<table width="100%" cellspacing="0" cellpadding="0"><tr>
  <td width="50%" valign="top">
    <table width="100%" cellspacing="0" cellpadding="0">
      <tr><th align="left" style="padding:6px 7px; background-color:{_HEAD_BG}; white-space:nowrap;">{tr('Rekapitulacija')}</th>
      <th align="right" style="padding:6px 7px; background-color:{_HEAD_BG}; white-space:nowrap;">{tr('Osnovica')}</th>
      <th align="right" style="padding:6px 7px; background-color:{_HEAD_BG}; white-space:nowrap;">{tr('PDV')}</th></tr>
      {recap_rows}
    </table>
  </td>
  <td width="5%"></td>
  <td width="45%" valign="top">
    <table width="100%" cellspacing="0" cellpadding="0">{totals}</table>
  </td>
</tr></table>
{payment}
{note}
</body></html>
"""
