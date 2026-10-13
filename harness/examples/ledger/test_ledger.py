import pytest

from ledger import Ledger, apply_fee, format_cents, monthly_interest, parse_amount


def funded() -> Ledger:
    ledger = Ledger()
    ledger.deposit("alice", 10_000, "salary")
    ledger.deposit("bob", 500)
    return ledger


def test_deposit_and_balance():
    ledger = funded()
    assert ledger.balance("alice") == 10_000
    assert ledger.deposit("alice", 1) == 10_001
    with pytest.raises(ValueError):
        ledger.deposit("alice", 0)


def test_withdraw_respects_overdraft_limit():
    ledger = funded()
    assert ledger.withdraw("bob", 500) == 0
    with pytest.raises(ValueError):
        ledger.withdraw("bob", 1)
    ledger.overdraft_cents = 100
    assert ledger.withdraw("bob", 100) == -100
    with pytest.raises(ValueError):
        ledger.withdraw("bob", 1)
    with pytest.raises(ValueError):
        ledger.withdraw("bob", -5)


def test_transfer_moves_money():
    ledger = funded()
    ledger.transfer("alice", "bob", 2_500)
    assert ledger.balance("alice") == 7_500 and ledger.balance("bob") == 3_000
    assert ledger.accounts() == ["alice", "bob"]


def test_statement_shows_running_balance():
    ledger = funded()
    ledger.withdraw("alice", 2_550, "rent")
    assert ledger.statement("alice") == ["+100.00 100.00 salary", "-25.50 74.50 rent"]


def test_format_cents():
    assert format_cents(0) == "0.00"
    assert format_cents(5) == "0.05"
    assert format_cents(-1234) == "-12.34"


def test_parse_amount():
    assert parse_amount("12.5") == 1250
    assert parse_amount("3") == 300
    assert parse_amount("0.07") == 7
    assert parse_amount("-1.10") == -110
    for bad in ("1.234", "abc", "1.x"):
        with pytest.raises(ValueError):
            parse_amount(bad)


def test_monthly_interest_rounds_half_up():
    assert monthly_interest(120_000, 100) == 100     # 1% a year on 1200.00 -> 1.00 a month
    assert monthly_interest(100, 100) == 0            # 0.083 cents rounds down
    assert monthly_interest(700, 100) == 1            # 0.58 cents rounds up
    assert monthly_interest(-500, 100) == 0
    assert monthly_interest(0, 500) == 0


def test_apply_fee_only_below_minimum():
    assert apply_fee(999, 250, 1000) == 749
    assert apply_fee(1000, 250, 1000) == 1000
