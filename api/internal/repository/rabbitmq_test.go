package repository_test

import (
	"context"
	"errors"
	"strings"
	"testing"

	"api/internal/config"
	"api/internal/repository"
)

type fakeQueueSession struct {
	published  [][]byte
	publishErr error
	closed     bool
	closeCalls int
}

func (s *fakeQueueSession) Publish(_ context.Context, body []byte) error {
	if s.publishErr != nil {
		return s.publishErr
	}
	s.published = append(s.published, body)
	return nil
}

func (s *fakeQueueSession) Closed() bool { return s.closed }

func (s *fakeQueueSession) Close() error {
	s.closeCalls++
	s.closed = true
	return nil
}

// fakeDialer hands out the given sessions (or errors) in order.
type fakeDialer struct {
	sessions []*fakeQueueSession
	errs     []error
	calls    int
	gotURL   string
	gotQueue string
	deadline bool
}

func (d *fakeDialer) dial(ctx context.Context, url, queue string) (repository.QueueSession, error) {
	i := d.calls
	d.calls++
	d.gotURL, d.gotQueue = url, queue
	_, d.deadline = ctx.Deadline()
	if i < len(d.errs) && d.errs[i] != nil {
		return nil, d.errs[i]
	}
	return d.sessions[i], nil
}

var rabbitCfg = config.Config{
	RabbitMQURL:       "amqp://user:s3cret@broker:5672/",
	RabbitMQQueueName: "ocr-processamento-fila",
}

func TestRabbitMQPublisher_PublishesJSONOnOneConnection(t *testing.T) {
	session := &fakeQueueSession{}
	dialer := &fakeDialer{sessions: []*fakeQueueSession{session}}
	pub := repository.NewRabbitMQEventPublisherWithDialer(rabbitCfg, dialer.dial)

	for range 2 {
		err := pub.Publish(context.Background(), map[string]string{"session_id": "abc", "s3_key": "photos/abc_a.jpg"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
	}

	if dialer.calls != 1 {
		t.Fatalf("expected one connection for two publications, got %d", dialer.calls)
	}
	if dialer.gotURL != rabbitCfg.RabbitMQURL || dialer.gotQueue != "ocr-processamento-fila" {
		t.Fatalf("dialed %q / %q", dialer.gotURL, dialer.gotQueue)
	}
	if !dialer.deadline {
		t.Fatal("expected the publication to be bounded by a deadline")
	}
	if len(session.published) != 2 {
		t.Fatalf("expected 2 messages, got %d", len(session.published))
	}
	if got := string(session.published[0]); got != `{"s3_key":"photos/abc_a.jpg","session_id":"abc"}` {
		t.Fatalf("unexpected body: %s", got)
	}
}

func TestRabbitMQPublisher_EmptyURLIsAnError(t *testing.T) {
	dialer := &fakeDialer{}
	pub := repository.NewRabbitMQEventPublisherWithDialer(config.Config{RabbitMQQueueName: "q"}, dialer.dial)

	err := pub.Publish(context.Background(), map[string]string{"foo": "bar"})

	if !errors.Is(err, repository.ErrPublisherNotConfigured) {
		t.Fatalf("expected ErrPublisherNotConfigured, got %v", err)
	}
	if dialer.calls != 0 {
		t.Fatalf("expected no connection attempt, got %d", dialer.calls)
	}
}

func TestRabbitMQPublisher_DialFailureIsReportedWithoutCredentials(t *testing.T) {
	dialer := &fakeDialer{errs: []error{errors.New("connection refused")}}
	pub := repository.NewRabbitMQEventPublisherWithDialer(rabbitCfg, dialer.dial)

	err := pub.Publish(context.Background(), map[string]string{"foo": "bar"})

	if err == nil {
		t.Fatal("expected an error")
	}
	if strings.Contains(err.Error(), "s3cret") || strings.Contains(err.Error(), "user") {
		t.Fatalf("credentials leaked in error: %v", err)
	}
	if !strings.Contains(err.Error(), "broker:5672") {
		t.Fatalf("expected the broker host in the error, got %v", err)
	}
}

func TestRabbitMQPublisher_ReconnectsAfterDialFailure(t *testing.T) {
	session := &fakeQueueSession{}
	dialer := &fakeDialer{
		errs:     []error{errors.New("connection refused"), nil},
		sessions: []*fakeQueueSession{nil, session},
	}
	pub := repository.NewRabbitMQEventPublisherWithDialer(rabbitCfg, dialer.dial)

	if err := pub.Publish(context.Background(), "a"); err == nil {
		t.Fatal("expected the first publication to fail")
	}
	if err := pub.Publish(context.Background(), "b"); err != nil {
		t.Fatalf("expected the second publication to succeed, got %v", err)
	}
	if dialer.calls != 2 || len(session.published) != 1 {
		t.Fatalf("calls=%d published=%d", dialer.calls, len(session.published))
	}
}

func TestRabbitMQPublisher_FailedPublicationDropsTheConnection(t *testing.T) {
	broken := &fakeQueueSession{publishErr: errors.New("message not confirmed by the broker")}
	healthy := &fakeQueueSession{}
	dialer := &fakeDialer{sessions: []*fakeQueueSession{broken, healthy}}
	pub := repository.NewRabbitMQEventPublisherWithDialer(rabbitCfg, dialer.dial)

	if err := pub.Publish(context.Background(), "a"); err == nil {
		t.Fatal("expected an unconfirmed message to be an error")
	}
	if broken.closeCalls != 1 {
		t.Fatalf("expected the failed connection to be closed once, got %d", broken.closeCalls)
	}
	if err := pub.Publish(context.Background(), "b"); err != nil {
		t.Fatalf("unexpected error after reconnect: %v", err)
	}
	if len(healthy.published) != 1 {
		t.Fatalf("expected the retry on a new connection, got %d", len(healthy.published))
	}
}

func TestRabbitMQPublisher_ReconnectsWhenTheBrokerClosedTheConnection(t *testing.T) {
	first := &fakeQueueSession{}
	second := &fakeQueueSession{}
	dialer := &fakeDialer{sessions: []*fakeQueueSession{first, second}}
	pub := repository.NewRabbitMQEventPublisherWithDialer(rabbitCfg, dialer.dial)

	if err := pub.Publish(context.Background(), "a"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	first.closed = true // the broker went away between two entries

	if err := pub.Publish(context.Background(), "b"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if dialer.calls != 2 || len(second.published) != 1 {
		t.Fatalf("calls=%d second.published=%d", dialer.calls, len(second.published))
	}
}

func TestRabbitMQPublisher_CloseReleasesAndAllowsReuse(t *testing.T) {
	first := &fakeQueueSession{}
	second := &fakeQueueSession{}
	dialer := &fakeDialer{sessions: []*fakeQueueSession{first, second}}
	pub := repository.NewRabbitMQEventPublisherWithDialer(rabbitCfg, dialer.dial)

	_ = pub.Publish(context.Background(), "a")
	if err := pub.Close(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if first.closeCalls != 1 {
		t.Fatalf("expected the connection to be closed, got %d", first.closeCalls)
	}
	if err := pub.Close(); err != nil {
		t.Fatalf("closing twice must be safe: %v", err)
	}
	if err := pub.Publish(context.Background(), "b"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestRabbitMQPublisher_UnmarshalablePayload(t *testing.T) {
	dialer := &fakeDialer{}
	pub := repository.NewRabbitMQEventPublisherWithDialer(rabbitCfg, dialer.dial)

	if err := pub.Publish(context.Background(), make(chan int)); err == nil {
		t.Fatal("expected a marshal error")
	}
	if dialer.calls != 0 {
		t.Fatal("expected no connection for an invalid payload")
	}
}

func TestRedactURL(t *testing.T) {
	cases := map[string]string{
		"amqp://guest:guest@rabbitmq:5672/":    "amqp://rabbitmq:5672/",
		"amqps://u:p@host.example.com:5671/vh": "amqps://host.example.com:5671/vh",
		"amqp://rabbitmq:5672":                 "amqp://rabbitmq:5672",
		"not a url":                            "<invalid url>",
		"":                                     "<invalid url>",
	}
	for in, want := range cases {
		if got := repository.RedactURL(in); got != want {
			t.Errorf("RedactURL(%q) = %q, want %q", in, got, want)
		}
	}
}
