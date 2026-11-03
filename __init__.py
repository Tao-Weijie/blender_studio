"""Studio: studio production toolset. Each feature is a subpackage providing register() / unregister(), listed in FEATURES.

If a feature needs add-on preferences, define PREFERENCES = (property name, PropertyGroup, title, draw function) in the
subpackage and add a PointerProperty with the same name to StudioPreferences below.
"""

bl_info = {
    "name": "Studio",
    "author": "taomou",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D View sidebar > Studio",
    "description": "Studio production tools: render matrix and more",
    "category": "Render",
}

if "FEATURES" in locals():
    import importlib
    FEATURES = tuple(importlib.reload(m) for m in FEATURES)  # noqa: F821
else:
    from . import render_matrix
    FEATURES = (
        render_matrix,
    )

import bpy  # noqa: E402
from bpy.props import PointerProperty  # noqa: E402


class StudioPreferences(bpy.types.AddonPreferences):
    """Preferences > Add-ons > Studio. An add-on can have only one AddonPreferences, so each feature's settings are a section in it."""
    bl_idname = __package__

    render_matrix: PointerProperty(type=render_matrix.PREFERENCES[1])

    def draw(self, context):
        for feature in FEATURES:
            section = getattr(feature, "PREFERENCES", None)
            if section is None:
                continue
            attr, _cls, title, draw_fn = section
            box = self.layout.box()
            box.label(text=title)
            draw_fn(box, getattr(self, attr))


def register():
    for feature in FEATURES:
        feature.register()
    bpy.utils.register_class(StudioPreferences)      # Depends on the PropertyGroups registered by each feature


def unregister():
    bpy.utils.unregister_class(StudioPreferences)
    for feature in reversed(FEATURES):
        feature.unregister()
