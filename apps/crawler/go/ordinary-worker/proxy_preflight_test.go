package worker

import (
	"errors"
	"strings"
	"testing"
)

func TestProxyPreflightEnvironmentOnly(t *testing.T) {
	for _, tc := range []struct {
		name, provider, pool, legacy, want string
	}{
		{"disabled", "none", "[]", "", "mode=disabled, pool_entries=0"},
		{"pool", "webshare", `["http://private-user:private-password@p.webshare.io:10001"]`, "", "mode=backbone_pool, pool_entries=1"},
		{"legacy", "webshare", "[]", "http://private-user:private-password@proxy.invalid:80", "mode=legacy_direct, pool_entries=0"},
		{"missing", "webshare", "[]", "", ""},
		{"malformed", "webshare", "private-non-json", "", ""},
		{"unsupported", "private-provider", "[]", "", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			env := map[string]string{"PROXY_PROVIDER": tc.provider, "WEBSHARE_PROXY_URLS": tc.pool, "WEBSHARE_PROXY_URL": tc.legacy}
			message, err := ProxyPreflight(func(k string) string { return env[k] })
			if tc.want == "" {
				if !errors.Is(err, ErrStartup) || message != "" {
					t.Fatal("invalid startup settings were accepted")
				}
			} else if err != nil || message != "Runtime proxy configuration valid: "+tc.want {
				t.Fatal("proxy preflight result differs")
			}
			if strings.Contains(message, "private") || strings.Contains(message, "proxy.invalid") || strings.Contains(message, "webshare.io") {
				t.Fatal("proxy preflight disclosed endpoint settings")
			}
		})
	}
}
