package config

import (
	"os"
	"strconv"
)

type Config struct {
	Port               string
	AWSEndpointURL     string
	AWSRegion          string
	AWSAccessKeyID     string
	AWSSecretAccessKey string
	AWSSessionToken    string
	S3BucketName       string
	SQSQueueURL        string
	DynamoDBTableName  string
	DatabaseURL        string
	RedisURL           string
	TotalParkingSpots  int
	FixedParkingRate   float64
}

func LoadConfig() Config {
	// AWS endpoint and credentials have no fallback: when unset the SDK talks to
	// the real AWS endpoints and resolves credentials through its default chain
	// (EC2 instance profile). Local runs point them at Floci via .env/Taskfile.
	spots, _ := strconv.Atoi(getEnv("TOTAL_PARKING_SPOTS", "50"))
	rate, _ := strconv.ParseFloat(getEnv("FIXED_PARKING_RATE", "10.00"), 64)

	return Config{
		Port:               getEnv("PORT", "8080"),
		AWSEndpointURL:     os.Getenv("AWS_ENDPOINT_URL"),
		AWSRegion:          getEnv("AWS_REGION", "us-east-1"),
		AWSAccessKeyID:     os.Getenv("AWS_ACCESS_KEY_ID"),
		AWSSecretAccessKey: os.Getenv("AWS_SECRET_ACCESS_KEY"),
		AWSSessionToken:    os.Getenv("AWS_SESSION_TOKEN"),
		S3BucketName:       getEnv("S3_BUCKET_NAME", "estacionamento-fotos-veiculos-local"),
		SQSQueueURL:        getEnv("SQS_QUEUE_URL", ""),
		DynamoDBTableName:  getEnv("DYNAMODB_TABLE_NAME", "AuditoriaEstacionamento"),
		DatabaseURL:        getEnv("DATABASE_URL", "postgres://app_user:app_password@localhost:5432/estacionamento?sslmode=disable"),
		RedisURL:           getEnv("REDIS_URL", "localhost:6379"),
		TotalParkingSpots:  spots,
		FixedParkingRate:   rate,
	}
}

func getEnv(key, fallback string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return fallback
}
