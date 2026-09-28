package enrichment

import (
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
)

type languageOracle struct {
	ModelSHA string `json:"model_sha256"`
	Cases    []struct {
		Text, Input string
		Prediction  *struct {
			Lang  string
			Score float64
		}
		Language  *string
		Languages []string
	}
	Preprocess [][2]string
}

func TestLanguagePythonOracle(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_language.json")
	if err != nil {
		t.Fatal(err)
	}
	var oracle languageOracle
	if err = json.Unmarshal(raw, &oracle); err != nil {
		t.Fatal(err)
	}
	if oracle.ModelSHA != languageModelSHA {
		t.Fatal("oracle model identity drift")
	}
	model, err := residentLanguageModel()
	if err != nil {
		t.Fatal(err)
	}
	for i, c := range oracle.Cases {
		if input := languageInput(c.Text); input != c.Input {
			t.Fatalf("case %d preprocessing: %q != %q", i, input, c.Input)
		}
		lang, score := model.predict(c.Text)
		if c.Prediction == nil {
			if lang != "" {
				t.Fatalf("case %d expected no prediction", i)
			}
		} else if lang != c.Prediction.Lang || math.Abs(float64(score)-c.Prediction.Score) > 2e-6 {
			t.Fatalf("case %d prediction: %s %.9f != %s %.9f", i, lang, score, c.Prediction.Lang, c.Prediction.Score)
		}
		if actual := model.primary(c.Text); !reflect.DeepEqual(actual, c.Language) {
			t.Fatalf("case %d primary mismatch: %v != %v", i, actual, c.Language)
		}
		if actual := model.all(c.Text); !reflect.DeepEqual(actual, c.Languages) {
			t.Fatalf("case %d language order/coverage mismatch: %v != %v", i, actual, c.Languages)
		}
	}
	for _, c := range oracle.Preprocess {
		if actual := languageInput(c[0]); actual != c[1] {
			t.Fatalf("Unicode preparation %q: %q != %q", c[0], actual, c[1])
		}
	}
}
func TestLanguageModelRejectsCorruption(t *testing.T) {
	if _, err := loadLanguageModel(nil); err == nil {
		t.Fatal("missing model accepted")
	}
	bad := append([]byte(nil), languageModelBytes...)
	bad[len(bad)-1] ^= 1
	if _, err := loadLanguageModel(bad); err == nil {
		t.Fatal("changed model accepted")
	}
}
func TestLanguageChunksPreserveLegacyHardCut(t *testing.T) {
	text := make([]rune, 1002)
	for i := range text {
		text[i] = '字'
	}
	text[500] = 'X'
	text[1001] = 'Y'
	chunks := languageChunks(string(text))
	if len(chunks) != 2 || len([]rune(chunks[0])) != 500 || len([]rune(chunks[1])) != 500 {
		t.Fatalf("chunk boundaries: %v", chunks)
	}
}
