package config_test

import (
	"os"
	"testing"

	"api/internal/config"
)

func TestLoadConfig_Defaults(t *testing.T) {
	_ = os.Unsetenv("PORT")
	_ = os.Unsetenv("S3_ENDPOINT_URL")
	_ = os.Unsetenv("DYNAMODB_ENDPOINT_URL")
	_ = os.Unsetenv("RABBITMQ_URL")
	_ = os.Unsetenv("RABBITMQ_QUEUE_NAME")
	_ = os.Unsetenv("AWS_REGION")
	_ = os.Unsetenv("AWS_ACCESS_KEY_ID")
	_ = os.Unsetenv("AWS_SECRET_ACCESS_KEY")
	_ = os.Unsetenv("AWS_SESSION_TOKEN")
	_ = os.Unsetenv("TOTAL_PARKING_SPOTS")
	_ = os.Unsetenv("FIXED_PARKING_RATE")
	_ = os.Unsetenv("PARKING_EXTRA_HOUR_RATE")
	_ = os.Unsetenv("PARKING_TOLERANCE_MINUTES")

	cfg := config.LoadConfig()

	if cfg.Port != "8080" {
		t.Fatalf("expected default port 8080, got %s", cfg.Port)
	}
	if cfg.AWSRegion != "us-east-1" {
		t.Fatalf("expected default region us-east-1, got %s", cfg.AWSRegion)
	}
	// Without endpoint/credentials the SDK must use real AWS endpoints and the
	// default credential chain (EC2 instance profile).
	if cfg.S3EndpointURL != "" || cfg.DynamoDBEndpointURL != "" {
		t.Fatalf("expected empty endpoints, got %q and %q", cfg.S3EndpointURL, cfg.DynamoDBEndpointURL)
	}
	if cfg.RabbitMQURL != "" {
		t.Fatalf("expected no broker URL by default, got %s", cfg.RabbitMQURL)
	}
	if cfg.RabbitMQQueueName != "ocr-processamento-fila" {
		t.Fatalf("expected default queue name, got %s", cfg.RabbitMQQueueName)
	}
	if cfg.AWSAccessKeyID != "" || cfg.AWSSecretAccessKey != "" || cfg.AWSSessionToken != "" {
		t.Fatalf("expected no static credentials by default")
	}
	if cfg.TotalParkingSpots != 50 {
		t.Fatalf("expected 50 spots, got %d", cfg.TotalParkingSpots)
	}
	if cfg.FixedParkingRate != 10.0 {
		t.Fatalf("expected rate 10.0, got %f", cfg.FixedParkingRate)
	}
	if cfg.ParkingExtraHourRate != 5.0 || cfg.ParkingToleranceMinutes != 15 {
		t.Fatalf("expected extra hour 5.0 and tolerance 15 min, got %f / %d", cfg.ParkingExtraHourRate, cfg.ParkingToleranceMinutes)
	}
}

func TestLoadConfig_CustomEnv(t *testing.T) {
	t.Setenv("PORT", "9090")
	t.Setenv("TOTAL_PARKING_SPOTS", "120")
	t.Setenv("FIXED_PARKING_RATE", "25.50")
	t.Setenv("S3_BUCKET_NAME", "custom-bucket")
	t.Setenv("S3_ENDPOINT_URL", "http://rustfs:9000")
	t.Setenv("DYNAMODB_ENDPOINT_URL", "http://dynamodb-local:8000")
	t.Setenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
	t.Setenv("RABBITMQ_QUEUE_NAME", "outra-fila")
	// Leftovers of the SQS/Floci setup are simply not read.
	t.Setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
	t.Setenv("SQS_QUEUE_URL", "http://localhost:4566/queue")

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
	if cfg.S3EndpointURL != "http://rustfs:9000" {
		t.Fatalf("unexpected S3 endpoint %s", cfg.S3EndpointURL)
	}
	if cfg.DynamoDBEndpointURL != "http://dynamodb-local:8000" {
		t.Fatalf("unexpected DynamoDB endpoint %s", cfg.DynamoDBEndpointURL)
	}
	if cfg.RabbitMQURL != "amqp://guest:guest@rabbitmq:5672/" || cfg.RabbitMQQueueName != "outra-fila" {
		t.Fatalf("unexpected broker config %s / %s", cfg.RabbitMQURL, cfg.RabbitMQQueueName)
	}
}
