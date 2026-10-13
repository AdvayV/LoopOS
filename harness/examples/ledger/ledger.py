"""A tiny double-entry ledger used to generate practice tasks for LoopOS loops."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Entry:
    account: str
    cents: int        # positive = credit, negative = debit
    memo: str = ""


@dataclass
class Ledger:
    entries: list[Entry] = field(default_factory=list)
    overdraft_cents: int = 0

    def balance(self, account: str) -> int:
        return sum(e.cents for e in self.entries if e.account == account)

    def deposit(self, account: str, cents: int, memo: str = "") -> int:
        if cents <= 0:
            raise ValueError("deposit must be positive")
        self.entries.append(Entry(account, cents, memo))
        return self.balance(account)

    def withdraw(self, account: str, cents: int, memo: str = "") -> int:
        if cents <= 0:
            raise ValueError("withdrawal must be positive")
        if self.balance(account) - cents < -self.overdraft_cents:
            raise ValueError("insufficient funds")
        self.entries.append(Entry(account, -cents, memo))
        return self.balance(account)

    def transfer(self, source: str, target: str, cents: int) -> None:
        self.withdraw(source, cents, f"to {target}")
        self.deposit(target, cents, f"from {source}")

    def accounts(self) -> list[str]:
        return sorted({e.account for e in self.entries})

    def statement(self, account: str) -> list[str]:
        running = 0
        lines = []
        for entry in self.entries:
            if entry.account != account:
                continue
            running += entry.cents
            sign = "+" if entry.cents > 0 else "-"
            lines.append(f"{sign}{format_cents(abs(entry.cents))} {format_cents(running)} {entry.memo}".rstrip())
        return lines


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def parse_amount(text: str) -> int:
    """'12.5' -> 1250, '3' -> 300, '0.07' -> 7."""
    text = text.strip()
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    whole, _, fraction = text.partition(".")
    if not whole.isdigit() or (fraction and not fraction.isdigit()) or len(fraction) > 2:
        raise ValueError(f"bad amount {text!r}")
    cents = int(whole) * 100 + int((fraction + "00")[:2])
    return -cents if negative else cents


def monthly_interest(balance_cents: int, annual_rate_bp: int) -> int:
    """Interest for one month, in cents, rounded half up. Rate is in basis points (1% = 100)."""
    if balance_cents <= 0:
        return 0
    numerator = balance_cents * annual_rate_bp
    denominator = 12 * 10_000
    return (numerator + denominator // 2) // denominator


def apply_fee(balance_cents: int, fee_cents: int, minimum_cents: int) -> int:
    """Charge the fee only when the balance is below the minimum."""
    if balance_cents < minimum_cents:
        return balance_cents - fee_cents
    return balance_cents
