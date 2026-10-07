package worker

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestHRManagerJoinMatchesActualPython(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Name, Board, Feed string
			Error             bool
			Jobs              []map[string]any
		}
	}
	raw, err := os.ReadFile("testdata/python_hr_manager.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Cases) != 17 {
		t.Fatal("actual Python HR Manager evidence missing", err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			feed, err := parseRSSProvider([]byte(c.Feed), "hr_manager")
			if err == nil {
				feed, err = attachHRManagerPositions(c.Board, feed, "fixture")
			}
			if (err != nil) != c.Error || len(feed.Jobs) != len(c.Jobs) {
				t.Fatal("HR Manager terminal inventory differs", err, c.Error, len(feed.Jobs), len(c.Jobs))
			}
			for i, job := range feed.Jobs {
				actual := map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "employment_type": job.EmploymentType, "language": job.Language, "source_identity": job.SourceIdentity, "metadata": job.Metadata, "date_posted": job.DatePosted}
				b, _ := json.Marshal(actual)
				var decoded map[string]any
				_ = json.Unmarshal(b, &decoded)
				for key, value := range decoded {
					if !reflect.DeepEqual(value, c.Jobs[i][key]) {
						t.Errorf("Python HR Manager %s differs: got=%v want=%v", key, value, c.Jobs[i][key])
					}
				}
			}
		})
	}
}
