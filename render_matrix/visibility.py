"""Visibility computation and reversible property overrides.

Strategy:
1. prepare()           - Before any override, compute the set of objects each model renders from the original state.
                          Every object in the scene is managed: each model renders only its checked collections; everything else is hidden.
2. open_render_paths() - Done once: open the paths to checked collections (exclude / render-disabled),
                          after which the render result is determined solely by object-level hide_render.
3. apply_variant()     - Per cell, only change hide_render values that actually differ.
All overrides go through the Recorder and are restored in reverse order at the end.
"""

import json

import bpy

from .props import managed_collections


# ---------------------------------------------------------------- Locator keys

def k_obj(o):
    return ('OB', o.name)


def k_coll(c):
    return ('CO', c.name)


def k_scene(scene, sub=""):
    return ('SC', scene.name, sub)


def k_lc(scene, vl, path):
    return ('LC', scene.name, vl.name) + tuple(path)


K_PREF_VIEW = ('PREF_VIEW',)


def resolve(key):
    """Re-resolve the RNA object from its locator key. RNA references are not stored, since they can become invalid after a view layer resync."""
    kind = key[0]
    try:
        if kind == 'OB':
            return bpy.data.objects.get(key[1])
        if kind == 'CO':
            return bpy.data.collections.get(key[1])
        if kind == 'CA':        # May appear in restore data saved by older versions (where camera entries could override focal length)
            return bpy.data.cameras.get(key[1])
        if kind == 'SC':
            scene = bpy.data.scenes.get(key[1])
            if scene is None:
                return None
            return scene.path_resolve(key[2]) if key[2] else scene
        if kind == 'LC':
            scene = bpy.data.scenes.get(key[1])
            vl = scene.view_layers.get(key[2]) if scene else None
            lc = vl.layer_collection if vl else None
            for name in key[3:]:
                if lc is None:
                    return None
                lc = lc.children.get(name)
            return lc
        if kind == 'PREF_VIEW':
            return bpy.context.preferences.view
    except (ValueError, AttributeError, ReferenceError):
        return None
    return None


# ---------------------------------------------------------------- Recorder

def _encode(value):
    if isinstance(value, bpy.types.ID):
        return {"__id__": _ID_DATA.get(value.id_type), "name": value.name}
    return value


def _decode(value):
    if isinstance(value, dict) and "__id__" in value:
        data = getattr(bpy.data, value["__id__"] or "", None)
        return data.get(value["name"]) if data is not None else None
    return value


_ID_DATA = {
    'OBJECT': "objects",
    'COLLECTION': "collections",
    'WORLD': "worlds",
    'CAMERA': "cameras",
    'SCENE': "scenes",
}


class Recorder:
    """Record each property's original value before its first override; restore() reverts in reverse order (children before parents)."""

    def __init__(self):
        self.log = []          # [(key, attr, old_value)]
        self._seen = set()

    def set(self, key, attr, value):
        target = resolve(key)
        if target is None:
            return False
        tag = (key, attr)
        if tag not in self._seen:
            self._seen.add(tag)
            self.log.append((key, attr, getattr(target, attr)))
        if getattr(target, attr) != value:
            setattr(target, attr, value)
        return True

    def restore(self):
        failed = []
        for key, attr, old in reversed(self.log):
            try:
                target = resolve(key)
                if target is not None:
                    setattr(target, attr, old)
            except (ReferenceError, AttributeError, TypeError, ValueError) as e:
                failed.append(f"{key}.{attr}: {e}")
        self.log.clear()
        self._seen.clear()
        return failed

    def to_json(self):
        return json.dumps([[list(k), a, _encode(v)] for k, a, v in self.log], ensure_ascii=False)

    @classmethod
    def from_json(cls, text):
        rec = cls()
        for key, attr, value in json.loads(text):
            key = tuple(key)
            rec._seen.add((key, attr))
            rec.log.append((key, attr, _decode(value)))
        return rec


# ---------------------------------------------------------------- Traversal

def iter_lc(lc, path=(), parents=()):
    """Depth-first walk of the view layer collection tree (excluding root), yielding (layer_collection, name path, ((ancestor lc, ancestor path), ...))."""
    for ch in lc.children:
        p = path + (ch.name,)
        yield ch, p, parents
        yield from iter_lc(ch, p, parents + ((ch, p),))


def subtree(coll, out):
    if coll not in out:
        out.add(coll)
        for ch in coll.children:
            subtree(ch, out)
    return out


def find_lc_path(vl, coll):
    for lc, path, _parents in iter_lc(vl.layer_collection):
        if lc.collection == coll:
            return path
    return None


# ---------------------------------------------------------------- Hidden checks (must be called before any override)

def obj_hidden(o, mode, vl):
    if mode == 'INCLUDE':
        return False
    if o.hide_render:
        return True
    if mode == 'SKIP':
        if o.hide_viewport:
            return True
        try:
            return o.hide_get(view_layer=vl)
        except RuntimeError:       # Object is not currently in the view layer (its collection is excluded), so it has no eye state
            return False
    return False


def coll_hidden(lc, mode):
    if mode == 'INCLUDE':
        return False
    c = lc.collection
    if lc.exclude or c.hide_render:
        return True
    if mode == 'SKIP':
        return c.hide_viewport or lc.hide_viewport
    return False


def gather_visible(lc, mode, vl, out):
    """Collect objects in lc that should render under mode. lc's own hidden flags are ignored (the user checked it explicitly)."""
    for o in lc.collection.objects:
        if not obj_hidden(o, mode, vl):
            out.add(o.name)
    for ch in lc.children:
        if not coll_hidden(ch, mode):
            gather_visible(ch, mode, vl, out)


# ---------------------------------------------------------------- Render plan

class Plan:
    def __init__(self):
        self.managed_colls = set()     # Collection
        self.managed_objs = set()      # object names
        self.variant_vis = {}          # variant uid -> set of visible object names
        self.warnings = []


def prepare(scene, vl, s):
    plan = Plan()
    # All collections and objects in the scene (including objects under the scene root collection) are managed: unchecked means not rendered
    subtree(scene.collection, plan.managed_colls)
    plan.managed_colls.discard(scene.collection)
    plan.managed_objs = {o.name for o in scene.objects}

    lc_of = {}
    for lc, _path, _parents in iter_lc(vl.layer_collection):
        lc_of.setdefault(lc.collection, lc)

    for v in s.variants:
        vis = set()
        for ref in v.refs:
            if not (ref.enabled and ref.collection):
                continue
            lc = lc_of.get(ref.collection)
            if lc is None:
                plan.warnings.append(f"[{v.name}] collection '{ref.collection.name}' is not linked to view layer '{vl.name}', ignored")
                continue
            gather_visible(lc, v.hidden_mode, vl, vis)
        plan.variant_vis[v.uid] = vis
        if v.enabled and not vis:
            plan.warnings.append(f"[{v.name}] no collections checked (or the checked collections have no renderable objects); the render will be empty")
    return plan


def open_render_paths(rec, scene, vl, s, plan):
    """Make the whole path of every checked collection (ancestors + itself + descendants) renderable;
    after opening previously blocking ancestors, hide their sibling collections and direct objects unrelated to the checked collections."""
    roots = set(managed_collections(s))
    to_open = {}                      # ordered: path -> lc (parents first)
    for lc, path, parents in iter_lc(vl.layer_collection):
        chain = [p[0].collection for p in parents] + [lc.collection]
        if any(c in roots for c in chain):
            for plc, ppath in parents:
                to_open.setdefault(ppath, plc)
            to_open.setdefault(path, lc)

    # Read all original state before overriding: changing exclude triggers a view layer resync, which can invalidate old lc references
    infos = []
    for path, lc in to_open.items():
        c = lc.collection
        infos.append((path, c, lc.exclude or c.hide_render, [ch.collection for ch in lc.children]))

    for path, c, was_blocked, children in infos:
        rec.set(k_lc(scene, vl, path), "exclude", False)
        rec.set(k_coll(c), "hide_render", False)
        if was_blocked and c not in plan.managed_colls:
            for ch in children:
                if path + (ch.name,) not in to_open:
                    rec.set(k_coll(ch), "hide_render", True)
            for o in c.objects:
                if o.name not in plan.managed_objs:
                    rec.set(k_obj(o), "hide_render", True)


def apply_variant(rec, plan, var_uid):
    vis = plan.variant_vis.get(var_uid, set())
    for name in plan.managed_objs:
        o = bpy.data.objects.get(name)
        if o is not None:
            rec.set(k_obj(o), "hide_render", name not in vis)
