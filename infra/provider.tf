variable "use_localstack" {
  type        = bool
  default     = true
  description = "True para rodar no Floci local, False para AWS Academy"
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = var.use_localstack ? "mock_key" : null
  secret_key                  = var.use_localstack ? "mock_secret" : null
  skip_credentials_validation = var.use_localstack
  skip_metadata_api_check     = var.use_localstack
  skip_requesting_account_id  = var.use_localstack
  s3_use_path_style           = var.use_localstack

  # Redireciona chamadas para o Floci quando local
  dynamic "endpoints" {
    for_each = var.use_localstack ? [1] : []
    content {
      s3          = "http://localhost:4566"
      sns         = "http://localhost:4566"
      sqs         = "http://localhost:4566"
      dynamodb    = "http://localhost:4566"
      rds         = "http://localhost:4566"
      elasticache = "http://localhost:4566"
    }
  }
}
