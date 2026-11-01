# Hardware test plan

The unit tests prove the integration and seymourlib behave as designed
against simulated controllers. They can't prove the design matches the real
controller, the real IP2SL, or the gaps in the RS-232 documentation. This plan
does that, adversarially, with an IP2SL connection only (no direct serial
port needed).

Run it after any seymourlib or integration release that touches I/O, after
a controller or IP2SL firmware change, and once before relying on the screen
for months unattended.

## Tools

All tools are in this directory and need only Python 3.11+, so they also run
on the Home Assistant host (for example from the Terminal & SSH add-on).

| Tool | Use |
| --- | --- |
| [`raw_console.py`](raw_console.py) | Raw frames to the controller, every byte timestamped: `send`, `bench` (latency), `watch` (status after a command), `second-connection`, `churn`, `interactive`. **Disable the Home Assistant entry first**: the IP2SL allows one connection. |
| [`fault_proxy.py`](fault_proxy.py) | A TCP proxy between Home Assistant and the IP2SL that injects faults on command: latency, blackhole, refused connections, RST/FIN, stalls, bit flips, injected bytes, a delayed reply, split or duplicated chunks, CR/LF. Reconfigure the entry to the proxy (`<proxy host>:14999`) and drive it with `nc 127.0.0.1 14998`. |
| [`soak_monitor.py`](soak_monitor.py) | Samples entity states through the REST API and counts connections to the IP2SL from `/proc/net/tcp`; summarizes availability, longest outage, and most connections. |
| [`soak_exerciser.yaml`](soak_exerciser.yaml) | A Home Assistant package that sends a random command every 10 minutes and a random preset every hour, and logs outages. |
| `seymourctl` | seymourlib's CLI. `seymourctl --host <ip2sl> ip2sl serial [--fix]` reads or fixes the bridge's serial settings. |

Also useful: `tcpdump -i any -w seymour.pcap port 4999` on the Home Assistant
host, and debug logging for the integration (its page, **Enable debug
logging**), which logs every frame.

## Pass criteria for every test

- The entry never ends up in **Failed to set up** (`setup_error`); only
  **Retrying setup** is acceptable, and it recovers by itself.
- Never more than one connection to the IP2SL (`soak_monitor.py` or
  `ss -tn dport = :4999` on the host).
- Within two polls of a fault clearing, entities are available again and
  commands work, without a reload.
- No tracebacks in the log, and no `Unexpected error` from the coordinator.
- During a long outage: at most a few log lines per hour, and polls back off
  to once a minute.
- No command lost silently: a command either works, or its service call
  fails with an error.
- When healthy, a stop reaches the controller (status `H`) within 1 second
  at the 99th percentile.

Record each result as **pass**, **fail**, or **finding**. A finding is
hardware behavior that differs from what the code assumes. Each test below
names the assumption it checks and what to change if it doesn't hold.

## A. The controller and its protocol

Use `raw_console.py` with the Home Assistant entry disabled.

| # | Test | Assumption it checks | If it doesn't hold |
| --- | --- | --- | --- |
| A1 | `bench '[01S]' --count 200`, then `[01P]`, `[01Y]`, `[01R]`, at rest and while the masks move. Note latency (median, p99, max), every distinct reply, and any **bytes after the frame**. | Answers come well within `DEFAULT_REQUEST_TIMEOUT` (10 s). Frames end at `]`; CR/LF after them is tolerated and logged at debug level only. | Raise or lower the request timeout. Anything other than whitespace after frames: add it to `receive()`. |
| A2 | `send '[01HA]' --listen 5` for every command (`O I M A H C U X`) with every motor ID (`T B L R V H A`), **including IDs your screen doesn't have**. | **Commands get no reply at all.** seymourlib confirms each command with a `[01S]` on the same connection and reads the first frame that comes back. | If commands are acknowledged, the confirmation reads the acknowledgment instead of the status, and a late acknowledgment shows up as `Drained ... stale bytes` warnings. Read the acknowledgment explicitly in `client._attempt`. |
| A3 | `watch '[01AA]' --seconds 60` from a position away from home; likewise `[01OA]`, `[01IA]`, `[01M235]`, `[01CA]`, `[01OTJ]`. Note how long the status takes to leave `P`/`H`, and its whole sequence. | The status shows motion within `HOMING_START_TIMEOUT` (5 s) and `MOTION_GRACE` (10 s); seymourlib's CLI waits 2 s. | Raise those constants in `const.py`. |
| A4 | After each move in A3 ends, and after a halt in the middle of each: which status remains? (`H`, `P`+ratio, `P` alone?) After a jog away from a preset, does the status still say `P` with that preset? | A preset is current only while the status is `P` with its number; selecting that preset then does nothing. | If a jog leaves `P235`, selecting 2.35 after fine-tuning won't move the masks back: compare positions with the preset in `select._async_move_to_preset`. |
| A5 | Positions (`[01P]`) fully retracted, fully extended, past either end, while moving, uncalibrated, and right after power-up before homing. | 4-character fields such as `00.0`, `100.`, `-10.`; a value that doesn't parse raises `SeymourProtocolError`; covers clamp to 0–100. | A recurring format the parser rejects (spaces, `----`): teach `decode_positions` to read it, for example as unknown. |
| A6 | By eye: is 0 % retracted? Does `O` retract the masks (cover **open**)? Do left/right and top/bottom report alike? | 0 % is home (retracted), `O` opens. | Swap the mapping in `cover.py` and `masks.is_at_home`. |
| A7 | `[01M235]` from a position away from home, and from another preset. Does the controller home first by itself, and end at the same positions as after homing? | Presets must be approached from home, so the integration homes first. | If the controller handles it, drop the homing step: preset moves get much faster. |
| A8 | Bad input, then `[01S]`: `[01M555]`, `[01M990]` (unused slot), `[01M000]`, `[01Z]`, `[02S]`, `[01s]`, `[01S`, a bare `]`, `[01SS]`, a 2 KB frame, `\x00\xff` noise. | The controller ignores what it doesn't understand, or answers `E`, and keeps answering afterwards. | If it locks up, find what clears it (power cycle?) and add it to the troubleshooting docs. |
| A9 | During each kind of motion: `S`/`P` latency; `H` (does it stop at once?); `O` during `I`; `M240` during `M235`; `A` during `C`; `U` and `R` while moving. | A newer command takes over, which is how the integration treats them. A halt always stops. | If a halt can be ignored in some state, the stop button needs a check-and-retry. |
| A10 | Pipelining: `send '[01S][01P][01S]'` in one write; then 10 `[01S]` with `--gap 0`. All answered, in order? | One request at a time is always safe. Pipelining isn't used, but a late reply from a timed-out request has to be tolerated. | If the controller drops frames that arrive while it's busy, add a minimum gap between requests in seymourlib. |
| A11 | Fragments: `send '[01' --listen 5`, then `send 'S]'`; then `[01` followed by a whole `[01S]`. | The controller resynchronizes on `[`. | If a fragment wedges it, seymourlib must never leave half-written frames behind (it doesn't today), and the docs should say how to clear it. |
| A12 | On a **scratch preset** (an unused custom slot): `[01U990]`, then `[01R]` at once, after 1 s, and after 5 s; power-cycle and `[01R]` again; then `[01X990]`. | Saving answers within the request timeout and persists. Whether a saved custom slot gets a height > 0 decides whether it then shows in the select. | If `R` right after `U` fails or is stale, delay the metadata refresh after saving. If the saved slot keeps height 0, list it in the select by another test. |
| A13 | Calibrate (`[01CA]`): how long, which statuses, does the controller answer during it, and does it change presets or positions? | Calibration is long, rare, and can be cancelled by a halt. The buttons are disabled by default. | If calibration can't be stopped, warn in the button's documentation. |
| A14 | `[01@D00]`, `[01@D10]`, `[01@D20]`: total size, how many `]`, how long until the line goes quiet. | A response ends after 0.5 s of silence (`DIAGNOSTICS_IDLE_TIMEOUT`). | Raise the timeout if gaps inside a response are longer. |
| A15 | The `E` status: try a halt during calibration, or a power cut during a move. What clears it? | An error clears with a homing or a calibration. | Add the recovery to the troubleshooting docs, or to the integration. |
| A16 | The IR remote, the 12 V trigger, and the front panel, while Home Assistant polls, and during one of its commands or preset moves. | Status changes show up within a poll; there are no conflicts beyond "newest command wins". | Document anything else. |
| A17 | `[01ITJ]` 50 times, then `[01ITM]` 50 times, uncalibrated and calibrated; compare the position change with what's expected. | A jog is the smallest step; `M` moves 1 % when calibrated and nothing otherwise. Neither is ever repeated by seymourlib. | Correct the service descriptions. |
| A18 | `[01Y]` before and after a power cycle: serial number, model padding, order of mask IDs. | They're stable; the entities are keyed to the serial number the entry was set up with. | — |

## B. The IP2SL and the network

| # | Test | Expected (after the fixes) |
| --- | --- | --- |
| B1 | `raw_console.py HOST second-connection`. | Find out: refused, accepted and ignored, replaces the first, or both answered. This decides how much damage a leaked connection could do; the integration is built to never leak one. |
| B2 | `raw_console.py HOST churn --count 1000 --interval 0`. | Every cycle answers. Note how soon a closed connection's slot is free again. |
| B3 | **A stale connection.** With the integration connected, cut the Home Assistant host off without closing the connection: `iptables -A OUTPUT -p tcp --dport 4999 -j DROP` (or pull its cable), then hard-reboot the host or kill Home Assistant, and restore the network. How long until a new connection works? | Find out. If it never does until the IP2SL is power-cycled, the repair **doesn't respond** (or **unreachable**) appears after 15 minutes and says to power-cycle it. Consider powering the IP2SL from a switchable outlet or PoE port. |
| B4 | `churn --count 10000 --interval 0.05`, checking the IP2SL's web page now and then. | The IP2SL stays healthy. The integration now reconnects at most every few minutes during long outages, and not on every failed poll. |
| B5 | Hold a connection open and idle for 1 h and for 24 h (`raw_console.py HOST interactive`, then type `[01S]`). | It still answers. The integration is never idle that long (it polls at least once a minute, and seymourlib enables TCP keepalive), but an IP2SL that drops idle connections would explain unexplained reconnects. |
| B6 | Power-cycle the IP2SL while the integration is idle, while masks move, and while a preset move runs. Press stop in the middle of it. | Entities go unavailable and recover by themselves within a minute of the bridge being back. Commands during the gap **fail with an error** instead of silently doing nothing. |
| B7 | `seymourctl --host HOST ip2sl serial`; then set 9600 baud on the IP2SL's web page. | After three polls without an answer (a few minutes, with their timeouts) Home Assistant raises the fixable repair **The IP2SL has the wrong serial settings**; submitting it restores 115200 and the controller answers again. |
| B8 | Change the IP2SL's address (a new DHCP reservation, then reboot it). | Once Home Assistant sees the bridge's DHCP request (its MAC starts with 00:0C:1E), the entry moves to the new address and reloads by itself. |
| B9 | Through `fault_proxy.py` (see below): every fault, one at a time, then combinations. | See the fault table below. |
| B10 | Controller off, IP2SL on, overnight (the screen's power switched with the projector). | Polls back off to once a minute; the log gets a handful of lines per outage; **doesn't respond** appears after 15 minutes and goes away when the controller is back; the first command after power-on works. |
| B11 | Cut the controller's power during a move, and during calibration. | As B10; afterwards see A15. |
| B12 | Unplug and replug the RS-232 cable while idle and while masks move. | As B10. |
| B13 | Run `seymourctl --host HOST status` while Home Assistant is connected. | Depends on B1. If the IP2SL replaces the old connection, Home Assistant gets it back within a few polls. |

### Faults through the proxy (B9)

Start the proxy on the Home Assistant host, or any host that both can reach,
and reconfigure the entry to it:

```shell
python3 fault_proxy.py --upstream <ip2sl>:4999 --listen 0.0.0.0:14999 --verbose
nc 127.0.0.1 14998   # then type the commands below
```

| Fault | Commands | Expected |
| --- | --- | --- |
| Short blackhole | `blackhole on`, wait 5 s, `blackhole off` | At most one failed poll; no reconnect. |
| Long blackhole | `blackhole on` for 30 s, 5 min, 1 h | Entities unavailable; polls back off to 1/min; **doesn't respond** after 15 min; recovery within a minute of `blackhole off`. Commands fail with an error during the outage. |
| Connection reset | `reset` | The next request reconnects in its own retries and succeeds. |
| Close | `close` | Same as a reset. |
| Bridge gone | `drop-upstream`, then `refuse on` for 5 min, then `clear` | **Unreachable** after 15 min; recovery afterwards. |
| Slow network | `latency 500 100`, then `latency 4000`, then `latency 6000` (applied each way) | Works while a round trip stays under the request timeout (10 s); beyond it, as a blackhole. |
| One late reply | `delay-next 11` | The request times out and is retried; the stray reply is drained before a later request (`Drained ... stale bytes` warning); no wrong data reaches an entity. |
| Line noise | `corrupt 0.05` for an hour | Occasional failed polls, never a traceback; entities recover at the next poll. |
| Junk frames | `inject [01P1--.]`, `inject \x00\xff`, `inject ]` | Discarded or drained; at most one failed poll. |
| Fragmentation | `split on` for 10 min | No errors. |
| Duplicates | `duplicate on` for 10 min | `Drained` warnings, no wrong states, no errors. |
| CR/LF frame ends | `crlf on` for 10 min | No warnings at all (debug-level lines only). |
| Stalled reads | `stall on` for 30 s | Requests time out; recovery after `stall off`. |

## C. The integration from end to end

| # | Test | Expected |
| --- | --- | --- |
| C1 | Open, close, and stop every cover, including the groups. | Each cover shows opening/closing only while **its** masks move; idle masks stay open/closed. Positions stay within 0–100. |
| C2 | `move_mask` jog and percent in both directions, 100 times each. | The total change matches 100 steps; never a doubled step. |
| C3 | Select each preset from home, from another preset, from the middle of a move, while moving, and while already there; 200 times. | No `homing incomplete` or `homing timeout` errors. Selecting the current preset doesn't move the masks. |
| C4 | `save_ratio` / `reset_ratio` on a scratch preset, by label and by number (`990`). | The select's options and labels update within one metadata refresh. |
| C5 | The home and calibrate buttons of every target (enable the calibrate buttons first). | They work; a running preset move stops. |
| C6 | Press stop at random moments: during moves, homing, and preset moves; while another cover's command is still running; under the soak exerciser. | The stop is sent within a second; a preset move in progress ends with **stopped, because another command came in**. |
| C7 | 20 mixed commands within 2 seconds from an automation. | The last command wins; no tracebacks; no `Drained` warnings. |
| C8 | Select preset A, then preset B one second later; open a cover during a preset move's homing. | B wins and A's call fails with the "stopped" message; the cover command wins over the preset move. |
| C9 | Reload, disable/enable, reconfigure, and delete the entry while idle, during a preset move, during a proxy blackhole, and while a request waits to retry. | Afterwards exactly one connection (or none, for disable/delete); the entry is loaded or retrying, never failed. |
| C10 | Restart Home Assistant during a move. Then hard power-off the Home Assistant host and boot it again. | The restart reconnects at once. The hard power-off is B3: either it reconnects, or the repair says to power-cycle the IP2SL. |
| C11 | The config flow: several Global Caché units on the network (including an IR-only one), a wrong host, a host name versus an address, a controller already set up under another name, reconfiguring to the same and to another IP2SL, and closing the dialog while it validates. | IR-only units aren't offered; a controller set up under another name aborts with **already configured**; a cancelled reconfigure leaves the entry loaded. |
| C12 | Download diagnostics during an outage and during a move. | It downloads, with the serial number removed. |

## D. Soak

| # | Test | Expected |
| --- | --- | --- |
| D1 | 14–30 days with the soak exerciser, `soak_monitor.py` on the Home Assistant host, and a random proxy fault every 1–6 hours (for example from a cron job: `echo "blackhole on" \| nc -q1 127.0.0.1 14998; sleep 600; echo clear \| nc -q1 127.0.0.1 14998`). | Availability is 100 % outside the injected faults; never more than one connection; Home Assistant's memory and open files don't grow; command failures only during faults. |
| D2 | As D1, with the controller's power switched off 20 hours a day. | As B10, every day, with no drift. |

## Order

Start with what decides the most: **B1, B3, A2, A3**, then A1 and A5, B2,
C9, C3, C6, and the rest. Record findings as issues, with the
`raw_console.py` log or the pcap attached: real traffic is the best material
for new regression tests.
