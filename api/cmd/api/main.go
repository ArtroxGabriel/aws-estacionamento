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
	"github.com/aws/aws-sdk-go-v2/service/sqs"
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
			NewSQSClient,
			NewDynamoDBClient,

			// 4. Repositories & Adapters (defined in internal/repository)
			fx.Annotate(repository.NewPostgresSessionRepo, fx.As(new(repository.SessionRepository))),
			fx.Annotate(repository.NewRedisSpotsRepo, fx.As(new(repository.SpotsRepository))),
			fx.Annotate(repository.NewS3BlobStorage, fx.As(new(repository.BlobStorage))),
			fx.Annotate(repository.NewSQSEventPublisher, fx.As(new(repository.EventPublisher))),
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
			credentials.NewStaticCredentialsProvider(cfg.AWSAccessKeyID, cfg.AWSSecretAccessKey, cfg.AWSSessionToken),
		))
	}

	return awsconfig.LoadDefaultConfig(ctx, opts...)
}

func NewS3Client(awsCfg aws.Config, cfg config.Config) *s3.Client {
	return s3.NewFromConfig(awsCfg, func(o *s3.Options) {
		// Path-style only for emulators (Floci); real S3 uses virtual-hosted style.
		if cfg.AWSEndpointURL != "" {
			o.UsePathStyle = true
			o.BaseEndpoint = &cfg.AWSEndpointURL
		}
	})
}

func NewSQSClient(awsCfg aws.Config, cfg config.Config) *sqs.Client {
	return sqs.NewFromConfig(awsCfg, func(o *sqs.Options) {
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
		Handler: handler.WithCORS(mux),
	}

	migrateCtx, stopMigrations := context.WithCancel(context.Background())

	lc.Append(fx.Hook{
		OnStart: func(ctx context.Context) error {
			if err := migrate(ctx, db); err != nil {
				// Postgres may still be booting (e.g. a fresh RDS behind a new
				// ASG instance): keep retrying in the background so the schema
				// is created as soon as the database answers.
				log.Printf("[WARN] Migrations not applied yet, retrying in background: %v", err)
				go retryMigrations(migrateCtx, db)
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
			stopMigrations()
			_ = server.Shutdown(ctx)
			_ = db.Close()
			_ = rdb.Close()
			return nil
		},
	})
}

// --- Migrations ---

const migrationRetryInterval = 5 * time.Second

func migrate(ctx context.Context, db *sql.DB) error {
	if err := db.PingContext(ctx); err != nil {
		return err
	}
	if err := repository.RunMigrations(db); err != nil {
		return err
	}
	log.Println("[INFO] Database migrations applied successfully")
	return nil
}

func retryMigrations(ctx context.Context, db *sql.DB) {
	ticker := time.NewTicker(migrationRetryInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			pingCtx, cancel := context.WithTimeout(ctx, migrationRetryInterval)
			err := migrate(pingCtx, db)
			cancel()
			if err == nil {
				return
			}
			log.Printf("[WARN] Migrations retry failed: %v", err)
		}
	}
}
