"""A small stock-keeping module used to generate practice tasks for LoopOS loops."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Item:
    sku: str
    price_cents: int
    quantity: int = 0


@dataclass
class Inventory:
    items: dict[str, Item] = field(default_factory=dict)
    reorder_level: int = 5

    def add(self, sku: str, price_cents: int, quantity: int = 0) -> Item:
        if price_cents < 0 or quantity < 0:
            raise ValueError("price and quantity must not be negative")
        item = self.items.get(sku)
        if item is None:
            item = self.items[sku] = Item(sku, price_cents, quantity)
        else:
            item.quantity += quantity
            item.price_cents = price_cents
        return item

    def remove(self, sku: str, quantity: int) -> int:
        item = self.items[sku]
        if quantity > item.quantity:
            raise ValueError(f"only {item.quantity} of {sku} in stock")
        item.quantity -= quantity
        return item.quantity

    def total_value(self) -> int:
        return sum(item.price_cents * item.quantity for item in self.items.values())

    def needs_reorder(self) -> list[str]:
        return sorted(sku for sku, item in self.items.items() if item.quantity <= self.reorder_level)

    def in_stock(self, sku: str) -> bool:
        item = self.items.get(sku)
        return item is not None and item.quantity > 0


def bulk_discount(quantity: int, unit_cents: int) -> int:
    """Total price with 10% off orders of 10 or more and 20% off orders of 50 or more."""
    total = quantity * unit_cents
    if quantity >= 50:
        return total * 80 // 100
    if quantity >= 10:
        return total * 90 // 100
    return total


def split_evenly(total_cents: int, people: int) -> list[int]:
    """Split a bill so shares differ by at most one cent and add up to the total."""
    if people <= 0:
        raise ValueError("people must be positive")
    share, extra = divmod(total_cents, people)
    return [share + 1 if index < extra else share for index in range(people)]
