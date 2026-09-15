output "s3_bucket_name" {
  description = "Nome do bucket S3 para fotos de veículos"
  value       = aws_s3_bucket.fotos.bucket
}

output "sns_topic_arn" {
  description = "ARN do tópico SNS para novos veículos"
  value       = aws_sns_topic.novo_veiculo.arn
}

output "sqs_queue_url" {
  description = "URL da fila SQS para processamento OCR"
  value       = aws_sqs_queue.ocr_queue.id
}

output "sqs_queue_arn" {
  description = "ARN da fila SQS para processamento OCR"
  value       = aws_sqs_queue.ocr_queue.arn
}

output "dynamodb_table_name" {
  description = "Nome da tabela DynamoDB para auditoria"
  value       = aws_dynamodb_table.logs.name
}

output "rds_endpoint" {
  description = "Endpoint do banco de dados RDS PostgreSQL"
  value       = aws_db_instance.postgres.endpoint
}

output "elasticache_endpoint" {
  description = "Endpoint do cluster ElastiCache Redis"
  value       = try(aws_elasticache_replication_group.redis.primary_endpoint_address, "localhost")
}
