"""textToCadForAll - SolidWorks live-session MCP server.

Gives an AI client real-time *awareness* of what the human is doing in a running
SOLIDWORKS session: current selection, view, feature tree, rebuild errors and
screenshots. Everything goes through the native COM API, never simulated input.

Units: all lengths returned to the client are millimetres (SOLIDWORKS API uses metres).

Capability tiers (honest):
  verified : tested on SOLIDWORKS 2026 SP3 with pywin32 312 / mcp 2.2
  pilot    : implemented, not yet exercised on a real session - review output
"""

from __future__ import annotations

import base64
import io
import os
import tempfile
from typing import Any

import pythoncom
import win32com.client
from mcp.server.mcpserver import MCPServer
from mcp.types import ImageContent, TextContent
from win32com.client import VARIANT

mcp = MCPServer(
    "sw-live",
    instructions=(
        "Live awareness of the running SOLIDWORKS session. Call live_status first, then "
        "get_selection / get_view / screenshot to see what the human is looking at before acting. "
        "Lengths are in millimetres."
    ),
)

DOC_TYPES = {1: "part", 2: "assembly", 3: "drawing"}

# swSelectType_e (subset that matters for design work)
SEL_TYPES = {
    0: "nothing", 1: "edge", 2: "face", 3: "vertex", 4: "plane", 5: "axis", 6: "point",
    9: "sketch", 10: "sketch_segment", 11: "sketch_point", 12: "drawing_view", 13: "gtol",
    14: "dimension", 15: "note", 19: "sheet", 20: "component", 21: "mate", 22: "feature",
    23: "ref_curve", 24: "ext_sketch_segment", 25: "ext_sketch_point", 26: "helix",
    29: "configuration", 76: "solid_body", 77: "surface_body", 91: "sketch_text",
}

# ISurface.Identity() codes
SURFACE_TYPES = {
    4001: "plane", 4002: "cylinder", 4003: "cone", 4004: "sphere", 4005: "torus",
    4006: "bsurface", 4007: "blend", 4008: "offset", 4009: "extruded", 4010: "swept",
}

FOLDER_TYPES = {
    "CommentsFolder", "FavoriteFolder", "HistoryFolder", "SelectionSetFolder", "SensorFolder",
    "LiveSectionFolder", "DocsFolder", "DetailCabinet", "MaterialFolder", "SolidBodyFolder",
    "SurfaceBodyFolder", "EnvFolder", "AmbientLight", "DirectionLight", "PointLight",
    "SpotLight", "CameraFolder", "MarkupFolder", "NotesFolder", "EqnFolder", "TableFolder",
    "AnnotationFolder", "Annotations", "SketchBlockDef", "BlockFolder", "MateGroup",
}


# --------------------------------------------------------------------------- COM helpers

def m(obj, name: str, *args):
    """Read a COM member. pywin32 exposes some zero-arg methods as properties."""
    attr = getattr(obj, name)
    if callable(attr) and not isinstance(attr, win32com.client.CDispatch):
        return attr(*args)
    return attr


def sw():
    pythoncom.CoInitialize()
    try:
        return win32com.client.GetActiveObject("SldWorks.Application")
    except pythoncom.com_error:
        raise RuntimeError(
            "SOLIDWORKS is not running (or runs at a different privilege level than this server)."
        )


def active_doc():
    doc = sw().ActiveDoc
    if doc is None:
        raise RuntimeError("No active document in SOLIDWORKS.")
    return doc


def mm(value_m: float | None) -> float | None:
    return None if value_m is None else round(float(value_m) * 1000.0, 4)


def mm3(seq) -> list[float] | None:
    if seq is None:
        return None
    return [mm(x) for x in list(seq)[:3]]


def byref_bool(initial: bool = False):
    return VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, initial)


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# --------------------------------------------------------------------------- watch tools

@mcp.tool()
def live_status() -> dict:
    """[verified] Snapshot of the session: version, active document, rebuild state, selection count.

    Call this first. Cheap. Tells you whether the human is inside an in-context component edit.
    """
    app = sw()
    doc = app.ActiveDoc
    out: dict[str, Any] = {
        "connected": True,
        "solidworks_revision": m(app, "RevisionNumber"),
        "active_document": None,
    }
    if doc is None:
        return out
    doc_type = m(doc, "GetType")
    out["active_document"] = {
        "title": m(doc, "GetTitle"),
        "path": m(doc, "GetPathName") or None,
        "type": DOC_TYPES.get(doc_type, str(doc_type)),
        "unsaved_changes": bool(_safe(lambda: m(doc, "GetSaveFlag"), False)),
        "needs_rebuild": bool(_safe(lambda: m(doc.Extension, "NeedsRebuild2"), False)),
        "active_configuration": _safe(lambda: m(doc.ConfigurationManager.ActiveConfiguration, "Name")),
    }
    out["selection_count"] = int(_safe(lambda: m(doc.SelectionManager, "GetSelectedObjectCount2", -1), 0))
    if doc_type == 2:
        edit_target = _safe(lambda: m(doc, "GetEditTarget"))
        out["editing_component_in_context"] = bool(
            edit_target is not None and _safe(lambda: m(edit_target, "GetTitle")) != m(doc, "GetTitle")
        )
    return out


def _describe_entity(sel_type: int, obj) -> dict:
    """Best-effort geometric description of a selected entity, in mm."""
    d: dict[str, Any] = {}
    if obj is None:
        return d
    kind = SEL_TYPES.get(sel_type)
    if kind == "face":
        surf = _safe(lambda: m(obj, "GetSurface"))
        if surf is not None:
            ident = _safe(lambda: m(surf, "Identity"))
            d["surface"] = SURFACE_TYPES.get(ident, str(ident))
            if ident == 4001:
                p = _safe(lambda: list(m(surf, "PlaneParams")))
                if p:
                    d["normal"] = [round(x, 4) for x in p[:3]]
                    d["origin_mm"] = mm3(p[3:6])
            elif ident in (4002, 4003):
                p = _safe(lambda: list(m(surf, "CylinderParams" if ident == 4002 else "ConeParams")))
                if p:
                    d["axis_origin_mm"] = mm3(p[0:3])
                    d["axis_dir"] = [round(x, 4) for x in p[3:6]]
                    d["radius_mm"] = mm(p[6])
        area = _safe(lambda: m(obj, "GetArea"))
        if area is not None:
            d["area_mm2"] = round(area * 1e6, 2)
    elif kind == "edge":
        curve = _safe(lambda: m(obj, "GetCurve"))
        if curve is not None:
            if _safe(lambda: m(curve, "IsLine"), False):
                d["curve"] = "line"
            elif _safe(lambda: m(curve, "IsCircle"), False):
                d["curve"] = "circle"
                p = _safe(lambda: list(m(curve, "CircleParams")))
                if p:
                    d["center_mm"] = mm3(p[0:3])
                    d["axis_dir"] = [round(x, 4) for x in p[3:6]]
                    d["radius_mm"] = mm(p[6])
            else:
                d["curve"] = "other"
        sv = _safe(lambda: m(obj, "GetStartVertex"))
        ev = _safe(lambda: m(obj, "GetEndVertex"))
        if sv is not None and ev is not None:
            d["start_mm"] = mm3(_safe(lambda: m(sv, "GetPoint")))
            d["end_mm"] = mm3(_safe(lambda: m(ev, "GetPoint")))
    elif kind == "vertex":
        d["point_mm"] = mm3(_safe(lambda: m(obj, "GetPoint")))
    elif kind in ("component",):
        d["component"] = _safe(lambda: m(obj, "Name2"))
        d["path"] = _safe(lambda: m(obj, "GetPathName"))
        d["suppressed"] = _safe(lambda: m(obj, "IsSuppressed"))
        d["fixed"] = _safe(lambda: m(obj, "IsFixed"))
    elif kind in ("feature", "sketch", "plane", "axis", "mate"):
        d["feature"] = _safe(lambda: m(obj, "Name"))
        d["feature_type"] = _safe(lambda: m(obj, "GetTypeName2"))
    elif kind == "dimension":
        dim = _safe(lambda: m(obj, "GetDimension2", 0))
        if dim is not None:
            d["dimension"] = _safe(lambda: m(dim, "FullName"))
            d["value_mm"] = mm(_safe(lambda: m(dim, "SystemValue")))
    return d


@mcp.tool()
def get_selection() -> dict:
    """[verified] What the human currently has selected, with geometry in mm.

    Returns each entity's kind (face/edge/vertex/component/feature/...), the owning component
    in an assembly, the click point, and a geometric description (plane normal, cylinder axis
    and radius, circle centre, line endpoints, ...). Empty list means nothing is selected.
    """
    doc = active_doc()
    sel = doc.SelectionManager
    n = int(m(sel, "GetSelectedObjectCount2", -1))
    items = []
    for i in range(1, n + 1):
        t = int(m(sel, "GetSelectedObjectType3", i, -1))
        obj = _safe(lambda: m(sel, "GetSelectedObject6", i, -1))
        entry: dict[str, Any] = {"index": i, "kind": SEL_TYPES.get(t, f"type_{t}")}
        comp = _safe(lambda: m(sel, "GetSelectedObjectsComponent4", i, -1))
        if comp is not None:
            entry["component"] = _safe(lambda: m(comp, "Name2"))
        pt = _safe(lambda: m(sel, "GetSelectionPoint2", i, -1))
        if pt is not None:
            entry["click_point_mm"] = mm3(pt)
        entry.update(_describe_entity(t, obj))
        items.append(entry)
    return {"count": n, "selected": items}


@mcp.tool()
def get_view() -> dict:
    """[verified] Active model view: orientation matrix, eye translation (mm), zoom scale.

    The 3x3 rotation is row-major from the view transform; use it to know which way the human
    is looking before choosing a named view for a screenshot.
    """
    doc = active_doc()
    view = doc.ActiveView
    xf = m(view, "Orientation3")
    data = [round(float(x), 5) for x in m(xf, "ArrayData")]
    tr = _safe(lambda: list(m(view, "Translation3").ArrayData))
    return {
        "rotation_3x3": [data[0:3], data[3:6], data[6:9]],
        "translation_mm": mm3(tr),
        "scale": _safe(lambda: float(m(view, "Scale2"))),
        "display_mode": _safe(lambda: int(m(view, "DisplayMode"))),
    }


@mcp.tool()
def get_feature_tree(max_items: int = 300, include_folders: bool = False) -> dict:
    """[verified] Feature tree of the active document: name, type, suppressed, error code.

    For assemblies the top-level components are listed too, with suppression and fixed state.
    Non-zero error_code means the feature has a rebuild error or warning.
    """
    doc = active_doc()
    feats = []
    f = m(doc, "FirstFeature")
    count = 0
    while f is not None and count < max_items:
        ftype = _safe(lambda: m(f, "GetTypeName2"), "?")
        if include_folders or ftype not in FOLDER_TYPES:
            what = byref_bool()
            err = _safe(lambda: int(f.GetErrorCode2(what)), None)
            feats.append({
                "name": _safe(lambda: m(f, "Name")),
                "type": ftype,
                "suppressed": bool(_safe(lambda: m(f, "IsSuppressed"), False)),
                "error_code": err,
            })
        f = m(f, "GetNextFeature")
        count += 1
    out: dict[str, Any] = {"document": m(doc, "GetTitle"), "features": feats, "truncated": count >= max_items}
    if m(doc, "GetType") == 2:
        comps = _safe(lambda: list(m(doc, "GetComponents", True)), [])
        out["components"] = [
            {
                "name": _safe(lambda: m(c, "Name2")),
                "suppressed": bool(_safe(lambda: m(c, "IsSuppressed"), False)),
                "fixed": bool(_safe(lambda: m(c, "IsFixed"), False)),
                "path": _safe(lambda: m(c, "GetPathName")),
            }
            for c in comps
        ]
    return out


@mcp.tool()
def get_rebuild_errors() -> dict:
    """[verified] Features (and assembly components) that currently carry a rebuild error/warning.

    Read-only; does not force a rebuild. Use rebuild() first if needs_rebuild is true.
    """
    tree = get_feature_tree(max_items=2000, include_folders=False)
    bad = [x for x in tree["features"] if x["error_code"] not in (0, None)]
    out = {"document": tree["document"], "error_features": bad}
    if "components" in tree:
        out["suppressed_components"] = [c["name"] for c in tree["components"] if c["suppressed"]]
    return out


@mcp.tool()
def screenshot(
    width: int = 1280,
    height: int = 800,
    named_view: str | None = None,
    zoom_to_fit: bool = False,
    restore_view: bool = True,
) -> list:
    """[verified] Capture the graphics area as a PNG image so you can LOOK at the model.

    named_view: None keeps the human's current view; or "*Isometric", "*Front", "*Top",
    "*Right", "*Back", "*Left", "*Bottom", "*Trimetric", "*Dimetric".
    zoom_to_fit fits the whole model. With restore_view (default) the human's view is put
    back after the capture, so taking pictures never disturbs their work.
    """
    doc = active_doc()
    view = doc.ActiveView
    saved = None
    if restore_view and (named_view or zoom_to_fit):
        saved = (
            _safe(lambda: m(view, "Orientation3")),
            _safe(lambda: m(view, "Translation3")),
            _safe(lambda: float(m(view, "Scale2"))),
        )
    try:
        if named_view:
            doc.ShowNamedView2(named_view, -1)
        if zoom_to_fit:
            m(doc, "ViewZoomtofit2")
        fd, bmp_path = tempfile.mkstemp(suffix=".bmp", prefix="sw_live_")
        os.close(fd)
        ok = bool(m(doc, "SaveBMP", bmp_path, int(width), int(height)))
        if not ok:
            raise RuntimeError("SaveBMP returned false (is the graphics window visible?).")
        from PIL import Image  # local import keeps startup fast

        with Image.open(bmp_path) as im:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="PNG", optimize=True)
        os.remove(bmp_path)
        data = base64.b64encode(buf.getvalue()).decode("ascii")
    finally:
        if saved and saved[0] is not None:
            try:
                view.Orientation3 = saved[0]
                if saved[1] is not None:
                    view.Translation3 = saved[1]
                if saved[2] is not None:
                    view.Scale2 = saved[2]
                m(doc, "GraphicsRedraw2")
            except Exception:
                pass
    caption = f"{m(doc, 'GetTitle')} | view={named_view or 'current'} | {width}x{height}"
    return [TextContent(type="text", text=caption), ImageContent(type="image", data=data, mimeType="image/png")]


# --------------------------------------------------------------------------- light actions

@mcp.tool()
def rebuild(force_all: bool = False) -> dict:
    """[pilot] Rebuild the active document and report the rebuild-error features afterwards."""
    doc = active_doc()
    if force_all:
        ok = bool(m(doc, "ForceRebuild3", False))
    else:
        ok = bool(m(doc, "EditRebuild3"))
    return {"rebuilt": ok, **get_rebuild_errors()}


@mcp.tool()
def undo(steps: int = 1) -> dict:
    """[pilot] Undo the last N operations in the active document (same as Ctrl+Z).

    Use this to back out an AI action the human rejects. Only undoes what SOLIDWORKS
    itself can undo; feature edits made via the API are undoable, file saves are not.
    """
    doc = active_doc()
    ok = bool(m(doc, "EditUndo2", int(steps)))
    return {"undone": ok, "steps": int(steps), "needs_rebuild": bool(_safe(lambda: m(doc.Extension, "NeedsRebuild2"), False))}


@mcp.tool()
def select_by_name(name: str, entity_type: str, component: str | None = None, append: bool = False) -> dict:
    """[pilot] Select a named entity so a following action (or the human) can use it.

    entity_type examples: PLANE, AXIS, FACE, EDGE, BODYFEATURE, SKETCH, COMPONENT, DIMENSION.
    In an assembly pass the component name (e.g. "bracket-1") and the entity name is
    resolved as "name@component@assembly".
    """
    doc = active_doc()
    full = name
    if component:
        full = f"{name}@{component}@{m(doc, 'GetTitle')}"
    null_disp = VARIANT(pythoncom.VT_DISPATCH, None)
    ok = bool(doc.Extension.SelectByID2(full, entity_type.upper(), 0, 0, 0, bool(append), 0, null_disp, 0))
    return {"selected": ok, "name": full, "entity_type": entity_type.upper(), "selection": get_selection()}


@mcp.tool()
def clear_selection() -> dict:
    """[verified] Clear the current selection."""
    doc = active_doc()
    doc.ClearSelection2(True)
    return {"selection_count": int(m(doc.SelectionManager, "GetSelectedObjectCount2", -1))}


if __name__ == "__main__":
    mcp.run()
