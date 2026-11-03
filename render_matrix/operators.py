"""Operators: run control, camera / model management, matrix toggles, restore and utilities."""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator

from . import prefs, runner
from .visibility import iter_lc
from .props import cell_map, find_by_uid, new_uid, sync_all, sync_cells, sync_refs, unique_camera_name

STATE = runner.STATE

TARGETS = [
    ('CAMERA', "Camera", ""),
    ('VARIANT', "Model", ""),
]


def _list_of(context, target):
    """Return (collection property, owner, active index property name)."""
    s = context.scene.mx
    return {
        'CAMERA': (s.cameras, s, "cam_index"),
        'VARIANT': (s.variants, s, "var_index"),
    }[target]


class _IdleOperator:
    """Disallow configuration changes while rendering is in progress."""

    @classmethod
    def poll(cls, context):
        return context.scene is not None and not STATE.active


# ================================================================ Run control

class MX_OT_run_matrix(Operator):
    bl_idname = "mx.run_matrix"
    bl_label = "Start Render Matrix"
    bl_description = "Render all checked matrix cells one by one in the background while the UI stays responsive"

    _timer = None

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.window is not None and not STATE.active

    def invoke(self, context, event):
        err = runner.start(context)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        for w in STATE.warnings[:5]:
            self.report({'WARNING'}, w)
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        runner.tag_redraw(context)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        return self.invoke(context, None)

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}          # Don't consume any input; the UI stays usable
        try:
            result = runner.step(context)
        except Exception as e:               # Fallback: always restore the scene on any unexpected error
            STATE.errors.append(f"Runner exception: {e}")
            runner.finish("Aborted due to error")
            result = 'FINISHED'
        runner.tag_redraw(context)
        if result == 'FINISHED':
            self._remove_timer(context)
            ok = STATE.completed and not STATE.errors
            self.report({'INFO'} if ok else {'WARNING'}, STATE.message)
            return {'FINISHED'}
        return {'PASS_THROUGH'}

    def cancel(self, context):
        if STATE.active:
            runner.finish("Interrupted")
        self._remove_timer(context)

    def _remove_timer(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None


class MX_OT_restore_saved(Operator):
    bl_idname = "mx.restore_saved"
    bl_label = "Restore Scene Visibility"
    bl_description = "This file was saved while Render Matrix was running; restore the visibility and render settings from before the run"

    @classmethod
    def poll(cls, context):
        return context.scene is not None and not STATE.active and runner.RESTORE_KEY in context.scene

    def execute(self, context):
        failed = runner.restore_saved(context.scene)
        if failed:
            self.report({'WARNING'}, f"Some properties could not be restored ({len(failed)} items)")
        else:
            self.report({'INFO'}, "Restored")
        return {'FINISHED'}


class MX_OT_open_output(Operator):
    bl_idname = "mx.open_output"
    bl_label = "Open Output Directory"

    def execute(self, context):
        out = prefs.get(context).output_dir
        if out.startswith("//") and not bpy.data.filepath:
            self.report({'ERROR'}, "File has not been saved; relative path cannot be resolved")
            return {'CANCELLED'}
        path = bpy.path.abspath(out)
        os.makedirs(path, exist_ok=True)
        bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


class MX_OT_sync(_IdleOperator, Operator):
    bl_idname = "mx.sync"
    bl_label = "Refresh Matrix"
    bl_description = "Fill in / clean up matrix cells and collection references in models"
    bl_options = {'UNDO'}

    def execute(self, context):
        sync_all(context.scene.mx)
        return {'FINISHED'}


class MX_OT_item_move(_IdleOperator, Operator):
    bl_idname = "mx.item_move"
    bl_label = "Move"
    bl_options = {'UNDO', 'INTERNAL'}

    target: EnumProperty(items=TARGETS)
    direction: EnumProperty(items=[('UP', "Move Up", ""), ('DOWN', "Move Down", "")])

    def execute(self, context):
        coll, owner, idx_attr = _list_of(context, self.target)
        i = getattr(owner, idx_attr)
        j = i - 1 if self.direction == 'UP' else i + 1
        if 0 <= i < len(coll) and 0 <= j < len(coll):
            coll.move(i, j)
            setattr(owner, idx_attr, j)
        return {'FINISHED'}


# ================================================================ Cameras

def _init_camera_item(item, scene, cam_obj=None):
    item.uid = new_uid()
    item.name = unique_camera_name(scene.mx, cam_obj.name if cam_obj else "Camera", exclude=item)
    item.camera = cam_obj
    item.res_x = scene.render.resolution_x
    item.res_y = scene.render.resolution_y
    item.res_pct = scene.render.resolution_percentage


class MX_OT_camera_add(_IdleOperator, Operator):
    bl_idname = "mx.camera_add"
    bl_label = "Add Camera"
    bl_description = "Add a camera entry (uses the active object directly if it is a camera not yet added)"
    bl_options = {'UNDO'}

    def execute(self, context):
        s = context.scene.mx
        obj = context.active_object
        used = {c.camera for c in s.cameras}
        cam = obj if obj is not None and obj.type == 'CAMERA' and obj not in used else None
        _init_camera_item(s.cameras.add(), context.scene, cam)
        s.cam_index = len(s.cameras) - 1
        sync_cells(s)
        return {'FINISHED'}


class MX_OT_camera_add_all(_IdleOperator, Operator):
    bl_idname = "mx.camera_add_all"
    bl_label = "Add All Scene Cameras"
    bl_options = {'UNDO'}

    def execute(self, context):
        s = context.scene.mx
        used = {c.camera for c in s.cameras}
        added = 0
        for obj in sorted(context.scene.objects, key=lambda o: o.name):
            if obj.type == 'CAMERA' and obj not in used:
                _init_camera_item(s.cameras.add(), context.scene, obj)
                added += 1
        sync_cells(s)
        self.report({'INFO'}, f"Added {added} camera(s)")
        return {'FINISHED'}


class MX_OT_camera_remove(_IdleOperator, Operator):
    bl_idname = "mx.camera_remove"
    bl_label = "Remove Camera"
    bl_options = {'UNDO'}

    def execute(self, context):
        s = context.scene.mx
        if 0 <= s.cam_index < len(s.cameras):
            s.cameras.remove(s.cam_index)
            s.cam_index = min(s.cam_index, len(s.cameras) - 1)
            sync_cells(s)
        return {'FINISHED'}


CAMERA_PARAMS = ["res_x", "res_y", "res_pct", "use_samples", "samples", "film_transparent"]


class MX_OT_camera_duplicate(_IdleOperator, Operator):
    bl_idname = "mx.camera_duplicate"
    bl_label = "Duplicate Camera"
    bl_description = "Duplicate the current camera entry (same camera object, all parameters and matrix toggles) and insert it below as a new entry"
    bl_options = {'UNDO'}

    def execute(self, context):
        s = context.scene.mx
        if not (0 <= s.cam_index < len(s.cameras)):
            return {'CANCELLED'}
        index = s.cam_index
        src = s.cameras[index]
        src_uid, name, cam_obj, enabled = src.uid, src.name, src.camera, src.enabled
        values = {a: getattr(src, a) for a in CAMERA_PARAMS}

        item = s.cameras.add()                  # The src reference may become invalid after add(), so read the data first
        item.uid = new_uid()
        item.camera, item.enabled = cam_obj, enabled
        for a, val in values.items():
            setattr(item, a, val)
        item.name = unique_camera_name(s, name, exclude=item)

        sync_cells(s)
        cmap = cell_map(s)
        for v in s.variants:
            old, new = cmap.get((src_uid, v.uid)), cmap.get((item.uid, v.uid))
            if old and new:
                new.enabled = old.enabled
        s.cameras.move(len(s.cameras) - 1, index + 1)
        s.cam_index = index + 1
        return {'FINISHED'}


class MX_OT_camera_copy_settings(_IdleOperator, Operator):
    bl_idname = "mx.camera_copy_settings"
    bl_label = "Apply to All Cameras"
    bl_description = "Overwrite all other camera entries with this entry's resolution, samples and transparent background"
    bl_options = {'UNDO'}

    def execute(self, context):
        s = context.scene.mx
        if not (0 <= s.cam_index < len(s.cameras)):
            return {'CANCELLED'}
        src = s.cameras[s.cam_index]
        for c in s.cameras:
            if c != src:
                for a in CAMERA_PARAMS:
                    setattr(c, a, getattr(src, a))
        return {'FINISHED'}


class MX_OT_camera_view(Operator):
    bl_idname = "mx.camera_view"
    bl_label = "View Through This Camera"
    bl_description = "Set this camera as the scene camera and switch the 3D view to camera view"

    uid: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return not STATE.active

    def execute(self, context):
        s = context.scene.mx
        item = find_by_uid(s.cameras, self.uid)
        if item is None or item.camera is None:
            return {'CANCELLED'}
        s.cam_index = list(s.cameras).index(item)      # Select this entry so the viewport overlay shows its render frame
        context.scene.camera = item.camera
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.region_3d.view_perspective = 'CAMERA'
                area.tag_redraw()
                break
        return {'FINISHED'}


# ================================================================ Models

class MX_OT_variant_add(_IdleOperator, Operator):
    bl_idname = "mx.variant_add"
    bl_label = "Add Model"
    bl_options = {'UNDO'}

    def execute(self, context):
        s = context.scene.mx
        v = s.variants.add()
        v.uid = new_uid()
        v.name = f"Model {len(s.variants)}"
        s.var_index = len(s.variants) - 1
        sync_refs(s)
        sync_cells(s)
        return {'FINISHED'}


class MX_OT_variant_remove(_IdleOperator, Operator):
    bl_idname = "mx.variant_remove"
    bl_label = "Remove Model"
    bl_options = {'UNDO'}

    def execute(self, context):
        s = context.scene.mx
        if 0 <= s.var_index < len(s.variants):
            s.variants.remove(s.var_index)
            s.var_index = min(s.var_index, len(s.variants) - 1)
            sync_cells(s)
        return {'FINISHED'}


class MX_OT_variant_duplicate(_IdleOperator, Operator):
    bl_idname = "mx.variant_duplicate"
    bl_label = "Duplicate Model"
    bl_description = "Duplicate the current model (including collection toggles, hidden object setting and matrix toggles)"
    bl_options = {'UNDO'}

    def execute(self, context):
        s = context.scene.mx
        if not (0 <= s.var_index < len(s.variants)):
            return {'CANCELLED'}
        src = s.variants[s.var_index]
        src_uid = src.uid
        snapshot = [(r.collection, r.enabled) for r in src.refs]
        name, enabled, mode = src.name, src.enabled, src.hidden_mode

        v = s.variants.add()                 # The src reference may become invalid after add(), so read the data first
        v.uid = new_uid()
        v.name, v.enabled, v.hidden_mode = f"{name} Copy", enabled, mode
        for coll, on in snapshot:
            r = v.refs.add()
            r.collection, r.enabled = coll, on
        sync_refs(s)
        sync_cells(s)
        cmap = cell_map(s)
        for cam in s.cameras:
            old = cmap.get((cam.uid, src_uid))
            new = cmap.get((cam.uid, v.uid))
            if old and new:
                new.enabled = old.enabled
        new_index = len(s.variants) - 1
        s.variants.move(new_index, s.var_index + 1)
        s.var_index += 1
        return {'FINISHED'}


def _active_variant(s):
    return s.variants[s.var_index] if 0 <= s.var_index < len(s.variants) else None


class MX_OT_variant_apply_to_all(_IdleOperator, Operator):
    bl_idname = "mx.variant_apply_to_all"
    bl_label = "Apply to Other Models"
    bl_description = "Overwrite all other models with this model's hidden object setting (optionally including collection toggles)"
    bl_options = {'UNDO'}

    include_collections: BoolProperty(name="Include Collection Toggles", default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        s = context.scene.mx
        src = _active_variant(s)
        if src is None:
            return {'CANCELLED'}
        src_uid, mode = src.uid, src.hidden_mode
        checked = {r.collection for r in src.refs if r.enabled}
        if self.include_collections:
            sync_refs(s)                         # Sync each model's collection records first so every collection can be set
        for v in s.variants:
            if v.uid == src_uid:
                continue
            v.hidden_mode = mode
            if self.include_collections:
                for r in v.refs:
                    r.enabled = r.collection in checked
        return {'FINISHED'}


class MX_OT_variant_select(_IdleOperator, Operator):
    bl_idname = "mx.variant_select"
    bl_label = "Select Model Collections"
    bl_description = "Select objects in all collections checked in this model and make the first checked collection active"
    bl_options = {'UNDO'}

    uid: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        s = context.scene.mx
        v = find_by_uid(s.variants, self.uid)
        if v is None:
            return {'CANCELLED'}
        s.var_index = list(s.variants).index(v)
        colls = [r.collection for r in v.refs if r.enabled and r.collection is not None]
        vl = context.view_layer

        for o in vl.objects:
            o.select_set(False)
        selected, seen = [], set()
        for coll in colls:
            for o in coll.all_objects:
                if o.name in seen:
                    continue
                seen.add(o.name)
                try:
                    o.select_set(True)          # Objects not in the current view layer (their collection is excluded) cannot be selected
                except RuntimeError:
                    continue
                selected.append(o)
        vl.objects.active = selected[0] if selected else None

        # Make the first checked collection active (highlighted in the Outliner)
        if colls:
            for lc, _path, _parents in iter_lc(vl.layer_collection):
                if lc.collection == colls[0]:
                    vl.active_layer_collection = lc
                    break

        self.report({'INFO'}, f"Selected {len(selected)} object(s) in {len(colls)} collection(s)")
        return {'FINISHED'}


class MX_OT_variant_refs_clear(_IdleOperator, Operator):
    bl_idname = "mx.variant_refs_clear"
    bl_label = "Clear Toggles"
    bl_description = "Uncheck all collections in the current model"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        v = _active_variant(context.scene.mx)
        if v is not None:
            for r in v.refs:
                r.enabled = False
        return {'FINISHED'}


# ================================================================ Matrix toggles

class MX_OT_matrix_set(_IdleOperator, Operator):
    bl_idname = "mx.matrix_set"
    bl_label = "Set Matrix Toggles"
    bl_description = "Toggle a whole row / column / everything"
    bl_options = {'UNDO', 'INTERNAL'}

    mode: EnumProperty(items=[
        ('ALL_ON', "Select All", ""),
        ('ALL_OFF', "Select None", ""),
        ('INVERT', "Invert", ""),
        ('ROW', "Toggle Camera Row", ""),
        ('COL', "Toggle Model Column", ""),
    ])
    uid: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        s = context.scene.mx
        sync_cells(s)
        cells = list(s.cells)
        if self.mode == 'ALL_ON':
            for c in cells:
                c.enabled = True
        elif self.mode == 'ALL_OFF':
            for c in cells:
                c.enabled = False
        elif self.mode == 'INVERT':
            for c in cells:
                c.enabled = not c.enabled
        else:
            attr = "cam_uid" if self.mode == 'ROW' else "var_uid"
            group = [c for c in cells if getattr(c, attr) == self.uid]
            value = not all(c.enabled for c in group)
            for c in group:
                c.enabled = value
        return {'FINISHED'}


classes = (
    MX_OT_run_matrix,
    MX_OT_restore_saved,
    MX_OT_open_output,
    MX_OT_sync,
    MX_OT_item_move,
    MX_OT_camera_add,
    MX_OT_camera_add_all,
    MX_OT_camera_remove,
    MX_OT_camera_duplicate,
    MX_OT_camera_copy_settings,
    MX_OT_camera_view,
    MX_OT_variant_add,
    MX_OT_variant_remove,
    MX_OT_variant_duplicate,
    MX_OT_variant_apply_to_all,
    MX_OT_variant_select,
    MX_OT_variant_refs_clear,
    MX_OT_matrix_set,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
