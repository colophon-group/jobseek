package worker

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRealKIPTOriginalPDFCompletePartialReserved(t *testing.T) {
	dir := os.Getenv("JOBSEEK_LOCALIZED_HTTP_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires private original PDF capture")
	}
	raw, e := os.ReadFile(filepath.Join(dir, "native-localized-three-national-science-center-kharkiv-institute-of-physics-and-technology-vacancies-original-public-capture1-2026-10-10.json"))
	var capture localizedPublicCapture
	if e != nil || json.Unmarshal(raw, &capture) != nil {
		t.Fatal("private original capture unavailable")
	}
	urls := []string{}
	bodies := map[string][]byte{}
	for _, exchange := range capture.Exchanges {
		if exchange.Base64 != "" {
			body, e := base64.StdEncoding.DecodeString(exchange.Base64)
			if e != nil {
				t.Fatal("PDF capture invalid")
			}
			urls = append(urls, exchange.URL)
			bodies[exchange.URL] = body
		}
	}
	if len(urls) != 2 || fmt.Sprintf("%x", sha256.Sum256(bodies[urls[0]])) != "3847f0f64441f682a828a9f993f41ccc6fb891c85b5ddb24a6190e584b3114c2" {
		t.Fatal("reviewed public PDF identity differs")
	}
	for _, mode := range []string{"complete", "partial", "reserved"} {
		t.Run(mode, func(t *testing.T) {
			// Use the fixture's known locality to verify the configured original
			// location override reaches canonical storage, alongside the real PDF.
			f := privateRichPipelineFixtureURL(t, "kipt", `{"scraper_type":"skip","max_age_days":365,"default_location":"Zurich"}`, capture.Board.BoardURL)
			claim, circuits := claimFixture(t, f)
			client := &VerifiedHTTP{client: verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				url := "https://" + r.Host + r.URL.String()
				if url == capture.Board.BoardURL {
					fmt.Fprintf(w, `<html><a href="%s">Vacancies</a>`, urls[0])
					if mode == "partial" {
						fmt.Fprintf(w, `<a href="%s">Vacancies</a>`, urls[1])
					}
					fmt.Fprint(w, "</html>")
					return
				}
				body, ok := bodies[url]
				if !ok {
					t.Error("request left captured PDF inventory")
					w.WriteHeader(400)
					return
				}
				if mode == "reserved" {
					w.Header().Set("TDM-Reservation", "1")
				}
				w.Header().Set("Content-Type", "application/pdf")
				w.Write(body)
			}))}
			result, e := RunGreenhouseClaim(context.Background(), f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("original PDF lifecycle failed to settle", e)
			}
			assertRichDeadlineAndLease(t, f, "kipt")
			var count, missing, failures int
			var reserved bool
			ctx := context.Background()
			if e := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&count); e != nil {
				t.Fatal(e)
			}
			if e := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
				t.Fatal(e)
			}
			if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); e != nil {
				t.Fatal(e)
			}
			if mode == "complete" {
				if count != 1 || missing != 1 || failures != 0 || reserved {
					t.Fatal("complete PDF canonical effects differ")
				}
				var source, title, description, locations, locales string
				if e := f.pg.QueryRow(ctx, "SELECT p.source_url,coalesce(p.titles[1],''),d.html,p.location_ids::text,p.locales::text FROM job_posting p JOIN descriptions d ON d.posting_id=p.id AND d.locale='uk' WHERE p.board_id=$1::uuid AND p.id<>$2::uuid", f.board, f.original).Scan(&source, &title, &description, &locations, &locales); e != nil {
					t.Fatal(e)
				}
				if !strings.HasPrefix(source, urls[0]+"?_jid=") || title == "" || description == "" || !strings.Contains(locales, "uk") || len(locations) < 3 {
					t.Fatal("PDF identity, content or locale was lost")
				}
			} else if count != 0 || missing != 0 || failures != map[bool]int{true: 1, false: 0}[mode == "partial"] || reserved != (mode == "reserved") {
				t.Fatal("unproved PDF prefix or publisher override written")
			}
		})
	}
}
