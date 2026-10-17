package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"
)

const hostLogLimit = 16 * 1024 * 1024

// Host helpers share the installed immutable inventory binary. They never run
// the inventory refresh or GitHub issue coordinator.
func runATSHost(ctx context.Context, args []string, input io.Reader, output io.Writer) error {
	if len(args) == 0 {
		return errors.New("host helper required")
	}
	switch args[0] {
	case "host-create-lock":
		if len(args) != 2 || args[1] != "/run/lock/jobseek-crawler-mutation.lock" {
			return errors.New("invalid shared lock path")
		}
		return createHostLock(args[1])
	case "host-bounded-tee":
		f := flag.NewFlagSet(args[0], flag.ContinueOnError)
		f.SetOutput(io.Discard)
		path := f.String("output", "", "")
		limit := f.Int("max-bytes", hostLogLimit, "")
		if e := f.Parse(args[1:]); e != nil {
			return e
		}
		if *path == "" || *limit < 1024 || f.NArg() != 0 {
			return errors.New("invalid bounded logger arguments")
		}
		return streamHostTail(input, output, *path, *limit)
	case "host-status":
		return runHostStatus(args[1:])
	case "host-token":
		return runHostToken(ctx, args[1:])
	case "host-registry-snapshot":
		return runHostRegistry(args[1:], output)
	case "host-network-probe":
		return runHostNetwork(ctx, args[1:], output)
	case "host-acceptance":
		if len(args) != 5 {
			return errors.New("invalid acceptance arguments")
		}
		var started int64
		if _, e := fmt.Sscan(args[1], &started); e != nil {
			return errors.New("invalid acceptance time")
		}
		return verifyHostAcceptance(args[4], started, args[2], args[3])
	case "host-capabilities":
		if len(args) != 1 {
			return errors.New("unexpected capability arguments")
		}
		_, e := fmt.Fprintln(output, "jobseek.ats-host-tools/v1")
		return e
	}
	return errors.New("unknown host helper")
}

func createHostLock(path string) error {
	fd, e := syscall.Open(path, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW, 0600)
	if e != nil {
		return e
	}
	return syscall.Close(fd)
}

func atomicHostBytes(path string, body []byte, mode os.FileMode, directoryMode os.FileMode) error {
	parent := filepath.Dir(path)
	if e := os.MkdirAll(parent, directoryMode); e != nil {
		return e
	}
	f, e := os.CreateTemp(parent, "."+filepath.Base(path)+".")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	if e = f.Chmod(mode); e == nil {
		_, e = f.Write(body)
	}
	if e == nil {
		e = f.Sync()
	}
	closeErr := f.Close()
	if e != nil {
		return e
	}
	if closeErr != nil {
		return closeErr
	}
	if e = os.Rename(f.Name(), path); e != nil {
		return e
	}
	return fsyncInventoryDirectory(parent)
}
func atomicHostJSON(path string, value any, mode os.FileMode, directoryMode os.FileMode) error {
	var b bytes.Buffer
	e := json.NewEncoder(&b)
	e.SetEscapeHTML(false)
	if err := e.Encode(value); err != nil {
		return err
	}
	return atomicHostBytes(path, b.Bytes(), mode, directoryMode)
}

func streamHostTail(input io.Reader, output io.Writer, path string, limit int) error {
	tail := make([]byte, 0, limit)
	chunk := make([]byte, 64*1024)
	for {
		n, e := input.Read(chunk)
		if n > 0 {
			if _, err := io.Copy(output, bytes.NewReader(chunk[:n])); err != nil {
				return err
			}
			if n >= limit {
				tail = append(tail[:0], chunk[n-limit:n]...)
			} else {
				overflow := len(tail) + n - limit
				if overflow > 0 {
					copy(tail, tail[overflow:])
					tail = tail[:len(tail)-overflow]
				}
				tail = append(tail, chunk[:n]...)
			}
		}
		if e == io.EOF {
			break
		}
		if e != nil {
			return e
		}
	}
	if len(tail) == limit {
		if n := bytes.IndexByte(tail, '\n'); n >= 0 {
			tail = tail[n+1:]
		}
	}
	return atomicHostBytes(path, tail, 0600, 0700)
}

func readHostJSON(path string) map[string]any {
	raw, e := boundedInventoryFile(path, hostLogLimit)
	if e != nil {
		return map[string]any{}
	}
	var out map[string]any
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var extra any
	if d.Decode(&out) != nil || d.Decode(&extra) != io.EOF || out == nil {
		return map[string]any{}
	}
	return out
}
func extractHostReport(path string) (map[string]any, error) {
	raw, e := boundedInventoryFile(path, hostLogLimit)
	if e != nil {
		return nil, errors.New("runner log unavailable or exceeds parser limit")
	}
	var report map[string]any
	for _, line := range bytes.Split(bytes.ToValidUTF8(raw, []byte("�")), []byte("\n")) {
		var record map[string]json.RawMessage
		d := json.NewDecoder(bytes.NewReader(line))
		d.UseNumber()
		var extra any
		if d.Decode(&record) == nil && d.Decode(&extra) == io.EOF {
			var event string
			var candidate map[string]any
			if json.Unmarshal(record["event"], &event) != nil || event != "ats_inventory.complete" {
				continue
			}
			decode := json.NewDecoder(bytes.NewReader(record["report"]))
			decode.UseNumber()
			if decode.Decode(&candidate) == nil && candidate != nil {
				report = candidate
			}
		}
	}
	return report, nil
}
func hostInteger(value any) int64 {
	switch v := value.(type) {
	case json.Number:
		n, _ := v.Int64()
		return n
	case int64:
		return v
	case int:
		return int64(v)
	}
	return 0
}
func recordHostStatus(root, log string, code int, requested, effective string, cap int, started, finished int64) (map[string]any, error) {
	report, e := extractHostReport(log)
	if e != nil {
		return nil, e
	}
	var visible any
	if code == 0 && report != nil {
		visible = report
	}
	degraded := false
	if code == 0 && report != nil {
		if candidates, ok := report["candidate_issues"].(map[string]any); ok {
			degraded = candidates["status"] == "rate_limited" || candidates["status"] == "rate_limited_preflight"
		}
	}
	success := code == 0 && report != nil && !degraded
	previous := readHostJSON(filepath.Join(root, "status/current.json"))
	lastSuccess := hostInteger(previous["last_success_unixtime"])
	lastReport := previous["last_success_report"]
	if object, ok := lastReport.(map[string]any); lastReport == nil || ok && len(object) == 0 {
		lastReport = previous["report"]
	}
	succeeded, degradedNumber := 0, 0
	if success {
		succeeded = 1
		lastSuccess = finished
		lastReport = report
	}
	if degraded {
		degradedNumber = 1
	}
	duration := finished - started
	if duration < 0 {
		duration = 0
	}
	payload := map[string]any{"schema": 1, "last_attempt_unixtime": finished, "last_attempt_started_unixtime": started, "last_attempt_duration_seconds": duration, "last_attempt_success": succeeded, "last_attempt_degraded": degradedNumber, "last_success_unixtime": lastSuccess, "return_code": code, "requested_mode": requested, "effective_mode": effective, "rollout_cap": cap, "report": visible, "last_success_report": lastReport}
	if e = atomicHostJSON(filepath.Join(root, "status/current.json"), payload, 0640, 0770); e != nil {
		return nil, e
	}
	history := filepath.Join(root, "status/history")
	if e = atomicHostJSON(filepath.Join(history, fmt.Sprintf("%020d.json", finished)), payload, 0640, 0770); e != nil {
		return nil, e
	}
	entries, e := filepath.Glob(filepath.Join(history, "*.json"))
	if e != nil {
		return nil, e
	}
	sort.Strings(entries)
	for i := 0; i < len(entries)-32; i++ {
		if e = os.Remove(entries[i]); e != nil && !errors.Is(e, os.ErrNotExist) {
			return nil, e
		}
	}
	return payload, nil
}
func runHostStatus(args []string) error {
	f := flag.NewFlagSet("host-status", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	root := f.String("state-root", "", "")
	log := f.String("log", "", "")
	code := f.Int("return-code", -999, "")
	requested := f.String("requested-mode", "", "")
	effective := f.String("effective-mode", "", "")
	cap := f.Int("rollout-cap", 0, "")
	started := f.Int64("started-at", -1, "")
	if e := f.Parse(args); e != nil {
		return e
	}
	validMode := func(s string) bool { return s == "report" || s == "dry-run" || s == "refill" }
	if *root == "" || *log == "" || *code == -999 || !validMode(*requested) || !validMode(*effective) || (*cap != 1 && *cap != 5 && *cap != 25) || *started < 0 || f.NArg() != 0 {
		return errors.New("invalid status arguments")
	}
	_, e := recordHostStatus(*root, *log, *code, *requested, *effective, *cap, *started, time.Now().Unix())
	return e
}
func verifyHostAcceptance(root string, started int64, revision, wrapper string) error {
	payload := readHostJSON(filepath.Join(root, "status/current.json"))
	report, _ := payload["report"].(map[string]any)
	if hostInteger(payload["last_attempt_started_unixtime"]) < started || hostInteger(payload["last_attempt_success"]) != 1 || payload["effective_mode"] != "report" || report["data_only"] != true {
		return errors.New("ATS report acceptance failed")
	}
	for name, want := range map[string]string{"deployed-sha": revision, "wrapper-sha256": wrapper} {
		raw, e := os.ReadFile(filepath.Join(root, name))
		if e != nil || strings.TrimSpace(string(raw)) != want {
			return errors.New("ATS deployment acceptance identity changed")
		}
	}
	return nil
}
