// Package browserproxy identifies a deployment-configured browser proxy without
// carrying its address or credentials in a browser execution request.
package browserproxy

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"net/url"
	"strconv"
	"strings"
)

const prefix = "crawler-proxy/v1:"

var ErrPolicy = errors.New("invalid configured browser proxy")

// Endpoint contains startup configuration. Its printable forms deliberately
// omit the endpoint and credentials; only Reference crosses the typed wire.
type Endpoint struct {
	address, username, password, reference string
}

func (Endpoint) String() string   { return "configured browser proxy" }
func (Endpoint) GoString() string { return "configured browser proxy" }

func Parse(raw string) (Endpoint, error) {
	if raw == "" || len(raw) > 8192 || strings.TrimSpace(raw) != raw || strings.ContainsAny(raw, "\r\n\x00") {
		return Endpoint{}, ErrPolicy
	}
	u, err := url.Parse(raw)
	if err != nil || u.Opaque != "" || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.Path != "" && u.Path != "/" {
		return Endpoint{}, ErrPolicy
	}
	if port := u.Port(); port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n < 1 || n > 65535 {
			return Endpoint{}, ErrPolicy
		}
	}
	var username, password string
	if u.User != nil {
		var present bool
		username = u.User.Username()
		password, present = u.User.Password()
		if username == "" || !present || password == "" || strings.ContainsAny(username+password, "\r\n\x00") {
			return Endpoint{}, ErrPolicy
		}
	}
	u.Path, u.RawPath = "", ""
	// Bind credential rotation as well as endpoint order: independently deployed
	// consumers with different protected settings must fail closed.
	digest := sha256.Sum256([]byte(u.String()))
	u.User = nil
	return Endpoint{u.String(), username, password, prefix + hex.EncodeToString(digest[:])}, nil
}

func (e Endpoint) Reference() string { return e.reference }
func (e Endpoint) Address() string   { return e.address }

// Credentials configure only the renderer's private child browser context.
func (e Endpoint) Credentials() (string, string) { return e.username, e.password }

func ValidReference(ref string) bool {
	if !strings.HasPrefix(ref, prefix) || len(ref) != len(prefix)+64 {
		return false
	}
	for _, c := range ref[len(prefix):] {
		if c < '0' || c > '9' {
			if c < 'a' || c > 'f' {
				return false
			}
		}
	}
	return true
}
