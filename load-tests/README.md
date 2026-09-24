# Gatling/Scala load tests

This project pins Gatling 3.14.9, Scala 2.13.17, Maven 3.9.11 and JDK 17. Gatling's Maven plugin is 4.15.0; Scala is compiled separately because current plugin 4.x no longer compiles Scala simulations itself. The versions follow Gatling's Maven/Scala documentation and official release metadata.

The two simulations deliberately keep workload models separate:

- `ClosedWorkloadSimulation`: `constantConcurrentUsers` with sequential loops.
- `OpenWorkloadSimulation`: `constantUsersPerSec`; each virtual user executes exactly one request.

Use `-DsimulationClass=...` with Maven. `baseUrl`, `scenario`, `payloadBytes`, `idsFile`, `phase`, `users`, `ratePerSecond`, `durationSeconds` and `seed` are explicit system properties. Request names are stable and contain no IDs. The simulation uses HTTP/1.1 keep-alive, no retries and no compression.

## Container commands

Build the image:

```bash
docker compose --profile go --profile load-test build gatling
```

Run a five-second smoke test against Go (the API must be running):

```bash
docker compose --profile go --profile load-test run --rm \
  -e BASE_URL=http://api-go:8080 -e SIMULATION=closed \
  -e SCENARIO=roundtrip -e PAYLOAD_BYTES=128 -e USERS=1 \
  gatling
```

The full orchestrator is `scripts/run_campaign.py`. It refuses to run without an explicit `--campaign` (`smoke` or `full`) and writes each run under `results/<campaign>/<run_id>/`. It prepares the 100,000-record GET dataset outside measurement, rotates implementation order, starts one API at a time, samples cgroups, stores Gatling reports/logs, and marks incomplete or generator-compromised runs accordingly.

`prepare_dataset.py` must be run before the measured window. Its POSTs are intentionally not included in Gatling summaries. The dataset is regenerated for each payload size and implementation baseline; the same deterministic logical content and count are used for every implementation.

Resource samples use Docker cgroup files when available. `memory.current` is total cgroup memory and is distinct from RSS; `memory.max` is the effective container limit. `cpu_usage_seconds` and `cpu_throttled_seconds` are cumulative counters. A one-second sampler reports an observed peak, not an absolute kernel peak. Host cache is never cleared.
