package worker

import (
	"compress/gzip"
	"encoding/json"
	"io"
	"os"
	"reflect"
	"testing"
)

func proxyHealthSnapshot(h proxyHealth) []any {
	return []any{h.failures, h.until, h.due, h.inFlight, h.generation}
}

func proxyPoolSnapshot(p *proxyPool) map[string]any {
	global := make([]any, len(p.global))
	for i, h := range p.global {
		global[i] = proxyHealthSnapshot(h)
	}
	origins := []any{}
	for entry := p.lru.Front(); entry != nil; entry = entry.Next() {
		state := entry.Value.(*proxyOriginState)
		origins = append(origins, []any{state.key.slot, state.key.origin, proxyHealthSnapshot(state.health)})
	}
	return map[string]any{"cursor": p.cursor, "global": global, "origins": origins, "evidence": p.evidence}
}

func jsonValue(t *testing.T, value any) any {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var result any
	if err = json.Unmarshal(data, &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func TestProxyPoolActualPythonPolicy(t *testing.T) {
	file, err := os.Open("testdata/python_proxy_policy.json.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	reader, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	data, err := io.ReadAll(io.LimitReader(reader, 8<<20))
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Cases []struct {
			Name   string
			Size   int
			Forced *int
			Steps  []struct {
				Action, ID, Origin, Reason string
				At                         float64
				Result, State              any
			}
		}
	}
	if err = json.Unmarshal(data, &corpus); err != nil {
		t.Fatal(err)
	}
	if len(corpus.Cases) != 34 {
		t.Fatalf("unexpected Python corpus size: %d", len(corpus.Cases))
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			now, forced := 0.0, -1
			if c.Forced != nil {
				forced = *c.Forced
			}
			pool, err := newProxyPool(c.Size, forced, func() float64 { return now })
			if err != nil {
				t.Fatal(err)
			}
			leases := map[string]*proxySelection{}
			for i, step := range c.Steps {
				var result any
				switch step.Action {
				case "time":
					now = step.At
				case "select":
					s, err := pool.selectEndpoint(step.Origin)
					if err != nil {
						if err != errProxyPoolExhausted {
							t.Fatal(err)
						}
						result = "exhausted"
					} else {
						leases[step.ID] = s
						result = []any{s.slot, s.halfOpen, s.globalGeneration, s.originGeneration, s.globalProbe, s.originProbe}
					}
				case "failure":
					pool.failure(leases[step.ID], step.Origin, step.Reason)
				case "success":
					pool.success(leases[step.ID])
				case "abandon":
					pool.abandon(leases[step.ID])
				default:
					t.Fatalf("unknown Python operation %q", step.Action)
				}
				if got := jsonValue(t, result); !reflect.DeepEqual(got, step.Result) {
					t.Fatalf("step %d %s result got %v want %v", i, step.Action, got, step.Result)
				}
				if got := jsonValue(t, proxyPoolSnapshot(pool)); !reflect.DeepEqual(got, step.State) {
					t.Fatalf("step %d %s state got %v want %v", i, step.Action, got, step.State)
				}
			}
		})
	}
}
