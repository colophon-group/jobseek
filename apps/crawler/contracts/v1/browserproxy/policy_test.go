package browserproxy

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

func TestConfiguredProxyReferenceBindsRotationWithoutDisclosingCredentials(t *testing.T) {
	a, err := Parse("http://fixture-user:fixture-password@proxy.example:8000/")
	if err != nil || !ValidReference(a.Reference()) || a.Address() != "http://proxy.example:8000" {
		t.Fatal("configured proxy did not produce a credential-free address/reference")
	}
	b, _ := Parse("http://fixture-user:rotated-password@proxy.example:8000")
	c, _ := Parse("http://fixture-user:fixture-password@proxy.example:8000")
	if a.Reference() == b.Reference() || a.Reference() != c.Reference() {
		t.Fatal("rotation or canonical slash binding differs")
	}
	username, password := a.Credentials()
	if username != "fixture-user" || password != "fixture-password" {
		t.Fatal("startup authentication was not retained")
	}
	raw, _ := json.Marshal(a)
	for _, output := range []string{a.Reference(), a.Address(), fmt.Sprint(a), fmt.Sprintf("%#v", a), string(raw)} {
		if strings.Contains(output, username) || strings.Contains(output, password) {
			t.Fatal("configured proxy disclosed authentication")
		}
	}
}

func TestConfiguredProxyRejectsUnsupportedAndAmbiguousSettings(t *testing.T) {
	for _, raw := range []string{"", " http://proxy.example", "socks5://proxy.example", "http://user@proxy.example", "http://user:@proxy.example", "http://proxy.example/path", "http://proxy.example?", "http://proxy.example?password=value", "http://proxy.example#fragment", "http://proxy.example:0", "http://proxy.example:65536", "http://user:%0A@proxy.example", "http://proxy.example\n"} {
		if _, err := Parse(raw); err != ErrPolicy {
			t.Fatal("invalid startup proxy accepted")
		}
	}
	for _, raw := range []string{"", "http://user:password@proxy.example", "crawler-proxy/v1:" + strings.Repeat("a", 63), "crawler-proxy/v1:" + strings.Repeat("A", 64), "crawler-proxy/v1:" + strings.Repeat("g", 64)} {
		if ValidReference(raw) {
			t.Fatal("unbounded or endpoint-bearing request policy accepted")
		}
	}
}
