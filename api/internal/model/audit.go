package model

type AuditLog struct {
	ID        string         `json:"id" dynamodbav:"id"`
	Action    string         `json:"action" dynamodbav:"action"`
	EntityID  string         `json:"entity_id" dynamodbav:"entity_id"`
	Timestamp string         `json:"timestamp" dynamodbav:"timestamp"`
	Details   map[string]any `json:"details" dynamodbav:"details"`
}
