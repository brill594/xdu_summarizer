package idsauth

// IDS second-factor (reAuth / MFA) client. It switches channel, requests a
// dynamic code, and submits it to resume the CAS login.

import (
	"context"
	"encoding/json"
	"fmt"
	"html"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
)

// ReAuthHandler resumes the CAS login after a successful second-factor
// challenge. It is only invoked when the server actually triggers 2FA.
type ReAuthHandler func(ctx context.Context, rc *ReAuthClient) (string, error)

// ReAuthClient drives a single IDS MFA challenge.
type ReAuthClient struct {
	http                       *http.Client
	challengeURI               string
	username                   string
	service                    string
	registerBrowserFingerprint func(context.Context) error

	recipientDescription string
	deliveryUsername     string
	challengePrepared    bool
	preparedCodeType     *CodeType
}

// NewReAuthClient builds a second-factor client.
func NewReAuthClient(
	httpClient *http.Client,
	challengeURI string,
	username string,
	service string,
	registerBrowserFingerprint func(context.Context) error,
) *ReAuthClient {
	return &ReAuthClient{
		http:                       httpClient,
		challengeURI:               challengeURI,
		username:                   username,
		service:                    service,
		registerBrowserFingerprint: registerBrowserFingerprint,
	}
}

// RecipientDescription returns the masked recipient / username shown to the
// user after the channel is selected.
func (rc *ReAuthClient) RecipientDescription() string { return rc.recipientDescription }

func (rc *ReAuthClient) isMultifactor() string {
	if u, err := url.Parse(rc.challengeURI); err == nil {
		if v := u.Query().Get("isMultifactor"); v != "" {
			return v
		}
	}
	return "true"
}

func (rc *ReAuthClient) prepare(ctx context.Context, codeType CodeType) error {
	if !rc.challengePrepared {
		var resp *http.Response
		err := retryTransient(ctx, func() error {
			req, err := http.NewRequestWithContext(ctx, http.MethodGet, rc.challengeURI, nil)
			if err != nil {
				return err
			}
			r, err := rc.http.Do(req)
			if err != nil {
				return err
			}
			resp = r
			return nil
		})
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			return &ReAuthExpiredError{Message: "二次认证已失效，请重新登录"}
		}
		body, err := io.ReadAll(resp.Body)
		if err != nil {
			return err
		}
		rc.deliveryUsername = parseReAuthUserId(string(body))
		if rc.registerBrowserFingerprint != nil {
			if err := rc.registerBrowserFingerprint(ctx); err != nil {
				return err
			}
		}
		rc.challengePrepared = true
	}
	if rc.preparedCodeType != nil && rc.preparedCodeType.ReAuthType == codeType.ReAuthType {
		return nil
	}

	form := url.Values{}
	form.Set("isMultifactor", rc.isMultifactor())
	form.Set("reAuthType", codeType.ReAuthType)
	form.Set("service", rc.service)
	jsonBody, err := rc.post(ctx, "/authserver/reAuthCheck/changeReAuthType.do", form)
	if err != nil {
		return err
	}
	if code, _ := jsonBody["code"].(string); code != "1" {
		msg, _ := jsonBody["message"].(string)
		if msg == "" {
			msg = "无法切换二次认证方式"
		}
		return protocolErrorf("%s", msg)
	}
	if data, ok := jsonBody["data"].(map[string]any); ok {
		if v, ok := data["reAuthUserNameInput"].(string); ok {
			rc.recipientDescription = v
		}
	}
	ct := codeType
	rc.preparedCodeType = &ct
	return nil
}

// SendCode requests a dynamic code for the given channel.
func (rc *ReAuthClient) SendCode(ctx context.Context, codeType CodeType) (CodeDelivery, error) {
	if err := rc.prepare(ctx, codeType); err != nil {
		return CodeDelivery{}, err
	}
	user := rc.deliveryUsername
	if user == "" {
		user = rc.username
	}
	form := url.Values{}
	form.Set("userName", user)
	form.Set("authCodeTypeName", codeType.AuthCodeTypeName)
	jsonBody, err := rc.post(ctx, "/authserver/dynamicCode/getDynamicCodeByReauth.do", form)
	if err != nil {
		return CodeDelivery{}, err
	}
	return ParseCodeDelivery(jsonBody)
}

// SubmitCode submits the code and returns the resolved service URL.
func (rc *ReAuthClient) SubmitCode(ctx context.Context, codeType CodeType, code string, trustDevice bool) (string, error) {
	if err := rc.prepare(ctx, codeType); err != nil {
		return "", err
	}
	code = strings.TrimSpace(code)
	if code == "" {
		return "", &ReAuthCodeRejectedError{Message: "请输入验证码"}
	}

	form := url.Values{}
	form.Set("service", rc.service)
	form.Set("reAuthType", codeType.ReAuthType)
	form.Set("isMultifactor", rc.isMultifactor())
	form.Set("password", "")
	form.Set("dynamicCode", code)
	form.Set("uuid", "")
	form.Set("answer1", "")
	form.Set("answer2", "")
	form.Set("otpCode", "")
	form.Set("skipTmpReAuth", fmt.Sprintf("%t", trustDevice))

	jsonBody, err := rc.post(ctx, "/authserver/reAuthCheck/reAuthSubmit.do", form)
	if err != nil {
		return "", err
	}
	result, err := ParseReAuthSubmit(jsonBody)
	if err != nil {
		return "", err
	}
	switch result.Status {
	case ReAuthFailed:
		return "", &ReAuthCodeRejectedError{Message: result.Message}
	case ReAuthUnauthorized:
		return "", &ReAuthExpiredError{Message: result.Message}
	case ReAuthSuccess:
		// continue
	}

	// Resume the CAS login (the server 302s to the business ticket).
	var lresp *http.Response
	err = retryTransient(ctx, func() error {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, idsOrigin+"/authserver/login", nil)
		if err != nil {
			return err
		}
		if rc.service != "" {
			q := req.URL.Query()
			q.Set("service", rc.service)
			req.URL.RawQuery = q.Encode()
		}
		r, err := rc.http.Do(req)
		if err != nil {
			return err
		}
		lresp = r
		return nil
	})
	if err != nil {
		return "", err
	}
	defer lresp.Body.Close()
	location := lresp.Header.Get("Location")
	if (lresp.StatusCode != http.StatusMovedPermanently && lresp.StatusCode != http.StatusFound) || location == "" {
		return "", protocolErrorf("二次认证成功，但没有收到业务系统登录票据")
	}
	ok, err := IsReAuthLocation(location, lresp.Request.URL.String())
	if err != nil {
		return "", err
	}
	if ok {
		return "", &ReAuthExpiredError{Message: "二次认证未完成，请重新登录"}
	}
	ref, err := url.Parse(location)
	if err != nil {
		return "", err
	}
	return lresp.Request.URL.ResolveReference(ref).String(), nil
}

func (rc *ReAuthClient) post(ctx context.Context, path string, form url.Values) (map[string]any, error) {
	var resp *http.Response
	err := retryTransient(ctx, func() error {
		req, err := http.NewRequestWithContext(
			ctx,
			http.MethodPost,
			idsOrigin+path,
			strings.NewReader(form.Encode()),
		)
		if err != nil {
			return err
		}
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
		req.Header.Set("X-Requested-With", "XMLHttpRequest")
		r, err := rc.http.Do(req)
		if err != nil {
			return err
		}
		resp = r
		return nil
	})
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return decodeJSON(resp)
}

func decodeJSON(resp *http.Response) (map[string]any, error) {
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(body, &m); err != nil {
		// The server can return a non-JSON body; surface a protocol error.
		return nil, protocolErrorf("统一认证返回了非 JSON 响应: %s", html.UnescapeString(strings.TrimSpace(string(body))))
	}
	return m, nil
}

var reAuthUserIdPattern = regexp.MustCompile(`"reAuthUserId"\s*:\s*"([^"\\]+)"`)

func parseReAuthUserId(body string) string {
	m := reAuthUserIdPattern.FindStringSubmatch(body)
	if len(m) < 2 {
		return ""
	}
	return strings.TrimSpace(m[1])
}
