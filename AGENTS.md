# Contributor instructions

## Project contract

- The native plugin ID is `dmitriiweb.agterm`. Keep `manifest.json` `id`, `BarWidget.qml` `moduleName`, `install.sh` `plugin_id`, and installation/update documentation consistent.
- The implementation is split across `manifest.json`, `BarWidget.qml`, `scripts/agterm_bridge.py`, `install.sh`, `tests/test_bridge.py`, and `assets/`.
- Preserve the native `Panel` widget, Omarchy `setting()` API, and argument-array subprocess invocation patterns.
- Keep the bridge standard-library-only. Focus is limited to selecting existing agterm sessions/windows; do not add session or agent creation behavior.
- Preserve the asset attribution and upstream license in `assets/LICENSE`. Keep `install.sh` and `scripts/agterm_bridge.py` executable.

## Safe verification

Run the existing behavior tests and native manifest validation:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
omarchy plugin validate .
```

Other documented checks:

```bash
python3 -m json.tool manifest.json
bash -n install.sh
python3 scripts/agterm_bridge.py --help
```

For any installer or plugin lifecycle check, isolate both `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` in temporary directories. Never run `install.sh` or enable/remove the plugin against a live desktop for routine checks. Never edit `/usr/share/omarchy`.

`python3 scripts/agterm_bridge.py snapshot` and `watch` read local agterm state and may write usage timestamps to the configured cache. They do not focus a window. The explicit `focus` command changes the selected session/window; do not use it in routine verification against a live desktop.
