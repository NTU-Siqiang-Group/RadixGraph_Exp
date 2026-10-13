import io
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


PREPARE = Path(__file__).resolve().parents[1] / "scripts" / "prepare-data.sh"
ARCHIVED = ("graph500-24", "uniform-24", "dota-league")
GENERATED = ("com-lj.ungraph", "com-orkut.ungraph", "twitter-2010")
NAMES = ARCHIVED + GENERATED
GRAPHLOGS = ("graph500-24-delete", "uniform-24-delete", "graph500-24-1.0",
             "uniform-24-1.0", "dota-league")
MIXED_GRAPHS = ("graph500-24", "uniform-24")


def write_graphlog(path, operations, *, aging=10):
    """Small binary Graphlog fixture with independently compressed edge blocks."""
    import struct
    import zlib

    vertices = struct.pack("=3Q", 13, 17, 29)
    values = {
        "aging_coeff": aging,
        "internal.vertices.begin": 0,
        "internal.vertices.cardinality": 3,
        "internal.edges.begin": 0,
        "internal.edges.block_size": 240,
        "internal.edges.cardinality": operations,
        "internal.edges.final": 10,
    }

    def header():
        return ("# GRAPHLOG\n" + "".join(f"{key} = {value:019d}\n" for key, value in values.items())
                + "__BINARY_SECTION_FOLLOWS\n").encode()

    values["internal.vertices.begin"] = len(header())
    values["internal.edges.begin"] = len(header()) + len(vertices)
    with path.open("wb") as stream:
        stream.write(header())
        stream.write(vertices)
        for first in range(0, operations, 10):
            indices = range(first, min(first + 10, operations))
            sources = [13 + i % 3 for i in indices]
            destinations = [17 + i % 5 for i in indices]
            weights = [0.5 if i % 2 else -0.5 for i in indices]
            block = (struct.pack(f"={len(sources)}Q", *sources)
                     + struct.pack(f"={len(destinations)}Q", *destinations)
                     + struct.pack(f"={len(weights)}d", *weights))
            compressor = zlib.compressobj(wbits=-15)
            stream.write(compressor.compress(block) + compressor.flush())


def read_graphlog(path):
    """Decode fixtures without using the implementation under test."""
    import struct
    import zlib

    contents = path.read_bytes()
    header = contents.split(b"__BINARY_SECTION_FOLLOWS\n", 1)[0].decode()
    values = dict(line.split(" = ", 1) for line in header.splitlines()[1:])
    payload = contents[int(values["internal.edges.begin"]):]
    edges = []
    while payload:
        decoder = zlib.decompressobj(wbits=-15)
        block = decoder.decompress(payload) + decoder.flush()
        if not decoder.eof or len(block) % 24:
            raise AssertionError("Invalid compressed Graphlog edge block")
        payload = decoder.unused_data
        count = len(block) // 24
        sources = struct.unpack_from(f"={count}Q", block)
        destinations = struct.unpack_from(f"={count}Q", block, count * 8)
        weights = struct.unpack_from(f"={count}d", block, count * 16)
        edges.extend(zip(sources, destinations, weights))
    return values, edges


def properties(name, *, weighted=False, bc=False):
    values = {
        "vertex-file": f"/publisher/datasets/{name}.v",
        "edge-file": f"/publisher/datasets/{name}.e",
        "directed": "false",
        "meta.vertices": "3",
        "meta.edges": "2",
        "algorithms": "bfs, cdlp, lcc, pr, wcc",
        "bfs.source-vertex": "17" if name == "graph500-24" else "29",
        "cdlp.max-iterations": "10",
        "pr.damping-factor": "0.85",
        "pr.num-iterations": "10",
    }
    if weighted:
        values.update({"edge-properties.names": "weight", "edge-properties.types": "real",
                       "sssp.weight-property": "weight", "sssp.source-vertex": "13"})
        values["algorithms"] += ", sssp"
    if bc:
        values["algorithms"] += ", bc"
        values["bc.max-iterations"] = "7"
    return "# Original dataset parameters\n" + "".join(
        f"graph.{name}.{key} = {value}\n" for key, value in values.items())


def read_properties(path):
    return dict(line.split(" = ", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#"))


class PrepareDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.gfe = self.root / "gfe"
        self.bin = self.root / "bin"
        self.calls = self.root / "calls.jsonl"
        (self.data / "downloads").mkdir(parents=True)
        self.gfe.mkdir()
        self.bin.mkdir()
        self.original = {name: properties(name, weighted=name == "dota-league",
                                          bc=name == "dota-league") for name in ARCHIVED}
        self.archive = self.data / "downloads" / "datasets.tar.gz"
        with tarfile.open(self.archive, "w:gz") as archive:
            for name in ARCHIVED:
                for extension, contents in (("properties", self.original[name]),
                                            ("e", "13 17 0.5\n17 29 0.25\n"),
                                            ("v", "13\n17\n29\n")):
                    encoded = contents.encode()
                    member = tarfile.TarInfo(f"datasets/{name}.{extension}")
                    member.size = len(encoded)
                    archive.addfile(member, io.BytesIO(encoded))
        for name in GENERATED:
            (self.data / f"{name}.el").write_text("13 17\n17 29\n")
        (self.data / ".twitter-deduplicated").touch()
        # All input data is local. Any download is a regression, not a slow test.
        self.write_tool(self.bin / "wget", "raise SystemExit('unexpected network download')\n")
        self.write_tool(self.gfe / "generate_property_files", """
source = Path(sys.argv[1])
name = source.stem
record('generate', name)
prefix = 'graph.' + name + '.'
values = {'vertex-file': str(source.with_suffix('.v')),
          'edge-file': str(source.with_suffix('.e')),
          'edge-properties.names': 'weight', 'edge-properties.types': 'real',
          'algorithms': 'bfs, cdlp, lcc, pr, sssp, wcc, bc',
          'bfs.source-vertex': '13', 'cdlp.max-iterations': '10',
          'pr.damping-factor': '0.85', 'pr.num-iterations': '10',
          'sssp.weight-property': 'weight', 'sssp.source-vertex': '13',
          'bc.max-iterations': '5'}
source.with_suffix('.properties').write_text(
    '# Generated SNAP parameters\\n' + ''.join(prefix + key + ' = ' + value + '\\n'
                                              for key, value in values.items()))
source.with_suffix('.e').write_text('13 17 0.5\\n17 29 0.25\\n')
source.with_suffix('.v').write_text('13\\n17\\n29\\n')
""")
        self.write_tool(self.gfe / "create_vertex_ops", """
record('vertices')
for name in %r:
    Path('datasets', name + '.vertices.el').write_text('13 17\\n')
""" % (NAMES,))
        self.write_tool(self.gfe / "graphlog-base" / "build" / "graphlog",
                        inspect.getsource(write_graphlog) + """
record('graphlog', Path(sys.argv[-1]).name)
aging = int(sys.argv[sys.argv.index('-a') + 1])
write_graphlog(Path(sys.argv[-1]), 10 if aging == 1 else 100, aging=aging)
""")

    def write_tool(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!{sys.executable}\n" + """
import json
import os
from pathlib import Path
import sys
def record(*event):
    with Path(os.environ['PREPARE_TEST_CALLS']).open('a') as stream:
        stream.write(json.dumps(event) + '\\n')
""" + body)
        path.chmod(0o755)

    def prepare(self):
        env = dict(os.environ, GFE_DIR=str(self.gfe), DATA_DIR=str(self.data),
                   PREPARE_TEST_CALLS=str(self.calls), PATH=f"{self.bin}:{os.environ['PATH']}")
        result = subprocess.run(["bash", str(PREPARE), "--profile", "paper"],
                                env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def events(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def assert_original_parameters(self):
        for name in ARCHIVED:
            with self.subTest(dataset=name):
                expected = dict(line.split(" = ", 1) for line in self.original[name].splitlines()
                                if not line.startswith("#"))
                prefix = f"graph.{name}."
                expected[prefix + "vertex-file"] = name + ".v"
                expected[prefix + "edge-file"] = name + ".e"
                if name != "dota-league":
                    expected[prefix + "algorithms"] += ", bc"
                    expected[prefix + "bc.max-iterations"] = "5"
                self.assertEqual(read_properties(self.data / f"{name}.properties"), expected)
                self.assertIn("# Original dataset parameters", (self.data / f"{name}.properties").read_text())

    def assert_gtx_prefixes(self):
        for name in MIXED_GRAPHS:
            with self.subTest(dataset=name):
                source = self.data / f"{name}-1.0.graphlog"
                prefix = self.data / f"{name}-1.0-gtx-20.graphlog"
                full_header, full_edges = read_graphlog(source)
                prefix_header, prefix_edges = read_graphlog(prefix)
                self.assertEqual(int(full_header["internal.edges.cardinality"]), 100)
                self.assertEqual(len(full_edges), 100)
                self.assertEqual(int(prefix_header["internal.edges.cardinality"]), 20)
                self.assertEqual(prefix_edges, full_edges[:20])
                self.assertEqual(prefix_header["internal.edges.begin"], full_header["internal.edges.begin"])
                vertex_begin = int(full_header["internal.vertices.begin"])
                edge_begin = int(full_header["internal.edges.begin"])
                self.assertEqual(prefix.read_bytes()[vertex_begin:edge_begin],
                                 source.read_bytes()[vertex_begin:edge_begin])
                self.assertTrue(prefix.with_suffix(prefix.suffix + ".json").is_file())

    def test_fresh_paper_uses_original_archive_properties_and_only_generates_snap(self):
        self.prepare()
        self.assert_original_parameters()
        generated = [event[1] for event in self.events() if event[0] == "generate"]
        self.assertCountEqual(generated, GENERATED)
        for name in GENERATED:
            props = read_properties(self.data / f"{name}.properties")
            self.assertEqual(props[f"graph.{name}.algorithms"], "bfs, cdlp, lcc, pr, sssp, wcc, bc")
            self.assertEqual(props[f"graph.{name}.sssp.source-vertex"], "13")
            self.assertEqual(props[f"graph.{name}.bc.max-iterations"], "5")
            self.assertEqual(props[f"graph.{name}.vertex-file"], name + ".v")
            self.assertEqual(props[f"graph.{name}.edge-file"], name + ".e")
            self.assertIn("# Generated SNAP parameters", (self.data / f"{name}.properties").read_text())
        self.assertEqual(sum(event[0] == "vertices" for event in self.events()), 1)
        self.assertEqual(sum(event[0] == "graphlog" for event in self.events()), 5)
        self.assert_gtx_prefixes()

    def test_old_cache_restores_properties_without_recreating_large_inputs(self):
        (self.data / ".profile").write_text("paper\n")
        retained = []
        generated_properties = {}
        for name in NAMES:
            for extension, content in (("e", "13 17 0.75\n"), ("v", "13\n17\n"),
                                       ("el", "13 17\n"), ("vertices.el", "13 17\n")):
                path = self.data / f"{name}.{extension}"
                path.write_text(content)
                retained.append(path)
            if name in ARCHIVED:
                text = properties(name, weighted=False)
                text = text.replace("bfs, cdlp, lcc, pr, wcc", "bfs, cdlp, lcc, pr, sssp, wcc, bc")
                text += f"graph.{name}.bc.max-iterations = 5\n"
            else:
                text = properties(name, weighted=True, bc=True)
                text = text.replace(f"/publisher/datasets/{name}.", f"{name}.")
            (self.data / f"{name}.properties").write_text(text)
            if name in GENERATED:
                (self.data / f".{name}-properties-ready").touch()
                generated_properties[name] = text
        (self.data / ".vertices-ready").touch()
        for name in GRAPHLOGS:
            path = self.data / f"{name}.graphlog"
            write_graphlog(path, 10 if name.endswith("-delete") else 100,
                           aging=1 if name.endswith("-delete") else 10)
            retained.append(path)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
                  for path in retained}
        self.prepare()
        self.assert_original_parameters()
        self.assertEqual(self.events(), [])
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
                          for path in retained}, before)
        self.assertEqual({name: (self.data / f"{name}.properties").read_text() for name in GENERATED},
                         generated_properties)
        self.assert_gtx_prefixes()

    def test_prepared_cache_is_idempotent_without_archive_or_external_tools(self):
        self.prepare()
        before = {path.name: path.read_bytes() for path in self.data.iterdir() if path.is_file()}
        workload_metadata = {
            path.name: (path.stat().st_mtime_ns, path.stat().st_ino)
            for path in self.data.iterdir() if path.suffix in (".graphlog", ".json")}
        events = self.events()
        self.archive.unlink()
        self.prepare()
        self.assertEqual({path.name: path.read_bytes() for path in self.data.iterdir() if path.is_file()}, before)
        self.assertEqual({path.name: (path.stat().st_mtime_ns, path.stat().st_ino)
                          for path in self.data.iterdir() if path.suffix in (".graphlog", ".json")},
                         workload_metadata)
        self.assertEqual(self.events(), events)


if __name__ == "__main__":
    unittest.main()
