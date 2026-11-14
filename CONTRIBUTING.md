# Contributing

Development set-up, tests and code style are in [AGENTS.md](AGENTS.md) ("Commands", "Code Style",
"Tests"). This file covers contributions that need no code.

## Adding a tested device

`apps/editor-gui/src/editor_gui/tested_devices.json` lists the applications someone has proven with
this editor. The device panel shows **Tested** beside a device whose application is listed, and
**Not yet tested** otherwise; MCP clients read the same list with `project_tested_devices`.

Add an entry when you have downloaded and/or read back a real device with the editor and the result
was right. One entry per application (all devices running it share the entry):

```json
{
  "manufacturer": "Siemens",
  "product": "Wall switch interface UP 220/03",
  "order_number": "5WG1 220-2AB03",
  "application": "M-0001_A-2207-10",
  "application_name": "12 S2 On-off-toggle/Dim/Shu/Scene 220710",
  "mask": "MV-0011",
  "proven": ["download", "read_back"],
  "date": "2026-11-13",
  "xets_version": "0.12.0",
  "notes": "Full download four times; each time the read-back matched."
}
```

- `application`: the application id without its hash, `M-xxxx_A-nnnn-vv` (manufacturer, application
  number, version). The device's Manufacturer panel and MCP `project_get_device` show the full id.
- `mask`: as the product data gives it. If the device reports another mask, say so in `notes`.
- `proven`: what you did, one or more of
  - `download`: a download to the real device finished without errors and the device worked as set;
  - `read_back`: what the device holds was read back and matched what the editor expects;
  - `plugin_edit`: a setting changed in a plug-in view (e.g. the Generic Plug-in) reached the device.
- `date`: the day of the proof (YYYY-MM-DD); `xets_version`: the editor version you used.
- `notes`: anything another user should know (a different reported mask, a step that needed a second
  try, what was not proven). Keep it general: no addresses, names or details of your installation.

Run `uv run pytest apps/editor-gui/src/editor_gui/tests/test_tested_devices.py` to check the file.
Already listed? Add the new proof to `proven`, and update `date`, `xets_version` and `notes`.
