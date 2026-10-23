package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

type commandStatus int

func (s commandStatus) Error() string { return fmt.Sprintf("command exited with status %d", s) }

type hygieneFinding struct {
	kind, name, detail, cleanup string
	ageSeconds                  float64
}

type hygieneCommand func(context.Context, string, ...string) ([]byte, error)

type hygieneOutput struct{ bytes.Buffer }

func (output *hygieneOutput) Write(body []byte) (int, error) {
	if output.Len()+len(body) > 32<<20 {
		return 0, fmt.Errorf("host inventory exceeds audit limit")
	}
	return output.Buffer.Write(body)
}

func (output *hygieneOutput) ReadFrom(reader io.Reader) (int64, error) {
	return io.Copy(struct{ io.Writer }{output}, reader)
}

// Docker inspect includes environment variables and credentials. Keep its output
// private and emit only the same reviewed inventory fields as the original audit.
func runHygieneCommand(ctx context.Context, name string, args ...string) ([]byte, error) {
	command := exec.CommandContext(ctx, name, args...)
	command.WaitDelay = 5 * time.Second
	var output hygieneOutput
	command.Stdout = &output
	if err := command.Run(); err != nil {
		return nil, fmt.Errorf("%s inspection failed", name)
	}
	return output.Bytes(), nil
}

func inspectHostHygiene(ctx context.Context, command hygieneCommand, now time.Time, uptime, maxAge float64) ([]hygieneFinding, error) {
	body, err := command(ctx, "docker", "ps", "-q")
	if err != nil {
		return nil, err
	}
	findings := []hygieneFinding{}
	ids := strings.Fields(string(body))
	if len(ids) > 0 {
		body, err = command(ctx, "docker", append([]string{"inspect"}, ids...)...)
		if err != nil {
			return nil, err
		}
		var containers []struct {
			ID     string `json:"Id"`
			Name   string
			Config struct {
				Image  string
				Labels map[string]string
			}
			State struct{ StartedAt string }
		}
		if json.Unmarshal(body, &containers) != nil || len(containers) != len(ids) {
			return nil, fmt.Errorf("docker inspect returned incomplete inventory")
		}
		for _, container := range containers {
			if container.Config.Labels["com.docker.compose.project"] != "" {
				continue
			}
			started, err := time.Parse(time.RFC3339Nano, container.State.StartedAt)
			if err != nil {
				return nil, fmt.Errorf("invalid Docker StartedAt timestamp")
			}
			age := math.Max(0, now.Sub(started).Seconds())
			if age < maxAge {
				continue
			}
			id := container.ID
			if id == "" {
				id = "unknown"
			}
			shortID := id[:min(12, len(id))]
			name := strings.TrimLeft(container.Name, "/")
			if name == "" {
				name = shortID
			}
			image := container.Config.Image
			if image == "" {
				image = "unknown"
			}
			findings = append(findings, hygieneFinding{"unmanaged container", name,
				fmt.Sprintf("id=%s image=%s", shortID, image),
				"do not remove by name; review immutable identity with jobseek-host-hygiene candidate-id=" + shellQuote(id), age})
		}
	}
	body, err = command(ctx, "systemctl", "list-units", "--type=service", "--state=active", "--no-legend", "--plain")
	if err != nil {
		return nil, err
	}
	for _, line := range strings.Split(string(body), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 4 || fields[2] != "active" || fields[3] != "exited" {
			continue
		}
		unit := fields[0]
		body, err = command(ctx, "systemctl", "show", unit, "--property=FragmentPath", "--property=ActiveEnterTimestampMonotonic", "--no-pager")
		if err != nil {
			return nil, err
		}
		properties := map[string]string{}
		for _, property := range strings.Split(string(body), "\n") {
			if key, value, ok := strings.Cut(property, "="); ok {
				properties[key] = value
			}
		}
		fragment := properties["FragmentPath"]
		if !strings.HasPrefix(fragment, "/run/systemd/transient/") {
			continue
		}
		value := properties["ActiveEnterTimestampMonotonic"]
		if value == "" {
			value = "0"
		}
		active, err := strconv.ParseInt(value, 10, 64)
		if err != nil {
			return nil, fmt.Errorf("invalid monotonic timestamp for %s", unit)
		}
		age := math.Max(0, uptime-float64(active)/1e6)
		if age >= maxAge {
			findings = append(findings, hygieneFinding{"active-exited transient service", unit,
				"fragment=" + fragment, "systemctl stop " + shellQuote(unit), age})
		}
	}
	return findings, nil
}

// Match shlex.quote for the advisory text; no cleanup command is executed.
func shellQuote(value string) string {
	if value != "" && strings.IndexFunc(value, func(c rune) bool {
		return !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("_@%+=:,./-", c))
	}) == -1 {
		return value
	}
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}

func hygieneAge(seconds float64) string {
	hours := int64(seconds / 3600)
	if hours >= 24 {
		return fmt.Sprintf("%dd%02dh", hours/24, hours%24)
	}
	return fmt.Sprintf("%dh", hours)
}

func runHostHygiene(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("host-hygiene", flag.ContinueOnError)
	flags.SetOutput(stderr)
	maxAge := flags.Float64("max-age-hours", 24, "report resources older than this threshold")
	nowValue := flags.String("now", "", "fixed RFC3339 observation time for replay")
	uptimePath := flags.String("proc-uptime", "/proc/uptime", "host uptime source")
	if flags.Parse(args) != nil || flags.NArg() != 0 || *maxAge <= 0 || math.IsNaN(*maxAge) || math.IsInf(*maxAge*3600, 0) {
		fmt.Fprintln(stderr, "host hygiene requires valid options and a positive finite age")
		return 2
	}
	now := time.Now().UTC()
	if *nowValue != "" {
		var err error
		now, err = time.Parse(time.RFC3339Nano, *nowValue)
		if err != nil {
			fmt.Fprintln(stderr, "host hygiene inspection failed: invalid observation timestamp")
			return 2
		}
	}
	body, err := os.ReadFile(*uptimePath)
	fields := strings.Fields(string(body))
	if err != nil || len(fields) == 0 {
		fmt.Fprintln(stderr, "host hygiene inspection failed: host uptime unavailable")
		return 2
	}
	uptime, err := strconv.ParseFloat(fields[0], 64)
	if err != nil || uptime < 0 || math.IsNaN(uptime) || math.IsInf(uptime, 0) {
		fmt.Fprintln(stderr, "host hygiene inspection failed: invalid host uptime")
		return 2
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	findings, err := inspectHostHygiene(ctx, runHygieneCommand, now, uptime, *maxAge*3600)
	if err != nil {
		fmt.Fprintln(stderr, "host hygiene inspection failed:", err)
		return 2
	}
	if len(findings) == 0 {
		fmt.Fprintf(stdout, "host hygiene clean: no unmanaged containers or active-exited transient services older than %gh\n", *maxAge)
		return 0
	}
	fmt.Fprintf(stderr, "host hygiene found %d stale resource(s) older than %gh:\n", len(findings), *maxAge)
	for _, finding := range findings {
		fmt.Fprintf(stderr, "- %s: %s age=%s %s\n  cleanup after verification: %s\n", finding.kind, finding.name, hygieneAge(finding.ageSeconds), finding.detail, finding.cleanup)
	}
	return 1
}
