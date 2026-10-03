data "aws_caller_identity" "current" {
  count = local.aws_count
}

locals {
  # Nomes de bucket são globais: na AWS o sufixo com o account id evita colisão.
  bucket_name = (local.is_aws
    ? "estacionamento-fotos-veiculos-${data.aws_caller_identity.current[0].account_id}"
    : "estacionamento-fotos-veiculos-local"
  )
}

resource "aws_s3_bucket" "fotos" {
  bucket = local.bucket_name
  # Permite o `tofu destroy` mesmo com fotos no bucket (infra efêmera).
  force_destroy = local.is_aws

  lifecycle {
    # Local e AWS usam estados separados: Floci no workspace "default" e AWS
    # no workspace "aws". Misturar os dois apaga/recria recursos do outro.
    precondition {
      condition     = var.use_localstack == (terraform.workspace != "aws")
      error_message = "Use o workspace \"aws\" com use_localstack=false e qualquer outro com use_localstack=true (veja as tasks tf:*:aws no Taskfile)."
    }
  }
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
  password            = local.db_password
  skip_final_snapshot = true
  # Infra efêmera na AWS: sem backups automáticos (criação mais rápida e sem
  # custo de snapshot) e disco gp3.
  backup_retention_period = local.is_aws ? 0 : null
  storage_type            = local.is_aws ? "gp3" : null
  # Na AWS o banco só é acessível de dentro da VPC (SG das instâncias da app).
  publicly_accessible    = var.use_localstack
  vpc_security_group_ids = local.is_aws ? [aws_security_group.rds[0].id] : null
}

# Senha do RDS gerada na AWS (sem caracteres especiais para caber na URL de
# conexão sem escape). Localmente o Floci usa a senha fixa do .env.
resource "random_password" "db" {
  count   = local.aws_count
  length  = 24
  special = false
}

locals {
  db_password = local.is_aws ? random_password.db[0].result : "app_password"
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
  security_group_ids   = local.is_aws ? [aws_security_group.redis[0].id] : null
}
