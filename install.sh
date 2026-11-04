#!/usr/bin/env bash
set -euo pipefail

plugin_id="dmitriiweb.agterm"
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
dest="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/$plugin_id"

if ! command -v omarchy >/dev/null 2>&1; then
  echo "Omarchy CLI not found. Run this on Omarchy Linux." >&2
  exit 1
fi
if [[ -e "$dest" ]]; then
  echo "Refusing to overwrite existing plugin: $dest" >&2
  echo "Back it up or remove it yourself before reinstalling." >&2
  exit 1
fi

mkdir -p -- "$dest/scripts" "$dest/assets"
cp -- "$source_dir/manifest.json" "$source_dir/BarWidget.qml" "$source_dir/LICENSE" "$dest/"
cp -- "$source_dir/scripts/agterm_bridge.py" "$dest/scripts/"
cp -- "$source_dir/assets/agterm-grid-symbolic.svg" "$source_dir/assets/agterm-grid-on-dark.png" "$source_dir/assets/agterm-grid-on-light.png" "$source_dir/assets/LICENSE" "$dest/assets/"
chmod +x -- "$dest/scripts/agterm_bridge.py"

if ! omarchy plugin validate "$dest"; then
  echo "Validation failed. The plugin remains at $dest for inspection." >&2
  exit 1
fi
omarchy-shell shell rescanPlugins
omarchy plugin enable "$plugin_id"
omarchy bar move "$plugin_id" --after omarchy.agents || true
printf '
Installed %s in %s
' "$plugin_id" "$dest"
echo "If no sessions show up, ensure agtermctl works and Agent Status Hooks are enabled in agterm."
