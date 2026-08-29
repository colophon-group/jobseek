package main

import (
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const address = ":8080"

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("/robots.txt", func(w http.ResponseWriter, r *http.Request) {
		write(w, http.StatusOK, "text/plain; charset=utf-8", "User-agent: *\nDisallow: /blocked\nAllow: /\n", nil)
	})
	mux.HandleFunc("/navigation", func(w http.ResponseWriter, r *http.Request) {
		write(w, http.StatusOK, "text/html; charset=utf-8", page("navigation", `<main id="content">synthetic navigation</main><script src="/static/navigation.js"></script>`), nil)
	})
	mux.HandleFunc("/javascript", func(w http.ResponseWriter, r *http.Request) {
		write(w, http.StatusOK, "text/html; charset=utf-8", page("javascript", `<main id="content">synthetic JavaScript</main><script>window.syntheticAnswer=40+2;document.documentElement.dataset.ready="true";</script>`), nil)
	})
	mux.HandleFunc("/redirect", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Location", "/navigation")
		write(w, http.StatusFound, "text/plain; charset=utf-8", "synthetic redirect\n", nil)
	})
	mux.HandleFunc("/tdm-header", func(w http.ResponseWriter, r *http.Request) {
		write(w, http.StatusOK, "text/html; charset=utf-8", page("tdm-header", `<main>reserved</main>`), map[string]string{"TDM-Reservation": "1"})
	})
	mux.HandleFunc("/tdm-meta", func(w http.ResponseWriter, r *http.Request) {
		body := `<!doctype html><html><head><meta name="tdm-reservation" content="1"><title>tdm-meta</title></head><body><main>reserved</main></body></html>`
		write(w, http.StatusOK, "text/html; charset=utf-8", body, nil)
	})
	mux.HandleFunc("/subresource-tdm", func(w http.ResponseWriter, r *http.Request) {
		write(w, http.StatusOK, "text/html; charset=utf-8", page("subresource-tdm", `<main>subresource policy</main><script src="/static/tdm.js"></script>`), nil)
	})
	mux.HandleFunc("/robots-blocked", func(w http.ResponseWriter, r *http.Request) {
		write(w, http.StatusOK, "text/html; charset=utf-8", page("robots-blocked", `<main>robots policy</main><script src="/blocked/script.js"></script>`), nil)
	})
	mux.HandleFunc("/request-overflow", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Location", "/request-overflow/1")
		write(w, http.StatusFound, "text/plain; charset=utf-8", "synthetic request chain\n", nil)
	})
	mux.HandleFunc("/request-overflow/", func(w http.ResponseWriter, r *http.Request) {
		current, err := strconv.Atoi(strings.TrimPrefix(r.URL.EscapedPath(), "/request-overflow/"))
		if err != nil || current < 1 || current > 12 {
			write(w, http.StatusBadRequest, "text/plain; charset=utf-8", "invalid synthetic request chain\n", nil)
			return
		}
		if current < 12 {
			w.Header().Set("Location", fmt.Sprintf("/request-overflow/%d", current+1))
			write(w, http.StatusFound, "text/plain; charset=utf-8", "synthetic request chain\n", nil)
			return
		}
		write(w, http.StatusOK, "text/html; charset=utf-8", page("request-overflow", `<main>request guard failed</main>`), nil)
	})
	mux.HandleFunc("/byte-overflow", func(w http.ResponseWriter, r *http.Request) {
		write(w, http.StatusOK, "text/html; charset=utf-8", page("byte-overflow", `<script src="/static/large.js"></script>`), nil)
	})
	mux.HandleFunc("/hang", func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(30 * time.Second)
		write(w, http.StatusOK, "text/html; charset=utf-8", page("hang", `<main>late</main>`), nil)
	})
	mux.HandleFunc("/static/navigation.js", func(w http.ResponseWriter, r *http.Request) {
		body := `const script=document.createElement("script");script.src="/static/dynamic.js";document.head.appendChild(script);`
		write(w, http.StatusOK, "application/javascript; charset=utf-8", body, nil)
	})
	mux.HandleFunc("/static/dynamic.js", func(w http.ResponseWriter, r *http.Request) {
		write(w, http.StatusOK, "application/javascript; charset=utf-8", `document.documentElement.dataset.dynamic="loaded";document.documentElement.dataset.ready="true";`, nil)
	})
	mux.HandleFunc("/static/tdm.js", func(w http.ResponseWriter, r *http.Request) {
		write(w, http.StatusOK, "application/javascript; charset=utf-8", `document.documentElement.dataset.ready="blocked";`, map[string]string{"TDM-Reservation": "1"})
	})
	mux.HandleFunc("/static/large.js", func(w http.ResponseWriter, r *http.Request) {
		write(w, http.StatusOK, "application/javascript; charset=utf-8", strings.Repeat(" ", 32768)+"void 0;", nil)
	})
	mux.HandleFunc("/blocked/script.js", func(w http.ResponseWriter, r *http.Request) {
		write(w, http.StatusInternalServerError, "application/javascript; charset=utf-8", "throw new Error('robots guard failed');", nil)
	})

	server := &http.Server{
		Addr:              address,
		Handler:           requestLog(mux),
		ReadHeaderTimeout: 2 * time.Second,
	}
	log.Printf("fixture listening on %s", address)
	log.Fatal(server.ListenAndServe())
}

func page(title, content string) string {
	return `<!doctype html><html><head><meta charset="utf-8"><title>` + title + `</title></head><body>` + content + `</body></html>`
}

func write(w http.ResponseWriter, status int, contentType, body string, headers map[string]string) {
	for name, value := range headers {
		w.Header().Set(name, value)
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	w.WriteHeader(status)
	_, _ = w.Write([]byte(body))
}

func requestLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		log.Printf("%s %s", r.Method, r.URL.EscapedPath())
		next.ServeHTTP(w, r)
	})
}
