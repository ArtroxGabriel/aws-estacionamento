resource "aws_s3_bucket" "fotos" {
  bucket = "estacionamento-fotos-veiculos-${var.use_localstack ? "local" : "prod"}"
}

resource "aws_sns_topic" "novo_veiculo" {
  name = "novo-veiculo-topico"
}

resource "aws_sqs_queue" "ocr_queue" {
  name                      = "ocr-processamento-fila"
  message_retention_seconds = 86400
}

resource "aws_sns_topic_subscription" "sns_to_sqs" {
  topic_arn = aws_sns_topic.novo_veiculo.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.ocr_queue.arn
}

resource "aws_sqs_queue_policy" "sqs_policy" {
  queue_url = aws_sqs_queue.ocr_queue.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = "*"
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.ocr_queue.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_sns_topic.novo_veiculo.arn
          }
        }
      }
    ]
  })
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
