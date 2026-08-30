package chromiumservice

// HealthReason is a closed, label-safe service state. It never contains host,
// endpoint, digest, assignment, or provider error text.
type HealthReason string

const (
	HealthReady             HealthReason = "ready"
	HealthInitializing      HealthReason = "initializing"
	HealthProcessDown       HealthReason = "process_down"
	HealthPinsMismatch      HealthReason = "pins_mismatch"
	HealthProcessUnhealthy  HealthReason = "process_unhealthy"
	HealthResourceLimit     HealthReason = "resource_limit"
	HealthCleanupFailed     HealthReason = "cleanup_failed"
	HealthRecyclePending    HealthReason = "recycle_pending"
	HealthCapacityExhausted HealthReason = "capacity_exhausted"
	HealthShuttingDown      HealthReason = "shutting_down"
)

// RecycleReason is deliberately closed. A provider may not inject arbitrary
// process or origin information into lifecycle state.
type RecycleReason string

const (
	RecycleNone            RecycleReason = "none"
	RecycleProcessAge      RecycleReason = "process_age"
	RecycleRSSLimit        RecycleReason = "rss_limit"
	RecycleSessionLeak     RecycleReason = "session_leak"
	RecycleTargetLeak      RecycleReason = "target_leak"
	RecycleProcessCrash    RecycleReason = "process_crash"
	RecycleFailedHealth    RecycleReason = "failed_health"
	RecycleCleanupFailure  RecycleReason = "cleanup_failure"
	RecycleProtocolFailure RecycleReason = "protocol_failure"
	RecycleTaskLimit       RecycleReason = "task_limit"
)

// ProcessSnapshot contains only bounded numeric and Boolean process state.
// Implementations must derive PinsExact from the immutable image, browser, and
// client identifiers in Config without returning those identifiers here.
type ProcessSnapshot struct {
	Live            bool
	Healthy         bool
	PinsExact       bool
	AgeMS           uint64
	RSSBytes        uint64
	OpenSessions    uint32
	OpenTargets     uint32
	PIDs            uint32
	FileDescriptors uint32
	Sockets         uint32
}

// Health is local state only; this package exposes no health listener.
type Health struct {
	Live           bool
	Ready          bool
	Reason         HealthReason
	ActiveSessions uint32
	Capacity       uint32
	LastCleanupOK  bool
	RecycleReason  RecycleReason
	CompletedTasks uint64
}

func (service *Service) Health() Health {
	if service == nil {
		return Health{Reason: HealthInitializing, RecycleReason: RecycleNone}
	}
	service.mu.Lock()
	defer service.mu.Unlock()
	return service.healthLocked()
}

func (service *Service) healthLocked() Health {
	health := Health{
		Live:           service.snapshot.Live,
		Reason:         HealthReady,
		ActiveSessions: service.active,
		Capacity:       service.config.MaxConcurrency,
		LastCleanupOK:  service.lastCleanupOK,
		RecycleReason:  service.recycleReason,
		CompletedTasks: service.completedTasks,
	}
	switch {
	case !service.snapshot.Live:
		health.Reason = HealthProcessDown
	case !service.snapshot.PinsExact:
		health.Reason = HealthPinsMismatch
	case !service.snapshot.Healthy:
		health.Reason = HealthProcessUnhealthy
	case service.snapshotExceedsLimits(service.snapshot):
		health.Reason = HealthResourceLimit
	case !service.lastCleanupOK:
		health.Reason = HealthCleanupFailed
	case service.shuttingDown:
		health.Reason = HealthShuttingDown
	case service.recycleReason != RecycleNone:
		health.Reason = HealthRecyclePending
	case service.active >= service.config.MaxConcurrency:
		health.Reason = HealthCapacityExhausted
	default:
		health.Ready = true
	}
	return health
}

func (service *Service) snapshotExceedsLimits(snapshot ProcessSnapshot) bool {
	return snapshot.AgeMS >= service.config.MaxProcessAgeMS ||
		snapshot.RSSBytes > service.config.MaxRSSBytes ||
		snapshot.OpenSessions > service.active ||
		snapshot.OpenTargets > service.active ||
		snapshot.OpenTargets > service.config.MaxTargets ||
		snapshot.PIDs > service.config.MaxPIDs ||
		snapshot.FileDescriptors > service.config.MaxFileDescriptors ||
		snapshot.Sockets > service.config.MaxSockets
}

func (service *Service) snapshotRecycleReason(snapshot ProcessSnapshot) RecycleReason {
	switch {
	case !snapshot.Live:
		return RecycleProcessCrash
	case !snapshot.Healthy:
		return RecycleFailedHealth
	case snapshot.AgeMS >= service.config.MaxProcessAgeMS:
		return RecycleProcessAge
	case snapshot.RSSBytes > service.config.MaxRSSBytes:
		return RecycleRSSLimit
	case snapshot.OpenSessions > service.active:
		return RecycleSessionLeak
	case snapshot.OpenTargets > service.active || snapshot.OpenTargets > service.config.MaxTargets:
		return RecycleTargetLeak
	case snapshot.PIDs > service.config.MaxPIDs ||
		snapshot.FileDescriptors > service.config.MaxFileDescriptors ||
		snapshot.Sockets > service.config.MaxSockets:
		return RecycleFailedHealth
	default:
		return RecycleNone
	}
}
