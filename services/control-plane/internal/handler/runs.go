package handler

import (
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/rs/zerolog/log"

	"github.com/yanjieij/evalops/control-plane/internal/observability"
)

// SubmitRunRequestDTO is the Week 1 request shape. It matches the subset
// of evalops.v1.SubmitRunRequest the control-plane exposes over HTTP. The
// full gRPC contract ships in Week 2 when we generate stubs.
type SubmitRunRequestDTO struct {
	BenchmarkID      string `json:"benchmark_id"          binding:"required"`
	BenchmarkVersion string `json:"benchmark_version"`
	SutName          string `json:"sut_name"              binding:"required"`
	SutKind          string `json:"sut_kind"              binding:"required"`
	JudgeKind        string `json:"judge_kind"            binding:"required"`
	Concurrency      int    `json:"concurrency"`
	MaxCases         int    `json:"max_cases"`
	IdempotencyKey   string `json:"idempotency_key"`
}

// SubmitRunStub accepts a run submission, increments metrics, and returns
// an accepted response with a generated run ID. Week 2 will push the run
// through a scheduler and eval-engine gRPC call.
func SubmitRunStub(c *gin.Context) {
	var req SubmitRunRequestDTO
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	runID := uuid.NewString()
	if req.Concurrency == 0 {
		req.Concurrency = 4
	}

	observability.RunsSubmittedTotal().
		WithLabelValues(req.BenchmarkID, req.SutName).
		Inc()

	reqID, _ := c.Get("request_id")
	log.Info().
		Str("request_id", toString(reqID)).
		Str("run_id", runID).
		Str("benchmark", req.BenchmarkID).
		Str("sut", req.SutName).
		Str("judge", req.JudgeKind).
		Int("concurrency", req.Concurrency).
		Msg("run.submit")

	c.JSON(http.StatusAccepted, gin.H{
		"run_id":      runID,
		"status":      "pending",
		"submitted_at": time.Now().UTC().Format(time.RFC3339),
		"note":        "Week 1 stub; scheduler wiring in Week 2",
	})
}

// GetRunStub returns a minimal mocked run object — enough to exercise the
// path between the control-plane and any client that wants to poll.
func GetRunStub(c *gin.Context) {
	id := c.Param("id")
	c.JSON(http.StatusOK, gin.H{
		"run_id":  id,
		"status":  "pending",
		"message": "Week 1 stub; real state store wiring in Week 2",
	})
}

func toString(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}
