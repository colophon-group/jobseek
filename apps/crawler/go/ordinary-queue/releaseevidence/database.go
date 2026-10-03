package releaseevidence

import (
	"context"
	"encoding/json"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// VerifiedDatabaseConfig reads credentials only from the exact protected active
// generation. It never sources a file, interprets shell syntax or uses caller
// PG settings/service/pass/certificate files. The installed host CLI clears PG*
// variables before this API; other callers must also provide that environment.
// The returned config contains credentials and must stay private in memory.
func VerifiedDatabaseConfig(ctx context.Context, directory, owner, filesSHA string) (*pgxpool.Config, error) {
	if ctx == nil || ctx.Err() != nil || !shaPattern.MatchString(filesSHA) {
		return nil, reject("bound database generation")
	}
	for _, pair := range os.Environ() {
		key, value, _ := strings.Cut(pair, "=")
		if strings.HasPrefix(key, "PG") && value != "" {
			return nil, reject("cleared database environment required")
		}
	}
	f, err := VerifyFiles(ctx, directory, owner)
	if err != nil || f.SHA256() != filesSHA {
		return nil, reject("verified database generation")
	}
	var doc document
	if json.Unmarshal([]byte(f.Body()), &doc) != nil {
		return nil, reject("database file identity")
	}
	root, err := os.OpenRoot(directory)
	if err != nil {
		return nil, reject("database generation root")
	}
	defer root.Close()
	r := &reader{ctx: ctx, root: root, hashes: map[string]string{}}
	body, err := hashed(r, "environment.env", doc.FileSHA256["environment.env"], 4<<20)
	if err != nil {
		return nil, reject("verified database environment")
	}
	line, err := lines(body)
	if err != nil {
		return nil, reject("database environment framing")
	}
	value, err := exact(line, "LOCAL_DATABASE_URL")
	if err != nil || len(value) > 8192 {
		return nil, reject("explicit selected database URL")
	}
	u, err := url.Parse(value)
	if err != nil || u.Opaque != "" || u.Fragment != "" || (u.Scheme != "postgres" && u.Scheme != "postgresql") || u.User == nil || u.User.Username() == "" || u.Hostname() == "" || strings.Contains(u.Host, ",") || len(u.Path) < 2 || strings.Contains(u.Path[1:], "/") {
		return nil, reject("complete selected database URL")
	}
	password, hasPassword := u.User.Password()
	if !hasPassword || password == "" {
		return nil, reject("selected database credentials required")
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		return nil, reject("selected database URL parameters")
	}
	for key, values := range q {
		if key != "sslmode" || len(values) != 1 {
			return nil, reject("closed selected database parameters")
		}
	}
	mode := q.Get("sslmode")
	if mode == "" {
		mode = "prefer"
	}
	switch mode {
	case "disable", "prefer", "require", "verify-full":
	default:
		return nil, reject("selected database TLS mode")
	}
	// Override pgx's OS-user defaults as well as environment fallbacks. No local
	// client-key, service or password file can become a credential source.
	for key, value := range map[string]string{"sslmode": mode, "sslcert": "", "sslkey": "", "sslrootcert": "", "sslpassword": "", "passfile": "", "connect_timeout": "5", "target_session_attrs": "read-write", "application_name": "jobseek:host:cold-coordinator", "options": "", "timezone": "UTC", "min_protocol_version": "3.0", "max_protocol_version": "3.0"} {
		q.Set(key, value)
	}
	u.RawQuery = q.Encode()
	config, err := pgxpool.ParseConfig(u.String())
	if err != nil {
		return nil, reject("selected database configuration")
	}
	config.MinConns, config.MaxConns = 0, 2
	config.ConnConfig.ConnectTimeout = 5 * time.Second
	config.ConnConfig.RuntimeParams = map[string]string{"application_name": "jobseek:host:cold-coordinator", "search_path": "pg_catalog,public", "statement_timeout": "10000", "timezone": "UTC"}
	again, err := VerifyFiles(ctx, directory, owner)
	if err != nil || again.SHA256() != filesSHA {
		return nil, reject("database generation readback")
	}
	return config, nil
}
