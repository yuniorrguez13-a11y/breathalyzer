"""Tests for tools/quality.py. Every install, pack and mod here is made up, in temp folders: no game is started
(modman.run is replaced) and the real Savage Wind folder is never touched."""
import contextlib
import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import bnp  # noqa: E402
import modman as mm  # noqa: E402
import quality as q  # noqa: E402
import wiiu_convert as wc  # noqa: E402

BOOL, F32 = 0, 1
GRASS_FILE = b"made-up terrain index"
# Every lever away from the game's (as overrides on Original): builds both mods and turns Remove Grass on.
ALL = {"grass": 0.4, "objects": 0.6, "shadow_range": 600.0, "ssao": False, "bloom": False, "dof": False,
       "no_grass": True}


def h(name: str) -> int:
    return zlib.crc32(name.encode())


def make_aamp(objects: dict[str, dict[str, tuple[int, bytes]]], share: bool = True) -> bytes:
    """A small AAMP v2 file: one root list holding the objects. Equal values are stored once when share is True
    (as the game's files do)."""
    names = list(objects)
    params = [(o, p, t, v) for o in names for p, (t, v) in objects[o].items()]
    tname = b"xml\0"
    root_at = 0x30 + len(tname)
    obj_at = root_at + 12
    par_at = obj_at + 8 * len(names)
    data_at = par_at + 8 * len(params)
    data = bytearray()
    where: dict[bytes, int] = {}
    offs = []
    for _, _, _, v in params:
        if share and v in where:
            offs.append(where[v])
            continue
        where[v] = data_at + len(data)
        offs.append(where[v])
        data += v
    out = bytearray(b"AAMP" + struct.pack("<IIIII", 2, 3, 0, 0, len(tname)))
    out += struct.pack("<IIIIII", 1, len(names), len(params), len(data), 0, 0)
    out += tname
    out += struct.pack("<IHHHH", h("param_root"), 0, 0, (obj_at - root_at) // 4, len(names))
    k = 0
    for i, o in enumerate(names):
        here = obj_at + 8 * i
        out += struct.pack("<IHH", h(o), (par_at + 8 * k - here) // 4, len(objects[o]))
        k += len(objects[o])
    for i, (o, p, t, v) in enumerate(params):
        here = par_at + 8 * i
        out += struct.pack("<II", h(p), (t << 24) | (offs[i] - here) // 4)
    out += data
    struct.pack_into("<I", out, 12, len(out))
    return bytes(out)


def b32(v: bool) -> tuple[int, bytes]:
    return BOOL, struct.pack("<I", 1 if v else 0)


def f32(v: float) -> tuple[int, bytes]:
    return F32, struct.pack("<f", v)


def value(data: bytes, obj: str, param: str) -> bytes:
    t, off = q.aamp_params(data)[(h(obj), h(param))]
    return data[off:off + 4]


def env_aamps() -> dict[str, bytes]:
    shared = {"is_enable": b32(True), "other_switch": b32(True), "spare": b32(False)}
    return {
        "postfx/common.bgsdw": make_aamp({"ssao_parameter": dict(shared), "light": {"on": b32(True)}}),
        "postfx/d_frequentuse.bgsdw": make_aamp({"ssao_parameter": dict(shared)}),
        "postfx/master_field.baglblm": make_aamp({"bloom": {"enable": b32(True), "x": b32(False)}}),
        "postfx/d_frequentuse.baglblm": make_aamp({"bloom": {"enable": b32(True), "x": b32(False)}}),
        "postfx/master_field.bagldof": make_aamp({"dof": {"enable": b32(True), "y": b32(False)}}),
        "postfx/d_frequentuse.bagldof": make_aamp({"dof": {"enable": b32(True), "y": b32(False)}}),
        "postfx/ui.baglblm": make_aamp({"bloom": {"enable": b32(True), "x": b32(False)}}),
    }


def gsys_aamp() -> bytes:
    return make_aamp({"Main": {"depth_shadow_far_0": f32(3000.0), "depth_shadow_near_0_0": f32(2.0)},
                      "UI": {"depth_shadow_far_0": f32(6.0)}})


def make_pack(inner_name: str, inner_files: dict[str, bytes], others: dict[str, bytes]) -> bytes:
    inner = bnp.sarc_build([(n, d, 8) for n, d in inner_files.items()])
    items = [(inner_name, wc.yaz0_compress(inner, 0), 8)] + [(n, d, 4) for n, d in others.items()]
    return bnp.sarc_build(items)


def bootup(extra: bytes = b"made-up effect list") -> bytes:
    return make_pack("Env/env.sgenvb", env_aamps(), {"Effect/Made.sesetlist": extra})


def bootup_graphics() -> bytes:
    return make_pack("System/KSys/U-King.Nin_NX_NVN.release.ssarc", {"gsys.bgmsconf": gsys_aamp(),
                                                                     "gsys.other": b"x" * 40},
                     {"Shader/made_up.sbfsha": b"made-up shader"})


def write(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return root


def inner_file(pack: bytes, inner: str, name: str) -> bytes:
    return bnp.Blob(pack).child(inner).open()[name][0]


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


class Presets(unittest.TestCase):
    def test_every_preset_has_every_lever_in_range(self):
        for lvl in q.LEVELS:
            with self.subTest(level=lvl):
                self.assertEqual(set(q.PRESETS[lvl]), set(q.LEVERS))
                for k, v in q.PRESETS[lvl].items():
                    self.assertEqual(q.parse_value(k, v), v)
                self.assertEqual(set(q.SETTINGS[lvl]), set(q.SETTING_LABELS))

    def test_original_is_the_game(self):
        self.assertEqual(q.PRESETS["original"], {k: lv.original for k, lv in q.LEVERS.items()})
        self.assertEqual(q.env_for(q.PRESETS["original"]), {})

    def test_lower_presets_never_draw_more(self):
        order = ["low", "medium", "high", "original"]
        for k, lv in q.LEVERS.items():
            vals = [q.PRESETS[lvl][k] for lvl in order]
            with self.subTest(lever=k):
                if k == "no_grass":
                    self.assertEqual(vals, sorted(vals, reverse=True))   # (removing grass: low only)
                else:
                    self.assertEqual(vals, sorted(vals))

    def test_json_for_the_menu(self):
        data = json.loads(json.dumps(q.presets()))
        self.assertEqual(data["levels"], list(q.LEVELS))
        self.assertEqual(set(data["presets"]), set(q.LEVELS))
        self.assertEqual(data["levers"]["grass"]["min"], 0.25)
        self.assertIn("frame_rate", data["presets"]["low"]["savage_wind"])

    def test_parse_value(self):
        self.assertIs(q.parse_value("ssao", "off"), False)
        self.assertIs(q.parse_value("ssao", "ON"), True)
        self.assertEqual(q.parse_value("grass", "0.5"), 0.5)
        for lever, bad in (("grass", "2"), ("grass", "x"), ("ssao", "maybe"), ("shadow_range", "10"),
                           ("nope", "1"), ("objects", True)):
            with self.subTest(lever=lever, value=bad), self.assertRaises(q.QualityError):
                q.parse_value(lever, bad)

    def test_env(self):
        env = q.env_for({"grass": 0.4, "objects": 0.6})
        self.assertEqual(env, {"SAVAGEWIND_GRASS_BLADES": "0.4", "SAVAGEWIND_GRASS_QUADS": "0.4",
                               "SAVAGEWIND_TRAVERSE_SCALE": "0.6", "SAVAGEWIND_FAR_SCALE": "1"})
        self.assertEqual(set(env), set(q.ENV_KEYS))
        self.assertEqual(q.env_for({"grass": 1.0, "objects": 0.8}),
                         {"SAVAGEWIND_TRAVERSE_SCALE": "0.8", "SAVAGEWIND_FAR_SCALE": "1"})

    def test_recipes_name_known_levers(self):
        for folder, mod_id in q.own_ids().items():
            r = q.load_recipe(folder)
            self.assertEqual(r["id"], mod_id)
            for lever, edits in r["edits"].items():
                self.assertEqual(q.LEVERS[lever].kind, "build")
                self.assertEqual(q.LEVERS[lever].recipe, folder)
                self.assertTrue(edits)
            toml = (Path(q.recipe_dir(folder)) / "mod.toml").read_text(encoding="utf-8")
            man, errors = mm.parse_manifest(toml)
            self.assertEqual(errors, [])
            self.assertEqual(man.id, mod_id)

    def test_presets_ship_only_what_was_measured(self):
        # High is the game's look with no mod; no preset uses the unverified shadow mod or a shorter object
        # distance (a gap in forests, enemies only nearer: docs/performance.md); only Low replaces Bootup.pack.
        self.assertEqual(q.PRESETS["high"], q.PRESETS["original"])
        for lvl in q.LEVELS:
            with self.subTest(level=lvl):
                self.assertEqual(q.PRESETS[lvl]["shadow_range"], 3000.0)
                self.assertEqual(q.PRESETS[lvl]["objects"], 1.0)
                self.assertIs(q.PRESETS[lvl]["no_grass"], False)
                fx_off = not all(q.PRESETS[lvl][k] for k in ("ssao", "bloom", "dof"))
                self.assertEqual(fx_off, lvl == "low")
                self.assertEqual(fx_off, "Bootup.pack" in q.BLURBS[lvl])
        for lvl in ("low", "medium", "high"):
            self.assertEqual(q.SETTINGS[lvl]["upscaler_quality"], "ultra_performance")
        self.assertNotIn("Frame generation", q.SETTINGS_TIP)
        self.assertIn("Bootup.pack", q.presets()["bootup_note"])


class Borrowed(unittest.TestCase):
    def test_the_helpers_quality_uses_are_there(self):
        mods = {"bnp": bnp, "wiiu_convert": wc}
        for mod, names in q.BORROWED.items():
            for name in names:
                with self.subTest(helper=f"{mod}.{name}"):
                    self.assertTrue(hasattr(mods[mod], name))
        for name in ("child", "open", "set", "data"):
            self.assertTrue(callable(getattr(bnp.Blob, name, None)), name)
        inner = bnp.Blob(bootup()).child("Env/env.sgenvb")
        self.assertTrue(all(hasattr(inner, a) for a in ("raw", "align", "yaz0")))

    def test_a_changed_helper_is_one_clear_error(self):
        with mock.patch.object(q.bnp, "Blob", lambda data: object()), self.assertRaises(q.QualityError) as cm:
            q.build_pack(bootup(), q.load_recipe("perf_effects"), [])
        self.assertIn("quality.py needs updating", str(cm.exception))


class EditAamp(unittest.TestCase):
    def test_unshared_value_changes_in_place(self):
        data = gsys_aamp()
        new, n = q.edit_aamp(data, "Main", "depth_shadow_far_0", "f32", 600)
        self.assertEqual((n, len(new)), (1, len(data)))
        self.assertEqual(struct.unpack("<f", value(new, "Main", "depth_shadow_far_0"))[0], 600.0)
        self.assertEqual(value(new, "UI", "depth_shadow_far_0"), struct.pack("<f", 6.0))  # (another object)
        self.assertEqual(value(new, "Main", "depth_shadow_near_0_0"), struct.pack("<f", 2.0))

    def test_shared_value_is_pointed_elsewhere(self):
        data = make_aamp({"ssao_parameter": {"is_enable": b32(True), "other": b32(True), "spare": b32(False)}})
        self.assertEqual(q.aamp_params(data)[(h("ssao_parameter"), h("is_enable"))],
                         q.aamp_params(data)[(h("ssao_parameter"), h("other"))])   # (stored once)
        new, n = q.edit_aamp(data, "ssao_parameter", "is_enable", "bool", False)
        self.assertEqual((n, len(new)), (1, len(data)))
        self.assertEqual(value(new, "ssao_parameter", "is_enable"), bytes(4))
        self.assertEqual(value(new, "ssao_parameter", "other"), struct.pack("<I", 1))   # (unchanged)
        self.assertEqual(value(new, "ssao_parameter", "spare"), bytes(4))

    def test_shared_value_without_a_slot_is_refused(self):
        data = make_aamp({"Main": {"depth_shadow_far_0": f32(3000.0), "x": f32(3000.0)}})
        with self.assertRaises(q.QualityError):
            q.edit_aamp(data, "Main", "depth_shadow_far_0", "f32", 777)

    def test_same_value_and_wrong_type(self):
        data = gsys_aamp()
        self.assertEqual(q.edit_aamp(data, "Main", "depth_shadow_far_0", "f32", 3000)[0], data)
        self.assertEqual(q.edit_aamp(data, "Main", "nothing", "f32", 1)[1], 0)
        with self.assertRaises(q.QualityError):
            q.edit_aamp(data, "Main", "depth_shadow_far_0", "bool", False)
        with self.assertRaises(q.QualityError):
            q.edit_aamp(b"not aamp" * 8, "Main", "x", "bool", False)


class BuildPack(unittest.TestCase):
    def test_only_the_values_change(self):
        recipe = q.load_recipe("perf_effects")
        base = bootup()
        edits = q.recipe_edits(recipe, {"ssao": False, "bloom": True, "dof": False})
        self.assertEqual({e[0] for e in edits}, {"ssao", "dof"})
        pack, notes = q.build_pack(base, recipe, edits)
        self.assertEqual(len(notes), 4)
        a, b = bnp.Blob(base), bnp.Blob(pack)
        self.assertEqual(sorted(a.open()), sorted(b.open()))
        self.assertEqual(a.open()["Effect/Made.sesetlist"][0], b.open()["Effect/Made.sesetlist"][0])
        ia, ib = a.child("Env/env.sgenvb"), b.child("Env/env.sgenvb")
        self.assertTrue(ib.yaz0)
        self.assertEqual(len(ia.raw), len(ib.raw))   # (same size inside: no size table change)
        fa, fb = ia.open(), ib.open()
        for name in fa:
            da, db = fa[name][0], fb[name][0]
            self.assertEqual(len(da), len(db))
            for o, p, t, off, _ in q._walk_aamp(da):
                key = (o, p)
                got = db[q.aamp_params(db)[key][1]:][:4]
                expect = da[off:off + 4]
                if (name.endswith(".bgsdw") and key == (h("ssao_parameter"), h("is_enable"))) or \
                        (name in ("postfx/master_field.bagldof", "postfx/d_frequentuse.bagldof")
                         and key == (h("dof"), h("enable"))):
                    expect = bytes(4)
                with self.subTest(file=name, param=hex(p)):
                    self.assertEqual(got, expect)

    def test_shadow_range(self):
        recipe = q.load_recipe("perf_shadows")
        pack, _ = q.build_pack(bootup_graphics(), recipe, q.recipe_edits(recipe, {"shadow_range": 1000.0}))
        g = inner_file(pack, recipe["inner"], "gsys.bgmsconf")
        self.assertEqual(struct.unpack("<f", value(g, "Main", "depth_shadow_far_0"))[0], 1000.0)
        self.assertEqual(q.recipe_edits(recipe, {"shadow_range": 3000.0}), [])

    def test_missing_parts_are_refused(self):
        recipe = q.load_recipe("perf_effects")
        edits = q.recipe_edits(recipe, {"ssao": False})
        with self.assertRaises(q.QualityError):
            q.build_pack(bnp.sarc_build([("Other.txt", b"x", 4)]), recipe, edits)
        no_ssao = make_pack("Env/env.sgenvb", {"postfx/common.bgsdw": make_aamp({"x": {"y": b32(True)}}),
                                               "postfx/d_frequentuse.bgsdw": make_aamp({"x": {"y": b32(True)}})}, {})
        with self.assertRaises(q.QualityError):
            q.build_pack(no_ssao, recipe, edits)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.install = write(self.root / "Savage Wind", {
            "savagewind.exe": b"MZ made-up exe\0",
            "extracted/romfs/Pack/Bootup.pack": bootup(),
            "extracted/romfs/Pack/Bootup_Graphics.pack": bootup_graphics(),
        })
        self.lib = mm.Library(str(self.root / "modloader"), str(self.install))
        self.lib.save()
        self.grass = write(self.root / "downloads" / "no-grass", {
            "romfs/Terrain/A/MainField.tscb": GRASS_FILE,
            "readme.txt": "Remove Grass, repacked"})
        # (the made-up file stands for Remove Grass's: it is found by its SHA-256)
        known = mock.patch.object(q, "NO_GRASS_SHA256", frozenset({hashlib.sha256(GRASS_FILE).hexdigest()}))
        known.start()
        self.addCleanup(known.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def load(self) -> mm.Library:
        return mm.Library.load(self.lib.root)

    def mod(self, mod_id: str) -> dict | None:
        return next((m for m in self.load().mods if m["id"] == mod_id), None)

    def set(self, level: str, **kw) -> dict:
        with quiet():
            return q.set_quality(self.lib.root, level, **kw)

    def built_value(self, mod_id: str, pack: str, inner: str, name: str, obj: str, param: str) -> bytes:
        p = Path(self.lib.mods_dir, mod_id, "romfs", *pack.split("/"))
        return value(inner_file(p.read_bytes(), inner, name), obj, param)

    def test_every_lever_builds_and_turns_on(self):
        with quiet():
            mm.add_mod(self.lib, str(self.grass), name="Remove Grass")
        self.lib = self.load()
        mm.Library.load(self.lib.root)
        with quiet():
            mm.add_mod(self.lib, str(write(self.root / "downloads" / "other", {"romfs/Actor/x.txt": "x"})))
        self.lib = self.load()
        i, m = self.lib.find("remove-grass")
        m["enabled"] = False
        self.lib.save()
        r = self.set("original", overrides={**ALL, "no_grass": "on"})
        self.assertEqual((r["level"], r["base"]), ("custom", "original"))
        self.assertEqual(sorted(r["changes"]["built"]), ["Performance: effects off", "Performance: shorter shadows"])
        self.assertEqual(r["changes"]["on"], ["Remove Grass"])
        self.assertIn(q.BOOTUP_NOTE, r["changes"]["notes"])
        self.assertTrue(r["deploy_needed"])
        self.assertEqual(r["env"]["SAVAGEWIND_GRASS_BLADES"], "0.4")
        self.assertEqual(r["env"]["SAVAGEWIND_TRAVERSE_SCALE"], "0.6")
        ids = [m["id"] for m in self.load().mods]
        self.assertEqual(ids[-2:], ["botw-mod-loader.perf-effects", "botw-mod-loader.perf-shadows"])
        self.assertTrue(all(m["enabled"] for m in self.load().mods))
        self.assertEqual(self.built_value("botw-mod-loader.perf-shadows", "Pack/Bootup_Graphics.pack",
                                          "System/KSys/U-King.Nin_NX_NVN.release.ssarc", "gsys.bgmsconf", "Main",
                                          "depth_shadow_far_0"),
                         struct.pack("<f", 600.0))
        self.assertEqual(self.built_value("botw-mod-loader.perf-effects", "Pack/Bootup.pack", "Env/env.sgenvb",
                                          "postfx/master_field.baglblm", "bloom", "enable"), bytes(4))
        self.assertEqual(self.built_value("botw-mod-loader.perf-effects", "Pack/Bootup.pack", "Env/env.sgenvb",
                                          "postfx/ui.baglblm", "bloom", "enable"), struct.pack("<I", 1))
        st = json.loads(Path(self.lib.root, q.STATE).read_text(encoding="utf-8"))
        self.assertEqual((st["tool"], st["level"], st["overrides"]), (q.TOOL_ID, "custom", ALL))

    def test_original_turns_ours_off_and_back_without_a_rebuild(self):
        with quiet():
            mm.add_mod(self.lib, str(self.grass), name="Remove Grass")
        self.set("original", overrides=ALL)
        r = self.set("original")
        self.assertEqual(r["changes"]["built"], [])
        self.assertEqual(sorted(r["changes"]["off"]),
                         ["Performance: effects off", "Performance: shorter shadows", "Remove Grass"])
        self.assertEqual(r["env"], {})
        self.assertNotIn(q.BOOTUP_NOTE, r["changes"]["notes"])
        self.assertFalse(any(m["enabled"] for m in self.load().mods))
        r = self.set("original", overrides=ALL)
        self.assertEqual(r["changes"]["built"], [])   # (same edits, same base pack: the mods are kept)
        self.assertEqual(len(r["changes"]["on"]), 3)
        r = self.set("original", overrides=ALL)
        self.assertFalse(r["deploy_needed"])
        r = self.set("low")   # (the same effects: kept; no shadow mod, no Remove Grass)
        self.assertEqual(r["changes"]["built"], [])
        self.assertEqual(sorted(r["changes"]["off"]), ["Performance: shorter shadows", "Remove Grass"])

    def test_other_values_rebuild(self):
        self.set("original", overrides=ALL)
        r = self.set("original", overrides={**ALL, "bloom": True, "shadow_range": 1200})
        self.assertEqual(sorted(r["changes"]["built"]), ["Performance: effects off", "Performance: shorter shadows"])
        self.assertEqual(self.built_value("botw-mod-loader.perf-effects", "Pack/Bootup.pack", "Env/env.sgenvb",
                                          "postfx/master_field.baglblm", "bloom", "enable"), struct.pack("<I", 1))
        self.assertEqual(len([m for m in self.load().mods if m["id"].startswith("botw-mod-loader.perf-")]), 2)

    def test_no_grass_missing_is_a_note(self):
        r = self.set("low", overrides={"no_grass": True})
        self.assertTrue(any("Remove Grass isn't in the library" in n for n in r["changes"]["notes"]))
        r = self.set("medium")
        self.assertFalse(r["changes"]["notes"])

    def test_built_on_another_mods_pack(self):
        other = bootup(extra=b"another mod's effect list")
        with quiet():
            mm.add_mod(self.lib, str(write(self.root / "downloads" / "fx", {"romfs/Pack/Bootup.pack": other})),
                       name="Other effects")
        r = self.set("low")
        self.assertTrue(any("Other effects" in n for n in r["changes"]["notes"]))
        mods = self.load().mods
        self.assertEqual(mods[0]["name"], "Other effects")
        p = Path(self.lib.mods_dir, "botw-mod-loader.perf-effects", "romfs", "Pack", "Bootup.pack")
        self.assertEqual(bnp.Blob(p.read_bytes()).open()["Effect/Made.sesetlist"][0], b"another mod's effect list")

    def test_toggle_and_status(self):
        self.set("high")
        with quiet():
            r = q.toggle(self.lib.root, "ssao", "off")
        self.assertEqual((r["level"], r["base"]), ("custom", "high"))
        self.assertIs(r["levers"]["ssao"], False)
        s = q.status(self.lib.root)
        self.assertEqual(s["level"], "custom")
        self.assertIs(s["levers"]["ssao"], False)
        self.assertTrue(any(m["id"] == "botw-mod-loader.perf-effects" and m["enabled"] for m in s["mods"]))
        self.assertEqual(s["notes"], [q.BOOTUP_NOTE])
        with quiet():
            r = q.toggle(self.lib.root, "ssao", "on")   # (back to the preset's value: no override left)
        self.assertEqual(r["level"], "high")
        r = self.set("low")
        self.assertEqual(q.status(self.lib.root)["level"], "low")
        with self.assertRaises(q.QualityError):
            self.set("ultra")

    def test_only_low_replaces_a_game_file(self):
        for lvl in ("high", "medium", "original"):
            r = self.set(lvl)
            with self.subTest(level=lvl):
                self.assertEqual(r["changes"]["built"], [])
                self.assertNotIn(q.BOOTUP_NOTE, r["changes"]["notes"])
                self.assertFalse(any(m["enabled"] for m in self.load().mods))
                self.assertEqual(q.status(self.lib.root)["notes"], [])
        r = self.set("low")
        self.assertEqual(r["changes"]["built"], ["Performance: effects off"])
        self.assertIn(q.BOOTUP_NOTE, r["changes"]["notes"])
        self.assertIn(q.BOOTUP_NOTE, q.result_text(r))
        self.assertIsNone(self.mod("botw-mod-loader.perf-shadows"))

    def test_another_terrain_mod_is_left_alone(self):
        with quiet():
            mm.add_mod(self.lib, str(write(self.root / "downloads" / "terrain", {
                "romfs/Terrain/A/MainField.tscb": b"someone else's terrain index"})), name="Terrain fix")
        self.assertIsNone(q.find_role(self.load(), q.load_state(self.load()), "no_grass"))
        r = self.set("low", overrides={"no_grass": True})
        self.assertTrue(any("Remove Grass isn't in the library" in n for n in r["changes"]["notes"]))
        self.set("original")
        self.assertTrue(self.mod("terrain-fix")["enabled"])
        with quiet():
            mm.add_mod(self.lib, str(self.grass), name="Remove Grass")
        self.assertEqual(q.find_role(self.load(), q.load_state(self.load()), "no_grass")["name"], "Remove Grass")
        with mock.patch.object(q, "NO_GRASS_SHA256", frozenset()):   # (another file than the known one)
            self.assertIsNone(q.find_role(self.load(), q.load_state(self.load()), "no_grass"))

    def test_user_mods_are_left_alone(self):
        with quiet():
            mm.add_mod(self.lib, str(write(self.root / "downloads" / "other", {"romfs/Actor/x.txt": "x"})),
                       name="Mine")
        self.set("low")
        self.set("original")
        self.assertTrue(self.mod("mine")["enabled"])

    def test_run_passes_the_values(self):
        self.set("low", overrides={"objects": 0.6})
        seen = {}

        def fake_run(lib, port=None, no_cheats=False):
            seen.update({k: os.environ.get(k) for k in q.ENV_KEYS}, port=port, no_cheats=no_cheats)
            return 0

        with mock.patch.object(mm, "run", fake_run), mock.patch.dict(os.environ, {"SAVAGEWIND_FAR_SCALE": "3"}), \
                quiet():
            self.assertEqual(q.run(self.lib.root, port=7777, no_cheats=True), 0)
        self.assertEqual(seen["SAVAGEWIND_GRASS_QUADS"], f"{q.PRESETS['low']['grass']:g}")
        self.assertEqual(seen["SAVAGEWIND_FAR_SCALE"], "1")
        self.assertEqual((seen["port"], seen["no_cheats"]), (7777, True))
        self.set("original")
        with mock.patch.object(mm, "run", fake_run), mock.patch.dict(os.environ, {"SAVAGEWIND_FAR_SCALE": "3"}), \
                quiet():
            q.run(self.lib.root)
        self.assertIsNone(seen["SAVAGEWIND_FAR_SCALE"])   # (cleared: Savage Wind's own setting applies)

    def test_deploy_refused_keeps_the_preset(self):
        with mock.patch.object(mm, "deploy", side_effect=mm.ModmanError("Savage Wind is running.")):
            r = self.set("medium", deploy=True)
            self.assertEqual((r["deployed"], r["deploy_error"]), (False, "Savage Wind is running."))
            self.assertIn("Not deployed: Savage Wind is running.", q.result_text(r))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = q.main(["--library", self.lib.root, "set", "low", "--deploy"])
        self.assertEqual(code, 1)
        self.assertEqual(q.status(self.lib.root)["level"], "low")
        with mock.patch.object(mm, "deploy", return_value={"mods": [], "attention": [], "notes": []}),                 mock.patch.object(mm, "format_deploy", return_value="Deployed."):
            r = self.set("original", deploy=True)
        self.assertEqual((r["deployed"], r["deploy_text"]), (True, "Deployed."))

    def test_game_files_missing(self):
        os.remove(self.install / "extracted/romfs/Pack/Bootup.pack")
        with self.assertRaises(q.QualityError):
            self.set("low")
        self.assertEqual(self.set("original")["changes"]["built"], [])

    def test_no_library(self):
        with self.assertRaises(q.QualityError):
            q.set_quality(str(self.root / "nowhere"), "low")


class CommandLine(unittest.TestCase):
    def run_main(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = q.main(list(argv))
        return code, out.getvalue()

    def test_list_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            code, text = self.run_main("--library", d, "list")
        self.assertEqual(code, 0)
        for lvl in q.LEVELS:
            self.assertIn(q.LABELS[lvl], text)
        code, text = self.run_main("json")
        self.assertEqual(json.loads(text)["levels"], list(q.LEVELS))

    def test_set_without_library_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            code, text = self.run_main("--library", str(Path(d, "none")), "set", "low")
        self.assertEqual(code, 1)
        self.assertIn("No mod library", text)

    def test_bad_level(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            q.main(["set", "ultra"])

    def test_library_after_the_command(self):
        with tempfile.TemporaryDirectory() as d:
            code, text = self.run_main("status", "--library", str(Path(d, "none")))
        self.assertEqual(code, 1)
        self.assertIn("No mod library", text)


if __name__ == "__main__":
    unittest.main()
