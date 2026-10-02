package releaseevidence

import (
	"context"
	"encoding/json"
	"regexp"
	"sort"
	"strings"
	"time"
)

// Containers is a complete bounded daemon inventory, not a host cutover grant.
// Raw inspect JSON contains credentials and stays in memory. The trusted host
// coordinator must hold the shared mutation lock and independently validate
// source/binary/mount/user contracts plus SQL barriers/leases before admission.
type Containers struct {
	body, digest string
	rows         []containerRow
	commands     map[string]containerCommand
}

func (c *Containers) Body() string   { return c.body }
func (c *Containers) SHA256() string { return c.digest }

type containerRow struct {
	ID                string `json:"id"`
	ImageID           string `json:"image_id"`
	Project           string `json:"project,omitempty"`
	Service           string `json:"service,omitempty"`
	Oneoff            bool   `json:"oneoff"`
	OneoffKnown       bool   `json:"oneoff_known"`
	Running           bool   `json:"running"`
	Paused            bool   `json:"paused"`
	Restarting        bool   `json:"restarting"`
	PID               int64  `json:"pid"`
	Status            string `json:"status"`
	RestartPolicy     string `json:"restart_policy"`
	MaximumRetryCount int64  `json:"maximum_retry_count"`
	ConfigSHA256      string `json:"config_sha256"`
	HostConfigSHA256  string `json:"host_config_sha256"`
	MountsSHA256      string `json:"mounts_sha256"`
}
type containerCommand struct{ cmd, entrypoint []string }
type inspectContainer struct {
	ID    string `json:"Id"`
	Image string `json:"Image"`
	State struct {
		Running    *bool  `json:"Running"`
		Paused     *bool  `json:"Paused"`
		Restarting *bool  `json:"Restarting"`
		PID        *int64 `json:"Pid"`
		Status     string `json:"Status"`
	} `json:"State"`
	Config     json.RawMessage `json:"Config"`
	HostConfig json.RawMessage `json:"HostConfig"`
	Mounts     json.RawMessage `json:"Mounts"`
}
type inspectContainerConfig struct {
	Labels     map[string]string `json:"Labels"`
	Cmd        []string          `json:"Cmd"`
	Entrypoint []string          `json:"Entrypoint"`
}
type inspectHostConfig struct {
	RestartPolicy *struct {
		Name              string `json:"Name"`
		MaximumRetryCount *int64 `json:"MaximumRetryCount"`
	} `json:"RestartPolicy"`
}

var containerIDPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var localImageIDPattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)

// ObserveContainers uses fixed read-only commands against the deployment
// identity's local daemon. Agents must never invoke it with a host Docker socket.
// Enumerating the entire daemon prevents project-filtered one-offs or orphaned
// old writers from disappearing from the evidence.
func ObserveContainers(ctx context.Context) (*Containers, error) {
	return observeContainers(ctx, readDocker)
}

func daemonIDs(ctx context.Context, run dockerRead) ([]string, error) {
	b, err := run(ctx, []string{"ps", "--all", "--no-trunc", "--format", "{{.ID}}"})
	if err != nil || len(b) > 256*65 {
		return nil, reject("bounded daemon enumeration")
	}
	text := strings.TrimSuffix(string(b), "\n")
	if text == "" {
		return []string{}, nil
	}
	ids := strings.Split(text, "\n")
	if len(ids) > 256 {
		return nil, reject("daemon container count")
	}
	seen := map[string]bool{}
	for _, id := range ids {
		if !containerIDPattern.MatchString(id) || seen[id] {
			return nil, reject("exact daemon container IDs")
		}
		seen[id] = true
	}
	sort.Strings(ids)
	return ids, nil
}

func containerSnapshot(ctx context.Context, run dockerRead) (*Containers, error) {
	ids, err := daemonIDs(ctx, run)
	if err != nil {
		return nil, err
	}
	rows := make([]containerRow, 0, len(ids))
	commands := map[string]containerCommand{}
	for start := 0; start < len(ids); start += 32 {
		end := start + 32
		if end > len(ids) {
			end = len(ids)
		}
		args := append([]string{"container", "inspect"}, ids[start:end]...)
		b, err := run(ctx, args)
		if err != nil || uniqueJSON(b) != nil {
			return nil, reject("container inspection")
		}
		var inspected []inspectContainer
		if json.Unmarshal(b, &inspected) != nil || len(inspected) != end-start {
			return nil, reject("container inspection cardinality")
		}
		for n, c := range inspected {
			if c.ID != ids[start+n] || !localImageIDPattern.MatchString(c.Image) || c.State.Running == nil || c.State.Paused == nil || c.State.Restarting == nil || c.State.PID == nil || *c.State.PID < 0 || len(c.Config) == 0 || string(c.Config) == "null" || len(c.HostConfig) == 0 || string(c.HostConfig) == "null" || len(c.Mounts) == 0 || string(c.Mounts) == "null" {
				return nil, reject("complete container identity and state")
			}
			status := c.State.Status
			switch status {
			case "created", "running", "paused", "restarting", "removing", "exited", "dead":
			default:
				return nil, reject("known container status")
			}
			var config inspectContainerConfig
			var host inspectHostConfig
			var mounts []json.RawMessage
			if json.Unmarshal(c.Config, &config) != nil || json.Unmarshal(c.HostConfig, &host) != nil || json.Unmarshal(c.Mounts, &mounts) != nil || len(mounts) > 256 || len(config.Cmd) > 256 || len(config.Entrypoint) > 64 || host.RestartPolicy == nil || host.RestartPolicy.MaximumRetryCount == nil || *host.RestartPolicy.MaximumRetryCount < 0 || *host.RestartPolicy.MaximumRetryCount > 100000 {
				return nil, reject("complete container configuration")
			}
			restart := host.RestartPolicy.Name
			switch restart {
			case "no", "always", "unless-stopped", "on-failure":
			default:
				return nil, reject("known restart policy")
			}
			project, service := config.Labels["com.docker.compose.project"], config.Labels["com.docker.compose.service"]
			if (project != "" && !servicePattern.MatchString(project)) || (service != "" && !servicePattern.MatchString(service)) {
				return nil, reject("bounded Compose identity labels")
			}
			oneoff := config.Labels["com.docker.compose.oneoff"]
			if oneoff != "" && oneoff != "True" && oneoff != "False" && oneoff != "true" && oneoff != "false" {
				return nil, reject("explicit one-off label")
			}
			rows = append(rows, containerRow{c.ID, c.Image, project, service, strings.EqualFold(oneoff, "true"), oneoff != "", *c.State.Running, *c.State.Paused, *c.State.Restarting, *c.State.PID, status, restart, *host.RestartPolicy.MaximumRetryCount, digest(c.Config), digest(c.HostConfig), digest(c.Mounts)})
			commands[c.ID] = containerCommand{append([]string{}, config.Cmd...), append([]string{}, config.Entrypoint...)}
		}
	}
	readback, err := daemonIDs(ctx, run)
	if err != nil || strings.Join(ids, "\n") != strings.Join(readback, "\n") || ctx.Err() != nil {
		return nil, reject("daemon enumeration readback")
	}
	body, err := json.Marshal(struct {
		Version    string         `json:"version"`
		Containers []containerRow `json:"containers"`
	}{"jobseek.crawler-container-inventory/v1", rows})
	if err != nil {
		return nil, reject("canonical container inventory")
	}
	return &Containers{string(body), digest(body), rows, commands}, nil
}

func observeContainers(ctx context.Context, run dockerRead) (*Containers, error) {
	if ctx == nil || ctx.Err() != nil || run == nil {
		return nil, reject("explicit container observation context")
	}
	bounded, cancel := context.WithTimeout(ctx, 120*time.Second)
	defer cancel()
	first, err := containerSnapshot(bounded, run)
	if err != nil {
		return nil, err
	}
	second, err := containerSnapshot(bounded, run)
	if err != nil || first.SHA256() != second.SHA256() || bounded.Err() != nil {
		return nil, reject("container inventory readback differs")
	}
	return first, nil
}

// ColdContainers proves the Docker state predicate only. Image runtime fidelity,
// selected release/spec admission, host lock, SQL barriers/leases and readiness
// are separate mandatory coordinator checks. It never disables restarts or stops
// anything. Only authenticated Redis/Postgres/Alloy daemon roles may stay live;
// all other services, every one-off and unaccounted container must be fully stopped
// with restart=no/0. New/old image bindings may share a project during staging.
type ColdContainers struct{ body, digest string }

func (c *ColdContainers) Body() string   { return c.body }
func (c *ColdContainers) SHA256() string { return c.digest }

func passiveDaemon(service string, image serviceImage, command containerCommand) bool {
	repository, _, _ := strings.Cut(repositoryDigest(image.Reference), "@")
	if len(command.cmd) == 0 {
		return false
	}
	entry := strings.Join(command.entrypoint, "\x00")
	switch {
	case service == "redis" && repository == "redis":
		return command.cmd[0] == "redis-server" && entry == "docker-entrypoint.sh"
	case service == "postgres" && repository == "postgres":
		return command.cmd[0] == "postgres" && entry == "docker-entrypoint.sh"
	case service == "alloy" && repository == "grafana/alloy":
		return command.cmd[0] == "run" && (entry == "/bin/alloy" || entry == "/usr/bin/alloy")
	}
	return false
}

func RequireColdContainers(ctx context.Context, inventory *Containers, images []*Images) (*ColdContainers, error) {
	if ctx == nil || ctx.Err() != nil || inventory == nil || !shaPattern.MatchString(inventory.SHA256()) || digest([]byte(inventory.Body())) != inventory.SHA256() || len(images) == 0 || len(images) > 12 {
		return nil, reject("explicit cold inventory and image bindings")
	}
	bindings := map[string]map[string][]serviceImage{}
	hashes := []string{}
	seen := map[string]bool{}
	architecture := ""
	for _, proof := range images {
		if proof == nil || !shaPattern.MatchString(proof.SHA256()) || digest([]byte(proof.Body())) != proof.SHA256() || seen[proof.SHA256()] {
			return nil, reject("unique authenticated image bindings")
		}
		var d imageDocument
		if json.Unmarshal([]byte(proof.Body()), &d) != nil || d.Version != "jobseek.crawler-release-images/v1" || !servicePattern.MatchString(d.Project) || !shaPattern.MatchString(d.FileEvidenceSHA256) || !shaPattern.MatchString(d.ComposeSHA256) || (d.Architecture != "amd64" && d.Architecture != "arm64") || len(d.Services) == 0 || len(d.Services) > 128 {
			return nil, reject("complete project image evidence")
		}
		if architecture != "" && architecture != d.Architecture {
			return nil, reject("same host image architecture")
		}
		architecture = d.Architecture
		seen[proof.SHA256()] = true
		hashes = append(hashes, proof.SHA256())
		if bindings[d.Project] == nil {
			bindings[d.Project] = map[string][]serviceImage{}
		}
		for name, image := range d.Services {
			if !servicePattern.MatchString(name) || !pinnedReferencePattern.MatchString(image.Reference) || !localImageIDPattern.MatchString(image.ImageID) {
				return nil, reject("project image identity")
			}
			bindings[d.Project][name] = append(bindings[d.Project][name], image)
		}
	}
	mutating, passive, unaccounted := 0, 0, 0
	for _, row := range inventory.rows {
		matched, allowedLive := false, false
		for _, image := range bindings[row.Project][row.Service] {
			if image.ImageID == row.ImageID {
				matched = true
				if row.OneoffKnown && !row.Oneoff && passiveDaemon(row.Service, image, inventory.commands[row.ID]) {
					allowedLive = true
				}
			}
		}
		if _, knownProject := bindings[row.Project]; knownProject && !matched {
			return nil, reject("unaccounted project service or image")
		}
		if allowedLive {
			if !row.Running || row.Paused || row.Restarting || row.PID <= 0 || row.Status != "running" {
				return nil, reject("required infrastructure daemon state")
			}
			passive++
			continue
		}
		if row.Running || row.Paused || row.Restarting || row.PID != 0 || (row.Status != "created" && row.Status != "exited") || row.RestartPolicy != "no" || row.MaximumRetryCount != 0 {
			return nil, reject("writer one-off or unaccounted container not contained")
		}
		if matched {
			mutating++
		} else {
			unaccounted++
		}
	}
	if ctx.Err() != nil {
		return nil, reject("cold container predicate cancelled")
	}
	sort.Strings(hashes)
	body, err := json.Marshal(struct {
		Version             string   `json:"version"`
		InventorySHA256     string   `json:"inventory_sha256"`
		ImageEvidenceSHA256 []string `json:"image_evidence_sha256"`
		Contained           int      `json:"contained_containers"`
		Infrastructure      int      `json:"infrastructure_containers"`
		UnaccountedStopped  int      `json:"unaccounted_stopped_containers"`
	}{"jobseek.crawler-container-state/v1", inventory.SHA256(), hashes, mutating, passive, unaccounted})
	if err != nil {
		return nil, reject("canonical cold container predicate")
	}
	return &ColdContainers{string(body), digest(body)}, nil
}
