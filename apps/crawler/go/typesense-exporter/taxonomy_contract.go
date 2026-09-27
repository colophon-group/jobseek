package main

import (
	_ "embed"
	"encoding/json"
	"errors"
)

type taxonomyField struct {
	Name  string `json:"name"`
	Type  string `json:"field_type"`
	Index *bool  `json:"index"`
	Facet *bool  `json:"facet"`
}

type taxonomySpec struct {
	Collection string          `json:"collection"`
	QueryBy    string          `json:"query_by"`
	Fields     []string        `json:"compared_fields"`
	Unordered  []string        `json:"unordered_fields"`
	Minimum    int             `json:"minimum_documents"`
	Schema     []taxonomyField `json:"schema_fields"`
}

type taxonomyContract struct {
	Collections   []taxonomySpec      `json:"collections"`
	PostingSchema []taxonomyField     `json:"job_posting_schema_fields"`
	MacroAliases  map[string][]string `json:"location_macro_aliases"`
	Queries       map[string]string   `json:"queries"`
}

// Static data ported from the Python verifier, embedded without a Python
// runtime dependency. The migration oracle checks this contract against the
// retained Python definitions until their cold rollback window has elapsed.
//
//go:embed taxonomy_contract.json
var taxonomyContractJSON []byte

func loadTaxonomyContract() (taxonomyContract, error) {
	var contract taxonomyContract
	if err := json.Unmarshal(taxonomyContractJSON, &contract); err != nil {
		return contract, err
	}
	if len(contract.Collections) != 5 || len(contract.PostingSchema) == 0 || len(contract.Queries) != 9 {
		return contract, errors.New("incomplete embedded taxonomy contract")
	}
	return contract, nil
}
