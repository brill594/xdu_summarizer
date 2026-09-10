package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/brill594/xdu-summarizer/ids-helper/internal/idsauth"
)

const serviceURL = "https://learning.xidian.edu.cn/cassso/xidian"

type authRequest struct {
	Username      string `json:"username"`
	Password      string `json:"password"`
	ReAuthChannel string `json:"reauth_channel"`
	Proxy         string `json:"proxy,omitempty"`
}

type codeRequest struct {
	Code string `json:"code"`
}

type authResponse struct {
	Status  string            `json:"status"`
	SentTo  string            `json:"sent_to,omitempty"`
	Cookies map[string]string `json:"cookies,omitempty"`
	Error   string            `json:"error,omitempty"`
}

func main() {
	decoder := json.NewDecoder(os.Stdin)
	encoder := json.NewEncoder(os.Stdout)

	var request authRequest
	if err := decoder.Decode(&request); err != nil {
		fail(encoder, fmt.Errorf("invalid auth request: %w", err))
	}
	request.Username = strings.TrimSpace(request.Username)
	if request.Username == "" || request.Password == "" {
		fail(encoder, errors.New("IDS username and password are required"))
	}

	jar, err := cookiejar.New(nil)
	if err != nil {
		fail(encoder, err)
	}
	httpClient := idsauth.NewChromiumClient(jar, strings.TrimSuffix(request.Proxy, "/"))
	client := idsauth.NewClient(httpClient)
	client.SetRegisterBrowserFingerprint(fingerprintRegistrar(httpClient))
	solver := idsauth.NewSliderCaptchaSolver(httpClient)
	codeType, err := resolveCodeType(request.ReAuthChannel)
	if err != nil {
		fail(encoder, err)
	}

	reAuthHandler := func(ctx context.Context, reAuth *idsauth.ReAuthClient) (string, error) {
		delivery, err := reAuth.SendCode(ctx, codeType)
		if err != nil {
			return "", err
		}
		if err := encoder.Encode(authResponse{
			Status: "reauth_required",
			SentTo: strings.TrimSpace(delivery.Message + " " + delivery.MaskedMobile),
		}); err != nil {
			return "", err
		}

		for {
			var code codeRequest
			if err := decoder.Decode(&code); err != nil {
				return "", fmt.Errorf("failed to read IDS verification code: %w", err)
			}
			ticket, err := reAuth.SubmitCode(ctx, codeType, strings.TrimSpace(code.Code), true)
			if err == nil {
				return ticket, nil
			}
			var rejected *idsauth.ReAuthCodeRejectedError
			if !errors.As(err, &rejected) {
				return "", err
			}
			if err := encoder.Encode(authResponse{Status: "reauth_rejected", Error: err.Error()}); err != nil {
				return "", err
			}
		}
	}

	ticket, err := client.Login(
		context.Background(),
		request.Username,
		request.Password,
		serviceURL,
		idsauth.LoginOptions{
			SolveSliderCaptcha: solver.Solve,
			ReAuthHandler:      reAuthHandler,
		},
	)
	if err != nil {
		fail(encoder, err)
	}
	if _, err := client.FollowRedirects(context.Background(), ticket, idsauth.FollowOptions{}); err != nil {
		fail(encoder, err)
	}

	cookies := collectCookies(jar)
	for _, name := range []string{"_d", "UID", "vc3"} {
		if cookies[name] == "" {
			fail(encoder, fmt.Errorf("IDS login succeeded but required cookie %s is missing", name))
		}
	}
	if err := encoder.Encode(authResponse{Status: "success", Cookies: cookies}); err != nil {
		os.Exit(1)
	}
}

func resolveCodeType(value string) (idsauth.CodeType, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "sms":
		return idsauth.Sms, nil
	case "wechat", "wecom", "enterprise_wechat":
		return idsauth.EnterpriseWechat, nil
	case "email":
		return idsauth.Email, nil
	default:
		return idsauth.CodeType{}, fmt.Errorf("unsupported IDS second-factor channel: %s", value)
	}
}

func fingerprintRegistrar(client *http.Client) func(context.Context) error {
	fingerprint := ""
	return func(ctx context.Context) error {
		if fingerprint == "" {
			value, err := idsauth.GenerateBrowserFingerprint()
			if err != nil {
				return err
			}
			fingerprint = value
		}
		request, err := http.NewRequestWithContext(
			ctx,
			http.MethodGet,
			"https://ids.xidian.edu.cn/authserver/bfp/info?bfp="+fingerprint+
				"&_="+strconv.FormatInt(time.Now().UnixMilli(), 10),
			nil,
		)
		if err != nil {
			return err
		}
		response, err := client.Do(request)
		if err != nil {
			return err
		}
		response.Body.Close()
		return nil
	}
}

func collectCookies(jar http.CookieJar) map[string]string {
	result := map[string]string{}
	for _, rawURL := range []string{
		"https://chaoxing.com/",
		"https://i.mooc.chaoxing.com/",
		"https://newes.chaoxing.com/",
		"https://learning.xidian.edu.cn/",
	} {
		target, err := url.Parse(rawURL)
		if err != nil {
			continue
		}
		for _, cookie := range jar.Cookies(target) {
			result[cookie.Name] = cookie.Value
		}
	}
	return result
}

func fail(encoder *json.Encoder, err error) {
	_ = encoder.Encode(authResponse{Status: "error", Error: err.Error()})
	os.Exit(1)
}
