package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

const (
	darkReadyPath    = "/run/jobseek/lightpanda-b0-supervisor-dark.ready"
	darkReadyContent = "jobseek-lightpanda-b0-go-dark-v1\n"
)

func main() {
	if len(os.Args) == 2 && os.Args[1] == "executor-health" {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if err := checkExecutorHealth(ctx); err != nil {
			_, _ = fmt.Fprintln(os.Stderr, "Lightpanda B0 executor is not ready:", err)
			os.Exit(1)
		}
		return
	}
	if len(os.Args) >= 2 && os.Args[1] == "producer" {
		configured, err := producerConfigFromEnvironment()
		if err == nil && len(os.Args) == 3 && os.Args[2] == "--healthcheck" {
			err = checkProducerReady(configured.Socket, uint32(os.Geteuid()), configured.ClientUID)
		} else if err == nil && len(os.Args) == 3 && os.Args[2] == "--check-activation-sentinel-clearable" {
			err = checkProducerActivationSentinelClearable(configured)
		} else if err == nil && len(os.Args) == 3 && os.Args[2] == "--check-activation-sentinel-absent" {
			err = checkProducerActivationSentinelAbsent(configured)
		} else if err == nil && len(os.Args) == 3 && os.Args[2] == "--clear-activation-sentinel" {
			err = clearProducerActivationSentinel(
				configured,
				requiredEnv("LIGHTPANDA_B0_ROLLBACK_PLAN_DIGEST"),
				requiredEnv("LIGHTPANDA_B0_SOURCE_RECEIPT_SHA256"),
			)
		} else if err == nil && len(os.Args) == 2 {
			ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
			defer cancel()
			err = runProducer(ctx, configured)
		} else if err == nil {
			err = errors.New("usage: lightpanda-b0-supervisor producer [--healthcheck|--check-activation-sentinel-clearable|--check-activation-sentinel-absent|--clear-activation-sentinel]")
		}
		if err != nil {
			_, _ = fmt.Fprintln(os.Stderr, "Lightpanda B0 producer failed closed:", err)
			os.Exit(1)
		}
		return
	}
	if len(os.Args) == 2 && os.Args[1] == "--healthcheck" {
		configured, err := configFromEnvironment()
		if err == nil {
			if configured.Mode == modeDark {
				err = checkDarkReady(darkReadyPath)
			} else {
				err = checkEnabledReady(configured.MetricsAddress)
			}
		}
		if err != nil {
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
		_, _ = fmt.Fprintln(os.Stderr, "usage: lightpanda-b0-supervisor [--healthcheck|--validate-dark]|producer [--healthcheck|--check-activation-sentinel-clearable|--check-activation-sentinel-absent|--clear-activation-sentinel]")
		os.Exit(2)
	}
	if err := run(); err != nil {
		_, _ = fmt.Fprintln(os.Stderr, "Lightpanda B0 supervisor failed closed:", err)
		os.Exit(1)
	}
}

func checkEnabledReady(address string) error {
	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Get("http://" + address + "/healthz") //nolint:noctx
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNoContent {
		return fmt.Errorf("enabled readiness returned HTTP %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 1))
	if err != nil || len(body) != 0 {
		return errors.New("enabled readiness returned a body")
	}
	return nil
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
