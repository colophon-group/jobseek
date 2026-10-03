package releaseevidence

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
)

func runningContainmentFixture(n int, service string) map[string]any {
	c := containerFixture(n, "jobseek", service)
	c["State"] = map[string]any{"Running": true, "Paused": false, "Restarting": false, "Pid": n + 100, "Status": "running"}
	c["HostConfig"].(map[string]any)["RestartPolicy"] = map[string]any{"Name": "always", "MaximumRetryCount": 0}
	return c
}

func containmentFixture(t *testing.T) ([]map[string]any, []*Images) {
	t.Helper()
	postgres := runningContainmentFixture(3, "postgres")
	postgres["Config"].(map[string]any)["Cmd"] = []string{"postgres"}
	postgres["Config"].(map[string]any)["Entrypoint"] = []string{"docker-entrypoint.sh"}
	images := []*Images{fixtureContainerImages(t, map[string]serviceImage{"worker": crawlerContainerImage(), "exporter": crawlerContainerImage(), "postgres": {"postgres:17-alpine@sha256:" + strings.Repeat("d", 64), fixtureImageID}})}
	return []map[string]any{runningContainmentFixture(1, "worker"), runningContainmentFixture(2, "exporter"), postgres, containerFixture(4, "foreign", "oneoff")}, images
}

func fixtureContainmentEffect(t *testing.T, rows []map[string]any, calls *[][]string) containmentEffect {
	t.Helper()
	return func(ctx context.Context, args []string) error {
		*calls = append(*calls, append([]string{}, args...))
		if len(args) == 4 && reflect.DeepEqual(args[:3], []string{"container", "update", "--restart=no"}) {
			for _, row := range rows {
				if row["Id"] == args[3] {
					row["HostConfig"].(map[string]any)["RestartPolicy"] = map[string]any{"Name": "no", "MaximumRetryCount": 0}
					return nil
				}
			}
		} else if len(args) >= 5 && reflect.DeepEqual(args[:4], []string{"container", "stop", "--time", "120"}) {
			for _, id := range args[4:] {
				for _, row := range rows {
					if row["Id"] == id {
						row["State"] = map[string]any{"Running": false, "Paused": false, "Restarting": false, "Pid": 0, "Status": "exited"}
					}
				}
			}
			return nil
		}
		t.Fatal("unexpected mutation command", args)
		return errors.New("fixture")
	}
}

func TestContainmentIncludesExporterAndRequiresActualColdReadback(t *testing.T) {
	rows, images := containmentFixture(t)
	original := observeFixtureContainers(t, rows...)
	plan, err := PlanWriterContainment(context.Background(), original, images, "jobseek")
	if err != nil || !reflect.DeepEqual(plan.TargetIDs(), []string{rows[0]["Id"].(string), rows[1]["Id"].(string)}) {
		t.Fatal("default writers/exporter omitted", err)
	}
	if strings.Contains(plan.Body(), "fixture-sensitive-value") || strings.Contains(plan.Body(), "PASSWORD") || strings.Contains(plan.Body(), "/app/data") {
		t.Fatal("private config disclosed")
	}
	decoded, err := DecodeWriterContainmentPlan([]byte(plan.Body()), plan.SHA256())
	if err != nil || decoded.SHA256() != plan.SHA256() {
		t.Fatal("retained plan retry", err)
	}
	if _, err := RequireColdContainers(context.Background(), original, images); err == nil {
		t.Fatal("plan incorrectly contained actual writers")
	}
	calls := [][]string{}
	checkpoint := false
	observe := func(context.Context) (*Containers, error) { return observeFixtureContainers(t, rows...), nil }
	effect := fixtureContainmentEffect(t, rows, &calls)
	cold, err := containWriters(context.Background(), decoded, images, false, func() error { return nil }, func() error {
		if len(calls) != 2 || !rows[0]["State"].(map[string]any)["Running"].(bool) {
			t.Fatal("stop before durable disabled barrier")
		}
		checkpoint = true
		return nil
	}, observe, func(ctx context.Context, args []string) error {
		if args[1] == "stop" && !checkpoint {
			t.Fatal("no restart barrier")
		}
		return effect(ctx, args)
	})
	if err != nil || cold == nil || len(calls) != 3 || !rows[2]["State"].(map[string]any)["Running"].(bool) {
		t.Fatal("actual containment/infra isolation", err, calls)
	}
	if _, err := containWriters(context.Background(), decoded, images, true, func() error { return nil }, func() error { t.Fatal("barrier rewritten"); return nil }, observe, effect); err != nil || len(calls) != 3 {
		t.Fatal("exact retry changed effects", err)
	}
}

func TestContainmentRejectsForeignLiveUnboundAndUnstableWritersBeforeEffects(t *testing.T) {
	for _, fault := range []string{"foreign running", "foreign restart", "unknown service", "wrong image", "paused", "restarting", "dead", "passive oneoff"} {
		t.Run(fault, func(t *testing.T) {
			rows, images := containmentFixture(t)
			switch fault {
			case "foreign running":
				rows[3]["State"] = rows[0]["State"]
			case "foreign restart":
				rows[3]["HostConfig"] = rows[0]["HostConfig"]
			case "unknown service":
				rows[0]["Config"].(map[string]any)["Labels"].(map[string]any)["com.docker.compose.service"] = "new-consumer"
			case "wrong image":
				rows[0]["Image"] = "sha256:" + strings.Repeat("e", 64)
			case "paused":
				rows[0]["State"].(map[string]any)["Paused"] = true
			case "restarting":
				rows[0]["State"].(map[string]any)["Restarting"] = true
			case "dead":
				rows[0]["State"].(map[string]any)["Status"] = "dead"
			case "passive oneoff":
				rows[2]["Config"].(map[string]any)["Labels"].(map[string]any)["com.docker.compose.oneoff"] = "True"
			}
			p, err := PlanWriterContainment(context.Background(), observeFixtureContainers(t, rows...), images, "jobseek")
			if err == nil || p != nil {
				t.Fatal("unsafe plan admitted", fault)
			}
		})
	}
}

func TestContainmentPreservesAlreadyColdMaintenanceOneoffWithoutAcquiringEffects(t *testing.T) {
	rows, images := containmentFixture(t)
	oneoff := containerFixture(5, "jobseek", "worker")
	oneoff["Config"].(map[string]any)["Labels"].(map[string]any)["com.docker.compose.oneoff"] = "True"
	rows = append(rows, oneoff)
	plan, err := PlanWriterContainment(context.Background(), observeFixtureContainers(t, rows...), images, "jobseek")
	if err != nil || len(plan.TargetIDs()) != 2 {
		t.Fatal("cold maintenance oneoff acquired effects", err)
	}
	calls := [][]string{}
	if _, err := containWriters(context.Background(), plan, images, false, func() error { return nil }, func() error { return nil }, func(context.Context) (*Containers, error) { return observeFixtureContainers(t, rows...), nil }, fixtureContainmentEffect(t, rows, &calls)); err != nil || len(calls) != 3 {
		t.Fatal("cold oneoff prevented exact regular writer containment", err)
	}
}

func TestContainmentRefusesIdentityConfigMountImageAndCheckpointDrift(t *testing.T) {
	for _, fault := range []string{"replacement ID", "new container", "missing container", "config", "mounts", "host field", "image proof", "outside state", "guard", "checkpoint", "restart after barrier", "false successful stop"} {
		t.Run(fault, func(t *testing.T) {
			rows, images := containmentFixture(t)
			plan, err := PlanWriterContainment(context.Background(), observeFixtureContainers(t, rows...), images, "jobseek")
			if err != nil {
				t.Fatal(err)
			}
			guard := func() error { return nil }
			barrier := func() error { return nil }
			disabled := false
			switch fault {
			case "replacement ID":
				rows[0]["Id"] = strings.Repeat("e", 64)
			case "new container":
				rows = append(rows, containerFixture(5, "foreign", "new"))
			case "missing container":
				rows = rows[:3]
			case "config":
				rows[0]["Config"].(map[string]any)["Env"] = []string{"PASSWORD=changed"}
			case "mounts":
				rows[0]["Mounts"] = []any{}
			case "host field":
				rows[0]["HostConfig"].(map[string]any)["PrivateValue"] = "changed"
			case "image proof":
				images = []*Images{fixtureContainerImages(t, map[string]serviceImage{"worker": crawlerContainerImage()})}
			case "outside state":
				rows[3]["State"].(map[string]any)["Status"] = "created"
			case "guard":
				guard = func() error { return errors.New("fixture") }
			case "checkpoint":
				barrier = func() error { return errors.New("fixture") }
			case "restart after barrier":
				disabled = true
			}
			calls := [][]string{}
			effect := fixtureContainmentEffect(t, rows, &calls)
			if fault == "false successful stop" {
				real := effect
				effect = func(ctx context.Context, args []string) error {
					if args[1] == "stop" {
						return nil
					}
					return real(ctx, args)
				}
			}
			cold, err := containWriters(context.Background(), plan, images, disabled, guard, barrier, func(context.Context) (*Containers, error) { return observeFixtureContainers(t, rows...), nil }, effect)
			if err == nil || cold != nil {
				t.Fatal("unsafe containment admitted", fault)
			}
			if fault != "checkpoint" && fault != "false successful stop" && len(calls) != 0 {
				t.Fatal("effect before drift refusal", fault, calls)
			}
			if fault == "checkpoint" && len(calls) != 2 {
				t.Fatal("stop despite failed barrier", calls)
			}
		})
	}
}

func TestContainmentRetriesPartialRestartEffectsButNeverAdoptsReplacement(t *testing.T) {
	rows, images := containmentFixture(t)
	plan, err := PlanWriterContainment(context.Background(), observeFixtureContainers(t, rows...), images, "jobseek")
	if err != nil {
		t.Fatal(err)
	}
	calls := [][]string{}
	effect := fixtureContainmentEffect(t, rows, &calls)
	observe := func(context.Context) (*Containers, error) { return observeFixtureContainers(t, rows...), nil }
	_, err = containWriters(context.Background(), plan, images, false, func() error { return nil }, func() error { return nil }, observe, func(ctx context.Context, args []string) error {
		if args[len(args)-1] == rows[1]["Id"] {
			return errors.New("crash fixture")
		}
		return effect(ctx, args)
	})
	if err == nil || len(calls) != 1 || !rows[0]["State"].(map[string]any)["Running"].(bool) {
		t.Fatal("partial failure restarted/stopped", err)
	}
	if _, err := containWriters(context.Background(), plan, images, false, func() error { return nil }, func() error { return nil }, observe, effect); err != nil || len(calls) != 3 {
		t.Fatal("partial retry failed", err, calls)
	}
	rows[0]["Id"] = strings.Repeat("e", 64)
	if _, err := containWriters(context.Background(), plan, images, true, func() error { return nil }, func() error { return nil }, observe, effect); err == nil || len(calls) != 3 {
		t.Fatal("replacement adopted")
	}
}

func TestContainmentRetainedPlanRequiresCanonicalExactInventory(t *testing.T) {
	rows, images := containmentFixture(t)
	plan, err := PlanWriterContainment(context.Background(), observeFixtureContainers(t, rows...), images, "jobseek")
	if err != nil {
		t.Fatal(err)
	}
	for _, fault := range []string{"hash", "unknown", "duplicate", "case", "trailer", "omitted row", "membership"} {
		t.Run(fault, func(t *testing.T) {
			b := []byte(plan.Body())
			hash := plan.SHA256()
			switch fault {
			case "hash":
				hash = strings.Repeat("e", 64)
			case "unknown":
				b = []byte(strings.Replace(string(b), "{", `{"secret":"hidden",`, 1))
			case "duplicate":
				b = []byte(strings.Replace(string(b), "{", `{"project":"other",`, 1))
			case "case":
				b = []byte(strings.Replace(string(b), `"project"`, `"Project"`, 1))
			case "trailer":
				b = append(b, '\n')
			case "omitted row":
				var d containmentDocument
				json.Unmarshal(b, &d)
				d.Containers = d.Containers[:3]
				b, _ = json.Marshal(d)
			case "membership":
				var d containmentDocument
				json.Unmarshal(b, &d)
				d.Containers[0].Target = false
				b, _ = json.Marshal(d)
			}
			if fault != "hash" {
				hash = digest(b)
			}
			p, err := DecodeWriterContainmentPlan(b, hash)
			if fault == "membership" {
				if err != nil || p.verify(context.Background(), observeFixtureContainers(t, rows...), images, false) == nil {
					t.Fatal("target membership not independently checked")
				}
			} else if err == nil || p != nil {
				t.Fatal("invalid retained plan admitted", fault)
			}
		})
	}
}
