package config

import (
	"os"
	"strconv"
)

type Config struct {
	Port                string
	AWSRegion           string
	AWSAccessKeyID      string
	AWSSecretAccessKey  string
	AWSSessionToken     string
	S3EndpointURL       string
	S3BucketName        string
	DynamoDBEndpointURL string
	DynamoDBTableName   string
	RabbitMQURL         string
	RabbitMQQueueName   string
	DatabaseURL         string
	RedisURL            string
	TotalParkingSpots   int
	FixedParkingRate    float64
}

func LoadConfig() Config {
	// Each backing service has its own endpoint. Endpoints and credentials have
	// no fallback: when unset the SDK talks to the real AWS endpoints and
	// resolves credentials through its default chain. Local runs point them at
	// the compose services (RustFS, DynamoDB Local) via Taskfile/compose.
	// RABBITMQ_URL has no fallback either: without it nothing is queued and
	// every entry is recorded with an unreadable plate.
	spots, _ := strconv.Atoi(getEnv("TOTAL_PARKING_SPOTS", "50"))
	rate, _ := strconv.ParseFloat(getEnv("FIXED_PARKING_RATE", "10.00"), 64)

	return Config{
		Port:                getEnv("PORT", "8080"),
		AWSRegion:           getEnv("AWS_REGION", "us-east-1"),
		AWSAccessKeyID:      os.Getenv("AWS_ACCESS_KEY_ID"),
		AWSSecretAccessKey:  os.Getenv("AWS_SECRET_ACCESS_KEY"),
		AWSSessionToken:     os.Getenv("AWS_SESSION_TOKEN"),
		S3EndpointURL:       os.Getenv("S3_ENDPOINT_URL"),
		S3BucketName:        getEnv("S3_BUCKET_NAME", "estacionamento-fotos-veiculos-local"),
		DynamoDBEndpointURL: os.Getenv("DYNAMODB_ENDPOINT_URL"),
		DynamoDBTableName:   getEnv("DYNAMODB_TABLE_NAME", "AuditoriaEstacionamento"),
		RabbitMQURL:         os.Getenv("RABBITMQ_URL"),
		RabbitMQQueueName:   getEnv("RABBITMQ_QUEUE_NAME", "ocr-processamento-fila"),
		DatabaseURL:         getEnv("DATABASE_URL", "postgres://app_user:app_password@localhost:5432/estacionamento?sslmode=disable"),
		RedisURL:            getEnv("REDIS_URL", "localhost:6379"),
		TotalParkingSpots:   spots,
		FixedParkingRate:    rate,
	}
}

func getEnv(key, fallback string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return fallback
}
