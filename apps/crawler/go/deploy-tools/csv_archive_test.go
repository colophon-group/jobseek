package main

import (
	"archive/tar"
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCSVArchiveCompleteContractAndExtraction(t *testing.T) {
	revision, runtime := strings.Repeat("a", 40), strings.Repeat("b", 64)
	board := []byte("slug\nboard\n")
	nested := []byte("slug\ncompany\n")
	manifest := []byte(bridgeHash(board) + "  boards.csv\n" + bridgeHash(nested) + "  nested/companies.csv\n")
	contract := bridgeHash(manifest)
	attestation := []byte("RUNTIME_ATTESTATION_FORMAT_VERSION=1\nPREVIOUS_REVISION=" + revision + "\nRUNTIME_CONTRACT_SHA256=" + runtime + "\nCOMPATIBLE_REVISION=" + revision + "\n")
	for _, fault := range []string{"none", "wrong-contract", "wrong-manifest", "missing-manifest", "missing-attestation", "duplicate-attestation", "foreign-revision", "invalid-utf8", "duplicate-file", "directory-collision", "link", "traversal", "absolute", "unexpected", "preexisting-data", "preexisting-file", "destination-symlink"} {
		t.Run(fault, func(t *testing.T) {
			dir := t.TempDir()
			archive := filepath.Join(dir, "snapshot.tar")
			dest := filepath.Join(dir, "generation")
			if err := os.Mkdir(dest, 0755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(dest, "docker-compose.yml"), []byte("retained"), 0644); err != nil {
				t.Fatal(err)
			}
			var buffer bytes.Buffer
			writer := tar.NewWriter(&buffer)
			appendFile := func(name string, body []byte, kind byte) {
				t.Helper()
				h := &tar.Header{Name: name, Mode: 0644, Size: int64(len(body)), Typeflag: kind}
				if kind == tar.TypeSymlink {
					h.Size = 0
					h.Linkname = "../outside"
				}
				if err := writer.WriteHeader(h); err != nil {
					t.Fatal(err)
				}
				if kind != tar.TypeSymlink {
					if _, err := writer.Write(body); err != nil {
						t.Fatal(err)
					}
				}
			}
			appendFile("data/boards.csv", board, tar.TypeReg)
			appendFile("data/nested/companies.csv", nested, tar.TypeReg)
			m := manifest
			if fault == "wrong-manifest" {
				m = []byte("mismatched")
			}
			if fault != "missing-manifest" {
				appendFile("data-files.sha256", m, tar.TypeReg)
			}
			a := attestation
			switch fault {
			case "duplicate-attestation":
				a = append(append([]byte{}, a...), []byte("COMPATIBLE_REVISION="+revision+"\n")...)
			case "foreign-revision":
				a = []byte(strings.ReplaceAll(string(a), revision, strings.Repeat("c", 40)))
			case "invalid-utf8":
				a = []byte{255}
			}
			if fault != "missing-attestation" {
				appendFile("runtime-attestation.env", a, tar.TypeReg)
			}
			switch fault {
			case "duplicate-file":
				appendFile("data/boards.csv", board, tar.TypeReg)
			case "directory-collision":
				if err := writer.WriteHeader(&tar.Header{Name: "data/boards.csv/", Typeflag: tar.TypeDir, Mode: 0755}); err != nil {
					t.Fatal(err)
				}
			case "link":
				appendFile("data/link.csv", nil, tar.TypeSymlink)
			case "traversal":
				appendFile("data/../outside.csv", board, tar.TypeReg)
			case "absolute":
				appendFile("/data/outside.csv", board, tar.TypeReg)
			case "unexpected":
				appendFile("secret.env", board, tar.TypeReg)
			}
			if err := writer.Close(); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(archive, buffer.Bytes(), 0600); err != nil {
				t.Fatal(err)
			}
			c := contract
			if fault == "wrong-contract" {
				c = strings.Repeat("d", 64)
			}
			switch fault {
			case "preexisting-data":
				if err := os.Symlink("../outside", filepath.Join(dest, "data")); err != nil {
					t.Fatal(err)
				}
			case "preexisting-file":
				if err := os.WriteFile(filepath.Join(dest, "data-files.sha256"), []byte("retained"), 0644); err != nil {
					t.Fatal(err)
				}
			case "destination-symlink":
				link := filepath.Join(dir, "link")
				if err := os.Symlink(dest, link); err != nil {
					t.Fatal(err)
				}
				dest = link
			}
			err := unpackCSVArchive(archive, dest, c, revision, runtime)
			if fault == "none" {
				if err != nil {
					t.Fatal(err)
				}
				for name, want := range map[string][]byte{"data/boards.csv": board, "data/nested/companies.csv": nested, "data-files.sha256": manifest, "runtime-attestation.env": attestation} {
					got, e := os.ReadFile(filepath.Join(dest, name))
					if e != nil || !bytes.Equal(got, want) {
						t.Fatal("exact candidate content lost", name)
					}
				}
			} else {
				if err == nil {
					t.Fatal("invalid candidate admitted")
				}
				if _, err := os.Lstat(filepath.Join(dest, "data/boards.csv")); !os.IsNotExist(err) {
					t.Fatal("invalid candidate partially extracted")
				}
			}
			retained, err := os.ReadFile(filepath.Join(dest, "docker-compose.yml"))
			if err != nil || string(retained) != "retained" {
				t.Fatal("existing release evidence changed")
			}
		})
	}
}
