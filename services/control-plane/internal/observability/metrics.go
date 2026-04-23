// Package observability exposes Prometheus primitives for the control-plane.
//
// We deliberately scope metrics to EvalOps domain concerns — request
// latency is useful but the main story is evalops_runs_submitted_total,
// evalops_run_duration_seconds, and evalops_run_cost_micro_usd_total.
// Those move the needle on our SLOs.
package observability

import (
	"sync"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
)

var (
	once     sync.Once
	registry *prometheus.Registry

	httpRequestsTotal  *prometheus.CounterVec
	httpRequestLatency *prometheus.HistogramVec

	runsSubmittedTotal *prometheus.CounterVec
	runDurationSeconds *prometheus.HistogramVec
	runCostMicroUSD    *prometheus.CounterVec
	runPassRate        *prometheus.GaugeVec
)

// PromRegistry returns a process-local Prometheus registry. Avoiding the
// default registry keeps our metrics free of Go runtime noise unless we
// opt in (which we do by registering the process collector below).
func PromRegistry() *prometheus.Registry {
	once.Do(initRegistry)
	return registry
}

func initRegistry() {
	registry = prometheus.NewRegistry()

	httpRequestsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "evalops_cp_http_requests_total",
			Help: "Count of HTTP requests handled by the control-plane.",
		},
		[]string{"method", "path", "status"},
	)
	httpRequestLatency = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "evalops_cp_http_request_duration_seconds",
			Help:    "HTTP request latency in seconds.",
			Buckets: prometheus.DefBuckets,
		},
		[]string{"method", "path"},
	)

	runsSubmittedTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "evalops_runs_submitted_total",
			Help: "Count of runs submitted to the control-plane.",
		},
		[]string{"benchmark", "sut"},
	)
	runDurationSeconds = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "evalops_run_duration_seconds",
			Help:    "Wall-clock duration of an evaluation run.",
			Buckets: []float64{1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600},
		},
		[]string{"benchmark", "sut"},
	)
	runCostMicroUSD = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "evalops_run_cost_micro_usd_total",
			Help: "Cumulative run cost in micro-USD.",
		},
		[]string{"benchmark", "sut"},
	)
	runPassRate = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "evalops_run_pass_rate",
			Help: "Pass rate of the last completed run, per benchmark/sut.",
		},
		[]string{"benchmark", "sut"},
	)

	registry.MustRegister(
		collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}),
		collectors.NewGoCollector(),
		httpRequestsTotal,
		httpRequestLatency,
		runsSubmittedTotal,
		runDurationSeconds,
		runCostMicroUSD,
		runPassRate,
	)
}

// HTTPRequestsTotal returns the handler-facing counter; Metrics middleware
// uses it.
func HTTPRequestsTotal() *prometheus.CounterVec {
	once.Do(initRegistry)
	return httpRequestsTotal
}

// HTTPRequestLatency returns the handler-facing histogram.
func HTTPRequestLatency() *prometheus.HistogramVec {
	once.Do(initRegistry)
	return httpRequestLatency
}

// RunsSubmittedTotal is incremented by the run submission handler.
func RunsSubmittedTotal() *prometheus.CounterVec {
	once.Do(initRegistry)
	return runsSubmittedTotal
}
