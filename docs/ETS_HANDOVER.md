# Hand Over to ETS

**File > Hand Over to ETS…** makes the files you need to bring an ETS project up to date with the
project you edited in XKNX Editor. You make the changes in ETS yourself, so ETS keeps its own
project and its own signatures: nothing is written into, signed for or sent to ETS.

## Steps in XKNX Editor

1. Open the edited project.
2. **File > Hand Over to ETS…**, then **Choose Baseline…**: the project as ETS has it now, for
   example a `.knxproj` exported from ETS, or a backup `.zip`. A password-protected project asks
   for its password.
3. The editor compares the two and shows how many devices and group addresses differ.
4. **Save Hand-Over…** saves one ZIP (in the browser UI it downloads to your computer).

MCP clients do the same with `project_ets_handover` (`out_path` writes the ZIP).

## What is in the ZIP

| File | What for |
|---|---|
| `group-addresses.xml` | Only the group addresses that are new or changed since the baseline, in ETS's own group address export format (`ga-export/01`). Left out when there are none. |
| `group-addresses.csv` | The same as CSV (3/1, tab separated). |
| `change-list.html` | A checklist to tick off in a browser: per device the address change, each parameter by its ETS name with the option to choose, and each group object (number and name) with the links to add and remove; then the group addresses. |
| `change-list.md`, `change-list.json` | The same list as text and for programs (`xknx-editor-change-list/1`). |
| `README.txt` | These steps, with the counts for this hand-over. |

## Steps in ETS

1. Back up the ETS project first.
2. Import `group-addresses.xml` (Group Addresses panel > Import). An import adds and updates
   group addresses but never removes one: delete those the checklist marks *removed* by hand.
3. Work through `change-list.html`, device by device. When a parameter gives a group object
   another function, ETS may drop its links: check each object keeps the links listed.
4. Check the changed devices, then download them from ETS.
