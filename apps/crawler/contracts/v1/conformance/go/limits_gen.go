// Code generated from ../../limits.json by tools/generate_limits.py. DO NOT EDIT.

package conformance

import runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"

func generatedHardLimits() *runtimev1.Limits {
	return &runtimev1.Limits{
		MaxFrameBytes:           1048576,
		MaxInlineBodyBytes:      8388608,
		MaxArtifactChunkBytes:   8388608,
		MaxMonitorBatches:       4096,
		MaxOutputItems:          100000,
		MaxInFlightFrames:       64,
		MaxActiveDurationMs:     900000,
		MaxBrowserActions:       256,
		MaxBrowserCaptures:      64,
		MaxBrowserEvaluations:   64,
		MaxHttpTransferBytes:    16777216,
		MaxBrowserTransferBytes: 67108864,
		MaxExecutionFrames:      4096,
		MaxArtifactCount:        64,
		MaxArtifactTotalBytes:   67108864,
		MaxRetryAfterMs:         604800000,
	}
}

type limitValue struct {
	name           string
	value, ceiling uint64
}

func limitValues(l *runtimev1.Limits) []limitValue {
	return []limitValue{
		{"max_frame_bytes", uint64(l.GetMaxFrameBytes()), uint64(hardLimits.GetMaxFrameBytes())},
		{"max_inline_body_bytes", uint64(l.GetMaxInlineBodyBytes()), uint64(hardLimits.GetMaxInlineBodyBytes())},
		{"max_artifact_chunk_bytes", uint64(l.GetMaxArtifactChunkBytes()), uint64(hardLimits.GetMaxArtifactChunkBytes())},
		{"max_monitor_batches", uint64(l.GetMaxMonitorBatches()), uint64(hardLimits.GetMaxMonitorBatches())},
		{"max_output_items", uint64(l.GetMaxOutputItems()), uint64(hardLimits.GetMaxOutputItems())},
		{"max_in_flight_frames", uint64(l.GetMaxInFlightFrames()), uint64(hardLimits.GetMaxInFlightFrames())},
		{"max_active_duration_ms", uint64(l.GetMaxActiveDurationMs()), uint64(hardLimits.GetMaxActiveDurationMs())},
		{"max_browser_actions", uint64(l.GetMaxBrowserActions()), uint64(hardLimits.GetMaxBrowserActions())},
		{"max_browser_captures", uint64(l.GetMaxBrowserCaptures()), uint64(hardLimits.GetMaxBrowserCaptures())},
		{"max_browser_evaluations", uint64(l.GetMaxBrowserEvaluations()), uint64(hardLimits.GetMaxBrowserEvaluations())},
		{"max_http_transfer_bytes", uint64(l.GetMaxHttpTransferBytes()), uint64(hardLimits.GetMaxHttpTransferBytes())},
		{"max_browser_transfer_bytes", uint64(l.GetMaxBrowserTransferBytes()), uint64(hardLimits.GetMaxBrowserTransferBytes())},
		{"max_execution_frames", uint64(l.GetMaxExecutionFrames()), uint64(hardLimits.GetMaxExecutionFrames())},
		{"max_artifact_count", uint64(l.GetMaxArtifactCount()), uint64(hardLimits.GetMaxArtifactCount())},
		{"max_artifact_total_bytes", uint64(l.GetMaxArtifactTotalBytes()), uint64(hardLimits.GetMaxArtifactTotalBytes())},
		{"max_retry_after_ms", uint64(l.GetMaxRetryAfterMs()), uint64(hardLimits.GetMaxRetryAfterMs())},
	}
}
