# Result schema v1

Each run lives at `results/<campaign>/<run_id>/`. `manifest.json` is JSON and records the immutable campaign dimensions, implementation image ID, Git revision, timestamps, resource limits, load model, seed and status. It never contains the encryption key or database credentials.

`requests.csv` contains one row per Gatling `REQUEST` event from the measured phase. Columns are `request_id` (string), `endpoint` (stable request name), `phase`, UTC timestamps, `duration_ms` (integer), `success` (boolean string), `status_http` (nullable; Gatling logs do not always expose it), and `error_class` (nullable). Warmup and drain rows are excluded.

Gatling 3.14.9 writes `simulation.log` in a binary format. The raw log and HTML report are still preserved, but a run whose decoder cannot produce request rows is marked `summary.status=invalid` and must be excluded from rankings until a compatible decoder or equivalent equal-cost instrumentation is supplied.

`resources.csv` contains one row per Docker container per sample. `cpu_usage_seconds` and `cpu_throttled_seconds` are cumulative cgroup counters, `memory_current_bytes` is total cgroup memory rather than RSS, and `memory_limit_bytes` is the effective limit. A one-second sample is an observed series and its maximum is not an absolute kernel peak. `restarts` and `oom_killed` are container-level signals.

`summary.json` contains `status` (`complete`, `failed`, or `invalid`), counts, measured duration, throughput definitions, latency percentiles, errors, and any generator or infrastructure validity reason. Missing fields remain null; they are never represented as zero. The report generator must aggregate repetitions from raw rows rather than average per-run percentiles.
