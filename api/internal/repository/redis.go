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

func (r *RedisSpotsRepo) GetAvailable(ctx context.Context, defaultCapacity int) (int64, error) {
	const key = "spots:available"
	_ = r.client.SetNX(ctx, key, defaultCapacity, 0).Err()
	return r.client.Get(ctx, key).Int64()
}

func (r *RedisSpotsRepo) Increment(ctx context.Context) (int64, error) {
	return r.client.Incr(ctx, "spots:available").Result()
}

func (r *RedisSpotsRepo) Decrement(ctx context.Context) (int64, error) {
	return r.client.Decr(ctx, "spots:available").Result()
}
