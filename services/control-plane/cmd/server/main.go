// Package main is the entry point for the EvalOps control-plane.
//
// Week 1 scope: boot a Gin server with health + a run-submission stub,
// expose Prometheus metrics, and register middleware for request ID +
// structured logging. Real scheduler, store, and gRPC client to the
// eval-engine come in Week 2.
package main

import (
	"context"
	"errors"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"

	"github.com/yanjieij/evalops/control-plane/internal/config"
	"github.com/yanjieij/evalops/control-plane/internal/router"
)

func main() {
	zerolog.TimeFieldFormat = zerolog.TimeFormatUnix
	log.Logger = log.Output(zerolog.ConsoleWriter{Out: os.Stderr, TimeFormat: time.RFC3339})

	cfg := config.Load()
	log.Info().
		Str("addr", cfg.HTTPAddr).
		Str("eval_engine_grpc", cfg.EvalEngineGRPCAddr).
		Msg("control-plane starting")

	srv := &http.Server{
		Addr:              cfg.HTTPAddr,
		Handler:           router.New(cfg),
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatal().Err(err).Msg("http server exited")
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	log.Info().Msg("shutdown signal received")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		log.Error().Err(err).Msg("graceful shutdown failed")
	}
	log.Info().Msg("control-plane stopped")
}
