package releaseevidence

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func databaseGenerationFixture(t *testing.T, value string) (string, *Files) {
	t.Helper()
	g := fixture(t).directory
	// Rebuild only the environment hash and its manifest binding. Database
	// credentials are not identity fields in the selected success marker.
	p := filepath.Join(g, "environment.env")
	body, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	old := digest(body)
	body = append(body, []byte("LOCAL_DATABASE_URL="+value+"\n")...)
	if os.WriteFile(p, body, 0600) != nil || os.WriteFile(filepath.Join(g, "environment.sha256"), []byte(digest(body)+"\n"), 0600) != nil {
		t.Fatal("selected environment fixture")
	}
	m, err := os.ReadFile(filepath.Join(g, "release.manifest"))
	if err != nil || os.WriteFile(filepath.Join(g, "release.manifest"), []byte(strings.ReplaceAll(string(m), old, digest(body))), 0600) != nil {
		t.Fatal("selected env hash fixture")
	}
	f, err := VerifyFiles(context.Background(), g, "colophon-group")
	if err != nil {
		t.Fatal("verified selected env fixture", err)
	}
	return g, f
}

func TestSelectedDatabaseUsesOnlyVerifiedCredentialURLAndClosedSettings(t *testing.T) {
	g, f := databaseGenerationFixture(t, "postgresql://crawler:fixture-password@127.0.0.1:54401/jobseek_ordinary_worker_test?sslmode=disable")
	c, err := VerifiedDatabaseConfig(context.Background(), g, "colophon-group", f.SHA256())
	if err != nil || c.ConnConfig.Host != "127.0.0.1" || c.ConnConfig.Port != 54401 || c.ConnConfig.User != "crawler" || c.ConnConfig.Password != "fixture-password" || c.ConnConfig.Database != "jobseek_ordinary_worker_test" || c.ConnConfig.TLSConfig != nil {
		t.Fatal("explicit selected DB config refused", err)
	}
	for _, fault := range []string{"wrong hash", "PGSERVICE", "PGHOST", "environment drift"} {
		t.Run(fault, func(t *testing.T) {
			expected := f.SHA256()
			if fault == "wrong hash" {
				expected = strings.Repeat("f", 64)
			}
			if strings.HasPrefix(fault, "PG") {
				t.Setenv(fault, "caller-sensitive-value")
			}
			if fault == "environment drift" {
				if os.WriteFile(filepath.Join(g, "environment.env"), []byte("LOCAL_DATABASE_URL=changed\n"), 0600) != nil {
					t.Fatal("drift fixture")
				}
			}
			got, err := VerifiedDatabaseConfig(context.Background(), g, "colophon-group", expected)
			if err == nil || got != nil || strings.Contains(err.Error(), "fixture-password") || strings.Contains(err.Error(), "caller-sensitive-value") {
				t.Fatal("unsafe selected DB admitted/disclosed", fault)
			}
		})
	}
}

func TestSelectedDatabaseRefusesMissingCredentialOrExternalCredentialParameters(t *testing.T) {
	for _, value := range []string{"invalid", "postgresql://127.0.0.1/db", "postgresql://crawler@127.0.0.1/db", "postgresql://crawler:password@127.0.0.1/", "postgresql://crawler:password@127.0.0.1,127.0.0.2/db", "postgresql://crawler:password@127.0.0.1/db?service=external", "postgresql://crawler:password@127.0.0.1/db?sslkey=/private/external-key", "postgresql://crawler:password@127.0.0.1/db?sslmode=disable&sslmode=require", "postgresql://crawler:password@127.0.0.1/db#fragment"} {
		t.Run("closed URL", func(t *testing.T) {
			g, f := databaseGenerationFixture(t, value)
			c, err := VerifiedDatabaseConfig(context.Background(), g, "colophon-group", f.SHA256())
			if err == nil || c != nil || strings.Contains(err.Error(), "password") || strings.Contains(err.Error(), "external-key") {
				t.Fatal("external/incomplete credential URL admitted/disclosed")
			}
		})
	}
}
