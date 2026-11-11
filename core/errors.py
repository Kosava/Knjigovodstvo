"""Izuzeci aplikacije.

Svi izuzeci koje core sloj namerno podiže nasleđuju ``AppError``, pa ih GUI
može uhvatiti na jednom mestu i korisniku prikazati razumljivu poruku.
"""

from __future__ import annotations

from typing import Iterable


class AppError(Exception):
    """Osnovni izuzetak aplikacije."""


class ValidationError(AppError):
    """Podaci koje je korisnik unio nisu ispravni.

    Može nositi više poruka odjednom (po jedna za svako neispravno polje).
    """

    def __init__(self, messages: str | Iterable[str]) -> None:
        if isinstance(messages, str):
            messages = [messages]
        self.messages: list[str] = [str(m) for m in messages]
        super().__init__("\n".join(self.messages))


class DatabaseError(AppError):
    """Greška pri radu sa SQLite bazom podataka."""
