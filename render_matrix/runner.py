"""Render runner: run state, applying scene settings, and the cell-by-cell state machine.

Rendering runs in Blender's render job thread via bpy.ops.render.render('INVOKE_DEFAULT', write_still=True),
so the UI thread is not blocked. The modal operator's timer calls step() on the main thread to poll whether the
render job has finished and the output file has been written.

Note: do not register render callbacks such as render_stats / render_complete. They are called from the render
thread, and running Python there crashes Blender (segfault).
"""

import os
import re
import time

import bpy
from bpy.app.handlers import persistent

from . import visibility as V
from . import prefs
from .props import FILE_EXTENSIONS, camera_label, cell_map, find_by_uid, sync_all

RESTORE_KEY = "mx_restore"
_BAD_CHARS = re.compile(r'[\\/:*?"<>|#]')


class RunState:
    def __init__(self):
        self.message = ""
        self.completed = False         # Whether the last run fully completed (not cancelled / interrupted)
        self.reset()

    def reset(self):
        self.active = False
        self.phase = 'IDLE'            # IDLE / NEXT / RENDERING
        self.scene_name = ""
        self.view_layer_name = ""
        self.tasks = []                # [(cam_uid, var_uid)]
        self.index = 0
        self.seen_running = False      # Whether the current cell's render job has been observed running
        self.cell_started_at = 0.0
        self.current_label = ""
        self.current_output = ""
        self.rec = None
        self.plan = None
        self.warnings = []
        self.errors = []
        self.outputs = []
        self.skipped = 0
        self.started_at = 0.0
        self.stamp = time.localtime()


STATE = RunState()


# ---------------------------------------------------------------- File handlers: write restore info on save

@persistent
def _on_save_pre(*_args):
    if STATE.active and STATE.rec:
        scene = bpy.data.scenes.get(STATE.scene_name)
        if scene is not None:
            scene[RESTORE_KEY] = STATE.rec.to_json()


@persistent
def _on_save_post(*_args):
    if STATE.active:
        scene = bpy.data.scenes.get(STATE.scene_name)
        if scene is not None and RESTORE_KEY in scene:
            del scene[RESTORE_KEY]


@persistent
def _on_load_pre(*_args):
    if STATE.active:
        STATE.reset()
        STATE.message = "A new file was opened; Render Matrix was interrupted"


_FILE_HANDLERS = (
    ("save_pre", _on_save_pre),
    ("save_post", _on_save_post),
    ("load_pre", _on_load_pre),
)


# ---------------------------------------------------------------- Applying settings

def _set_engine(rec, scene, engine):
    if engine == 'KEEP':
        return
    candidates = {
        'CYCLES': ('CYCLES',),
        'EEVEE': ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'),
        'WORKBENCH': ('BLENDER_WORKBENCH',),
    }[engine]
    for eid in candidates:
        try:
            rec.set(V.k_scene(scene, "render"), "engine", eid)
            return
        except TypeError:
            continue
    raise RuntimeError(f"Render engine {engine} is not supported by this Blender version")


def apply_globals(rec, scene, vl, p):
    """Apply global settings from the add-on preferences. Returns a list of warnings (Worlds not found by name)."""
    warnings = []
    render = V.k_scene(scene, "render")
    _set_engine(rec, scene, p.engine)
    rec.set(render, "use_file_extension", True)
    if p.single_layer:
        rec.set(render, "use_single_layer", True)
    if p.use_world:
        world = bpy.data.worlds.get(p.world_name)
        if world is None:
            warnings.append(f"World \"{p.world_name}\" not found in the current file; World not overridden")
        else:
            rec.set(V.k_scene(scene), "world", world)
    if p.use_exposure:
        rec.set(V.k_scene(scene, "view_settings"), "exposure", p.exposure)
    if p.use_frame:
        rec.set(V.k_scene(scene), "frame_current", p.frame)
    rec.set(V.K_PREF_VIEW, "render_display_type", 'NONE')     # Don't open a render window
    return warnings


def apply_camera(rec, scene, cam):
    render = V.k_scene(scene, "render")
    img = V.k_scene(scene, "render.image_settings")
    rec.set(V.k_scene(scene), "camera", cam.camera)
    rec.set(render, "resolution_x", cam.res_x)
    rec.set(render, "resolution_y", cam.res_y)
    rec.set(render, "resolution_percentage", cam.res_pct)
    rec.set(render, "film_transparent", cam.film_transparent)
    file_format = prefs.get().file_format
    rec.set(img, "file_format", file_format)
    alpha = cam.film_transparent and file_format != 'JPEG'
    rec.set(img, "color_mode", 'RGBA' if alpha else 'RGB')
    if cam.use_samples:
        engine = scene.render.engine
        if engine == 'CYCLES':
            rec.set(V.k_scene(scene, "cycles"), "samples", cam.samples)
        elif "EEVEE" in engine:
            rec.set(V.k_scene(scene, "eevee"), "taa_render_samples", cam.samples)


def _clean(name):
    return _BAD_CHARS.sub("_", name).strip() or "_"


def _tokens(scene, cam_label, cam_object, var_name, res, index, stamp):
    return dict(
        camera=_clean(cam_label),
        camera_object=_clean(cam_object),
        variant=_clean(var_name),
        res=res,
        scene=_clean(scene.name),
        frame=scene.frame_current,
        index=index + 1,
        date=time.strftime("%Y%m%d", stamp),
        time=time.strftime("%H%M%S", stamp),
    )


def output_base(scene, cam, var, index, stamp):
    """Return the output path without extension (Blender adds the extension automatically)."""
    res = f"{cam.res_x * cam.res_pct // 100}x{cam.res_y * cam.res_pct // 100}"
    tokens = _tokens(scene, camera_label(cam), cam.camera.name, var.name, res, index, stamp)
    rel = prefs.get().name_template.format(**tokens)
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return os.path.join(bpy.path.abspath(prefs.get().output_dir), *parts)


def output_extension():
    return FILE_EXTENSIONS.get(prefs.get().file_format, "")


def validate(scene, s):
    p = prefs.get()
    if p.output_dir.startswith("//") and not bpy.data.filepath:
        return "Output directory is a relative path (//); save the .blend file first or use an absolute path"
    try:
        p.name_template.format(**_tokens(scene, "cam", "obj", "var", "1x1", 0, time.localtime()))
    except (KeyError, IndexError, ValueError) as e:
        return f"Invalid naming template: {e}"
    return None


def find_output_conflicts(scene, s, tasks):
    """Return cells that would write to the same file: [(path, [cell name, ...]), ...]."""
    stamp = time.localtime()
    by_path = {}
    for i, (cam_uid, var_uid) in enumerate(tasks):
        cam = find_by_uid(s.cameras, cam_uid)
        var = find_by_uid(s.variants, var_uid)
        path = output_base(scene, cam, var, i, stamp) + output_extension()
        by_path.setdefault(os.path.normcase(path), []).append(f"{camera_label(cam)} × {var.name}")
    return [(p, labels) for p, labels in by_path.items() if len(labels) > 1]


def build_tasks(s):
    cmap = cell_map(s)
    cams = [c for c in s.cameras if c.enabled and c.camera]
    vars_ = [v for v in s.variants if v.enabled]

    def on(c, v):
        cell = cmap.get((c.uid, v.uid))
        return cell is None or cell.enabled

    if prefs.get().order == 'VARIANT':
        return [(c.uid, v.uid) for v in vars_ for c in cams if on(c, v)]
    return [(c.uid, v.uid) for c in cams for v in vars_ if on(c, v)]


# ---------------------------------------------------------------- State machine

def progress_factor():
    n = len(STATE.tasks)
    if not n:
        return 0.0
    return min(1.0, STATE.index / n)


def fmt_seconds(sec):
    if sec is None:
        return "--:--"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def start(context):
    """Initialize a matrix render. Returns None on success, or an error message on failure (scene is left unmodified)."""
    if STATE.active:
        return "Render Matrix is already running"
    if bpy.app.is_job_running('RENDER'):
        return "Another render job is in progress"
    scene, vl = context.scene, context.view_layer
    s = scene.mx
    sync_all(s)
    err = validate(scene, s)
    if err:
        return err
    tasks = build_tasks(s)
    if not tasks:
        return "No cells to render: check cameras, models and matrix toggles"
    conflicts = find_output_conflicts(scene, s, tasks)
    if conflicts:
        path, labels = conflicts[0]
        return (f"{len(conflicts)} group(s) of cells would write to the same file (e.g. {' / '.join(labels)} → "
                f"{os.path.basename(path)}). Rename the camera entries, or add {{res}} / {{index}} to the naming template")

    message = STATE.message
    STATE.reset()
    STATE.message = message
    STATE.scene_name, STATE.view_layer_name = scene.name, vl.name
    STATE.tasks = tasks
    STATE.rec = V.Recorder()
    STATE.plan = V.prepare(scene, vl, s)            # Must happen before any override
    STATE.warnings = list(STATE.plan.warnings)
    try:
        V.open_render_paths(STATE.rec, scene, vl, s, STATE.plan)
        STATE.warnings += apply_globals(STATE.rec, scene, vl, prefs.get(context))
    except Exception as e:
        STATE.rec.restore()
        STATE.reset()
        return f"Initialization failed: {e}"

    STATE.active = True
    STATE.phase = 'NEXT'
    STATE.started_at = time.time()
    STATE.stamp = time.localtime()
    STATE.message = ""
    return None


def _ensure_window(context, scene):
    win = context.window
    if win is None:
        return
    if win.scene != scene:
        win.scene = scene
    vl = scene.view_layers.get(STATE.view_layer_name)
    if vl is not None and win.view_layer != vl:
        win.view_layer = vl


def _start_next_cell(context, scene):
    s = scene.mx
    while STATE.index < len(STATE.tasks):
        cam_uid, var_uid = STATE.tasks[STATE.index]
        cam = find_by_uid(s.cameras, cam_uid)
        var = find_by_uid(s.variants, var_uid)
        if cam is None or var is None or cam.camera is None:
            STATE.errors.append(f"#{STATE.index + 1}: camera or model was deleted, skipped")
            STATE.index += 1
            continue
        label = f"{camera_label(cam)} × {var.name}"
        try:
            _ensure_window(context, scene)
            apply_camera(STATE.rec, scene, cam)
            V.apply_variant(STATE.rec, STATE.plan, var.uid)
            base = output_base(scene, cam, var, STATE.index, STATE.stamp)
            os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
            STATE.rec.set(V.k_scene(scene, "render"), "filepath", base)
            expected = base + output_extension()
            if prefs.get().skip_existing and os.path.exists(expected):
                STATE.skipped += 1
                STATE.index += 1
                continue

            STATE.current_label = label
            STATE.current_output = expected
            STATE.seen_running = False
            STATE.cell_started_at = time.time()
            result = bpy.ops.render.render('INVOKE_DEFAULT', write_still=True)
            if 'RUNNING_MODAL' not in result:
                raise RuntimeError(f"Render failed to start ({set(result)})")
            STATE.phase = 'RENDERING'
            return
        except Exception as e:
            STATE.phase = 'NEXT'
            STATE.errors.append(f"{label}: {e}")
            STATE.index += 1
    STATE.phase = 'NEXT'


def step(context):
    """Called by the modal operator's timer. Returns 'RUNNING' or 'FINISHED'."""
    if not STATE.active:
        return 'FINISHED'
    scene = bpy.data.scenes.get(STATE.scene_name)
    if scene is None:
        finish("Scene was deleted")
        return 'FINISHED'

    if STATE.phase == 'RENDERING':
        if bpy.app.is_job_running('RENDER'):
            STATE.seen_running = True
            return 'RUNNING'
        if not STATE.seen_running and time.time() - STATE.cell_started_at < 2.0:
            return 'RUNNING'           # The render job hasn't actually started yet
        # Render job finished: success if an output file was written since this cell started, otherwise treat as cancelled (Esc / status bar x) or failed
        if not _output_written():
            finish("Render cancelled")
            return 'FINISHED'
        STATE.outputs.append(STATE.current_output)
        STATE.index += 1
        STATE.phase = 'NEXT'

    if STATE.index >= len(STATE.tasks):
        finish("Done", completed=True)
        return 'FINISHED'
    if bpy.app.is_job_running('RENDER'):
        return 'RUNNING'               # Another render is running; wait
    _start_next_cell(context, scene)
    if STATE.phase == 'NEXT' and STATE.index >= len(STATE.tasks):
        finish("Done", completed=True)
        return 'FINISHED'
    return 'RUNNING'


def _output_written():
    path = STATE.current_output
    return os.path.exists(path) and os.path.getmtime(path) >= STATE.cell_started_at - 1.0


def finish(reason, completed=False):
    if STATE.rec is not None:
        STATE.errors.extend(f"Restore failed {x}" for x in STATE.rec.restore())
    scene = bpy.data.scenes.get(STATE.scene_name)
    if scene is not None and RESTORE_KEY in scene:
        del scene[RESTORE_KEY]
    elapsed = time.time() - STATE.started_at if STATE.started_at else 0
    STATE.message = (
        f"{reason}: rendered {len(STATE.outputs)}/{len(STATE.tasks)}"
        f", skipped {STATE.skipped}, errors {len(STATE.errors)}, time {fmt_seconds(elapsed)}"
    )
    STATE.completed = completed
    STATE.active = False
    STATE.phase = 'IDLE'
    STATE.rec = None
    STATE.plan = None


def restore_saved(scene):
    """Restore from scene[RESTORE_KEY] (used when a file saved mid-render is reopened)."""
    text = scene.get(RESTORE_KEY)
    if not text:
        return []
    failed = V.Recorder.from_json(text).restore()
    del scene[RESTORE_KEY]
    return failed


def tag_redraw(context):
    wm = context.window_manager if context else bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type in {'PROPERTIES', 'VIEW_3D'}:
                area.tag_redraw()


def register():
    for name, fn in _FILE_HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn not in lst:
            lst.append(fn)


def unregister():
    if STATE.active:
        finish("Add-on disabled")
    for name, fn in _FILE_HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        while fn in lst:
            lst.remove(fn)
