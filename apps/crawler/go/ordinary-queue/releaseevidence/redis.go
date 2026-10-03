package releaseevidence

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
)

var redisMemoryPattern = regexp.MustCompile(`^[1-9][0-9]{0,12}(kb|mb|gb)?$`)
var decimalInodePattern = regexp.MustCompile(`^[1-9][0-9]{0,19}$`)
var decimalDescriptorPattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,9})$`)

// RedisEndpoint is an opaque selected-release observation, not runtime admission
// or exclusion of Redis producers/leases. The connection URL stays in memory;
// body contains only hashes and observed daemon/socket identities. Reobserve it
// under the host mutation lock before/after every native phase.
type RedisEndpoint struct {
	body, digest, connection, inode string
	pid                             int64
	port                            int
}

func (e *RedisEndpoint) Body() string   { return e.body }
func (e *RedisEndpoint) SHA256() string { return e.digest }

// ConnectionURL is for the trusted in-process coordinator only. It derives from
// effective selected service execution; never journal or log private values.
func (e *RedisEndpoint) ConnectionURL(ctx context.Context) (string, error) {
	if e == nil || e.Check(ctx) != nil {
		return "", reject("live selected Redis endpoint")
	}
	return e.connection, nil
}

func selectedRedisURL(raw string) (string, int, error) {
	if len(raw) == 0 || len(raw) > 8192 {
		return "", 0, reject("selected Redis URL framing")
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "redis" || u.Opaque != "" || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.RawPath != "" || (u.Hostname() != "localhost" && u.Hostname() != "127.0.0.1") {
		return "", 0, reject("closed selected loopback Redis URL")
	}
	port, err := strconv.Atoi(u.Port())
	if err != nil || port < 1 || port > 65535 || strconv.Itoa(port) != u.Port() || u.Host != net.JoinHostPort(u.Hostname(), u.Port()) {
		return "", 0, reject("explicit selected Redis port")
	}
	if !strings.HasPrefix(u.Path, "/") {
		return "", 0, reject("explicit selected Redis database")
	}
	db, err := strconv.Atoi(strings.TrimPrefix(u.Path, "/"))
	if err != nil || db < 0 || db > 1023 || u.Path != "/"+strconv.Itoa(db) {
		return "", 0, reject("canonical selected Redis database")
	}
	return "redis://127.0.0.1:" + strconv.Itoa(port) + u.Path, port, nil
}

func selectedRedisServerPort(cmd []string) (int, error) {
	if len(cmd) == 0 || cmd[0] != "redis-server" || len(cmd) > 64 || (len(cmd)-1)%2 != 0 {
		return 0, reject("explicit Redis server command")
	}
	port, bind := 6379, false
	seen := map[string]bool{}
	for n := 1; n < len(cmd); n += 2 {
		key, value := cmd[n], cmd[n+1]
		if seen[key] {
			return 0, reject("duplicate Redis server option")
		}
		seen[key] = true
		switch key {
		case "--bind":
			if value != "127.0.0.1" {
				return 0, reject("selected Redis bind")
			}
			bind = true
		case "--port":
			p, err := strconv.Atoi(value)
			if err != nil || p < 1 || p > 65535 || strconv.Itoa(p) != value {
				return 0, reject("selected Redis server port")
			}
			port = p
		case "--maxmemory":
			if !redisMemoryPattern.MatchString(value) {
				return 0, reject("selected Redis memory bound")
			}
		case "--maxmemory-policy":
			if value != "noeviction" {
				return 0, reject("selected Redis eviction policy")
			}
		case "--appendonly":
			if value != "yes" && value != "no" {
				return 0, reject("selected Redis persistence option")
			}
		case "--save":
			if value != "" {
				return 0, reject("unverified Redis save configuration")
			}
		default:
			return 0, reject("unverified Redis command option or configuration file")
		}
	}
	if !bind {
		return 0, reject("explicit loopback Redis bind required")
	}
	return port, nil
}

// Socket ownership uses the trusted Linux /proc view, never caller paths or
// namespaces. All host listeners on the selected port must reduce to one exact
// IPv4 loopback listener whose inode belongs to the observed Redis daemon PID.
func redisListener(ctx context.Context, pid int64, port int) (string, error) {
	if runtime.GOOS != "linux" {
		return "", reject("Linux Redis socket observation")
	}
	return redisListenerAt(ctx, "/proc", pid, port)
}

func redisListenerAt(ctx context.Context, root string, pid int64, port int) (string, error) {
	if ctx == nil || ctx.Err() != nil || pid < 1 || pid > 2147483647 || port < 1 || port > 65535 {
		return "", reject("bounded Redis socket identity")
	}
	f, err := os.Open(filepath.Join(root, "net/tcp"))
	if err != nil {
		return "", reject("host TCP observation")
	}
	body, err := io.ReadAll(io.LimitReader(f, (8<<20)+1))
	_ = f.Close()
	if err != nil || len(body) > 8<<20 {
		return "", reject("bounded host TCP observation")
	}
	rows := strings.Split(string(body), "\n")
	if len(rows) < 2 || !strings.Contains(rows[0], "local_address") {
		return "", reject("complete host TCP observation")
	}
	inode := ""
	for _, line := range rows[1:] {
		if strings.TrimSpace(line) == "" {
			continue
		}
		f := strings.Fields(line)
		if len(f) < 10 {
			return "", reject("complete TCP listener fields")
		}
		address, p, ok := strings.Cut(f[1], ":")
		n, e := strconv.ParseUint(p, 16, 16)
		if !ok || len(address) != 8 || len(p) != 4 || e != nil {
			return "", reject("TCP listener address framing")
		}
		if int(n) != port || f[3] != "0A" {
			continue
		}
		if address != "0100007F" || inode != "" || !decimalInodePattern.MatchString(f[9]) {
			return "", reject("unique loopback Redis listener")
		}
		inode = f[9]
	}
	if inode == "" {
		return "", reject("selected Redis listener absent")
	}
	// A dual-stack wildcard must not create a second route to this port.
	v6, err := os.Open(filepath.Join(root, "net/tcp6"))
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return "", reject("host IPv6 TCP observation")
	}
	if err == nil {
		body, err := io.ReadAll(io.LimitReader(v6, (8<<20)+1))
		_ = v6.Close()
		if err != nil || len(body) > 8<<20 {
			return "", reject("bounded IPv6 TCP observation")
		}
		rows := strings.Split(string(body), "\n")
		if len(rows) < 2 || !strings.Contains(rows[0], "local_address") {
			return "", reject("complete IPv6 TCP observation")
		}
		for _, line := range rows[1:] {
			if strings.TrimSpace(line) == "" {
				continue
			}
			fields := strings.Fields(line)
			if len(fields) < 10 {
				return "", reject("complete IPv6 listener fields")
			}
			address, p, ok := strings.Cut(fields[1], ":")
			n, e := strconv.ParseUint(p, 16, 16)
			if !ok || len(address) != 32 || len(p) != 4 || e != nil {
				return "", reject("IPv6 listener framing")
			}
			if int(n) == port && fields[3] == "0A" {
				return "", reject("ambiguous IPv6 Redis listener")
			}
		}
	}
	fd := filepath.Join(root, strconv.FormatInt(pid, 10), "fd")
	d, err := os.Open(fd)
	if err != nil {
		return "", reject("Redis daemon descriptor root")
	}
	entries, err := d.ReadDir(4097)
	_ = d.Close()
	if err != nil && err != io.EOF || len(entries) > 4096 {
		return "", reject("bounded Redis daemon descriptor observation")
	}
	matched := false
	for _, entry := range entries {
		if !decimalDescriptorPattern.MatchString(entry.Name()) {
			return "", reject("Redis descriptor framing")
		}
		target, err := os.Readlink(filepath.Join(fd, entry.Name()))
		// Client descriptors may disappear while Redis keeps its listener. They
		// cannot satisfy the exact listener inode and are not endpoint drift.
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			return "", reject("Redis descriptor observation changed")
		}
		if target == "socket:["+inode+"]" {
			matched = true
		}
	}
	if !matched || ctx.Err() != nil {
		return "", reject("listener not owned by selected Redis daemon")
	}
	return inode, nil
}

func (e *RedisEndpoint) Check(ctx context.Context) error {
	if e == nil || !shaPattern.MatchString(e.digest) || digest([]byte(e.body)) != e.digest {
		return reject("opaque Redis endpoint binding")
	}
	inode, err := redisListener(ctx, e.pid, e.port)
	if err != nil || inode != e.inode {
		return reject("selected Redis listener changed")
	}
	return nil
}

// RequireSelectedRedisEndpoint uses the complete freshly verified execution and
// inventory plus the exact selected release's opaque resolved Compose/images.
// It does not connect, mutate Redis, select a release, clear leases or claim
// all-writer exclusion. Docker access remains with the trusted host coordinator.
func RequireSelectedRedisEndpoint(ctx context.Context, filesSHA string, inventory *Containers, images []*Images, execution *Execution) (*RedisEndpoint, error) {
	return requireSelectedRedisEndpoint(ctx, filesSHA, inventory, images, execution, redisListener)
}

func requireSelectedRedisEndpoint(ctx context.Context, filesSHA string, inventory *Containers, images []*Images, execution *Execution, listener func(context.Context, int64, int) (string, error)) (*RedisEndpoint, error) {
	if ctx == nil || ctx.Err() != nil || !shaPattern.MatchString(filesSHA) || inventory == nil || execution == nil || listener == nil {
		return nil, reject("explicit selected Redis evidence")
	}
	again, err := RequireContainerExecution(ctx, inventory, images)
	if err != nil || again.SHA256() != execution.SHA256() || again.Body() != execution.Body() {
		return nil, reject("fresh complete Redis execution binding")
	}
	var selected *Images
	var doc imageDocument
	for _, image := range images {
		var d imageDocument
		if json.Unmarshal([]byte(image.Body()), &d) != nil {
			return nil, reject("selected Redis image evidence")
		}
		if d.FileEvidenceSHA256 == filesSHA {
			if selected != nil {
				return nil, reject("unique selected Redis generation")
			}
			selected, doc = image, d
		}
	}
	if selected == nil {
		return nil, reject("selected Redis generation absent")
	}
	var compose struct {
		Name     string
		Services map[string]struct {
			Environment map[string]*string
			NetworkMode string `json:"network_mode"`
		}
	}
	var settingsCompose composeExecution
	if json.Unmarshal(selected.compose, &compose) != nil || json.Unmarshal(selected.compose, &settingsCompose) != nil || compose.Name != doc.Project {
		return nil, reject("selected Redis Compose binding")
	}
	var inspected []struct {
		ID     string `json:"Id"`
		Config json.RawMessage
	}
	if json.Unmarshal(selected.imageInspect, &inspected) != nil {
		return nil, reject("selected Redis image defaults")
	}
	defaults := map[string]executionConfig{}
	for _, image := range inspected {
		c, err := decodeExecutionConfig(image.Config, false)
		if err != nil {
			return nil, reject("selected Redis defaults framing")
		}
		defaults[image.ID] = c
	}
	connection, port := "", 0
	type consumer struct {
		Service    string   `json:"service"`
		URLSHA256  string   `json:"effective_url_sha256"`
		Containers []string `json:"containers"`
	}
	consumers := []consumer{}
	observedConsumers := 0
	names := []string{}
	for name := range doc.Services {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		image := doc.Services[name]
		c, ok := defaults[image.ImageID]
		if !ok {
			return nil, reject("selected Redis service defaults absent")
		}
		env, err := executionEnvironment(c.Env)
		if err != nil {
			return nil, err
		}
		for key, value := range compose.Services[name].Environment {
			if value == nil {
				return nil, reject("unresolved selected service environment")
			}
			env[key] = *value
		}
		raw, present := env["REDIS_URL"]
		if !present {
			continue
		}
		url, p, err := selectedRedisURL(raw)
		if err != nil || compose.Services[name].NetworkMode != "host" || connection != "" && url != connection {
			return nil, reject("consistent selected host Redis consumers")
		}
		connection, port = url, p
		joined := consumer{Service: name, URLSHA256: digest([]byte(raw)), Containers: []string{}}
		for _, row := range inventory.rows {
			if row.Project != doc.Project || row.Service != name || row.ImageID != image.ImageID || row.Oneoff || !row.OneoffKnown {
				continue
			}
			private := inventory.commands[row.ID]
			if _, err := verifyExecutionSettings(name, selected.compose, settingsCompose, c, private); err != nil {
				return nil, reject("selected Redis consumer execution drift")
			}
			var host struct{ NetworkMode string }
			if json.Unmarshal(private.host, &host) != nil || host.NetworkMode != "host" {
				return nil, reject("selected Redis consumer network drift")
			}
			joined.Containers = append(joined.Containers, row.ID)
			observedConsumers++
		}
		sort.Strings(joined.Containers)
		consumers = append(consumers, joined)
	}
	if connection == "" || len(consumers) == 0 || observedConsumers == 0 {
		return nil, reject("selected Redis consumers absent")
	}
	server, ok := doc.Services["redis"]
	if !ok || compose.Services["redis"].NetworkMode != "host" {
		return nil, reject("selected host Redis daemon absent")
	}
	var daemon *containerRow
	for n, row := range inventory.rows {
		if row.Project != doc.Project || row.Service != "redis" {
			continue
		}
		if daemon != nil || row.ImageID != server.ImageID || !row.OneoffKnown || row.Oneoff || !row.Running || row.Paused || row.Restarting || row.Status != "running" || row.PID < 1 {
			return nil, reject("unique live selected Redis daemon")
		}
		private := inventory.commands[row.ID]
		if !passiveDaemon("redis", server, private) {
			return nil, reject("authenticated Redis daemon role")
		}
		c, ok := defaults[row.ImageID]
		if !ok {
			return nil, reject("Redis daemon defaults absent")
		}
		settings, err := verifyExecutionSettings("redis", selected.compose, settingsCompose, c, private)
		if err != nil {
			return nil, reject("Redis daemon execution drift")
		}
		actualPort, err := selectedRedisServerPort(settings.Cmd)
		var host struct{ NetworkMode string }
		if err != nil || actualPort != port || json.Unmarshal(private.host, &host) != nil || host.NetworkMode != "host" {
			return nil, reject("Redis daemon listener/network drift")
		}
		daemon = &inventory.rows[n]
	}
	if daemon == nil {
		return nil, reject("selected Redis daemon not observed")
	}
	inode, err := listener(ctx, daemon.PID, port)
	if err != nil {
		return nil, reject("selected Redis daemon socket authority")
	}
	body, err := json.Marshal(struct {
		Version          string     `json:"version"`
		FilesSHA         string     `json:"selected_file_evidence_sha256"`
		ImageSHA         string     `json:"selected_image_evidence_sha256"`
		ExecutionSHA     string     `json:"execution_sha256"`
		InventorySHA     string     `json:"inventory_sha256"`
		ConnectionSHA    string     `json:"connection_sha256"`
		DaemonID         string     `json:"daemon_id"`
		DaemonPID        int64      `json:"daemon_pid"`
		SocketInode      string     `json:"listener_inode"`
		Consumers        []consumer `json:"consumers"`
		RuntimeAdmission bool       `json:"runtime_admission"`
	}{"jobseek.crawler-selected-redis/v1", filesSHA, selected.SHA256(), execution.SHA256(), inventory.SHA256(), digest([]byte(connection)), daemon.ID, daemon.PID, inode, consumers, false})
	if err != nil || ctx.Err() != nil {
		return nil, reject("canonical selected Redis binding")
	}
	return &RedisEndpoint{body: string(body), digest: digest(body), connection: connection, inode: inode, pid: daemon.PID, port: port}, nil
}
