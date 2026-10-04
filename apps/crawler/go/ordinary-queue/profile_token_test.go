package queue

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestRealOwnedResolvedGreenhouseTokenBindsCanonicalAndCache(t *testing.T) {
	for _, row := range []struct{ name, url, metadata string }{
		{"custom-explicit", "https://company.example.test/careers", `{"token":"fixture","scraper_type":"skip"}`},
		{"api-inferred", "https://boards-api.greenhouse.io/v1/boards/fixture/jobs", `{"scraper_type":"skip"}`},
		{"embedded-ignored-alias", "https://boards.greenhouse.io/embed/job_board?for=fixture", `{"board_token":"wrong-token","scraper_type":"skip"}`},
		{"regional-null-token", "https://job-boards.eu.greenhouse.io/?url_token=fixture", `{"token":null,"scraper_type":"skip"}`},
	} {
		t.Run(row.name, func(t *testing.T) {
			f := greenhouseAuthorityFixture(t)
			ctx := context.Background()
			if _, err := f.observer.Exec(ctx, "UPDATE job_board SET board_url=$2,metadata=$3::jsonb WHERE id=$1::uuid", f.task.ID, row.url, row.metadata); err != nil {
				t.Fatal(err)
			}
			if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "board_url", row.url, "metadata", row.metadata).Err(); err != nil {
				t.Fatal(err)
			}
			plan := stageFixturePlan(t, f, strings.Repeat("a", 40))
			if _, err := f.authority.InspectStagedOwnership(ctx, plan.digest, plan.SourceRevision()); err != nil {
				t.Fatal("resolved profile could not restage against canonical/cache", err)
			}
			// Private fixture selection only; production still uses cold host
			// admission and acknowledged projection publication.
			activateFixturePlan(t, f, plan)
			if err := f.client.redis.Set(ctx, ownershipProjectionKey, plan.projection, 0).Err(); err != nil {
				t.Fatal(err)
			}
			owner, err := OpenOwnedAuthority(ctx, f.dsn, f.client, f.epoch, plan.digest, plan.SourceRevision())
			if err != nil {
				t.Fatal(err)
			}
			defer owner.Close()
			profile, err := owner.ObserveGreenhouseMonitor(ctx, f.task.ID)
			if err != nil || profile.Token != "fixture" || profile.Endpoint != "https://boards-api.greenhouse.io/v1/boards/fixture/jobs?content=true" {
				t.Fatal("resolved owner lost its fixed API endpoint", err)
			}
			claim, err := owner.Claim(ctx, Simple)
			if err != nil || claim == nil || !claim.OwnershipBound() || claim.Descriptor().ID != f.task.ID {
				t.Fatal("resolved profile could not claim under exact ownership", err)
			}
		})
	}
}

func TestGreenhouseProfileActualPythonTokenOracle(t *testing.T) {
	body, err := os.ReadFile("testdata/python_greenhouse_profile_tokens.json")
	if err != nil {
		t.Fatal(err)
	}
	var oracle struct {
		Cases []struct {
			Name, Token, Endpoint string
			BoardURL              string         `json:"board_url"`
			Metadata              map[string]any `json:"metadata"`
			Accepted              bool
		}
	}
	if json.Unmarshal(body, &oracle) != nil || len(oracle.Cases) != 79 {
		t.Fatal("actual Python token/request oracle unavailable")
	}
	for _, row := range oracle.Cases {
		t.Run(row.Name, func(t *testing.T) {
			config := profileConfig()
			meta, _ := json.Marshal(row.Metadata)
			config["board_url"], config["metadata"] = row.BoardURL, string(meta)
			profile, err := InspectGreenhouseMonitor(profileBoardID, config)
			if !row.Accepted {
				if !errors.Is(err, ErrUnsupportedProfile) || !reflect.DeepEqual(profile, GreenhouseMonitorProfile{}) {
					t.Fatal("unsupported URL inference admitted a partial profile")
				}
				return
			}
			if err != nil || profile.Token != row.Token || profile.Endpoint != row.Endpoint {
				t.Fatalf("actual Python token/request differs: %q %q %v", profile.Token, profile.Endpoint, err)
			}
		})
	}
}
