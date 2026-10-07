#!/usr/bin/env python3
"""Sequential paper experiments, durable job logs, and artifact generation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
DATASETS = ("com-lj.ungraph", "dota-league", "com-orkut.ungraph", "graph500-24", "uniform-24", "twitter-2010")
SYSTEMS = {"radixgraph": "radixgraph", "bvgt": "bvgt", "teseo": "teseo.13", "sortledton": "sortledton.4", "gtx": "gtx"}


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def split_names(value, allowed):
    names = value.split(",")
    unknown = set(names) - set(allowed)
    if unknown:
        raise argparse.ArgumentTypeError("Unknown selection: " + ", ".join(sorted(unknown)))
    return list(dict.fromkeys(names))


class Pipeline:
    def __init__(self, args):
        self.args = args
        self.profile = args.profile
        self.gfe = args.gfe_dir.resolve()
        self.radix = args.radix_dir.resolve()
        self.datasets = args.data_dir.resolve()
        self.threads = args.threads or (64 if self.profile == "paper" else 2)
        if self.threads < 1 or args.timeout_hours <= 0:
            raise ValueError("threads and timeout-hours must be positive")
        os.environ["RG_THREADS"] = str(self.threads)
        os.environ["OMP_NUM_THREADS"] = str(self.threads)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.output = args.run_dir.resolve() if args.run_dir else args.output_dir.resolve() / run_id
        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / "logs").mkdir(exist_ok=True)
        self.gfe_work = self.output / "work" / "gfe"
        self.gfe_work.mkdir(parents=True, exist_ok=True)
        self.dataset_specs = [dict(name=name, edge=self.datasets / (name + ".e"),
                                   vertices=self.datasets / (name + ".vertices.el"),
                                   properties=self.datasets / (name + ".properties"),
                                   optimizer=self.datasets / (name + ".v")) for name in args.datasets]
        self.jobs = []
        self.manifest_path = self.output / "manifest.json"
        if self.manifest_path.exists():
            if args.stages != ["report"]:
                raise ValueError("Run directory already has a manifest; choose a new directory or --stages report")
            self.manifest = json.loads(self.manifest_path.read_text())
            self.jobs = self.manifest["jobs"]
            self.profile = self.manifest["profile"]
        else:
            self.manifest = dict(profile=self.profile, started_utc=datetime.now(timezone.utc).isoformat(),
                                 config=vars(args), threads=self.threads, jobs=self.jobs,
                                 host=dict(platform=platform.platform(), cpu_count=os.cpu_count(),
                                           cpuinfo=Path("/proc/cpuinfo").read_text() if Path("/proc/cpuinfo").exists() else "",
                                           meminfo=Path("/proc/meminfo").read_text() if Path("/proc/meminfo").exists() else ""))
            versions = HERE.parent / "source-versions.json"
            if not versions.exists():
                versions = Path("/workspace/build-manifest.json")
            if versions.exists():
                self.manifest["sources"] = json.loads(versions.read_text())
            self.save()

    def save(self):
        atomic_json(self.manifest_path, self.manifest)

    def snapshot_inputs(self):
        marker = self.datasets / ".profile"
        if marker.exists() and marker.read_text().strip() != self.profile:
            raise ValueError("Data cache profile differs from selected run profile; use a separate data directory")
        inputs = []
        for spec in self.dataset_specs:
            entry = dict(dataset=spec["name"], files={})
            for key in ("edge", "vertices", "properties", "optimizer"):
                path = spec[key]
                entry["files"][key] = dict(path=str(path), bytes=path.stat().st_size if path.exists() else None)
                if key == "properties" and path.is_file():
                    data = path.read_bytes()
                    entry["files"][key].update(sha256=hashlib.sha256(data).hexdigest(), content=data.decode())
            entry["graphlogs"] = []
            for suffix in ("", "-1.0", "-delete"):
                path = self.datasets / (spec["name"] + suffix + ".graphlog")
                if path.is_file():
                    with path.open("rb") as stream:
                        header, marker, _ = stream.read(65536).partition(b"__BINARY_SECTION_FOLLOWS")
                    entry["graphlogs"].append(dict(path=str(path), bytes=path.stat().st_size,
                                                   header=header.decode(errors="replace") if marker else None))
            inputs.append(entry)
        atomic_json(self.output / "inputs.json", inputs)

    def note(self, name, status, reason, metadata=None):
        result = dict(name=name, status=status, reason=reason, metadata=metadata or {}, log="", exit_code=None)
        self.jobs.append(result)
        self.save()
        print(f"[{status}] {name}: {reason}", flush=True)
        return result

    def run(self, name, command, cwd, input_text=None, metadata=None):
        command = [str(item) for item in command]
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        logfile = self.output / "logs" / (safe_name + ".log")
        result = dict(name=name, command=command, cwd=str(cwd), input_text=input_text,
                      metadata=metadata or {}, log=str(logfile), status="running", exit_code=None,
                      started_utc=datetime.now(timezone.utc).isoformat())
        self.jobs.append(result)
        self.save()
        print(f"[run] {name}: {shlex.join(command)}", flush=True)
        started = time.monotonic()
        process = None
        interrupted = False
        with logfile.open("w") as stream:
            try:
                process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                           stdout=stream, stderr=subprocess.STDOUT, text=True, start_new_session=True)
                process.communicate(input=input_text, timeout=self.args.timeout_hours * 3600)
                result.update(exit_code=process.returncode, status="ok" if process.returncode == 0 else "failed")
            except subprocess.TimeoutExpired:
                self.stop(process)
                result.update(status="timeout", exit_code=124)
                stream.write("\nPipeline wall-clock limit exceeded.\n")
            except KeyboardInterrupt:
                self.stop(process)
                result.update(status="interrupted", exit_code=130)
                interrupted = True
            except OSError as error:
                stream.write(str(error) + "\n")
                result.update(status="failed", exit_code=127, reason=str(error))
        result["elapsed_seconds"] = time.monotonic() - started
        self.save()
        print(f"[{result['status']}] {name} ({result['elapsed_seconds']:.1f}s)", flush=True)
        if interrupted:
            raise KeyboardInterrupt
        return result

    @staticmethod
    def stop(process):
        if process is None or process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

    def optimize(self, system, spec):
        # GFE reads settings.txt in its working directory. Keep runtime writes
        # in the output mount; weighted edges cannot be passed to this optimizer.
        if system != "radixgraph":
            return True
        result = self.run("optimizer-" + spec["name"] + "-" + str(len(self.jobs)),
                          [self.gfe / "optimizer", spec["optimizer"], "8"], self.gfe_work,
                          metadata=dict(suite="setup", dataset=spec["name"]))
        return result["status"] == "ok" and (self.gfe_work / "settings.txt").is_file()

    def gfe_job(self, system, spec, experiment, extra=None, variant="", metadata=None):
        meta = dict(suite="gfe", system=SYSTEMS[system], dataset=spec["name"], experiment=experiment)
        meta.update(metadata or {})
        name = "-".join(filter(None, [experiment, system, spec["name"], variant]))
        if (self.profile == "paper" and spec["name"] == "twitter-2010"
                and system in ("teseo", "sortledton", "gtx") and experiment in ("random", "vertices", "analytics")):
            return self.note(name, "expected_omission", "Twitter run omitted as in the paper (baseline failure/timeout)", meta)
        if not self.optimize(system, spec):
            return self.note(name, "failed", "RadixGraph optimizer did not produce settings.txt", meta)
        binary = self.gfe / "build" / ("gfe_driver_" + system + ("_" + variant.split("-")[0] if variant else ""))
        graph = spec["vertices"] if experiment == "vertices" else spec["properties"]
        command = [binary, "-G", graph, "-u", "-l", SYSTEMS[system], "-w", str(self.threads)]
        if experiment == "random":
            command += ["-R", "0"]
        elif experiment == "vertices":
            command += ["-R", "0", "--insert_vertex_only=true"]
        elif experiment == "analytics":
            command += ["-r", str(self.threads), "-R", "3", "--blacklist", "cdlp"]
        elif experiment in ("mixed", "delete", "concurrent"):
            suffix = "-delete" if experiment == "delete" else "-1.0" if experiment == "mixed" else ""
            graphlog = self.datasets / (spec["name"] + suffix + ".graphlog")
            database = self.output / "raw" / (name + ".sqlite3")
            database.parent.mkdir(exist_ok=True)
            meta["database"] = str(database)
            command += ["--log", graphlog, "--aging_timeout", f"{max(1, int(self.args.timeout_hours * 3600))}s",
                        "--aging_memfp=true", "--aging_memfp_physical=true", "-d", database]
            if experiment == "delete":
                command[command.index("-w") + 1] = "2"
                command += ["--aging_release_memory=false", "--delete_all=true"]
            if experiment == "concurrent":
                command[command.index("-w") + 1] = str(meta["write_threads"])
                command += ["-r", str(meta["read_threads"]), "--aging_release_memory=true", "--mixed_workload=true"]
        command += extra or []
        return self.run(name, command, self.gfe_work, metadata=meta)

    def run_main(self):
        for system in self.args.systems:
            for spec in self.dataset_specs:
                for experiment in ("random", "vertices", "analytics"):
                    self.gfe_job(system, spec, experiment)
                if spec["name"] in ("graph500-24", "uniform-24"):
                    for experiment in ("mixed", "delete"):
                        self.gfe_job(system, spec, experiment)
        dota = next((s for s in self.dataset_specs if s["name"] == "dota-league"), None)
        if dota:
            for system in (s for s in self.args.systems if s in ("radixgraph", "gtx")):
                for hop in (1, 2):
                    for count in ([4, 8, 16, 32] if self.profile == "paper" else [1, 2]):
                        for axis in ("read", "write"):
                            fixed = 32 if self.profile == "paper" else 2
                            self.gfe_job(system, dota, "concurrent", variant=f"{hop}hop-{axis}-{count}",
                                         metadata=dict(hop=hop, axis=axis, count=count,
                                                       read_threads=count if axis == "read" else fixed,
                                                       write_threads=count if axis == "write" else fixed))


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    result.add_argument("--profile", choices=("paper", "smoke"), default=os.getenv("PROFILE", "paper"),
                        help="smoke uses tiny synthetic fixtures and cannot reproduce paper measurements")
    result.add_argument("--stages", type=lambda v: split_names(v, ("all", "prepare", "main", "studies", "batch", "report")), default=["all"],
                        help="comma-separated stages; reports are always exported, including on failures")
    result.add_argument("--gfe-dir", type=Path, default=Path(os.getenv("GFE_DIR", "/workspace/gfe_driver")))
    result.add_argument("--radix-dir", type=Path, default=Path(os.getenv("RADIX_DIR", "/workspace/RadixGraph")))
    result.add_argument("--data-dir", type=Path, default=Path(os.getenv("DATA_DIR", "/data")), help="reusable dataset cache")
    result.add_argument("--output-dir", type=Path, default=Path(os.getenv("OUTPUT_DIR", "/output")), help="parent of timestamped run directories")
    result.add_argument("--run-dir", type=Path, help="explicit run directory, also used by --stages report")
    result.add_argument("--threads", type=int, default=int(os.getenv("THREADS", "0")), help="workers, 0 selects paper=64/smoke=2")
    result.add_argument("--timeout-hours", type=float, default=float(os.getenv("TIMEOUT_HOURS", "48")), help="wall-clock limit for each job")
    result.add_argument("--systems", type=lambda v: split_names(v, SYSTEMS), default=list(SYSTEMS), help="GFE systems, comma-separated")
    result.add_argument("--datasets", type=lambda v: split_names(v, DATASETS), default=list(DATASETS), help="dataset stems, comma-separated")
    result.add_argument("--skip-prepare", action="store_true", help="use an already prepared data cache")
    return result


def export(p):
    from gfe_reports import render_gfe_reports
    from study_reports import render_study_reports
    from batch_baselines import render_batch_reports
    for name, render in (("gfe", lambda: render_gfe_reports(p.output, p.jobs, p.profile)),
                         ("batch", lambda: render_batch_reports(p.output)),
                         ("studies", lambda: render_study_reports(p.output))):
        try:
            report = render()
            for issue in (report or {}).get("issues", []) if isinstance(report, dict) else []:
                reason = str(issue)
                if not any(j.get("reason") == reason for j in p.jobs):
                    p.note("report-" + name, "failed", reason)
        except Exception as error:
            p.note("report-" + name, "failed", str(error))
    from upstream_plots import render_original_plots
    plotting = render_original_plots(p.output, p.jobs, p.gfe, p.radix, p.profile)
    for issue in plotting["issues"]:
        reason = str(issue)
        if not any(j.get("reason") == reason for j in p.jobs):
            p.note("report-original-plots", "failed", reason)
    p.manifest["plotting"] = "plotting/manifest.json"
    config = p.manifest["config"]
    stages = set(config["stages"])
    if "all" in stages:
        stages = {"prepare", "main", "studies", "batch"}
    expected = []
    if "main" in stages:
        expected += ["figures/figure8.pdf", "figures/figure10.pdf"]
        if set(config["datasets"]) & {"graph500-24", "uniform-24"}:
            expected.append("figures/figure9.pdf")
        if (p.profile == "paper" and "dota-league" in config["datasets"]
                and {"radixgraph", "gtx"} <= set(config["systems"])):
            expected.append("figures/figure11.pdf")
    if "studies" in stages:
        expected += ["figures/figure12_a.pdf", "figures/figure12_b.pdf", "figures/figure13.pdf",
                     "tables/figure12_transformations.csv", "tables/figure12_transformations.md",
                     "tables/table5.csv", "tables/table6.csv", "tables/table7.csv"]
        if p.profile == "paper":
            expected.append("figures/figure12_c.pdf")
    if "batch" in stages:
        expected += ["tables/table7_baselines.csv", "tables/table7.csv"]
    missing = [path for path in expected if not (p.output / path).is_file()]
    for path in missing:
        if not any(j.get("name") == "missing-artifact-" + path for j in p.jobs):
            p.note("missing-artifact-" + path, "failed", "Expected artifact was not produced: " + path)
    full_scope = (stages >= {"main", "studies", "batch"} and set(config["datasets"]) == set(DATASETS)
                  and set(config["systems"]) == set(SYSTEMS))
    atomic_json(p.output / "coverage.json", dict(full_experiment_scope=full_scope, profile=p.profile,
                                                expected_artifacts=expected, missing_artifacts=missing,
                                                renderer="original upstream scripts",
                                                plot_limitations=[r for r in plotting["plots"] if r["status"] == "skipped"],
                                                table_only_panels=["Figure 12(d): no original renderer is provided; see tables/figure12_transformations.md"]))
    failures = [j for j in p.jobs if j["status"] not in ("ok", "expected_omission")]
    p.manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
    p.manifest["status"] = "incomplete" if failures else "finished"
    p.save()
    artifacts = sorted(str(f.relative_to(p.output)) for sub in ("figures", "tables")
                       for f in (p.output / sub).rglob("*") if f.is_file())
    (p.output / "artifacts.json").write_text(json.dumps(artifacts, indent=2) + "\n")
    lines = ["# RadixGraph reproduction run", "", f"Profile: **{p.profile}**. Status: **{p.manifest['status']}**.", "",
             "Coverage: **" + ("full experiment grid" if full_scope else "selected experiment subset") + "**; see [coverage.json](coverage.json).", "",
             "Tables contain this run's measurements. Figures use the original upstream plotting scripts and their original axes/progress calculations.",
             "See [plotting/manifest.json](plotting/manifest.json) for script hashes, inputs, execution logs, and retained upstream assumptions.",
             "The smoke profile verifies wiring with synthetic data; it is not a paper result.", "",
             "## Artifacts", ""] + [f"- [{a}]({a})" for a in artifacts]
    lines += ["", "## Original plotting", "",
              "Original plotting sources are unchanged. Figure 12(d) is retained as a measured table because no original renderer is provided.", ""]
    for plot in plotting["plots"]:
        lines.append(f"- {plot['artifact']}: {plot['status']}. " + plot.get("reason", ""))
        for note in plot.get("notes", []):
            lines.append("  - " + str(note))
    lines += ["", "## Job status", "", "| Job | Status | Log |", "| --- | --- | --- |"]
    for j in p.jobs:
        log = Path(j["log"])
        if j["log"]:
            log = p.output / "logs" / log.name
        link = f"[log]({log.relative_to(p.output)})" if j["log"] and log.is_relative_to(p.output) else j.get("reason", "")
        lines.append(f"| {j['name']} | {j['status']} | {link} |")
    (p.output / "README.md").write_text("\n".join(lines) + "\n")
    return failures


def main():
    args = parser().parse_args()
    p = Pipeline(args)
    print("Output directory: " + str(p.output), flush=True)
    stages = {"prepare", "main", "studies", "batch"} if "all" in args.stages else set(args.stages)
    exit_code = 0
    try:
        if not args.skip_prepare and (stages & {"prepare", "main", "studies", "batch"}):
            env = ["env", f"DATA_DIR={p.datasets}", f"GFE_DIR={p.gfe}", f"PROFILE={p.profile}"]
            prepared = p.run("prepare-data", env + ["bash", HERE / "prepare-data.sh"], HERE)
            if prepared["status"] != "ok":
                raise RuntimeError("Dataset preparation failed; see prepare-data.log")
        if stages & {"main", "studies", "batch"}:
            p.snapshot_inputs()
        if "main" in stages:
            p.run_main()
        if "studies" in stages:
            from studies import run_studies
            run_studies(p)
        if "batch" in stages:
            from batch_baselines import run_batch_baselines
            run_batch_baselines(p)
    except KeyboardInterrupt:
        exit_code = 130
        p.note("pipeline-interrupted", "interrupted", "Interrupted; completed logs and artifacts retained")
    except Exception as error:
        exit_code = 1
        p.note("pipeline-error", "failed", str(error))
    finally:
        try:
            if export(p):
                exit_code = exit_code or 1
        except Exception as error:
            p.note("report-error", "failed", str(error))
            exit_code = exit_code or 1
        print("Artifacts retained at " + str(p.output), flush=True)
    return exit_code


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    sys.exit(main())
