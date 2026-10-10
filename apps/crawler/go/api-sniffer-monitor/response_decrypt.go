package apisniffer

import (
	"crypto/aes"
	"crypto/cipher"
	"encoding/base64"
	"reflect"
	"strings"
	"unicode/utf8"
)

type responseDecrypt struct {
	key  []byte
	mode string
}

func (*responseDecrypt) String() string   { return "configured response decryption" }
func (*responseDecrypt) GoString() string { return "configured response decryption" }

func parseResponseDecrypt(raw any) (*responseDecrypt, error) {
	if raw == nil {
		return nil, nil
	}
	m, ok := raw.(map[string]any)
	if !ok {
		return nil, ErrOptions
	}
	if len(m) == 0 {
		return nil, nil
	}
	for k := range m {
		if k != "key" && k != "iv_mode" {
			return nil, ErrOptions
		}
	}
	key, ok := m["key"].(string)
	if !ok || !utf8.ValidString(key) {
		return nil, ErrOptions
	}
	if _, err := aes.NewCipher([]byte(key)); err != nil {
		return nil, ErrOptions
	}
	mode := "suffix"
	if value, exists := m["iv_mode"]; exists {
		mode, ok = value.(string)
		if !ok {
			return nil, ErrOptions
		}
	}
	if mode != "suffix" && (!strings.HasPrefix(mode, "fixed:") || len([]byte(mode[6:])) != aes.BlockSize) {
		return nil, ErrOptions
	}
	return &responseDecrypt{key: []byte(key), mode: mode}, nil
}

// The original decrypts only the initial response's Data field. A malformed
// ciphertext leaves that field unchanged, without emitting private values.
func decryptInitialResponse(d *Document, o *responseDecrypt) *Document {
	if d == nil || o == nil {
		return d
	}
	root, ok := d.Value.(map[string]any)
	if !ok {
		return d
	}
	value, ok := root["Data"].(string)
	if !ok || value == "" || len(value) > 64<<20 {
		return d
	}
	text := value
	var iv []byte
	if o.mode == "suffix" {
		runes := []rune(value)
		if len(runes) < 16 {
			return d
		}
		iv = []byte(string(runes[len(runes)-16:]))
		text = string(runes[:len(runes)-16])
	} else {
		iv = []byte(o.mode[6:])
	}
	if len(iv) != aes.BlockSize {
		return d
	}
	// Python's non-strict base64 decoder ignores ASCII nonalphabet bytes.
	var clean strings.Builder
	for _, b := range []byte(text) {
		if b > 127 {
			return d
		}
		if b >= 'A' && b <= 'Z' || b >= 'a' && b <= 'z' || b >= '0' && b <= '9' || b == '+' || b == '/' || b == '=' {
			clean.WriteByte(b)
		}
	}
	encoded := clean.String()
	if at := strings.IndexByte(encoded, '='); at >= 0 {
		end := at
		for end < len(encoded) && encoded[end] == '=' {
			end++
		}
		encoded = encoded[:end]
	}
	decoded, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil || len(decoded) == 0 || len(decoded)%aes.BlockSize != 0 {
		return d
	}
	block, err := aes.NewCipher(o.key)
	if err != nil {
		return d
	}
	cipher.NewCBCDecrypter(block, iv).CryptBlocks(decoded, decoded)
	padding := int(decoded[len(decoded)-1])
	if padding < 1 || padding > aes.BlockSize || padding > len(decoded) {
		return d
	}
	for _, b := range decoded[len(decoded)-padding:] {
		if int(b) != padding {
			return d
		}
	}
	plain := decoded[:len(decoded)-padding]
	if !utf8.Valid(plain) {
		return d
	}
	decrypted, err := Decode(plain)
	if err != nil {
		return d
	}
	copyRoot := map[string]any{}
	for k, v := range root {
		copyRoot[k] = v
	}
	copyRoot["Data"] = decrypted.Value
	orders := map[reflect.Value][]string{}
	for k, v := range d.order {
		orders[k] = append([]string(nil), v...)
	}
	for k, v := range decrypted.order {
		orders[k] = append([]string(nil), v...)
	}
	orders[reflect.ValueOf(copyRoot)] = d.ObjectKeys(root)
	return &Document{Value: copyRoot, Root: d.Root, order: orders}
}
