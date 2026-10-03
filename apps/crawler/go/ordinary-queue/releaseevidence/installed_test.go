package releaseevidence

import (
	"archive/tar"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"sort"
	"strings"
	"testing"
)

func installedFixture(t *testing.T, legacy bool) (*InstalledExpectation, map[string][]byte) {
	t.Helper()
	s := InstalledExpectationSpec{Version: "jobseek.crawler-installed-expectation/v1", SourceRevision: strings.Repeat("a", 40), ImageID: fixtureImageID, Architecture: "amd64", Kind: "native-ordinary", BinarySHA256: digest([]byte("binary")), CASHA256: digest([]byte("CA")), Assets: map[string]string{"boards.csv": digest([]byte("board")), "images/logo.svg": digest([]byte("logo"))}}
	contents := map[string][]byte{"go-ordinary-worker": []byte("binary"), "ca-certificates.crt": []byte("CA"), "data/boards.csv": []byte("board"), "data/images/logo.svg": []byte("logo")}
	if legacy {
		s.Kind = "legacy-python"
		s.SourceFiles = map[string]string{"__init__.py": digest([]byte("source"))}
		s.PackageFiles = map[string]string{"__init__.py": digest([]byte("source"))}
		s.EntrypointSHA256 = digest([]byte("entrypoint"))
		contents["python3.13"] = []byte("binary")
		contents["crawler"] = []byte("entrypoint")
		contents["src/__init__.py"] = []byte("source")
	}
	b, err := json.Marshal(s)
	if err != nil {
		t.Fatal(err)
	}
	e, err := DecodeInstalledExpectation(b, digest(b))
	if err != nil {
		t.Fatal(err)
	}
	return e, contents
}

func TestInstalledLegacyExpectationRequiresInstalledPackageAndEntrypoint(t *testing.T) {
	e, _ := installedFixture(t, true)
	for _, fault := range []string{"source missing", "package missing", "entrypoint missing", "entrypoint malformed", "package path", "package file parent", "native package", "native entrypoint"} {
		t.Run(fault, func(t *testing.T) {
			s := e.spec
			s.PackageFiles = map[string]string{"__init__.py": digest([]byte("source"))}
			switch fault {
			case "source missing":
				s.SourceFiles = nil
			case "package missing":
				s.PackageFiles = nil
			case "entrypoint missing":
				s.EntrypointSHA256 = ""
			case "entrypoint malformed":
				s.EntrypointSHA256 = "label"
			case "package path":
				s.PackageFiles["../private"] = s.BinarySHA256
			case "package file parent":
				s.PackageFiles["__init__.py/child"] = s.BinarySHA256
			case "native package":
				s.Kind, s.SourceFiles, s.EntrypointSHA256 = "native-ordinary", nil, ""
			case "native entrypoint":
				s.Kind, s.SourceFiles, s.PackageFiles = "native-ordinary", nil, nil
			}
			b, _ := json.Marshal(s)
			if _, err := DecodeInstalledExpectation(b, digest(b)); !errors.Is(err, ErrInvalid) {
				t.Fatal("incomplete or misplaced installed legacy authority admitted", err)
			}
		})
	}
}

func installedArchive(t *testing.T, group installedGroup, contents map[string][]byte, change func(*tar.Header)) []byte {
	t.Helper()
	var b bytes.Buffer
	w := tar.NewWriter(&b)
	names := []string{}
	for name := range group.files {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		mode := int64(0644)
		if group.executable {
			mode = 0755
		}
		h := &tar.Header{Name: name, Mode: mode, Typeflag: tar.TypeReg, Size: int64(len(contents[name])), Format: tar.FormatUSTAR}
		if change != nil {
			change(h)
		}
		if w.WriteHeader(h) != nil {
			t.Fatal("fixture archive header")
		}
		if h.Typeflag == tar.TypeReg || h.Typeflag == tar.TypeRegA {
			if _, err := w.Write(contents[name]); err != nil {
				t.Fatal(err)
			}
		}
	}
	if w.Close() != nil {
		t.Fatal("fixture archive close")
	}
	return b.Bytes()
}

func TestInstalledExpectationRequiresExactCanonicalRetainedIdentity(t *testing.T) {
	e, _ := installedFixture(t, false)
	for _, fault := range []string{"hash", "noncanonical", "unknown", "duplicate", "source", "image", "platform", "kind", "missing binary", "missing CA", "empty assets", "path", "file parent", "native source"} {
		t.Run(fault, func(t *testing.T) {
			s := e.spec
			s.Assets = map[string]string{"boards.csv": digest([]byte("board"))}
			switch fault {
			case "source":
				s.SourceRevision = "label"
			case "image":
				s.ImageID = "image:latest"
			case "platform":
				s.Architecture = "x86"
			case "kind":
				s.Kind = "shell"
			case "missing binary":
				s.BinarySHA256 = ""
			case "missing CA":
				s.CASHA256 = ""
			case "empty assets":
				s.Assets = nil
			case "path":
				s.Assets["../private"] = s.BinarySHA256
			case "file parent":
				s.Assets["boards.csv/child"] = s.BinarySHA256
			case "native source":
				s.SourceFiles = map[string]string{"x.py": s.BinarySHA256}
			}
			body, _ := json.Marshal(s)
			switch fault {
			case "noncanonical":
				body = append(body, '\n')
			case "unknown":
				body = []byte(strings.Replace(string(body), `{`, `{"private":"hidden",`, 1))
			case "duplicate":
				body = []byte(strings.Replace(string(body), `{`, `{"version":"ignored",`, 1))
			}
			hash := digest(body)
			if fault == "hash" {
				hash = strings.Repeat("b", 64)
			}
			if _, err := DecodeInstalledExpectation(body, hash); !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "private") {
				t.Fatal("invalid retained expectation accepted/disclosed", err)
			}
		})
	}
}

func TestInstalledArchivesVerifyFullMembershipAndRejectUnsafeTar(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		e, contents := installedFixture(t, legacy)
		for _, group := range installedGroups(e.spec) {
			good := installedArchive(t, group, contents, nil)
			if err := verifyInstalledArchive(context.Background(), bytes.NewReader(good), group); err != nil {
				t.Fatal("complete installed archive refused", err)
			}
			for _, fault := range []string{"wrong content", "missing end", "trailer", "unknown file", "link", "device", "writable", "setuid", "nonexecutable", "alias", "PAX xattr"} {
				if fault == "nonexecutable" && !group.executable {
					continue
				}
				bad := installedArchive(t, group, contents, func(h *tar.Header) {
					switch fault {
					case "unknown file":
						h.Name = "unaccounted"
					case "link":
						h.Typeflag, h.Linkname, h.Size = tar.TypeSymlink, "private", 0
					case "device":
						h.Typeflag, h.Size = tar.TypeChar, 0
					case "writable":
						h.Mode = 0777
					case "setuid":
						h.Mode = 04755
					case "nonexecutable":
						h.Mode = 0644
					case "alias":
						h.Name = "../private"
					case "PAX xattr":
						h.Format = tar.FormatPAX
						h.PAXRecords = map[string]string{"SCHILY.xattr.user.private": "hidden"}
					}
				})
				switch fault {
				case "wrong content":
					bad[512] ^= 1
				case "missing end":
					bad = bad[:len(bad)-1024]
				case "trailer":
					bad = append(bad, []byte("private")...)
				}
				if err := verifyInstalledArchive(context.Background(), bytes.NewReader(bad), group); !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "private") {
					t.Fatal("unsafe installed archive accepted/disclosed", fault, err)
				}
			}
		}
	}
}

func TestInstalledArchiveDirectoryAndPAXFraming(t *testing.T) {
	e, contents := installedFixture(t, false)
	group := installedGroups(e.spec)[2]
	good := installedArchive(t, group, contents, nil)
	for _, directory := range []string{"data/", "data/images/", "data/unaccounted/", "./data/"} {
		var prefix bytes.Buffer
		w := tar.NewWriter(&prefix)
		if w.WriteHeader(&tar.Header{Name: directory, Mode: 0755, Typeflag: tar.TypeDir, Format: tar.FormatUSTAR}) != nil || w.Close() != nil {
			t.Fatal("directory fixture")
		}
		archive := append(prefix.Bytes()[:prefix.Len()-1024], good...)
		err := verifyInstalledArchive(context.Background(), bytes.NewReader(archive), group)
		if (directory == "data/" || directory == "data/images/") != (err == nil) {
			t.Fatal("closed installed directory membership differs", err)
		}
	}
	pax := installedArchive(t, group, contents, func(h *tar.Header) {
		h.Format = tar.FormatPAX
		h.PAXRecords = map[string]string{"mtime": "1.25"}
	})
	if err := verifyInstalledArchive(context.Background(), bytes.NewReader(pax), group); err != nil {
		t.Fatal("legitimate Docker PAX time metadata refused", err)
	}
	duplicate := append(append([]byte{}, good[:len(good)-1024]...), good...)
	if err := verifyInstalledArchive(context.Background(), bytes.NewReader(duplicate), group); !errors.Is(err, ErrInvalid) {
		t.Fatal("duplicate installed files admitted", err)
	}
	missing := installedArchive(t, installedGroup{files: map[string]string{}}, contents, nil)
	if err := verifyInstalledArchive(context.Background(), bytes.NewReader(missing), group); !errors.Is(err, ErrInvalid) {
		t.Fatal("missing installed files admitted", err)
	}
}

func TestInstalledContainerFilesBindExactReadbackWithoutExecutingImage(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		for _, fault := range []string{"", "wrong container", "image drift", "platform drift", "file drift", "inventory drift", "no response", "command failure", "ignored parse error", "duplicate response"} {
			e, contents := installedFixture(t, legacy)
			c := containerFixture(1, "jobseek", "worker")
			inventory := observeFixtureContainers(t, c)
			read := containerReader(t, c)
			images, copies := 0, 0
			run := func(ctx context.Context, args []string) ([]byte, error) {
				if len(args) == 3 && args[0] == "image" && args[1] == "inspect" && args[2] == fixtureImageID {
					images++
					id, arch := fixtureImageID, "amd64"
					if images == 2 && fault == "image drift" {
						id = "sha256:" + strings.Repeat("f", 64)
					}
					if fault == "platform drift" {
						arch = "arm64"
					}
					return json.Marshal([]map[string]any{{"Id": id, "Os": "linux", "Architecture": arch, "Private": "private-value"}})
				}
				if fault == "inventory drift" {
					c["Config"].(map[string]any)["Cmd"] = []string{"changed"}
				}
				return read(ctx, args)
			}
			stream := func(ctx context.Context, args []string, consume func(io.Reader) error) error {
				copies++
				if len(args) != 3 || args[0] != "cp" || args[2] != "-" || !strings.HasPrefix(args[1], c["Id"].(string)+":") {
					t.Fatal("unexpected installed command")
				}
				if fault == "command failure" {
					return errors.New("private-value")
				}
				if fault == "no response" {
					return nil
				}
				for _, group := range installedGroups(e.spec) {
					if args[1] == c["Id"].(string)+":"+group.location {
						body := installedArchive(t, group, contents, nil)
						if fault == "ignored parse error" {
							_ = consume(bytes.NewReader([]byte("private-value")))
							return nil
						}
						if fault == "duplicate response" {
							_ = consume(bytes.NewReader(body))
							_ = consume(bytes.NewReader(body))
							return nil
						}
						if fault == "file drift" && copies == len(installedGroups(e.spec))+1 {
							body[512] ^= 1
						}
						return consume(bytes.NewReader(body))
					}
				}
				t.Fatal("unexpected installed path")
				return nil
			}
			id := c["Id"].(string)
			if fault == "wrong container" {
				id = strings.Repeat("f", 64)
			}
			got, err := observeInstalledContainerFiles(context.Background(), inventory, e, id, run, stream)
			if fault != "" {
				if !errors.Is(err, ErrInvalid) || got != nil || strings.Contains(err.Error(), "private-value") {
					t.Fatal("installed drift admitted/disclosed", fault, err)
				}
				continue
			}
			if err != nil || copies != 2*len(installedGroups(e.spec)) || images != 2 || got.SHA256() != digest([]byte(got.Body())) || !strings.Contains(got.Body(), `"runtime_admission":false`) || strings.Contains(got.Body(), "private-value") || strings.Contains(got.Body(), "boards.csv") {
				t.Fatal("incomplete or disclosed installed evidence", err)
			}
		}
	}
}
