package service

import (
	"math"
	"time"
)

// Pricing is the tariff table, modeled on the usual Brazilian parking lots:
// a free tolerance, a price for the first hour and a price for each extra hour
// (or fraction of it).
type Pricing struct {
	Tolerance time.Duration // up to here the stay is free
	FirstHour float64       // from the end of the tolerance up to 1 h
	ExtraHour float64       // each hour or fraction after the first
}

// CalculateFee returns the amount due for a stay of the given length.
//
//	até a tolerância (15 min) → grátis
//	até 1 h                   → FirstHour
//	cada hora (ou fração) a mais → + ExtraHour
func CalculateFee(stay time.Duration, p Pricing) float64 {
	if stay <= p.Tolerance {
		return 0
	}
	if stay <= time.Hour {
		return p.FirstHour
	}
	extraHours := math.Ceil((stay - time.Hour).Hours())
	return math.Round((p.FirstHour+extraHours*p.ExtraHour)*100) / 100
}
