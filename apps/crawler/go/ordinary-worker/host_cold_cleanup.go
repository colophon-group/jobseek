package worker

import (
	"bytes"
	"context"
	"encoding/json"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5/pgxpool"
)

type HostColdCleanupEvidence struct{ body, digest string }

func (e *HostColdCleanupEvidence) Payload() string { return e.body }
func (e *HostColdCleanupEvidence) SHA256() string  { return e.digest }

// ObserveHostColdB0Cleanup joins an original live host flock, checked SQL/Redis
// scopes and completed restoration ancestry to a fresh zero-fence/tombstone
// observation. It retains evidence only. Producer stop, sentinel/executable/
// immutable image checks and the durable delegated command remain separate;
// neither its JSON nor its digest grants permission for later cleanup effects.
func ObserveHostColdB0Cleanup(ctx context.Context, pool *pgxpool.Pool, restoredRequestSHA, targetSHA, luaSHA string) (*HostColdCleanupEvidence, error) {
	if ctx == nil || CheckHostMutationScope(ctx) != nil || !planPattern.MatchString(restoredRequestSHA) || !planPattern.MatchString(targetSHA) || !planPattern.MatchString(luaSHA) {
		return nil, errHostPreflight
	}
	s, _ := ctx.Value(hostColdPhaseKey{}).(*hostColdPhaseScope)
	if s == nil {
		return nil, errHostPreflight
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	redis, _ := ctx.Value(hostColdRedisKey{}).(*hostColdRedisScope)
	guard := func() error {
		if CheckHostMutationScope(ctx) != nil || s.check(ctx, pool) != nil || redis == nil || redis.check(ctx, s) != nil {
			return errHostPreflight
		}
		return nil
	}
	if guard() != nil {
		return nil, errHostPreflight
	}
	rawRequest, err := s.store.read("cold-request-"+restoredRequestSHA+".json", false)
	r, decodeErr := decodeHostColdPhase(rawRequest, restoredRequestSHA, s.info.Binding)
	if err != nil || decodeErr != nil || r.Operation != "cold-ordinary-rollback-inspect" || r.RedisEndpointSHA256 != redis.endpointSHA || r.RedisInstanceSHA256 != redis.instanceSHA {
		return nil, errHostPreflight
	}
	_, parent, err := s.predecessor(r)
	if err != nil || parent == nil || parent.Outcome != "completed" {
		return nil, errHostPreflight
	}
	_, inputs, err := s.config(r)
	if err != nil {
		return nil, errHostPreflight
	}
	result, resultBody, err := s.readResult(restoredRequestSHA, false)
	if err != nil || result.Outcome != "completed" || validateHostColdPhaseResult(r, result, inputs, parent) != nil {
		return nil, errHostPreflight
	}
	completed, err := s.store.read("cold-completed-"+restoredRequestSHA+".json", false)
	var completion hostColdPhaseCompletion
	if err != nil || canonicalHostDecode(completed, &completion) != nil || completion.Version != "jobseek.crawler-host-cold-completion/v1" || completion.Binding != r.Binding || completion.RequestSHA256 != restoredRequestSHA || completion.ResultSHA256 != hostDigest(resultBody) {
		return nil, errHostPreflight
	}
	index, err := s.store.read("cold-predecessor-"+completion.ResultSHA256+".json", false)
	if err != nil || !bytes.Equal(index, completed) {
		return nil, errHostPreflight
	}
	targetBody, err := s.store.read("cold-input-"+targetSHA, false)
	luaBody, luaErr := s.store.read("cold-input-"+luaSHA, false)
	if err != nil || luaErr != nil || hostDigest(luaBody) != luaSHA {
		return nil, errHostPreflight
	}
	target, err := queue.DecodeColdB0Target(string(targetBody), targetSHA, luaBody)
	if err != nil {
		return nil, errHostPreflight
	}
	observation, err := queue.ObserveColdB0Cleanup(ctx, pool, redis.client, r.OrdinaryRestorationPlanSHA256, r.Binding.SourceRevision, target)
	if err != nil || observation == nil || guard() != nil {
		return nil, errHostPreflight
	}
	// Re-read protected inputs around the live database/Redis observation.
	for name, expected := range map[string][]byte{
		"cold-request-" + restoredRequestSHA + ".json":          rawRequest,
		"cold-result-" + restoredRequestSHA + ".json":           resultBody,
		"cold-completed-" + restoredRequestSHA + ".json":        completed,
		"cold-predecessor-" + completion.ResultSHA256 + ".json": index,
		"cold-input-" + targetSHA:                               targetBody, "cold-input-" + luaSHA: luaBody,
	} {
		current, err := s.store.readColdPhaseLimit(name, false, 48<<20)
		if err != nil || !bytes.Equal(expected, current) {
			return nil, errHostPreflight
		}
	}
	info := s.info
	info.RedisEndpointSHA256, info.RedisInstanceSHA256 = redis.endpointSHA, redis.instanceSHA
	body, err := json.Marshal(struct {
		Version                  string               `json:"version"`
		Context                  HostColdPhaseContext `json:"context"`
		RestorationRequestSHA256 string               `json:"restoration_request_sha256"`
		RestorationResultSHA256  string               `json:"restoration_result_sha256"`
		SQLRedis                 json.RawMessage      `json:"sql_redis"`
		SQLRedisSHA256           string               `json:"sql_redis_sha256"`
		RuntimeAdmission         bool                 `json:"runtime_admission"`
	}{"jobseek.crawler-host-cold-cleanup-evidence/v1", info, restoredRequestSHA, completion.ResultSHA256, json.RawMessage(observation.Payload()), observation.SHA256(), false})
	if err != nil || guard() != nil {
		return nil, errHostPreflight
	}
	digest := hostDigest(body)
	if s.store.retain("cold-cleanup-observation-"+digest+".json", body, nil) != nil || guard() != nil {
		return nil, errHostPreflight
	}
	return &HostColdCleanupEvidence{string(body), digest}, nil
}
