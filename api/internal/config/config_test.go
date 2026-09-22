package config_test

import (
	"os"
	"testing"

	"api/internal/config"
)

func TestLoadConfig_Defaults(t *testing.T) {
	_ = os.Unsetenv("PORT")
	_ = os.Unsetenv("AWS_ENDPOINT_URL")
	_ = os.Unsetenv("AWS_REGION")
	_ = os.Unsetenv("TOTAL_PARKING_SPOTS")
	_ = os.Unsetenv("FIXED_PARKING_RATE")

	cfg := config.LoadConfig()

	if cfg.Port != "8080" {
		t.Fatalf("expected default port 8080, got %s", cfg.Port)
	}
	if cfg.AWSRegion != "us-east-1" {
		t.Fatalf("expected default region us-east-1, got %s", cfg.AWSRegion)
	}
	if cfg.AWSAccessKeyID != "mock_key" {
		t.Fatalf("expected default mock_key, got %s", cfg.AWSAccessKeyID)
	}
	if cfg.AWSSecretAccessKey != "mock_secret" {
		t.Fatalf("expected default mock_secret, got %s", cfg.AWSSecretAccessKey)
	}
	if cfg.TotalParkingSpots != 50 {
		t.Fatalf("expected 50 spots, got %d", cfg.TotalParkingSpots)
	}
	if cfg.FixedParkingRate != 10.0 {
		t.Fatalf("expected rate 10.0, got %f", cfg.FixedParkingRate)
	}
}

func TestLoadConfig_CustomEnv(t *testing.T) {
	t.Setenv("PORT", "9090")
	t.Setenv("TOTAL_PARKING_SPOTS", "120")
	t.Setenv("FIXED_PARKING_RATE", "25.50")
	t.Setenv("S3_BUCKET_NAME", "custom-bucket")

	cfg := config.LoadConfig()

	if cfg.Port != "9090" {
		t.Fatalf("expected port 9090, got %s", cfg.Port)
	}
	if cfg.TotalParkingSpots != 120 {
		t.Fatalf("expected 120 spots, got %d", cfg.TotalParkingSpots)
	}
	if cfg.FixedParkingRate != 25.50 {
		t.Fatalf("expected rate 25.50, got %f", cfg.FixedParkingRate)
	}
	if cfg.S3BucketName != "custom-bucket" {
		t.Fatalf("expected custom-bucket, got %s", cfg.S3BucketName)
	}
}
