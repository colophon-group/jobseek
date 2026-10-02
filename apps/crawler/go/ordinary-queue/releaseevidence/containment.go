package releaseevidence

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"sort"
	"time"
)

// WriterContainmentPlan records exact daemon identities before any effect.
// It authorizes only restart disabling and stopping under the caller's held
// deployment lock. It never selects specs, starts containers or grants runtime
// ownership. Raw configuration and credentials are not retained in this plan.
type WriterContainmentPlan struct {
	body, digest string
	document     containmentDocument
}

func (p *WriterContainmentPlan) Body() string   { return p.body }
func (p *WriterContainmentPlan) SHA256() string { return p.digest }
func (p *WriterContainmentPlan) TargetIDs() []string {
	ids := []string{}
	for _, row := range p.document.Containers {
		if row.Target {
			ids = append(ids, row.Original.ID)
		}
	}
	return ids
}

type containmentRow struct {
	Original                 containerRow `json:"original"`
	Target                   bool         `json:"target"`
	HostWithoutRestartSHA256 string       `json:"host_without_restart_sha256"`
}
type containmentDocument struct {
	Version         string           `json:"version"`
	Project         string           `json:"project"`
	InventorySHA256 string           `json:"inventory_sha256"`
	ImagesSHA256    []string         `json:"image_evidence_sha256"`
	Containers      []containmentRow `json:"containers"`
}

// Only RestartPolicy is permitted to change. Canonical JSON retains every
// other field, including unknown fields and exact integer values.
func hostWithoutRestart(raw json.RawMessage) (string, error) {
	if uniqueJSON(raw) != nil {
		return "", reject("complete containment host config")
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || fields == nil || fields["RestartPolicy"] == nil {
		return "", reject("containment restart field")
	}
	delete(fields, "RestartPolicy")
	b, err := json.Marshal(fields)
	if err != nil {
		return "", reject("containment host config")
	}
	return digest(b), nil
}

func containmentImages(images []*Images, project string) (map[string][]serviceImage, []string, error) {
	if !servicePattern.MatchString(project) || len(images) < 1 || len(images) > 12 {
		return nil, nil, reject("containment project images")
	}
	services, hashes, seen := map[string][]serviceImage{}, []string{}, map[string]bool{}
	architecture := ""
	for _, proof := range images {
		if proof == nil || digest([]byte(proof.Body())) != proof.SHA256() || seen[proof.SHA256()] {
			return nil, nil, reject("unique containment images")
		}
		var d imageDocument
		if json.Unmarshal([]byte(proof.Body()), &d) != nil || d.Version != "jobseek.crawler-release-images/v1" || d.Project != project || !shaPattern.MatchString(d.FileEvidenceSHA256) || !shaPattern.MatchString(d.ComposeSHA256) || (d.Architecture != "amd64" && d.Architecture != "arm64") || len(d.Services) < 1 || len(d.Services) > 128 || (architecture != "" && architecture != d.Architecture) {
			return nil, nil, reject("bound containment images")
		}
		architecture = d.Architecture
		for service, image := range d.Services {
			if !servicePattern.MatchString(service) || !pinnedReferencePattern.MatchString(image.Reference) || !localImageIDPattern.MatchString(image.ImageID) {
				return nil, nil, reject("containment service image")
			}
			services[service] = append(services[service], image)
		}
		seen[proof.SHA256()] = true
		hashes = append(hashes, proof.SHA256())
	}
	sort.Strings(hashes)
	return services, hashes, nil
}

func containmentTarget(row containerRow, command containerCommand, project string, services map[string][]serviceImage) (bool, error) {
	if row.Project != project {
		return false, nil
	}
	matched, passive := false, false
	for _, image := range services[row.Service] {
		if image.ImageID == row.ImageID {
			matched = true
			passive = passive || (row.OneoffKnown && !row.Oneoff && passiveDaemon(row.Service, image, command))
		}
	}
	if !matched {
		return false, reject("unbound containment project container")
	}
	// Maintenance one-offs and ambiguous labels must already be cold. Their
	// execution is not covered by regular service installed-file requests, so
	// this phase never acquires mutation authority for them.
	if row.Oneoff || !row.OneoffKnown {
		return false, nil
	}
	return !passive, nil
}

func PlanWriterContainment(ctx context.Context, inventory *Containers, images []*Images, project string) (*WriterContainmentPlan, error) {
	if ctx == nil || ctx.Err() != nil || inventory == nil || digest([]byte(inventory.Body())) != inventory.SHA256() {
		return nil, reject("observed containment inventory")
	}
	services, hashes, err := containmentImages(images, project)
	if err != nil {
		return nil, err
	}
	d := containmentDocument{"jobseek.crawler-writer-containment-plan/v1", project, inventory.SHA256(), hashes, []containmentRow{}}
	// This copy is only a feasibility check. Never expose its synthetic stopped
	// states as an observation; actual state is required after Docker effects.
	prospective := &Containers{rows: append([]containerRow{}, inventory.rows...), commands: inventory.commands}
	for n, row := range inventory.rows {
		target, err := containmentTarget(row, inventory.commands[row.ID], project, services)
		if err != nil {
			return nil, err
		}
		host, err := hostWithoutRestart(inventory.commands[row.ID].host)
		if err != nil {
			return nil, err
		}
		if target {
			if row.Paused || row.Restarting || !((row.Running && row.PID > 0 && row.Status == "running") || (!row.Running && row.PID == 0 && (row.Status == "created" || row.Status == "exited"))) {
				return nil, reject("stable containment target")
			}
			prospective.rows[n].Running, prospective.rows[n].PID, prospective.rows[n].Status = false, 0, "exited"
			prospective.rows[n].RestartPolicy, prospective.rows[n].MaximumRetryCount = "no", 0
		}
		d.Containers = append(d.Containers, containmentRow{row, target, host})
	}
	b, _ := json.Marshal(struct {
		Version    string         `json:"version"`
		Containers []containerRow `json:"containers"`
	}{containerInventoryVersion, prospective.rows})
	prospective.body, prospective.digest = string(b), digest(b)
	if _, err := RequireColdContainers(ctx, prospective, images); err != nil {
		return nil, err
	}
	body, err := json.Marshal(d)
	if err != nil {
		return nil, reject("canonical containment plan")
	}
	return &WriterContainmentPlan{string(body), digest(body), d}, nil
}

// Decode requires the exact immutable retained plan digest. Target membership
// is re-derived from fresh opaque images and inspect commands before effects.
func DecodeWriterContainmentPlan(body []byte, expected string) (*WriterContainmentPlan, error) {
	if !shaPattern.MatchString(expected) || digest(body) != expected || uniqueJSON(body) != nil {
		return nil, reject("bound retained containment plan")
	}
	var d containmentDocument
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&d) != nil || decoder.Decode(new(any)) != io.EOF || d.Version != "jobseek.crawler-writer-containment-plan/v1" || !servicePattern.MatchString(d.Project) || !shaPattern.MatchString(d.InventorySHA256) || d.Containers == nil || len(d.Containers) > 256 || len(d.ImagesSHA256) < 1 || len(d.ImagesSHA256) > 12 {
		return nil, reject("complete retained containment plan")
	}
	for n, hash := range d.ImagesSHA256 {
		if !shaPattern.MatchString(hash) || (n > 0 && hash <= d.ImagesSHA256[n-1]) {
			return nil, reject("sorted containment images")
		}
	}
	rows := []containerRow{}
	for n, row := range d.Containers {
		if !containerIDPattern.MatchString(row.Original.ID) || (n > 0 && row.Original.ID <= d.Containers[n-1].Original.ID) || !shaPattern.MatchString(row.HostWithoutRestartSHA256) {
			return nil, reject("exact sorted containment IDs")
		}
		rows = append(rows, row.Original)
	}
	original, _ := json.Marshal(struct {
		Version    string         `json:"version"`
		Containers []containerRow `json:"containers"`
	}{containerInventoryVersion, rows})
	canonical, err := json.Marshal(d)
	if err != nil || !bytes.Equal(body, canonical) || digest(original) != d.InventorySHA256 {
		return nil, reject("canonical complete containment inventory")
	}
	return &WriterContainmentPlan{string(body), expected, d}, nil
}

func (p *WriterContainmentPlan) verify(ctx context.Context, inventory *Containers, images []*Images, restartsDisabled bool) error {
	if ctx == nil || ctx.Err() != nil || p == nil || inventory == nil || digest([]byte(p.Body())) != p.SHA256() || digest([]byte(inventory.Body())) != inventory.SHA256() {
		return reject("current containment bindings")
	}
	services, hashes, err := containmentImages(images, p.document.Project)
	if err != nil || len(inventory.rows) != len(p.document.Containers) {
		return reject("complete current containment set")
	}
	h, _ := json.Marshal(hashes)
	wanted, _ := json.Marshal(p.document.ImagesSHA256)
	if !bytes.Equal(h, wanted) {
		return reject("retained containment image drift")
	}
	for n, row := range inventory.rows {
		bound := p.document.Containers[n]
		target, err := containmentTarget(row, inventory.commands[row.ID], p.document.Project, services)
		if err != nil || row.ID != bound.Original.ID || target != bound.Target {
			return reject("retained containment membership drift")
		}
		if !target {
			if row != bound.Original {
				return reject("outside containment drift")
			}
			continue
		}
		host, err := hostWithoutRestart(inventory.commands[row.ID].host)
		if err != nil || host != bound.HostWithoutRestartSHA256 || row.ConfigSHA256 != bound.Original.ConfigSHA256 || row.MountsSHA256 != bound.Original.MountsSHA256 || row.ImageID != bound.Original.ImageID || row.Project != bound.Original.Project || row.Service != bound.Original.Service || row.Oneoff != bound.Original.Oneoff || row.OneoffKnown != bound.Original.OneoffKnown {
			return reject("containment target configuration drift")
		}
		noRestart := row.RestartPolicy == "no" && row.MaximumRetryCount == 0
		if !noRestart && (restartsDisabled || row.RestartPolicy != bound.Original.RestartPolicy || row.MaximumRetryCount != bound.Original.MaximumRetryCount) {
			return reject("containment restart drift")
		}
		if row.Paused || row.Restarting || !((row.Running && row.PID > 0 && row.Status == "running") || (!row.Running && row.PID == 0 && (row.Status == "created" || row.Status == "exited"))) {
			return reject("containment target state drift")
		}
	}
	return nil
}

type containmentObserve func(context.Context) (*Containers, error)
type containmentEffect func(context.Context, []string) error

func containmentDockerEffect(ctx context.Context, args []string) error {
	bounded, cancel := context.WithTimeout(ctx, 150*time.Second)
	defer cancel()
	cmd := dockerReadCommand(bounded, args)
	if bindContainmentChild(cmd) != nil {
		return reject("contained Docker child required")
	}
	cmd.Stdout, cmd.Stderr = io.Discard, io.Discard
	if cmd.Run() != nil || bounded.Err() != nil {
		return reject("Docker containment effect failed")
	}
	return nil
}

// ContainWriters must be called only by the supported coordinator deployment
// identity. guard independently rechecks the lock, retained intent and selected
// evidence before every fixed command; checkpoint fsyncs the disabled-restart
// barrier before stopping. A failure never restarts any container. Agents must
// not invoke this function against a host daemon.
func ContainWriters(ctx context.Context, plan *WriterContainmentPlan, images []*Images, disabled bool, guard func() error, checkpoint func() error) (*ColdContainers, error) {
	return containWriters(ctx, plan, images, disabled, guard, checkpoint, ObserveContainers, containmentDockerEffect)
}

func containWriters(ctx context.Context, plan *WriterContainmentPlan, images []*Images, disabled bool, guard func() error, checkpoint func() error, observe containmentObserve, effect containmentEffect) (*ColdContainers, error) {
	if ctx == nil || ctx.Err() != nil || plan == nil || guard == nil || checkpoint == nil || observe == nil || effect == nil {
		return nil, reject("explicit guarded containment")
	}
	check := func() (*Containers, error) {
		if ctx.Err() != nil || guard() != nil {
			return nil, reject("containment coordinator guard")
		}
		current, err := observe(ctx)
		if err != nil || plan.verify(ctx, current, images, disabled) != nil || guard() != nil {
			return nil, reject("containment observation drift")
		}
		return current, nil
	}
	if _, err := check(); err != nil {
		return nil, err
	}
	if !disabled {
		for _, id := range plan.TargetIDs() {
			current, err := check()
			if err != nil {
				return nil, err
			}
			for _, row := range current.rows {
				if row.ID == id && (row.RestartPolicy != "no" || row.MaximumRetryCount != 0) {
					if effect(ctx, []string{"container", "update", "--restart=no", id}) != nil {
						return nil, reject("containment restart update failed")
					}
				}
			}
		}
		disabled = true
		if _, err := check(); err != nil {
			return nil, err
		}
		if checkpoint() != nil {
			return nil, reject("durable restart barrier failed")
		}
	}
	current, err := check()
	if err != nil {
		return nil, err
	}
	ids := []string{}
	for n, row := range current.rows {
		if plan.document.Containers[n].Target && row.Running {
			ids = append(ids, row.ID)
		}
	}
	if len(ids) > 0 {
		// All restart policies are already observed disabled and durably bound.
		args := append([]string{"container", "stop", "--time", "120"}, ids...)
		if effect(ctx, args) != nil {
			return nil, reject("containment stop failed")
		}
	}
	current, err = check()
	if err != nil {
		return nil, err
	}
	cold, err := RequireColdContainers(ctx, current, images)
	if err != nil || guard() != nil {
		return nil, reject("observed Docker containment required")
	}
	return cold, nil
}
