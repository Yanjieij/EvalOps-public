// Package middleware provides Gin middlewares shared across all routes.
package middleware

import (
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

const (
	// HeaderRequestID is the canonical EvalOps request id header. We also
	// accept the upstream-style X-Request-ID for interop.
	HeaderRequestID = "X-Request-ID"
)

// RequestID assigns (or propagates) a request ID and pushes it into the
// Gin context under the key "request_id" for downstream handlers + logs.
func RequestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader(HeaderRequestID)
		if id == "" {
			id = uuid.NewString()
		}
		c.Set("request_id", id)
		c.Writer.Header().Set(HeaderRequestID, id)
		c.Next()
	}
}
