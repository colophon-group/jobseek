package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

var sourceRevision = "unbuilt"

func run(args []string) error {
	if len(args) == 4 && args[0] == "release-activate" {
		return activateRelease(args[1], args[2], args[3])
	}
	if len(args) == 2 && args[0] == "release-fsync" {
		return syncRelease(args[1])
	}
	if len(args) == 3 && args[0] == "release-bridge" {
		state, err := verifyReleaseBridge(args[1], args[2])
		if err != nil {
			return err
		}
		fmt.Println(state)
		return nil
	}
	if len(args) == 5 && args[0] == "runtime-attestation" {
		body, err := bridgeRead(filepath.Dir(args[1]), filepath.Base(args[1]))
		if err != nil {
			return err
		}
		_, err = runtimeAttestation(body, args[2], args[3], args[4])
		return err
	}
	if len(args) >= 4 && args[0] == "spec-pack" {
		return packSpecs(args[1], args[2], args[3:])
	}
	if len(args) >= 5 && args[0] == "spec-unpack" {
		return unpackSpecs(args[1], args[2], args[3], args[4:])
	}
	if len(args) == 1 && args[0] == "--build-info" {
		return json.NewEncoder(os.Stdout).Encode(map[string]string{"source_revision": sourceRevision})
	}
	if len(args) == 3 && (args[0] == "csv-manifest" || args[0] == "csv-verify") {
		body, err := csvManifest(args[1])
		if err != nil {
			return err
		}
		if args[0] == "csv-manifest" {
			return writeManifest(args[2], body)
		}
		return verifyManifest(args[2], body)
	}
	if len(args) == 1 && args[0] == "postgresql-preflight" {
		ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
		defer cancel()
		started := time.Now()
		if err := postgresPreflight(ctx, os.Getenv("GRAFANA_PROM_URL"), os.Getenv("GRAFANA_PROM_USERNAME"), os.Getenv("GRAFANA_PROM_PASSWORD")); err != nil {
			return err
		}
		fmt.Printf("PostgreSQL operational preflight passed in %.2fs\n", time.Since(started).Seconds())
		return nil
	}
	return fmt.Errorf("unsupported deployment command or argument count")
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "crawler deployment tools rejected:", err)
		os.Exit(1)
	}
}
