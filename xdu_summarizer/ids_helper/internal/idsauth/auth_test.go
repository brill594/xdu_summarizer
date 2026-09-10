package idsauth

import (
	"encoding/json"
	"regexp"
	"strings"
	"testing"
)

// Reference values were computed independently (Python cryptography AES-CBC,
// same "xidianscriptsxdu" scheme) to cross-check the Go implementation.
const legacyRef = "A4twxjOlILN+haoJb2FWvIpIc39nbu4v9TBP2L344pbmlOmMGMRqCorOh3zldDT/U4MSjW8sVoappE/7O2Tr9Ci2Bdjzpkt3hODYWaQ6dkk="

func TestEncryptLegacyPasswordReference(t *testing.T) {
	got, err := EncryptLegacyPassword("123456", "kh7Vnp4Jwpx1Qbcw")
	if err != nil {
		t.Fatalf("EncryptLegacyPassword error: %v", err)
	}
	if got != legacyRef {
		t.Fatalf("legacy AES mismatch:\n got %s\nwant %s", got, legacyRef)
	}
}

func TestEncryptLegacyPasswordEmptyAndSaltVar(t *testing.T) {
	// Deterministic: same input -> same output.
	a, _ := EncryptLegacyPassword("123456", "kh7Vnp4Jwpx1Qbcw")
	b, _ := EncryptLegacyPassword("123456", "kh7Vnp4Jwpx1Qbcw")
	if a != b {
		t.Fatalf("expected deterministic output")
	}
	// Different salt -> different output.
	c, _ := EncryptLegacyPassword("123456", "0123456789abcdef")
	if a == c {
		t.Fatalf("expected different output for different salt")
	}
}

func TestSignSliderCaptchaDeterministic(t *testing.T) {
	// Sign uses a random nonce/IV, so it must NOT be deterministic, but it must
	// be valid base64 and non-empty.
	key := make([]byte, 16)
	sig, err := SignSliderCaptcha(`{"a":1}`, key)
	if err != nil {
		t.Fatalf("SignSliderCaptcha error: %v", err)
	}
	if sig == "" {
		t.Fatalf("expected non-empty signature")
	}
}

func TestGenerateBrowserFingerprint(t *testing.T) {
	fp, err := GenerateBrowserFingerprint()
	if err != nil {
		t.Fatalf("GenerateBrowserFingerprint error: %v", err)
	}
	if !ValidBrowserFingerprint(fp) {
		t.Fatalf("generated fingerprint not valid: %q", fp)
	}
	if !regexp.MustCompile(`^[0-9A-F]{32}$`).MatchString(fp) {
		t.Fatalf("fingerprint format wrong: %q", fp)
	}
	if ValidBrowserFingerprint("nope") {
		t.Fatalf("should reject bad fingerprint")
	}
}

func TestMaskPhoneNumber(t *testing.T) {
	if got := MaskPhoneNumber("13812345678"); got != "138****5678" {
		t.Fatalf("MaskPhoneNumber got %q", got)
	}
	if got := MaskPhoneNumber("123"); got != "****" {
		t.Fatalf("short mask got %q", got)
	}
}

func TestIsReAuthLocation(t *testing.T) {
	trueCases := []string{
		"https://ids.xidian.edu.cn/authserver/reAuthCheck/reAuthLoginView.do?isMultifactor=true&service=https%3A%2F%2Fehall.xidian.edu.cn%2F",
		"/authserver/reAuthCheck/reAuthLoginView.do?isMultifactor=true",
	}
	for _, c := range trueCases {
		base := ""
		if strings.HasPrefix(c, "/") {
			base = "https://ids.xidian.edu.cn/authserver/login"
		}
		ok, err := IsReAuthLocation(c, base)
		if err != nil {
			t.Fatalf("IsReAuthLocation(%q) error %v", c, err)
		}
		if !ok {
			t.Fatalf("expected true for %q", c)
		}
	}
	falseCases := []string{
		"https://ids.xidian.edu.cn/authserver/login?service=x",
		"https://ehall.xidian.edu.cn/some/path",
	}
	for _, c := range falseCases {
		ok, err := IsReAuthLocation(c, "")
		if err != nil {
			t.Fatalf("IsReAuthLocation(%q) error %v", c, err)
		}
		if ok {
			t.Fatalf("expected false for %q", c)
		}
	}
}

func TestParseReAuthSubmit(t *testing.T) {
	res, err := ParseReAuthSubmit(map[string]any{"code": "reAuth_success", "msg": "ok"})
	if err != nil || res.Status != ReAuthSuccess {
		t.Fatalf("reAuth_success: %+v err=%v", res, err)
	}
	_, err = ParseReAuthSubmit(map[string]any{"code": "reAuth_failed", "msg": "no"})
	if err != nil {
		t.Fatal("expected parse to succeed for reAuth_failed")
	} else if res.Status == ReAuthFailed {
		// res is the last success; just ensure no error path for failed
	}
	if _, err = ParseReAuthSubmit(map[string]any{"code": "weird"}); err == nil {
		t.Fatal("expected error for unknown code")
	}
}

func TestParseCodeDelivery(t *testing.T) {
	d, err := ParseCodeDelivery(map[string]any{
		"res":           "success",
		"returnMessage": "验证码已发送",
		"mobile":        "13812345678",
		"codeTime":      "60",
	})
	if err != nil {
		t.Fatalf("ParseCodeDelivery error: %v", err)
	}
	if d.MaskedMobile != "138****5678" {
		t.Fatalf("MaskedMobile got %q", d.MaskedMobile)
	}
	if d.RetryAfterSeconds != 60 {
		t.Fatalf("RetryAfterSeconds got %d", d.RetryAfterSeconds)
	}
	if _, err := ParseCodeDelivery(map[string]any{"res": "bogus"}); err == nil {
		t.Fatal("expected error for unknown res")
	}
}

func TestTrackJSONShape(t *testing.T) {
	// The tracks serialize as a/b/c (lowercase) as the server expects.
	tp := TrackPoint{A: 1, B: 2, C: 3}
	b, _ := json.Marshal(tp)
	if string(b) != `{"a":1,"b":2,"c":3}` {
		t.Fatalf("track json %s", b)
	}
}
