"""Viewport overlay: in camera view, draw the actual render frame using the camera entry's custom resolution, without modifying any file settings.

How it works: in camera view, the viewport projection depends only on the camera's optical parameters, not on the
file's render resolution (resolution only affects Blender's built-in frame). So we compute the render frame corners in
camera local space from "camera parameters + entry aspect ratio", transform them to world space and project them to the
screen, which gives the entry's actual render area.
"""

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import prefs
from .props import camera_label

COLOR_ACTIVE = (1.0, 0.62, 0.15, 1.0)      # Currently selected camera entry
COLOR_OTHER = (0.85, 0.85, 0.85, 0.6)      # Other entries using the same camera object
LINE_WIDTH_ACTIVE = 2.0
LINE_WIDTH_OTHER = 1.0

_handle = None
_error_reported = False


# ---------------------------------------------------------------- Geometry

def item_resolution(item):
    """Actual output resolution of the entry (percentage already applied)."""
    return item.res_x * item.res_pct / 100.0, item.res_y * item.res_pct / 100.0


def frame_local(scene, item):
    """Four corners of the render frame in camera local space (bottom-left, bottom-right, top-right, top-left). Returns None for panoramic cameras.

    Consistent with Blender's BKE_camera_params_compute_viewplane:
    - Sensor fit: AUTO fits the larger side of the aspect ratio, otherwise the specified direction;
    - Perspective camera: half-width along the fit direction = sensor size / (2 x focal length), drawn at display_size depth;
    - Orthographic camera: half-width along the fit direction = ortho_scale / 2;
    - Lens shift is measured in units of the full width along the fit direction.
    """
    data = item.camera.data
    if data.type == 'PANO':
        return None
    rx, ry = item_resolution(item)
    render = scene.render
    aspect = (rx * render.pixel_aspect_x) / max(ry * render.pixel_aspect_y, 1e-6)

    if data.sensor_fit == 'AUTO':
        horizontal = aspect >= 1.0
    else:
        horizontal = data.sensor_fit == 'HORIZONTAL'

    depth = max(data.display_size, 1e-3)
    if data.type == 'ORTHO':
        half_fit = data.ortho_scale / 2.0
    else:
        sensor = data.sensor_height if data.sensor_fit == 'VERTICAL' else data.sensor_width
        half_fit = sensor / (2.0 * max(data.lens, 1e-6)) * depth

    if horizontal:
        hx, hy = half_fit, half_fit / aspect
    else:
        hx, hy = half_fit * aspect, half_fit

    sx = data.shift_x * 2.0 * half_fit
    sy = data.shift_y * 2.0 * half_fit
    return [Vector((sx + x, sy + y, -depth)) for x, y in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]


def frame_region(scene, item, region, rv3d):
    """Four 2D corners of the render frame projected into the viewport region; None if it cannot be projected."""
    local = frame_local(scene, item)
    if local is None:
        return None
    mw = item.camera.matrix_world.normalized()
    points = [location_3d_to_region_2d(region, rv3d, mw @ p) for p in local]
    if any(p is None for p in points):
        return None
    return points


# ---------------------------------------------------------------- Drawing

def _draw_lines(points, color, width):
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    coords = [(points[i].x, points[i].y, 0.0) for i in (0, 1, 1, 2, 2, 3, 3, 0)]
    batch = batch_for_shader(shader, 'LINES', {"pos": coords})
    shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    shader.uniform_float("lineWidth", width)
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_shade(points, region, alpha):
    """Darken the area outside the render frame (in camera view the frame is always screen-axis aligned)."""
    if alpha <= 0.0:
        return
    w, h = region.width, region.height
    x0 = max(0.0, min(p.x for p in points))
    x1 = min(w, max(p.x for p in points))
    y0 = max(0.0, min(p.y for p in points))
    y1 = min(h, max(p.y for p in points))
    rects = (
        (0, 0, w, y0),          # bottom
        (0, y1, w, h),          # top
        (0, y0, x0, y1),        # left
        (x1, y0, w, y1),        # right
    )
    verts = []
    for ax, ay, bx, by in rects:
        if bx > ax and by > ay:
            verts += [(ax, ay, 0.0), (bx, ay, 0.0), (bx, by, 0.0),
                      (ax, ay, 0.0), (bx, by, 0.0), (ax, by, 0.0)]
    if not verts:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": verts})
    shader.uniform_float("color", (0.0, 0.0, 0.0, alpha))
    batch.draw(shader)


def _draw_label(points, text, color, ui_scale):
    font = 0
    blf.size(font, 11 * ui_scale)
    blf.color(font, *color)
    blf.enable(font, blf.SHADOW)
    blf.shadow(font, 3, 0.0, 0.0, 0.0, 0.8)
    blf.shadow_offset(font, 1, -1)
    x = min(p.x for p in points)
    y = max(p.y for p in points) + 6 * ui_scale
    blf.position(font, x, y, 0)
    blf.draw(font, text)
    blf.disable(font, blf.SHADOW)


def _draw_impl(context):
    scene = context.scene
    region = context.region
    rv3d = context.region_data
    if scene is None or region is None or rv3d is None or not hasattr(scene, "mx"):
        return
    s = scene.mx
    p = prefs.get(context)
    if not p.show_frame_overlay or rv3d.view_perspective != 'CAMERA':
        return
    cam_obj = scene.camera
    if cam_obj is None:
        return

    items = [c for c in s.cameras if c.camera == cam_obj]
    if not items:
        return
    active = s.cameras[s.cam_index] if 0 <= s.cam_index < len(s.cameras) else None
    if active not in items:
        active = None
    others = [c for c in items if c != active] if p.overlay_all else []
    if active is None and not others:
        return

    ui_scale = context.preferences.system.ui_scale
    gpu.state.blend_set('ALPHA')
    try:
        active_pts = frame_region(scene, active, region, rv3d) if active is not None else None
        if active_pts is not None:
            _draw_shade(active_pts, region, p.overlay_shade)
        for item in others:
            pts = frame_region(scene, item, region, rv3d)
            if pts is not None:
                _draw_lines(pts, COLOR_OTHER, LINE_WIDTH_OTHER)
        if active_pts is not None:
            _draw_lines(active_pts, COLOR_ACTIVE, LINE_WIDTH_ACTIVE)
            rx, ry = item_resolution(active)
            _draw_label(active_pts, f"{camera_label(active)}  {int(rx)}×{int(ry)}", COLOR_ACTIVE, ui_scale)
    finally:
        gpu.state.blend_set('NONE')


def _draw_callback():
    global _error_reported
    try:
        _draw_impl(bpy.context)
    except Exception as e:      # The draw callback runs every frame; report errors only once to avoid flooding the console
        if not _error_reported:
            _error_reported = True
            print(f"[Studio] Render frame overlay draw failed: {e!r}")


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw_callback, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
