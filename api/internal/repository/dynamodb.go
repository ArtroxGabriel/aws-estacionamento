package repository

import (
	"context"
	"errors"
	"fmt"
	"time"

	"api/internal/config"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/feature/dynamodb/attributevalue"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	dynamodbtypes "github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
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
	var rnfe *dynamodbtypes.ResourceNotFoundException
	if errors.As(err, &rnfe) {
		return nil
	}
	return err
}
