output "s3_bucket_name" {
  description = "Nome do bucket S3 para fotos de veículos"
  value       = aws_s3_bucket.fotos.bucket
}


output "sqs_queue_url" {
  description = "URL da fila SQS para processamento OCR"
  value       = aws_sqs_queue.ocr_queue.id
}

output "sqs_queue_arn" {
  description = "ARN da fila SQS para processamento OCR"
  value       = aws_sqs_queue.ocr_queue.arn
}

output "sqs_dlq_url" {
  description = "URL da fila de mensagens mortas (DLQ) do processamento OCR"
  value       = aws_sqs_queue.ocr_dlq.id
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

output "alb_url" {
  description = "URL pública da aplicação (Application Load Balancer)"
  value       = local.is_aws ? "http://${aws_lb.app[0].dns_name}" : null
}

output "asg_name" {
  description = "Nome do Auto Scaling Group"
  value       = local.is_aws ? aws_autoscaling_group.app[0].name : null
}

output "ecr_registry" {
  description = "Registry ECR onde as imagens api/worker/web são publicadas"
  value       = local.is_aws ? local.registry : null
}
