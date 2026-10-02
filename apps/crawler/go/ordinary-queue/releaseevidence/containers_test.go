package releaseevidence

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"sort"
	"strings"
	"testing"
)

func containerFixture(n int, project, service string) map[string]any {
	return map[string]any{
		"Id": fmt.Sprintf("%064x", n), "Image": fixtureImageID,
		"State":      map[string]any{"Running": false, "Paused": false, "Restarting": false, "Pid": 0, "Status": "exited"},
		"Config":     map[string]any{"Labels": map[string]any{"com.docker.compose.project": project, "com.docker.compose.service": service, "com.docker.compose.oneoff": "False"}, "Cmd": []string{"crawler", "run"}, "Entrypoint": []string{"/app/crawler"}, "Env": []string{"PASSWORD=fixture-sensitive-value"}},
		"HostConfig": map[string]any{"RestartPolicy": map[string]any{"Name": "no", "MaximumRetryCount": 0}, "PrivateValue": "fixture-sensitive-value"},
		"Mounts":     []any{map[string]any{"Source": "/private/fixture-sensitive-value", "Destination": "/app/data"}},
	}
}

func containerReader(t *testing.T, containers ...map[string]any) dockerRead {
	t.Helper()
	byID := map[string]map[string]any{}
	ids := []string{}
	for _, c := range containers {
		id := c["Id"].(string)
		ids = append(ids, id)
		byID[id] = c
	}
	sort.Sort(sort.Reverse(sort.StringSlice(ids))) // daemon order is not trusted
	return func(ctx context.Context, args []string) ([]byte, error) {
		t.Helper()
		if _, ok := ctx.Deadline(); !ok {
			t.Fatal("unbounded observation")
		}
		if reflect.DeepEqual(args, []string{"ps", "--all", "--no-trunc", "--format", "{{.ID}}"}) {
			if len(ids) == 0 {
				return nil, nil
			}
			return []byte(strings.Join(ids, "\n") + "\n"), nil
		}
		if len(args) < 3 || args[0] != "container" || args[1] != "inspect" || len(args) > 34 || !sort.StringsAreSorted(args[2:]) {
			t.Fatal("unexpected command or unordered/unbounded batch", args)
		}
		rows := []map[string]any{}
		for _, id := range args[2:] {
			if byID[id] == nil {
				t.Fatal("unlisted inspected ID")
			}
			rows = append(rows, byID[id])
		}
		return json.Marshal(rows)
	}
}

func observeFixtureContainers(t *testing.T, containers ...map[string]any) *Containers {
	t.Helper()
	got, err := observeContainers(context.Background(), containerReader(t, containers...))
	if err != nil {
		t.Fatal(err)
	}
	return got
}

func fixtureContainerImages(t *testing.T, services map[string]serviceImage) *Images {
	t.Helper()
	b, err := json.Marshal(imageDocument{Version: "jobseek.crawler-release-images/v1", FileEvidenceSHA256: strings.Repeat("a", 64), ComposeSHA256: strings.Repeat("b", 64), Project: "jobseek", Architecture: "amd64", Services: services})
	if err != nil {
		t.Fatal(err)
	}
	return &Images{body: string(b), digest: digest(b)}
}

func crawlerContainerImage() serviceImage {
	return serviceImage{"ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("c", 64), fixtureImageID}
}

func TestContainerInventoryIncludesWholeDaemonAndBoundsInspection(t *testing.T) {
	for _, count := range []int{0, 1, 33, 256} {
		t.Run(fmt.Sprint(count), func(t *testing.T) {
			rows := []map[string]any{}
			for n := 1; n <= count; n++ {
				rows = append(rows, containerFixture(n, "other-project", "unaccounted-oneoff"))
			}
			calls := 0
			r := containerReader(t, rows...)
			got, err := observeContainers(context.Background(), func(ctx context.Context, args []string) ([]byte, error) { calls++; return r(ctx, args) })
			if err != nil || len(got.rows) != count || calls != 4+2*((count+31)/32) || got.SHA256() != digest([]byte(got.Body())) {
				t.Fatal("incomplete global inventory", err, calls)
			}
			if strings.Contains(got.Body(), "fixture-sensitive-value") || strings.Contains(got.Body(), "PASSWORD") || strings.Contains(got.Body(), "/app/data") {
				t.Fatal("raw container configuration disclosed")
			}
			for _, row := range got.rows {
				if !shaPattern.MatchString(row.ConfigSHA256) || !shaPattern.MatchString(row.HostConfigSHA256) || !shaPattern.MatchString(row.MountsSHA256) {
					t.Fatal("configuration binding omitted")
				}
			}
		})
	}
}

func TestContainerInventoryRejectsIncompleteOrMalformedObservations(t *testing.T) {
	changes := map[string]func(map[string]any){
		"image tag":          func(c map[string]any) { c["Image"] = "crawler:latest" },
		"missing running":    func(c map[string]any) { delete(c["State"].(map[string]any), "Running") },
		"missing paused":     func(c map[string]any) { delete(c["State"].(map[string]any), "Paused") },
		"missing restarting": func(c map[string]any) { delete(c["State"].(map[string]any), "Restarting") },
		"missing pid":        func(c map[string]any) { delete(c["State"].(map[string]any), "Pid") },
		"negative pid":       func(c map[string]any) { c["State"].(map[string]any)["Pid"] = -1 },
		"unknown state":      func(c map[string]any) { c["State"].(map[string]any)["Status"] = "unknown" },
		"missing config":     func(c map[string]any) { delete(c, "Config") },
		"null hostconfig":    func(c map[string]any) { c["HostConfig"] = nil },
		"null mounts":        func(c map[string]any) { c["Mounts"] = nil },
		"missing policy":     func(c map[string]any) { delete(c["HostConfig"].(map[string]any), "RestartPolicy") },
		"missing retries": func(c map[string]any) {
			delete(c["HostConfig"].(map[string]any)["RestartPolicy"].(map[string]any), "MaximumRetryCount")
		},
		"negative retries": func(c map[string]any) {
			c["HostConfig"].(map[string]any)["RestartPolicy"].(map[string]any)["MaximumRetryCount"] = -1
		},
		"unknown policy": func(c map[string]any) {
			c["HostConfig"].(map[string]any)["RestartPolicy"].(map[string]any)["Name"] = "sometimes"
		},
		"project flag": func(c map[string]any) {
			c["Config"].(map[string]any)["Labels"].(map[string]any)["com.docker.compose.project"] = "--context"
		},
		"ambiguous oneoff": func(c map[string]any) {
			c["Config"].(map[string]any)["Labels"].(map[string]any)["com.docker.compose.oneoff"] = "yes"
		},
		"oversized command": func(c map[string]any) { c["Config"].(map[string]any)["Cmd"] = make([]string, 257) },
		"oversized mounts":  func(c map[string]any) { c["Mounts"] = make([]any, 257) },
	}
	for name, change := range changes {
		t.Run(name, func(t *testing.T) {
			c := containerFixture(1, "jobseek", "worker")
			change(c)
			_, err := observeContainers(context.Background(), containerReader(t, c))
			if !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "fixture-sensitive-value") {
				t.Fatal("incomplete evidence accepted or credentials disclosed", err)
			}
		})
	}
	for _, phase := range []string{"duplicate ID", "short ID", "257 IDs", "command failure", "duplicate JSON", "trailing JSON", "missing row", "wrong row", "enumeration drift", "state drift", "config drift", "mount drift", "cancelled"} {
		t.Run(phase, func(t *testing.T) {
			c := containerFixture(1, "jobseek", "exporter")
			r := containerReader(t, c)
			calls := 0
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			_, err := observeContainers(ctx, func(ctx context.Context, args []string) ([]byte, error) {
				calls++
				b, err := r(ctx, args)
				switch phase {
				case "duplicate ID":
					if calls == 1 {
						b = append(b, b...)
					}
				case "short ID":
					if calls == 1 {
						b = []byte("1234\n")
					}
				case "257 IDs":
					if calls == 1 {
						b = []byte(strings.Repeat(string(b), 257))
					}
				case "command failure":
					return nil, errors.New("fixture-sensitive-value")
				case "duplicate JSON":
					if calls == 2 {
						b = []byte(strings.Replace(string(b), `"Image":`, `"Image":"ignored","Image":`, 1))
					}
				case "trailing JSON":
					if calls == 2 {
						b = append(b, []byte(`{}`)...)
					}
				case "missing row":
					if calls == 2 {
						b = []byte(`[]`)
					}
				case "wrong row":
					if calls == 2 {
						b = []byte(strings.Replace(string(b), c["Id"].(string), fmt.Sprintf("%064x", 2), 1))
					}
				case "enumeration drift":
					if calls == 3 {
						b = nil
					}
				case "state drift":
					if calls == 5 {
						b = []byte(strings.Replace(string(b), `"Running":false`, `"Running":true`, 1))
					}
				case "config drift":
					if calls == 5 {
						b = []byte(strings.Replace(string(b), "PASSWORD=", "NEW_PASSWORD=", 1))
					}
				case "mount drift":
					if calls == 5 {
						b = []byte(strings.Replace(string(b), "/app/data", "/app/other", 1))
					}
				case "cancelled":
					if calls == 6 {
						cancel()
					}
				}
				return b, err
			})
			if !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "fixture-sensitive-value") {
				t.Fatal("unsafe observation accepted", err)
			}
		})
	}
}

func TestColdContainerPredicateContainsAllWritersAndForeignOneoffs(t *testing.T) {
	services := map[string]serviceImage{}
	rows := []map[string]any{}
	for n, service := range []string{"worker", "exporter", "browser", "lightpanda-b0", "go-ordinary-worker", "new-runtime-consumer"} {
		services[service] = crawlerContainerImage()
		rows = append(rows, containerFixture(n+1, "jobseek", service))
	}
	rows = append(rows, containerFixture(20, "foreign", "oneoff"))
	rows[len(rows)-1]["Config"].(map[string]any)["Labels"].(map[string]any)["com.docker.compose.oneoff"] = "True"
	images := []*Images{fixtureContainerImages(t, services)}
	got, err := RequireColdContainers(context.Background(), observeFixtureContainers(t, rows...), images)
	if err != nil || got.SHA256() != digest([]byte(got.Body())) || !strings.Contains(got.Body(), `"contained_containers":6`) || !strings.Contains(got.Body(), `"unaccounted_stopped_containers":1`) {
		t.Fatal("complete containment failed", err)
	}
	for _, service := range []string{"exporter", "new-runtime-consumer", "foreign-oneoff"} {
		for _, mutation := range []string{"running", "paused", "restarting", "pid", "dead", "always", "unless-stopped", "on-failure", "retry-count"} {
			t.Run(service+"/"+mutation, func(t *testing.T) {
				project := "jobseek"
				if service == "foreign-oneoff" {
					project = "foreign"
				}
				c := containerFixture(1, project, service)
				s := c["State"].(map[string]any)
				p := c["HostConfig"].(map[string]any)["RestartPolicy"].(map[string]any)
				switch mutation {
				case "running":
					s["Running"], s["Status"], s["Pid"] = true, "running", 123
				case "paused":
					s["Paused"] = true
				case "restarting":
					s["Restarting"] = true
				case "pid":
					s["Pid"] = 123
				case "dead":
					s["Status"] = "dead"
				case "retry-count":
					p["MaximumRetryCount"] = 1
				default:
					p["Name"] = mutation
				}
				if _, err := RequireColdContainers(context.Background(), observeFixtureContainers(t, c), images); !errors.Is(err, ErrInvalid) {
					t.Fatal("writer or oneoff allowed to run/restart", err)
				}
			})
		}
	}
}

func TestColdContainerPredicateAuthenticatesInfrastructureAndStagedImages(t *testing.T) {
	for _, service := range []string{"redis", "postgres", "alloy"} {
		t.Run(service, func(t *testing.T) {
			c := containerFixture(1, "jobseek", service)
			config := c["Config"].(map[string]any)
			repo, cmd, entry := service, service+"-server", "docker-entrypoint.sh"
			if service == "postgres" {
				cmd = "postgres"
			}
			if service == "alloy" {
				repo, cmd, entry = "grafana/alloy", "run", "/bin/alloy"
			}
			config["Cmd"], config["Entrypoint"] = []string{cmd}, []string{entry}
			c["State"] = map[string]any{"Running": true, "Paused": false, "Restarting": false, "Pid": 123, "Status": "running"}
			image := serviceImage{repo + "@sha256:" + strings.Repeat("c", 64), fixtureImageID}
			proof := fixtureContainerImages(t, map[string]serviceImage{service: image})
			if _, err := RequireColdContainers(context.Background(), observeFixtureContainers(t, c), []*Images{proof}); err != nil {
				t.Fatal(err)
			}
			for _, drift := range []string{"wrong command", "wrong entrypoint", "oneoff", "missing oneoff label", "wrong repository", "wrong image", "paused", "stopped", "unknown service"} {
				t.Run(drift, func(t *testing.T) {
					b, _ := json.Marshal(c)
					var changed map[string]any
					_ = json.Unmarshal(b, &changed)
					config := changed["Config"].(map[string]any)
					i := image
					switch drift {
					case "wrong command":
						config["Cmd"] = []string{"sh", "-c", "crawler run"}
					case "wrong entrypoint":
						config["Entrypoint"] = []string{"/app/writer"}
					case "oneoff":
						config["Labels"].(map[string]any)["com.docker.compose.oneoff"] = "True"
					case "missing oneoff label":
						delete(config["Labels"].(map[string]any), "com.docker.compose.oneoff")
					case "wrong repository":
						i.Reference = crawlerContainerImage().Reference
					case "wrong image":
						changed["Image"] = "sha256:" + strings.Repeat("2", 64)
					case "paused":
						changed["State"].(map[string]any)["Paused"] = true
					case "stopped":
						changed["State"].(map[string]any)["Running"] = false
					case "unknown service":
						config["Labels"].(map[string]any)["com.docker.compose.service"] = "omitted-exporter"
					}
					if _, err := RequireColdContainers(context.Background(), observeFixtureContainers(t, changed), []*Images{fixtureContainerImages(t, map[string]serviceImage{service: i})}); !errors.Is(err, ErrInvalid) {
						t.Fatal("infrastructure alias admitted", err)
					}
				})
			}
		})
	}
	old := crawlerContainerImage()
	newImage := serviceImage{strings.ReplaceAll(old.Reference, strings.Repeat("c", 64), strings.Repeat("d", 64)), "sha256:" + strings.Repeat("2", 64)}
	a, b := fixtureContainerImages(t, map[string]serviceImage{"worker": old}), fixtureContainerImages(t, map[string]serviceImage{"worker": newImage})
	c := containerFixture(1, "jobseek", "worker")
	c["Image"] = newImage.ImageID
	if _, err := RequireColdContainers(context.Background(), observeFixtureContainers(t, c), []*Images{a, b}); err != nil {
		t.Fatal("staged exact old/new image binding refused", err)
	}
	if _, err := RequireColdContainers(context.Background(), observeFixtureContainers(t, c), []*Images{a}); !errors.Is(err, ErrInvalid) {
		t.Fatal("unknown stopped project image admitted", err)
	}
}

func TestColdContainerPredicateRequiresOpaqueBoundEvidenceAndLiveContext(t *testing.T) {
	inventory := observeFixtureContainers(t)
	image := fixtureContainerImages(t, map[string]serviceImage{"worker": crawlerContainerImage()})
	for _, proofs := range [][]*Images{nil, {nil}, {image, image}, make([]*Images, 13), {&Images{}}, {&Images{body: image.Body(), digest: strings.Repeat("a", 64)}}} {
		if _, err := RequireColdContainers(context.Background(), inventory, proofs); !errors.Is(err, ErrInvalid) {
			t.Fatal("incomplete evidence admitted", err)
		}
	}
	for _, inv := range []*Containers{nil, {}, {body: inventory.Body(), digest: strings.Repeat("a", 64)}} {
		if _, err := RequireColdContainers(context.Background(), inv, []*Images{image}); !errors.Is(err, ErrInvalid) {
			t.Fatal("invalid inventory admitted", err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := RequireColdContainers(ctx, inventory, []*Images{image}); !errors.Is(err, ErrInvalid) {
		t.Fatal("cancelled predicate admitted", err)
	}
	if _, err := observeContainers(ctx, containerReader(t)); !errors.Is(err, ErrInvalid) {
		t.Fatal("cancelled observation admitted", err)
	}
	if _, err := observeContainers(nil, containerReader(t)); !errors.Is(err, ErrInvalid) {
		t.Fatal("nil context admitted", err)
	}
	if _, err := observeContainers(context.Background(), nil); !errors.Is(err, ErrInvalid) {
		t.Fatal("nil command admitted", err)
	}
	if _, err := RequireColdContainers(nil, inventory, []*Images{image}); !errors.Is(err, ErrInvalid) {
		t.Fatal("nil predicate context admitted", err)
	}
	for _, change := range []func(*imageDocument){
		func(d *imageDocument) { d.Version = "unknown" },
		func(d *imageDocument) { d.Architecture = "x86" },
		func(d *imageDocument) { d.Project = "--context" },
		func(d *imageDocument) { d.ComposeSHA256 = "missing" },
		func(d *imageDocument) { d.FileEvidenceSHA256 = "missing" },
		func(d *imageDocument) { d.Services = nil },
		func(d *imageDocument) { d.Services["worker"] = serviceImage{"crawler:latest", fixtureImageID} },
	} {
		var d imageDocument
		_ = json.Unmarshal([]byte(image.Body()), &d)
		change(&d)
		body, _ := json.Marshal(d)
		bad := &Images{body: string(body), digest: digest(body)}
		if _, err := RequireColdContainers(context.Background(), inventory, []*Images{bad}); !errors.Is(err, ErrInvalid) {
			t.Fatal("malformed bound image evidence admitted", err)
		}
	}
	var differentArch imageDocument
	_ = json.Unmarshal([]byte(image.Body()), &differentArch)
	differentArch.Architecture = "arm64"
	body, _ := json.Marshal(differentArch)
	if _, err := RequireColdContainers(context.Background(), inventory, []*Images{image, {body: string(body), digest: digest(body)}}); !errors.Is(err, ErrInvalid) {
		t.Fatal("different host architecture evidence admitted", err)
	}
}

func TestContainerInventoryIgnoresVolatileHealthLogsWithoutOmittingState(t *testing.T) {
	c := containerFixture(1, "jobseek", "exporter")
	r := containerReader(t, c)
	calls := 0
	got, err := observeContainers(context.Background(), func(ctx context.Context, args []string) ([]byte, error) {
		calls++
		if calls == 5 {
			c["State"].(map[string]any)["Health"] = map[string]any{"Status": "healthy", "Log": []string{"fixture-sensitive-value"}}
		}
		return r(ctx, args)
	})
	if err != nil || strings.Contains(got.Body(), "fixture-sensitive-value") || got.rows[0].Status != "exited" {
		t.Fatal("health log drift confused process-state predicate or disclosed raw logs", err)
	}
}
