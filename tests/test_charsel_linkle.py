"""Linkle v3's outfit for the friends' view (charsel.outfit_parts): her tunic/skirt/boots, hood down, hair, pouches.
Shape names as The Linkle Mod 3.0.1's Model/Armor_001 and Armor_Default have them."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import charsel  # noqa: E402


def model(name, shapes):
    """A fake model: shapes = [(shape name, material name, textures, vertex count)]."""
    return types.SimpleNamespace(
        name=name,
        shapes=[types.SimpleNamespace(name=s, material=k, vertex_buffer=k) for k, (s, _m, _t, _n) in enumerate(shapes)],
        materials=[types.SimpleNamespace(name=m, textures=list(t)) for _s, m, t, _n in shapes],
        vertex_buffers=[types.SimpleNamespace(vertex_count=n) for _s, _m, _t, n in shapes])


HEAD = [("Hood_A_001_Metal__Mt_Hood_001", "Mt_Hood_001", ["Armor_001_Hood_Alb.0"], 1261),
        ("green hood", "green_head", ["Armor_001_Hood_Alb.0"], 2014),
        ("green hood down", "green_head_0", ["Armor_001_Hood_Alb.0"], 2052),
        ("Hood_A_001_Metal__Mt_Hood_Down", "Mt_Hood_001_0", ["Armor_001_Hood_Alb.0"], 1263),
        ("green necklace", "green_head", ["Armor_001_Hood_Alb.0"], 992),
        ("green necklace001", "green_head_0", ["Armor_001_Hood_Alb.0"], 992),
        ("BraidsLinkle.001", "Mt_Hair_001", ["Link_Hair_Alb"], 1511)]


class OutfitParts(unittest.TestCase):
    def test_outfit_hood_down_hair_and_pouches(self):
        fit = [model("Armor_001_Head_A", HEAD), model("Armor_001_Head_B", HEAD),
               model("Armor_001_Upper", [("Metal_001__Mt_Upper_001", "Mt_Upper_001", ["Armor_001_Upper_Alb.0"], 5060),
                                         ("green shirt", "green_upper", ["greenshirt"], 5302),
                                         ("Gone", "Mt_Upper_001", ["Armor_001_Upper_Alb.0"], 1)]),
               model("Armor_001_Lower", [("Mt_Lower_001", "Mt_Lower_001", ["Armor_001_Lower_Alb.0"], 1299),
                                         ("green boots", "green_boots", ["greenleg"], 2539)])]
        look = [model("Armor_Default_Head", [("FINALBANGS", "Mt_Hair", ["Link_Hair_Alb"], 2264)]),
                model("Armor_Default_Lower", [("Lower_1__Mt_Lower", "Mt_Lower", ["Armor_Default_Lower_Alb"], 185)]),
                model("Armor_Default_Extra_00", [("Pouch_B_Body__Mt_Pouch_B", "Mt_Pouch_A", ["Pouch_A_Alb"], 168)]),
                model("Armor_Default_Extra_01", [("Pouch_B_Body__Mt_Pouch_B", "Mt_Pouch_B", ["Pouch_B_Alb"], 1)])]
        have = {"Armor_001_Hood_Alb.0", "Link_Hair_Alb", "Armor_001_Upper_Alb.0", "Armor_001_Lower_Alb.0",
                "Armor_Default_Lower_Alb", "Pouch_A_Alb", "Pouch_B_Alb"}
        outfit, keep, left = charsel.outfit_parts(fit, look, have)
        self.assertEqual([(p.name, [s.name for s in p.shapes]) for p in outfit],
                         [("Armor_001_Head_A", ["green hood down", "Hood_A_001_Metal__Mt_Hood_Down",
                                                "green necklace001", "BraidsLinkle.001"]),
                          ("Armor_001_Upper", ["Metal_001__Mt_Upper_001"]), ("Armor_001_Lower", ["Mt_Lower_001"])])
        self.assertEqual([p.name for p in keep], ["Armor_Default_Extra_00"])     # (pouches; hair: Head_A's)
        self.assertEqual(left, ["Armor_001_Head_A/Hood_A_001_Metal__Mt_Hood_001: hood up",
                                "Armor_001_Head_A/green hood: hood up", "Armor_001_Head_A/green necklace: hood up",
                                "Armor_001_Head_B: a second copy of Armor_001_Head_A",
                                "Armor_001_Upper/green shirt: no texture greenshirt",
                                "Armor_001_Lower/green boots: no texture greenleg"])
        self.assertEqual(len(fit[0].shapes), len(HEAD))               # (the mod's models themselves stay whole)
        up = outfit[1]                                                 # only the materials its kept shapes use
        self.assertEqual([m.name for m in up.materials], ["Mt_Upper_001"])
        self.assertEqual(up.shapes[0].material, 0)
        self.assertEqual([m.name for m in outfit[0].materials], ["green_head_0", "Mt_Hood_001_0", "green_head_0",
                                                                 "Mt_Hair_001"])
        self.assertEqual([s.material for s in outfit[0].shapes], [0, 1, 2, 3])
        self.assertEqual(fit[0].shapes[2].material, 2)                # (renumbered on copies only)

    def test_constants(self):
        self.assertEqual(charsel.LINKLE_OUTFIT, "Armor_001")
        self.assertEqual(charsel.LINKLE_FROM_LOOK, ("Armor_Default_Extra_00", "Armor_Default_Extra_01"))
        outfit, _keep, left = charsel.outfit_parts([model("Armor_001_Head_A", HEAD[:2])], [], {"Armor_001_Hood_Alb.0"})
        self.assertEqual(outfit, [])                                   # (all hood up: nothing to wear)
        self.assertEqual(len(left), 2)


if __name__ == "__main__":
    unittest.main()
