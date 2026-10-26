package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"time"
)

type inventoryHTTPTransport struct {
	Base        http.RoundTripper
	ReadTimeout time.Duration
}

func (t inventoryHTTPTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	request = request.Clone(request.Context())
	if request.Header.Get("User-Agent") == "" {
		request.Header.Set("User-Agent", "jobseek-ats-inventory/1")
	}
	response, e := t.Base.RoundTrip(request)
	if e == nil {
		response.Body = &inventoryIdleBody{ReadCloser: response.Body, Timeout: t.ReadTimeout}
	}
	return response, e
}

type inventoryIdleBody struct {
	io.ReadCloser
	Timeout time.Duration
}

func (b *inventoryIdleBody) Read(p []byte) (int, error) {
	timer := time.AfterFunc(b.Timeout, func() { b.ReadCloser.Close() })
	n, e := b.ReadCloser.Read(p)
	timer.Stop()
	return n, e
}
func newInventoryHTTPClient() *http.Client {
	transport := &http.Transport{Proxy: http.ProxyFromEnvironment, DialContext: (&net.Dialer{Timeout: 60 * time.Second, KeepAlive: 30 * time.Second}).DialContext, TLSHandshakeTimeout: 60 * time.Second, ResponseHeaderTimeout: 180 * time.Second, MaxConnsPerHost: 4, MaxIdleConnsPerHost: 2, IdleConnTimeout: 90 * time.Second}
	return &http.Client{Transport: inventoryHTTPTransport{transport, 180 * time.Second}}
}
func inventoryEnvDefault(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok {
		return v
	}
	return fallback
}

type inventoryCLIOptions struct {
	CacheDir, ManifestURL, SupportIssues, CandidateIssues, CompaniesFile, BoardsFile, LedgerPath, GitHubRepo, GitHubTokenEnv, GitHubTokenFile string
	CandidateLimit, Retention, MaxCacheMiB, ImpactMaxCacheMiB, ImpactMaxArtifactMiB, ImpactFreeReserveMiB                                     int
	Impact                                                                                                                                    bool
	GitHubRateReserve                                                                                                                         int64
	ClaimTTLHours                                                                                                                             float64
	Policy                                                                                                                                    inventoryQueuePolicy
}

func parseInventoryCLI(args []string) (inventoryCLIOptions, error) {
	opts := inventoryCLIOptions{Policy: defaultInventoryQueuePolicy()}
	f := flag.NewFlagSet("go-ats-inventory", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	f.StringVar(&opts.CacheDir, "cache-dir", inventoryEnvDefault("ATS_INVENTORY_CACHE_DIR", ".cache/ats-inventory"), "Persistent checksum-addressed cache directory")
	f.StringVar(&opts.ManifestURL, "manifest-url", defaultManifestURL, "Published v2 manifest URL")
	f.StringVar(&opts.SupportIssues, "support-issues", "off", "off, plan, or create")
	f.StringVar(&opts.CandidateIssues, "candidate-issues", "off", "off, plan, report, dry-run, or refill")
	f.IntVar(&opts.CandidateLimit, "candidate-limit", 100, "Maximum impact-ranked rows in plan mode")
	f.IntVar(&opts.Policy.LowWater, "queue-low-water", 450, "Available request low-water count")
	f.IntVar(&opts.Policy.Target, "queue-target", 500, "Available request target")
	f.IntVar(&opts.Policy.HardCap, "queue-hard-cap", 600, "Maximum total open requests")
	f.IntVar(&opts.Policy.PerTickCap, "queue-per-tick-cap", 25, "Maximum creates per invocation")
	f.IntVar(&opts.Policy.DailyCap, "queue-daily-cap", 50, "Maximum creates per UTC day")
	f.IntVar(&opts.Policy.RolloutCap, "queue-rollout-cap", 1, "Canary stage cap: 1, 5, or 25")
	f.Float64Var(&opts.ClaimTTLHours, "queue-claim-ttl-hours", 4, "Claim expiry in hours")
	f.Float64Var(&opts.Policy.JitterMinSeconds, "queue-jitter-min-seconds", 1, "Minimum sequential create delay")
	f.Float64Var(&opts.Policy.JitterMaxSeconds, "queue-jitter-max-seconds", 3, "Maximum sequential create delay")
	f.Int64Var(&opts.GitHubRateReserve, "github-rate-reserve", 100, "GitHub primary-rate remaining reserve")
	f.StringVar(&opts.CompaniesFile, "companies-file", "", "Company registry override")
	f.StringVar(&opts.BoardsFile, "boards-file", "", "Board registry override")
	f.StringVar(&opts.LedgerPath, "candidate-ledger", "", "SQLite ledger override")
	f.StringVar(&opts.GitHubRepo, "github-repo", inventoryEnvDefault("ATS_INVENTORY_GITHUB_REPO", "colophon-group/jobseek"), "GitHub repository")
	f.StringVar(&opts.GitHubTokenEnv, "github-token-env", "GH_TOKEN", "Injected token environment variable")
	f.StringVar(&opts.GitHubTokenFile, "github-token-file", os.Getenv("ATS_INVENTORY_GITHUB_TOKEN_FILE"), "Ephemeral token file")
	f.IntVar(&opts.Retention, "retention", 7, "Recent validated manifest retention")
	f.IntVar(&opts.MaxCacheMiB, "max-cache-mib", 256, "Inventory cache MiB limit")
	f.BoolVar(&opts.Impact, "impact", false, "Refresh compact per-company impact")
	f.IntVar(&opts.ImpactMaxCacheMiB, "impact-max-cache-mib", 768, "Impact cache MiB limit")
	f.IntVar(&opts.ImpactMaxArtifactMiB, "impact-max-artifact-mib", 512, "Parquet artifact MiB limit")
	f.IntVar(&opts.ImpactFreeReserveMiB, "impact-free-reserve-mib", 512, "Free disk reserve MiB")
	if e := f.Parse(args); e != nil {
		return opts, e
	}
	if f.NArg() != 0 {
		return opts, fmt.Errorf("unexpected ATS inventory arguments")
	}
	if opts.CandidateLimit < 1 {
		return opts, fmt.Errorf("candidate-limit must be at least 1")
	}
	if !slices.Contains([]string{"off", "plan", "create"}, opts.SupportIssues) || !slices.Contains([]string{"off", "plan", "report", "dry-run", "refill"}, opts.CandidateIssues) || !slices.Contains([]int{1, 5, 25}, opts.Policy.RolloutCap) {
		return opts, fmt.Errorf("unsupported ATS inventory mode or rollout cap")
	}
	opts.Policy.ClaimTTLSeconds = int(opts.ClaimTTLHours * 3600)
	return opts, nil
}
func resolveInventoryRegistryPaths(companies, boards string) (string, string, error) {
	if companies != "" && boards != "" {
		return companies, boards, nil
	}
	info, e := os.Stat("/app/data")
	if e != nil || !info.IsDir() {
		return "", "", fmt.Errorf("installed ATS inventory requires /app/data or explicit companies-file and boards-file")
	}
	if companies == "" {
		companies = "/app/data/companies.csv"
	}
	if boards == "" {
		boards = "/app/data/boards.csv"
	}
	return companies, boards, nil
}
func inventoryRateLimitReport(mode string, client *inventoryGitHubClient, limited *githubRateLimitError) map[string]any {
	return map[string]any{"mode": mode, "status": "rate_limited_preflight", "actions": []any{}, "rate_remaining": client.RateRemaining, "rate_reset": limited.ResetAt, "retry_after": limited.RetryAfter, "retry_at": limited.retryAt(time.Now().Unix())}
}
func runATSInventory(ctx context.Context, args []string, client inventoryHTTPClient, output io.Writer) error {
	opts, e := parseInventoryCLI(args)
	if e != nil {
		return e
	}
	unlock, e := acquireInventoryLock(filepath.Join(opts.CacheDir, "runner.lock"))
	if e != nil {
		return e
	}
	defer unlock()
	sourceOpts := defaultInventorySourceOptions()
	sourceOpts.Retention = opts.Retention
	sourceOpts.MaxCacheBytes = int64(opts.MaxCacheMiB) * 1024 * 1024
	source, e := newInventorySource(opts.CacheDir, client, opts.ManifestURL, sourceOpts)
	if e != nil {
		return e
	}
	snapshot, e := source.Sync(ctx)
	if e != nil {
		return e
	}
	report := snapshot.report()
	report["impact"] = map[string]any{"enabled": opts.Impact}
	impactOpts := defaultImpactCacheOptions()
	impactOpts.MaxCacheBytes = int64(opts.ImpactMaxCacheMiB) * 1024 * 1024
	impactOpts.MaxArtifactBytes = int64(opts.ImpactMaxArtifactMiB) * 1024 * 1024
	impactOpts.MinFreeBytes = int64(opts.ImpactFreeReserveMiB) * 1024 * 1024
	cache, e := newImpactCache(filepath.Join(opts.CacheDir, "impact"), client, impactOpts)
	if e != nil {
		return e
	}
	var impact *impactSnapshot
	if opts.Impact {
		got, e := cache.Sync(ctx, snapshot)
		if e != nil {
			return e
		}
		impact = &got
		r := got.report()
		r["enabled"] = true
		report["impact"] = r
	}
	report["support_issues"] = map[string]any{"mode": opts.SupportIssues, "actions": []any{}}
	var github *inventoryGitHubClient
	if opts.SupportIssues != "off" || opts.CandidateIssues != "off" {
		token, e := readInventoryGitHubToken(opts.GitHubTokenEnv, opts.GitHubTokenFile)
		if e != nil {
			return e
		}
		github, e = newInventoryGitHubClient(client, opts.GitHubRepo, token, opts.GitHubRateReserve)
		if e != nil {
			return e
		}
	}
	automatic := "off"
	if opts.CandidateIssues == "refill" {
		automatic = "create"
	} else if opts.CandidateIssues == "dry-run" {
		automatic = "plan"
	}
	var preflightRate *githubRateLimitError
	if opts.SupportIssues != "off" || automatic != "off" {
		create := opts.SupportIssues == "create" || automatic == "create"
		mode := "plan"
		if create {
			mode = "create"
		}
		actions, e := reconcileSupportIssues(ctx, snapshot, github, create)
		if e != nil {
			if !errors.As(e, &preflightRate) {
				return e
			}
			r := inventoryRateLimitReport(mode, github, preflightRate)
			r["automatic"] = automatic != "off"
			report["support_issues"] = r
		} else {
			report["support_issues"] = map[string]any{"mode": mode, "automatic": automatic != "off", "actions": actions, "rate_remaining": github.RateRemaining, "rate_reset": github.RateReset}
		}
	}
	report["candidate_issues"] = map[string]any{"mode": opts.CandidateIssues, "actions": []any{}}
	if preflightRate != nil && opts.CandidateIssues != "off" {
		report["candidate_issues"] = inventoryRateLimitReport(opts.CandidateIssues, github, preflightRate)
	} else if opts.CandidateIssues != "off" {
		if opts.CandidateIssues != "plan" {
			if e = opts.Policy.validate(); e != nil {
				return e
			}
		}

		if opts.CandidateIssues == "plan" {
			if !snapshot.Coverage.CandidateGenerationAllowed {
				return fmt.Errorf("candidate generation quarantined below coverage gate")
			}
			if impact == nil {
				got, e := cache.loadCurrent(false, 0)
				if e != nil {
					return e
				}
				impact = &got
			}
			if impact.ManifestSHA256 != snapshot.ManifestSHA256 || impact.InventorySHA256 != snapshot.InventorySHA256 {
				return fmt.Errorf("cached impact does not match inventory; rerun with --impact")
			}
		}
		ledgerPath := opts.LedgerPath
		if ledgerPath == "" {
			ledgerPath = filepath.Join(opts.CacheDir, "candidates", "ledger.sqlite")
		}
		ledger, e := openCandidateLedger(ledgerPath)
		if e != nil {
			return e
		}
		defer ledger.Close()
		var items []githubWorkItem
		claims := []githubClaim{}
		if opts.CandidateIssues != "plan" {
			items, e = github.listCandidateWorkItems(ctx)
			if e == nil {
				claims, e = github.listRecentClaims(ctx, time.Now().Add(-time.Duration(opts.Policy.ClaimTTLSeconds)*time.Second))
			}
			if e != nil {
				var limited *githubRateLimitError
				if !errors.As(e, &limited) {
					return e
				}
				report["candidate_issues"] = inventoryRateLimitReport(opts.CandidateIssues, github, limited)
				return writeInventoryCompleteReport(output, report)
			}
		}
		companiesFile, boardsFile, e := resolveInventoryRegistryPaths(opts.CompaniesFile, opts.BoardsFile)
		if e != nil {
			return e
		}
		local, e := loadLocalRegistryIndex(companiesFile, boardsFile)
		if e != nil {
			return e
		}
		coordinator, e := bootstrapCandidateCoordinator(ctx, github, local, ledger, items)
		if e != nil {
			return e
		}
		loadImpact := func() error {
			if impact == nil {
				current, e := cache.loadCurrent(false, 0)
				if e != nil {
					return e
				}
				impact = &current
			}
			if impact.ManifestSHA256 != snapshot.ManifestSHA256 || impact.InventorySHA256 != snapshot.InventorySHA256 {
				return fmt.Errorf("cached impact does not match inventory; rerun with --impact")
			}
			return nil
		}
		if opts.CandidateIssues == "plan" {
			if !snapshot.Coverage.CandidateGenerationAllowed {
				return fmt.Errorf("candidate generation quarantined below coverage gate")
			}
			if e = loadImpact(); e != nil {
				return e
			}
			ranked := impact.ranked()
			actions := []candidateIssueAction{}
			registry, e := loadCompatibility()
			if e != nil {
				return e
			}
			eligible := 0
			for _, company := range ranked[:min(opts.CandidateLimit, len(ranked))] {
				candidate, e := candidateFromImpact(company, registry)
				if e != nil {
					return e
				}
				action, e := coordinator.create(ctx, candidate, true)
				if e != nil {
					return e
				}
				actions = append(actions, action)
				if action.Action == "would_create" {
					eligible++
				}
			}
			report["candidate_issues"] = map[string]any{"mode": "plan", "considered": len(actions), "eligible": eligible, "actions": actions, "ledger_reconciliation": coordinator.Reconciliation, "rate_remaining": github.RateRemaining, "rate_reset": github.RateReset}
		} else {
			companies := []companyImpact{}
			admissionBlock := ""
			if opts.CandidateIssues == "dry-run" || opts.CandidateIssues == "refill" {
				if !snapshot.Coverage.CandidateGenerationAllowed {
					admissionBlock = "coverage_quarantined"
				} else {
					if e = loadImpact(); e != nil {
						return e
					}
					companies = impact.ranked()
				}
			}
			refiller := inventoryQueueRefiller{Coordinator: coordinator, Ledger: ledger, Items: items, Claims: claims, Policy: opts.Policy, RefreshOpenCount: github.countOpenCompanyRequests}
			queue, e := refiller.run(ctx, companies, opts.CandidateIssues, admissionBlock)
			if e != nil {
				return e
			}
			queue["ledger_reconciliation"] = coordinator.Reconciliation
			report["candidate_issues"] = queue
		}
	}
	return writeInventoryCompleteReport(output, report)
}
func writeInventoryCompleteReport(output io.Writer, report map[string]any) error {
	encoder := json.NewEncoder(output)
	encoder.SetEscapeHTML(false)
	if e := encoder.Encode(map[string]any{"event": "ats_inventory.complete", "report": report}); e != nil {
		return e
	}
	encoder.SetIndent("", "  ")
	return encoder.Encode(report)
}

func main() {
	var e error
	if len(os.Args) > 1 && strings.HasPrefix(os.Args[1], "host-") {
		// Preserve the original short-lived helpers' default signal termination,
		// including a bounded logger blocked while waiting for pipe input.
		e = runATSHost(context.Background(), os.Args[1:], os.Stdin, os.Stdout)
	} else {
		ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		e = runATSInventory(ctx, os.Args[1:], newInventoryHTTPClient(), os.Stdout)
	}
	if errors.Is(e, flag.ErrHelp) {
		fmt.Println("go-ats-inventory: validate data-only inventory; --impact; --candidate-issues off|plan|report|dry-run|refill; --support-issues off|plan|create; --cache-dir PATH. Registry defaults to /app/data; use --companies-file and --boards-file for checkout tools.")
		return
	}
	if e != nil {
		fmt.Fprintln(os.Stderr, "ATS inventory failed:", e)
		os.Exit(1)
	}
}
