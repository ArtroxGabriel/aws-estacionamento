package repository

import (
	"context"
	"encoding/json"

	"api/internal/config"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/sqs"
)

type SQSEventPublisher struct {
	client   *sqs.Client
	queueURL string
}

var _ EventPublisher = (*SQSEventPublisher)(nil)

func NewSQSEventPublisher(client *sqs.Client, cfg config.Config) *SQSEventPublisher {
	return &SQSEventPublisher{client: client, queueURL: cfg.SQSQueueURL}
}

func (p *SQSEventPublisher) Publish(ctx context.Context, payload any) error {
	if p.queueURL == "" {
		return nil
	}
	b, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	_, err = p.client.SendMessage(ctx, &sqs.SendMessageInput{
		QueueUrl:    aws.String(p.queueURL),
		MessageBody: aws.String(string(b)),
	})
	return err
}
