output "alb_url" {
  description = "URL pública da aplicação (Application Load Balancer)"
  value       = module.app.alb_url
}

output "asg_name" {
  description = "Nome do Auto Scaling Group"
  value       = module.app.asg_name
}

output "ecr_registry" {
  description = "Registry ECR das imagens api/worker/web"
  value       = module.app.ecr_registry
}

output "s3_bucket_name" {
  description = "Bucket S3 das fotos dos veículos"
  value       = module.app.s3_bucket_name
}

output "rds_endpoint" {
  description = "Endpoint do RDS PostgreSQL"
  value       = module.app.rds_endpoint
}

output "elasticache_endpoint" {
  description = "Endpoint do ElastiCache Redis"
  value       = module.app.elasticache_endpoint
}

output "sqs_queue_url" {
  description = "URL da fila SQS de OCR"
  value       = module.app.sqs_queue_url
}
