# MLCommons Endpoints Submission Tools

A Python package with two tools for managing MLPerf Endpoints benchmark submissions:

- **`endpoints-submission-cli`** — registers benchmark runs, assembles submission packages, runs compliance checks, and opens GitHub pull requests via the PRISM API.
- **`submission-checker`** — validates a submission folder against the §9.1 automated compliance rules before or after upload.

---

## Installation

**With pip:**

```bash
pip install endpoints-submission-cli
```

**From source (editable):**

```bash
pip install -e ".[dev]"
```

**With [uv](https://github.com/astral-sh/uv):**

```bash
uv sync --extra dev
```

---

# endpoints-submission-cli

## Requirements

- Python 3.10 or later
- [`gh` CLI](https://cli.github.com/) — required for creating, updating, and withdrawing submissions

## Authentication

Every command requires a PRISM API token in `mlc_…` format. Supply it as an env var or pass `--token` per command:

```bash
# Persistent (add to shell profile)
export PRISM_USER_API_TOKEN=mlc_your_token_here

# Per-command override
endpoints-submission-cli runs list --token mlc_your_token_here
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `PRISM_USER_API_TOKEN` | — | API key. Required unless `--token` is passed. |

Add to your shell profile for a persistent setup:

```bash
export PRISM_USER_API_TOKEN=mlc_your_token_here
```

## Quick start

```bash
# 1. Verify connectivity
endpoints-submission-cli runs list

# 2. Register a benchmark run from a local result folder
endpoints-submission-cli runs create --path /results/llama3_h100_c4
# → Run created: d5d9873e-5eca-4f8d-a487-4be1cb8b440c
RUN_ID=d5d9873e-5eca-4f8d-a487-4be1cb8b440c

# 3. Create a submission (assembles, checks, uploads, hands to review)
endpoints-submission-cli submissions create \
  --division standardized \
  --availability available \
  --run-ids $RUN_ID
# → Submission created: a1b2c3d4-…
SUB_ID=a1b2c3d4-e5f6-7890-abcd-ef1234567890

# 4. Withdraw if needed
endpoints-submission-cli submissions withdraw --submission-id $SUB_ID
```

### Adding shared `src/` and `docs/` content

`src/` and `docs/` are shared across a whole submission (§8.1), and are normally
assembled from each run folder's own `src/` and `documentation/`. Where the content
lives outside the runs, pass it on the command line:

```bash
endpoints-submission-cli submissions create \
  --division standardized --availability available --run-ids $RUN_ID \
  --shared-src ./implementations \
  --shared-docs ./disclosures
```

Both flags merge **contents**, so whatever shape the directory has is the shape the
bundle gets:

```
./implementations/        →   src/
├── trtllm/                   ├── trtllm/
│   └── README.md             │   └── README.md
└── vllm/                     └── vllm/
    └── README.md                 └── README.md
```

Both are repeatable, and both are **additive**: whatever the run archives supply is
still written, and the flags add to it. A file supplied by both a run and a flag is a
build error unless the bytes are identical — silently taking either side would put a
file in the bundle that neither source contains.

Everything else is unchanged, which is worth knowing for two cases:

- Every resulting `src/<implementation>/` must contain a `README.md` (§2.2.1),
  including the ones a flag added.
- Adding a second implementation makes `shared_src` ambiguous, so each `point.yaml`
  must then declare which one produced it. The builder will not guess.

`submissions create-local` has no equivalent flags: it takes an already-assembled tree,
so `src/` and `docs/` are already in it.

## Command reference

```
endpoints-submission-cli
├── runs
│   ├── list        List all runs
│   ├── create      Register a run from a local folder
│   ├── get         Fetch run details
│   ├── delete      Delete a run and its archive
│   ├── pin         Pin a run (prevent expiry)
│   └── unpin       Restore normal expiry
└── submissions
    ├── list        List all submissions
    ├── create      Create a submission from runs (full pipeline)
    ├── get         Fetch submission details
    ├── update      Update run list or metadata
    ├── withdraw    Withdraw a submission
    └── remove-run  Remove a run from a submission
```

Use `--help` on any command for full flag details:

```bash
endpoints-submission-cli submissions create --help
```

---

# submission-checker

CLI tool for validating MLPerf Endpoints submissions against the §9.1 automated compliance checks.

## Usage

### Check a submission

```bash
submission-checker check /path/to/submission
```

The path may be the submitting organisation's directory or a `<submission_id>/`
directory below it; a submission root is the level holding `results/` and `docs/` (§8.1).

**Options:**

| Flag | Description |
|------|-------------|
| `--strict` | Treat warnings as errors (exit 1 on any warning) |
| `--quiet` / `-q` | Suppress INFO-level passing checks |
| `--output FILE` / `-o FILE` | Write full results as JSON to *FILE* |
| `--seed-sets FILE` | Published seed sets to check against (§4.6). Defaults to the bundled set; also settable via `$MLPERF_ENDPOINTS_SEED_SETS`. |
| `--approved-drafters FILE` | Published approved drafters (§2.9.4). Defaults to the bundled list; also settable via `$MLPERF_ENDPOINTS_APPROVED_DRAFTERS`. |

**Exit codes:** `0` = all checks passed, `1` = one or more errors (or warnings with `--strict`).

### Native client reports and disabled warmup

The checker accepts the client's native accuracy report, including when
`accuracy_scores` is embedded in `results.json`:

```json
{"accuracy_scores": [{"dataset_name": "swe_bench", "score": 0.935,
                      "unit_samples": 200, "num_repeats": 1}]}
```

Entries are checked separately by dataset name. Legacy dataset-keyed mappings
remain supported. Native `unit_samples` and `num_repeats` feed the existing
sample-count and repeat checks; a report-level `average_accuracy` does not
replace per-dataset scores. Model recognition does not supply missing accuracy
thresholds or approve speculative-decoding drafters.

Summary percentile keys such as `"90"` and `"90.0"` are equivalent. Conflicting
values for equivalent keys are rejected. Parsing never rewrites measured files.

For a run with no warmup, declare:

```yaml
warmup:
  duration_s: 0
  requests_issued: 0
  requests_completed: 0
  data_source: "none; warmup disabled"
  concurrency: 0
```

Zero concurrency is accepted only when all three duration/request values are
zero. Such a declaration needs no warmup request logs. Active warmup still
requires positive concurrency and retains the log-disclosure checks.

### Show region boundaries

```bash
submission-checker regions --max-concurrency 1024 --min-concurrency 16
```

Prints the concurrency range for each region given a `(C_max, C_min)` pair, using the
§5.5 reference algorithm. `--min-concurrency` defaults to 32; in a real submission
`C_min` is derived from the lowest measurement point rather than declared (§5.4).

## Required Files in submission structure

Layout as of `mlcommons/endpoints_policies` PR #119: there is no per-system file — every
Pareto point carries its own `system_desc.json`.

```
<submitting_organization>/
└── <submission_id>/
    ├── src/
    │   └── <implementation>/         # §2.2.1 — README.md + endpoint interface code
    │       └── README.md
    ├── docs/                         # calibration, software disclosure, …
    └── results/
        └── <system>/
            ├── system_power.json      # §4.5.2 — REQUIRED, one per system
            └── <model_name>/
                └── r<N>/             # one directory per concurrency level
                    ├── point.yaml            # §8.3 measurement-point disclosure
                    ├── system_desc.json      # §8.2 — per point since PR #119
                    ├── result_summary.json   # aggregate metrics
                    ├── accuracy_results.json # §6.6 accuracy results
                    ├── config.yaml           # OPTIONAL as of v1.0
                    └── server_configs/       # OPTIONAL, submitter-defined
```

`src/` and `docs/` are shared across the whole submission; each `point.yaml` names them
via `shared_src` and `shared_docs`, which must resolve to a directory under the
submission root (§9.1).

## What gets checked

### Structure

| Rule | Spec | Description |
|------|------|-------------|
| `path-exists` | §1 | Submission root directory exists |
| `required-dir` | §1 | `results/` and `docs/` present |
| `src-dir` | §2.2.1 | `src/` present with at least one implementation directory |
| `src-readme` | §2.2.1 | Each `src/<implementation>/` has a `README.md` |
| `system-results-dir` | §1 | At least one `results/<system>/` directory exists |
| `benchmark-model-dir` | §1 | At least one benchmark-model directory per system |
| `point-dirs` | §1 | At least one `r<N>/` Pareto-point directory per model |
| `measurement-points-present` | §1 | Every `r<N>/` carries a `point.yaml` |
| `point-dirname-concurrency` | §1 | `r<N>/` name matches the declared concurrency (warn) |
| `result-summary-present` | §1 | `result_summary.json` exists for each point |
| `shared-path-resolution` | §9.1 | `shared_src` / `shared_docs` resolve under the submission root |

### System description (§8.2)

| Rule | Spec | Description |
|------|------|-------------|
| `system-description-present` | §8.2 | Every point has a `system_desc.json` |
| `system-description-valid` | §8.2 | It parses against the `SystemDescription` schema |
| `system-description-consistency` | §8.5 | Every point of a curve describes the same system |
| `model-name-valid` | §3.2 | `point.yaml`'s `model_name` is exactly one of the round's supported models, spelled canonically |
| `model-name-consistency` | §8.1 | It is exactly the results directory name |
| `max-concurrency-declared` | §7 | `max_supported_concurrency` (C_max) present and > 32 |
| `tps-utilization` | §8.2 | Equals `system_tps / max(system_tps)` over the point's own curve |
| `power-descriptor` | §4.5.2 | `system_power.json` present per system and states a power §4.5.2 can derive |
| `power-estimated` | §4.5.2 | Flags component groups left for MLCommons to auto-populate (warn) |

> The benchmark model name is read from **`point.yaml`** (§8.3), and from nowhere else.
> Policies PR #130 removed `model_name` from §8.2's `system_desc.json` table and template,
> and §8.5 now sources a result ID's `model_id` from the point's disclosure. A
> `system_desc.json` that still carries `model_name` or `model_id` parses, but the value
> decides nothing — a point that declares no name is incomplete, and
> `point-disclosure-complete` reports it.
>
> The name must be written in canonical form, which is also its directory name:
> `llama3_1-8b`, `gpt-oss-120b` or `deepseek-r1`. The checker does not rewrite it, so
> `llama3.1-8b` fails `model-name-valid`, and the error names the spelling to use.

§4.5.2's power model:

```
System Power     = Major_components + Other_components
Major_components = CPU_power + Accelerator_power + Network_scale_up_power
Other_components = overhead_fraction × Major_components
overhead_fraction = 0.30 liquid-cooled, 0.50 air-cooled
```

`system_power.json` is read with §4.5.2's own field names — `num_cpu`, `tdp_per_cpu`,
`num_accelerator`, `tdp_per_accelerator`, `num_switches`, `tdp_per_switch`,
`public_specification` — and with the generic `count` / `tdp_per_unit` / `link`
spellings, since §4.5.2 publishes names but no JSON schema.

Three details are easy to get wrong:

- **Scale-out network is not a major component.** §4.5.2 defines `Other_components` as
  "scale-out networking, storage, power-supply overhead, and cooling", so a declared
  scale-out group is already inside the overhead fraction. It is read and reported but
  never summed into the total, which would count it twice.
- **`overhead_fraction` comes from the cooling method**, not from the submitter. §8.2's
  system description already declares `cooling`, so the checker reads it from there
  (system level or `node_types[]`), and a system with mixed node cooling takes the
  air-cooled fraction — §4.5.2 estimates conservatively. Where no cooling method can be
  established and none is declared, that is an **error**, not an assumed zero: dropping
  `Other_components` shrinks the denominator by 23–33 % and inflates `system_tps_per_kw`.
- **Three paths give the total**, in §4.5.2's own order of precedence: a declared
  `provisioned_power_w`, then §4.5.2.1 rack-level node scaling
  (`rack_power_w × submitted_nodes / rack_nodes`), then the component formula. A
  combined `compute` group stands in for CPU + accelerator where a vendor publishes
  them as one figure.

### Regions (§5)

`C_min` is **derived** from the submission's own points in v1.0, not declared, so the
boundaries differ per curve. The 10 % margin above `C_max` is its own region and does
not satisfy High Concurrency coverage.

| Rule | Spec | Description |
|------|------|-------------|
| `region-basis` | §5.4 | Reports the derived `C_min` and how many points it came from |
| `region-computation` | §5.5 | `(C_max, C_min)` is a valid input to the reference algorithm |
| `concurrency-in-range` | §9.1 | Each concurrency falls in a valid region, margin included |
| `region-declared` | §8.3 | Declared `region` is one of the spec's values |
| `region-placement` | §8.3 | Declared region matches the computed one (warn) |
| `offline-declared` | §5.7 | `offline` is `dedicated`, `elected`, or `none` |
| `offline-point-present` | §5.7 | Exactly one Offline point, `elected` sitting on the C_max point — or **none at all** for an agentic benchmark, which §5.7 says "neither requires nor may include" one |
| `offline-ordering` | §5.7.2 | Offline beats C_max on throughput (2% tolerance) and concurrency (warn) |
| `ultra-low-concurrency-coverage` | §5.4 | At least one point at concurrency ≤ 32 |
| `low-concurrency-coverage` | §9.1 | At least one point in the Low Concurrency region |
| `med-concurrency-coverage` | §9.1 | At least one point in the Medium Concurrency region |
| `high-concurrency-coverage` | §9.1 | At least one point in the High Concurrency region |
| `point-count` | §5.3 | 7–32 measurement points; 8 with a dedicated Offline run; 7 for an agentic benchmark |
| `benchmark-type-consistency` | §6.1, §8.5 | Every point on a curve declares the same `load_pattern`, since one curve is one benchmark |
| `point-cap` | §2, §8 | Point count does not exceed 32 |

### Measurement points (§8.3, §6)

| Rule | Spec | Description |
|------|------|-------------|
| `point-config-valid` | §8.3 | `point.yaml` parses against the `PointConfig` schema |
| `point-disclosure-complete` | §8.3 | Every required §8.3 disclosure field is present |
| `load-pattern` | §6.1 | `load_pattern` is `concurrency` or `agentic_inference`, with a positive level |
| `streaming-config` | §6.5 | Reports client IPC chunk forwarding; either flag value is accepted and neither proves server streaming |
| `point-duration` | §6.2 | Steady-state window's issue-time span meets the region minimum (warn) |
| `steady-state-valid` | §4.4 | `status`, `verdict` and gating `state` use the spec's vocabulary |
| `steady-state-consistency` | §4.4 | The reported status agrees with the window it describes |
| `steady-state-basis` | §4.4 | Which basis supplies the official result; flags fallbacks and drift (warn) |
| `min-query-count` | §6.4 | `n_samples_completed` meets the dataset minimum |
| `warmup-present` | §6.3.3 | Warmup declaration present; concurrency zero is valid only with zero duration and request counts |
| `warmup-logs-retained` | §6.3.2 | Warmup log retention declared when warmup was performed (warn) |
| `warmup-salt` | §6.3.3 | Warns when the warmup salt is enabled |
| `config-consistency-dataset` | §9.1 | All points use the same dataset |
| `config-consistency-model` | §9.1 | All points declare the same `model_name` |

### Seed binding (§4.6)

| Rule | Spec | Description |
|------|------|-------------|
| `seed-set-consistency` | §9.1 | Every point records the same seed set |
| `seed-set-membership` | §9.1 | The bound set is one MLCommons published |
| `seed-runtime-match` | §2.1.1 | The RNG seeds equal the bound set's values |
| `target-cohort` | §4.6 | `target_cohort` matches `YYYY-MM-C0` / `YYYY-MM-C1` |
| `seed-set-adoption` | §4.6 | `target_cohort` falls inside the set's four-cohort adoption window |
| `seed-config-legacy` | §4.6 | v0.7 fallback: seeds == 42 when no `seed_set` is declared |
| `seed-set-registry` | §4.6 | Warns when the seed-set file itself cannot be read |

### Speculative decoding (§2.9.4)

| Rule | Spec | Description |
|------|------|-------------|
| `approved-drafter` | §2.9.4 | The declared drafter is on the benchmark's published list |
| `drafter-approval-lead-time` | §2.9.4 | Approved at least two cohorts before `target_cohort` |
| `drafter-list-registry` | §2.9.4 | Warns when the drafter list itself cannot be read |

The approved list ships as data (`src/submission_checker/data/approved_drafters.yaml`)
and is **empty** — §2.9.4's list has not been published yet, and an empty registry means
speculative decoding is not permitted for any benchmark, which is §2.9.4's own rule for a
benchmark with no approved drafter. Point `--approved-drafters FILE` or
`$MLPERF_ENDPOINTS_APPROVED_DRAFTERS` at a published list.

The published sets ship as data (`src/submission_checker/data/seed_sets.yaml`), mirrored
from the policies repo's `seedset.yaml`. The file's `cohort-id` is the cohort its sets
were published for; §4.6's four-cohort adoption window is derived from it. Point
`--seed-sets FILE` or `$MLPERF_ENDPOINTS_SEED_SETS` at a newer file to check against a
set published after this release.

### Metrics (§9.1, §14)

| Rule | Spec | Description |
|------|------|-------------|
| `result-file-valid` | §8.3 | `result_summary.json` parses against `PointSummary` |
| `metric-consistency-duration` | §14 | `duration_ns` > 0 |
| `metric-consistency-accounting` | §14 | `completed + failed == issued` |
| `metric-consistency-output-tokens` | §14 | `total_output_tokens` ≥ 0 |
| `metric-consistency-system-tps` | §9.1 | Stored `system_tps` matches the derived value |
| `metric-consistency-tpot-p90` | §9.1 | Reported TPOT P90 present, finite, strictly positive |
| `metric-consistency-tps-per-user` | §9.1 | Stored `tps_per_user` matches `1000 / tpot_p90_ms` |
| `metric-consistency-tps-per-kw` | §4.5.3 | Stored `system_tps_per_kw` matches `system_tps / provisioned_power_kw` |
| `agentic-metric-consistency` | §4.1 | `e2e_avg_interactivity` is derivable from its reported inputs |

### Accuracy (§15)

| Rule | Spec | Description |
|------|------|-------------|
| `accuracy-present` | §15 | At least one model in the submission carries accuracy results |
| `accuracy-coverage` | §5.3 | Accuracy at each of the four mandatory bands, plus the Offline point (N=5; N=4 for an agentic benchmark, which has none) |
| `accuracy-valid` | §15 | `accuracy_results.json` parses correctly |
| `accuracy-sample-count` | §15 | Issued sample count meets the model's minimum |
| `accuracy-gate` | §15 | Score meets the benchmark quality target |
| `agentic-accuracy` | §3.2 | The agentic model is one the reference implementation publishes thresholds for, and they are not TBD |
| `agentic-accuracy-inline` | §4.3 | Inline accuracy clears the model's floor at **every** point |
| `agentic-accuracy-swebench` | §4.3 | The **mean** of the N SWE-bench results clears the model's floor; individual results need not |
| `agentic-osl-range` | §4.3 | Full-run OSL per-turn mean falls inside the model's range |

### Agentic benchmarks

§5.3, §5.7 and §9.1 apply differently to agentic benchmarks: the point minimum is 7
rather than 8, accuracy is required at 4 points rather than 5, and an agentic
submission "neither requires nor may include" an Offline point (§5.7).

§8.3 has no field naming the benchmark type. The checker reads it from §6.1's load
pattern instead — the reference implementation names its fixed-concurrency agentic
scheduler `agentic_inference`, and that is the only agentic signal in any file a
submission carries:

```yaml
runtime_settings:
  load_pattern: agentic_inference
```

A curve is one benchmark (§8.5), so every point must agree; `benchmark-type-consistency`
reports points that do not, and a curve that disagrees is read as single-turn, which
keeps the Offline requirement in force rather than letting one mislabelled point switch
it off.


Accuracy is gated differently too. §15's gate folds every dataset of a point into one
sample-weighted score per metric; the agentic benchmarks gate three quantities that do
not reduce that way, so for a recognised agentic model it stands down in favour of the
three rules above:

| Quantity | Source | Aggregation |
|---|---|---|
| Inline accuracy | `agentic_combined` in the accuracy results | per point — every point must clear |
| SWE-bench accuracy | `swe_bench` in the accuracy results | **mean-of-4** across the mandatory regions (§4.3's multi-turn branch) |
| OSL per-turn mean | `output_sequence_lengths_full_run.output_sequence_lengths.avg` in `result_summary.json` | per point, against a range |

The OSL field is resolved explicitly and never falls back to the windowed
`output_sequence_lengths` block, which has the same shape and a different value.

Thresholds come from the reference implementation's Agentic Inference example, which
§3.2 makes the authority. All three agentic models — Kimi K3 (`kimi-k3`),
Qwen3.6-35B-A3B (`qwen3_6-35b-a3b`) and DeepSeek-V4.1-Flash (`deepseek-v4_1-flash`) —
have published thresholds. A recognised model whose thresholds are TBD there would be
reported as ungateable rather than passed silently.

Both agentic scorers report a fraction in [0, 1], which is always rescaled to a
percentage; a value outside that range is an error, never read as a percentage already.
The SWE-bench mean takes one value per mandatory band: a result outside the four bands
is left out, and a band with several results is averaged into one value, with a warning.

## Programmatic API

```python
from submission_checker import SubmissionChecker, Report

checker = SubmissionChecker(Path("/submissions/acme_corp"))
report = checker.run()

if report.passed:
    print("All checks passed")
else:
    for result in report.errors:
        print(f"[{result.rule}] {result.message}")
```

The `Report` object also exposes `report.warnings` and serialises cleanly via `report.model_dump_json()`.

---

## Development

```bash
uv run pytest                          # run all tests
uv run pytest --no-cov -x             # fast fail on first error
uv run ruff check src/ tests/          # lint
uv run ruff format src/ tests/         # auto-format
```
