package main

import (
	"bytes"
	"encoding/csv"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

type registryTable struct {
	Columns []string             `json:"columns"`
	Rows    []map[string]*string `json:"rows"`
}

// Polars' all-string CSV reader distinguishes a missing field from a quoted
// empty string. FieldPos retains that distinction without replacing the RFC
// CSV parser, including for embedded newlines and CRLF records.
func parseRegistryCSV(body []byte) (registryTable, error) {
	var table registryTable
	body = bytes.TrimPrefix(body, []byte{0xef, 0xbb, 0xbf})
	if !utf8.Valid(body) {
		return table, errors.New("registry CSV is not UTF-8")
	}
	currentLine, lineStart := 1, 0
	fieldPosition := func(line, column int) int {
		for currentLine < line {
			newline := bytes.IndexByte(body[lineStart:], '\n')
			if newline < 0 {
				return len(body)
			}
			lineStart += newline + 1
			currentLine++
		}
		return lineStart + column - 1
	}
	reader := csv.NewReader(bytes.NewReader(body))
	header, err := reader.Read()
	if err != nil {
		return table, errors.New("registry CSV header is unavailable")
	}
	seen := map[string]bool{}
	for _, name := range header {
		if name == "" || seen[name] {
			return table, errors.New("registry CSV headers are empty or duplicated")
		}
		seen[name] = true
	}
	table.Columns = header
	table.Rows = []map[string]*string{}
	for {
		values, err := reader.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return table, fmt.Errorf("invalid registry CSV record: %w", err)
		}
		row := map[string]*string{}
		for i, value := range values {
			line, column := reader.FieldPos(i)
			position := fieldPosition(line, column)
			quoted := position < len(body) && body[position] == '"'
			if quoted {
				value = registryQuotedCSVValue(body, position)
			}
			if value == "" && !quoted {
				row[header[i]] = nil
			} else {
				copy := value
				row[header[i]] = &copy
			}
		}
		table.Rows = append(table.Rows, row)
		if len(table.Rows) > 100000 {
			return table, errors.New("registry CSV exceeds row limit")
		}
	}
	return table, nil
}

func loadRegistryCSV(directory, name string, optional bool) (registryTable, error) {
	path := filepath.Join(directory, name+".csv")
	file, err := os.Open(path)
	if optional && errors.Is(err, os.ErrNotExist) {
		return registryTable{Columns: []string{}, Rows: []map[string]*string{}}, nil
	}
	if err != nil {
		return registryTable{}, fmt.Errorf("read %s CSV failed", name)
	}
	defer file.Close()
	body, err := io.ReadAll(io.LimitReader(file, 128<<20+1))
	if err != nil || len(body) > 128<<20 {
		return registryTable{}, fmt.Errorf("read %s CSV exceeded limit", name)
	}
	return parseRegistryCSV(body)
}
func registryText(row map[string]*string, key string) string {
	if value := row[key]; value != nil {
		return *value
	}
	return ""
}
func registryOptional(row map[string]*string, key string) *string {
	value := row[key]
	if value == nil || *value == "" {
		return nil
	}
	return value
}
func registryTrim(value string) string {
	return strings.Trim(value, " \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000")
}

// encoding/csv normalizes CRLF inside quoted fields; Polars retains it.
// The standard reader already validated quoting before this reconstruction.
func registryQuotedCSVValue(body []byte, position int) string {
	var value strings.Builder
	for i := position + 1; i < len(body); i++ {
		if body[i] == '"' {
			if i+1 < len(body) && body[i+1] == '"' {
				value.WriteByte('"')
				i++
				continue
			}
			break
		}
		value.WriteByte(body[i])
	}
	return value.String()
}
