"""Render Matrix add-on preferences (shown in Preferences > Add-ons > Studio, stored in user preferences, independent of the .blend file).

Preferences cannot reference data-blocks in a .blend directly, so the World is stored by name and looked up by name in the current file at render time.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup

from .props import ENGINES, FILE_FORMATS, ORDERS, redraw_views

# Module name of the Studio root add-on (e.g. bl_ext.user_default.studio); preferences live under it
ROOT_PACKAGE = __package__.rpartition(".")[0]


class MX_Preferences(PropertyGroup):
    # ---- Output
    output_dir: StringProperty(
        name="Output Directory", default="//render/", subtype='DIR_PATH',
        description="Directory to save render results; a leading // means relative to the current .blend file",
    )
    file_format: EnumProperty(name="Output Format", items=FILE_FORMATS, default='PNG')
    name_template: StringProperty(
        name="Naming Template", default="{camera}__{variant}",
        description="Available variables: {camera} (camera entry name) {camera_object} {variant} {res} {scene} {frame} {index} "
                    "{date} {time}; use / for subdirectories",
    )
    skip_existing: BoolProperty(name="Skip Existing Files", default=False)
    order: EnumProperty(name="Render Order", items=ORDERS, default='VARIANT')

    # ---- Render
    engine: EnumProperty(name="Render Engine", items=ENGINES, default='KEEP')
    single_layer: BoolProperty(
        name="Render Current View Layer Only", default=True,
        description="Visibility is computed for the current view layer; rendering only this layer is recommended",
    )
    use_world: BoolProperty(name="Override World", default=False)
    world_name: StringProperty(name="World", description="Look up the World by name in the current file")
    use_exposure: BoolProperty(name="Override Exposure", default=False)
    exposure: FloatProperty(name="Exposure", default=0.0, soft_min=-10.0, soft_max=10.0)
    use_frame: BoolProperty(name="Specific Frame", default=False)
    frame: IntProperty(name="Frame", default=1)

    # ---- Viewport render frame
    show_frame_overlay: BoolProperty(
        name="Show Render Frame in Camera View", default=True, update=redraw_views,
        description="Overlay the actual render frame in camera view using the current camera entry's resolution (file settings are not modified)",
    )
    overlay_all: BoolProperty(
        name="Show Other Entries of Same Camera", default=True, update=redraw_views,
        description="Also draw render frames of other camera entries using the same camera object as thin gray lines",
    )
    overlay_shade: FloatProperty(
        name="Outside Mask", default=0.5, min=0.0, max=1.0, subtype='FACTOR', update=redraw_views,
        description="Darken the area outside the current entry's render frame",
    )


class _Defaults:
    """Defaults used when the add-on is not enabled via preferences (e.g. imported directly for debugging)."""
    output_dir = "//render/"
    file_format = 'PNG'
    name_template = "{camera}__{variant}"
    skip_existing = False
    order = 'VARIANT'
    engine = 'KEEP'
    single_layer = True
    use_world = False
    world_name = ""
    use_exposure = False
    exposure = 0.0
    use_frame = False
    frame = 1
    show_frame_overlay = True
    overlay_all = True
    overlay_shade = 0.5


def get(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(ROOT_PACKAGE)
    p = getattr(addon.preferences, "render_matrix", None) if addon else None
    return p if p is not None else _Defaults


# ---------------------------------------------------------------- Drawing

def _toggle_row(layout, p, use_prop, value_prop, heading, search=None):
    col = layout.column(heading=heading)
    row = col.row(align=True)
    row.prop(p, use_prop, text="")
    sub = row.row()
    sub.active = getattr(p, use_prop)
    if search:
        sub.prop_search(p, value_prop, bpy.data, search, text="")
    else:
        sub.prop(p, value_prop, text="")


def draw(layout, p):
    layout.use_property_split = True
    layout.use_property_decorate = False

    col = layout.column()
    col.label(text="Output", icon='FILE_FOLDER')
    col.prop(p, "output_dir")
    col.prop(p, "file_format")
    col.prop(p, "name_template")
    col.prop(p, "skip_existing")
    col.prop(p, "order")
    col.operator("mx.open_output", icon='FILE_FOLDER')

    layout.separator()
    col = layout.column()
    col.label(text="Render", icon='RENDER_STILL')
    col.prop(p, "engine")
    col.prop(p, "single_layer")
    _toggle_row(layout, p, "use_world", "world_name", "World", search="worlds")
    _toggle_row(layout, p, "use_exposure", "exposure", "Exposure")
    _toggle_row(layout, p, "use_frame", "frame", "Frame")

    layout.separator()
    col = layout.column(heading="Viewport Render Frame")
    col.prop(p, "show_frame_overlay", text="Show")
    sub = col.column()
    sub.active = p.show_frame_overlay
    sub.prop(p, "overlay_all")
    sub.prop(p, "overlay_shade", slider=True)


classes = (
    MX_Preferences,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
