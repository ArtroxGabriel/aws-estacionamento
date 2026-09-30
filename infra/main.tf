resource "aws_s3_bucket" "fotos" {
  bucket = "estacionamento-fotos-veiculos-${var.use_localstack ? "local" : "prod"}"
}

resource "aws_sqs_queue" "ocr_queue" {
  name                      = "ocr-processamento-fila"
  message_retention_seconds = 86400
  # Tempo para o worker concluir S3 + OCR + RDS/Redis/DynamoDB antes que a
  # mensagem volte a ficar visível para outra instância do ASG.
  visibility_timeout_seconds = 300

  # Após 3 recebimentos sem sucesso o SQS move a mensagem para a DLQ; o worker
  # registra a auditoria POISON_MESSAGE na 3ª entrega (MAX_RECEIVE_COUNT).
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ocr_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "ocr_dlq" {
  name                      = "ocr-processamento-fila-dlq"
  message_retention_seconds = 1209600
}

resource "aws_dynamodb_table" "logs" {
  name         = "AuditoriaEstacionamento"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
}

resource "aws_db_instance" "postgres" {
  identifier          = "estacionamento-db"
  allocated_storage   = 20
  engine              = "postgres"
  engine_version      = "16"
  instance_class      = "db.t3.micro"
  db_name             = "estacionamento"
  username            = "app_user"
  password            = "app_password"
  skip_final_snapshot = true
  publicly_accessible = true
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "estacionamento-cache"
  description          = "Cluster Redis para gestao de vagas"
  engine               = "redis"
  node_type            = "cache.t3.micro"
  num_cache_clusters   = 1
  parameter_group_name = "default.redis7"
  port                 = 6379
  apply_immediately    = true
}
