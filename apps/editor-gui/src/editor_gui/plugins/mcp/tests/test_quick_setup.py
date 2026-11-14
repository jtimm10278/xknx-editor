"""MCP Quick Setup: the templates for a device, applying one as one undo step (also as a
project_batch op), and refusals that change nothing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

import editor_gui.quick_setup as quick_setup
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin

from .test_server import _FIXTURE, _build_ctx, _data

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")

_PRESSED = "MD-1_M-100_MI-1_UP-3"
_RELEASED = "MD-1_M-100_MI-1_UP-4"


def _template_file(folder: Path, object_number: int) -> None:
    """A template for the fixture's application (Gira 7072 version 21): channel 1 sends On when
    pressed and Off when released; one object linked to one group address."""
    folder.mkdir(exist_ok=True)
    doc = {
        "format": 1,
        "device": {
            "name": "Gira push-button interface",
            "manufacturer": "M-0008",
            "application_number": "7072",
            "application_versions": ["21"],
        },
        "templates": [
            {
                "id": "press-on-release-off",
                "name": {"en": "Press on, release off"},
                "parameters": [
                    {"parameter": _PRESSED, "value": "1"},
                    {"parameter": _RELEASED, "value": "2"},
                ],
                "slots": [
                    {"id": "light", "label": "Light", "object": object_number},
                ],
            },
            {
                "id": "bad-value",
                "name": {"en": "Refused"},
                "parameters": [
                    {"parameter": _PRESSED, "value": "1"},
                    {"parameter": _RELEASED, "value": "99"},
                ],
                "slots": [
                    {"id": "light", "label": "Light", "object": object_number},
                ],
            },
        ],
    }
    (folder / "gira.json").write_text(json.dumps(doc), encoding="utf-8")


async def _setup(
    client: Client, ctx: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, str]:
    await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
    await _data(client, "catalog_import_product", path=str(_FIXTURE))
    products = (await _data(client, "catalog_list_products"))["items"]
    node_id = (
        await _data(
            client, "project_add_device", product_ref_id=products[0]["product_ref_id"]
        )
    )["node_id"]
    device = ctx.api.project.find_device_by_node_id(node_id)
    # An object of channel 2, which the template's channel 1 parameters leave alone.
    number = next(
        co.number
        for co in device.com_objects
        if co.db_id is not None and co.name.startswith("K 2")
    )
    folder = tmp_path / "templates"
    _template_file(folder, number)
    monkeypatch.setattr(quick_setup, "_library", quick_setup.TemplateLibrary([folder]))
    ga = await _data(client, "project_create_group_address", name="Light")
    printed = ctx.api.project.get_group_address(ga["group_address_id"]).address
    return node_id, printed


def _server(tmp_path: Path) -> tuple[Any, Any]:
    ctx = _build_ctx(tmp_path)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    return build_server(ctx), ctx


def _value(ctx: Any, node_id: int, parameter: str) -> str | None:
    device = ctx.api.project.find_device_by_node_id(node_id)
    ref = quick_setup.resolve_ref(device, device.app.program.id, parameter)
    return device.get_param_value(ref)


async def test_templates_for_a_device_and_apply_as_one_undo_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, ctx = _server(tmp_path)
    async with Client(mcp) as client:
        node_id, printed = await _setup(client, ctx, tmp_path, monkeypatch)
        listed = await _data(client, "project_quick_setup_templates", node_id=node_id)
        assert listed["errors"] == []
        assert [t["id"] for t in listed["templates"]] == [
            "bad-value",
            "press-on-release-off",
        ]
        before = (_value(ctx, node_id, _PRESSED), _value(ctx, node_id, _RELEASED))

        result = await _data(
            client,
            "project_quick_setup_apply",
            template="press-on-release-off",
            node_id=node_id,
            group_addresses={"light": printed},
        )
        assert [p["value"] for p in result["parameters"]] == ["1", "2"]
        assert [link["slot"] for link in result["links"]] == ["light"]
        assert (_value(ctx, node_id, _PRESSED), _value(ctx, node_id, _RELEASED)) == (
            "1",
            "2",
        )

        await _data(client, "project_undo")
        assert (
            _value(ctx, node_id, _PRESSED),
            _value(ctx, node_id, _RELEASED),
        ) == before


async def test_a_refused_step_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, ctx = _server(tmp_path)
    async with Client(mcp) as client:
        node_id, printed = await _setup(client, ctx, tmp_path, monkeypatch)
        before = _value(ctx, node_id, _PRESSED)
        cursor = ctx.api.project.cursor
        with pytest.raises(Exception, match="99"):
            await client.call_tool(
                "project_quick_setup_apply",
                {
                    "template": "bad-value",
                    "node_id": node_id,
                    "group_addresses": {"light": printed},
                },
            )
        assert _value(ctx, node_id, _PRESSED) == before
        assert ctx.api.project.cursor == cursor
        with pytest.raises(Exception, match="no group address for 'light'"):
            await client.call_tool(
                "project_quick_setup_apply",
                {"template": "press-on-release-off", "node_id": node_id},
            )
        with pytest.raises(Exception, match="no Quick Setup template"):
            await client.call_tool(
                "project_quick_setup_apply",
                {"template": "nope", "node_id": node_id},
            )


async def test_batch_applies_a_template_by_device_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, ctx = _server(tmp_path)
    async with Client(mcp) as client:
        node_id, printed = await _setup(client, ctx, tmp_path, monkeypatch)
        await _data(
            client, "project_set_individual_address", node_id=node_id, address="1.1.20"
        )
        result = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "quick_setup",
                    "params": {
                        "device_address": "1.1.20",
                        "template": "press-on-release-off",
                        "group_addresses": {"light": printed},
                    },
                }
            ],
        )
        assert result["ok"] == 1, result
        assert _value(ctx, node_id, _RELEASED) == "2"
