// Command gen turns demo/decks.json into Stella deck exports with a believable
// study history, ready to import into a fresh instance (see demo/seed.py).
//
// Studied decks are put through weeks of simulated daily sessions using
// Stella's own SM2 scheduler (src/srs) with a fake clock, so the scheduling
// state and review log are exactly what Stella would have recorded. Deck
// covers and flag images are drawn here, so the demo needs no binary assets.
//
// Usage: go run . <decks.json> <out_dir>
//
// It writes <out_dir>/NN.json (one deck export each) and <out_dir>/manifest.json
// (file, deck name, group and tags, for the seeding step).
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"github.com/contre95/stella/src/srs"
)

// --- input (demo/decks.json) ---

type input struct {
	Groups []string    `json:"groups"`
	Decks  []deckInput `json:"decks"`
}

type deckInput struct {
	Name        string      `json:"name"`
	Description string      `json:"description"`
	Group       string      `json:"group"`
	Tags        []string    `json:"tags"`
	Cover       [2]string   `json:"cover"`
	Study       *study      `json:"study"` // nil: never studied, every card unseen
	Facts       []factInput `json:"facts"`
}

type study struct {
	StartDaysAgo int `json:"startDaysAgo"`
	NewPerDay    int `json:"newPerDay"`
}

type factInput struct {
	Front   string `json:"front"`
	Back    string `json:"back"`
	Reverse bool   `json:"reverse"`
	Flag    *flag  `json:"flag"` // drawn as the front image
}

type flag struct {
	Dir    string   `json:"dir"` // "v" (vertical stripes) or "h"
	Colors []string `json:"colors"`
}

// --- output: mirrors the deck export format in src/features/portability ---

type deckExport struct {
	Name        string       `json:"name"`
	Description string       `json:"description,omitempty"`
	Image       string       `json:"image,omitempty"`
	ImageData   []byte       `json:"image_data,omitempty"`
	Facts       []factExport `json:"facts"`
}

type factExport struct {
	Front          string      `json:"front"`
	Back           string      `json:"back"`
	ImageFront     string      `json:"image_front,omitempty"`
	ImageFrontData []byte      `json:"image_front_data,omitempty"`
	Reverse        bool        `json:"reverse"`
	Created        int64       `json:"created,omitempty"`
	Modified       int64       `json:"modified,omitempty"`
	Cards          []cardStats `json:"cards,omitempty"`
}

type cardStats struct {
	View              srs.CardView  `json:"view"`
	Grade             int           `json:"grade"`
	Easiness          float64       `json:"easiness"`
	AcqReps           int           `json:"acq_reps"`
	RetReps           int           `json:"ret_reps"`
	Lapses            int           `json:"lapses"`
	AcqRepsSinceLapse int           `json:"acq_reps_since_lapse"`
	RetRepsSinceLapse int           `json:"ret_reps_since_lapse"`
	LastRep           int64         `json:"last_rep"`
	NextRep           int64         `json:"next_rep"`
	Active            bool          `json:"active"`
	Reviews           []reviewStats `json:"reviews,omitempty"`
}

type reviewStats struct {
	Grade             int    `json:"grade"`
	RetReps           int    `json:"ret_reps"`
	ScheduledInterval int64  `json:"scheduled_interval"`
	ActualInterval    int64  `json:"actual_interval"`
	ThinkingTime      int64  `json:"thinking_time"`
	Timestamp         int64  `json:"timestamp"`
	Source            string `json:"source,omitempty"`
}

type manifestEntry struct {
	File  string   `json:"file"`
	Name  string   `json:"name"`
	Group string   `json:"group,omitempty"`
	Tags  []string `json:"tags,omitempty"`
}

// --- simulation ---

// simCard is one card being studied: its scheduling state, its review log and
// how easy this particular card is to remember.
type simCard struct {
	card    srs.Card
	reviews []reviewStats
	recall  float64
}

type simulator struct {
	rng   *rand.Rand
	clock time.Time
	sched *srs.Scheduler
}

func newSimulator(seed int64) *simulator {
	s := &simulator{rng: rand.New(rand.NewSource(seed))}
	// Same defaults as config.example.yaml; the demo instance runs in UTC.
	s.sched = srs.NewScheduler(srs.Params{DayStartsAt: 3, NonMemorisedCardsInHand: 10},
		time.UTC, func() time.Time { return s.clock }, seededRand{s.rng})
	return s
}

type seededRand struct{ r *rand.Rand }

func (r seededRand) Choice(opts []int) int { return opts[r.r.Intn(len(opts))] }
func (r seededRand) Uniform(low, high float64) float64 {
	return low + r.r.Float64()*(high-low)
}

// grade records one repetition the way the review service does
// (src/features/review/service.go).
func (s *simulator) grade(c *simCard, grade int, source string) {
	scheduled := c.card.NextRep - c.card.LastRep
	actual := s.clock.Unix() - c.card.LastRep
	if c.card.Grade == srs.GradeUnseen {
		actual = 0
	}
	s.sched.GradeAnswer(&c.card, grade, false)
	c.reviews = append(c.reviews, reviewStats{
		Grade:             grade,
		RetReps:           c.card.RetReps,
		ScheduledInterval: scheduled,
		ActualInterval:    actual,
		ThinkingTime:      int64(2 + s.rng.Intn(14)),
		Timestamp:         s.clock.Unix(),
		Source:            source,
	})
	s.clock = s.clock.Add(time.Duration(10+s.rng.Intn(40)) * time.Second)
}

// answer grades a card until it is memorised (grade >= 2), as a learner who
// keeps seeing a failed card in the same session would.
func (s *simulator) answer(c *simCard, recall float64, source string) {
	for {
		if s.rng.Float64() < recall {
			s.grade(c, []int{3, 4, 4, 5}[s.rng.Intn(4)], source)
			return
		}
		s.grade(c, []int{0, 1, 1}[s.rng.Intn(3)], source)
		recall = 0.75
		s.clock = s.clock.Add(time.Duration(1+s.rng.Intn(4)) * time.Minute)
	}
}

// session reviews what is due at the current clock, then learns up to
// newCards unseen cards. dueShare < 1 leaves part of the due cards undone.
func (s *simulator) session(cards []*simCard, newCards int, dueShare float64, source string) {
	now := s.sched.AdjustedNow(s.clock)
	for _, c := range cards {
		if c.card.Grade != srs.GradeUnseen && c.card.NextRep <= now && s.rng.Float64() < dueShare {
			// Late and lapsed cards are harder to recall.
			recall := c.recall
			if c.card.Lapses > 0 {
				recall -= 0.08
			}
			s.answer(c, recall, source)
		}
	}
	for _, c := range cards {
		if newCards == 0 {
			break
		}
		if c.card.Grade == srs.GradeUnseen {
			s.answer(c, c.recall-0.25, source)
			newCards--
		}
	}
}

// studyDeck runs one session a day from st.StartDaysAgo until yesterday, plus a
// partial one today, so the demo has history, streaks and cards still due.
func (s *simulator) studyDeck(cards []*simCard, st study, now time.Time) {
	today := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	for ago := st.StartDaysAgo; ago >= 1; ago-- {
		if s.rng.Float64() < 0.12 { // the odd missed day
			continue
		}
		day := today.AddDate(0, 0, -ago)
		s.clock = day.Add(time.Duration(7*3600+s.rng.Intn(14*3600)) * time.Second)
		source := "web"
		if s.rng.Float64() < 0.2 {
			source = "x4" // studied offline on the e-ink device
		}
		s.session(cards, st.NewPerDay, 1, source)
	}
	if t := now.Add(-90 * time.Minute); t.After(today.Add(4 * time.Hour)) {
		s.clock = t
		s.session(cards, 1, 0.4, "web")
	}
}

func unseen(view srs.CardView) srs.Card {
	c := srs.Card{View: view, Active: true}
	c.ResetLearningData()
	return c
}

// --- images ---

func hexColor(h string) color.RGBA {
	v, err := strconv.ParseUint(h[1:], 16, 32)
	if err != nil || len(h) != 7 {
		panic("bad color " + h)
	}
	return color.RGBA{uint8(v >> 16), uint8(v >> 8), uint8(v), 255}
}

func mix(a, b color.RGBA, t float64) color.RGBA {
	l := func(x, y uint8) uint8 { return uint8(float64(x)*(1-t) + float64(y)*t) }
	return color.RGBA{l(a.R, b.R), l(a.G, b.G), l(a.B, b.B), 255}
}

func encodePNG(img image.Image) []byte {
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		panic(err)
	}
	return buf.Bytes()
}

// cover draws a diagonal gradient with a few soft rings, like a starry sky.
func cover(from, to string, rng *rand.Rand) []byte {
	const w, h = 640, 360
	a, b := hexColor(from), hexColor(to)
	type ring struct{ x, y, r float64 }
	rings := make([]ring, 6)
	for i := range rings {
		rings[i] = ring{rng.Float64() * w, rng.Float64() * h, 30 + rng.Float64()*120}
	}
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			c := mix(a, b, (float64(x)/w+float64(y)/h)/2)
			for _, r := range rings {
				d := math.Abs(math.Hypot(float64(x)-r.x, float64(y)-r.y) - r.r)
				if d < 3 {
					c = mix(c, color.RGBA{255, 255, 255, 255}, 0.18*(1-d/3))
				}
			}
			img.SetRGBA(x, y, c)
		}
	}
	return encodePNG(img)
}

func drawFlag(f *flag) []byte {
	const w, h = 600, 400
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	n := len(f.Colors)
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			i := y * n / h
			if f.Dir == "v" {
				i = x * n / w
			}
			img.SetRGBA(x, y, hexColor(f.Colors[i]))
		}
	}
	return encodePNG(img)
}

// --- main ---

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: gen <decks.json> <out_dir>")
		os.Exit(2)
	}
	raw, err := os.ReadFile(os.Args[1])
	check(err)
	var in input
	check(json.Unmarshal(raw, &in))
	out := os.Args[2]
	check(os.MkdirAll(out, 0o755))

	now := time.Now().UTC()
	var manifest []manifestEntry
	for i, d := range in.Decks {
		sim := newSimulator(int64(i + 1))
		exp := deckExport{Name: d.Name, Description: d.Description, Image: "cover.png",
			ImageData: cover(d.Cover[0], d.Cover[1], sim.rng)}

		// Forward cards first, then the reverse ones, so reverse cards are
		// introduced after their forward sisters.
		var forward, reverse []*simCard
		for range d.Facts {
			forward = append(forward, &simCard{card: unseen(srs.ViewForward), recall: 0.86 + sim.rng.Float64()*0.12})
		}
		for _, f := range d.Facts {
			var c *simCard
			if f.Reverse {
				c = &simCard{card: unseen(srs.ViewReverse), recall: 0.8 + sim.rng.Float64()*0.15}
			}
			reverse = append(reverse, c)
		}
		created := now.AddDate(0, 0, -2)
		if d.Study != nil {
			created = now.AddDate(0, 0, -d.Study.StartDaysAgo-1)
			all := append([]*simCard{}, forward...)
			for _, c := range reverse {
				if c != nil {
					all = append(all, c)
				}
			}
			sim.studyDeck(all, *d.Study, now)
		}

		for j, f := range d.Facts {
			fe := factExport{Front: f.Front, Back: f.Back, Reverse: f.Reverse,
				Created: created.Unix(), Modified: created.Unix()}
			if f.Flag != nil {
				if fe.Front == "" {
					fe.Front = "{center}Which country's flag is this?{/center}"
				}
				fe.ImageFront = fmt.Sprintf("flag-%d.png", j)
				fe.ImageFrontData = drawFlag(f.Flag)
			}
			for _, c := range []*simCard{forward[j], reverse[j]} {
				if c == nil {
					continue
				}
				fe.Cards = append(fe.Cards, cardStats{
					View: c.card.View, Grade: c.card.Grade, Easiness: c.card.Easiness,
					AcqReps: c.card.AcqReps, RetReps: c.card.RetReps, Lapses: c.card.Lapses,
					AcqRepsSinceLapse: c.card.AcqRepsSinceLapse, RetRepsSinceLapse: c.card.RetRepsSinceLapse,
					LastRep: c.card.LastRep, NextRep: c.card.NextRep, Active: true, Reviews: c.reviews,
				})
			}
			exp.Facts = append(exp.Facts, fe)
		}

		file := fmt.Sprintf("%02d.json", i+1)
		data, err := json.Marshal(exp)
		check(err)
		check(os.WriteFile(filepath.Join(out, file), data, 0o644))
		manifest = append(manifest, manifestEntry{File: file, Name: d.Name, Group: d.Group, Tags: d.Tags})
	}

	data, err := json.MarshalIndent(map[string]any{"groups": in.Groups, "decks": manifest}, "", "  ")
	check(err)
	check(os.WriteFile(filepath.Join(out, "manifest.json"), data, 0o644))
	fmt.Printf("wrote %d decks to %s\n", len(manifest), out)
}

func check(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
