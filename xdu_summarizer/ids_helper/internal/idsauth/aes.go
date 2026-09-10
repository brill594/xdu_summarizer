package idsauth

// AES-CBC helpers used by the IDS (Xidian unified authentication) protocol.
// Two schemes are needed:
//
//   - Legacy password: the plaintext is prefixed with the fixed string
//     "xidianscriptsxdu" repeated four times, PKCS7-padded *before* the block
//     cipher (so no extra padding is applied), keyed by the page's
//     `pwdEncryptSalt` with IV = "xidianscriptsxdu" (both UTF-8).
//
//   - Slider-CAPTCHA sign: plaintext = 64-byte random nonce + JSON payload,
//     AES-CBC with PKCS7, key = last 16 bytes of the PNG piece image, IV = a
//     16-byte random string.
//
// Both outputs are Base64.

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"errors"
)

const passwordPrefix = "xidianscriptsxduxidianscriptsxduxidianscriptsxduxidianscriptsxdu"

const aesChars = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"

// EncryptLegacyPassword encrypts an IDS password using the legacy scheme
// (the one Traintime PDA / libxduauth / xidian-script uses and the server
// accepts).
func EncryptLegacyPassword(password, salt string) (string, error) {
	raw := append([]byte(passwordPrefix), []byte(password)...)
	// Manual PKCS#7 padding, then CBC with NO additional padding.
	block, err := aes.NewCipher([]byte(salt))
	if err != nil {
		return "", err
	}
	blockSize := block.BlockSize()
	padLen := blockSize - (len(raw) % blockSize)
	padded := make([]byte, len(raw)+padLen)
	copy(padded, raw)
	for i := len(raw); i < len(padded); i++ {
		padded[i] = byte(padLen)
	}
	iv := []byte("xidianscriptsxdu")
	out := make([]byte, len(padded))
	cipher.NewCBCEncrypter(block, iv).CryptBlocks(out, padded)
	return base64.StdEncoding.EncodeToString(out), nil
}

// SignSliderCaptcha signs the Slider-CAPTCHA verification payload.
func SignSliderCaptcha(payload string, key []byte) (string, error) {
	nonce, err := randomAesChars(64)
	if err != nil {
		return "", err
	}
	iv, err := randomAesChars(16)
	if err != nil {
		return "", err
	}
	raw := append([]byte(nonce), []byte(payload)...)
	ct, err := aesCBCPKCS7(raw, key, []byte(iv))
	if err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(ct), nil
}

func aesCBCPKCS7(src, key, iv []byte) ([]byte, error) {
	if len(key) != aes.BlockSize {
		return nil, errors.New("idsauth: AES key must be 16 bytes")
	}
	if len(iv) != aes.BlockSize {
		return nil, errors.New("idsauth: AES IV must be 16 bytes")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	blockSize := block.BlockSize()
	padLen := blockSize - (len(src) % blockSize)
	padded := make([]byte, len(src)+padLen)
	copy(padded, src)
	for i := len(src); i < len(padded); i++ {
		padded[i] = byte(padLen)
	}
	out := make([]byte, len(padded))
	cipher.NewCBCEncrypter(block, iv).CryptBlocks(out, padded)
	return out, nil
}

// randomAesChars returns n chars drawn from the ad-hoc alphabet the IDS
// CAPTCHA signer uses.
func randomAesChars(n int) (string, error) {
	// Use rejection sampling over a slightly-larger byte range to stay uniform.
	var out []byte
	buf := make([]byte, n*2)
	needed := n
	for needed > 0 {
		if _, err := rand.Read(buf); err != nil {
			return "", err
		}
		for _, b := range buf {
			if needed == 0 {
				break
			}
			// Keep only bytes that map safely into the 56-char alphabet.
			if int(b) >= len(aesChars) {
				continue
			}
			out = append(out, aesChars[int(b)])
			needed--
		}
	}
	return string(out), nil
}
