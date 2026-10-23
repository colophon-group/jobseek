package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

func acquireInventoryLock(path string) (func(), error) {
	if e := os.MkdirAll(filepath.Dir(path), 0755); e != nil {
		return nil, e
	}
	file, e := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0600)
	if e != nil {
		return nil, e
	}
	if e = syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); e != nil {
		file.Close()
		return nil, fmt.Errorf("inventory run already active")
	}
	fail := func(err error) (func(), error) {
		syscall.Flock(int(file.Fd()), syscall.LOCK_UN)
		file.Close()
		return nil, err
	}
	if e = file.Truncate(0); e != nil {
		return fail(e)
	}
	if _, e = fmt.Fprintf(file, "pid=%d\n", os.Getpid()); e != nil {
		return fail(e)
	}
	if e = file.Sync(); e != nil {
		return fail(e)
	}
	return func() { _ = syscall.Flock(int(file.Fd()), syscall.LOCK_UN); _ = file.Close() }, nil
}
func fsyncInventoryDirectory(path string) error {
	f, e := os.Open(path)
	if e != nil {
		return e
	}
	defer f.Close()
	return f.Sync()
}
func atomicInventoryBytes(path string, body []byte) error {
	parent := filepath.Dir(path)
	if e := os.MkdirAll(parent, 0755); e != nil {
		return e
	}
	f, e := os.CreateTemp(parent, "."+filepath.Base(path)+".")
	if e != nil {
		return e
	}
	temp := f.Name()
	defer os.Remove(temp)
	if _, e = f.Write(body); e == nil {
		e = f.Sync()
	}
	closeError := f.Close()
	if e != nil {
		return e
	}
	if closeError != nil {
		return closeError
	}
	if e = os.Rename(temp, path); e != nil {
		return e
	}
	return fsyncInventoryDirectory(parent)
}
func atomicInventoryJSON(path string, value any) error {
	var b bytes.Buffer
	encoder := json.NewEncoder(&b)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if e := encoder.Encode(value); e != nil {
		return e
	}
	return atomicInventoryBytes(path, b.Bytes())
}
func boundedInventoryFile(path string, limit int64) ([]byte, error) {
	f, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil {
		return nil, e
	}
	if !info.Mode().IsRegular() || info.Size() > limit {
		return nil, sourceError("cached object exceeds byte limit")
	}
	body, e := io.ReadAll(io.LimitReader(f, limit+1))
	if e != nil {
		return nil, e
	}
	if int64(len(body)) > limit {
		return nil, sourceError("cached object exceeds byte limit")
	}
	return body, nil
}
func readValidInventoryObject(path, digest string, limit int64) ([]byte, error) {
	body, e := boundedInventoryFile(path, limit)
	if e != nil || sha256Bytes(body) != digest {
		return nil, sourceError("cached inventory object is missing or corrupt")
	}
	return body, nil
}
func validInventoryObject(path, digest string, size, limit int64) bool {
	f, e := os.Open(path)
	if e != nil {
		return false
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil || !info.Mode().IsRegular() || info.Size() != size || size > limit {
		return false
	}
	h := sha256.New()
	n, e := io.Copy(h, io.LimitReader(f, limit+1))
	return e == nil && n == size && hex.EncodeToString(h.Sum(nil)) == digest
}
func inventoryDirectorySize(root string) int64 {
	var total int64
	_ = filepath.Walk(root, func(_ string, info os.FileInfo, e error) error {
		if e == nil && info.Mode().IsRegular() {
			total += info.Size()
		}
		return nil
	})
	return total
}
func inventoryNowISO(now time.Time) string {
	now = now.UTC().Truncate(time.Microsecond)
	if now.Nanosecond() == 0 {
		return now.Format("2006-01-02T15:04:05+00:00")
	}
	return now.Format("2006-01-02T15:04:05.000000+00:00")
}
func pythonStateSize(value any) (int, error) {
	var b bytes.Buffer
	encoder := json.NewEncoder(&b)
	encoder.SetEscapeHTML(false)
	if e := encoder.Encode(value); e != nil {
		return 0, e
	}
	raw := bytes.TrimSuffix(b.Bytes(), []byte("\n"))
	n := len(raw)
	quoted, escaped := false, false
	for _, c := range raw {
		if quoted {
			if escaped {
				escaped = false
			} else if c == '\\' {
				escaped = true
			} else if c == '"' {
				quoted = false
			}
		} else if c == '"' {
			quoted = true
		} else if c == ',' || c == ':' {
			n++
		}
	}
	return n, nil
}
func removeInventoryFile(path string) error {
	e := os.Remove(path)
	if errors.Is(e, os.ErrNotExist) {
		return nil
	}
	return e
}
func cloneFamilyCounts(in map[string]int) map[string]int {
	out := map[string]int{}
	for k, v := range in {
		out[k] = v
	}
	return out
}
func inventoryStringPointer(value string) *string { return &value }
func inventoryETagValue(value any) *string {
	s, ok := value.(string)
	if !ok {
		return nil
	}
	return &s
}
func sourceSchemaVersion(value any) bool {
	switch v := value.(type) {
	case json.Number:
		return string(v) == "1"
	case bool:
		return v
	case int:
		return v == 1
	}
	return false
}
func differenceFamilies(a, b map[string]int) []string {
	out := []string{}
	for k := range a {
		if _, ok := b[k]; !ok {
			out = append(out, k)
		}
	}
	sortStrings(out)
	return out
}
func sortStrings(v []string) {
	for i := 1; i < len(v); i++ {
		for j := i; j > 0 && strings.Compare(v[j], v[j-1]) < 0; j-- {
			v[j], v[j-1] = v[j-1], v[j]
		}
	}
}
