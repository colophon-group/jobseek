package main

import "testing"

func TestHeapLimitSignalSurvivesTruncatedAndSplitProcessLogs(t *testing.T) {
	logs := &boundedBuffer{limit: 4}
	_, _ = logs.Write([]byte("initial output fills the bounded log"))
	marker := []byte(lightpandaHeapLimitLog)
	for start := 0; start < len(marker); start += 7 {
		_, _ = logs.Write(marker[start:min(start+7, len(marker))])
	}
	if !logs.heapLimitReached() || len(logs.String()) != 4 {
		t.Fatal("heap failure was lost after bounded log truncation")
	}
}
