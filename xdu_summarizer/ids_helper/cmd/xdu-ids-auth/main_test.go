package main

import (
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"testing"
)

func TestResolveCodeTypeRejectsUnknownChannel(t *testing.T) {
	if _, err := resolveCodeType("carrier-pigeon"); err == nil {
		t.Fatal("unknown second-factor channels must fail instead of silently selecting one")
	}
}

func TestCollectCookiesReturnsDownloaderCredentials(t *testing.T) {
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	target, _ := url.Parse("https://chaoxing.com/")
	jar.SetCookies(target, []*http.Cookie{
		{Name: "_d", Value: "d-value"},
		{Name: "UID", Value: "uid-value"},
		{Name: "vc3", Value: "vc3-value"},
	})

	cookies := collectCookies(jar)

	for _, name := range []string{"_d", "UID", "vc3"} {
		if cookies[name] == "" {
			t.Fatalf("required downloader cookie %s was not collected", name)
		}
	}
}
