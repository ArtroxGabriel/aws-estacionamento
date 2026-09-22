package config

import (
	"os"
	"strconv"
)

type Config struct {
	Port                string
	AWSEndpointURL      string
	AWSRegion           string
	AWSAccessKeyID      string
	AWSSecretAccessKey  string
	S3BucketName        string
	SNSTopicARN         string
	DynamoDBTableName   string
	DatabaseURL         string
	RedisURL            string
	TotalParkingSpots   int
	FixedParkingRate    float64
}

func LoadConfig() Config {
	spots, _ := strconv.Atoi(getEnv("TOTAL_PARKING_SPOTS", "50"))
	rate, _ := strconv.ParseFloat(getEnv("FIXED_PARKING_RATE", "10.00"), 64)

	return Config{
		Port:               getEnv("PORT", "8080"),
		AWSEndpointURL:     getEnv("AWS_ENDPOINT_URL", "http://localhost:4566"),
		AWSRegion:          getEnv("AWS_REGION", "us-east-1"),
		AWSAccessKeyID:     getEnv("AWS_ACCESS_KEY_ID", "mock_key"),
		AWSSecretAccessKey: getEnv("AWS_SECRET_ACCESS_KEY", "mock_secret"),
		S3BucketName:       getEnv("S3_BUCKET_NAME", "estacionamento-fotos-veiculos-local"),
		SNSTopicARN:        getEnv("SNS_TOPIC_ARN", ""),
		DynamoDBTableName:  getEnv("DYNAMODB_TABLE_NAME", "AuditoriaEstacionamento"),
		DatabaseURL:       getEnv("DATABASE_URL", "postgres://app_user:app_password@localhost:5432/estacionamento?sslmode=disable"),
		RedisURL:          getEnv("REDIS_URL", "localhost:6379"),
		TotalParkingSpots: spots,
		FixedParkingRate:  rate,
	}
}

func getEnv(key, fallback string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return fallback
}
