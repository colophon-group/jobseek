package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/lightpanda-probe/internal/harness"
	"google.golang.org/protobuf/encoding/protojson"
)

func main() {
	var planPath, outputPath, wsURL, allowedOrigin string
	var maxRequests, maxResponseBytes uint64
	var identityDiagnostic bool
	flag.StringVar(&planPath, "plan", "", "path to a committed synthetic BrowserPlan JSON fixture")
	flag.StringVar(&outputPath, "output", "", "path for the deterministic result JSON")
	flag.StringVar(&wsURL, "ws", harness.LightpandaWS, "fixed internal Lightpanda browser WebSocket")
	flag.StringVar(&allowedOrigin, "allowed-origin", harness.FixtureOrigin, "fixed internal synthetic fixture origin")
	flag.Uint64Var(&maxRequests, "max-requests", 32, "maximum robots plus browser requests")
	flag.Uint64Var(&maxResponseBytes, "max-response-bytes", 1048576, "maximum aggregate response bytes")
	flag.BoolVar(&identityDiagnostic, "identity-diagnostic", false, "emit only sanitized same/different blocked-request identity relations")
	flag.Parse()

	if planPath == "" || outputPath == "" {
		fatal("-plan and -output are required")
	}
	planData, err := os.ReadFile(planPath)
	if err != nil {
		fatal("read plan")
	}
	plan := &runtimev1.BrowserPlan{}
	if err := (protojson.UnmarshalOptions{DiscardUnknown: false}).Unmarshal(planData, plan); err != nil {
		fatal("parse plan")
	}

	runner := harness.Runner{
		WSURL:         wsURL,
		AllowedOrigin: allowedOrigin,
		Limits: harness.Limits{
			MaxRequests:      maxRequests,
			MaxResponseBytes: maxResponseBytes,
		},
	}
	if identityDiagnostic {
		runner.IdentityDiagnostic = os.Stderr
	}
	result, ledger, requestCount, responseBytes, cleanup := runner.Execute(context.Background(), plan)
	resultJSON, err := harness.MarshalResult(result)
	if err != nil {
		fatal("marshal result")
	}
	fixtureID := strings.TrimSuffix(filepath.Base(planPath), filepath.Ext(planPath))
	envelope, err := harness.MarshalEnvelope(harness.Envelope{
		Format:        harness.OutputFormat,
		FixtureID:     fixtureID,
		PlanSHA256:    harness.SHA256(planData),
		Result:        resultJSON,
		Ledger:        ledger,
		RequestCount:  requestCount,
		ResponseBytes: responseBytes,
		Cleanup:       cleanup,
	})
	if err != nil {
		fatal("marshal envelope")
	}
	if err := os.WriteFile(outputPath, envelope, 0o600); err != nil {
		fatal("write output")
	}
}

func fatal(operation string) {
	fmt.Fprintf(os.Stderr, "lightpanda probe: %s failed\n", operation)
	os.Exit(2)
}
