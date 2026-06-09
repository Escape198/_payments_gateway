from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

Currency = NewType("Currency", str)

ISO_4217_MINOR_UNITS: dict[str, int] = {
    "USD": 2, "EUR": 2, "GBP": 2, "PLN": 2, "RUB": 2, "BYN": 2,
    "JPY": 0, "KRW": 0, "VND": 0,
    "BHD": 3, "KWD": 3, "OMR": 3, "JOD": 3,
}


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount_minor, int) or self.amount_minor < 0:
            raise ValueError("amount_minor must be a non-negative int (minor units)")
        if self.currency not in ISO_4217_MINOR_UNITS:
            raise ValueError(f"unsupported currency: {self.currency}")

    @classmethod
    def of(cls, amount_minor: int, currency: str) -> "Money":
        return cls(int(amount_minor), Currency(currency.upper()))

    def add(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def subtract(self, other: "Money") -> "Money":
        self._same(other)
        if other.amount_minor > self.amount_minor:
            raise ValueError("subtraction would produce negative Money")
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def _same(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(f"cross-currency op forbidden: {self.currency}/{other.currency}")
