package main

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"net"
	"net/http"
	"time"

	"api/internal/config"
	"api/internal/handler"
	"api/internal/repository"
	"api/internal/service"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/sns"
	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
	"go.uber.org/fx"
)

func main() {
	fx.New(
		fx.Provide(
			// 1. Config
			config.LoadConfig,

			// 2. Database & Cache clients
			NewDatabase,
			NewRedisClient,

			// 3. AWS Infrastructure
			NewAWSConfig,
			NewS3Client,
			NewSNSClient,
			NewDynamoDBClient,

			// 4. Repositories & Adapters (defined in internal/repository)
			fx.Annotate(repository.NewPostgresSessionRepo, fx.As(new(repository.SessionRepository))),
			fx.Annotate(repository.NewRedisSpotsRepo, fx.As(new(repository.SpotsRepository))),
			fx.Annotate(repository.NewS3BlobStorage, fx.As(new(repository.BlobStorage))),
			fx.Annotate(repository.NewSNSEventPublisher, fx.As(new(repository.EventPublisher))),
			fx.Annotate(repository.NewDynamoDBAuditLogger, fx.As(new(repository.AuditLogger))),

			// 5. Service (defined in internal/service)
			service.NewParkingService,

			// 6. Transport / HTTP Handler (defined in internal/handler)
			handler.NewHandler,
			handler.NewServeMux,
		),
		fx.Invoke(
			RegisterLifecycle,
		),
	).Run()
}

// --- AWS & Client Constructors ---

func NewDatabase(cfg config.Config) (*sql.DB, error) {
	return sql.Open("postgres", cfg.DatabaseURL)
}

func NewRedisClient(cfg config.Config) *redis.Client {
	return redis.NewClient(&redis.Options{
		Addr: cfg.RedisURL,
	})
}

func NewAWSConfig(cfg config.Config) (aws.Config, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	opts := []func(*awsconfig.LoadOptions) error{
		awsconfig.WithRegion(cfg.AWSRegion),
	}

	if cfg.AWSAccessKeyID != "" && cfg.AWSSecretAccessKey != "" {
		opts = append(opts, awsconfig.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(cfg.AWSAccessKeyID, cfg.AWSSecretAccessKey, ""),
		))
	}

	return awsconfig.LoadDefaultConfig(ctx, opts...)
}

func NewS3Client(awsCfg aws.Config, cfg config.Config) *s3.Client {
	return s3.NewFromConfig(awsCfg, func(o *s3.Options) {
		o.UsePathStyle = true
		if cfg.AWSEndpointURL != "" {
			o.BaseEndpoint = &cfg.AWSEndpointURL
		}
	})
}

func NewSNSClient(awsCfg aws.Config, cfg config.Config) *sns.Client {
	return sns.NewFromConfig(awsCfg, func(o *sns.Options) {
		if cfg.AWSEndpointURL != "" {
			o.BaseEndpoint = &cfg.AWSEndpointURL
		}
	})
}

func NewDynamoDBClient(awsCfg aws.Config, cfg config.Config) *dynamodb.Client {
	return dynamodb.NewFromConfig(awsCfg, func(o *dynamodb.Options) {
		if cfg.AWSEndpointURL != "" {
			o.BaseEndpoint = &cfg.AWSEndpointURL
		}
	})
}

// --- Lifecycle Hooks ---

func RegisterLifecycle(
	lc fx.Lifecycle,
	db *sql.DB,
	rdb *redis.Client,
	mux *http.ServeMux,
	cfg config.Config,
) {
	server := &http.Server{
		Addr:    fmt.Sprintf(":%s", cfg.Port),
		Handler: mux,
	}

	lc.Append(fx.Hook{
		OnStart: func(ctx context.Context) error {
			if err := db.PingContext(ctx); err != nil {
				log.Printf("[WARN] Postgres ping failed (skipping migrations if offline): %v", err)
			} else {
				if err := repository.RunMigrations(db); err != nil {
					log.Printf("[WARN] Migrations warning: %v", err)
				} else {
					log.Println("[INFO] Database migrations applied successfully")
				}
			}

			ln, err := net.Listen("tcp", server.Addr)
			if err != nil {
				return err
			}
			log.Printf("[INFO] Server listening on http://0.0.0.0:%s", cfg.Port)

			go func() {
				if err := server.Serve(ln); err != nil && err != http.ErrServerClosed {
					log.Printf("[ERROR] HTTP server error: %v", err)
				}
			}()
			return nil
		},
		OnStop: func(ctx context.Context) error {
			log.Println("[INFO] Shutting down application gracefully...")
			_ = server.Shutdown(ctx)
			_ = db.Close()
			_ = rdb.Close()
			return nil
		},
	})
}
