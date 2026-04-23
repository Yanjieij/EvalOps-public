// Package config loads the control-plane's runtime configuration.
//
// Every knob is an environment variable with a documented default.
// We intentionally avoid a config file in Week 1 — if you need to override,
// export the EVALOPS_CP_* variables or wrap the binary with an env file.
package config

import (
	"os"
	"strconv"
	"time"
)

// Config holds the runtime configuration.
type Config struct {
	// HTTP server
	HTTPAddr     string
	ReadTimeout  time.Duration
	WriteTimeout time.Duration

	// Downstream services
	EvalEngineGRPCAddr string // e.g. "localhost:50061"

	// Storage
	PostgresDSN string // "postgres://evalops:evalops@localhost:5432/evalops"
	RedisAddr   string // "localhost:6379"

	// Observability
	JaegerEndpoint string // "" = disabled; "http://localhost:4318/v1/traces"
	ServiceName    string
	LogLevel       string
}

// Load reads configuration from the environment.
func Load() Config {
	return Config{
		HTTPAddr:           env("EVALOPS_CP_HTTP_ADDR", ":8090"),
		ReadTimeout:        durationEnv("EVALOPS_CP_READ_TIMEOUT", 15*time.Second),
		WriteTimeout:       durationEnv("EVALOPS_CP_WRITE_TIMEOUT", 30*time.Second),
		EvalEngineGRPCAddr: env("EVALOPS_CP_EVAL_ENGINE_GRPC", "localhost:50061"),
		PostgresDSN:        env("EVALOPS_CP_POSTGRES_DSN", ""),
		RedisAddr:          env("EVALOPS_CP_REDIS_ADDR", ""),
		JaegerEndpoint:     env("EVALOPS_CP_JAEGER_ENDPOINT", ""),
		ServiceName:        env("EVALOPS_CP_SERVICE_NAME", "evalops-control-plane"),
		LogLevel:           env("EVALOPS_CP_LOG_LEVEL", "info"),
	}
}

func env(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

func durationEnv(key string, def time.Duration) time.Duration {
	v, ok := os.LookupEnv(key)
	if !ok || v == "" {
		return def
	}
	if d, err := time.ParseDuration(v); err == nil {
		return d
	}
	if n, err := strconv.Atoi(v); err == nil {
		return time.Duration(n) * time.Second
	}
	return def
}
