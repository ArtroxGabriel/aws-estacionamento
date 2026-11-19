package service_test

import (
	"testing"
	"time"

	"api/internal/service"
)

func TestCalculateFee(t *testing.T) {
	p := service.Pricing{Tolerance: 15 * time.Minute, FirstHour: 10, ExtraHour: 5}
	min := time.Minute

	cases := []struct {
		name string
		stay time.Duration
		want float64
	}{
		{"zero", 0, 0},
		{"dentro da tolerância", 10 * min, 0},
		{"exatamente 15 min é grátis", 15 * min, 0},
		{"15 min e 1 s já cobra a 1ª hora", 15*min + time.Second, 10},
		{"exatamente 1 h", 60 * min, 10},
		{"1 h e 1 s cobra 1 hora extra", 60*min + time.Second, 15},
		{"2 h", 120 * min, 15},
		{"2 h e 1 min cobra mais uma hora", 121 * min, 20},
		{"5 h", 300 * min, 30},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := service.CalculateFee(c.stay, p); got != c.want {
				t.Fatalf("stay %v: expected %.2f, got %.2f", c.stay, c.want, got)
			}
		})
	}
}
