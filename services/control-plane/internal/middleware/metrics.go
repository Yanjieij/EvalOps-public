package middleware

import (
	"strconv"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/yanjieij/evalops/control-plane/internal/observability"
)

// Metrics counts and times every HTTP request. Path is read from
// c.FullPath() so we get the template (/api/v1/runs/:id) instead of the
// resolved URL — this prevents cardinality blow-up.
func Metrics() gin.HandlerFunc {
	counter := observability.HTTPRequestsTotal()
	latency := observability.HTTPRequestLatency()
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		path := c.FullPath()
		if path == "" {
			path = "unknown"
		}
		status := strconv.Itoa(c.Writer.Status())
		counter.WithLabelValues(c.Request.Method, path, status).Inc()
		latency.WithLabelValues(c.Request.Method, path).Observe(time.Since(start).Seconds())
	}
}
