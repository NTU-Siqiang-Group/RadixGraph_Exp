# How I developed RadixGraph_Exp with Codex

I initially developed the repository with step-by-step Jupyter Notebook instructions and a minimal Docker example for reproducing experiments on the LiveJournal dataset (commit `869130bb06507d77ea32a3aa012e1887ccd5aa72`).

I then asked Codex to build a Docker pipeline for reproducing the full set of experiments, based on the notebook, the minimal example, and the GFE Driver and RadixGraph documentation. I used several GPT models, primarily GPT-6.1 Sol Ultra.

## From notebook guidance to a working pipeline

We first analyzed the existing artifacts, identified missing experiment coverage, and automated the process. The work then expanded into a pipeline covering **dataset preparation, benchmarks, and report generation**.

Our development loop had three steps:

1. **I set the goal.** I described the expected behavior, priorities, and acceptance criteria.
2. **Codex implemented and checked it.** It changed the scripts and Docker configuration, ran automated tests, and sometimes used specialist agents for parallel implementation and review.
3. **I reviewed the result.** I inspected the changes, ran experiments, and gave feedback on scientific fidelity, output quality, and usability. That feedback shaped the next round of work.

## Refinement

Testing the workflow myself supplied information that automated checks had missed:

- **Graph500-24 and Uniform-24 datasets exposed a preparation bug.** Codex implemented a smoke test (which is later integrated to GitHub workflow for automated testing). Synthetic smoke tests had missed the problem: the preparation script had changed these datasets' property files incorrectly. I clarified which datasets needed generated properties and which should retain their original files. Codex used this guidance and the logs to diagnose the failure, revise the script, and add regression checks.
- **Long experiments needed a more practical workflow.** I requested stage-by-stage commands, reusable cached data, early resource checks, and concise reproduction instructions. Permission errors and NUMA failures from actual runs also led to fixes.

These exchanges were an important part of development: I supplied the experiment context and evidence from real runs, while Codex translated that feedback into code changes and checks.

## One pipeline across platforms

Once the main pipeline was in place, I directed three related improvements:

- **ARM support.** I asked Codex to support ARM, while excluding the x64-dependent baselines (Spruce). Further debugging exposed a CPU-topology problem during preparation and for Teseo baseline. Therefore, the final archived ARM configuration fixed the data preparation issue, and excluded both Spruce and Teseo baselines as they are x64-dependent.
- **Four CI workflows.** I specified separate LiveJournal and smoke tests for both x64 and ARM64, making each combination visible independently.
- **Shared Docker infrastructure.** We migrated the remaining workflow away from the minimal Docker example (the initial version I developed) so that both architectures used the maintained main Docker pipeline, reducing duplicated maintenance.