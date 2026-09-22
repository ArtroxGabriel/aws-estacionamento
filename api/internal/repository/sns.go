package repository

import (
	"context"
	"encoding/json"

	"api/internal/config"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/sns"
)

type SNSEventPublisher struct {
	client   *sns.Client
	topicARN string
}

var _ EventPublisher = (*SNSEventPublisher)(nil)

func NewSNSEventPublisher(client *sns.Client, cfg config.Config) *SNSEventPublisher {
	return &SNSEventPublisher{client: client, topicARN: cfg.SNSTopicARN}
}

func (p *SNSEventPublisher) Publish(ctx context.Context, payload any) error {
	if p.topicARN == "" {
		return nil
	}
	b, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	_, err = p.client.Publish(ctx, &sns.PublishInput{
		TopicArn: aws.String(p.topicARN),
		Message:  aws.String(string(b)),
	})
	return err
}
