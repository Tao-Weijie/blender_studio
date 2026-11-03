"""UI: UILists and panels (3D View sidebar > Studio)."""

import time

import bpy
from bpy.types import Panel, UIList

from . import runner
from .props import camera_label, cell_map, needs_sync, refs_out_of_sync, sync_refs

STATE = runner.STATE


# ================================================================ UIList

class MX_UL_cameras(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        sub = row.row()
        sub.active = item.enabled
        sub.prop(item, "name", text="", emboss=False, icon='CAMERA_DATA')
        sub.label(text=f"{item.res_x}×{item.res_y} {item.res_pct}%")
        op = row.operator("mx.camera_view", text="", icon='HIDE_OFF', emboss=False)
        op.uid = item.uid


class MX_UL_variants(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        sub = row.row()
        sub.active = item.enabled
        sub.prop(item, "name", text="", emboss=False, icon='SCENE_DATA')
        on = sum(1 for r in item.refs if r.enabled and r.collection)
        sub.label(text=f"{on} collection(s)")
        op = row.operator("mx.variant_select", text="", icon='HIDE_OFF', emboss=False)
        op.uid = item.uid


# ================================================================ Shared drawing

TABLE_LABEL_FACTOR = 0.3      # Width ratio of the row-label column on the left of the table


def _table_row(layout, ncols):
    """One table row: returns (left row-label area, grid of ncols equal-width columns).
    Every row uses a split with the same ratio + an even_columns grid with the same column count, so column boundaries line up exactly."""
    split = layout.split(factor=TABLE_LABEL_FACTOR)
    left = split.row()
    grid = split.grid_flow(row_major=True, columns=ncols, even_columns=True, even_rows=False, align=False)
    return left, grid


def _table_cell(grid, center=True):
    """One cell in the grid; narrow widgets like checkboxes are centered, header buttons fill the cell (text centered, truncated if too long)."""
    cell = grid.row()
    if center:
        cell.alignment = 'CENTER'
    return cell


def _list_with_buttons(layout, ul, owner, prop, idx_owner, idx_prop, target, add_ops, remove_op, rows=4):
    row = layout.row()
    row.template_list(ul, "", owner, prop, idx_owner, idx_prop, rows=rows)
    col = row.column(align=True)
    for op_id, icon, kwargs in add_ops:
        op = col.operator(op_id, text="", icon=icon)
        for k, v in kwargs.items():
            setattr(op, k, v)
    op = col.operator(remove_op[0], text="", icon='REMOVE')
    for k, v in remove_op[1].items():
        setattr(op, k, v)
    col.separator()
    for direction, icon in (('UP', 'TRIA_UP'), ('DOWN', 'TRIA_DOWN')):
        op = col.operator("mx.item_move", text="", icon=icon)
        op.target, op.direction = target, direction
    return col


def _progress(layout, factor, text):
    if hasattr(layout, "progress"):
        layout.progress(factor=factor, type='BAR', text=text)
    else:
        layout.label(text=f"{text}  [{'█' * int(factor * 20):░<20}]")


STATUS_BAR_SCALE = 1.5     # Height of the start button and progress bar (multiple of UI units)


def draw_status(layout, context):
    scene = context.scene
    s = scene.mx

    if not STATE.active and runner.RESTORE_KEY in scene:
        box = layout.box()
        box.alert = True
        box.label(text="This file was saved while Render Matrix was running", icon='ERROR')
        box.operator("mx.restore_saved", icon='LOOP_BACK')

    # Always two rows so the height stays the same before / during / after rendering:
    # Row 1: start button or progress bar (same height); row 2: always one line of text
    col = layout.column()
    top = col.row()
    top.scale_y = STATUS_BAR_SCALE
    info = col.row()
    if STATE.active:
        n = len(STATE.tasks)
        _progress(top, runner.progress_factor(), f"{min(STATE.index + 1, n)}/{n}")
        info.label(text=STATE.current_label, icon='RENDER_STILL')
        sub = info.row()
        sub.alignment = 'RIGHT'
        sub.label(text=runner.fmt_seconds(time.time() - STATE.started_at), icon='TIME')
    else:
        total = len(runner.build_tasks(s))
        top.operator("mx.run_matrix", text=f"Start Render Matrix ({total} images)", icon='RENDER_STILL')
        if STATE.message:
            ok = STATE.completed and not STATE.errors
            info.label(text=STATE.message, icon='CHECKMARK' if ok else 'ERROR')
        else:
            info.label(text="")          # Placeholder to keep the height

    if STATE.warnings:
        box = layout.box()
        for w in STATE.warnings[:5]:
            box.label(text=w, icon='ERROR')
        if len(STATE.warnings) > 5:
            box.label(text=f"… {len(STATE.warnings) - 5} more warning(s)")
    if STATE.errors:
        box = layout.box()
        box.alert = True
        for e in STATE.errors[-5:]:
            box.label(text=e, icon='CANCEL')


# ================================================================ Panel contents
# Each panel's content is written once (the _Xxx classes below); _build_panels() generates real Panel subclasses for each display location.

class _Main:
    bl_label = "Render Matrix"

    def draw(self, context):
        layout = self.layout
        draw_status(layout, context)
        if not STATE.active and needs_sync(context.scene.mx):
            layout.operator("mx.sync", icon='FILE_REFRESH')
        draw_matrix(layout, context)


class _Cameras:
    bl_label = "Cameras"

    def draw(self, context):
        layout = self.layout
        layout.enabled = not STATE.active
        s = context.scene.mx
        row = layout.row(align=True)
        row.operator("mx.camera_add_all", text="All Cameras", icon='OUTLINER_OB_CAMERA')
        row.operator("mx.camera_duplicate", text="Duplicate", icon='DUPLICATE')
        row.operator("mx.camera_copy_settings", text="Apply to All", icon='COPYDOWN')
        _list_with_buttons(
            layout, "MX_UL_cameras", s, "cameras", s, "cam_index", 'CAMERA',
            add_ops=[("mx.camera_add", 'ADD', {})],
            remove_op=("mx.camera_remove", {}),
        )

        if not (0 <= s.cam_index < len(s.cameras)):
            return
        cam = s.cameras[s.cam_index]
        body = layout.column()
        body.use_property_split = True
        body.use_property_decorate = False
        body.prop(cam, "camera")
        col = body.column(align=True)
        col.prop(cam, "res_x", text="Resolution X")
        col.prop(cam, "res_y", text="Y")
        col.prop(cam, "res_pct", text="%")

        col = body.column(heading="Samples")
        row = col.row(align=True)
        row.prop(cam, "use_samples", text="")
        sub = row.row()
        sub.active = cam.use_samples
        sub.prop(cam, "samples", text="")

        body.prop(cam, "film_transparent")


_sync_pending = set()      # Names of scenes with a pending collection-record sync


def _schedule_ref_sync(scene):
    """Data cannot be modified in draw(): schedule a timer to sync model collection records on the next tick (e.g. a new collection was created)."""
    name = scene.name
    if name in _sync_pending:
        return
    _sync_pending.add(name)

    def run():
        _sync_pending.discard(name)
        sc = bpy.data.scenes.get(name)
        if sc is not None and not STATE.active:
            sync_refs(sc.mx)
            runner.tag_redraw(None)
        return None

    bpy.app.timers.register(run, first_interval=0.0)


def _draw_collection_checks(col, refs, lc, depth=0, parent_on=False):
    """List collection checkboxes row by row following the Outliner hierarchy; when a parent is checked, children are grayed out (rendered with the parent)."""
    for ch in lc.children:
        coll = ch.collection
        ref = refs.get(coll)
        on = ref is not None and ref.enabled
        row = col.row(align=True)
        row.active = not parent_on
        text = "    " * depth + coll.name
        if ref is not None:
            row.prop(ref, "enabled", text=text)
        else:
            row.label(text=text, icon='CHECKBOX_DEHLT')     # Record not synced yet; it will be added automatically on the next tick
        if ch.exclude or coll.hide_render:
            row.label(text="", icon='RESTRICT_RENDER_ON')
        _draw_collection_checks(col, refs, ch, depth + 1, parent_on or on)


class _Variants:
    bl_label = "Models"

    def draw(self, context):
        layout = self.layout
        layout.enabled = not STATE.active
        s = context.scene.mx
        row = layout.row(align=True)
        row.operator("mx.variant_duplicate", text="Duplicate", icon='DUPLICATE')
        row.operator("mx.variant_apply_to_all", text="Apply to All", icon='COPYDOWN')
        _list_with_buttons(
            layout, "MX_UL_variants", s, "variants", s, "var_index", 'VARIANT',
            add_ops=[("mx.variant_add", 'ADD', {})],
            remove_op=("mx.variant_remove", {}),
            rows=3,
        )

        if 0 <= s.var_index < len(s.variants):
            v = s.variants[s.var_index]
            body = layout.column()
            body.use_property_split = True
            body.use_property_decorate = False
            body.prop(v, "hidden_mode")
            vl = context.view_layer
            if vl.layer_collection.children:
                if refs_out_of_sync(s):
                    _schedule_ref_sync(context.scene)
                refs = {r.collection: r for r in v.refs}
                _draw_collection_checks(body.column(heading="Collections"), refs, vl.layer_collection)
            body.operator("mx.variant_refs_clear", text="Uncheck All", icon='X')


def draw_matrix(layout, context):
    """Render matrix toggle table: rows = cameras, columns = models."""
    s = context.scene.mx
    if not s.cameras or not s.variants:
        return
    layout = layout.column()
    layout.enabled = not STATE.active
    row = layout.row(align=True)
    for mode, text, icon in (('ALL_ON', "Select All", 'CHECKBOX_HLT'),
                             ('ALL_OFF', "Select None", 'CHECKBOX_DEHLT'),
                             ('INVERT', "Invert", 'ARROW_LEFTRIGHT')):
        row.operator("mx.matrix_set", text=text, icon=icon).mode = mode

    cmap = cell_map(s)
    box = layout.box()
    ncols = len(s.variants)
    left, grid = _table_row(box, ncols)
    left.label(text="")
    for v in s.variants:
        cell = _table_cell(grid, center=False)
        cell.active = v.enabled
        op = cell.operator("mx.matrix_set", text=v.name, emboss=False, icon='SCENE_DATA')
        op.mode, op.uid = 'COL', v.uid

    for c in s.cameras:
        left, grid = _table_row(box, ncols)
        left.active = c.enabled
        left.alignment = 'LEFT'
        op = left.operator("mx.matrix_set", text=camera_label(c), emboss=False, icon='CAMERA_DATA')
        op.mode, op.uid = 'ROW', c.uid
        grid.active = c.enabled and c.camera is not None
        for v in s.variants:
            cell = _table_cell(grid)
            cell.active = v.enabled
            item = cmap.get((c.uid, v.uid))
            if item is not None:
                cell.prop(item, "enabled", text="")
            else:
                cell.label(text="·")


# ================================================================ Panel generation

# Display locations: idname suffix -> space attributes
LOCATIONS = {
    "": dict(bl_space_type='VIEW_3D', bl_region_type='UI', bl_category="Studio"),
}

# (content class, key, parent panel key, collapsed by default), in display order
PANEL_TREE = (
    (_Main, "main", None, False),
    (_Cameras, "cameras", "main", False),
    (_Variants, "variants", "main", False),
)


def _build_panels():
    """Generate MX_PT_<key><suffix> for each display location, e.g. MX_PT_cameras in the sidebar."""
    panels = []
    for suffix, space in LOCATIONS.items():
        for order, (body, key, parent, closed) in enumerate(PANEL_TREE):
            idname = f"MX_PT_{key}{suffix}"
            ns = dict(space, bl_idname=idname, bl_order=order,
                      bl_options={'DEFAULT_CLOSED'} if closed else set())
            if parent:
                ns["bl_parent_id"] = f"MX_PT_{parent}{suffix}"
            panels.append(type(idname, (body, Panel), ns))
    return panels


classes = (
    MX_UL_cameras,
    MX_UL_variants,
    *_build_panels(),
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
