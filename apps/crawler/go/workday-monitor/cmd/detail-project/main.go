// detail-project projects one captured Workday detail response without making
// a publisher request. It is used for same-byte Python/Go parity checks.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

func run() error {
	var input struct {
		Data          json.RawMessage `json:"data"`
		Tenant        string          `json:"tenant"`
		TenantAliases []string        `json:"tenant_aliases"`
	}
	decoder := json.NewDecoder(io.LimitReader(os.Stdin, 2<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		return fmt.Errorf("decode Workday detail input: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return fmt.Errorf("Workday detail input has trailing data: %v", err)
	}
	if len(input.Data) == 0 {
		return fmt.Errorf("Workday detail input is missing data")
	}
	content, err := workday.ProjectDetail(input.Data, input.Tenant, input.TenantAliases)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(content)
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
