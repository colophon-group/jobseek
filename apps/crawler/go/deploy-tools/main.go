package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

var sourceRevision = "unbuilt"

func run(args []string) error {
	if len(args) > 0 && args[0] == "reconciliation-state" {
		if status := runReconciliationState(args[1:], os.Stdout, os.Stderr); status != 0 {
			return commandStatus(status)
		}
		return nil
	}
	if len(args) > 0 && args[0] == "host-hygiene" {
		if status := runHostHygiene(args[1:], os.Stdout, os.Stderr); status != 0 {
			return commandStatus(status)
		}
		return nil
	}
	if len(args) == 7 && args[0] == "b0-receipt-identity" || len(args) == 10 && args[0] == "b0-receipt-attest" {
		reply, err := b0ReceiptReply(args[1:], args[0] == "b0-receipt-attest")
		if err != nil {
			return err
		}
		fmt.Println(reply)
		return nil
	}
	if len(args) == 2 && args[0] == "b0-epoch-reserve-reply" || len(args) == 3 && args[0] == "b0-epoch-attest-reply" {
		expected := ""
		if len(args) == 3 {
			expected = args[2]
		}
		reply, err := b0EpochReply(args[1], expected)
		if err != nil {
			return err
		}
		if reply != "" {
			fmt.Println(reply)
		}
		return nil
	}
	if len(args) == 3 && args[0] == "b0-path-fsync" {
		return b0PathSync(args[1], args[2])
	}
	if len(args) >= 12 && args[0] == "publication-prune" {
		return prunePublications(args[1:], time.Now())
	}
	if len(args) == 4 && args[0] == "journal-publish" {
		return publishJournal(args[1], args[2], args[3])
	}
	if len(args) == 2 && args[0] == "directory-fsync" {
		return syncDirectory(args[1])
	}
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
	if len(args) == 5 && args[0] == "csv-archive-verify" {
		_, err := readCSVArchive(args[1], args[2], args[3], args[4])
		return err
	}
	if len(args) == 6 && args[0] == "csv-unpack" {
		return unpackCSVArchive(args[1], args[2], args[3], args[4], args[5])
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
	if filepath.Base(os.Args[0]) == "jobseek-reconciliation-state" && !(len(os.Args) == 2 && os.Args[1] == "--build-info") {
		os.Exit(runReconciliationState(os.Args[1:], os.Stdout, os.Stderr))
	}
	if err := run(os.Args[1:]); err != nil {
		var status commandStatus
		if errors.As(err, &status) {
			os.Exit(int(status))
		}
		fmt.Fprintln(os.Stderr, "crawler deployment tools rejected:", err)
		os.Exit(1)
	}
}
