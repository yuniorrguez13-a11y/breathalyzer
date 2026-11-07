"""Tests for tools/mod_symbols.py: reading the names from botw.h, swmod.h, docs/addresses.md and a code map (all
made-up fixtures here, written to a temp folder), the cache, the search order, and the search speed on 70,000
made-up names. No game files are read."""
import os
import random
import statistics
import string
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import mod_symbols as ms  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# A made-up botw.h in the shape of the real one: "//:" docs above each name, internal names, a name with its doc
# at the end of the line, and one that is marked as not tested.
BOTW_H = """// botw.h (made up for the tests)
#pragma once
#include "swmod.h"

//: Version these are for.
#define BOTW_GAME_VERSION "9.9.9"
//: Where the made-up thing lives.
#define BOTW_ADDR_THING 0x7000123400ull
#define BOTW_ADDR_OTHER 0x7000aa0000ull  //: Another made-up function.
//: Offset in the thing: its count (s32).
#define BOTW_THING_COUNT 0x1c
//: A full wheel of the made-up value.
#define BOTW_VALUE_FULL 1000.0f

//: (internal) Read a u64.
static inline uint64_t botw__rd64(uint64_t a) {
    return a ? *(volatile const uint64_t *)(uintptr_t)a : 0;
}

//: The thing's address, or 0.
static inline uint64_t botw_thing(const SwModApi *api) {
    if (!api) return 0;
    return botw__rd64(BOTW_ADDR_THING);
}

//: Set the made-up value. False when there is no thing.
static inline bool botw_value_set(const SwModApi *api,
                                  float value) {
    return api && value >= 0.0f;
}

//: (not tested in game) Move the thing to x y z.
static inline bool botw_thing_move(const SwModApi *api, float x, float y, float z) {
    return api && x == y && y == z;
}

//: A function botw_each_frame runs.
typedef void (*BotwTick)(struct Cpu *c);
//: (internal) the function to run.
static BotwTick botw__tick;
"""

SWMOD_H = """// swmod.h (made up for the tests)
#pragma once
#define SWMOD_API_VERSION 2
#ifdef __cplusplus
#define SWMOD_EXPORT extern "C" __declspec(dllexport)
#else
#define SWMOD_EXPORT __declspec(dllexport)
#endif
// True when the host gives at least API version `version`.
#define SWMOD_API_OK(api, version) ((api) && (api)->api_version >= (uint32_t)(version))
struct Cpu;
typedef void (*SwModGuestFn)(struct Cpu *c);
typedef struct SwModReply SwModReply;  // a command's answer (opaque)
#define SWMOD_HOOK_OK 0
#define SWMOD_HOOK_NO_SLOT 2   // no hook slot for this address

// s<n> as a float.
static inline float swmod_get_s(struct Cpu *c, int n) {
    float f = 0;
    (void)c; (void)n;
    return f;
}

typedef struct {
    const char *name;    // unique
    unsigned flags;
} SwModCommand;

typedef struct SwModApi {
    uint32_t api_version;      // the host's SWMOD_API_VERSION
    const char *game_version;  // e.g. "1.9.0"
    // One line into the log (printf format).
    void (*log)(const char *fmt, ...);
    int (*control_register)(const SwModCommand *cmds, int count);
    void (*reply_str)(SwModReply *r, const char *key, const char *value);
    struct Cpu *(*current_cpu)(void);
    int (*hook)(uint64_t guest_addr, SwModGuestFn before, SwModGuestFn after,
                SwModGuestFn replace);
} SwModApi;

SWMOD_EXPORT int swmod_init(const SwModApi *api);
"""

ADDRESSES_MD = """# Game addresses (made up)

| Address | What it is | Where the repo uses it | How it was verified | Status |
|---|---|---|---|---|
| **Things** | | | | |
| `0x7000123400` | The thing (u64) | `x.c` | In game: it was there | used in a mod |
| `0x7000555500` | Set a widget's colour | `x.c` | Read the code only | research only |
| **Pointers and offsets** | | | | |
| `+0x1c` | Thing: its count | `x.c` | Counted | verified |
| `+0x1c` | Widget: its size | `x.c` | Measured | verified |
| **Made up for the selftest** | | | | |
| `0x7000001000` | Fake function | `tools/build_native.py` | selftest | made up (selftest) |
"""

CODEMAP_HEAD = "address,name,kind,notes\n"


def write_repo(root: Path, codemap_rows=None) -> Path:
    (root / "native" / "modhost").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "native" / "modhost" / "botw.h").write_text(BOTW_H, encoding="utf-8")
    (root / "native" / "modhost" / "swmod.h").write_text(SWMOD_H, encoding="utf-8")
    (root / "docs" / "addresses.md").write_text(ADDRESSES_MD, encoding="utf-8")
    cm = root / "codemap"
    cm.mkdir()
    rows = codemap_rows if codemap_rows is not None else [
        ("0x7000200000", "made::up::Widget::vf_1", "vfunc", ""),
        ("0x7000200010", "made::up::Widget::vf_2", "vfunc", ""),
        ("0x7000200020", "thing_site", "site", "a made-up site, with a comma"),
    ]
    with open(cm / "functions.csv", "w", encoding="utf-8", newline="") as f:
        f.write(CODEMAP_HEAD)
        for r in rows:
            f.write(",".join(f'"{x}"' if "," in x else x for x in r) + "\n")
    return cm


def names(cat):
    return {e["name"]: e for e in cat}


class Parsing(unittest.TestCase):
    def test_botw_h(self):
        cat = names(ms.parse_botw_h(BOTW_H))
        self.assertNotIn("botw__rd64", cat)       # internal
        self.assertNotIn("botw__tick", cat)
        e = cat["BOTW_ADDR_THING"]
        self.assertEqual((e["kind"], e["address"]), ("address", "0x7000123400"))
        self.assertEqual(e["doc"], "Where the made-up thing lives.")
        self.assertEqual(cat["BOTW_ADDR_OTHER"]["doc"], "Another made-up function.")  # (doc at the end of its line)
        self.assertEqual((cat["BOTW_THING_COUNT"]["kind"], cat["BOTW_THING_COUNT"]["address"]), ("offset", "0x1c"))
        self.assertEqual(cat["BOTW_VALUE_FULL"]["kind"], "helper")     # a float, not an address
        self.assertEqual(cat["BOTW_GAME_VERSION"]["signature"], 'BOTW_GAME_VERSION = "9.9.9"')
        self.assertEqual(cat["botw_thing"]["signature"], "uint64_t botw_thing(const SwModApi *api)")
        self.assertEqual(cat["botw_value_set"]["signature"],  # (over two lines)
                         "bool botw_value_set(const SwModApi *api, float value)")
        self.assertEqual(cat["BotwTick"]["kind"], "helper")
        self.assertTrue(ms.is_verified(cat["botw_thing"]))
        self.assertFalse(ms.is_verified(cat["botw_thing_move"]))
        self.assertEqual(ms.trust_text(cat["botw_thing_move"]), "not tested in game")
        for e in cat.values():
            self.assertEqual(set(e), set(ms.FIELDS))
            self.assertEqual(e["source"], ms.BOTW_H)

    def test_swmod_h(self):
        cat = names(ms.parse_swmod_h(SWMOD_H))
        self.assertEqual(cat["SWMOD_EXPORT"]["signature"], "SWMOD_EXPORT = __declspec(dllexport)")  # (the C one)
        self.assertEqual(cat["SWMOD_API_OK"]["signature"], "SWMOD_API_OK(api, version)")
        self.assertIn("at least API version", cat["SWMOD_API_OK"]["doc"])
        self.assertEqual(cat["api->log"]["signature"], "void api->log(const char *fmt, ...)")
        self.assertEqual(cat["api->log"]["doc"], "One line into the log (printf format).")
        self.assertEqual(cat["api->game_version"]["signature"], "const char *api->game_version")
        self.assertEqual(cat["api->current_cpu"]["signature"], "struct Cpu *api->current_cpu(void)")
        self.assertIn("SwModGuestFn replace", cat["api->hook"]["signature"])
        self.assertTrue(cat["api->reply_str"]["doc"])  # (our own words when swmod.h has none)
        self.assertEqual(cat["swmod_get_s"]["signature"], "float swmod_get_s(struct Cpu *c, int n)")
        self.assertEqual(cat["SWMOD_HOOK_NO_SLOT"]["doc"], "no hook slot for this address")
        for n in ("SwModApi", "SwModCommand", "SwModReply", "SwModGuestFn", "swmod_init"):
            self.assertIn(n, cat)
        self.assertNotIn("name", cat)       # (a member of SwModCommand, not of the api)
        self.assertNotIn("api->name", cat)
        self.assertTrue(all(e["kind"] == "api" for e in cat.values()))

    def test_addresses_md(self):
        rows = ms.parse_addresses_md(ADDRESSES_MD)
        by = {(e["kind"], e["address"], e["name"]) for e in rows}
        self.assertIn(("address", "0x7000123400", "addr_thing"), by)
        self.assertIn(("address", "0x7000555500", "addr_set_widget_colour"), by)
        self.assertIn(("offset", "0x1c", "off_thing_count"), by)
        self.assertIn(("offset", "0x1c", "off_widget_size"), by)
        self.assertFalse([e for e in rows if e["address"] == "0x7000001000"])  # made-up selftest rows are left out
        widget = next(e for e in rows if e["name"] == "addr_set_widget_colour")
        self.assertFalse(ms.is_verified(widget))
        self.assertEqual(ms.trust_text(widget), "docs/addresses.md: research only")

    def test_research_only_row_merged_into_a_botw_name(self):
        """Review: BOTW_ADDR_GUEST_END showed 'checked in game' while its table row says research only."""
        e = ms.entry("BOTW_ADDR_END", "address", "BOTW_ADDR_END = 0x7320000000", "End of memory. (docs/addresses.md: "
                     "research only)", "0x7320000000", ms.BOTW_H)
        self.assertFalse(ms.is_verified(e))
        self.assertEqual(ms.trust_text(e), "docs/addresses.md: research only")
        ok = dict(e, doc="Player info pointer. (docs/addresses.md: used in a mod)")
        self.assertTrue(ms.is_verified(ok))
        self.assertEqual(ms.trust_text(ok), "checked in game")
        if (REPO / ms.BOTW_H).is_file():   # the repo's own header and table
            real = names(ms.build_catalog(REPO))
            self.assertEqual(ms.trust_text(real["BOTW_ADDR_GUEST_END"]), "docs/addresses.md: research only")
            self.assertEqual(ms.trust_text(real["BOTW_POS_XYZ"]), "checked in game")
            self.assertIn("docs/addresses.md: verified", real["BOTW_POS_STEP_B"]["doc"])

    def test_merge_keeps_one_name_per_value(self):
        botw = ms.parse_botw_h(BOTW_H)
        rows = ms.merge_addresses(botw, ms.parse_addresses_md(ADDRESSES_MD))
        left = {e["name"] for e in rows}
        self.assertNotIn("addr_thing", left)      # botw.h names it: BOTW_ADDR_THING
        self.assertNotIn("off_thing_count", left)  # same offset, same object (a shared word)
        self.assertIn("off_widget_size", left)     # same offset, another object
        self.assertIn("docs/addresses.md: used in a mod", names(botw)["BOTW_ADDR_THING"]["doc"])

    def test_codemap_and_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cm = write_repo(root)
            core = ms.build_catalog(root)
            self.assertFalse([e for e in core if e["kind"] == "codemap"])  # only when asked for
            full = ms.build_catalog(root, cm)
            code = [e for e in full if e["kind"] == "codemap"]
            self.assertEqual(len(code), 3)
            site = names(code)["thing_site"]
            self.assertEqual(site["address"], "0x7000200020")
            self.assertTrue(site["doc"].startswith(ms.CODEMAP_DOC))
            self.assertIn("with a comma", site["doc"])
            self.assertEqual(site["source"], "codemap/functions.csv")
            self.assertFalse(ms.is_verified(site))
            self.assertEqual({e["kind"] for e in full}, set(ms.KINDS))
            # a missing code map, or one that isn't a code map, is skipped
            self.assertEqual(len(ms.build_catalog(root, root / "nowhere")), len(core))
            (cm / "functions.csv").write_text("something else\n1,2\n", encoding="utf-8")
            self.assertEqual(len(ms.build_catalog(root, cm)), len(core))

    def test_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            cm = write_repo(root)
            cache = Path(tmp) / "cache"
            a, cached = ms.load_catalog(root, cm, cache)
            self.assertFalse(cached)
            b, cached = ms.load_catalog(root, cm, cache)
            self.assertTrue(cached)
            self.assertEqual(a, b)
            f = root / "native" / "modhost" / "botw.h"
            f.write_text(BOTW_H + '//: One more.\n#define BOTW_ADDR_NEW 0x7000777700ull\n', encoding="utf-8")
            st = f.stat()
            os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
            c, cached = ms.load_catalog(root, cm, cache)
            self.assertFalse(cached)
            self.assertIn("BOTW_ADDR_NEW", names(c))
            (cache / ms.CACHE_NAME).write_text("{not json", encoding="utf-8")
            d, cached = ms.load_catalog(root, cm, cache)  # (a broken cache is made again)
            self.assertFalse(cached)
            self.assertEqual(c, d)

    def test_real_headers(self):
        """The repo's own botw.h and swmod.h read as the editor needs them (skipped while botw.h isn't there)."""
        botw = REPO / ms.BOTW_H
        if not botw.is_file():
            self.skipTest("native/modhost/botw.h isn't there yet")
        text = botw.read_text(encoding="utf-8")
        cat = names(ms.build_catalog(REPO))
        for line, nxt in zip(text.splitlines(), text.splitlines()[1:]):
            if line.startswith("//:") and "(internal)" not in line:
                m = ms._DEFINE_RE.match(nxt.strip()) or ms._FUNC_RE.match(nxt.strip())
                if m:
                    self.assertIn(m.group("name"), cat, f"{m.group('name')} from botw.h")
        for n in ("api->log", "api->hook", "api->reply_num", "SWMOD_API_OK", "SWMOD_EXPORT", "swmod_init"):
            self.assertIn(n, cat)
        addrs = [e for e in cat.values() if e["kind"] == "address"]
        self.assertTrue(addrs)
        self.assertFalse([e for e in cat.values() if "__" in e["name"]])


def made_up_catalog(n: int = 70000, seed: int = 7) -> list[dict]:
    """About 200 curated names and the rest code-map-like names (80% vt_<hex>::vf_N, the rest nn::Word::Word::vf_N),
    like a real code map in shape, all invented."""
    rnd = random.Random(seed)
    words = ["actor", "player", "stamina", "heap", "spawn", "matrix", "physics", "anim", "link", "effect", "sound",
             "pos", "frame", "job", "info", "param", "pack", "string", "handle", "camera", "weapon", "shield"]
    cat = [ms.entry(f"botw_{a}_{b}", "helper", f"bool botw_{a}_{b}(const SwModApi *api)", f"Made-up {a} {b}.", "",
                    ms.BOTW_H) for a in words for b in ("get", "set", "on")]
    cat += [ms.entry(f"BOTW_ADDR_{w.upper()}", "address", "", f"Made-up {w}.", f"0x70001{i:05x}0", ms.BOTW_H)
            for i, w in enumerate(words)]
    cat += [ms.entry(f"api->{w}_call", "api", f"void api->{w}_call(int x)", "Made up.", "", ms.SWMOD_H)
            for w in words]
    i = 0
    while len(cat) < n:
        if rnd.random() < 0.8:
            name = f"vt_{0x7001000000 + i * 16:x}::vf_{rnd.randrange(900)}"
        else:
            name = "nn::" + "::".join(rnd.choice(words).capitalize() + rnd.choice(words).capitalize()
                                      for _ in range(2)) + f"::vf_{rnd.randrange(60)}"
        cat.append({"name": name, "kind": "codemap", "signature": "", "doc": ms.CODEMAP_DOC,
                    "address": f"0x{0x7000100000 + i * 16:x}", "source": "codemap/functions.csv"})
        i += 1
    return cat


class Search(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cm = write_repo(root)
            cls.cat = ms.build_catalog(root, cm)
        cls.index = ms.build_index(cls.cat)

    def find(self, text, limit=10, **kw):
        return [e["name"] for e in ms.search(self.index, text, limit, **kw)]

    def test_prefix_then_substring_then_fuzzy(self):
        got = self.find("botw_thing")
        self.assertEqual(got[:3], ["botw_thing", "botw_thing_move", "BOTW_THING_COUNT"])  # exact, prefix, by kind
        got = self.find("thing")     # a word in the name counts as a prefix
        self.assertLess(got.index("botw_thing"), got.index("made::up::Widget::vf_1") if "made::up::Widget::vf_1"
                        in got else 99)
        self.assertIn("thing_site", got)
        got = self.find("hng")        # no prefix, no substring: the letters in order
        self.assertEqual(got[0], "botw_thing")
        got = self.find("widget")     # curated (substring of off_widget_size's word) before the code map
        self.assertTrue(got[0].startswith(("off_widget", "addr_set_widget")))
        self.assertIn("made::up::Widget::vf_1", got)
        self.assertLess(max(got.index(n) for n in got if not n.startswith("made::")),
                        got.index("made::up::Widget::vf_1"))

    def test_order_inside_prefix(self):
        got = self.find("BOTW_")      # the same case first: the constants before the botw_ functions
        self.assertTrue(got[0].startswith("BOTW_"))
        got = self.find("botw_")
        self.assertTrue(got[0].startswith("botw_"))
        got = self.find("api->")
        self.assertTrue(got and all(n.startswith("api->") for n in got))
        self.assertEqual(self.find("api->lo")[0], "api->log")
        self.assertEqual(self.find("log")[0], "api->log")   # (the member's own name is a key too)

    def test_kinds_address_docs_and_limits(self):
        self.assertEqual(self.find("thing", kinds={"codemap"}), ["thing_site"])
        self.assertTrue(all(e["kind"] == "api" for e in ms.search(self.index, "s", 10, kinds={"api"})))
        self.assertEqual(self.find("0x70001234")[0], "BOTW_ADDR_THING")  # by address
        self.assertEqual(self.find("0x7000200020"), ["thing_site"])
        self.assertEqual(len(self.find("", 5)), 5)        # nothing typed: the curated names, helpers first
        self.assertEqual(ms.search(self.index, "", 1)[0]["kind"], "helper")
        self.assertEqual(self.find("zzzzqq"), [])
        self.assertEqual(self.find("thing", 0), [])
        self.assertIn("botw_value_set", self.find("no thing", docs=True))  # words of the doc, at the end

    def test_insertion_and_display(self):
        by = self.index.by_name
        self.assertEqual(ms.call_params(by["botw_value_set"]), ["api", "value"])
        text, a, b = ms.insertion(by["botw_value_set"])
        self.assertEqual(text, "botw_value_set(api, value)")
        self.assertEqual(text[a:b], "value")          # the first argument that isn't api is picked
        text, a, b = ms.insertion(by["botw_thing"])
        self.assertEqual((text, a, b), ("botw_thing(api)", len(text), len(text)))
        text, a, b = ms.insertion(by["api->log"], receiver="a")
        self.assertEqual(text, "a->log(fmt, ...)")
        self.assertEqual(text[a:b], "fmt")
        self.assertEqual(ms.insertion(by["api->game_version"])[0], "api->game_version")
        self.assertEqual(ms.insertion(by["BOTW_ADDR_THING"])[0], "BOTW_ADDR_THING")
        self.assertIsNone(ms.call_params(by["SWMOD_EXPORT"]))   # (its value has parentheses, it isn't called)
        self.assertEqual(ms.call_params(by["SWMOD_API_OK"]), ["api", "version"])
        self.assertEqual(ms.insertion(by["addr_set_widget_colour"])[0], "0x7000555500ull")
        self.assertEqual(ms.insertion(by["off_widget_size"])[0], "0x1c")
        text, _, _ = ms.insertion(by["thing_site"])
        self.assertTrue(text.startswith("0x7000200020ull /* thing_site") and "not tested" in text)
        self.assertEqual(ms.display_text(by["botw_value_set"]), "botw_value_set(api, value)")
        self.assertIn("(code map)", ms.display_text(by["thing_site"]))
        self.assertLessEqual(len(ms.display_text(by["thing_site"], 12)), 12)


class Speed(unittest.TestCase):
    """70,000 made-up names: every query well under 5 ms (best of 3, so a busy moment of the PC doesn't count)."""

    QUERIES = ["s", "st", "sta", "stam", "stamina", "botw_", "BOTW_", "api->", "api->sp", "player pos", "zzq", "xyzw",
               "vfz", "fvt", "kqzj", "effaux", "nn::", "::vf_1", "vt_7001", "0x7000", "e13ed0", "camweap", "hndl",
               "shieldplayervf", "qqqqqq", "a_b_c", "pos", "frame job",
               # several words, one of them in nearly every line (review: 'vf_ zzz' took 35 ms, ':: qq' 36 ms)
               "vf_ zzz", ":: qq", "nn:: zq", "vt_ qj", "vf_ :: 7 xq", "_ zzz"]

    @classmethod
    def setUpClass(cls):
        cls.cat = made_up_catalog()
        t = time.perf_counter()
        cls.index = ms.build_index(cls.cat)
        cls.index_ms = (time.perf_counter() - t) * 1000

    def test_size(self):
        self.assertEqual(len(self.index), 70000)
        self.assertGreater(self.index.codemap_count, 69000)

    def test_queries_under_5_ms(self):
        times = {}
        letters = string.ascii_lowercase + "_"
        randoms = ["".join(random.Random(i).choice(letters) for _ in range(1 + i % 7)) for i in range(60)]
        for q in self.QUERIES + randoms:
            best = min(self._time(q) for _ in range(3))
            times[q] = best
            self.assertLess(best, 5.0, f"search {q!r} took {best:.2f} ms")
        slowest = max(times, key=times.get)
        print(f"\n  search, 70000 names: index {self.index_ms:.0f} ms; median {statistics.median(times.values()):.2f} "
              f"ms, slowest {times[slowest]:.2f} ms ({slowest!r})", file=sys.stderr)

    def _time(self, q):
        t = time.perf_counter()
        ms.search(self.index, q, 10)
        return (time.perf_counter() - t) * 1000

    def test_several_words_scan_the_rarest(self):
        """The code map's substring scan looks for the rarest word, and still finds lines with every word."""
        t = self.index.tiers[1]
        self.assertLess(t.rarity("zzz"), t.rarity("vf_"))
        got = [e["name"] for e in ms.search(self.index, "shield vf_", 10, kinds={"codemap"})]
        self.assertTrue(got)
        self.assertTrue(all("shield" in n.lower() and "vf_" in n.lower() for n in got), got)

    def test_results_are_right_on_the_big_list(self):
        got = [e["name"] for e in ms.search(self.index, "stam", 10)]
        self.assertTrue(got[0].startswith("botw_stamina"))
        got = [e["name"] for e in ms.search(self.index, "nn::", 3)]
        self.assertTrue(all(n.startswith("nn::") for n in got) and len(got) == 3)
        fuzzy = [e["name"] for e in ms.search(self.index, "shldplyr", 10, kinds={"codemap"})]
        self.assertTrue(fuzzy)
        for n in fuzzy:
            it = iter(n.lower())
            self.assertTrue(all(c in it for c in "shldplyr"), n)  # the letters, in order


if __name__ == "__main__":
    unittest.main()
