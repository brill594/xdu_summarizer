package idsauth

// Slider CAPTCHA solver for the IDS login. It fetches the puzzle/piece pair,
// locates the piece via normalized cross-correlation, fabricates a human-like
// finger trace, and verifies it. Reproduced from the Traintime PDA client.

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	"image/jpeg"
	"image/png"
	"io"
	"math"
	"math/rand"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

// canvasWidth is the width (px) of the slider track used to translate the
// puzzle offset into a move length. The IDS web widget uses 280.
const canvasWidth = 280

// SliderCaptchaSolver solves the IDS slider CAPTCHA using the caller's HTTP
// client (which must carry the IDS session cookies).
type SliderCaptchaSolver struct {
	http *http.Client
	rng  *rand.Rand

	puzzle    image.Image
	piece     image.Image
	puzzleImg []byte
	pieceImg  []byte
	aesKey    []byte
}

// NewSliderCaptchaSolver builds a solver.
func NewSliderCaptchaSolver(httpClient *http.Client) *SliderCaptchaSolver {
	return &SliderCaptchaSolver{http: httpClient, rng: rand.New(rand.NewSource(time.Now().UnixNano()))}
}

// Solve fetches, locates and verifies the current CAPTCHA. It returns nil on
// success and a non-nil error (see CaptchaSolveFailedError) when it cannot
// solve the CAPTCHA, so it can be used directly as a
// LoginOptions.SolveSliderCaptcha.
func (s *SliderCaptchaSolver) Solve(ctx context.Context) error {
	for attempt := 0; attempt < 6; attempt++ {
		if err := s.updatePuzzle(ctx); err != nil {
			return err
		}
		offset := solveSliderOffset(s.puzzle, s.piece)
		if offset < 0 {
			return &CaptchaSolveFailedError{}
		}
		base := int(math.Round(offset * canvasWidth))
		for _, delta := range []int{1, -1, 2, -2, 3, -3, 4} {
			move := base + delta
			if move < 0 || move > canvasWidth {
				continue
			}
			tracks := generateTracks(s.rng, move)
			// Delay mimicking human movement before verifying.
			time.Sleep(time.Duration(maxInt(tracks[len(tracks)-1].C-100, 0)) * time.Millisecond)
			ok, err := s.verify(ctx, tracks)
			if err != nil {
				return err
			}
			if ok {
				return nil
			}
		}
	}
	return &CaptchaSolveFailedError{}
}

func (s *SliderCaptchaSolver) verify(ctx context.Context, tracks []TrackPoint) (bool, error) {
	payload, err := json.Marshal(map[string]any{
		"canvasLength": canvasWidth,
		"moveLength":   tracks[len(tracks)-1].A,
		"tracks":       tracks,
	})
	if err != nil {
		return false, err
	}
	sign, err := SignSliderCaptcha(string(payload), s.aesKey)
	if err != nil {
		return false, err
	}
	form := url.Values{}
	form.Set("sign", sign)
	req, err := http.NewRequestWithContext(
		ctx, http.MethodPost, idsOrigin+"/authserver/common/verifySliderCaptcha.htl",
		bytes.NewBufferString(form.Encode()),
	)
	if err != nil {
		return false, err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded;charset=UTF-8")
	req.Header.Set("Accept", "application/json, text/javascript, */*; q=0.01")
	req.Header.Set("X-Requested-With", "XMLHttpRequest")
	req.Header.Set("Origin", idsOrigin)
	resp, err := s.http.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return false, err
	}
	var m map[string]any
	if err := json.Unmarshal(body, &m); err != nil {
		return false, nil
	}
	// errorCode == 1 means success.
	code, _ := m["errorCode"].(float64)
	return int(code) == 1, nil
}

func (s *SliderCaptchaSolver) updatePuzzle(ctx context.Context) error {
	u := idsOrigin + "/authserver/common/openSliderCaptcha.htl?_=" + strconv.FormatInt(time.Now().UnixMilli(), 10)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return err
	}
	resp, err := s.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	var m map[string]any
	if err := json.Unmarshal(body, &m); err != nil {
		return fmt.Errorf("idsauth: captcha response not JSON: %s", string(body))
	}
	big, _ := m["bigImage"].(string)
	small, _ := m["smallImage"].(string)
	bigBytes, err := base64.StdEncoding.DecodeString(big)
	if err != nil {
		return err
	}
	smallBytes, err := base64.StdEncoding.DecodeString(small)
	if err != nil {
		return err
	}
	puzzle, err := decodeImage(bigBytes, "jpeg")
	if err != nil {
		return err
	}
	piece, err := decodeImage(smallBytes, "png")
	if err != nil {
		return err
	}
	s.puzzle = puzzle
	s.piece = piece
	s.puzzleImg = bigBytes
	s.pieceImg = smallBytes
	// AES key = last 16 bytes of the piece image file.
	if len(smallBytes) < 16 {
		return fmt.Errorf("idsauth: piece image too small")
	}
	s.aesKey = smallBytes[len(smallBytes)-16:]
	return nil
}

func decodeImage(data []byte, _ string) (image.Image, error) {
	if cfg, err := png.DecodeConfig(bytes.NewReader(data)); err == nil {
		img, err := png.Decode(bytes.NewReader(data))
		if err == nil && cfg.Width > 0 {
			return img, nil
		}
	}
	if _, err := jpeg.DecodeConfig(bytes.NewReader(data)); err == nil {
		return jpeg.Decode(bytes.NewReader(data))
	}
	return nil, fmt.Errorf("idsauth: cannot decode captcha image")
}

// TrackPoint is a single finger-movement sample (x, y, elapsedMs).
type TrackPoint struct {
	A int `json:"a"`
	B int `json:"b"`
	C int `json:"c"`
}

func luminanceAt(img image.Image, x, y int) float64 {
	r, g, b, _ := img.At(x, y).RGBA()
	return 0.299*float64(r>>8) + 0.587*float64(g>>8) + 0.114*float64(b>>8)
}

// imageBbox returns (xLeft, yTop, xRight, yBottom) of the opaque region
// (alpha == 255) in a piece image.
func imageBbox(piece image.Image) (int, int, int, int) {
	b := piece.Bounds()
	xL, yT, xR, yB := b.Dx(), b.Dy(), 0, 0
	for y := b.Min.Y; y < b.Max.Y; y++ {
		for x := b.Min.X; x < b.Max.X; x++ {
			_, _, _, a := piece.At(x, y).RGBA()
			if a == 0xffff {
				if x < xL {
					xL = x
				}
				if y < yT {
					yT = y
				}
				if x > xR {
					xR = x
				}
				if y > yB {
					yB = y
				}
			}
		}
	}
	if xR == 0 || yB == 0 {
		// fully transparent piece — return the whole image bounds
		return b.Min.X, b.Min.Y, b.Max.X - 1, b.Max.Y - 1
	}
	return xL, yT, xR, yB
}

// solveSliderOffset returns the horizontal offset of the piece within the
// puzzle as a fraction of the puzzle width, or -1 when not found.
func solveSliderOffset(puzzle, piece image.Image) float64 {
	const border = 24
	xL0, yT0, xR0, yB0 := imageBbox(piece)
	xL := xL0 + border
	yT := yT0 + border
	xR := xR0 - border
	yB := yB0 - border

	windowWidth := xR - xL + 1
	windowHeight := yB - yT + 1
	if windowWidth <= 0 || windowHeight <= 0 {
		return -1
	}
	pWidth := puzzle.Bounds().Dx()
	pieceW := piece.Bounds().Dx()
	bigWidth := pWidth - pieceW + windowWidth
	if bigWidth <= windowWidth {
		return -1
	}

	area := float64(windowWidth * windowHeight)
	templateMean := imageSum(piece, xL, yT, windowWidth, windowHeight) / area
	template := make([]float64, windowWidth*windowHeight)
	idx := 0
	for y := yT; y < yT+windowHeight; y++ {
		for x := xL; x < xL+windowWidth; x++ {
			template[idx] = luminanceAt(piece, x, y) - templateMean
			idx++
		}
	}

	columnSums := make([]float64, bigWidth)
	for x := 0; x < bigWidth; x++ {
		columnSums[x] = imageSum(puzzle, x+xL, yT, 1, windowHeight)
	}

	windowSum := 0.0
	for x := 0; x < windowWidth; x++ {
		windowSum += columnSums[x]
	}

	nccMax := imageNcc(puzzle, xL, yT, windowWidth, windowHeight, template, windowSum/area)
	xMax := 0
	for x := 1; x < bigWidth-windowWidth; x++ {
		windowSum += columnSums[x+windowWidth-1] - columnSums[x-1]
		ncc := imageNcc(puzzle, x+xL, yT, windowWidth, windowHeight, template, windowSum/area)
		if ncc > nccMax {
			nccMax = ncc
			xMax = x
		}
	}
	return float64(xMax) / float64(pWidth)
}

func imageSum(img image.Image, xL, yT, width, height int) float64 {
	var sum float64
	for y := yT; y < yT+height; y++ {
		for x := xL; x < xL+width; x++ {
			sum += luminanceAt(img, x, y)
		}
	}
	return sum
}

func imageNcc(
	window image.Image,
	xL, yT, width, height int,
	template []float64,
	meanW float64,
) float64 {
	var sumWt, sumWw float64
	sumWw = 0.000001
	idx := 0
	for y := yT; y < yT+height; y++ {
		for x := xL; x < xL+width; x++ {
			w := luminanceAt(window, x, y) - meanW
			sumWt += w * template[idx]
			sumWw += w * w
			idx++
		}
	}
	return sumWt / sumWw
}

// generateTracks fabricates a human-like finger trace to the final offset.
func generateTracks(rng *rand.Rand, offs int) []TrackPoint {
	genTracksNorm := 1.0 / (1.0 + math.Exp(-7.0*(1.0-0.42)))
	tracks := []TrackPoint{{A: 0, B: 0, C: 0}}
	n := rng.Intn(5) + 10
	b := 0
	for i := 0; i < n; i++ {
		z := (1.0 / (1.0 + math.Exp(-7.0*(float64(i)/float64(n)-0.42)))) / genTracksNorm
		a := minInt(offs-1, maxInt(tracks[len(tracks)-1].A+1, int(math.Round(float64(offs)*z))))
		r := rng.Float64()
		switch {
		case r < 0.65:
			b--
		case r < 0.80:
			b++
		}
		b = maxInt(-10, minInt(10, b))
		tracks = append(tracks, TrackPoint{A: a, B: b, C: rng.Intn(201) + 300})
	}
	tracks = append(tracks, TrackPoint{A: offs, B: b, C: rng.Intn(201) + 300})
	return tracks
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
