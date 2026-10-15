"""Native architecture detection and exclusion of unsupported ARM64 builds."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


BUILD = Path(__file__).resolve().parents[1] / "scripts" / "build"


class BuildArchitectureTests(unittest.TestCase):
    def test_docker_and_compiler_architecture_names(self):
        for name, expected in (("amd64", "amd64"), ("x86_64-linux-gnu", "amd64"),
                               ("arm64", "arm64"), ("aarch64-linux-gnu", "arm64")):
            with self.subTest(name=name):
                result = subprocess.run(
                    ["bash", "-c", 'source "$1"; printf "%s" "$TARGETARCH"', "check", str(BUILD / "architecture.sh")],
                    env=dict(os.environ, TARGETARCH=name), text=True, capture_output=True, check=True)
                self.assertEqual(result.stdout, expected)

    def test_compiler_target_used_without_buildkit_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            compiler = Path(directory) / "compiler"
            compiler.write_text('#!/bin/sh\n[ "$1" = -dumpmachine ] || exit 1\necho aarch64-linux-gnu\n')
            compiler.chmod(0o755)
            environment = dict(os.environ, CXX=str(compiler))
            environment.pop("TARGETARCH", None)
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; printf "%s" "$TARGETARCH"', "check", str(BUILD / "architecture.sh")],
                env=environment, text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout, "arm64")

    def test_unsupported_architecture_rejected(self):
        result = subprocess.run(["bash", str(BUILD / "architecture.sh")],
                                env=dict(os.environ, TARGETARCH="arm"), text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unsupported architecture", result.stderr)

    def test_arm_skips_spruce_and_junction_without_sources_or_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_sources = Path(directory) / "missing-sources"
            environment = dict(os.environ, TARGETARCH="arm64", GFE_DIR=str(missing_sources), BUILD_JOBS="2")
            for script, arguments in (("junction.sh", []), ("gfe-driver.sh", ["bvgt"])):
                with self.subTest(script=script):
                    result = subprocess.run(["bash", str(BUILD / script), *arguments], env=environment,
                                            text=True, capture_output=True, check=True)
                    self.assertIn("Skipping x86-only Spruce/bvgt", result.stdout)
                    self.assertFalse(missing_sources.exists())

    def test_arm_skips_teseo_library_and_driver_without_touching_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_sources = Path(directory) / "missing-sources"
            environment = dict(os.environ, TARGETARCH="arm64", GFE_DIR=str(missing_sources), BUILD_JOBS="2")
            for script, arguments in (("teseo.sh", []), ("gfe-driver.sh", ["teseo"])):
                with self.subTest(script=script):
                    result = subprocess.run(["bash", str(BUILD / script), *arguments], env=environment,
                                            text=True, capture_output=True, check=True)
                    self.assertIn("Skipping Teseo", result.stdout)
                    self.assertIn("CPU-affinity topology detection is unsupported", result.stdout)
                    self.assertFalse(missing_sources.exists())


if __name__ == "__main__":
    unittest.main()
