package releaseevidence

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

const fixtureImageID = "sha256:1111111111111111111111111111111111111111111111111111111111111111"

type imageFixture struct {
	g       generation
	c       ImageObservationConfig
	compose []byte
	inspect []byte
}

func imageSetup(t *testing.T, override bool) imageFixture {
	t.Helper()
	g := fixture(t)
	if override {
		g = bridgeFixture(t, "2", true)
	}
	f := verify(t, g)
	c := ImageObservationConfig{g.directory, fixtureOwner, "jobseek", f.SHA256(), "amd64"}
	ref := "ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("c", 64)
	compose := []byte(`{"name":"jobseek","services":{"worker-1":{"image":"` + ref + `","environment":{"LOCAL_DATABASE_URL":"fixture-sensitive-value"}},"exporter":{"image":"` + ref + `"}}}`)
	inspect := []byte(`[{"Id":"` + fixtureImageID + `","RepoDigests":["` + ref + `"],"Architecture":"amd64","Os":"linux","Config":{"Env":["PASSWORD=fixture-sensitive-value"]}}]`)
	return imageFixture{g, c, compose, inspect}
}

func (f imageFixture) reader(t *testing.T) dockerRead {
	t.Helper()
	return func(ctx context.Context, args []string) ([]byte, error) {
		t.Helper()
		if _, ok := ctx.Deadline(); !ok {
			t.Fatal("unbounded command context")
		}
		expected := []string{"compose", "--project-name", "jobseek", "--project-directory", f.g.directory, "--env-file", filepath.Join(f.g.directory, "environment.env"), "-f", filepath.Join(f.g.directory, "docker-compose.yml")}
		if f.g.fields["HAS_IMAGE_OVERRIDE"] == "1" {
			expected = append(expected, "-f", filepath.Join(f.g.directory, "rollback-images.override.yml"))
		}
		expected = append(expected, "config", "--format", "json")
		if reflect.DeepEqual(expected, args) {
			return f.compose, nil
		}
		if reflect.DeepEqual([]string{"image", "inspect", "ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("c", 64)}, args) {
			return f.inspect, nil
		}
		t.Fatal("unexpected Docker operation or argument", args)
		return nil, errors.New("unexpected command")
	}
}

func TestImageObservationsBindFilesAndExcludeCredentialOutput(t *testing.T) {
	for _, override := range []bool{false, true} {
		f := imageSetup(t, override)
		calls := 0
		r := f.reader(t)
		run := func(ctx context.Context, args []string) ([]byte, error) { calls++; return r(ctx, args) }
		got, err := observeImages(context.Background(), f.c, run)
		if err != nil || calls != 4 || got.SHA256() != digest([]byte(got.Body())) {
			t.Fatal("independent four-command observation failed", err, calls)
		}
		if strings.Contains(got.Body(), "fixture-sensitive-value") || strings.Contains(got.Body(), f.g.directory) || !strings.Contains(got.Body(), fixtureImageID) || !strings.Contains(got.Body(), f.c.FileEvidenceSHA256) {
			t.Fatal("credential exposure or missing binding")
		}
		var d imageDocument
		if json.Unmarshal([]byte(got.Body()), &d) != nil || len(d.Services) != 2 || d.Services["exporter"].ImageID != fixtureImageID {
			t.Fatal("service inventory omitted exporter")
		}
	}
}

func TestImageObservationRefusesDriftBeforeAdmission(t *testing.T) {
	tests := map[string]func(*imageFixture){
		"wrong file hash":          func(f *imageFixture) { f.c.FileEvidenceSHA256 = strings.Repeat("e", 64) },
		"wrong architecture input": func(f *imageFixture) { f.c.Architecture = "x86" },
		"project flag":             func(f *imageFixture) { f.c.Project = "--context" },
		"project differs": func(f *imageFixture) {
			f.compose = []byte(strings.Replace(string(f.compose), `"name":"jobseek"`, `"name":"other"`, 1))
		},
		"mutable image": func(f *imageFixture) {
			f.compose = []byte(`{"name":"jobseek","services":{"worker":{"image":"redis:latest"}}}`)
		},
		"runtime digest differs": func(f *imageFixture) {
			f.compose = []byte(strings.ReplaceAll(string(f.compose), strings.Repeat("c", 64), strings.Repeat("d", 64)))
		},
		"build supplied": func(f *imageFixture) {
			f.compose = []byte(strings.Replace(string(f.compose), `"environment":`, `"build":{"context":"."},"environment":`, 1))
		},
		"duplicate service": func(f *imageFixture) { f.compose = []byte(`{"name":"jobseek","services":{"worker":{},"worker":{}}}`) },
		"duplicate unused credential": func(f *imageFixture) {
			f.compose = []byte(strings.Replace(string(f.compose), `"LOCAL_DATABASE_URL":"fixture-sensitive-value"`, `"secret":"a","secret":"b"`, 1))
		},
		"trailing JSON":         func(f *imageFixture) { f.compose = append(f.compose, []byte(`{}`)...) },
		"too large":             func(f *imageFixture) { f.compose = []byte(strings.Repeat(" ", (8<<20)+1)) },
		"empty services":        func(f *imageFixture) { f.compose = []byte(`{"name":"jobseek","services":{}}`) },
		"service without image": func(f *imageFixture) { f.compose = []byte(`{"name":"jobseek","services":{"worker":{}}}`) },
		"null services":         func(f *imageFixture) { f.compose = []byte(`{"name":"jobseek","services":null}`) },
		"wrong platform":        func(f *imageFixture) { f.inspect = []byte(strings.ReplaceAll(string(f.inspect), "amd64", "arm64")) },
		"wrong OS":              func(f *imageFixture) { f.inspect = []byte(strings.ReplaceAll(string(f.inspect), "linux", "windows")) },
		"tag as image ID": func(f *imageFixture) {
			f.inspect = []byte(strings.ReplaceAll(string(f.inspect), fixtureImageID, "redis:latest"))
		},
		"wrong repo digest": func(f *imageFixture) {
			f.inspect = []byte(strings.ReplaceAll(string(f.inspect), strings.Repeat("c", 64), strings.Repeat("d", 64)))
		},
		"no images": func(f *imageFixture) { f.inspect = []byte(`[]`) },
		"duplicate image key": func(f *imageFixture) {
			f.inspect = []byte(strings.Replace(string(f.inspect), `"Architecture":"amd64"`, `"Architecture":"arm64","Architecture":"amd64"`, 1))
		},
		"unsafe service name": func(f *imageFixture) {
			f.compose = []byte(strings.ReplaceAll(string(f.compose), "worker-1", "fixture-sensitive-value\n"))
		},
	}
	for name, change := range tests {
		t.Run(name, func(t *testing.T) {
			f := imageSetup(t, false)
			change(&f)
			_, err := observeImages(context.Background(), f.c, f.reader(t))
			if !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "fixture-sensitive-value") || strings.Contains(err.Error(), f.g.directory) {
				t.Fatal("missing rejection or leaked diagnostic", err)
			}
		})
	}
}

func TestImageObservationReadbackAndCommandFailureContainment(t *testing.T) {
	for _, phase := range []string{"compose failed", "inspect failed", "compose drift", "image drift", "files drift", "cancelled"} {
		t.Run(phase, func(t *testing.T) {
			f := imageSetup(t, false)
			calls := 0
			r := f.reader(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			run := func(ctx context.Context, args []string) ([]byte, error) {
				calls++
				b, err := r(ctx, args)
				if (phase == "compose failed" && calls == 1) || (phase == "inspect failed" && calls == 2) {
					return nil, errors.New("fixture-sensitive-value")
				}
				if (phase == "compose drift" && calls == 3) || (phase == "image drift" && calls == 4) {
					return append(append([]byte{}, b...), ' '), nil
				}
				if phase == "files drift" && calls == 4 {
					write(t, f.g, "data/companies/acme.csv", []byte("changed\n"))
				}
				if phase == "cancelled" && calls == 4 {
					cancel()
				}
				return b, err
			}
			_, err := observeImages(ctx, f.c, run)
			if !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "fixture-sensitive-value") {
				t.Fatal("unsafe command/readback rejection", err)
			}
		})
	}
}

func TestTransitiveBridgeShimCannotResolveAnotherDigest(t *testing.T) {
	f := imageSetup(t, true)
	// The current transitive shim is f...; using the historical e... shim must
	// refuse even though both references are immutable and owner-scoped.
	f.compose = []byte(`{"name":"jobseek","services":{"murmur":{"image":"ghcr.io/colophon-group/jobseek-murmur-shim@sha256:` + strings.Repeat("e", 64) + `"}}}`)
	if _, err := observeImages(context.Background(), f.c, f.reader(t)); !errors.Is(err, ErrInvalid) {
		t.Fatal("Compose lost the transitive current shim binding", err)
	}
}

func TestDistinctImageInspectionOrderAndServiceMapping(t *testing.T) {
	f := imageSetup(t, false)
	crawler := "ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("c", 64)
	browser := "ghcr.io/colophon-group/jobseek-crawler-browser@sha256:" + strings.Repeat("d", 64)
	browserID := "sha256:" + strings.Repeat("2", 64)
	f.compose = []byte(`{"name":"jobseek","services":{"worker-1":{"image":"` + crawler + `"},"browser-1":{"image":"` + browser + `"}}}`)
	installed := []inspectImage{{browserID, []string{browser}, "amd64", "linux"}, {fixtureImageID, []string{crawler}, "amd64", "linux"}}
	b, err := json.Marshal(installed)
	if err != nil {
		t.Fatal(err)
	}
	read := f.reader(t)
	run := func(ctx context.Context, args []string) ([]byte, error) {
		if args[0] == "image" {
			if !reflect.DeepEqual(args, []string{"image", "inspect", browser, crawler}) {
				t.Fatal("image arguments not sorted")
			}
			return b, nil
		}
		return read(ctx, args)
	}
	got, err := observeImages(context.Background(), f.c, run)
	if err != nil {
		t.Fatal(err)
	}
	var d imageDocument
	if json.Unmarshal([]byte(got.Body()), &d) != nil || d.Services["browser-1"].ImageID != browserID || d.Services["worker-1"].ImageID != fixtureImageID {
		t.Fatal("distinct installed images mapped to wrong services")
	}
	installed[0], installed[1] = installed[1], installed[0]
	b, err = json.Marshal(installed)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := observeImages(context.Background(), f.c, run); !errors.Is(err, ErrInvalid) {
		t.Fatal("inspect ordering drift admitted")
	}
}

func TestDockerOutputBoundAndJSONDepth(t *testing.T) {
	var b boundedOutput
	if _, err := b.Write(make([]byte, 8<<20)); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Write([]byte{0}); !errors.Is(err, ErrInvalid) {
		t.Fatal("unbounded output")
	}
	var copied boundedOutput
	// Match os/exec's pipe drain: io.Copy must not bypass the Write bound via
	// a promoted bytes.Buffer.ReadFrom method.
	if _, err := io.Copy(&copied, io.LimitReader(strings.NewReader(strings.Repeat("x", (8<<20)+1)), (8<<20)+1)); !errors.Is(err, ErrInvalid) {
		t.Fatal("pipe copying bypassed the output bound")
	}
	if uniqueJSON([]byte(strings.Repeat("[", 66)+"0"+strings.Repeat("]", 66))) == nil {
		t.Fatal("unbounded depth")
	}
	if uniqueJSON([]byte{0xff}) == nil {
		t.Fatal("invalid UTF-8 observation")
	}
}

func TestDockerReadCommandHasFixedDaemonEnvironmentAndBoundedWait(t *testing.T) {
	t.Setenv("DOCKER_HOST", "tcp://fixture-sensitive-value")
	t.Setenv("DOCKER_CONTEXT", "caller-context")
	t.Setenv("COMPOSE_FILE", "caller-compose")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	cmd := dockerReadCommand(ctx, []string{"image", "inspect", fixtureImageID})
	if cmd.Path != "/usr/bin/docker" || cmd.Dir != "/" || cmd.WaitDelay == 0 || !reflect.DeepEqual(cmd.Env, []string{"PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/", "DOCKER_CONFIG=/var/empty/jobseek-docker-read", "DOCKER_HOST=unix:///var/run/docker.sock"}) {
		t.Fatal("Docker command inherited caller authority")
	}
	// Deliberately never Run/Start: local agents must not access a Docker socket.
}
