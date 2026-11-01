"""Config flow for the Seymour Masking Screen integration."""

import asyncio
import os
import socket
from typing import Any, override

import probatio
from seymourlib.client import SeymourClient
from seymourlib.discovery import (
    DEFAULT_DISCOVERY_INTERVAL,
    DiscoveryError,
    TCPTransportCandidate,
    enumerate_tcp_transports,
)
from seymourlib.exceptions import SeymourConnectionError, SeymourError
from seymourlib.protocol import SystemInfo
from seymourlib.transport import ITACH_SERIAL_PORT, URLTransport, tcp_url

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_DEVICE, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    SerialPortSelector,
    TextSelector,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .const import DOMAIN, LOGGER

# Fail fast while the user waits, instead of the library's longer
# retry schedule meant for a running integration.
VALIDATION_TIMEOUT = 5.0
VALIDATION_RETRIES = 1
RESOLVE_TIMEOUT = 5.0


class NotSeymourError(Exception):
    """Raised when the device answered but is not a Seymour controller."""


async def _async_get_system_info(url: str) -> SystemInfo:
    """Connect to the controller at the given URL and read its system info."""
    client = SeymourClient(
        URLTransport(url, connect_timeout=VALIDATION_TIMEOUT),
        max_retries=VALIDATION_RETRIES,
        request_timeout=VALIDATION_TIMEOUT,
    )
    try:
        await client.connect()
        return await client.get_system_info()
    except SeymourConnectionError:
        raise
    except SeymourError as err:
        raise NotSeymourError from err
    finally:
        # The IP2SL allows one connection per port, so always release it
        # before the config entry sets up its own client.
        await client.close()


def _split_tcp_url(url: str) -> tuple[str, int] | None:
    """Return the host and port of a `socket://` URL, if it is one."""
    if not url.startswith("socket://"):
        return None
    host, _, port = url.removeprefix("socket://").rpartition(":")
    if not host or not port.isdigit():
        return None
    return host.strip("[]"), int(port)


async def _async_device_aliases(hass: HomeAssistant, url: str) -> set[str]:
    """Return the URLs the device of a URL goes by.

    The same IP2SL can be entered by name or by address, and the same serial
    port through a /dev/serial/by-id link.
    """
    if tcp := _split_tcp_url(url):
        host, port = tcp
        try:
            async with asyncio.timeout(RESOLVE_TIMEOUT):
                infos = await hass.loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:  # also TimeoutError
            return {url}
        return {url} | {tcp_url(str(info[4][0]), port) for info in infos}
    if url.startswith("/"):
        return {url, await hass.async_add_executor_job(os.path.realpath, url)}
    return {url}


def _lost_its_bridge(entry: ConfigEntry) -> bool:
    """Return whether an entry may have lost its IP2SL to a new address."""
    if not _split_tcp_url(entry.data[CONF_DEVICE]):
        return False
    if entry.state is not ConfigEntryState.LOADED:
        return True
    return not entry.runtime_data.status.last_update_success


async def _async_discover_ip2sl() -> list[TCPTransportCandidate]:
    """Listen for Global Caché IP2SL discovery beacons."""
    try:
        return await enumerate_tcp_transports()
    except (DiscoveryError, OSError) as err:
        LOGGER.debug("IP2SL discovery failed: %s", err)
        return []


class SeymourConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Seymour Masking Screen."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_task: asyncio.Task[list[TCPTransportCandidate]] | None = None
        self._discovered: list[TCPTransportCandidate] = []
        self._released_entry = False

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask how the controller is connected."""
        return self.async_show_menu(step_id="user", menu_options=["serial", "ip2sl"])

    @override
    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Follow a Global Caché bridge to a new address.

        Only bridges that an entry may have lost are asked who they are:
        asking sends bytes to whatever device is on their serial port, and
        a controller that isn't set up yet is added from the user flow.
        """
        url = tcp_url(discovery_info.ip, ITACH_SERIAL_PORT)
        self._async_abort_entries_match({CONF_DEVICE: url})
        if not any(
            _lost_its_bridge(entry)
            for entry in self._async_current_entries(include_ignore=False)
        ):
            return self.async_abort(reason="no_lost_bridge")
        try:
            system_info = await _async_get_system_info(url)
        except SeymourConnectionError:
            return self.async_abort(reason="cannot_connect")
        except NotSeymourError:
            return self.async_abort(reason="not_seymour")
        await self.async_set_unique_id(system_info.serial_number.to_serial_number())
        # The entry of this controller moves to the new address and reloads
        self._abort_if_unique_id_configured(updates={CONF_DEVICE: url})
        return self.async_abort(reason="no_lost_bridge")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask how the controller is connected now."""
        return self.async_show_menu(
            step_id="reconfigure", menu_options=["serial", "ip2sl"]
        )

    @property
    def _current_device(self) -> str | None:
        """Return the device of the entry being reconfigured."""
        if self.source != SOURCE_RECONFIGURE:
            return None
        device: str = self._get_reconfigure_entry().data[CONF_DEVICE]
        return device

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a controller on a serial port or serial proxy."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_DEVICE]
            if self.source != SOURCE_RECONFIGURE:
                await self._async_abort_if_device_configured(url)
            if system_info := await self._async_validate(url, errors):
                return await self._async_create_entry(url, system_info)

        suggested: dict[str, Any] = {}
        if (device := self._current_device) and not _split_tcp_url(device):
            suggested[CONF_DEVICE] = device
        return self.async_show_form(
            step_id="serial",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema({probatio.Required(CONF_DEVICE): SerialPortSelector()}),
                user_input or suggested,
            ),
            errors=errors,
        )

    async def async_step_ip2sl(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Look for Global Caché IP2SL bridges on the network."""
        if self._discovery_task is None:
            self._discovery_task = self.hass.async_create_task(_async_discover_ip2sl())
        elif self._discovery_task.done():
            # IR and contact-closure units can't reach a controller
            self._discovered = [
                candidate
                for candidate in self._discovery_task.result()
                if candidate.has_serial_port is not False
            ]
            return self.async_show_progress_done(next_step_id="ip2sl_host")
        return self.async_show_progress(
            step_id="ip2sl",
            progress_action="discover_ip2sl",
            progress_task=self._discovery_task,
            description_placeholders={
                "seconds": str(round(DEFAULT_DISCOVERY_INTERVAL))
            },
        )

    async def async_step_ip2sl_host(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a controller behind a Global Caché IP2SL bridge."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = tcp_url(user_input[CONF_HOST].strip(), int(user_input[CONF_PORT]))
            if self.source != SOURCE_RECONFIGURE:
                await self._async_abort_if_device_configured(url)
            if system_info := await self._async_validate(url, errors):
                return await self._async_create_entry(url, system_info)

        host_selector: SelectSelector | TextSelector = TextSelector()
        if self._discovered:
            host_selector = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(
                            value=candidate.host,
                            label=f"{candidate.metadata.get('Model', 'IP2SL')} "
                            f"({candidate.host})",
                        )
                        for candidate in self._discovered
                    ],
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        suggested: dict[str, Any] = {CONF_PORT: ITACH_SERIAL_PORT}
        if self._discovered:
            suggested[CONF_HOST] = self._discovered[0].host
        if (device := self._current_device) and (current := _split_tcp_url(device)):
            suggested[CONF_HOST], suggested[CONF_PORT] = current
        return self.async_show_form(
            step_id="ip2sl_host",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_HOST): host_selector,
                        probatio.Required(CONF_PORT): NumberSelector(
                            NumberSelectorConfig(
                                min=1, max=65535, step=1, mode=NumberSelectorMode.BOX
                            )
                        ),
                    }
                ),
                user_input or suggested,
            ),
            errors=errors,
        )

    async def _async_abort_if_device_configured(self, url: str) -> None:
        """Abort if an entry already uses this device, also under another name.

        Validating it would fail anyway: the entry holds the only connection
        the controller allows.
        """
        self._async_abort_entries_match({CONF_DEVICE: url})
        if not (entries := self._async_current_entries(include_ignore=False)):
            return
        aliases = await _async_device_aliases(self.hass, url)
        for entry in entries:
            if aliases & await _async_device_aliases(
                self.hass, entry.data[CONF_DEVICE]
            ):
                raise AbortFlow("already_configured")

    async def _async_validate(
        self, url: str, errors: dict[str, str]
    ) -> SystemInfo | None:
        """Read the system info of the controller, or record an error."""
        await self._async_release_reconfigure_entry()
        system_info: SystemInfo | None = None
        try:
            system_info = await _async_get_system_info(url)
        except SeymourConnectionError:
            errors["base"] = "cannot_connect"
        except NotSeymourError:
            errors["base"] = "not_seymour"
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        finally:
            # Also when the flow is cancelled, so the entry isn't left unloaded
            if system_info is None:
                self._async_restore_reconfigure_entry()
        return system_info

    async def _async_release_reconfigure_entry(self) -> None:
        """Unload the entry being reconfigured, so its connection is free.

        The controller accepts a single connection, which the loaded entry
        holds, also when the new port is another name for the same one.
        """
        if self.source != SOURCE_RECONFIGURE:
            return
        entry = self._get_reconfigure_entry()
        if entry.state is ConfigEntryState.LOADED:
            await self.hass.config_entries.async_unload(entry.entry_id)
            self._released_entry = True

    @callback
    def _async_restore_reconfigure_entry(self) -> None:
        """Load the entry being reconfigured again, if it was unloaded."""
        if self._released_entry:
            self._released_entry = False
            self.hass.config_entries.async_schedule_reload(
                self._get_reconfigure_entry().entry_id
            )

    async def _async_create_entry(
        self, url: str, system_info: SystemInfo
    ) -> ConfigFlowResult:
        """Create the config entry for a validated controller."""
        await self.async_set_unique_id(system_info.serial_number.to_serial_number())
        if self.source == SOURCE_RECONFIGURE:
            if self.unique_id != self._get_reconfigure_entry().unique_id:
                self._async_restore_reconfigure_entry()
                return self.async_abort(reason="wrong_controller")
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data_updates={CONF_DEVICE: url}
            )
        self._abort_if_unique_id_configured(updates={CONF_DEVICE: url})
        model = system_info.screen_model or None
        return self.async_create_entry(
            title=f"Seymour {model}" if model else "Seymour masking screen",
            data={CONF_DEVICE: url},
        )
