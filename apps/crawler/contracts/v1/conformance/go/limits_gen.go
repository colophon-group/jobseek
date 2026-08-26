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
