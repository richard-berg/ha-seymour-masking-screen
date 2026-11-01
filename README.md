# Seymour Masking Screen for Home Assistant

[![Validate](https://github.com/richard-berg/ha-seymour-masking-screen/actions/workflows/validate.yml/badge.svg)](https://github.com/richard-berg/ha-seymour-masking-screen/actions/workflows/validate.yml)
[![Core parity](https://github.com/richard-berg/ha-seymour-masking-screen/actions/workflows/core-parity.yml/badge.svg)](https://github.com/richard-berg/ha-seymour-masking-screen/actions/workflows/core-parity.yml)

Control [Seymour-Screen Excellence](https://www.seymourscreenexcellence.com/)
motorized masking screens from Home Assistant. Move the masks to an aspect
ratio preset when a movie starts, open and close masks or pairs of masks, and
see what the screen is doing.

The integration talks to the screen's controller over its RS-232 port, either
directly through a serial adapter or ESPHome serial proxy, or over the
network through a [Global Caché iTach IP2SL](https://www.globalcache.com/products/itach/ip2slspecs/).
It uses the [`seymourlib`](https://github.com/richard-berg/seymourlib) library.

## Supported devices

- Seymour-Screen Excellence masking screens with a motor controller that has
  an RS-232 control port, with up to four masks (top, bottom, left, right).
- Ways to connect the controller:
  - A USB serial adapter, or a serial port of the machine running Home
    Assistant.
  - An [ESPHome serial proxy](https://esphome.io/components/serial_proxy/).
  - A Global Caché iTach IP2SL, or another bridge that passes raw serial data
    over TCP.

## Installation

Requires Home Assistant 2026.11 or newer.

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=richard-berg&repository=ha-seymour-masking-screen&category=integration)

1. Select the badge above to open this repository in the Marketplace of your
   Home Assistant, and select **Install**. Once the integration is in the
   Marketplace catalog, you can also find it by searching **Settings** >
   **Marketplace** for **Seymour Masking Screen**.
2. Go to **Settings** > **Devices & services**, select **Add integration**,
   and choose **Seymour Masking Screen**.
3. Choose how the controller is connected:
   - **Serial port or serial proxy**: select the port the controller is
     connected to. You can also enter a [serialx URL](https://puddly.github.io/serialx/)
     yourself, like `socket://192.168.1.70:4999`.
   - **Network, through a Global Caché IP2SL**: Home Assistant listens for
     IP2SL bridges on your network for about 12 seconds. Choose a bridge it
     found, or enter its address.

Home Assistant then reads the controller's model and serial number, and adds
the screen as a device.

### Configuration parameters

| Parameter | Description |
| --- | --- |
| Serial port | The serial port or serial proxy the controller is connected to. |
| Host | The IP address or hostname of the IP2SL. |
| Port | The TCP port of the IP2SL that carries the raw serial data, usually `4999`. Port `4998` is the IP2SL's command port and does not work. |

To change the port or address later, open the integration's menu and select
**Reconfigure**. The integration has no other options.

## What you can do with it

### Entities

| Entity | Description |
| --- | --- |
| **Top mask**, **Bottom mask**, **Left mask**, **Right mask** covers | Open (retract), close (extend), and stop each mask, and see its position. |
| **Vertical masks** and **Horizontal masks** covers | Move a pair of masks together. Only on screens that have both masks of the pair and other masks too. |
| **All masks** cover | Move all masks together. Groups report the average position of their masks. |
| **Aspect ratio** select | Move the masks to a ratio preset stored on the controller, like `2.35:1 (235)`. Shows the preset the screen is at, if any. |
| **Home** and **Calibrate** buttons | Return masks to their home position, or calibrate them. The buttons for single masks are disabled by default when there are groups. |
| **Status** sensor | What the screen is doing: at a ratio preset, moving, homing, calibrating, stopped, or an error. |
| **Screen width** and **Screen height** sensors | The size of the visible screen area, in inches. |
| **Last contact** sensor | When the controller last answered. Disabled by default. |

Moving to a ratio preset starts from the home position. If the masks are not
at home, the integration homes them first, and then moves to the preset.
Selecting the preset the screen is already at does nothing. Any newer command
for the masks, like a stop or another preset, takes over from a move to a
preset that is still in progress.

### Actions

| Action | Target | Description |
| --- | --- | --- |
| `seymour_masking_screen.move_mask` | A mask cover | Move the masks a small step, to fine-tune them. `direction` is `open` or `close`; `distance` is `jog` (the smallest step) or `percent` (1% of the range, needs a calibrated screen). |
| `seymour_masking_screen.save_ratio` | The aspect ratio select | Store the current mask positions in a ratio preset. `option` is the preset, as shown in the select. |
| `seymour_masking_screen.reset_ratio` | The aspect ratio select | Restore the factory positions of a ratio preset. `option` is the preset, as shown in the select. |

### Examples

Match the masks to the aspect ratio of what is playing, using a media player
that reports it in an attribute:

```yaml
automation:
  - alias: "Mask the screen for the movie"
    triggers:
      - trigger: state
        entity_id: media_player.theater
        to: playing
    conditions:
      - condition: template
        value_template: "{{ state_attr('media_player.theater', 'aspect_ratio') | float(0) > 2.3 }}"
    actions:
      - action: select.select_option
        target:
          entity_id: select.seymour_prh_123_aspect_ratio
        data:
          option: "2.35:1 (235)"
```

Open all masks when the projector turns off:

```yaml
automation:
  - alias: "Open the masks when the projector turns off"
    triggers:
      - trigger: state
        entity_id: media_player.projector
        to: "off"
    actions:
      - action: cover.open_cover
        target:
          entity_id: cover.seymour_prh_123_all_masks
```

Fine-tune the top mask by 1%:

```yaml
action: seymour_masking_screen.move_mask
target:
  entity_id: cover.seymour_prh_123_top_mask
data:
  direction: close
  distance: percent
```

### Use cases

- Switch between 16:9 and scope presets automatically from the media player,
  a remote, or a scene.
- Add the masks to a "movie time" scene with the lights and projector.
- Store adjusted presets after fine-tuning the masks, without the controller's
  own remote.

## How data is updated

The integration polls the controller for its status and mask positions every
5 seconds, and every second while masks are moving. It reads the ratio presets
and the screen's dimensions every 5 minutes, and right after a preset is saved
or reset.

While the controller doesn't answer, for example because the screen is
switched off with the projector, polls slow down to once a minute. Commands
are still sent right away, and the first one that succeeds resumes normal
polling. A repair is raised once the controller has been out of reach for 15
minutes, and goes away when it answers again.

## Known limitations

- Each entry controls one controller. Add an entry for each screen.
- Only one connection to the controller is possible. While Home Assistant is
  connected, other software can't use the same serial port or IP2SL port.
- The controller can't be discovered automatically: a serial adapter or an
  IP2SL doesn't tell what is connected to it. The setup does list IP2SL
  bridges it finds.
- Unused custom preset slots of the controller don't show in the aspect ratio
  select.
- The controller's diagnostic file listings are not supported.

## Troubleshooting

- **"No response from a Seymour controller" during setup**: check that you
  chose the port the controller is connected to, and that it is powered on.
  For an IP2SL, use the serial data port, usually `4999`, and check that the
  IP2SL's serial port is set to the controller's speed of 115200 baud.
- **"Doesn't look like a Seymour controller"**: the port answered, but not
  like a Seymour controller. Check that you chose the right port.
- **A repair says the screen is unreachable**: Home Assistant couldn't
  connect for 15 minutes. It keeps trying, and the repair goes away
  once the controller answers. Check the power and cabling, and the IP2SL's
  network connection. If the controller moved to another port or address,
  reconfigure the integration.
- **A repair says the screen doesn't respond**: Home Assistant can connect,
  but the controller hasn't answered for 15 minutes. Check that the controller is on and its
  cable is plugged in. With an IP2SL, check that its serial port is set to
  115200 baud, no flow control, and no parity; a factory reset changes it to
  9600 baud. If Home Assistant lost power or its network, power-cycle the
  IP2SL: it can keep holding the old connection, and it allows only one.
- **The serial port is busy**: another program, or another integration, has
  the port open. Only one program can use it at a time.
- **Debug logs**: go to the integration's page and select **Enable debug
  logging**. Reproduce the problem, then disable it to download the log. The
  integration's diagnostics download has the status, ratio presets, and
  connection details, with the serial number removed.

## Removal

1. Go to **Settings** > **Devices & services** and select **Seymour Masking
   Screen**.
2. Open the menu of the entry and select **Delete**.
3. To remove the files too, uninstall the integration in **Settings** >
   **Marketplace**.

## Development

This integration follows the rules of the
[integration quality scale](https://contributors.home-assistant.io/core/quality-scale/)
up to the platinum tier, as listed in
[`quality_scale.yaml`](custom_components/seymour_masking_screen/quality_scale.yaml).
The quality scale has no tiers for custom integrations, so the
[Core parity](.github/workflows/core-parity.yml) workflow checks it with the
tooling of Home Assistant Core itself: it copies the integration and its tests
into a Core checkout and runs hassfest with the tier, ruff, mypy, pylint, and
the tests with full coverage, against the newest supported release and the
development branch.

To run the same checks locally, set up a
[Core development environment](https://contributors.home-assistant.io/core/development/environment/)
in a throwaway checkout, then run from its virtual environment:

```shell
python script/core_parity.py /path/to/core
```

`strings.json` holds the English text. After changing it, run the checks and
copy the generated `translations/en.json` they point you to.
