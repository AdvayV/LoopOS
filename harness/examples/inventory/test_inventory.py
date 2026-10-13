import pytest

from inventory import Inventory, bulk_discount, split_evenly


def stocked() -> Inventory:
    inventory = Inventory()
    inventory.add("apple", 50, 20)
    inventory.add("pear", 80, 3)
    return inventory


def test_add_merges_quantities_and_updates_price():
    inventory = stocked()
    inventory.add("apple", 60, 5)
    assert inventory.items["apple"].quantity == 25
    assert inventory.items["apple"].price_cents == 60


def test_add_rejects_negative_values():
    with pytest.raises(ValueError):
        Inventory().add("x", -1)
    with pytest.raises(ValueError):
        Inventory().add("x", 1, -1)


def test_remove_reduces_stock_and_refuses_overdraw():
    inventory = stocked()
    assert inventory.remove("apple", 20) == 0
    with pytest.raises(ValueError):
        inventory.remove("pear", 4)


def test_total_value():
    assert stocked().total_value() == 50 * 20 + 80 * 3


def test_needs_reorder_includes_items_at_the_level():
    inventory = stocked()
    inventory.add("plum", 10, 5)
    assert inventory.needs_reorder() == ["pear", "plum"]


def test_in_stock():
    inventory = stocked()
    inventory.remove("pear", 3)
    assert inventory.in_stock("apple")
    assert not inventory.in_stock("pear")
    assert not inventory.in_stock("kiwi")


@pytest.mark.parametrize("quantity, expected", [(9, 900), (10, 900), (49, 4410), (50, 4000)])
def test_bulk_discount_tiers(quantity, expected):
    assert bulk_discount(quantity, 100) == expected


def test_split_evenly():
    assert split_evenly(100, 3) == [34, 33, 33]
    assert split_evenly(90, 3) == [30, 30, 30]
    assert sum(split_evenly(101, 4)) == 101
    with pytest.raises(ValueError):
        split_evenly(10, 0)
