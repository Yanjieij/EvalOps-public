// Package router wires up the Gin router, middleware, and handlers.
package router

import (
	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/yanjieij/evalops/control-plane/internal/config"
	"github.com/yanjieij/evalops/control-plane/internal/handler"
	"github.com/yanjieij/evalops/control-plane/internal/middleware"
	"github.com/yanjieij/evalops/control-plane/internal/observability"
)

// New constructs a fully-configured Gin engine.
// Middleware order matters: request ID must be set before structured
// logging so every log line carries it, and metrics must wrap both so
// request_id and path are both observable.
func New(cfg config.Config) *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()

	r.Use(gin.Recovery())
	r.Use(middleware.RequestID())
	r.Use(middleware.Logger())
	r.Use(middleware.Metrics())

	// Health + metrics
	r.GET("/healthz", handler.Healthz)
	r.GET("/readyz", handler.Readyz)
	r.GET("/metrics", gin.WrapH(promhttp.HandlerFor(observability.PromRegistry(), promhttp.HandlerOpts{})))

	// API v1 — Week 1 ships stubs to exercise the middleware + metrics.
	v1 := r.Group("/api/v1")
	{
		v1.POST("/runs", handler.SubmitRunStub)
		v1.GET("/runs/:id", handler.GetRunStub)
	}

	return r
}
