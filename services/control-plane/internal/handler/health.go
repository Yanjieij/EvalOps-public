// Package handler contains the Gin request handlers for the control-plane.
package handler

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

// Healthz is a liveness probe — returns 200 as long as the process is up.
func Healthz(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

// Readyz is a readiness probe — Week 1 returns 200 unconditionally; once
// we wire up the eval-engine gRPC client and Postgres pool, Readyz will
// pre-flight both.
func Readyz(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"status":      "ok",
		"dependencies": gin.H{
			"eval_engine": "not_wired",
			"postgres":    "not_wired",
			"redis":       "not_wired",
		},
	})
}
