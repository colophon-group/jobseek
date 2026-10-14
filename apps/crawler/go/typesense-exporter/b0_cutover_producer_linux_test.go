//go:build linux

package main

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

func TestB0CutoverAuthenticatedRequests(t *testing.T) {
	for _, operation := range []string{"manifest", "prepare", "activate"} {
		for _, fault := range []string{"", "wrong_operation", "wrong_cohort", "trailing", "lost_authority"} {
			t.Run(operation+"/"+fault, func(t *testing.T) {
				dir := t.TempDir()
				if err := os.Chmod(dir, 0700); err != nil {
					t.Fatal(err)
				}
				socket := filepath.Join(dir, "control.sock")
				listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
				if err != nil {
					t.Fatal(err)
				}
				defer listener.Close()
				if err := os.Chmod(socket, 0600); err != nil {
					t.Fatal(err)
				}
				config := map[string]string{"domain": "jobs.example", "board_id": "board", "source_url": "https://jobs.example/job", "description_r2_hash": "0", "scrape_step": "0", "scrape_interval_hours": "24"}
				want := map[string]any{"browser": true, "cohort": "", "config": config, "domain": "jobs.example", "expected_digest": "", "first_time": false, "next_scrape_at_ms": int64(1234001), "operation": operation, "operator_transfer": true, "posting_id": "posting", "version": capacityProducerProtocol, "legacy_schedule_score": "1234.0001"}
				if operation == "manifest" {
					want = map[string]any{"browser": false, "cohort": "cdom", "config": map[string]string{}, "domain": "", "expected_digest": "", "first_time": false, "next_scrape_at_ms": int64(0), "operation": "manifest", "operator_transfer": true, "posting_id": "", "version": capacityProducerProtocol}
				}
				if operation == "activate" {
					want["expected_digest"] = strings.Repeat("a", 64)
				}
				canonical, err := b0task.CanonicalJSON(want, true)
				if err != nil {
					t.Fatal(err)
				}
				server := make(chan error, 1)
				go func() {
					conn, err := listener.AcceptUnix()
					if err != nil {
						server <- err
						return
					}
					defer conn.Close()
					_ = conn.SetDeadline(time.Now().Add(3 * time.Second))
					payload, err := framing.ReadRecord(conn, 256*1024)
					if err != nil {
						server <- err
						return
					}
					if string(payload) != string(canonical) {
						server <- errors.New("original administrative request differs")
						return
					}
					d := capacityProducerDecision{Version: capacityProducerProtocol, Outcome: "prepared", Reason: "prepared", PreparationDigest: strings.Repeat("a", 64), PayloadSHA256: strings.Repeat("b", 64), BoardSlugs: []string{}, LifetimeOccupancy: 26, LifetimeCapacity: 2048, LifetimeHeadroom: 2022}
					if operation == "activate" {
						d.Outcome = "activated"
						d.Reason = "activated"
						d.Activated = true
					}
					if operation == "manifest" {
						d.Outcome = "manifest"
						d.Reason = "manifest"
						d.PreparationDigest = ""
						d.PayloadSHA256 = ""
						d.Cohort = "cdom"
						d.BoardSlugs = []string{"algorized-careers", "browser-use-careers", "bunq-careers"}
					}
					switch fault {
					case "wrong_operation":
						d.Outcome = "legacy"
						d.Reason = "literal_legacy"
						d.PreparationDigest = ""
						d.PayloadSHA256 = ""
						d.Activated = false
						d.Cohort = ""
						d.BoardSlugs = []string{}
					case "wrong_cohort":
						if operation == "manifest" {
							d.Cohort = "c1"
						} else {
							d.Cohort = "cdom"
						}
					case "lost_authority":
						d.Outcome = "error"
						d.Reason = "authority_lost"
					}
					body, err := b0task.CanonicalJSON(d, true)
					if err != nil {
						server <- err
						return
					}
					record, err := framing.EncodeRecord(body, 256*1024)
					if err != nil {
						server <- err
						return
					}
					if fault == "trailing" {
						record = append(record, 0)
					}
					_, err = conn.Write(record)
					server <- err
				}()
				p := capacityProducer{mode: "enabled", socket: socket, uid: uint32(os.Getuid())}
				var got capacityProducerDecision
				if operation == "manifest" {
					got, err = p.cutoverManifest(context.Background(), "cdom")
				} else {
					digest := ""
					if operation == "activate" {
						digest = strings.Repeat("a", 64)
					}
					got, err = p.cutoverTask(context.Background(), operation, "jobs.example", "posting", 1234.0001, config, false, "1234.0001", digest)
				}
				if serverErr := <-server; serverErr != nil {
					t.Fatal(serverErr)
				}
				if fault != "" {
					if err == nil {
						t.Fatal("invalid administrative reply accepted")
					}
					return
				}
				if err != nil {
					t.Fatal(err)
				}
				if got.LifetimeOccupancy != 26 || got.LifetimeHeadroom != 2022 {
					t.Fatal("capacity witness differs")
				}
				if operation == "manifest" && !reflect.DeepEqual(got.BoardSlugs, []string{"algorized-careers", "browser-use-careers", "bunq-careers"}) {
					t.Fatal("manifest identity differs")
				}
			})
		}
	}
}
