# agterm for Omarchy

An unofficial Omarchy bar plugin for [agterm-linux](https://github.com/melonamin/agterm-linux) that shows agent status across open agterm workspaces and lets you focus an existing session. It is not affiliated with or endorsed by the Omarchy or agterm-linux projects.

## Features

- Shows blocked, completed, and active session counts, plus an offline indicator.
- Opens a scrollable list of sessions across open agterm windows and workspaces; page sessions are excluded.
- Shows recent sessions first when usage timestamps are available, with deterministic status and name tie-breaks.
- Focuses an existing session/window; it does not create terminals or agents.
- Middle-click or Enter jumps to the bridge-prioritized session; right-click restarts the monitor.
- Uses agterm events with a 20-second safety snapshot and keeps its usage cache locally.
- Makes no network requests at runtime and does not change agterm or OMP configuration.

## Requirements

- Omarchy with native Quickshell/Quattro plugin support, normally Omarchy 4.x. Older Omarchy compatibility is not claimed.
- Native agterm-linux, with `agtermctl window list --json` working.
- Python 3.10+ available as `python3`.
- Agent status hooks configured in agterm for non-idle statuses. Without hooks, sessions can still appear as idle.
- Git for Git-based installation. Omarchy's plugin tooling also uses `jq`.

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

The popup sorts by recency descending, then active state, status (blocked, completed, active, idle), status-change time descending, and name. The attention action uses the bridge's separate status-first ordering. Page sessions are omitted. When agterm is unavailable, the popup shows the error/offline state and reconnects automatically; when online but no sessions match the filter, it shows “No sessions to display.”

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
- The bridge reads local window, workspace, and agent status data through `agtermctl`. It stores recent-use data in `${XDG_CACHE_HOME:-$HOME/.cache}/omarchy-agterm/usage.json`; event monitoring also takes a safety snapshot every 20 seconds.
- The plugin runtime makes no network requests. GitHub access is used by Git-based installation and updates, not by the running plugin.
- The plugin displays statuses already reported by agterm. For OMP lifecycle status, configure the appropriate hook in agterm. This plugin does not configure, copy, or overwrite OMP extensions or agterm hooks.

## Development

Architecture:

- `manifest.json` — native Omarchy plugin metadata and settings.
- `BarWidget.qml` — native Quickshell `Panel`, popup, display sorting, and interactions.
- `scripts/agterm_bridge.py` — standard-library-only CLI bridge, monitoring, cache, and explicit focus operation.
- `install.sh` — validated manual copy installer.
- `tests/test_bridge.py` — bridge behavior tests.
- `assets/` — upstream SVG, derived light/dark PNG variants, and upstream asset license.

Verification commands:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 -m json.tool manifest.json
bash -n install.sh
omarchy plugin validate .
python3 scripts/agterm_bridge.py --help
```

The Python bridge has no third-party dependencies. See [AGENTS.md](AGENTS.md) for contributor checks and safety constraints.

## License and credits

The plugin source is MIT licensed; see [LICENSE](LICENSE). The SVG and its two derived PNG variants are covered by the upstream MIT license in [`assets/LICENSE`](assets/LICENSE), copyright © 2026 Umputun.

The exact original SVG source is [`agterm-grid-symbolic.svg`](https://github.com/melonamin/agterm-linux/blob/linux-port/agterm-linux/Resources/icons/hicolor/scalable/actions/agterm-grid-symbolic.svg) from [melonamin/agterm-linux](https://github.com/melonamin/agterm-linux), branch `linux-port`, blob SHA `af31e16152691f393a83ab5c026d2f15b3eda705`. The two small PNGs are rasterizations of that SVG with only the fill color adjusted for light/dark bar backgrounds; neither is a screenshot. See the [upstream license](https://github.com/melonamin/agterm-linux/blob/linux-port/LICENSE).
