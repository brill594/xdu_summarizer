package idsauth

// Browser fingerprint. The IDS `/authserver/bfp/info` endpoint expects a
// stable 32-hex-char upper-case token per browser; it is an anti-automation
// signal. The module only generates it; the caller persists it (e.g. in a
// DB / config) so it stays stable across sessions.

import (
	"crypto/rand"
	"encoding/hex"
	"regexp"
	"strings"
)

var fingerprintPattern = regexp.MustCompile(`^[0-9A-F]{32}$`)

// GenerateBrowserFingerprint returns a fresh 32-hex-char token.
func GenerateBrowserFingerprint() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return strings.ToUpper(hex.EncodeToString(b)), nil
}

// ValidBrowserFingerprint reports whether s is a well-formed token.
func ValidBrowserFingerprint(s string) bool {
	return fingerprintPattern.MatchString(s)
}
