package enrichment

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"strings"
)

//go:embed intern_signals.json
var internJSON []byte
var interns = map[string]bool{}

func init() {
	var values []string
	if err := json.Unmarshal(internJSON, &values); err != nil {
		panic(err)
	}
	for _, s := range values {
		interns[s] = true
	}
}

type Request struct {
	ID             uint64   `json:"id"`
	Operation      string   `json:"operation"`
	Titles         []string `json:"titles,omitempty"`
	EmploymentType string   `json:"employment_type,omitempty"`
	Description    string   `json:"description,omitempty"`
}
type TitleResult struct {
	Occupation *string `json:"occupation"`
	Seniority  *string `json:"seniority"`
}
type Response struct {
	ID             uint64        `json:"id"`
	Titles         []TitleResult `json:"titles,omitempty"`
	Intern         bool          `json:"intern"`
	Technologies   []string      `json:"technologies"`
	Error          string        `json:"error,omitempty"`
	ExperienceMin  *float64      `json:"experience_min,omitempty"`
	ExperienceMax  *float64      `json:"experience_max,omitempty"`
	Language       *string       `json:"language"`
	Languages      []string      `json:"languages"`
	NormalizedHTML *string       `json:"normalized_html"`
}

func (m *Matcher) Process(r Request) (Response, error) {
	out := Response{ID: r.ID, Technologies: []string{}}
	switch r.Operation {
	case "occupation_seniority":
		if len(r.Titles) > 1000 {
			return out, fmt.Errorf("too many titles")
		}
		out.Titles = make([]TitleResult, 0, len(r.Titles))
		for _, title := range r.Titles {
			out.Titles = append(out.Titles, TitleResult{m.Occupation(title), Seniority(title)})
		}
		out.Intern = interns[lower(strings.TrimFunc(r.EmploymentType, space))]
	case "technology":
		out.Technologies = m.Technologies(r.Description)
	case "experience":
		out.ExperienceMin, out.ExperienceMax = Experience(r.Description)
	case "language", "all_languages":
		model, err := residentLanguageModel()
		if err != nil {
			return out, fmt.Errorf("language model unavailable")
		}
		if r.Operation == "language" {
			out.Language = model.primary(r.Description)
		} else {
			out.Languages = model.all(r.Description)
		}
	case "normalize_html":
		var err error
		out.NormalizedHTML, err = NormalizeDescriptionHTML(r.Description)
		if err != nil {
			return out, fmt.Errorf("HTML normalization failed")
		}
	default:
		return out, fmt.Errorf("unsupported operation")
	}
	return out, nil
}
