"""Explicit source data and auditable money arithmetic for the order."""

from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path

CENTS = Decimal("0.01")


def money(n: Decimal | str | int) -> Decimal:
    return Decimal(str(n)).quantize(CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Address:
    company: str
    street: str
    zip: str
    city: str
    country: str


@dataclass(frozen=True)
class Item:
    sku: str
    description: str
    qty: Decimal
    unit_net: Decimal
    discount_percent: Decimal
    vat_percent: Decimal
    source_line_net: Decimal

    @property
    def line_net(self):
        return money(self.qty * self.unit_net * (Decimal("1") - self.discount_percent / 100))

    @property
    def gross_master_price(self):
        return money(self.unit_net * (1 + self.vat_percent / 100))

    @property
    def line_vat(self):
        return money(self.line_net * self.vat_percent / 100)


@dataclass(frozen=True)
class Order:
    reference: str
    order_date: date
    source_customer_id: str
    currency: str
    company: str
    first_name: str
    last_name: str
    alias: str
    email: str
    phone: str
    billing: Address
    delivery: Address
    payment_method: str
    paid: bool
    payment_date: date | None
    items: tuple[Item, ...]
    source_net: Decimal
    source_vat: Decimal
    source_gross: Decimal

    @property
    def calculated_net(self):
        return money(sum((x.line_net for x in self.items), Decimal("0")))

    @property
    def calculated_vat(self):
        return money(sum((x.line_vat for x in self.items), Decimal("0")))

    @property
    def calculated_gross(self):
        return self.calculated_net + self.calculated_vat

    def validate(self):
        if not self.reference or not self.company or not self.items:
            raise ValueError("Reference, company and at least one item are required")
        if not self.currency or self.currency != "EUR":
            raise ValueError(
                f"Unexpected currency {self.currency!r}; this assessment source is EUR"
            )
        if len({i.sku for i in self.items}) != len(self.items):
            raise ValueError("Repeated SKU in the source image: requires manual line-level review")
        if self.paid and not self.payment_date:
            raise ValueError("PAID requires a payment date")
        for item in self.items:
            if item.qty <= 0 or item.unit_net < 0 or not (0 <= item.discount_percent <= 100):
                raise ValueError(f"Invalid quantity/price/discount for {item.sku}")
            if not (0 <= item.vat_percent <= 100):
                raise ValueError(f"Invalid VAT for {item.sku}")
            if item.line_net != item.source_line_net:
                raise ValueError(
                    f"{item.sku}: OCR net {item.source_line_net} != computed {item.line_net}"
                )
        expected = (self.calculated_net, self.calculated_vat, self.calculated_gross)
        actual = (self.source_net, self.source_vat, self.source_gross)
        if expected != actual:
            raise ValueError(f"Order total mismatch: computed {expected}; source {actual}")

    def save_json(self, path):
        def convert(value):
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, tuple):
                return list(value)
            raise TypeError(type(value))

        Path(path).write_text(json.dumps(asdict(self), default=convert, indent=2), encoding="utf8")


def read_order(path) -> Order:
    d = json.loads(Path(path).read_text(encoding="utf8"))
    d["order_date"] = date.fromisoformat(d["order_date"])
    d["payment_date"] = date.fromisoformat(d["payment_date"]) if d["payment_date"] else None
    d["billing"] = Address(**d["billing"])
    d["delivery"] = Address(**d["delivery"])
    d["items"] = tuple(
        Item(
            **{
                k: (
                    Decimal(str(v))
                    if k
                    in ("qty", "unit_net", "discount_percent", "vat_percent", "source_line_net")
                    else v
                )
                for k, v in x.items()
            }
        )
        for x in d["items"]
    )
    for k in ("source_net", "source_vat", "source_gross"):
        d[k] = Decimal(d[k])
    order = Order(**d)
    order.validate()
    return order
