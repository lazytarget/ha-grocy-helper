"""Smoke tests to verify test infrastructure works."""

from tests.conftest import (
    FakeCoordinator,
    FakeGrocyAPI,
    FakeBarcodeBuddyAPI,
    FakeStore,
    make_master_data,
    make_product,
)


def test_fake_coordinator_has_master_data(fake_coordinator: FakeCoordinator):
    """Coordinator fixture has valid master data."""
    assert fake_coordinator.data is not None
    assert "products" in fake_coordinator.data
    assert "locations" in fake_coordinator.data
    assert "known_qu" in fake_coordinator.data


async def test_fake_grocy_api_register_and_lookup(fake_grocy_api: FakeGrocyAPI):
    """Registering a product makes it findable by barcode."""
    product = make_product(id=42, name="Milk")
    fake_grocy_api.register_product(product, barcodes=["7340011492900"])

    result = await fake_grocy_api.get_stock_product_by_barcode("7340011492900")
    assert result is not None
    assert result["product"]["name"] == "Milk"


async def test_fake_grocy_api_barcode_not_found(fake_grocy_api: FakeGrocyAPI):
    """Unknown barcode returns None."""
    result = await fake_grocy_api.get_stock_product_by_barcode("0000000000000")
    assert result is None


async def test_fake_bbuddy_api_mode(fake_bbuddy_api: FakeBarcodeBuddyAPI):
    """BBuddy fake tracks mode changes."""
    assert await fake_bbuddy_api.get_mode() == 2  # PURCHASE
    await fake_bbuddy_api.set_mode(5)
    assert await fake_bbuddy_api.get_mode() == 5  # ADD_TO_SHOPPING_LIST


async def test_fake_store_round_trip(fake_store: FakeStore):
    """Store fake persists and loads data."""
    assert await fake_store.async_load() is None

    await fake_store.async_save({"items": [1, 2, 3], "mode": "BBUDDY-P"})
    loaded = await fake_store.async_load()
    assert loaded == {"items": [1, 2, 3], "mode": "BBUDDY-P"}


async def test_fake_coordinator_lookup_barcode(fake_coordinator: FakeCoordinator):
    """Coordinator barcode lookup returns empty result for unknown codes."""
    result = await fake_coordinator.lookup_barcode("9999999999999")
    assert result["barcode"] == "9999999999999"
    assert result["product_aliases"] == []


async def test_fake_coordinator_add_stock(fake_coordinator: FakeCoordinator):
    """Coordinator delegates add_stock to grocy API."""
    result = await fake_coordinator.add_stock(1, {"amount": 1})
    assert result is not None
    assert fake_coordinator._api_grocy._added_stock == [(1, {"amount": 1})]
