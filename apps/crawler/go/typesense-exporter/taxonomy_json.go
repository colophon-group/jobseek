package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

// Taxonomy evidence persists hashes produced by Python json.dumps with sorted
// keys, compact separators and ensure_ascii=False. Keep those bytes stable,
// including integral floats, exponent thresholds, and unescaped HTML/U+2028.
func taxonomyCanonicalJSON(value any, ascii bool) ([]byte, error) {
	var output bytes.Buffer
	if err := writeTaxonomyJSON(&output, value, ascii); err != nil {
		return nil, err
	}
	return output.Bytes(), nil
}

func taxonomyJSONString(output *bytes.Buffer, value string, ascii bool) error {
	if !utf8.ValidString(value) {
		return errors.New("invalid UTF-8 taxonomy string")
	}
	output.WriteByte('"')
	for _, character := range value {
		switch character {
		case '"', '\\':
			output.WriteByte('\\')
			output.WriteRune(character)
		case '\b':
			output.WriteString(`\b`)
		case '\f':
			output.WriteString(`\f`)
		case '\n':
			output.WriteString(`\n`)
		case '\r':
			output.WriteString(`\r`)
		case '\t':
			output.WriteString(`\t`)
		default:
			if character < 32 || ascii && character >= 127 {
				units := []rune{character}
				if character > 0xffff {
					high, low := utf16.EncodeRune(character)
					units = []rune{high, low}
				}
				for _, unit := range units {
					hex := strconv.FormatInt(int64(unit), 16)
					output.WriteString(`\u`)
					output.WriteString(strings.Repeat("0", 4-len(hex)))
					output.WriteString(hex)
				}
			} else {
				output.WriteRune(character)
			}
		}
	}
	output.WriteByte('"')
	return nil
}

func taxonomyFloatJSON(value float64) (string, error) {
	if math.IsInf(value, 0) || math.IsNaN(value) {
		return "", errors.New("non-finite taxonomy number")
	}
	exponential := strconv.FormatFloat(value, 'e', -1, 64)
	exponent, err := strconv.Atoi(exponential[strings.LastIndexByte(exponential, 'e')+1:])
	if err != nil {
		return "", err
	}
	if exponent >= -4 && exponent < 16 {
		fixed := strconv.FormatFloat(value, 'f', -1, 64)
		if !strings.ContainsRune(fixed, '.') {
			fixed += ".0"
		}
		return fixed, nil
	}
	return exponential, nil
}

func writeTaxonomyJSON(output *bytes.Buffer, value any, ascii bool) error {
	if value == nil {
		output.WriteString("null")
		return nil
	}
	if number, ok := value.(json.Number); ok {
		if len(number) > 4300 {
			return errors.New("taxonomy number exceeds safety limit")
		}
		if strings.ContainsAny(string(number), ".eE") {
			parsed, err := strconv.ParseFloat(string(number), 64)
			if err != nil {
				return err
			}
			encoded, err := taxonomyFloatJSON(parsed)
			if err != nil {
				return err
			}
			output.WriteString(encoded)
			return nil
		}
		parsed, ok := new(big.Int).SetString(string(number), 10)
		if !ok {
			return errors.New("invalid taxonomy integer")
		}
		output.WriteString(parsed.String())
		return nil
	}
	reflected := reflect.ValueOf(value)
	switch reflected.Kind() {
	case reflect.String:
		return taxonomyJSONString(output, reflected.String(), ascii)
	case reflect.Bool:
		output.WriteString(strconv.FormatBool(reflected.Bool()))
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		output.WriteString(strconv.FormatInt(reflected.Int(), 10))
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		output.WriteString(strconv.FormatUint(reflected.Uint(), 10))
	case reflect.Float32, reflect.Float64:
		encoded, err := taxonomyFloatJSON(reflected.Float())
		if err != nil {
			return err
		}
		output.WriteString(encoded)
	case reflect.Slice, reflect.Array:
		output.WriteByte('[')
		for i := 0; i < reflected.Len(); i++ {
			if i != 0 {
				output.WriteByte(',')
			}
			if err := writeTaxonomyJSON(output, reflected.Index(i).Interface(), ascii); err != nil {
				return err
			}
		}
		output.WriteByte(']')
	case reflect.Map:
		if reflected.Type().Key().Kind() != reflect.String {
			return errors.New("taxonomy JSON requires string keys")
		}
		keys := make([]string, 0, reflected.Len())
		iterator := reflected.MapRange()
		for iterator.Next() {
			keys = append(keys, iterator.Key().String())
		}
		sort.Strings(keys)
		output.WriteByte('{')
		for i, key := range keys {
			if i != 0 {
				output.WriteByte(',')
			}
			if err := taxonomyJSONString(output, key, ascii); err != nil {
				return err
			}
			output.WriteByte(':')
			if err := writeTaxonomyJSON(output, reflected.MapIndex(reflect.ValueOf(key)).Interface(), ascii); err != nil {
				return err
			}
		}
		output.WriteByte('}')
	default:
		return errors.New("unsupported taxonomy JSON value")
	}
	return nil
}
