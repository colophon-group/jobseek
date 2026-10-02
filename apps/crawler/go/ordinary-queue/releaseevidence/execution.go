package releaseevidence

import (
	"context"
	"encoding/json"
	"path"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// Execution binds container execution settings to reobserved release Compose and
// image defaults. It is not source/binary provenance, actual process UID/GID,
// mounted-content fidelity, complete namespace/network/security admission, a
// stopped-writer proof or a host cutover grant. These remain coordinator gates.
type Execution struct{ body, digest string }

func (e *Execution) Body() string   { return e.body }
func (e *Execution) SHA256() string { return e.digest }

type executionConfig struct {
	Cmd, Entrypoint  []string
	User, WorkingDir string
	Env              []string
	Volumes          map[string]json.RawMessage
}
type executionMount struct {
	Type, Name, Source, Destination, Propagation, Driver string
	RW                                                   *bool
}
type executionHost struct {
	Binds        []string
	Mounts       []struct{ Type, Source, Target string }
	Tmpfs        map[string]string
	VolumesFrom  []string
	VolumeDriver string
}
type composeExecution struct {
	Name     string
	Services map[string]struct {
		Image               string
		Command, Entrypoint json.RawMessage
		Environment         map[string]*string
		Volumes             []struct {
			Type, Source, Target string
			ReadOnly             bool `json:"read_only"`
			Bind                 struct {
				Propagation string
				SELinux     string `json:"selinux"`
			}
			Volume struct {
				Subpath string
				NoCopy  bool `json:"nocopy"`
			}
			Tmpfs json.RawMessage
		}
		Tmpfs []string
	}
	Volumes map[string]struct {
		Name, Driver string
		DriverOpts   map[string]string `json:"driver_opts"`
	}
}
type executionProjection struct {
	Cmd, Entrypoint []string
	UID, GID        uint32
	WorkingDir      string
	Environment     map[string]string
}

var numericUserPattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,9}):(0|[1-9][0-9]{0,9})$`)
var environmentNamePattern = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)
var volumeNamePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$`)

func exactExecutionKeys(fields map[string]json.RawMessage, names []string) error {
	for key := range fields {
		for _, name := range names {
			if key != name && strings.EqualFold(key, name) {
				return reject("ambiguous execution field casing")
			}
		}
	}
	return nil
}

func decodeExecutionConfig(raw json.RawMessage, container bool) (executionConfig, error) {
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || fields == nil {
		return executionConfig{}, reject("complete execution config object")
	}
	if err := exactExecutionKeys(fields, []string{"User", "WorkingDir", "Env", "Cmd", "Entrypoint", "Volumes"}); err != nil {
		return executionConfig{}, err
	}
	// Stable container Config fields are not omitempty in the Engine API.
	// OCI image defaults are optional; absent fields legitimately inherit empty.
	// https://github.com/moby/moby/blob/master/api/types/container/config.go
	for _, key := range []string{"User", "WorkingDir", "Env", "Cmd", "Entrypoint"} {
		value, present := fields[key]
		if container && !present {
			return executionConfig{}, reject("missing container execution field")
		}
		if present && (key == "User" || key == "WorkingDir") && string(value) == "null" {
			return executionConfig{}, reject("nullable configured execution string")
		}
	}
	var config executionConfig
	if json.Unmarshal(raw, &config) != nil {
		return executionConfig{}, reject("typed execution config")
	}
	return config, nil
}

func configuredUser(user string) (uint32, uint32, error) {
	if user == "" {
		return 0, 0, nil
	} // Docker's implicit configured root, not process identity
	if !numericUserPattern.MatchString(user) {
		return 0, 0, reject("explicit numeric UID and GID")
	}
	pair := strings.Split(user, ":")
	u, e1 := strconv.ParseUint(pair[0], 10, 32)
	g, e2 := strconv.ParseUint(pair[1], 10, 32)
	if e1 != nil || e2 != nil {
		return 0, 0, reject("numeric UID and GID bounds")
	}
	return uint32(u), uint32(g), nil
}
func executionEnvironment(values []string) (map[string]string, error) {
	if len(values) > 2048 {
		return nil, reject("bounded image/container environment")
	}
	result := map[string]string{}
	for _, value := range values {
		name, v, ok := strings.Cut(value, "=")
		if !ok || !environmentNamePattern.MatchString(name) || strings.ContainsRune(v, '\x00') {
			return nil, reject("complete execution environment")
		}
		if _, exists := result[name]; exists {
			return nil, reject("duplicate execution environment")
		}
		result[name] = v
	}
	return result, nil
}
func configuredArguments(raw json.RawMessage, inherited []string) ([]string, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return append([]string{}, inherited...), nil
	}
	// Compose config resolves shell-form text into an argv array. Do not shell
	// parse or execute evidence. Empty-string/list explicitly clears defaults.
	if string(raw) == `""` {
		return []string{}, nil
	}
	var args []string
	if json.Unmarshal(raw, &args) != nil || args == nil || len(args) > 256 {
		return nil, reject("resolved execution arguments")
	}
	for _, arg := range args {
		if strings.ContainsRune(arg, '\x00') {
			return nil, reject("execution argument framing")
		}
	}
	return args, nil
}
func executionSettings(config executionConfig) (executionProjection, error) {
	u, g, err := configuredUser(config.User)
	if err != nil {
		return executionProjection{}, err
	}
	env, err := executionEnvironment(config.Env)
	if err != nil {
		return executionProjection{}, err
	}
	if len(config.Cmd) > 256 || len(config.Entrypoint) > 256 || (config.WorkingDir != "" && (!strings.HasPrefix(config.WorkingDir, "/") || path.Clean(config.WorkingDir) != config.WorkingDir)) {
		return executionProjection{}, reject("bounded configured execution")
	}
	for _, args := range [][]string{config.Cmd, config.Entrypoint} {
		for _, arg := range args {
			if strings.ContainsRune(arg, '\x00') {
				return executionProjection{}, reject("configured argument framing")
			}
		}
	}
	return executionProjection{append([]string{}, config.Cmd...), append([]string{}, config.Entrypoint...), u, g, config.WorkingDir, env}, nil
}
func cleanDestination(s string) bool {
	return strings.HasPrefix(s, "/") && path.Clean(s) == s && !strings.ContainsRune(s, '\x00') && s != "/"
}

func verifyExecutionMounts(service string, compose composeExecution, defaults executionConfig, private containerCommand) error {
	s := compose.Services[service]
	if len(s.Volumes) > 256 || len(s.Tmpfs) > 256 {
		return reject("bounded declared mounts")
	}
	var host executionHost
	var mounts []executionMount
	var hostFields map[string]json.RawMessage
	if json.Unmarshal(private.host, &hostFields) != nil {
		return reject("complete mount host configuration")
	}
	if err := exactExecutionKeys(hostFields, []string{"Binds", "VolumesFrom", "VolumeDriver", "Mounts", "Tmpfs"}); err != nil {
		return err
	}
	for _, key := range []string{"Binds", "VolumesFrom", "VolumeDriver"} {
		if _, present := hostFields[key]; !present {
			return reject("missing mount host field")
		}
	}
	if string(hostFields["VolumeDriver"]) == "null" {
		return reject("nullable volume driver")
	}
	if json.Unmarshal(private.host, &host) != nil || json.Unmarshal(private.mounts, &mounts) != nil || len(host.VolumesFrom) != 0 || host.VolumeDriver != "" {
		return reject("unsupported mounted execution source")
	}
	wantedTmpfs := map[string]string{}
	for _, value := range s.Tmpfs {
		target, options, _ := strings.Cut(value, ":")
		if !cleanDestination(target) {
			return reject("unique tmpfs declaration")
		}
		if _, ok := wantedTmpfs[target]; ok {
			return reject("duplicate tmpfs declaration")
		}
		wantedTmpfs[target] = options
	}
	byTarget := map[string]executionMount{}
	for _, m := range mounts {
		if !cleanDestination(m.Destination) || m.RW == nil {
			return reject("complete mounted destination")
		}
		if _, ok := byTarget[m.Destination]; ok {
			return reject("duplicate mounted destination")
		}
		byTarget[m.Destination] = m
	}
	expectedTargets := map[string]bool{}
	for _, v := range s.Volumes {
		_, tmpfsTarget := wantedTmpfs[v.Target]
		if !cleanDestination(v.Target) || expectedTargets[v.Target] || tmpfsTarget || len(v.Tmpfs) > 0 && string(v.Tmpfs) != "null" || v.Volume.Subpath != "" || v.Volume.NoCopy || v.Bind.SELinux != "" {
			return reject("supported exact mount declaration")
		}
		expectedTargets[v.Target] = true
		m, ok := byTarget[v.Target]
		if !ok || m.Type != v.Type || *m.RW == v.ReadOnly {
			return reject("mounted destination type or access drift")
		}
		switch v.Type {
		case "bind":
			if !strings.HasPrefix(v.Source, "/") || path.Clean(v.Source) != v.Source || strings.ContainsRune(v.Source, '\x00') || m.Source != v.Source || m.Name != "" {
				return reject("bound mount source drift")
			}
			propagation := v.Bind.Propagation
			if propagation == "" {
				propagation = "rprivate"
			}
			if m.Propagation != propagation {
				return reject("bind propagation drift")
			}
		case "volume":
			definition := compose.Volumes[v.Source]
			name := definition.Name
			if (definition.Driver != "" && definition.Driver != "local") || len(definition.DriverOpts) != 0 {
				return reject("unsupported declared volume provider")
			}
			if !volumeNamePattern.MatchString(name) || m.Name != name || !strings.HasPrefix(m.Source, "/") || path.Clean(m.Source) != m.Source || m.Driver != "local" {
				return reject("named volume identity drift")
			}
		default:
			return reject("unsupported declared mount type")
		}
		delete(byTarget, v.Target)
	}
	// Image VOLUME defaults may create anonymous local data volumes. They cannot
	// authorize explicit source substitution hidden in HostConfig.
	for target := range defaults.Volumes {
		if !cleanDestination(target) {
			return reject("supported image volume destination")
		}
		_, tmpfsTarget := wantedTmpfs[target]
		if !expectedTargets[target] && !tmpfsTarget {
			if _, present := byTarget[target]; !present {
				return reject("missing image volume mount")
			}
		}
	}
	for target, m := range byTarget {
		if _, tmpfsTarget := wantedTmpfs[target]; tmpfsTarget && m.Type == "tmpfs" {
			readOnly, readWrite := false, false
			for _, option := range strings.Split(wantedTmpfs[target], ",") {
				readOnly = readOnly || option == "ro"
				readWrite = readWrite || option == "rw"
			}
			if m.Source != "" || m.Name != "" || (readOnly && readWrite) || *m.RW == readOnly {
				return reject("tmpfs mounted access drift")
			}
			continue // exact options checked against HostConfig below
		}
		if _, declared := defaults.Volumes[target]; !declared || m.Type != "volume" || !*m.RW || !volumeNamePattern.MatchString(m.Name) || !strings.HasPrefix(m.Source, "/") || path.Clean(m.Source) != m.Source || m.Driver != "local" {
			return reject("extra or substituted image mount")
		}
		for _, b := range host.Binds {
			p := strings.Split(b, ":")
			if len(p) > 1 && p[1] == target {
				return reject("explicit substitution of image volume")
			}
		}
		for _, v := range host.Mounts {
			if v.Target == target && v.Source != "" {
				return reject("explicit substitution of image volume")
			}
		}
	}
	if len(wantedTmpfs) != len(host.Tmpfs) {
		return reject("tmpfs mount cardinality")
	}
	for target, options := range wantedTmpfs {
		if actual, ok := host.Tmpfs[target]; !ok || actual != options {
			return reject("tmpfs options drift")
		}
	}
	return nil
}

func verifyExecutionSettings(service string, composeBytes []byte, compose composeExecution, image executionConfig, private containerCommand) (executionProjection, error) {
	s := compose.Services[service]
	var raw struct {
		Services map[string]struct {
			User             string
			WorkingDir       string `json:"working_dir"`
			Configs, Secrets json.RawMessage
			VolumesFrom      json.RawMessage `json:"volumes_from"`
		}
	}
	if json.Unmarshal(composeBytes, &raw) != nil {
		return executionProjection{}, reject("private Compose execution")
	}
	for _, provider := range []json.RawMessage{raw.Services[service].Configs, raw.Services[service].Secrets, raw.Services[service].VolumesFrom} {
		if len(provider) > 0 && string(provider) != "null" && string(provider) != "[]" {
			return executionProjection{}, reject("unsupported declared mount provider")
		}
	}
	actual, decodeErr := decodeExecutionConfig(private.config, true)
	if decodeErr != nil {
		return executionProjection{}, reject("private container execution")
	}
	expected := image
	var err error
	expected.Entrypoint, err = configuredArguments(s.Entrypoint, image.Entrypoint)
	if err != nil {
		return executionProjection{}, err
	}
	defaultCommand := image.Cmd
	if len(s.Entrypoint) > 0 && string(s.Entrypoint) != "null" {
		defaultCommand = nil
	}
	expected.Cmd, err = configuredArguments(s.Command, defaultCommand)
	if err != nil {
		return executionProjection{}, err
	}
	if user := raw.Services[service].User; user != "" {
		expected.User = user
	}
	if wd := raw.Services[service].WorkingDir; wd != "" {
		expected.WorkingDir = wd
	}
	wanted, err := executionSettings(expected)
	if err != nil {
		return executionProjection{}, err
	}
	for name, value := range s.Environment {
		if !environmentNamePattern.MatchString(name) || value == nil || strings.ContainsRune(*value, '\x00') {
			return executionProjection{}, reject("fully resolved Compose environment")
		}
		wanted.Environment[name] = *value
	}
	observed, err := executionSettings(actual)
	if err != nil || !reflect.DeepEqual(wanted, observed) {
		return executionProjection{}, reject("container command user directory or environment drift")
	}
	if err := verifyExecutionMounts(service, compose, image, private); err != nil {
		return executionProjection{}, err
	}
	return wanted, nil
}

// RequireContainerExecution checks declared settings only. Raw Docker records
// never leave this opaque evidence path. Caller-held lock, independent installed
// provenance/content/process identities, complete cold exclusion and readiness
// remain required before the protected host coordinator may change ownership.
func RequireContainerExecution(ctx context.Context, inventory *Containers, images []*Images) (*Execution, error) {
	if ctx == nil || ctx.Err() != nil || inventory == nil || digest([]byte(inventory.Body())) != inventory.SHA256() || !shaPattern.MatchString(inventory.SHA256()) || len(images) == 0 || len(images) > 12 {
		return nil, reject("explicit execution evidence")
	}
	type binding struct {
		doc       imageDocument
		compose   composeExecution
		raw       []byte
		defaults  map[string]executionConfig
		hash      string
		imageHash string
	}
	bindings := []binding{}
	hashes := []string{}
	seen := map[string]bool{}
	projects := map[string]bool{}
	architecture := ""
	for _, proof := range images {
		if proof == nil || !shaPattern.MatchString(proof.SHA256()) || digest([]byte(proof.Body())) != proof.SHA256() || seen[proof.SHA256()] || uniqueJSON(proof.compose) != nil || uniqueJSON(proof.imageInspect) != nil {
			return nil, reject("opaque complete image execution evidence")
		}
		var d imageDocument
		var c composeExecution
		if json.Unmarshal([]byte(proof.Body()), &d) != nil || json.Unmarshal(proof.compose, &c) != nil || d.Version != "jobseek.crawler-release-images/v1" || !servicePattern.MatchString(d.Project) || c.Name != d.Project || !shaPattern.MatchString(d.FileEvidenceSHA256) || d.ComposeSHA256 != digest(proof.compose) || len(c.Services) != len(d.Services) || len(d.Services) == 0 || len(d.Services) > 128 || (d.Architecture != "amd64" && d.Architecture != "arm64") || (architecture != "" && architecture != d.Architecture) {
			return nil, reject("bound resolved execution evidence")
		}
		for name, image := range d.Services {
			if !servicePattern.MatchString(name) || !pinnedReferencePattern.MatchString(image.Reference) || !localImageIDPattern.MatchString(image.ImageID) || c.Services[name].Image != image.Reference {
				return nil, reject("exact declared execution service/image set")
			}
		}
		architecture = d.Architecture
		var inspected []struct {
			ID     string `json:"Id"`
			Config json.RawMessage
		}
		if json.Unmarshal(proof.imageInspect, &inspected) != nil {
			return nil, reject("complete image execution defaults")
		}
		defaults := map[string]executionConfig{}
		for _, i := range inspected {
			config, err := decodeExecutionConfig(i.Config, false)
			if err != nil || !localImageIDPattern.MatchString(i.ID) {
				return nil, reject("complete immutable image defaults")
			}
			if old, ok := defaults[i.ID]; ok && !reflect.DeepEqual(old, config) {
				return nil, reject("conflicting immutable image defaults")
			}
			defaults[i.ID] = config
		}
		bindings = append(bindings, binding{d, c, proof.compose, defaults, proof.SHA256(), digest(proof.imageInspect)})
		seen[proof.SHA256()] = true
		hashes = append(hashes, proof.SHA256())
		projects[d.Project] = true
	}
	sort.Slice(bindings, func(i, j int) bool { return bindings[i].hash < bindings[j].hash })
	type verified struct {
		ID                    string `json:"id"`
		ImageEvidenceSHA256   string `json:"image_evidence_sha256"`
		ImageInspectionSHA256 string `json:"image_inspection_sha256"`
		SettingsSHA256        string `json:"settings_sha256"`
		UID                   uint32 `json:"configured_uid"`
		GID                   uint32 `json:"configured_gid"`
	}
	verifiedRows := []verified{}
	unaccounted, oneoffs := 0, 0
	for _, row := range inventory.rows {
		if !projects[row.Project] {
			unaccounted++
			continue
		}
		private, complete := inventory.commands[row.ID]
		if !complete || digest(private.config) != row.ConfigSHA256 || digest(private.host) != row.HostConfigSHA256 || digest(private.mounts) != row.MountsSHA256 {
			return nil, reject("bound private container settings")
		}
		knownImage := false
		for _, b := range bindings {
			if b.doc.Project == row.Project && b.doc.Services[row.Service].ImageID == row.ImageID {
				knownImage = true
			}
		}
		if !knownImage {
			return nil, reject("unaccounted project execution service/image")
		}
		if row.Oneoff || !row.OneoffKnown {
			if row.Running || row.Paused || row.Restarting || row.PID != 0 || (row.Status != "created" && row.Status != "exited") || row.RestartPolicy != "no" || row.MaximumRetryCount != 0 {
				return nil, reject("unverified one-off execution not contained")
			}
			oneoffs++
			continue
		}
		matched := false
		var settingsErr error
		for _, b := range bindings {
			image, ok := b.doc.Services[row.Service]
			if !ok || b.doc.Project != row.Project || image.ImageID != row.ImageID {
				continue
			}
			defaults, ok := b.defaults[row.ImageID]
			if !ok || b.compose.Services[row.Service].Image != image.Reference {
				return nil, reject("complete service/image execution binding")
			}
			settings, err := verifyExecutionSettings(row.Service, b.raw, b.compose, defaults, private)
			if err != nil {
				settingsErr = err // closed phase label only; never private values
				continue
			}
			body, _ := json.Marshal(settings)
			verifiedRows = append(verifiedRows, verified{row.ID, b.hash, b.imageHash, digest(body), settings.UID, settings.GID})
			matched = true
			break
		}
		if !matched {
			if settingsErr != nil {
				return nil, settingsErr
			}
			return nil, reject("no release matches container execution")
		}
	}
	if ctx.Err() != nil {
		return nil, reject("execution verification cancelled")
	}
	sort.Strings(hashes)
	body, err := json.Marshal(struct {
		Version             string     `json:"version"`
		Scope               string     `json:"scope"`
		RuntimeAdmission    bool       `json:"runtime_admission"`
		InventorySHA256     string     `json:"inventory_sha256"`
		ImageEvidenceSHA256 []string   `json:"image_evidence_sha256"`
		Verified            []verified `json:"verified_containers"`
		Unaccounted         int        `json:"unaccounted_containers"`
		ContainedOneoffs    int        `json:"contained_oneoffs"`
	}{"jobseek.crawler-container-execution/v1", "declared command/environment/configured numeric user/working directory/bind/local-volume/tmpfs identities", false, inventory.SHA256(), hashes, verifiedRows, unaccounted, oneoffs})
	if err != nil {
		return nil, reject("canonical container execution")
	}
	return &Execution{string(body), digest(body)}, nil
}
