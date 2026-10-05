package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"golang.org/x/sys/unix"
)

const cohortFileLimit = 1 << 20

var cohortID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

type OwnershipAdminConfig struct {
	database, redis, source, cohortFile, digest string
	epoch                                       int64
	inspect                                     bool
}

// OwnershipStageIdentity exposes only bounded public document identities. It
// contains neither board configuration nor credentials, and grants no activation.
type OwnershipStageIdentity struct {
	Version        string `json:"version"`
	State          string `json:"state"`
	SourceRevision string `json:"source_revision"`
	RoutingEpoch   int64  `json:"routing_epoch"`
	PlanSHA256     string `json:"plan_sha256"`
	ProjectionSHA1 string `json:"projection_sha1"`
	Members        int    `json:"members"`
	DetailBoards   int    `json:"detail_boards,omitempty"`
}

// Preserve the rejection contract while reporting only a fixed phase/category.
// Database messages, environment values and board configurations stay private.
func ownershipAdminFailure(phase string, cause error) error {
	diagnostic := claimRunError("ownership_"+phase, cause).(*ClaimRunError)
	log.Printf("ordinary ownership administration %s: %s", phase, diagnostic.Kind)
	return ErrStartup
}

// ReadOwnershipAdminConfig binds a distinct protected administrative mode to the
// compiled source and caller-attested current epoch. No defaults select an epoch
// or operation. Worker-mode environments cannot accidentally run staging.
func ReadOwnershipAdminConfig(getenv func(string) string, installed string, inspect bool) (OwnershipAdminConfig, error) {
	var c OwnershipAdminConfig
	mode := "stage-ownership"
	if inspect {
		mode = "inspect-ownership"
	}
	if getenv == nil || !sourcePattern.MatchString(installed) || getenv("ORDINARY_GO_WORKER_MODE") != mode || getenv("ORDINARY_OWNERSHIP_SOURCE_REVISION") != installed || getenv("ORDINARY_OWNERSHIP_PROJECTION_SHA1") != "" {
		return c, ErrStartup
	}
	c.database, c.redis, c.source, c.inspect = getenv("LOCAL_DATABASE_URL"), getenv("REDIS_URL"), installed, inspect
	epoch := getenv("ORDINARY_OWNERSHIP_ROUTING_EPOCH")
	number, err := strconv.ParseInt(epoch, 10, 64)
	if err != nil || number < 1 || number > 9999999999999 || strconv.FormatInt(number, 10) != epoch || c.database == "" || c.redis == "" {
		return OwnershipAdminConfig{}, ErrStartup
	}
	c.epoch, c.digest, c.cohortFile = number, getenv("ORDINARY_OWNERSHIP_PLAN_SHA256"), getenv("ORDINARY_GO_COHORT_FILE")
	if inspect {
		if !planPattern.MatchString(c.digest) || c.cohortFile != "" {
			return OwnershipAdminConfig{}, ErrStartup
		}
	} else if c.digest != "" || !filepath.IsAbs(c.cohortFile) || strings.ContainsRune(c.cohortFile, 0) {
		return OwnershipAdminConfig{}, ErrStartup
	}
	return c, nil
}

func readProtectedOwnershipFile(path string, limit int64) ([]byte, error) {
	if !filepath.IsAbs(path) || strings.ContainsRune(path, 0) || limit < 1 {
		return nil, ErrStartup
	}
	// O_NONBLOCK also prevents a raced FIFO/device replacement from hanging the
	// one-shot tool; O_NOFOLLOW rejects a symlink before opening its target.
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, ErrStartup
	}
	file := os.NewFile(uintptr(fd), "ordinary-cohort")
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0o022 != 0 || info.Size() > limit {
		return nil, ErrStartup
	}
	data, err := io.ReadAll(io.LimitReader(file, limit+1))
	if err != nil || len(data) == 0 || int64(len(data)) > limit {
		return nil, ErrStartup
	}
	return data, nil
}

type ownershipCohort struct {
	Monitors []string
	Details  []string
}

func readOwnershipCohort(path string) ([]string, error) {
	cohort, err := readOwnershipSelection(path)
	if err != nil || len(cohort.Details) != 0 {
		return nil, ErrStartup
	}
	return cohort.Monitors, nil
}

// The historical array selects monitors only. Details require an explicit
// versioned selection, and canonical admission still runs inside staging.
func readOwnershipSelection(path string) (ownershipCohort, error) {
	var cohort ownershipCohort
	data, err := readProtectedOwnershipFile(path, cohortFileLimit)
	if err != nil {
		return cohort, ErrStartup
	}
	data = bytes.TrimSpace(data)
	if len(data) == 0 {
		return cohort, ErrStartup
	}
	if data[0] == '[' {
		if json.Unmarshal(data, &cohort.Monitors) != nil {
			return ownershipCohort{}, ErrStartup
		}
	} else {
		decoder := json.NewDecoder(bytes.NewReader(data))
		token, err := decoder.Token()
		if err != nil || token != json.Delim('{') {
			return ownershipCohort{}, ErrStartup
		}
		seen := map[string]bool{}
		var version string
		for decoder.More() {
			token, err := decoder.Token()
			key, ok := token.(string)
			if err != nil || !ok || seen[key] {
				return ownershipCohort{}, ErrStartup
			}
			seen[key] = true
			switch key {
			case "version":
				err = decoder.Decode(&version)
			case "monitors":
				err = decoder.Decode(&cohort.Monitors)
			case "details":
				err = decoder.Decode(&cohort.Details)
			default:
				return ownershipCohort{}, ErrStartup
			}
			if err != nil {
				return ownershipCohort{}, ErrStartup
			}
		}
		token, err = decoder.Token()
		if err != nil || token != json.Delim('}') || version != "jobseek.ordinary.cohort/v1" || len(seen) != 3 || cohort.Details == nil {
			return ownershipCohort{}, ErrStartup
		}
		if _, err := decoder.Token(); err != io.EOF {
			return ownershipCohort{}, ErrStartup
		}
	}
	if len(cohort.Monitors) < 1 || len(cohort.Monitors) > 20000 || len(cohort.Details) > 20000 {
		return ownershipCohort{}, ErrStartup
	}
	monitors := make(map[string]bool, len(cohort.Monitors))
	for _, id := range cohort.Monitors {
		if !cohortID.MatchString(id) || monitors[id] {
			return ownershipCohort{}, ErrStartup
		}
		monitors[id] = true
	}
	details := make(map[string]bool, len(cohort.Details))
	for _, id := range cohort.Details {
		if !cohortID.MatchString(id) || details[id] {
			return ownershipCohort{}, ErrStartup
		}
		details[id] = true
	}
	return cohort, nil
}

// RunOwnershipAdmin stages or inspects an exact candidate only. It never claims,
// allocates an epoch, activates a plan, writes a Redis ownership projection or
// loads Python/HTTP/model execution. The supported cold protocol owns activation.
func RunOwnershipAdmin(ctx context.Context, c OwnershipAdminConfig) (*OwnershipStageIdentity, error) {
	if c.database == "" || c.redis == "" || !sourcePattern.MatchString(c.source) || c.epoch < 1 || c.epoch > 9999999999999 {
		return nil, ErrStartup
	}
	var cohort ownershipCohort
	var err error
	if !c.inspect {
		cohort, err = readOwnershipSelection(c.cohortFile)
		if err != nil {
			return nil, ownershipAdminFailure("cohort", err)
		}
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	client, err := queue.Open(c.redis, queue.Settings{LeaseTTL: 600 * time.Second, MaxDomains: 10})
	if err != nil {
		return nil, ownershipAdminFailure("queue", err)
	}
	defer client.Close()
	authority, err := queue.OpenAuthority(ctx, c.database, client, c.epoch)
	if err != nil {
		return nil, ownershipAdminFailure("authority", err)
	}
	defer authority.Close()
	var plan *queue.OwnershipPlan
	phase := "stage"
	if c.inspect {
		phase = "inspect"
		plan, err = authority.InspectStagedOwnership(ctx, c.digest, c.source)
	} else {
		plan, err = authority.StageOwnership(ctx, c.source, cohort.Monitors, cohort.Details)
		if err == nil {
			// A second fresh readback declines if a canonical configuration changed
			// after staging. The retained staged document grants no queue authority.
			phase = "readback"
			plan, err = authority.InspectStagedOwnership(ctx, plan.SHA256(), c.source)
		}
	}
	if err != nil {
		return nil, ownershipAdminFailure(phase, err)
	}
	return &OwnershipStageIdentity{"jobseek.ordinary.stage-identity/v1", "staged", plan.SourceRevision(), plan.Epoch(), plan.SHA256(), plan.ProjectionSHA1(), plan.MemberCount(), plan.DetailBoardCount()}, nil
}
