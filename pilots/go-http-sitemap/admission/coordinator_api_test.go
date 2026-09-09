package admission_test

import (
	"reflect"
	"testing"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/admission"
)

func TestCoordinatorDoesNotExposeProcessorBypass(t *testing.T) {
	if _, exposed := reflect.TypeOf((*admission.Coordinator)(nil)).MethodByName("Process"); exposed {
		t.Fatal("Coordinator exposes Process and bypasses bounded dispatch")
	}
}
