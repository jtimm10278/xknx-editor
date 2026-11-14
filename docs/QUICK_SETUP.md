# Quick Setup templates

**Quick Setup…** (in a device's Device panel) applies a ready-made set-up for a common wall-switch
pattern: it sets the device's parameters and links its group objects to the group addresses you pick,
as one undo step. Nothing is sent to the device until you download it.

The button appears only for devices whose application has a template. The editor ships templates in
`apps/editor-gui/src/editor_gui/quick_setup_templates/`; your own go in the `quick_setup` folder of the
editor's settings folder (the same folder as its settings and catalog). A file of your own with the
same application and template id replaces the bundled one.

## Bundled templates

| Device | Application | Templates |
|---|---|---|
| Siemens UP 220/03 | 2207 version 10 | 2 latching inputs: lights on/off + blinds up/down; Push-buttons: toggle lights; Push-buttons: blinds up/down/stop |

## Adding a device or a template

One JSON file per application:

```json
{
  "format": 1,
  "device": {
    "name": "Siemens UP 220/03 (2207-10)",
    "manufacturer": "M-0001",
    "application_number": "2207",
    "application_versions": ["10"]
  },
  "templates": [
    {
      "id": "push-toggle-lights",
      "name": {"en": "Push-buttons: toggle lights", "de": "Taster: Licht umschalten"},
      "description": {"en": "Each press toggles its light."},
      "parameters": [
        {"parameter": "P-543", "value": "0"},
        {"parameter": "P-355", "value": "1"},
        {"parameter": "UP-362", "value": "2"}
      ],
      "slots": [
        {"id": "light_a", "label": {"en": "Light (Input A)"}, "object": 0, "sending": true, "required": true}
      ]
    }
  ]
}
```

- `manufacturer`, `application_number` and `application_versions` come from the application id
  (`M-0001_A-2207-10-E8C8`: manufacturer `M-0001`, number `2207`, version `10`). A template only fits
  the versions it lists, because parameter ids can change between versions.
- `parameters` are applied in order. `parameter` is the parameter id: the ParameterRef id without the
  application prefix and without `_R-…` (MCP `project_list_parameters` shows the ref ids). It is
  resolved at its turn, so a step that switches a mode can show the next parameter under another ref.
  Module parameters keep their module part, e.g. `MD-1_M-100_MI-1_UP-3`. `value` is the raw value
  (an enumeration's value, not its text).
- `slots` are the group objects to link, by object number, looked up after all parameters are set.
  `required: false` lets the user leave one unlinked.
- `name`, `description` and `label` take a text per UI language; English (`en`) is required.

Check a new template on a copy of a project: apply it, look at the Parameters and Group Objects tabs,
and run **Simulate Download** before any real download. MCP clients can list and apply templates with
`project_quick_setup_templates` and `project_quick_setup_apply` (or the `quick_setup` op of
`project_batch`).
