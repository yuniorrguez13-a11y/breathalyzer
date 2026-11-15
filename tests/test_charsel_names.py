"""Our own model files are named as their files (tools/charsel.py own_names; docs/room.md).

The costumes on Link's skeleton (Linkle, an imported VRM avatar) were Link's model file with new shapes, still named
"Link" inside, with the model list's unit "Link": the game could not create them. In the game (2026-11-15) the same
files renamed as their own file (file, model, textures, unit) loaded at once. Made-up model and textures (the AK-47
tests' claymore), no game files.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import aamp  # noqa: E402
import charsel  # noqa: E402
from mp import ghost_link_pack as glp  # noqa: E402
from test_ak47 import donor_model, donor_textures  # noqa: E402
from test_charsel import pause_pack, template_pack  # noqa: E402
from wiiu_model import nx_bfres, nx_model  # noqa: E402

NAME = "SwChar_custom"


def names(model: bytes, tex: bytes):
    res = nx_model.read(charsel._unyaz(model))
    ts = nx_bfres.read_textures(charsel._unyaz(tex))
    return res.name, [m.name for m in res.models], ts.name, res, ts


def link_unit_pack(cid="custom"):
    """A pack as the 2026-11-14 build made it: our folder, the unit still "Link"."""
    files = charsel.make_copy_pack(cid, charsel.NPC_SLOTS[4], "NPC", template_pack(), pause_pack())
    rel = f"Actor/ModelList/{charsel.ours(cid)}.bmodellist"
    ml = aamp.load(files[rel])
    unit = ml.list("ModelData").list("ModelData_0").list("Unit").object("Unit_0")
    unit[aamp.name_hash("UnitName")] = aamp.Param(unit.get(aamp.name_hash("UnitName")).type, "Link")
    files[rel] = aamp.dump(ml)
    return glp.pack_bytes(files)[0]


class OwnNames(unittest.TestCase):
    def test_file_model_and_textures_renamed(self):
        before = names(donor_model(), donor_textures())
        model, tex, changed = charsel.own_names(donor_model(), donor_textures(), NAME)
        self.assertTrue(changed)
        fres, models, tname, res, ts = names(model, tex)
        self.assertEqual((fres, models, tname), (NAME, [NAME], NAME))
        self.assertEqual(len(res.models[0].shapes), len(before[3].models[0].shapes))       # nothing else changed
        self.assertEqual([t.name for t in ts.textures], [t.name for t in before[4].textures])
        self.assertEqual(model[:4], b"Yaz0")                                               # packed like the game's

    def test_named_already_unchanged(self):
        model, tex, _ = charsel.own_names(donor_model(), donor_textures(), NAME)
        again = charsel.own_names(model, tex, NAME)
        self.assertEqual(again, (model, tex, False))


class Units(unittest.TestCase):
    def test_copy_pack_unit_is_its_folder(self):
        files = charsel.make_copy_pack("linkle", charsel.NPC_SLOTS[4], "NPC", template_pack(), pause_pack())
        data = glp.pack_bytes(files)[0]
        rb = charsel.read_back(data)
        self.assertEqual((rb["folder"], rb["units"]), ("SwChar_linkle", ["SwChar_linkle"]))
        self.assertEqual(charsel.check_pack(data, charsel.NPC_SLOTS[4]), [])

    def test_check_refuses_a_link_unit(self):
        bad = charsel.check_pack(link_unit_pack(), charsel.NPC_SLOTS[4])
        self.assertTrue(any("is unit Link" in b for b in bad), bad)

    def test_old_pack_repaired(self):
        fixed, changed = charsel.own_pack_units(link_unit_pack(), NAME)
        self.assertTrue(changed)
        self.assertEqual(charsel.check_pack(fixed, charsel.NPC_SLOTS[4]), [])
        self.assertEqual(charsel.own_pack_units(fixed, NAME), (fixed, False))


class RepairKept(unittest.TestCase):
    def test_kept_import_renamed_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            top = Path(d) / "romfs"
            (top / "Model").mkdir(parents=True)
            (top / "Actor" / "Pack").mkdir(parents=True)
            slot = charsel.NPC_SLOTS[4]
            (top / "Model" / f"{NAME}.sbfres").write_bytes(charsel._yaz(donor_model()))
            (top / "Model" / f"{NAME}.Tex.sbfres").write_bytes(charsel._yaz(donor_textures()))
            (top / "Actor" / "Pack" / f"{slot}.sbactorpack").write_bytes(link_unit_pack())
            said = []
            self.assertEqual(charsel.repair_own_names(top, ["custom"], {"custom": slot}, said.append), ["custom"])
            fres, models, tname, _, _ = names((top / "Model" / f"{NAME}.sbfres").read_bytes(),
                                              (top / "Model" / f"{NAME}.Tex.sbfres").read_bytes())
            self.assertEqual((fres, models, tname), (NAME, [NAME], NAME))
            pack = (top / "Actor" / "Pack" / f"{slot}.sbactorpack").read_bytes()
            self.assertEqual(charsel.check_pack(pack, slot), [])
            self.assertTrue(said and "renamed" in said[0])
            self.assertEqual(charsel.repair_own_names(top, ["custom"], {"custom": slot}, said.append), [])


if __name__ == "__main__":
    unittest.main()
