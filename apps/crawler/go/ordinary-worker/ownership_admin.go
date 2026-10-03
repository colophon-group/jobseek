package worker

import (
	"context"
	"encoding/json"
	"io"
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

func readOwnershipCohort(path string) ([]string, error) {
	data, err := readProtectedOwnershipFile(path, cohortFileLimit)
	var ids []string
	if err != nil || len(data) > cohortFileLimit || json.Unmarshal(data, &ids) != nil || len(ids) < 1 || len(ids) > 20000 {
		return nil, ErrStartup
	}
	seen := make(map[string]bool, len(ids))
	for _, id := range ids {
		if !cohortID.MatchString(id) || seen[id] {
			return nil, ErrStartup
		}
		seen[id] = true
	}
	return ids, nil
}

// RunOwnershipAdmin stages or inspects an exact candidate only. It never claims,
// allocates an epoch, activates a plan, writes a Redis ownership projection or
// loads Python/HTTP/model execution. The supported cold protocol owns activation.
func RunOwnershipAdmin(ctx context.Context, c OwnershipAdminConfig) (*OwnershipStageIdentity, error) {
	if c.database == "" || c.redis == "" || !sourcePattern.MatchString(c.source) || c.epoch < 1 || c.epoch > 9999999999999 {
		return nil, ErrStartup
	}
	var ids []string
	var err error
	if !c.inspect {
		ids, err = readOwnershipCohort(c.cohortFile)
		if err != nil {
			return nil, ErrStartup
		}
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	client, err := queue.Open(c.redis, queue.Settings{LeaseTTL: 600 * time.Second, MaxDomains: 10})
	if err != nil {
		return nil, ErrStartup
	}
	defer client.Close()
	authority, err := queue.OpenAuthority(ctx, c.database, client, c.epoch)
	if err != nil {
		return nil, ErrStartup
	}
	defer authority.Close()
	var plan *queue.OwnershipPlan
	if c.inspect {
		plan, err = authority.InspectStagedOwnership(ctx, c.digest, c.source)
	} else {
		plan, err = authority.StageGreenhouseOwnership(ctx, c.source, ids)
		if err == nil {
			// A second fresh readback declines if a canonical configuration changed
			// after staging. The retained staged document grants no queue authority.
			plan, err = authority.InspectStagedOwnership(ctx, plan.SHA256(), c.source)
		}
	}
	if err != nil {
		return nil, ErrStartup
	}
	return &OwnershipStageIdentity{"jobseek.ordinary.stage-identity/v1", "staged", plan.SourceRevision(), plan.Epoch(), plan.SHA256(), plan.ProjectionSHA1(), plan.MemberCount()}, nil
}
