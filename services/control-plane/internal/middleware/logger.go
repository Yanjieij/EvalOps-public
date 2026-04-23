package middleware

import (
	"time"

	"github.com/gin-gonic/gin"
	"github.com/rs/zerolog/log"
)

// Logger emits one structured log line per request. It includes the
// request ID set by RequestID(), so grepping a specific request across
// logs is trivial.
func Logger() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		latency := time.Since(start)

		reqID, _ := c.Get("request_id")
		log.Info().
			Str("method", c.Request.Method).
			Str("path", c.FullPath()).
			Int("status", c.Writer.Status()).
			Dur("latency", latency).
			Str("request_id", toString(reqID)).
			Str("client_ip", c.ClientIP()).
			Msg("request")
	}
}

func toString(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}
