"""Quick Setup: ready-made parameter + link set-ups for common wall-switch patterns, as the
device's "Quick Setup…" dialog applies them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.project import (
    require_device,
    require_group_address_id,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService


def _language() -> str:
    from editor_gui.strings import get_locale

    return get_locale()


def apply_quick_setup(
    project: ProjectService,
    device: Device,
    template: str,
    group_address_ids: dict[str, int] | None,
    group_addresses: dict[str, str] | None,
    *,
    replace_links: bool = True,
) -> dict[str, Any]:
    """Apply the template ``template`` (key or id) to ``device`` as one undo step (see
    project_quick_setup_apply); raises ToolError with the reason when it is refused."""
    from editor_gui.plugins.project.strings import S
    from editor_gui.quick_setup import QuickSetupError, apply_template, library

    chosen = library().find(template)
    if chosen is None:
        raise ToolError(
            f"no Quick Setup template {template!r}; call project_quick_setup_templates"
        )
    both = set(group_address_ids or {}) & set(group_addresses or {})
    if both:
        raise ToolError(
            f"slot(s) {sorted(both)} given in both group_address_ids and group_addresses"
        )
    assignments: dict[str, int | None] = {}
    for slot, ga_id in (group_address_ids or {}).items():
        assignments[slot] = require_group_address_id(project, int(ga_id), None)
    for slot, printed in (group_addresses or {}).items():
        assignments[slot] = require_group_address_id(project, None, str(printed))
    try:
        result = apply_template(
            project,
            device.node_id,
            chosen,
            assignments,
            replace_links=replace_links,
            label=S.QUICK_SETUP_UNDO.format(template=chosen.name(_language())),
        )
    except QuickSetupError as exc:
        raise ToolError(str(exc)) from exc
    return {
        "node_id": result.node_id,
        "template": result.template,
        "parameters": [
            {"ref_id": ref, "value": value} for ref, value in result.parameters
        ],
        "links": [
            {
                "slot": link.slot,
                "object": link.object,
                "group_address_id": link.group_address_id,
                "sending": link.sending,
            }
            for link in result.links
        ],
        "removed_links": result.removed_links,
    }


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    project = ctx.api.project

    @tool("read")
    def project_quick_setup_templates(
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """The Quick Setup templates: ready-made set-ups for common wall-switch patterns (e.g. two
        latching inputs for lights + blinds), as the device's "Quick Setup…" dialog offers them.

        With node_id OR device_address (e.g. "1.0.60"): only the templates for that device's
        application. Without: every template. Templates come from the editor's bundled folder and
        the user's own folder (``quick_setup`` under the editor's config folder, JSON files).

        Returns {templates, errors}. Each template: {key, id, device, name, description,
        parameters: [{parameter, value}], slots: [{id, label, object, sending, required}],
        source}. ``slots`` are the group objects the template links: pass a group address per
        slot id to project_quick_setup_apply. ``errors`` lists template files that could not be
        read."""
        from editor_gui.quick_setup import describe, iter_templates, library

        lib = library()
        language = _language()
        if node_id is None and device_address is None:
            templates = lib.templates
        else:
            require_project(ctx)

            def _for_device() -> list[Any]:
                device = require_device(project, node_id, device_address)
                return lib.for_device(device)

            templates = ctx.run_locked(_for_device)
        return {
            "templates": [describe(t, language) for t in iter_templates(templates)],
            "errors": list(lib.errors),
        }

    @tool("write")
    def project_quick_setup_apply(
        template: str,
        node_id: int | None = None,
        device_address: str | None = None,
        group_address_ids: dict[str, int] | None = None,
        group_addresses: dict[str, str] | None = None,
        replace_links: bool = True,
    ) -> dict[str, Any]:
        """Apply a Quick Setup template to a device, as its "Quick Setup…" dialog does: set the
        template's parameters and link each slot's group object, as ONE undo step
        (project_undo reverts all of it). Nothing is sent to the device; download it afterwards.

        template: a template ``key`` (or ``id``) from project_quick_setup_templates.
        Device by node_id OR device_address. Group addresses per slot id, by id
        (group_address_ids, e.g. {"lights": 12}) and/or by printed address (group_addresses, e.g.
        {"blinds": "3/1/13"}); a slot in neither stays unlinked, which only an optional slot
        allows. replace_links (default true) removes the slot objects' other links first.

        Returns {node_id, template, parameters: [{ref_id, value}], links: [{slot, object,
        group_address_id, sending}], removed_links}. When any step is refused nothing is changed
        and the error names the step."""
        require_project(ctx)

        def _apply() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            return apply_quick_setup(
                project,
                device,
                template,
                group_address_ids,
                group_addresses,
                replace_links=replace_links,
            )

        return ctx.run_locked(_apply)
