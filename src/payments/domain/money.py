from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

Currency = NewType("Currency", str)

_ISO_4217_MINOR_UNITS: dict[str, int] = {
    "USD": 2, "EUR": 2, "GBP": 2, "PLN": 2, "RUB": 2, "BYN": 2,
    "JPY": 0, "KRW": 0, "VND": 0,
    "BHD": 3, "KWD": 3, "OMR": 3, "JOD": 3,
}


def minor_units_for(currency: str) -> int:
    code = currency.upper()
    if code not in _ISO_4217_MINOR_UNITS:
        raise ValueError(f"Unsupported currency: {currency!r}")
    return _ISO_4217_MINOR_UNITS[code]


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount_minor, int):
            raise TypeError("amount_minor must be int (minor units)")
        if self.amount_minor < 0:
            raise ValueError("Money cannot be negative; use a refund/void operation instead")
        minor_units_for(self.currency)

    @classmethod
    def of(cls, amount_minor: int, currency: str) -> "Money":
        return cls(amount_minor=int(amount_minor), currency=Currency(currency.upper()))

    def add(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def subtract(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        if other.amount_minor > self.amount_minor:
            raise ValueError("subtraction would produce negative Money")
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def is_zero(self) -> bool:
        return self.amount_minor == 0

    def _assert_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"Cross-currency arithmetic forbidden: {self.currency} vs {other.currency}"
            )

    def as_major_string(self) -> str:
        scale = minor_units_for(self.currency)
        if scale == 0:
            return str(self.amount_minor)
        whole, frac = divmod(self.amount_minor, 10 ** scale)
        return f"{whole}.{frac:0{scale}d}"
