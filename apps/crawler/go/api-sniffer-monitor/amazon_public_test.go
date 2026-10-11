package apisniffer

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
)

func TestAmazonCompleteOriginalPublicPartitionsAndAllFields(t *testing.T) {
	dir := os.Getenv("JOBSEEK_AMAZON_PUBLIC_REPLAY_DIR")
	if dir == "" {
		t.Skip("requires protected complete original streaming capture")
	}
	body, err := os.ReadFile(filepath.Join(dir, "index.json"))
	var index struct {
		OriginalSHA string `json:"original_capture_sha256"`
		Jobs        int
		Exchanges   []struct {
			URL, Method, File string
			Status            int
		}
	}
	if err != nil || json.Unmarshal(body, &index) != nil || index.Jobs != 21896 || len(index.Exchanges) != 465 || index.OriginalSHA != "8c2183270cd97de25dc17e4e6587b437bb6083d332cbf888c3840c707b8e1c10" {
		t.Fatal("complete original replay not bound")
	}
	read := func(name string) ([]byte, error) {
		if filepath.Base(name) != name {
			return nil, ErrInventory
		}
		f, err := os.Open(filepath.Join(dir, name))
		if err != nil {
			return nil, err
		}
		defer f.Close()
		reader, err := gzip.NewReader(f)
		if err != nil {
			return nil, err
		}
		defer reader.Close()
		return io.ReadAll(io.LimitReader(reader, (64<<20)+1))
	}
	responses := map[string]string{}
	remaining := map[string]int{}
	categoryFile := ""
	for _, x := range index.Exchanges {
		if x.Status == 200 {
			responses[x.URL] = x.File
			if strings.Contains(x.URL, "/search.json?") {
				remaining[x.URL]++
			} else {
				categoryFile = x.File
			}
		}
	}
	if categoryFile == "" {
		t.Fatal("original live category partition missing")
	}
	responses[AmazonCategoriesURL] = categoryFile
	remaining[AmazonCategoriesURL] = 1
	f, err := os.Open(filepath.Join(dir, "jobs.jsonl.gz"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	reader, err := gzip.NewReader(f)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	expected := map[string]map[string]any{}
	decoder := json.NewDecoder(reader)
	for {
		var job map[string]any
		err := decoder.Decode(&job)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal("complete original jobs unavailable", err)
		}
		expected[job["url"].(string)] = job
	}
	if len(expected) != 21896 {
		t.Fatal("original identities not unique")
	}
	var mu sync.Mutex
	calls := 0
	fetch := func(ctx context.Context, r Request) ([]byte, error) {
		u, err := url.Parse(r.URL)
		if err != nil {
			return nil, err
		}
		u.RawQuery = u.Query().Encode()
		key := u.String()
		mu.Lock()
		file := responses[key]
		left := remaining[key]
		remaining[key]--
		calls++
		mu.Unlock()
		if file == "" || left < 1 || r.Method != "GET" || r.Body != "" {
			return nil, fmt.Errorf("unbound original public query")
		}
		return read(file)
	}
	count := 0
	truncated, err := DiscoverAmazon(context.Background(), AmazonOptions{}, fetch, func(batch []map[string]any) error {
		for _, job := range batch {
			key := job["url"].(string)
			want, ok := expected[key]
			if !ok {
				return fmt.Errorf("unexpected or repeated public identity")
			}
			for k, v := range want {
				if v == nil {
					delete(want, k)
				}
			}
			body, _ := json.Marshal(job)
			var normal map[string]any
			if json.Unmarshal(body, &normal) != nil || !reflect.DeepEqual(normal, want) {
				return fmt.Errorf("original fields differ at item %d", count)
			}
			delete(expected, key)
			count++
		}
		return nil
	})
	if err != nil || truncated || count != 21896 || len(expected) != 0 || calls != 463 {
		t.Fatal("complete public inventory not preserved", err, truncated, count, len(expected), calls)
	}
	for _, left := range remaining {
		if left != 0 {
			t.Fatal("original partition request omitted")
		}
	}
	t.Log("all 21,896 original jobs and all 463 provider requests match (465 physical exchanges including category redirects)")
}
