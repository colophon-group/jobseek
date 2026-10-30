package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"testing"
)

// Check the wire request against S3's canonical URI rules. Checking that an
// Authorization header exists would also accept a double-escaped signature.
func verifyFixtureS3Signature(r *http.Request, body []byte) bool {
	auth := strings.TrimPrefix(r.Header.Get("Authorization"), "AWS4-HMAC-SHA256 ")
	fields := make(map[string]string)
	for _, field := range strings.Split(auth, ",") {
		key, value, ok := strings.Cut(strings.TrimSpace(field), "=")
		if !ok {
			return false
		}
		fields[key] = value
	}
	credential := strings.Split(fields["Credential"], "/")
	if len(credential) != 5 || credential[0] != "test-key" || credential[2] != "auto" || credential[3] != "s3" || credential[4] != "aws4_request" {
		return false
	}
	var canonicalHeaders strings.Builder
	for _, name := range strings.Split(fields["SignedHeaders"], ";") {
		var value string
		switch name {
		case "host":
			value = r.Host
		case "content-length":
			value = strconv.FormatInt(r.ContentLength, 10)
		default:
			value = strings.Join(r.Header.Values(name), ",")
		}
		fmt.Fprintf(&canonicalHeaders, "%s:%s\n", name, strings.Join(strings.Fields(value), " "))
	}
	payload := sha256.Sum256(body)
	canonical := strings.Join([]string{r.Method, r.URL.EscapedPath(), r.URL.RawQuery, canonicalHeaders.String(), fields["SignedHeaders"], hex.EncodeToString(payload[:])}, "\n")
	digest := sha256.Sum256([]byte(canonical))
	scope := strings.Join(credential[1:], "/")
	toSign := strings.Join([]string{"AWS4-HMAC-SHA256", r.Header.Get("X-Amz-Date"), scope, hex.EncodeToString(digest[:])}, "\n")
	mac := func(key []byte, value string) []byte {
		h := hmac.New(sha256.New, key)
		_, _ = h.Write([]byte(value))
		return h.Sum(nil)
	}
	key := []byte("AWS4test-secret")
	for _, part := range credential[1:] {
		key = mac(key, part)
	}
	return hmac.Equal([]byte(fields["Signature"]), []byte(hex.EncodeToString(mac(key, toSign))))
}

func TestPutUsesS3CanonicalURIAndPythonObjectKeys(t *testing.T) {
	for _, tc := range []struct{ name, locale, path string }{
		{"plain", "en", "en"},
		{"space", "de CH", "de%20CH"},
		{"unicode", "français", "fran%C3%A7ais"},
		{"percent", "en%US", "en%25US"},
		{"plus", "en+US", "en%2BUS"},
		{"slash", "en/US", "en/US"},
		{"brackets", "[en]", "%5Ben%5D"},
		{"empty", "", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			client, server := testR2(t, func(w http.ResponseWriter, r *http.Request) {
				want := "/fixture-bucket/job/fixture-posting/" + tc.path + "/latest.html"
				if r.URL.EscapedPath() != want {
					t.Errorf("object path = %q, want Python-compatible %q", r.URL.EscapedPath(), want)
				}
				body, err := io.ReadAll(r.Body)
				if err != nil || string(body) != "Grüezi <p>fixture</p>" {
					t.Error("object body changed")
				}
				if !verifyFixtureS3Signature(r, body) {
					t.Error("wire request does not have a valid S3 canonical URI signature")
					w.WriteHeader(http.StatusForbidden)
					return
				}
				w.WriteHeader(http.StatusOK)
			})
			defer server.Close()
			if err := client.put(context.Background(), description{PostingID: "fixture-posting", Locale: tc.locale, HTML: "Grüezi <p>fixture</p>"}); err != nil {
				t.Fatal(err)
			}
		})
	}
}
