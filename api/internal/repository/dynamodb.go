package repository

import (
	"context"
	"errors"
	"fmt"
	"time"

	"api/internal/config"
	"api/internal/model"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/feature/dynamodb/attributevalue"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	dynamodbtypes "github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
)

type DynamoDBAuditLogger struct {
	client    *dynamodb.Client
	tableName string
}

var (
	_ AuditLogger = (*DynamoDBAuditLogger)(nil)
	_ AuditReader = (*DynamoDBAuditLogger)(nil)
)

func NewDynamoDBAuditLogger(client *dynamodb.Client, cfg config.Config) *DynamoDBAuditLogger {
	return &DynamoDBAuditLogger{client: client, tableName: cfg.DynamoDBTableName}
}

func (d *DynamoDBAuditLogger) LogEvent(ctx context.Context, action, entityID string, details map[string]any) error {
	record := model.AuditEvent{
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
	var rnfe *dynamodbtypes.ResourceNotFoundException
	if errors.As(err, &rnfe) {
		return nil
	}
	return err
}

// ListEvents scans the whole audit table. Scan does not guarantee ordering;
// callers are responsible for sorting. A missing table yields an empty list.
func (d *DynamoDBAuditLogger) ListEvents(ctx context.Context) ([]model.AuditEvent, error) {
	events := make([]model.AuditEvent, 0)

	paginator := dynamodb.NewScanPaginator(d.client, &dynamodb.ScanInput{
		TableName: aws.String(d.tableName),
	})
	for paginator.HasMorePages() {
		page, err := paginator.NextPage(ctx)
		var rnfe *dynamodbtypes.ResourceNotFoundException
		if errors.As(err, &rnfe) {
			return []model.AuditEvent{}, nil
		}
		if err != nil {
			return nil, err
		}

		var pageEvents []model.AuditEvent
		if err := attributevalue.UnmarshalListOfMaps(page.Items, &pageEvents); err != nil {
			return nil, err
		}
		events = append(events, pageEvents...)
	}
	return events, nil
}
