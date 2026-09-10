package idsauth

import (
	"image"
	"image/color"
	"math"
	"testing"
)

// buildPuzzleAndPiece creates a random puzzle and a full-height piece whose
// opaque region is the puzzle strip at column px (the IDS piece slides only
// horizontally, so piece y == puzzle y). The NCC solver should recover
// offset ≈ px / puzzleWidth.
func buildPuzzleAndPiece(px, pw int) (image.Image, image.Image) {
	puzzle := image.NewRGBA(image.Rect(0, 0, 200, 150))
	// Deterministic pseudo-random pattern (unique enough for NCC).
	for y := 0; y < 150; y++ {
		for x := 0; x < 200; x++ {
			v := uint8((x*7 + y*13 + x*y*3 + (x^y)*11) & 0xff)
			puzzle.SetRGBA(x, y, color.RGBA{R: v, G: uint8((v + 40) & 0xff), B: uint8((v + 80) & 0xff), A: 255})
		}
	}
	piece := image.NewRGBA(image.Rect(0, 0, pw, 150))
	// Opaque region = full piece, content = puzzle columns [px, px+pw).
	for y := 0; y < 150; y++ {
		for x := 0; x < pw; x++ {
			src := puzzle.RGBAAt(px+x, y)
			piece.SetRGBA(x, y, color.RGBA{R: src.R, G: src.G, B: src.B, A: 255})
		}
	}
	return puzzle, piece
}

func TestSolveSliderOffsetFindsPiece(t *testing.T) {
	cases := []struct {
		px, pw int
	}{
		{70, 100},
		{40, 80},
		{120, 70},
		{60, 60},
	}
	for _, c := range cases {
		puzzle, piece := buildPuzzleAndPiece(c.px, c.pw)
		frac := solveSliderOffset(puzzle, piece)
		if frac < 0 {
			t.Fatalf("px=%d pw=%d: solver returned -1", c.px, c.pw)
		}
		px := frac * float64(puzzle.Bounds().Dx())
		// The recovered offset should match the true piece x within a few px.
		if math.Abs(px-float64(c.px)) > 5 {
			t.Fatalf("px=%d pw=%d: got offset %.1f", c.px, c.pw, px)
		}
	}
}
