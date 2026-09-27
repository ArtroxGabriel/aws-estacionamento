package repository

import (
	"context"

	"github.com/redis/go-redis/v9"
)

type RedisSpotsRepo struct {
	client *redis.Client
}

var _ SpotsRepository = (*RedisSpotsRepo)(nil)

func NewRedisSpotsRepo(client *redis.Client) *RedisSpotsRepo {
	return &RedisSpotsRepo{client: client}
}

func (r *RedisSpotsRepo) GetAvailable(ctx context.Context) (int64, error) {
	return r.client.Get(ctx, "spots:available").Int64()
}

func (r *RedisSpotsRepo) SetAvailable(ctx context.Context, count int64) error {
	return r.client.Set(ctx, "spots:available", count, 0).Err()
}

func (r *RedisSpotsRepo) Increment(ctx context.Context) (int64, error) {
	return r.client.Incr(ctx, "spots:available").Result()
}

func (r *RedisSpotsRepo) Decrement(ctx context.Context) (int64, error) {
	return r.client.Decr(ctx, "spots:available").Result()
}
