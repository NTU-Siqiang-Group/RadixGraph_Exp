#!/usr/bin/env python3
"""Sequential paper experiments, durable job logs, and artifact generation."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
DATASETS = ("com-lj.ungraph", "dota-league", "com-orkut.ungraph", "graph500-24", "uniform-24", "twitter-2010")
FIGURE_STAGES = tuple(f"figure{n}" for n in range(8, 14))
DEFAULT_STAGES = frozenset({"prepare", "main", "figure12", "figure13"})
EXPERIMENT_STAGES = {"main", "studies", "batch", "tables", *FIGURE_STAGES}
SYSTEMS = {"radixgraph": "radixgraph", "bvgt": "bvgt", "teseo": "teseo.13", "sortledton": "sortledton.4", "gtx": "gtx"}


def load_source_versions():
    for path in (HERE.parent / "source-versions.json", Path("/workspace/build-manifest.json")):
        if path.is_file():
            return json.loads(path.read_text())
    return {}


def runtime_capabilities(sources=None):
    """Prefer image capabilities, including when the image runs under emulation."""
    sources = load_source_versions() if sources is None else sources
    machine = sources.get("architecture") or platform.machine()
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine.lower(), machine.lower())
    available = sources.get("available_systems")
    if available is None:
        available = [system for system in SYSTEMS if architecture != "arm64" or system != "bvgt"]
    if not isinstance(available, list) or not available or set(available) - set(SYSTEMS):
        raise ValueError("Image manifest available_systems must list known GFE systems")
    available = list(dict.fromkeys(available))
    exclusions = dict(sources.get("excluded_systems", {}))
    for system in (name for name in SYSTEMS if name not in available):
        exclusions.setdefault(system, "Spruce/bvgt requires x86-64 and is excluded from ARM64 images"
                              if system == "bvgt" and architecture == "arm64"
                              else "System is not included in this image")
    return dict(architecture=architecture, available_systems=available,
                excluded_systems=exclusions)


def select_systems(value, capabilities):
    names = split_names(value, SYSTEMS)
    unavailable = [system for system in names if system not in capabilities["available_systems"]]
    if unavailable:
        reasons = [system + ": " + capabilities["excluded_systems"][system] for system in unavailable]
        raise argparse.ArgumentTypeError("Unavailable systems: " + "; ".join(reasons))
    return names


def gfe_job_name(system, spec, experiment, variant=""):
    return "-".join(filter(None, [experiment, system, spec["name"], variant]))


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


def expand_stages(names):
    stages = set(names)
    if "all" in stages:
        stages.remove("all")
        stages.update(DEFAULT_STAGES)
    return stages


class Pipeline:
    def __init__(self, args):
        self.args = args
        sources = load_source_versions()
        self.capabilities = runtime_capabilities(sources)
        if args.stages != ["report"]:
            args.systems = select_systems(",".join(args.systems), self.capabilities)
        self.profile = args.profile
        self.gfe = args.gfe_dir.resolve()
        self.radix = args.radix_dir.resolve()
        self.datasets = args.data_dir.resolve()
        self.threads = args.threads or (64 if self.profile == "paper" else 2)
        if self.threads < 1:
            raise ValueError("threads must be positive")
        if not math.isfinite(args.timeout_hours) or args.timeout_hours < 0:
            raise ValueError("timeout-hours must be finite and nonnegative (0 disables the limit)")
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
        self.progress = None
        self.progress_pending = set()
        self.manifest_path = self.output / "manifest.json"
        if self.manifest_path.exists():
            if args.stages != ["report"]:
                raise ValueError("Run directory already has a manifest; choose a new directory or --stages report")
            self.manifest = json.loads(self.manifest_path.read_text())
            self.jobs = self.manifest["jobs"]
            self.profile = self.manifest["profile"]
            self.capabilities = self.manifest.get("capabilities", self.capabilities)
        else:
            self.manifest = dict(profile=self.profile, capabilities=self.capabilities,
                                 started_utc=datetime.now(timezone.utc).isoformat(),
                                 config=vars(args), threads=self.threads, jobs=self.jobs,
                                 host=dict(platform=platform.platform(), cpu_count=os.cpu_count(),
                                           cpuinfo=Path("/proc/cpuinfo").read_text() if Path("/proc/cpuinfo").exists() else "",
                                           meminfo=Path("/proc/meminfo").read_text() if Path("/proc/meminfo").exists() else ""))
            if sources:
                self.manifest["sources"] = sources
            self.save()

    def save(self):
        atomic_json(self.manifest_path, self.manifest)

    def planned_jobs(self, stages):
        """Count fixed benchmark tasks; adaptive optimizer calls are setup."""
        from studies import planned_study_jobs
        from batch_baselines import planned_batch_jobs

        stages = expand_stages(stages)
        names = {"report"}
        if not self.args.skip_prepare and stages & ({"prepare"} | EXPERIMENT_STAGES):
            names.add("prepare-data")
        if "main" in stages or stages & set(FIGURE_STAGES[:4]):
            figures = None if "main" in stages else stages & set(FIGURE_STAGES[:4])
            names.update(gfe_job_name(system, spec, experiment, variant)
                         for system, spec, experiment, variant, _ in self.main_jobs(figures))
        artifacts = study_artifacts(stages)
        if artifacts:
            names.update(planned_study_jobs(self.profile, self.args.datasets, artifacts))
        if "batch" in stages:
            names.update(planned_batch_jobs(self.profile, self.args.datasets))
        return names

    def start_progress(self, stages):
        if self.args.no_progress:
            return
        from progress import ProgressBar
        self.progress_pending = self.planned_jobs(stages)
        self.progress = ProgressBar(len(self.progress_pending))

    def message(self, text):
        if self.progress:
            self.progress.clear()
        print(text, flush=True)

    def complete_progress(self, name, status):
        if self.progress and name in self.progress_pending and status != "interrupted":
            self.progress_pending.remove(name)
            self.progress.complete(name, status)

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
            for suffix in ("", "-1.0", "-1.0-gtx-20", "-delete"):
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
        self.message(f"[{status}] {name}: {reason}")
        self.complete_progress(name, status)
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
        self.message(f"[run] {name}: {shlex.join(command)}")
        if self.progress:
            self.progress.start(name)
        started = time.monotonic()
        process = None
        interrupted = False
        refresh_stop = threading.Event()
        refresh_thread = None

        def refresh_progress():
            while not refresh_stop.wait(max(.01, self.progress.interval)):
                self.progress.refresh()

        with logfile.open("w") as stream:
            try:
                process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                           stdout=stream, stderr=subprocess.STDOUT, text=True, start_new_session=True)
                if self.progress:
                    refresh_thread = threading.Thread(target=refresh_progress, daemon=True)
                    refresh_thread.start()
                timeout = self.args.timeout_hours * 3600 if self.args.timeout_hours else None
                process.communicate(input=input_text, timeout=timeout)
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
            finally:
                refresh_stop.set()
                if refresh_thread:
                    refresh_thread.join()
        result["elapsed_seconds"] = time.monotonic() - started
        self.save()
        self.message(f"[{result['status']}] {name} ({result['elapsed_seconds']:.1f}s)")
        self.complete_progress(name, result["status"])
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
        name = gfe_job_name(system, spec, experiment, variant)
        # The paper omits these baselines only when loading Twitter's edges.
        # Vertex-only insertion/query/memory measurements still include them.
        if (self.profile == "paper" and spec["name"] == "twitter-2010"
                and system in ("teseo", "sortledton", "gtx") and experiment in ("random", "analytics")):
            return self.note(name, "expected_omission", "Full-graph Twitter run omitted as in the paper (baseline failure/timeout)", meta)
        if not self.optimize(system, spec):
            return self.note(name, "failed", "RadixGraph optimizer did not produce settings.txt", meta)
        binary = self.gfe / "build" / ("gfe_driver_" + system + ("_" + variant.split("-")[0] if variant else ""))
        graph = spec["vertices"] if experiment == "vertices" else spec["properties"]
        # GFE otherwise defaults to a one-hour limit per graph algorithm.
        # Optional process limits are enforced by run(), not by each kernel.
        command = [binary, "-G", graph, "-u", "-l", SYSTEMS[system], "-w", str(self.threads),
                   "--timeout", "0"]
        if experiment == "random":
            command += ["-R", "0"]
        elif experiment == "vertices":
            command += ["-R", "0", "--insert_vertex_only=true"]
        elif experiment == "analytics":
            command += ["-r", str(self.threads), "-R", "3", "--blacklist", "cdlp"]
        elif experiment in ("mixed", "delete", "concurrent"):
            suffix = "-delete" if experiment == "delete" else "-1.0" if experiment == "mixed" else ""
            if experiment == "mixed":
                full_graphlog = self.datasets / (spec["name"] + "-1.0.graphlog")
                fraction = .2 if system == "gtx" and spec["name"] in ("graph500-24", "uniform-24") else 1.0
                if fraction == .2:
                    suffix = "-1.0-gtx-20"
                meta.update(workload_fraction=fraction, full_graphlog=str(full_graphlog))
            graphlog = self.datasets / (spec["name"] + suffix + ".graphlog")
            if experiment == "mixed":
                meta["graphlog"] = str(graphlog)
            database = self.output / "raw" / (name + ".sqlite3")
            database.parent.mkdir(exist_ok=True)
            meta["database"] = str(database)
            aging_timeout = max(1, int(self.args.timeout_hours * 3600)) if self.args.timeout_hours else 0
            command += ["--log", graphlog, "--aging_timeout", f"{aging_timeout}s",
                        "--aging_memfp=true", "--aging_memfp_physical=true", "-d", database]
            if experiment == "delete":
                command[command.index("-w") + 1] = "2"
                command += ["--aging_release_memory=false", "--delete_all=true"]
            if experiment == "concurrent":
                command[command.index("-w") + 1] = str(meta["write_threads"])
                command += ["-r", str(meta["read_threads"]), "--aging_release_memory=true", "--mixed_workload=true"]
        command += extra or []
        return self.run(name, command, self.gfe_work, metadata=meta)

    def main_jobs(self, figures=None):
        figures = set(figures) if figures is not None else set(FIGURE_STAGES[:4])
        for system in self.args.systems:
            for spec in self.dataset_specs:
                for experiment in ("random", "vertices", "analytics"):
                    if ("figure10" if experiment == "analytics" else "figure8") in figures:
                        yield system, spec, experiment, "", None
                if "figure9" in figures and spec["name"] in ("graph500-24", "uniform-24"):
                    for experiment in ("mixed", "delete"):
                        yield system, spec, experiment, "", None
        dota = next((s for s in self.dataset_specs if s["name"] == "dota-league"), None)
        if dota and "figure11" in figures:
            for system in (s for s in self.args.systems if s in ("radixgraph", "gtx")):
                for hop in (1, 2):
                    for count in ([4, 8, 16, 32] if self.profile == "paper" else [1, 2]):
                        for axis in ("read", "write"):
                            fixed = 32 if self.profile == "paper" else 2
                            yield (system, dota, "concurrent", f"{hop}hop-{axis}-{count}",
                                   dict(hop=hop, axis=axis, count=count,
                                        read_threads=count if axis == "read" else fixed,
                                        write_threads=count if axis == "write" else fixed))

    def run_main(self, figures=None):
        for system, spec, experiment, variant, metadata in self.main_jobs(figures):
            self.gfe_job(system, spec, experiment, variant=variant, metadata=metadata)


def study_artifacts(stages):
    artifacts = stages & {"figure12", "figure13"}
    if "studies" in stages:
        artifacts |= {"figure12", "figure13", "table5", "table6", "table7"}
    if "tables" in stages:
        artifacts |= {"table5", "table6", "table7"}
    return artifacts


def parser():
    capabilities = runtime_capabilities()
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    result.add_argument("--profile", choices=("paper", "smoke"), default=os.getenv("PROFILE", "paper"),
                        help="smoke uses tiny synthetic fixtures and cannot reproduce paper measurements")
    result.add_argument("--stages", type=lambda v: split_names(v, ("all", "prepare", "main", "studies", "batch", "tables", "report", *FIGURE_STAGES)), default=["all"],
                        help="comma-separated stages; all runs preparation and Figures 8–13; tables and batch are opt-in; reports are always exported, including on failures")
    result.add_argument("--gfe-dir", type=Path, default=Path(os.getenv("GFE_DIR", "/workspace/gfe_driver")))
    result.add_argument("--radix-dir", type=Path, default=Path(os.getenv("RADIX_DIR", "/workspace/RadixGraph")))
    result.add_argument("--data-dir", type=Path, default=Path(os.getenv("DATA_DIR", "/data")), help="reusable dataset cache")
    result.add_argument("--output-dir", type=Path, default=Path(os.getenv("OUTPUT_DIR", "/output")), help="parent of timestamped run directories")
    result.add_argument("--run-dir", type=Path, help="explicit run directory, also used by --stages report")
    result.add_argument("--threads", type=int, default=int(os.getenv("THREADS", "0")), help="workers, 0 selects paper=64/smoke=2")
    result.add_argument("--timeout-hours", type=float, default=float(os.getenv("TIMEOUT_HOURS", "0")),
                        help="optional wall-clock limit for each job; 0 disables the limit")
    result.add_argument("--systems", type=lambda v: select_systems(v, capabilities),
                        default=capabilities["available_systems"],
                        help="GFE systems, comma-separated; defaults to systems available in this image")
    result.add_argument("--datasets", type=lambda v: split_names(v, DATASETS), default=list(DATASETS), help="dataset stems, comma-separated")
    result.add_argument("--skip-prepare", action="store_true", help="use an already prepared data cache")
    result.add_argument("--no-progress", action="store_true", help="disable the job progress bar and periodic updates")
    result.add_argument("--check-requirements", action="store_true",
                        help="check machine resources and exit without preparing data or running experiments")
    result.add_argument("--force", action="store_true",
                        help="continue despite unmet requirements, printing and recording the shortfalls")
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
    from table_figures import render_table_figures
    try:
        table_figures = render_table_figures(p.output)
        for issue in table_figures["issues"]:
            reason = str(issue)
            if not any(j.get("reason") == reason for j in p.jobs):
                p.note("report-table-figures", "failed", reason)
    except Exception as error:
        table_figures = dict(figures=[], tables=[], issues=[str(error)])
        p.note("report-table-figures", "failed", str(error))
    p.manifest["table_figures"] = table_figures
    from upstream_plots import render_original_plots
    plotting = render_original_plots(p.output, p.jobs, p.gfe, p.radix, p.profile)
    for issue in plotting["issues"]:
        reason = str(issue)
        if not any(j.get("reason") == reason for j in p.jobs):
            p.note("report-original-plots", "failed", reason)
    p.manifest["plotting"] = "plotting/manifest.json"
    config = p.manifest["config"]
    stages = expand_stages(config["stages"])
    expected = []
    if "main" in stages or stages & set(FIGURE_STAGES[:4]):
        expected += [f"figures/{figure}.pdf" for figure in ("figure8", "figure10")
                     if "main" in stages or figure in stages]
        if ("main" in stages or "figure9" in stages) and set(config["datasets"]) & {"graph500-24", "uniform-24"}:
            expected.append("figures/figure9.pdf")
        if (("main" in stages or "figure11" in stages) and p.profile == "paper" and "dota-league" in config["datasets"]
                and {"radixgraph", "gtx"} <= set(config["systems"])):
            expected.append("figures/figure11.pdf")
    if "studies" in stages or "figure12" in stages:
        expected += ["figures/figure12_a.pdf", "figures/figure12_b.pdf",
                     "tables/figure12_transformations.csv"]
        if p.profile == "paper":
            expected.append("figures/figure12_c.pdf")
    if "studies" in stages or "figure13" in stages:
        expected.append("figures/figure13.pdf")
    if stages & {"studies", "tables"}:
        expected += [f"{folder}/table{n}.{extension}" for n in (5, 6, 7)
                     for folder, extension in (("tables", "csv"), ("figures", "pdf"))]
    if "batch" in stages:
        expected += ["tables/table7_baselines.csv", "tables/table7.csv"]
        if "figures/table7.pdf" not in expected:
            expected.append("figures/table7.pdf")
    missing = [path for path in expected if not (p.output / path).is_file()]
    for path in missing:
        if not any(j.get("name") == "missing-artifact-" + path for j in p.jobs):
            p.note("missing-artifact-" + path, "failed", "Expected artifact was not produced: " + path)
    full_stages_and_datasets = (("main" in stages or set(FIGURE_STAGES[:4]) <= stages)
                  and ("studies" in stages or {"figure12", "figure13", "tables"} <= stages)
                  and "batch" in stages and set(config["datasets"]) == set(DATASETS))
    full_scope = full_stages_and_datasets and set(config["systems"]) == set(SYSTEMS)
    full_available_scope = (full_stages_and_datasets
                            and set(config["systems"]) == set(p.capabilities["available_systems"]))
    atomic_json(p.output / "coverage.json", dict(full_experiment_scope=full_scope,
                                                full_available_scope=full_available_scope,
                                                capabilities=p.capabilities, selected_systems=config["systems"], profile=p.profile,
                                                expected_artifacts=expected, missing_artifacts=missing,
                                                renderer="original upstream scripts",
                                                table_renderer="paper-style measured tables",
                                                plot_limitations=[r for r in plotting["plots"] if r["status"] == "skipped"],
                                                table_only_panels=["Figure 12(d): no original renderer is provided; see tables/figure12_transformations.csv"]))
    failures = [j for j in p.jobs if j["status"] not in ("ok", "expected_omission")]
    p.manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
    p.manifest["status"] = "incomplete" if failures else "finished"
    p.save()
    artifacts = sorted(str(f.relative_to(p.output)) for sub in ("figures", "tables")
                       for f in (p.output / sub).rglob("*") if f.is_file())
    (p.output / "artifacts.json").write_text(json.dumps(artifacts, indent=2) + "\n")
    lines = ["# RadixGraph reproduction run", "", f"Profile: **{p.profile}**. Status: **{p.manifest['status']}**.", "",
             "Coverage: **" + ("full experiment grid" if full_scope else "selected experiment subset") + "**; see [coverage.json](coverage.json).", "",
             "CSV tables retain this run's full-precision measurements. Table PDFs in figures/ follow the paper's layout and contain only the table grid, headers and values.",
             "Experimental plots retain the original upstream drawing code. Figure 9 mixed-update progress uses measured operation counts, completion times and the recorded workload fraction (GTX's 20% prefix for new runs; legacy records default to full workloads).",
             "See [plotting/manifest.json](plotting/manifest.json) for script hashes, inputs, execution logs, and retained upstream assumptions.",
             "The smoke profile verifies wiring with synthetic data; it is not a paper result.", ""]
    if p.capabilities["excluded_systems"]:
        lines += ["## Image capabilities", "", "Architecture: **" + p.capabilities["architecture"] + "**.", ""]
        lines += [f"- {system}: {reason}." for system, reason in p.capabilities["excluded_systems"].items()]
        lines.append("")
    lines += ["## Artifacts", ""] + [f"- [{a}]({a})" for a in artifacts]
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
    from preflight import check_requirements, print_requirements, RequirementsError
    try:
        resources = check_requirements(args)
    except RequirementsError as error:
        print("ERROR: " + str(error), file=sys.stderr, flush=True)
        return 2
    print_requirements(resources)
    if args.check_requirements:
        return 0
    p = Pipeline(args)
    if resources["status"] == "ok" or "requirements" not in p.manifest:
        p.manifest["requirements"] = resources
    p.save()
    print("Output directory: " + str(p.output), flush=True)
    stages = expand_stages(args.stages)
    p.start_progress(stages)
    exit_code = 0
    try:
        if not args.skip_prepare and (stages & ({"prepare"} | EXPERIMENT_STAGES)):
            env = ["env", f"DATA_DIR={p.datasets}", f"GFE_DIR={p.gfe}", f"PROFILE={p.profile}"]
            prepared = p.run("prepare-data", env + ["bash", HERE / "prepare-data.sh"], HERE)
            if prepared["status"] != "ok":
                raise RuntimeError("Dataset preparation failed; see prepare-data.log")
        if stages & EXPERIMENT_STAGES:
            p.snapshot_inputs()
        if "main" in stages or stages & set(FIGURE_STAGES[:4]):
            p.run_main(None if "main" in stages else stages & set(FIGURE_STAGES[:4]))
        if stages & {"studies", "tables", "figure12", "figure13"}:
            from studies import run_studies
            run_studies(p, study_artifacts(stages))
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
            if p.progress:
                p.progress.start("report")
            failures = export(p)
            p.complete_progress("report", "failed" if failures else "ok")
            if failures:
                exit_code = exit_code or 1
        except KeyboardInterrupt:
            p.note("report-interrupted", "interrupted", "Report interrupted; completed artifacts retained")
            exit_code = 130
        except Exception as error:
            p.note("report-error", "failed", str(error))
            p.complete_progress("report", "failed")
            exit_code = exit_code or 1
        finally:
            if p.progress:
                status = "interrupted" if exit_code == 130 else "incomplete" if exit_code else "finished"
                p.progress.finish(status)
        p.message("Artifacts retained at " + str(p.output))
    return exit_code


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    sys.exit(main())
