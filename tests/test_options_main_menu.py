"""Tests for OptionsFlow init main menu presentation."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.helpers import selector

from custom_components.grocy_helper.config_flow import GrocyOptionsFlowHandler
from custom_components.grocy_helper.scan_types import Step
from custom_components.grocy_helper.queue import ScanQueue

from tests.conftest import (
    FakeBarcodeBuddyAPI,
    FakeCoordinator,
    FakeGrocyAPI,
    FakeStore,
    make_master_data,
)


async def _make_flow_with_queue(pending_barcodes: list[str]) -> GrocyOptionsFlowHandler:
    """Create an options flow handler with a populated queue."""
    queue = ScanQueue(FakeStore())
    await queue.async_load()
    for barcode in pending_barcodes:
        await queue.async_add(barcode)

    coordinator = FakeCoordinator(
        grocy_api=FakeGrocyAPI(),
        bbuddy_api=FakeBarcodeBuddyAPI(),
        master_data=make_master_data(),
    )
    coordinator.queue = queue

    config_entry = SimpleNamespace(coordinator=coordinator, data={})

    flow = GrocyOptionsFlowHandler(config_entry)
    flow._config_entry = config_entry
    return flow


async def test_init_menu_shows_queue_counts_in_description():
    """Init form previews pending/failed queue counts."""
    flow = await _make_flow_with_queue(["111", "222"])

    result = await flow.async_step_init(None)

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["description_placeholders"]["pending_count"] == "2"
    assert result["description_placeholders"]["failed_count"] == "0"


async def test_init_menu_uses_list_selector_and_queue_label():
    """Init form uses list-mode selector (radio-style) and queue summary label."""
    flow = await _make_flow_with_queue(["111"])

    result = await flow.async_step_init(None)

    data_schema = result["data_schema"]
    field_validator = next(iter(data_schema.schema.values()))

    assert isinstance(field_validator, selector.SelectSelector)
    assert field_validator.config["mode"] == selector.SelectSelectorMode.LIST

    options = field_validator.config["options"]
    queue_option = next(opt for opt in options if opt["value"] == Step.HANDLE_QUEUE)
    assert "1 pending" in queue_option["label"]
    assert "0 failed" in queue_option["label"]


async def test_init_dispatches_with_string_selector_value():
    """String selector values should dispatch to SCAN_START correctly."""
    flow = await _make_flow_with_queue([])

    result = await flow.async_step_init({"choose_form": Step.SCAN_START.value})

    assert result["type"] == "form"
    assert result["step_id"] == Step.SCAN_START
