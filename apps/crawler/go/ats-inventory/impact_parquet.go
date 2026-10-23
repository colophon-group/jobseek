package main

import (
	"context"
	"io"
	"os"
	"sort"
	"strings"
	"time"

	parquet "github.com/parquet-go/parquet-go"
	"github.com/parquet-go/parquet-go/format"
)

func impactParquetString(v parquet.Value, node parquet.Node) any {
	if v.IsNull() {
		return nil
	}
	if logical := node.Type().LogicalType(); logical != nil {
		switch typ := logical.Value.(type) {
		case *format.DateType:
			return time.Unix(int64(v.Int32())*86400, 0).UTC().Format("2006-01-02")
		case *format.TimestampType:
			unit, precision := int64(1000000), 6
			switch typ.Unit.Value.(type) {
			case *format.MilliSeconds:
				unit = 1000
				precision = 3
			case *format.NanoSeconds:
				unit = 1000000000
				precision = 9
			}
			n := v.Int64()
			at := time.Unix(n/unit, n%unit*(1000000000/unit)).UTC()
			value := at.Format("2006-01-02 15:04:05") + "." + at.Format(".000000000")[1:1+precision]
			if typ.IsAdjustedToUTC {
				value += "+00:00"
			}
			return value
		}
	}
	return v.String()
}
func deriveImpactFamily(ctx context.Context, path string, artifact impactArtifact, derivedAt string) (impactFamilySummary, error) {
	var empty impactFamilySummary
	file, e := os.Open(path)
	if e != nil {
		return empty, impactError("Parquet artifact is unreadable")
	}
	defer file.Close()
	info, e := file.Stat()
	if e != nil {
		return empty, e
	}
	data, e := parquet.OpenFile(file, info.Size())
	if e != nil {
		return empty, impactError("Parquet artifact is unreadable")
	}
	selected := map[int]string{}
	nodes := map[int]parquet.Node{}
	projected := parquet.Group{}
	for _, name := range []string{"url", "company", "ats_id", "location", "country_iso", "is_remote", "posted_at", "tenant_key"} {
		leaf, ok := data.Schema().Lookup(name)
		if !ok {
			if name == "tenant_key" {
				continue
			}
			return empty, impactError("Parquet artifact lacks required column")
		}
		if leaf.MaxRepetitionLevel != 0 {
			return empty, impactError("Parquet artifact column must be scalar")
		}
		projected[name] = leaf.Node
	}
	projectedSchema := parquet.NewSchema(data.Schema().Name(), projected)
	conversion, e := parquet.Convert(projectedSchema, data.Schema())
	if e != nil {
		return empty, impactError("Parquet selected columns cannot be projected")
	}
	for name := range projected {
		leaf, ok := projectedSchema.Lookup(name)
		if !ok {
			return empty, impactError("Parquet projection lost required column")
		}
		selected[leaf.ColumnIndex] = name
		nodes[leaf.ColumnIndex] = leaf.Node
	}
	if data.NumRows() != int64(artifact.Rows) {
		return empty, impactError("Parquet row count differs from manifest")
	}
	type identity struct{ kind, key, name string }
	buckets := map[identity]*mutableImpactBucket{}
	total := 0
	for _, group := range data.RowGroups() {
		rows := parquet.ConvertRowGroup(group, conversion).Rows()
		batch := make([]parquet.Row, 256)
		for {
			if e = ctx.Err(); e != nil {
				rows.Close()
				return empty, e
			}
			n, readErr := rows.ReadRows(batch)
			for _, row := range batch[:n] {
				raw := map[string]any{}
				for _, v := range row {
					if name, ok := selected[v.Column()]; ok {
						raw[name] = impactParquetString(v, nodes[v.Column()])
					}
				}
				direct, extracted, name := normalizeInventoryIdentity(raw["tenant_key"], 500), inventoryTenantKey(artifact.Family, raw["url"], raw["ats_id"]), normalizeInventoryIdentity(raw["company"], 500)
				id := identity{"unmatched", "", ""}
				switch {
				case direct != "":
					id = identity{"direct", direct, name}
				case extracted != "":
					id = identity{"tenant", extracted, name}
				case name != "":
					id = identity{"name", name, name}
				}
				bucket := buckets[id]
				if bucket == nil {
					bucket = newMutableImpactBucket(id.kind, id.key)
					buckets[id] = bucket
				}
				remote := false
				if value, ok := raw["is_remote"].(string); ok {
					switch strings.ToLower(value) {
					case "true", "1", "yes":
						remote = true
					}
				}
				bucket.add(raw["company"], raw["location"], raw["country_iso"], remote, raw["posted_at"])
				total++
			}
			if readErr != nil {
				rows.Close()
				if readErr != io.EOF {
					return empty, impactError("Parquet rows are unreadable")
				}
				break
			}
			if n == 0 {
				rows.Close()
				return empty, impactError("Parquet reader made no progress")
			}
		}
	}
	if total != artifact.Rows {
		return empty, impactError("Parquet rows are incomplete")
	}
	ids := make([]identity, 0, len(buckets))
	for id := range buckets {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool {
		a, b := ids[i], ids[j]
		if a.kind != b.kind {
			return a.kind < b.kind
		}
		if a.key != b.key {
			return a.key < b.key
		}
		return a.name < b.name
	})
	frozen := make([]impactBucket, 0, len(ids))
	keyed := 0
	for _, id := range ids {
		b := buckets[id].freeze()
		frozen = append(frozen, b)
		if b.Kind != "unmatched" {
			keyed += b.ActiveJobs
		}
	}
	return impactFamilySummary{SchemaVersion: 1, AlgorithmVersion: impactAlgorithmVersion, Family: artifact.Family, ArtifactSHA256: artifact.SHA256, ArtifactSize: artifact.Size, ArtifactRows: artifact.Rows, DerivedAt: derivedAt, BucketCount: len(frozen), KeyedJobs: keyed, UnkeyedJobs: artifact.Rows - keyed, Buckets: frozen}, nil
}
