# agterm for Omarchy

An unofficial Omarchy bar plugin for [agterm-linux](https://github.com/melonamin/agterm-linux) that shows agent status across open agterm workspaces and lets you focus an existing session. It is not affiliated with or endorsed by the Omarchy or agterm-linux projects.

## Features

- Shows blocked, completed, and active session counts, plus an offline indicator.
- Opens a scrollable list of sessions across open agterm windows and workspaces; page sessions are excluded.
- Shows observed recent sessions first when usage timestamps are available, with deterministic status and name tie-breaks.
- Focuses an existing session/window; it does not create terminals or agents.
- Middle-click or Enter uses status-first attention ordering; right-click restarts the monitor.
- Uses supported agterm events with a 20-second safety snapshot and keeps its usage cache locally.
- Makes no network requests at runtime. The plugin and installer do not modify agterm or OMP configuration.

## Requirements

- Omarchy with native Quickshell/Quattro plugin support, normally Omarchy 4.x. Older Omarchy compatibility is not claimed.
- Native agterm-linux, with `agtermctl window list --json` working.
- Python 3.10+ available as `python3`.
- Git for Git-based installation. Omarchy's plugin tooling also uses `jq`.

### Optional OMP status reporting

OMP needs its own extension to report agent lifecycle status to agterm. This adapter is opt-in and is not installed or enabled by the plugin installer:

```bash
install -D -m 0644 integrations/omp-agterm-status.ts \
  "${HOME}/.omp/agent/extensions/agterm-status.ts"
```

The adapter invokes `~/.config/agterm/agent-status/agterm-agent-status.sh` using OMP's argument-array process API. It sets `AGTERMCTL` to `AGTERM_STATUS_CLI` or `agtermctl` on `PATH`, overriding any stale CLI path baked into an older wrapper. Set `AGTERM_STATUS_COMMAND` or `AGTERM_STATUS_CLI` in the OMP launch environment when the defaults do not match your installation. Restart OMP after installing or changing the extension.

Only the root OMP agent reports. Agent work reports active; explicit approval and `ask` waits report blocked; resolution resumes active; final settlement reports completed; shutdown clears to idle. Ordinary tool calls do not mean blocked. Agent workers and OMP sessions outside agterm are left untouched. Status reporting errors are suppressed so they cannot fail an agent turn.

Pi integrations use Pi's own extension-loading directory and API; this adapter is specifically for OMP. Do not install it into Pi's extension directory. The adapter does not repair or replace other agent integrations.

## Install with Omarchy

```bash
omarchy plugin add https://github.com/dmitriiweb/omarchy-agterm.git --enable
```

Review and trust the plugin before accepting Omarchy's confirmation prompt. Plugin code runs with your user permissions. For deliberate noninteractive automation, add `--yes`.

To place it immediately after the agents widget, when that reference widget is installed:

```bash
omarchy bar move dmitriiweb.agterm --after omarchy.agents
```

### Migrate an existing `local.agterm` install

If `local.agterm` is already installed, disable it first, then run the Git installation command above:

```bash
omarchy plugin disable local.agterm
omarchy plugin add https://github.com/dmitriiweb/omarchy-agterm.git --enable
```

The old plugin directory remains as a disabled backup. Settings are not migrated automatically; reapply `cliPath`, `showIdle`, and `showIcon` to `dmitriiweb.agterm` as needed.

### Manual copy installation

```bash
git clone https://github.com/dmitriiweb/omarchy-agterm.git
cd omarchy-agterm
./install.sh
```

The installer targets `${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/dmitriiweb.agterm`, refuses to overwrite an existing directory, validates the manifest, rescans plugins, and enables the plugin. If validation fails, the copied files remain at the destination for inspection. It attempts to place the plugin after `omarchy.agents`; that placement only applies when the reference widget exists.

This copy workflow has no Git-based `omarchy plugin update` path. To update it, back up or move the existing destination directory before rerunning the installer. The installer will not overwrite it.

## Configuration

Configure these values in Omarchy's bar plugin settings:

| Setting | Default | Behavior |
| --- | --- | --- |
| `cliPath` | empty | Finds `agtermctl` on `PATH`, then checks standard install paths. An explicit path expands `~` and environment variables; for example `/home/your-user/.local/bin/agtermctl`. |
| `showIdle` | `true` | Includes idle sessions in the popup. It filters only the popup list, not bridge data or bar counts. |
| `showIcon` | `true` | Shows the icon on horizontal bars. Vertical bars always show it. |

## Usage

- Left-click the bar widget to toggle the popup.
- Click a popup row to focus that existing session and its window.
- Middle-click the bar widget or press Enter in the popup to focus the first bridge-prioritized session: blocked, completed, active, then idle.
- Right-click the bar widget to restart the monitor.

The popup sorts by recency descending, then selected state, status (blocked, completed, active, idle), status-change time descending, and name. The attention action uses the bridge's separate status-first ordering. Page sessions are omitted. Usage is recorded after successful plugin focus; external selection is noticed only when a snapshot observes the changed selected session, so rapid selection changes between snapshots cannot be reconstructed.

## Updates and removal

For Git installs:

```bash
omarchy plugin update dmitriiweb.agterm
omarchy plugin remove dmitriiweb.agterm
```

Both commands ask for confirmation; add `--yes` only for deliberate automation. Removal deletes the installed Git checkout, including local edits. Plugin changes are rescanned/hot-reloaded; a shell restart is not required.

## Troubleshooting and privacy

- Check the native client directly: `agtermctl window list --json`.
- Validate an installed copy: `omarchy plugin validate <installed-path>`.
- From a repository checkout, inspect a snapshot with `python3 scripts/agterm_bridge.py snapshot`.
- The bridge reads local window, workspace, and agent status data through `agtermctl`. It stores usage timestamps in `${XDG_CACHE_HOME:-$HOME/.cache}/omarchy-agterm/usage.json`; event monitoring also takes a safety snapshot every 20 seconds. Snapshot/data errors and event-monitor errors are reported separately; partial tree failures preserve rows from readable windows.
- The plugin runtime makes no network requests. GitHub access is used by Git-based installation and updates, not by the running plugin.
- The plugin displays statuses already reported by agterm. OMP status reporting is an explicit manual extension install described above. The plugin and installer never copy extensions, enable hooks, or alter OMP configuration automatically.

## Development

Architecture:

- `manifest.json` — native Omarchy plugin metadata and settings.
- `BarWidget.qml` — native Quickshell `Panel`, popup, display sorting, and interactions.
- `scripts/agterm_bridge.py` — standard-library-only CLI bridge, monitoring, cache, and explicit focus operation.
- `install.sh` — validated manual copy installer.
- `tests/test_bridge.py` — bridge data-boundary, ordering, and recency tests.
- `tests/test_bridge_process.py` — isolated fake-CLI focus, watch/recovery, and coordinated cache-writer tests.
- `tests/test_adapter.mjs` and `tests/test_adapter.py` — isolated adapter lifecycle tests, included in unittest discovery.
- `integrations/omp-agterm-status.ts` — optional, manually installed OMP lifecycle adapter; separate from plugin installation.
- `assets/` — upstream SVG, derived light/dark PNG variants, and upstream asset license.

Verification commands:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 -m json.tool manifest.json
bash -n install.sh
omarchy plugin validate .
python3 scripts/agterm_bridge.py --help
```

The Python bridge has no third-party dependencies. The adapter tests require Node.js 18+; unittest discovery explicitly skips that group if `node` is unavailable. Run them separately with `node --test tests/test_adapter.mjs`.

Process tests use temporary configuration/cache directories and a fake `agtermctl` executable. Adapter tests execute the real adapter with a simulated OMP event context and controlled timers; they do not prove that an installed OMP runtime emits those events. These tests do not focus live windows, set live agent status, or install/enable plugins. Native popup interaction and real OMP-to-agterm lifecycle acceptance still require an isolated desktop/agterm instance.

See [AGENTS.md](AGENTS.md) for contributor checks and safety constraints.

## License and credits

The plugin source is MIT licensed; see [LICENSE](LICENSE). The SVG and its two derived PNG variants are covered by the upstream MIT license in [`assets/LICENSE`](assets/LICENSE), copyright © 2026 Umputun.

The exact original SVG source is [`agterm-grid-symbolic.svg`](https://github.com/melonamin/agterm-linux/blob/linux-port/agterm-linux/Resources/icons/hicolor/scalable/actions/agterm-grid-symbolic.svg) from [melonamin/agterm-linux](https://github.com/melonamin/agterm-linux), branch `linux-port`, blob SHA `af31e16152691f393a83ab5c026d2f15b3eda705`. The two small PNGs are rasterizations of that SVG with only the fill color adjusted for light/dark bar backgrounds; neither is a screenshot. See the [upstream license](https://github.com/melonamin/agterm-linux/blob/linux-port/LICENSE).
