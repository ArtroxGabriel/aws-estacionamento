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

// decrIfPositive never creates the key (same reason as incrIfExists) and never
// goes below zero, like the worker's clamped DECR.
var decrIfPositive = redis.NewScript(`
local v = redis.call('GET', KEYS[1])
if not v then return nil end
if tonumber(v) <= 0 then return 0 end
return redis.call('DECR', KEYS[1])
`)

// Decrement returns redis.Nil when the key is absent (nothing is written).
func (r *RedisSpotsRepo) Decrement(ctx context.Context) (int64, error) {
	return decrIfPositive.Run(ctx, r.client, []string{"spots:available"}).Int64()
}
