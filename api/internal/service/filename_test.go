package service

import "testing"

func TestSafeFileName(t *testing.T) {
	cases := map[string]string{
		"car.jpg":            "car.jpg",
		"../../etc/passwd":   "passwd",
		`C:\fotos\placa.png`: "placa.png",
		"a b/c d.jpg":        "c_d.jpg",
		"":                   "photo",
		"..":                 "photo",
	}
	for in, want := range cases {
		if got := safeFileName(in); got != want {
			t.Errorf("safeFileName(%q) = %q, want %q", in, got, want)
		}
	}
}
