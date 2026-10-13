#!/usr/bin/env python3
"""Extract an exact operation prefix from GFE's binary compressed graphlog.

Complete edge blocks retain their original compressed bytes. Only a boundary
block is repacked, preserving its source/destination/weight columns. Vertices,
the aging factor, and the full graph's edge target remain unchanged.
"""

import argparse
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zlib


CHUNK = 1024 * 1024
HEADER_LIMIT = 1024 * 1024
MARKER = b"__BINARY_SECTION_FOLLOWS\n"


class GraphlogError(ValueError):
    """A graphlog cannot safely be shortened."""


def read_header(stream):
    """Return the original header bytes and numeric graphlog layout fields."""
    stream.seek(0)
    lines = []
    size = 0
    while size < HEADER_LIMIT:
        line = stream.readline(HEADER_LIMIT - size + 1)
        if not line:
            raise GraphlogError("Missing graphlog binary-section marker")
        lines.append(line)
        size += len(line)
        if size > HEADER_LIMIT:
            raise GraphlogError("Graphlog header exceeds 1 MiB")
        if line == MARKER:
            break
    else:
        raise GraphlogError("Graphlog header exceeds 1 MiB")
    header = b"".join(lines)
    if not header.startswith(b"# GRAPHLOG\n"):
        raise GraphlogError("Missing graphlog magic header")
    properties = {}
    for key in ("internal.edges.begin", "internal.edges.block_size", "internal.edges.cardinality"):
        matches = list(re.finditer(rb"^" + re.escape(key.encode()) + rb"\s*=\s*([0-9]+)[ \t]*$", header, re.M))
        if len(matches) != 1:
            raise GraphlogError(f"Missing or invalid {key}")
        properties[key] = int(matches[0][1])
    if properties["internal.edges.begin"] < len(header):
        raise GraphlogError("Edge section overlaps graphlog header")
    if properties["internal.edges.block_size"] <= 0 or properties["internal.edges.block_size"] % 24:
        raise GraphlogError("Edge block size must be a positive multiple of 24 bytes")
    if properties["internal.edges.cardinality"] <= 0:
        raise GraphlogError("Graphlog operation count must be positive")
    return header, properties


def _prefix_header(header, count):
    pattern = rb"^(internal\.edges\.cardinality[ \t]*=[ \t]*)([0-9]+[ \t]*)$"
    match = re.search(pattern, header, re.M)
    if match is None:
        raise GraphlogError("Cannot update graphlog operation count")
    width = len(match[2])
    value = str(count).encode()
    if len(value) > width:
        raise GraphlogError("Prefix operation count does not fit the original header")
    return header[:match.start(2)] + value.ljust(width) + header[match.end(2):]


def _copy_bytes(source, destination, count):
    while count:
        data = source.read(min(count, CHUNK))
        if not data:
            raise GraphlogError("Truncated graphlog before its edge section")
        destination.write(data)
        count -= len(data)


def _decode_block(source, max_bytes, retain):
    """Find one raw-DEFLATE boundary, discarding full-block decoded data."""
    start = source.tell()
    decoder = zlib.decompressobj(-15)
    pending = b""
    size = 0
    payload = bytearray() if retain else None
    try:
        while not decoder.eof:
            if not pending:
                pending = source.read(CHUNK)
                if not pending:
                    raise GraphlogError("Truncated compressed graphlog edge block")
            decoded = decoder.decompress(pending, min(CHUNK, max_bytes - size + 1))
            pending = decoder.unconsumed_tail
            size += len(decoded)
            if size > max_bytes:
                raise GraphlogError("Graphlog edge block exceeds its declared size")
            if retain:
                payload.extend(decoded)
    except zlib.error as error:
        raise GraphlogError(f"Corrupt compressed graphlog edge block: {error}") from error
    end = source.tell() - len(decoder.unused_data)
    source.seek(end)
    if not size or size % 24:
        raise GraphlogError("Graphlog edge block does not contain complete operation columns")
    return start, end, size // 24, payload


def _write_partial(destination, payload, operations, keep):
    compressor = zlib.compressobj(level=9, wbits=-15)
    view = memoryview(payload)
    for column in range(3):
        begin = column * operations * 8
        end = begin + keep * 8
        for offset in range(begin, end, CHUNK):
            destination.write(compressor.compress(view[offset:min(offset + CHUNK, end)]))
    destination.write(compressor.flush())


def _identity(path, header):
    stat = path.stat()
    return {"path": str(path.resolve()), "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "header_sha256": hashlib.sha256(header).hexdigest()}


def _output_identity(path, header):
    value = _identity(path, header)
    del value["path"]
    return value


def create_prefix(source, destination, fraction=Fraction(1, 5)):
    """Atomically create/cache a graphlog with floor(fraction * count) updates."""
    source, destination = Path(source), Path(destination)
    fraction = Fraction(fraction)
    if not 0 < fraction <= 1:
        raise GraphlogError("Workload fraction must be greater than 0 and at most 1")
    if source.resolve() == destination.resolve():
        raise GraphlogError("Source and prefix destination must be different files")
    sidecar = Path(str(destination) + ".json")
    temporary = None
    metadata_temporary = None
    try:
        with source.open("rb") as original:
            header, properties = read_header(original)
            identity = _identity(source, header)
            if properties["internal.edges.begin"] >= identity["size"]:
                raise GraphlogError("Graphlog has no compressed edge payload")
            full = properties["internal.edges.cardinality"]
            count = full * fraction.numerator // fraction.denominator
            if not count:
                raise GraphlogError("Requested workload fraction contains no operations")
            output_header = _prefix_header(header, count)
            metadata = {"version": 1, "fraction": str(fraction), "full_operations": full,
                        "prefix_operations": count, "source": identity}
            try:
                cached = json.loads(sidecar.read_text())
                with destination.open("rb") as existing:
                    cached_header, cached_properties = read_header(existing)
                if (isinstance(cached, dict)
                        and all(cached.get(key) == value for key, value in metadata.items())
                        and cached_header == output_header
                        and cached_properties["internal.edges.cardinality"] == count
                        and cached.get("output") == _output_identity(destination, cached_header)):
                    return cached
            except (OSError, ValueError):
                pass

            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent,
                                             prefix=f".{destination.name}.", suffix=".part",
                                             delete=False) as output:
                temporary = Path(output.name)
                output.write(output_header)
                original.seek(len(header))
                _copy_bytes(original, output, properties["internal.edges.begin"] - len(header))
                capacity = properties["internal.edges.block_size"] // 24
                consumed = 0
                while consumed < count:
                    remaining = count - consumed
                    start, end, operations, payload = _decode_block(
                        original, properties["internal.edges.block_size"], remaining < capacity)
                    expected = min(capacity, full - consumed)
                    if operations != expected:
                        raise GraphlogError("Compressed graphlog block disagrees with the header operation count")
                    keep = min(remaining, operations)
                    if keep == operations:
                        original.seek(start)
                        _copy_bytes(original, output, end - start)
                    else:
                        _write_partial(output, payload, operations, keep)
                    consumed += keep
                if count == full and original.read(1):
                    raise GraphlogError("Graphlog contains operations beyond its header count")
            if _identity(source, header) != identity:
                raise GraphlogError("Source graphlog changed while its prefix was being extracted")
            metadata["output"] = _output_identity(temporary, output_header)
            with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent,
                                             prefix=f".{sidecar.name}.", suffix=".part",
                                             delete=False) as document:
                metadata_temporary = Path(document.name)
                json.dump(metadata, document, indent=2)
                document.write("\n")
            os.replace(temporary, destination)
            temporary = None
            os.replace(metadata_temporary, sidecar)
            metadata_temporary = None
            return metadata
    finally:
        for path in (temporary, metadata_temporary):
            if path is not None:
                path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fraction", type=Fraction, default=Fraction(1, 5))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    try:
        metadata = create_prefix(args.source, args.destination, args.fraction)
    except (GraphlogError, OSError) as error:
        parser.exit(1, f"graphlog prefix: {error}\n")
    print(f"Prepared {metadata['prefix_operations']:,} / {metadata['full_operations']:,} "
          f"operations ({metadata['fraction']}) in {args.destination}")


if __name__ == "__main__":
    main()
