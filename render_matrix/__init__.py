"""Feature: Render Matrix - asynchronously batch-render a "camera x model" matrix in the current Blender session."""

if "props" in locals():
    import importlib
    # Reload in dependency order; modules added in newer versions may not have been imported yet, skip them
    for _name in ("props", "prefs", "visibility", "runner", "operators", "ui", "overlay"):
        if _name in locals():
            importlib.reload(locals()[_name])

from . import props, prefs, visibility, runner, operators, ui, overlay  # noqa: E402,F401

# Used by the Studio root add-on's preferences: (property name, PropertyGroup, title, draw function)
PREFERENCES = ("render_matrix", prefs.MX_Preferences, "Render Matrix", prefs.draw)


def register():
    props.register()
    prefs.register()
    operators.register()
    ui.register()
    overlay.register()
    runner.register()


def unregister():
    runner.unregister()
    overlay.unregister()
    ui.unregister()
    operators.unregister()
    prefs.unregister()
    props.unregister()
