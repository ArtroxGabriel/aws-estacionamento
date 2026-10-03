package repository

import (
	"context"
	"fmt"
	"slices"
	"time"

	"api/internal/config"
	"api/internal/model"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/feature/dynamodb/attributevalue"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
)

type DynamoDBAuditLogger struct {
	client    *dynamodb.Client
	tableName string
}

var _ AuditLogger = (*DynamoDBAuditLogger)(nil)

func NewDynamoDBAuditLogger(client *dynamodb.Client, cfg config.Config) *DynamoDBAuditLogger {
	return &DynamoDBAuditLogger{client: client, tableName: cfg.DynamoDBTableName}
}

type auditRecord struct {
	ID        string         `dynamodbav:"id"`
	Action    string         `dynamodbav:"action"`
	EntityID  string         `dynamodbav:"entity_id"`
	Timestamp string         `dynamodbav:"timestamp"`
	Details   map[string]any `dynamodbav:"details"`
}

func (d *DynamoDBAuditLogger) LogEvent(ctx context.Context, action, entityID string, details map[string]any) error {
	record := auditRecord{
		ID:        fmt.Sprintf("%s#%d", entityID, time.Now().UnixNano()),
		Action:    action,
		EntityID:  entityID,
		Timestamp: time.Now().UTC().Format(time.RFC3339Nano),
		Details:   details,
	}

	av, err := attributevalue.MarshalMap(record)
	if err != nil {
		return err
	}

	_, err = d.client.PutItem(ctx, &dynamodb.PutItemInput{
		TableName: aws.String(d.tableName),
		Item:      av,
	})
	return err
}

// GetRecentLogs returns the newest `limit` audit records. DynamoDB Scan has no
// ordering, so every page is read and sorted here. Fine for the audit volume of
// this project; a GSI on timestamp would be the scalable alternative.
func (d *DynamoDBAuditLogger) GetRecentLogs(ctx context.Context, limit int) ([]*model.AuditLog, error) {
	var logs []*model.AuditLog
	pages := dynamodb.NewScanPaginator(d.client, &dynamodb.ScanInput{TableName: aws.String(d.tableName)})
	for pages.HasMorePages() {
		out, err := pages.NextPage(ctx)
		if err != nil {
			return nil, err
		}
		for _, item := range out.Items {
			var l model.AuditLog
			if err := attributevalue.UnmarshalMap(item, &l); err != nil {
				return nil, err
			}
			logs = append(logs, &l)
		}
	}

	// Parse instead of comparing strings: RFC3339Nano trims trailing zeros, so
	// "05.1Z" would sort after "05.12Z" lexicographically.
	ts := func(l *model.AuditLog) time.Time {
		t, _ := time.Parse(time.RFC3339Nano, l.Timestamp)
		return t
	}
	slices.SortFunc(logs, func(a, b *model.AuditLog) int { return ts(b).Compare(ts(a)) })

	if len(logs) > limit {
		logs = logs[:limit]
	}
	return logs, nil
}
