package main

import (
	"bytes"
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestHostStatusRetainsSuccessfulReportAcrossFailureAndDegradation(t *testing.T) {
	root := t.TempDir()
	log := filepath.Join(root, "run.log")
	write := func(text string) {
		t.Helper()
		if e := os.WriteFile(log, []byte(text), 0600); e != nil {
			t.Fatal(e)
		}
	}
	write(`{"event":"ats_inventory.complete","report":{"data_only":true,"rows":10,"candidate_issues":{}}}` + "\n")
	first, e := recordHostStatus(root, log, 0, "report", "report", 1, 100, 120)
	if e != nil || first["last_attempt_success"] != 1 || first["last_success_unixtime"] != int64(120) {
		t.Fatal("success report", first, e)
	}
	retained := first["report"]
	write("failed before report\n")
	failed, e := recordHostStatus(root, log, 1, "refill", "refill", 5, 130, 140)
	if e != nil || failed["report"] != nil || failed["last_attempt_success"] != 0 || failed["last_success_unixtime"] != int64(120) || !reflect.DeepEqual(failed["last_success_report"], retained) {
		t.Fatal("last success lost", failed, e)
	}
	for _, status := range []string{"rate_limited", "rate_limited_preflight"} {
		write(fmt.Sprintf(`{"event":"ats_inventory.complete","report":{"candidate_issues":{"status":%q}}}`, status))
		degraded, e := recordHostStatus(root, log, 0, "refill", "refill", 5, 150, 160)
		if e != nil || degraded["last_attempt_success"] != 0 || degraded["last_attempt_degraded"] != 1 || degraded["last_success_unixtime"] != int64(120) || !reflect.DeepEqual(degraded["last_success_report"], retained) {
			t.Fatal("rate limit reported successful", e)
		}
	}
	write(`{"event":"ats_inventory.complete","report":{"data_only":true}}`)
	for i := int64(200); i < 240; i++ {
		if _, e = recordHostStatus(root, log, 0, "report", "report", 1, i, i); e != nil {
			t.Fatal(e)
		}
	}
	entries, _ := filepath.Glob(filepath.Join(root, "status/history/*.json"))
	if len(entries) != 32 {
		t.Fatal("history not bounded", len(entries))
	}
	info, e := os.Stat(filepath.Join(root, "status/current.json"))
	if e != nil || info.Mode().Perm() != 0640 {
		t.Fatal("status permissions", e)
	}
	if e = os.WriteFile(log, bytes.Repeat([]byte("x"), hostLogLimit+1), 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = recordHostStatus(root, log, 0, "report", "report", 1, 300, 310); e == nil {
		t.Fatal("unbounded parser accepted")
	}
}

func TestHostOriginalPythonOracles(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_host_tools.json")
	if e != nil {
		t.Fatal(e)
	}
	var fixture struct {
		Source   string `json:"source_reference"`
		Statuses []struct {
			Name              string
			Code              int
			Log               string
			Started, Finished int64
			Expected          map[string]any
		}
		Tails []struct {
			Input  string `json:"input_b64"`
			Output string `json:"output_b64"`
			Limit  int
		}
		Endpoints hostEndpoints `json:"network_endpoints"`
	}
	if json.Unmarshal(raw, &fixture) != nil || fixture.Source != "abd8dd801fe07525bf2fa502b39355b5f4c36e3e" || len(fixture.Statuses) != 9 || len(fixture.Tails) != 5 {
		t.Fatal("original Python fixtures unavailable")
	}
	root := t.TempDir()
	log := filepath.Join(root, "log")
	for _, row := range fixture.Statuses {
		os.WriteFile(log, []byte(row.Log), 0600)
		actual, e := recordHostStatus(root, log, row.Code, "report", "report", 1, row.Started, row.Finished)
		if e != nil {
			t.Fatal(row.Name, e)
		}
		normalized, _ := json.Marshal(actual)
		var decoded map[string]any
		json.Unmarshal(normalized, &decoded)
		if !reflect.DeepEqual(decoded, row.Expected) {
			t.Fatalf("original status transition differs: %s", row.Name)
		}
	}
	for i, row := range fixture.Tails {
		input, _ := base64.StdEncoding.DecodeString(row.Input)
		expected, _ := base64.StdEncoding.DecodeString(row.Output)
		var mirrored bytes.Buffer
		output := filepath.Join(root, fmt.Sprintf("tail-%d", i))
		if e = streamHostTail(bytes.NewReader(input), &mirrored, output, row.Limit); e != nil {
			t.Fatal(e)
		}
		actual, e := os.ReadFile(output)
		if e != nil || !bytes.Equal(actual, expected) || !bytes.Equal(mirrored.Bytes(), input) {
			t.Fatal("original bounded tail differs", i, e)
		}
	}
	env := filepath.Join(root, "env")
	os.WriteFile(env, []byte("LOCAL_DATABASE_URL='postgresql://user:synthetic-password@db.test:5433/jobs'\n"), 0600)
	lookup := func(context.Context, string, string) ([]net.IP, error) {
		return []net.IP{net.ParseIP("10.0.0.9"), net.ParseIP("10.0.0.2"), net.ParseIP("10.0.0.9")}, nil
	}
	endpoints, e := buildHostEndpoints(context.Background(), env, "172.20.0.1", lookup)
	if e != nil || !reflect.DeepEqual(endpoints, fixture.Endpoints) {
		t.Fatal("original endpoint document differs", e)
	}
}

func TestHostBoundedTailMirrorsAndRetainsWholeFinalLines(t *testing.T) {
	for _, input := range []string{"short\n", strings.Repeat("x", 1024), strings.Repeat("old line\n", 300) + "final report\n"} {
		output := filepath.Join(t.TempDir(), "tail")
		var mirror bytes.Buffer
		if e := streamHostTail(strings.NewReader(input), &mirror, output, 1024); e != nil {
			t.Fatal(e)
		}
		tail, e := os.ReadFile(output)
		if e != nil || mirror.String() != input || len(tail) > 1024 {
			t.Fatal("stream changed", e)
		}
		expected := []byte(input)
		if len(expected) > 1024 {
			expected = expected[len(expected)-1024:]
		}
		if len(expected) == 1024 {
			if i := bytes.IndexByte(expected, '\n'); i >= 0 {
				expected = expected[i+1:]
			}
		}
		if !bytes.Equal(tail, expected) {
			t.Fatal("tail differs from original contract")
		}
		info, _ := os.Stat(output)
		if info.Mode().Perm() != 0600 {
			t.Fatal("log permissions")
		}
	}
}

func TestHostSharedLockCreationIsExclusiveAndRefusesSymlinks(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lock")
	if e := createHostLock(path); e != nil {
		t.Fatal(e)
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0600 {
		t.Fatal("lock permissions")
	}
	os.WriteFile(path, []byte("retained"), 0600)
	if e := createHostLock(path); e == nil {
		t.Fatal("existing lock replaced")
	}
	raw, _ := os.ReadFile(path)
	if string(raw) != "retained" {
		t.Fatal("existing lock truncated")
	}
	link := path + "-link"
	os.Symlink(path, link)
	if e := createHostLock(link); e == nil {
		t.Fatal("lock symlink followed")
	}
	missing := path + "-missing"
	os.Remove(link)
	os.Symlink(missing, link)
	if e := createHostLock(link); e == nil {
		t.Fatal("dangling lock symlink followed")
	}
	if _, e := os.Stat(missing); !errors.Is(e, os.ErrNotExist) {
		t.Fatal("symlink target created")
	}
}

func TestHostBoundedLoggerSubprocess(t *testing.T) {
	if os.Getenv("JOBSEEK_HOST_LOGGER_CHILD") == "1" {
		os.Args = []string{"go-ats-inventory", "host-bounded-tee", "--output", os.Getenv("JOBSEEK_HOST_LOGGER_OUTPUT")}
		main()
		return
	}
	executable, e := os.Executable()
	if e != nil {
		t.Fatal(e)
	}
	command := exec.Command(executable, "-test.run=^TestHostBoundedLoggerSubprocess$")
	command.Env = append(os.Environ(), "JOBSEEK_HOST_LOGGER_CHILD=1", "JOBSEEK_HOST_LOGGER_OUTPUT="+filepath.Join(t.TempDir(), "tail"))
	input, e := command.StdinPipe()
	if e != nil {
		t.Fatal(e)
	}
	defer input.Close()
	output, e := command.StdoutPipe()
	if e != nil {
		t.Fatal(e)
	}
	if e = command.Start(); e != nil {
		t.Fatal(e)
	}
	defer command.Process.Kill()
	if _, e = io.WriteString(input, "ready"); e != nil {
		t.Fatal(e)
	}
	ready := make(chan error, 1)
	go func() {
		body := make([]byte, 5)
		_, e := io.ReadFull(output, body)
		if e == nil && string(body) != "ready" {
			e = errors.New("unexpected logger output")
		}
		ready <- e
	}()
	select {
	case e = <-ready:
		if e != nil {
			t.Fatal(e)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("logger did not read pipe")
	}
	if e = command.Process.Signal(syscall.SIGTERM); e != nil {
		t.Fatal(e)
	}
	ended := make(chan error, 1)
	go func() { ended <- command.Wait() }()
	select {
	case e = <-ended:
		exit, ok := e.(*exec.ExitError)
		if !ok {
			t.Fatal("logger did not terminate on signal", e)
		}
		state, ok := exit.Sys().(syscall.WaitStatus)
		if !ok || !state.Signaled() || state.Signal() != syscall.SIGTERM {
			t.Fatal("logger ignored default signal termination", e)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("logger blocked after SIGTERM")
	}
}

func TestHostReportParserRejectsMalformedAndWrongCaseEvents(t *testing.T) {
	path := filepath.Join(t.TempDir(), "log")
	for _, line := range []string{`{"Event":"ats_inventory.complete","report":{}}`, `{"event":"ats_inventory.complete","Report":{}}`, `{"event":"ats_inventory.complete","report":{}} trailing`, `{"event":"ats_inventory.complete","report":[]} `} {
		os.WriteFile(path, []byte(line), 0600)
		report, e := extractHostReport(path)
		if e != nil || report != nil {
			t.Fatal("malformed report accepted", e)
		}
	}
}

func TestHostTokenClaimsSignatureAndBoundedHTTP(t *testing.T) {
	key, e := rsa.GenerateKey(rand.Reader, 2048)
	if e != nil {
		t.Fatal(e)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})
	jwt, e := hostAppJWT("123", keyPEM, 1800000000)
	if e != nil {
		t.Fatal(e)
	}
	parts := strings.Split(jwt, ".")
	if len(parts) != 3 {
		t.Fatal("JWT segments")
	}
	body, _ := base64.RawURLEncoding.DecodeString(parts[1])
	var claims map[string]any
	json.Unmarshal(body, &claims)
	if !reflect.DeepEqual(claims, map[string]any{"iat": float64(1799999940), "exp": float64(1800000540), "iss": "123"}) {
		t.Fatal("JWT claims", claims)
	}
	signature, _ := base64.RawURLEncoding.DecodeString(parts[2])
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if rsa.VerifyPKCS1v15(&key.PublicKey, crypto.SHA256, digest[:], signature) != nil {
		t.Fatal("invalid signature")
	}
	for _, status := range []int{201, 403} {
		client := hostHTTPFake(func(r *http.Request) (*http.Response, error) {
			if r.URL.String() != "https://api.github.com/app/installations/456/access_tokens" || r.Method != "POST" || !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
				t.Fatal("token request contract")
			}
			return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(`{"token":"synthetic-token"}`))}, nil
		})
		token, e := mintHostToken(context.Background(), client, "123", "456", keyPEM, 1800000000)
		if status == 201 && (e != nil || token != "synthetic-token") {
			t.Fatal("token response", e)
		}
		if status == 403 && e == nil {
			t.Fatal("rejected token request accepted")
		}
	}
	path := filepath.Join(t.TempDir(), "credential")
	if e = os.WriteFile(path, []byte("test"), 0600); e != nil {
		t.Fatal(e)
	}
	link := path + "-link"
	os.Symlink(path, link)
	if _, e = hostCredential(link, 64); e == nil {
		t.Fatal("credential symlink accepted")
	}
	if _, e = hostAppJWT("0", keyPEM, 0); e == nil {
		t.Fatal("invalid ID accepted")
	}
	if _, e = hostAppJWT("123", []byte("not a key"), 0); e == nil {
		t.Fatal("invalid key accepted")
	}
}

type hostHTTPFake func(*http.Request) (*http.Response, error)

func (f hostHTTPFake) Do(r *http.Request) (*http.Response, error) { return f(r) }

func TestHostRegistryCopiesOnlyAttestedGenerationAndRejectsDriftBeforePruning(t *testing.T) {
	root := t.TempDir()
	releases := filepath.Join(root, "releases")
	generation := filepath.Join(releases, "release-abc.123")
	data := filepath.Join(generation, "data")
	snapshots := filepath.Join(root, "snapshots")
	os.MkdirAll(data, 0700)
	os.Mkdir(snapshots, 0700)
	files := map[string][]byte{"boards.csv": []byte("company_slug,board_url\nacme,https://acme.test/jobs\n"), "companies.csv": []byte("slug,name\nacme,Acme\n"), "nested/other.csv": []byte("one,two\n")}
	manifest := ""
	for _, name := range []string{"boards.csv", "companies.csv", "nested/other.csv"} {
		os.MkdirAll(filepath.Dir(filepath.Join(data, name)), 0700)
		os.WriteFile(filepath.Join(data, name), files[name], 0600)
		manifest += fmt.Sprintf("%x  %s\n", sha256.Sum256(files[name]), name)
	}
	os.WriteFile(filepath.Join(generation, "data-files.sha256"), []byte(manifest), 0600)
	digest := fmt.Sprintf("%x", sha256.Sum256([]byte(manifest)))
	os.WriteFile(filepath.Join(generation, "release.manifest"), []byte("RELEASE_FORMAT_VERSION=3\nDATA_FILES_SHA256="+digest+"\nDATA_CONTRACT_SHA256="+digest+"\n"), 0600)
	image := "ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("a", 64)
	revision := strings.Repeat("b", 40)
	os.WriteFile(filepath.Join(generation, "success.env"), []byte("CRAWLER_IMAGE_REF="+image+"\nJOBSEEK_DEPLOY_REVISION="+revision+"\n"), 0600)
	pointer := filepath.Join(root, "active")
	os.Symlink(generation, pointer)
	snapshot, e := prepareHostRegistry(pointer, releases, snapshots, image, revision)
	if e != nil {
		t.Fatal(e)
	}
	actual, e := hostRegistryTree(snapshot)
	expected, _ := hostRegistryTree(data)
	if e != nil || !reflect.DeepEqual(actual, expected) {
		t.Fatal("snapshot differs", e)
	}
	os.WriteFile(filepath.Join(data, "boards.csv"), []byte("tampered"), 0600)
	if _, e = prepareHostRegistry(pointer, releases, snapshots, image, revision); e == nil {
		t.Fatal("tamper accepted")
	}
	if !plainHostDirectory(snapshot) {
		t.Fatal("old snapshot pruned before source attested")
	}
	os.WriteFile(filepath.Join(data, "boards.csv"), files["boards.csv"], 0600)
	os.Symlink(filepath.Join(data, "boards.csv"), filepath.Join(data, "extra.csv"))
	if _, e = prepareHostRegistry(pointer, releases, snapshots, image, revision); e == nil {
		t.Fatal("symlink accepted")
	}
	os.Remove(filepath.Join(data, "extra.csv"))
	if _, e = prepareHostRegistry(pointer, releases, snapshots, image, "different"); e == nil {
		t.Fatal("wrong revision accepted")
	}
}

func TestHostNetworkKeepsPrivateTargetsBlockedAndPublicTargetsRequired(t *testing.T) {
	env := filepath.Join(t.TempDir(), "env")
	os.WriteFile(env, []byte("LOCAL_DATABASE_URL='postgresql://user:synthetic-password@db.test:5433/jobs'\n"), 0600)
	lookup := func(context.Context, string, string) ([]net.IP, error) {
		return []net.IP{net.ParseIP("10.0.0.9"), net.ParseIP("10.0.0.2"), net.ParseIP("10.0.0.9")}, nil
	}
	payload, e := buildHostEndpoints(context.Background(), env, "172.20.0.1", lookup)
	if e != nil || len(payload.Required) != 2 || len(payload.Blocked) != 3 || payload.Blocked[1].Host != "10.0.0.2" || payload.Blocked[2].Port != 5433 {
		t.Fatal("endpoint contract", payload, e)
	}
	connect := func(_ context.Context, host string, port int) error {
		if port == 443 {
			return nil
		}
		return errors.New("blocked")
	}
	proof, e := verifyHostEndpoints(context.Background(), payload, connect)
	if e != nil || proof["event"] != "ats_inventory.network_boundary_verified" {
		t.Fatal("boundary proof", e)
	}
	if _, e = verifyHostEndpoints(context.Background(), payload, func(context.Context, string, int) error { return nil }); e == nil {
		t.Fatal("reachable protected endpoint accepted")
	}
	if _, e = verifyHostEndpoints(context.Background(), payload, func(context.Context, string, int) error { return errors.New("blocked") }); e == nil {
		t.Fatal("unreachable public endpoint accepted")
	}
	publicLookup := func(context.Context, string, string) ([]net.IP, error) {
		return []net.IP{net.ParseIP("203.0.113.1")}, nil
	}
	if _, e = buildHostEndpoints(context.Background(), env, "172.20.0.1", publicLookup); e == nil {
		t.Fatal("public database target accepted")
	}
	if _, e = buildHostEndpoints(context.Background(), env, "127.0.0.1", lookup); e == nil {
		t.Fatal("loopback gateway accepted")
	}
}
