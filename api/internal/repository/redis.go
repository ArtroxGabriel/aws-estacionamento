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

// incrIfExists never creates the key: a missing key means Redis lost its state
// and the next GetAvailableSpots rebuilds the real count from RDS. A plain INCR
// would create it as 1 and block that rebuild. Same contract as the worker's
// _INCR_IF_EXISTS (worker/storage/spots.py).
var incrIfExists = redis.NewScript(`
if redis.call('EXISTS', KEYS[1]) == 0 then return nil end
return redis.call('INCR', KEYS[1])
`)

// Increment returns redis.Nil when the key is absent (nothing is written).
func (r *RedisSpotsRepo) Increment(ctx context.Context) (int64, error) {
	return incrIfExists.Run(ctx, r.client, []string{"spots:available"}).Int64()
}

func (r *RedisSpotsRepo) Decrement(ctx context.Context) (int64, error) {
	return r.client.Decr(ctx, "spots:available").Result()
}
