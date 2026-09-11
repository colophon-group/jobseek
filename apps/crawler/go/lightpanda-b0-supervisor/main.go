package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
)

const (
	darkReadyPath    = "/run/jobseek/lightpanda-b0-supervisor-dark.ready"
	darkReadyContent = "jobseek-lightpanda-b0-go-dark-v1\n"
)

func main() {
	if len(os.Args) == 2 && os.Args[1] == "--healthcheck" {
		if err := checkDarkReady(darkReadyPath); err != nil {
			_, _ = fmt.Fprintln(os.Stderr, "Lightpanda B0 supervisor is not ready:", err)
			os.Exit(1)
		}
		return
	}
	if len(os.Args) == 2 && os.Args[1] == "--validate-dark" {
		configured, err := configFromEnvironment()
		if err != nil || configured.Mode != modeDark || configured.RedisOptions != nil {
			_, _ = fmt.Fprintln(os.Stderr, "Lightpanda B0 supervisor dark validation failed")
			os.Exit(1)
		}
		return
	}
	if len(os.Args) != 1 {
		_, _ = fmt.Fprintln(os.Stderr, "usage: lightpanda-b0-supervisor [--healthcheck|--validate-dark]")
		os.Exit(2)
	}
	if err := run(); err != nil {
		_, _ = fmt.Fprintln(os.Stderr, "Lightpanda B0 supervisor failed closed:", err)
		os.Exit(1)
	}
}

func run() error {
	config, err := configFromEnvironment()
	if err != nil {
		return err
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if config.Mode == modeDark {
		if err := publishDarkReady(darkReadyPath); err != nil {
			return err
		}
		defer os.Remove(darkReadyPath)
		<-ctx.Done()
		return nil
	}
	supervisor, redisClient, err := newSupervisor(config)
	if err != nil {
		return err
	}
	defer redisClient.Close()
	metricsErr := make(chan error, 1)
	go func() { metricsErr <- supervisor.metrics.serve(ctx, config.MetricsAddress) }()
	supervisorErr := make(chan error, 1)
	go func() { supervisorErr <- supervisor.run(ctx) }()
	select {
	case err := <-metricsErr:
		cancel()
		supervisorRunErr := <-supervisorErr
		if err != nil {
			return fmt.Errorf("B0 metrics server: %w", err)
		}
		return supervisorRunErr
	case err := <-supervisorErr:
		cancel()
		metricsRunErr := <-metricsErr
		if err == nil && metricsRunErr != nil {
			return fmt.Errorf("B0 metrics server: %w", metricsRunErr)
		}
		return err
	}
}

func publishDarkReady(path string) error {
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return fmt.Errorf("publish dark readiness: %w", err)
	}
	writeErr := error(nil)
	if written, err := io.WriteString(file, darkReadyContent); err != nil || written != len(darkReadyContent) {
		writeErr = errors.New("publish dark readiness: short write")
	}
	if writeErr == nil {
		writeErr = file.Sync()
	}
	if closeErr := file.Close(); writeErr == nil {
		writeErr = closeErr
	}
	if writeErr != nil {
		_ = os.Remove(path)
		return fmt.Errorf("publish dark readiness: %w", writeErr)
	}
	return checkDarkReady(path)
}

func checkDarkReady(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm() != 0o600 || info.Size() != int64(len(darkReadyContent)) {
		return errors.New("dark readiness marker metadata mismatch")
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if string(content) != darkReadyContent {
		return errors.New("dark readiness marker content mismatch")
	}
	return nil
}
