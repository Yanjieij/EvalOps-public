# EvalOps local infrastructure

One `docker-compose.yml` boots everything the dev loop needs:

| Service | Host port | Purpose |
|---|---|---|
| Postgres | 5452 | Benchmark / run / case metadata |
| Redis | 6389 | Judge result cache, job locks |
| MinIO | 9010 (API) / 9011 (console) | Dataset artifacts, judge log dumps |
| Jaeger | 16696 (UI) / 4327 (OTLP gRPC) / 4328 (OTLP HTTP) | Distributed tracing |
| Prometheus | 9091 | Scrapes control-plane + eval-engine |
| Grafana | 3001 | `admin` / `admin`, EvalOps Overview pre-provisioned |

Ports are deliberately offset from a companion app's defaults so both stacks
can run side-by-side.

## Usage

```bash
# from the repo root
make infra-up     # docker compose up -d
make infra-ps     # health check
make infra-down   # stop, keep volumes
make infra-nuke   # stop and wipe volumes
```

## Grafana dashboards

Dashboards live in `grafana/dashboards/*.json` and are auto-provisioned
via `grafana/provisioning/dashboards/dashboards.yml`. Week 1 ships a
single `EvalOps Overview` panel showing:

- Runs submitted / second
- Latest pass rate per (benchmark, sut)
- Run cost burn (µUSD / hour)
- Control-plane HTTP RPS and p95 latency

Week 2 will add per-capability pass rate, judge agreement kappa, and cost
attribution by judge model.

## Prometheus targets

`prometheus/prometheus.yml` scrapes two host endpoints via
`host.docker.internal`:

- `:8090/metrics` — control-plane
- `:9100/metrics` — eval-engine (when running the `evalops serve` worker
  with `EVALOPS_PROMETHEUS_PORT=9100`)

If you bind either service to a different port, edit `prometheus.yml`
and reload with `curl -X POST http://localhost:9091/-/reload`.
