package worker

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
)

// Synthetic fixture values never contain a provider key or captured job data.
func encryptedInitialFixture(t *testing.T, plain string, fixed bool) string {
	t.Helper()
	key, iv := []byte("fixture-key-1234"), []byte("0123456789abcdef")
	padding := aes.BlockSize - len(plain)%aes.BlockSize
	data := append([]byte(plain), make([]byte, padding)...)
	for i := len(data) - padding; i < len(data); i++ {
		data[i] = byte(padding)
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		t.Fatal(err)
	}
	cipher.NewCBCEncrypter(block, iv).CryptBlocks(data, data)
	value := base64.StdEncoding.EncodeToString(data)
	if !fixed {
		value += string(iv)
	}
	return value
}

func TestRealInitialResponseDecryptPersistenceAndFailureAuthority(t *testing.T) {
	for _, proxy := range []bool{false, true} {
		for _, mode := range []string{"suffix", "fixed", "plain-tail", "encrypted-tail", "malformed", "reserved"} {
			t.Run(fmt.Sprintf("%s/proxy=%t", mode, proxy), func(t *testing.T) {
				decrypt := map[string]any{"key": "fixture-key-1234"}
				if mode == "fixed" {
					decrypt["iv_mode"] = "fixed:0123456789abcdef"
				}
				md := map[string]any{"api_url": "https://example.com/api", "json_path": "Data.jobs", "total_path": "total", "url_field": "url", "fields": map[string]any{"title": "title", "description": "description", "locations": "city"}, "scraper_type": "skip", "response_decrypt": decrypt, "transport_attempts": 1}
				tail := mode == "plain-tail" || mode == "encrypted-tail"
				if tail {
					md["pagination"] = map[string]any{"param_name": "page", "start_value": 1, "max_pages": 2}
				}
				if proxy {
					md["proxy"] = true
				}
				raw, _ := json.Marshal(md)
				f := privateRichPipelineFixture(t, "api_sniffer", string(raw))
				ctx := context.Background()
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
				claim, circuits := claimFixture(t, f)
				calls := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(403)
						return
					}
					plain := fmt.Sprintf(`{"jobs":[{"url":"https://example.com/job/%s/%d","title":"Engineer","description":"<p>Build systems.</p>","city":"Zurich"}]}`, f.company, calls)
					total := 1
					if tail {
						total = 2
					}
					// The original tolerates a one-item count difference; use a
					// larger known gap to prove disappearance is suppressed.
					if mode == "encrypted-tail" {
						total = 3
					} else if mode == "malformed" {
						total = 2
					}
					var data any = json.RawMessage(plain)
					if calls == 1 || mode == "encrypted-tail" {
						data = encryptedInitialFixture(t, plain, mode == "fixed")
					}
					if mode == "malformed" {
						data = "invalid-ciphertext"
					}
					body, _ := json.Marshal(map[string]any{"total": total, "Data": data})
					w.Write(body)
				}))
				if proxy {
					client = credentialedProxyFixture(t, client)
				}
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("encrypted initial response did not settle", err)
				}
				var active, reserved bool
				var missing, failures, count int
				if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &count); err != nil {
					t.Fatal(err)
				}
				complete := mode == "suffix" || mode == "fixed" || mode == "plain-tail"
				if complete {
					wanted := 1
					if tail {
						wanted = 2
					}
					if active || missing != 4 || failures != 0 || reserved || count != wanted+1 || result.Batches.Inserted != wanted || calls != wanted {
						t.Fatal("complete encrypted inventory lost canonical effects")
					}
					var title, description string
					if err := f.pg.QueryRow(ctx, `SELECT p.titles[1],d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.source_url=$2`, f.board, fmt.Sprintf("https://example.com/job/%s/1", f.company)).Scan(&title, &description); err != nil || title != "Engineer" || description != "<p>Build systems.</p>" {
						t.Fatal("decrypted canonical content changed", err)
					}
				} else {
					wantedCount := 1
					if mode == "encrypted-tail" {
						// Initial rows retain insertion authority, while the known
						// incomplete count suppresses absence, matching Python.
						wantedCount = 2
					}
					if !active || missing != 3 || count != wantedCount || failures != 0 || reserved != (mode == "reserved") {
						t.Fatal("incomplete or reserved encrypted inventory changed canonical authority")
					}
				}
				assertRichDeadlineAndLease(t, f, "api_sniffer")
			})
		}
	}
}
