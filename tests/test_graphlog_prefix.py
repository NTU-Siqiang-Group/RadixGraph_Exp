from fractions import Fraction
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from graphlog_prefix import create_prefix, GraphlogError, read_header


def compress(payload):
    compressor = zlib.compressobj(wbits=-15)
    return compressor.compress(payload) + compressor.flush()


def encode(operations):
    return (struct.pack(f"={len(operations)}Q", *(value[0] for value in operations))
            + struct.pack(f"={len(operations)}Q", *(value[1] for value in operations))
            + struct.pack(f"={len(operations)}d", *(value[2] for value in operations)))


def write_graphlog(path, count=25, capacity=4):
    operations = [(i + 1, 1000 + i, -1.0 if i % 3 else i + .25) for i in range(count)]
    vertices = compress(struct.pack("=3Q", 1, 2, 3))
    header = ("# GRAPHLOG\naging_coeff = 10\ninternal.edges.final = 25\n"
              f"internal.edges.block_size = {capacity * 24}\n"
              f"internal.edges.cardinality = {count:<19}\n"
              f"internal.edges.begin = {0:<19}\n\n__BINARY_SECTION_FOLLOWS\n").encode()
    begin = len(header) + len(vertices)
    header = header.replace(f"internal.edges.begin = {0:<19}".encode(),
                            f"internal.edges.begin = {begin:<19}".encode())
    blocks = [compress(encode(operations[start:start + capacity]))
              for start in range(0, count, capacity)]
    path.write_bytes(header + vertices + b"".join(blocks))
    return operations, blocks, header + vertices


def read_operations(path):
    with path.open("rb") as stream:
        header, properties = read_header(stream)
        stream.seek(properties["internal.edges.begin"])
        data = stream.read()
    result = []
    blocks = []
    while data:
        decoder = zlib.decompressobj(-15)
        payload = decoder.decompress(data)
        if not decoder.eof:
            raise AssertionError("Incomplete stream")
        blocks.append(data[:len(data) - len(decoder.unused_data)])
        data = decoder.unused_data
        count = len(payload) // 24
        sources = struct.unpack(f"={count}Q", payload[:count * 8])
        destinations = struct.unpack(f"={count}Q", payload[count * 8:count * 16])
        weights = struct.unpack(f"={count}d", payload[count * 16:])
        result.extend(zip(sources, destinations, weights))
    return result, blocks, header, properties


class GraphlogPrefixTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "full.graphlog"
        self.destination = self.root / "gtx.graphlog"

    def test_exact_prefix_repackages_all_three_columns(self):
        operations, original_blocks, before_edges = write_graphlog(self.source)
        original = self.source.read_bytes()
        metadata = create_prefix(self.source, self.destination)
        result, blocks, header, properties = read_operations(self.destination)
        self.assertEqual(result, operations[:5])
        self.assertEqual(blocks[0], original_blocks[0])
        self.assertNotEqual(blocks[1], original_blocks[1])
        self.assertEqual(properties["internal.edges.cardinality"], 5)
        self.assertEqual(len(header), len(before_edges) - len(compress(struct.pack("=3Q", 1, 2, 3))))
        self.assertIn(b"aging_coeff = 10\ninternal.edges.final = 25\n", header)
        begin = properties["internal.edges.begin"]
        self.assertEqual(self.destination.read_bytes()[len(header):begin], original[len(header):begin])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(metadata["full_operations"], 25)
        self.assertEqual(metadata["prefix_operations"], 5)
        self.assertEqual(json.loads(Path(str(self.destination) + ".json").read_text()), metadata)

    def test_exact_complete_block_boundary_copies_compressed_bytes(self):
        operations, original_blocks, _ = write_graphlog(self.source, count=20)
        create_prefix(self.source, self.destination, Fraction(2, 5))
        result, blocks, _, _ = read_operations(self.destination)
        self.assertEqual(result, operations[:8])
        self.assertEqual(blocks, original_blocks[:2])

    def test_fraction_one_preserves_every_operation_and_block(self):
        operations, original_blocks, _ = write_graphlog(self.source)
        create_prefix(self.source, self.destination, Fraction(1))
        result, blocks, _, _ = read_operations(self.destination)
        self.assertEqual(result, operations)
        self.assertEqual(blocks, original_blocks)

    def test_stream_boundaries_across_small_io_and_decoding_chunks(self):
        operations, original_blocks, _ = write_graphlog(self.source)
        with patch("graphlog_prefix.CHUNK", 11):
            create_prefix(self.source, self.destination)
        result, blocks, _, _ = read_operations(self.destination)
        self.assertEqual(result, operations[:5])
        self.assertEqual(blocks[0], original_blocks[0])

    def test_cache_reuse_and_source_change_invalidation(self):
        write_graphlog(self.source)
        first = create_prefix(self.source, self.destination)
        os.utime(self.destination, ns=(123000000000, 123000000000))
        # Keep the cached output identity in sync so a later unchanged call
        # demonstrates cache reuse rather than merely equal regenerated data.
        sidecar = Path(str(self.destination) + ".json")
        first["output"]["mtime_ns"] = self.destination.stat().st_mtime_ns
        sidecar.write_text(json.dumps(first))
        reused = create_prefix(self.source, self.destination)
        self.assertEqual(reused, first)
        self.assertEqual(self.destination.stat().st_mtime_ns, 123000000000)
        source_stat = self.source.stat()
        os.utime(self.source, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns + 1000000000))
        second = create_prefix(self.source, self.destination)
        self.assertNotEqual(first["source"], second["source"])
        self.assertNotEqual(self.destination.stat().st_mtime_ns, 123000000000)

    def test_modified_destination_invalidates_cache(self):
        operations, _, _ = write_graphlog(self.source)
        create_prefix(self.source, self.destination)
        self.destination.write_bytes(b"invalid")
        create_prefix(self.source, self.destination)
        self.assertEqual(read_operations(self.destination)[0], operations[:5])

    def test_fraction_uses_integer_floor(self):
        operations, _, _ = write_graphlog(self.source, count=27)
        metadata = create_prefix(self.source, self.destination)
        self.assertEqual(metadata["prefix_operations"], 5)
        self.assertEqual(read_operations(self.destination)[0], operations[:5])

    def test_rejects_missing_header_and_bad_block_units(self):
        self.source.write_bytes(b"not a graphlog\n")
        with self.assertRaises(GraphlogError):
            create_prefix(self.source, self.destination)
        write_graphlog(self.source)
        self.source.write_bytes(self.source.read_bytes().replace(b"block_size = 96", b"block_size = 95"))
        with self.assertRaisesRegex(GraphlogError, "multiple of 24"):
            create_prefix(self.source, self.destination)

    def test_rejects_truncated_or_corrupt_compressed_blocks_atomically(self):
        _, _, before_edges = write_graphlog(self.source)
        for damaged in (self.source.read_bytes()[:len(before_edges) + 3], before_edges + b"\x07corrupt"):
            with self.subTest(damaged=damaged[-8:]):
                self.source.write_bytes(damaged)
                with self.assertRaisesRegex(GraphlogError, "Truncated|Corrupt"):
                    create_prefix(self.source, self.destination)
                self.assertFalse(self.destination.exists())
                self.assertFalse(Path(str(self.destination) + ".json").exists())
                self.assertEqual(list(self.root.glob("*.part")), [])

    def test_rejects_header_count_larger_or_smaller_than_payload(self):
        for count in (24, 26):
            write_graphlog(self.source)
            self.source.write_bytes(self.source.read_bytes().replace(
                f"cardinality = {25:<19}".encode(), f"cardinality = {count:<19}".encode()))
            with self.subTest(count=count), self.assertRaisesRegex(GraphlogError, "count|beyond"):
                create_prefix(self.source, self.destination, Fraction(1))

    def test_rejects_empty_or_out_of_range_prefix_and_same_path(self):
        write_graphlog(self.source, count=2)
        for fraction in (Fraction(0), Fraction(2), Fraction(1, 5)):
            with self.subTest(fraction=fraction), self.assertRaises(GraphlogError):
                create_prefix(self.source, self.destination, fraction)
        with self.assertRaisesRegex(GraphlogError, "different files"):
            create_prefix(self.source, self.source)


if __name__ == "__main__":
    unittest.main()
