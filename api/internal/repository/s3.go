package repository

import (
	"bytes"
	"context"
	"io"

	"api/internal/config"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

type S3BlobStorage struct {
	client *s3.Client
	bucket string
}

var _ BlobStorage = (*S3BlobStorage)(nil)

func NewS3BlobStorage(client *s3.Client, cfg config.Config) *S3BlobStorage {
	return &S3BlobStorage{client: client, bucket: cfg.S3BucketName}
}

func (s *S3BlobStorage) Upload(ctx context.Context, key string, body io.Reader, contentType string) error {
	buf := new(bytes.Buffer)
	if _, err := io.Copy(buf, body); err != nil {
		return err
	}

	input := &s3.PutObjectInput{
		Bucket:      aws.String(s.bucket),
		Key:         aws.String(key),
		Body:        bytes.NewReader(buf.Bytes()),
		ContentType: aws.String(contentType),
	}
	_, err := s.client.PutObject(ctx, input)
	return err
}

func (s *S3BlobStorage) Download(ctx context.Context, key string) (io.ReadCloser, string, error) {
	output, err := s.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(s.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, "", err
	}

	contentType := "application/octet-stream"
	if output.ContentType != nil {
		contentType = *output.ContentType
	}

	return output.Body, contentType, nil
}
