"""Tests for the Seymour Masking Screen diagnostics."""

import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant


@pytest.fixture
def mock_config_entry(mock_config_entry: MockConfigEntry) -> MockConfigEntry:
    """Use an ESPHome proxy, whose API key must not show up."""
    return MockConfigEntry(
        domain=mock_config_entry.domain,
        title=mock_config_entry.title,
        data={CONF_DEVICE: "esphome://proxy.local:6053/?port_name=RS232&key=SECRET"},
        unique_id=mock_config_entry.unique_id,
        entry_id=mock_config_entry.entry_id,
    )


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the config entry diagnostics."""
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, init_integration
    )

    assert diagnostics == snapshot
    assert "SECRET" not in str(diagnostics)
    assert "12345" not in str(diagnostics)
