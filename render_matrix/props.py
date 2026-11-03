"""Data model: cameras, models, matrix cells, global settings. All stored in Scene.mx and saved with the .blend."""

import uuid

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup


def new_uid():
    return uuid.uuid4().hex[:12]


def _poll_camera(self, obj):
    return obj.type == 'CAMERA'


def redraw_views(self, context):
    """Redraw 3D views when a property changes so the render frame overlay updates immediately."""
    wm = context.window_manager if context else bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


HIDDEN_MODES = [
    ('KEEP', "Keep Original", "Only respect render visibility (camera icon) and collection exclusion, matching Blender's default render result"),
    ('SKIP', "Skip Hidden Objects", "Objects and child collections hidden in the viewport (eye / monitor icon) are not rendered either; what you see is what you get"),
    ('INCLUDE', "Render Hidden Objects", "Force-render everything in the collection, including hidden or excluded objects and child collections"),
]

FILE_FORMATS = [
    ('PNG', "PNG", ""),
    ('JPEG', "JPEG", ""),
    ('OPEN_EXR', "OpenEXR", ""),
    ('TIFF', "TIFF", ""),
]

FILE_EXTENSIONS = {'PNG': ".png", 'JPEG': ".jpg", 'OPEN_EXR': ".exr", 'TIFF': ".tif"}

ENGINES = [
    ('KEEP', "Keep Scene Setting", "Do not change the render engine"),
    ('CYCLES', "Cycles", ""),
    ('EEVEE', "EEVEE", ""),
    ('WORKBENCH', "Workbench", ""),
]

ORDERS = [
    ('VARIANT', "By Model", "Outer loop over models; all cameras of a model render consecutively, minimizing visibility changes (recommended)"),
    ('CAMERA', "By Camera", "Outer loop over cameras; all models of a camera render consecutively"),
]


class MX_CameraItem(PropertyGroup):
    # The built-in name property is the entry name (used for {camera} and UI display); one camera object can have multiple entries with different names
    uid: StringProperty()
    enabled: BoolProperty(name="Enabled", default=True)
    camera: PointerProperty(name="Camera", type=bpy.types.Object, poll=_poll_camera, update=redraw_views)
    res_x: IntProperty(name="Width", default=1920, min=4, max=65536, subtype='PIXEL', update=redraw_views)
    res_y: IntProperty(name="Height", default=1080, min=4, max=65536, subtype='PIXEL', update=redraw_views)
    res_pct: IntProperty(name="Scale", default=100, min=1, max=100, subtype='PERCENTAGE', update=redraw_views)
    use_samples: BoolProperty(name="Override Samples", default=False)
    samples: IntProperty(name="Samples", default=128, min=1, max=1 << 24)
    film_transparent: BoolProperty(name="Transparent Background", default=False)


class MX_CollRef(PropertyGroup):
    """A model's toggle state for one scene collection. Each model keeps one record per scene collection (see sync_refs)."""
    collection: PointerProperty(name="Collection", type=bpy.types.Collection)
    enabled: BoolProperty(name="Render", default=False, description="Render this collection in this model")


class MX_Variant(PropertyGroup):
    uid: StringProperty()
    enabled: BoolProperty(name="Enabled", default=True)
    refs: CollectionProperty(type=MX_CollRef)
    ref_index: IntProperty()
    hidden_mode: EnumProperty(name="Hidden Objects", items=HIDDEN_MODES, default='KEEP')


class MX_Cell(PropertyGroup):
    cam_uid: StringProperty()
    var_uid: StringProperty()
    enabled: BoolProperty(name="Render", default=True)


class MX_Settings(PropertyGroup):
    cameras: CollectionProperty(type=MX_CameraItem)
    cam_index: IntProperty(update=redraw_views)
    variants: CollectionProperty(type=MX_Variant)
    var_index: IntProperty()
    cells: CollectionProperty(type=MX_Cell)


# ---------------------------------------------------------------- Helpers

def find_by_uid(coll, uid):
    for item in coll:
        if item.uid == uid:
            return item
    return None


def cell_map(s):
    return {(c.cam_uid, c.var_uid): c for c in s.cells}


def camera_label(item):
    """Display name of a camera entry: the entry name, or the camera object name if unnamed."""
    if item.name:
        return item.name
    return item.camera.name if item.camera else "(No Camera)"


def unique_camera_name(s, base, exclude=None):
    used = {c.name for c in s.cameras if c != exclude}
    if base not in used:
        return base
    n = 2
    while f"{base}_{n}" in used:
        n += 1
    return f"{base}_{n}"


def ensure_camera_names(s):
    """Assign unique names to unnamed or duplicate camera entries so output files don't overwrite each other."""
    seen = set()
    for c in s.cameras:
        base = c.name or (c.camera.name if c.camera else "Camera")
        if not c.name or c.name in seen:
            c.name = unique_camera_name(s, base, exclude=c)
        seen.add(c.name)


def managed_collections(s):
    """Collections checked in any model (deduplicated, in first-seen order), used to open render paths."""
    out = []
    for v in s.variants:
        for r in v.refs:
            if r.enabled and r.collection is not None and r.collection not in out:
                out.append(r.collection)
    return out


def scene_collections(scene):
    """All collections in the scene (excluding the root collection)."""
    return list(scene.collection.children_recursive)


def refs_out_of_sync(s):
    """Whether models' collection records are out of sync with the scene collections (lets the UI decide whether a sync is needed)."""
    colls = set(scene_collections(s.id_data))
    return any({r.collection for r in v.refs} != colls for v in s.variants)


def sync_refs(s):
    """Ensure each model has exactly one record per scene collection (new ones unchecked by default) and remove stale records."""
    colls = scene_collections(s.id_data)
    valid = set(colls)
    for v in s.variants:
        seen = set()
        for i in reversed(range(len(v.refs))):
            c = v.refs[i].collection
            if c is None or c not in valid or c in seen:
                v.refs.remove(i)
            else:
                seen.add(c)
        for c in colls:
            if c not in seen:
                v.refs.add().collection = c


def sync_cells(s):
    """Ensure every "camera x model" combination has exactly one cell."""
    cams = {c.uid for c in s.cameras}
    vars_ = {v.uid for v in s.variants}
    seen = set()
    for i in reversed(range(len(s.cells))):
        c = s.cells[i]
        key = (c.cam_uid, c.var_uid)
        if c.cam_uid not in cams or c.var_uid not in vars_ or key in seen:
            s.cells.remove(i)
        else:
            seen.add(key)
    for cu in cams:
        for vu in vars_:
            if (cu, vu) not in seen:
                cell = s.cells.add()
                cell.cam_uid, cell.var_uid = cu, vu


def sync_all(s):
    ensure_camera_names(s)
    sync_refs(s)
    sync_cells(s)


def needs_sync(s):
    """Lets the UI decide whether to show the "Refresh" button (data cannot be modified inside draw)."""
    return len(s.cells) != len(s.cameras) * len(s.variants)


classes = (
    MX_CameraItem,
    MX_CollRef,
    MX_Variant,
    MX_Cell,
    MX_Settings,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mx = PointerProperty(type=MX_Settings)


def unregister():
    del bpy.types.Scene.mx
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
