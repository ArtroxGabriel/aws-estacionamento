package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"sync"
	"time"

	"api/internal/config"

	amqp "github.com/rabbitmq/amqp091-go"
)

// ErrPublisherNotConfigured is returned by Publish when RABBITMQ_URL is empty.
// It is an error on purpose: a silent no-op would leave the session waiting
// for a worker that never gets the message.
var ErrPublisherNotConfigured = errors.New("RABBITMQ_URL is not set")

// publishTimeout bounds one publication, connection included, so a broker
// outage costs the entry gate a few seconds at most.
const publishTimeout = 5 * time.Second

// QueueSession is an open, confirmed channel to one queue. The real one wraps
// amqp091; tests inject their own.
type QueueSession interface {
	// Publish returns nil only after the broker confirmed the message.
	Publish(ctx context.Context, body []byte) error
	Closed() bool
	Close() error
}

// DialFunc opens a QueueSession to the queue, failing when it does not exist.
type DialFunc func(ctx context.Context, url, queue string) (QueueSession, error)

// RabbitMQEventPublisher publishes OCR tasks to a RabbitMQ queue. The
// connection is opened on the first publication and reopened after a failure,
// so the API starts and keeps serving while the broker is down.
type RabbitMQEventPublisher struct {
	url   string
	queue string
	dial  DialFunc

	mu      sync.Mutex
	session QueueSession
}

var _ EventPublisher = (*RabbitMQEventPublisher)(nil)

func NewRabbitMQEventPublisher(cfg config.Config) *RabbitMQEventPublisher {
	return NewRabbitMQEventPublisherWithDialer(cfg, dialAMQP)
}

func NewRabbitMQEventPublisherWithDialer(cfg config.Config, dial DialFunc) *RabbitMQEventPublisher {
	return &RabbitMQEventPublisher{url: cfg.RabbitMQURL, queue: cfg.RabbitMQQueueName, dial: dial}
}

func (p *RabbitMQEventPublisher) Publish(ctx context.Context, payload any) error {
	if p.url == "" {
		return ErrPublisherNotConfigured
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(ctx, publishTimeout)
	defer cancel()

	p.mu.Lock()
	defer p.mu.Unlock()

	if p.session == nil || p.session.Closed() {
		p.dropSession()
		session, err := p.dial(ctx, p.url, p.queue)
		if err != nil {
			return fmt.Errorf("rabbitmq %s: %w", RedactURL(p.url), err)
		}
		p.session = session
	}

	if err := p.session.Publish(ctx, body); err != nil {
		// The channel state is unknown after a failure: start over next time.
		p.dropSession()
		return fmt.Errorf("rabbitmq publish to %s: %w", p.queue, err)
	}
	return nil
}

// Close releases the connection. The publisher can still be used afterwards;
// it reconnects on the next publication.
func (p *RabbitMQEventPublisher) Close() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.dropSession()
	return nil
}

func (p *RabbitMQEventPublisher) dropSession() {
	if p.session != nil {
		_ = p.session.Close()
		p.session = nil
	}
}

// RedactURL keeps only scheme, host and path of a URL, for logs and errors.
func RedactURL(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return "<invalid url>"
	}
	return u.Scheme + "://" + u.Host + u.Path
}

// --- amqp091 session ---

type amqpSession struct {
	conn    *amqp.Connection
	ch      *amqp.Channel
	queue   string
	returns chan amqp.Return
}

func dialAMQP(ctx context.Context, rawURL, queue string) (QueueSession, error) {
	timeout := publishTimeout
	if deadline, ok := ctx.Deadline(); ok {
		timeout = time.Until(deadline)
	}
	if timeout <= 0 {
		return nil, context.DeadlineExceeded
	}

	conn, err := amqp.DialConfig(rawURL, amqp.Config{
		Dial:      amqp.DefaultDial(timeout),
		Heartbeat: 10 * time.Second,
	})
	if err != nil {
		return nil, err
	}
	ch, err := conn.Channel()
	if err != nil {
		_ = conn.Close()
		return nil, err
	}
	if err := ch.Confirm(false); err != nil {
		_ = conn.Close()
		return nil, err
	}
	// Passive: the topology belongs to infra/rabbitmq/definitions.json. The
	// API only checks that the queue is there and never declares it.
	if _, err := ch.QueueDeclarePassive(queue, true, false, false, false, nil); err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("queue %q not available: %w", queue, err)
	}
	// Buffered: one publication at a time, so at most one return is pending.
	returns := ch.NotifyReturn(make(chan amqp.Return, 1))
	return &amqpSession{conn: conn, ch: ch, queue: queue, returns: returns}, nil
}

func (s *amqpSession) Publish(ctx context.Context, body []byte) error {
	confirmation, err := s.ch.PublishWithDeferredConfirmWithContext(ctx, "", s.queue, true, false, amqp.Publishing{
		ContentType:  "application/json",
		DeliveryMode: amqp.Persistent,
		Timestamp:    time.Now().UTC(),
		Body:         body,
	})
	if err != nil {
		return err
	}
	acked, err := confirmation.WaitContext(ctx)
	if err != nil {
		return err
	}
	if !acked {
		return errors.New("message not confirmed by the broker")
	}
	// An unroutable mandatory message is returned before it is confirmed.
	select {
	case ret, ok := <-s.returns:
		if ok {
			return fmt.Errorf("message returned by the broker: %s", ret.ReplyText)
		}
	default:
	}
	return nil
}

func (s *amqpSession) Closed() bool {
	return s.conn.IsClosed() || s.ch.IsClosed()
}

func (s *amqpSession) Close() error {
	return s.conn.Close()
}
