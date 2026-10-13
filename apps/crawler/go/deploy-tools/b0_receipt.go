package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"regexp"
	"strconv"
	"strings"
	"syscall"
)

var b0Digest = regexp.MustCompile(`^[0-9a-f]{64}$`)
var b0Revision = regexp.MustCompile(`^[0-9a-f]{40}$`)
var b0Image = regexp.MustCompile(`^ghcr\.io/[^/]+/jobseek-crawler@sha256:[0-9a-f]{64}$`)
var b0Positive = regexp.MustCompile(`^[1-9][0-9]*$`)

const b0EpochMaximum = 9_999_999_999_999

func b0PathSync(kind, path string) error {
	flags := os.O_RDONLY | syscall.O_NOFOLLOW | syscall.O_CLOEXEC
	if kind == "directory" {
		flags |= syscall.O_DIRECTORY
	} else if kind != "file" {
		return errors.New("invalid B0 fsync path kind")
	}
	f, err := os.OpenFile(path, flags, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	return f.Sync()
}

func b0Epoch(text string) (int64, bool) {
	if len(text) > 13 || !b0Positive.MatchString(text) {
		return 0, false
	}
	n, err := strconv.ParseInt(text, 10, 64)
	return n, err == nil && n <= b0EpochMaximum
}

// Read through one descriptor and retain the exact receipt bytes for hashing.
// The selected cold wrapper remains responsible for the mutation lock.
func b0Receipt(path string, requireOneLink bool) ([]byte, map[string]string, error) {
	f, err := regularOpenFlags(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC)
	if err != nil {
		return nil, nil, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil, nil, err
	}
	meta, ok := info.Sys().(*syscall.Stat_t)
	if !ok || info.Mode().Perm() != 0600 || meta.Uid != uint32(os.Geteuid()) || requireOneLink && meta.Nlink != 1 {
		return nil, nil, errors.New("unsafe B0 receipt metadata")
	}
	body, err := io.ReadAll(io.LimitReader(f, 4097))
	if err != nil || len(body) > 4096 || !bytes.HasSuffix(body, []byte("\n")) || bytes.ContainsAny(body, "\r\x00") {
		return nil, nil, errors.New("unsafe B0 receipt bytes")
	}
	for _, b := range body {
		if b > 127 {
			return nil, nil, errors.New("non-ASCII B0 receipt")
		}
	}
	values := map[string]string{}
	for _, line := range strings.Split(string(body[:len(body)-1]), "\n") {
		if strings.Count(line, "=") != 1 {
			return nil, nil, errors.New("invalid B0 receipt field")
		}
		key, value, _ := strings.Cut(line, "=")
		if _, exists := values[key]; exists {
			return nil, nil, errors.New("duplicate B0 receipt field")
		}
		values[key] = value
	}
	return body, values, nil
}

func validateB0Receipt(v map[string]string, expected map[string]string) error {
	keys := strings.Fields("schema state cohort namespace shard_id routing_epoch plan_digest compose_digest crawler_image_ref deploy_revision activated_at_epoch")
	rollback := v["state"] == "rollback-pending" || v["state"] == "rollback-cleared"
	if rollback {
		keys = append(keys, "source_receipt_state", "source_receipt_sha256", "retirement_routing_epoch")
	}
	if len(v) != len(keys) {
		return errors.New("B0 receipt field count differs")
	}
	for _, k := range keys {
		if _, ok := v[k]; !ok {
			return errors.New("B0 receipt field missing")
		}
	}
	for k, want := range expected {
		if v[k] != want {
			return errors.New("B0 receipt identity differs")
		}
	}
	epoch, valid := b0Epoch(v["routing_epoch"])
	if v["schema"] != "jobseek.lightpanda-b0-active/v1" || !valid || !b0Digest.MatchString(v["plan_digest"]) || !b0Digest.MatchString(v["compose_digest"]) || !b0Positive.MatchString(v["activated_at_epoch"]) || !rollback && v["state"] != "active" && v["state"] != "pending" {
		return errors.New("B0 receipt values differ")
	}
	if rollback {
		retirement, valid := b0Epoch(v["retirement_routing_epoch"])
		unreserved := v["retirement_routing_epoch"] == "unreserved"
		if v["source_receipt_state"] != "active" && v["source_receipt_state"] != "pending" || !b0Digest.MatchString(v["source_receipt_sha256"]) || !unreserved && (!valid || retirement <= epoch) || v["state"] == "rollback-cleared" && unreserved {
			return errors.New("B0 reversal identity differs")
		}
	}
	return nil
}

// Both original receipt consumers retain their distinct output ABI and checks.
func b0ReceiptReply(args []string, attest bool) (string, error) {
	count := 6
	if attest {
		count = 9
	}
	if len(args) != count {
		return "", errors.New("invalid B0 receipt arguments")
	}
	body, v, err := b0Receipt(args[0], !attest)
	if err != nil {
		return "", err
	}
	expected := map[string]string{"cohort": args[1], "namespace": args[2], "shard_id": args[3]}
	if attest {
		expected["routing_epoch"], expected["crawler_image_ref"], expected["deploy_revision"], expected["compose_digest"], expected["state"] = args[4], args[5], args[6], args[7], args[8]
		if !b0Revision.MatchString(v["deploy_revision"]) || !b0Image.MatchString(v["crawler_image_ref"]) {
			return "", errors.New("B0 receipt release differs")
		}
	} else {
		expected["crawler_image_ref"], expected["deploy_revision"] = args[4], args[5]
	}
	if err := validateB0Receipt(v, expected); err != nil {
		return "", err
	}
	retirement, sourceState, sourceSHA := "-", "-", "-"
	if v["state"] == "rollback-pending" || v["state"] == "rollback-cleared" {
		retirement, sourceState, sourceSHA = v["retirement_routing_epoch"], v["source_receipt_state"], v["source_receipt_sha256"]
	}
	if attest {
		hash := sha256.Sum256(body)
		return strings.Join([]string{hex.EncodeToString(hash[:]), v["plan_digest"], sourceSHA, retirement, sourceState}, "\t"), nil
	}
	return strings.Join([]string{v["state"], v["routing_epoch"], retirement, sourceState, sourceSHA, v["plan_digest"]}, "\t"), nil
}

func b0EpochReply(body, expected string) (string, error) {
	if len(body) > 4096 {
		return "", errors.New("B0 epoch reply exceeds limit")
	}
	decoder := json.NewDecoder(strings.NewReader(body))
	decoder.UseNumber()
	var v map[string]any
	if decoder.Decode(&v) != nil || decoder.Decode(new(any)) != io.EOF {
		return "", errors.New("invalid B0 epoch reply")
	}
	number, ok := v["routing_epoch"].(json.Number)
	if !ok {
		return "", errors.New("invalid B0 epoch type")
	}
	n, err := number.Int64()
	if err != nil || n < 1 || n > b0EpochMaximum {
		return "", errors.New("invalid B0 epoch value")
	}
	if expected == "" {
		if len(v) != 1 {
			return "", errors.New("invalid B0 allocator fields")
		}
		return number.String(), nil
	}
	want, valid := b0Epoch(expected)
	if !valid || n != want || len(v) != 2 || v["current"] != true {
		return "", errors.New("invalid B0 epoch attestation")
	}
	return "", nil
}
