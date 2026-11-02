package main

import (
	"testing"
	"time"
)

func TestExporterSettingsPreserveOperatorBatchAndInterval(t *testing.T) {
	t.Setenv("LOCAL_DATABASE_URL", "postgresql://fixture.invalid/crawler")
	t.Setenv("TYPESENSE_OPERATIONS_KEY", "fixture")
	t.Setenv("TYPESENSE_PROTOCOL", "http")
	t.Setenv("TYPESENSE_HOST", "fixture.invalid")
	t.Setenv("TYPESENSE_PORT", "8108")
	t.Setenv("METRICS_PORT", "")
	for _, test := range []struct {
		name, batch, interval string
		wantBatch             int
		wantInterval          time.Duration
		valid                 bool
	}{
		{"defaults", "", "", 2000, time.Second, true},
		{"operator overrides", "20", "3", 20, 3 * time.Second, true},
		{"upper bounds", "2000", "3600", 2000, time.Hour, true},
		{"zero batch", "0", "1", 0, 0, false},
		{"oversize batch", "2001", "1", 0, 0, false},
		{"negative interval", "20", "-1", 0, 0, false},
		{"oversize interval", "20", "3601", 0, 0, false},
		{"malformed interval", "20", "nan", 0, 0, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			t.Setenv("EXPORT_BATCH_LIMIT", test.batch)
			t.Setenv("EXPORT_INTERVAL", test.interval)
			settings, err := loadExporterSettings()
			if (err == nil) != test.valid {
				t.Fatalf("settings error = %v, valid = %v", err, test.valid)
			}
			if test.valid && (settings.BatchLimit != test.wantBatch || settings.Interval != test.wantInterval) {
				t.Fatalf("settings = %d/%v, want %d/%v", settings.BatchLimit, settings.Interval, test.wantBatch, test.wantInterval)
			}
		})
	}
}
