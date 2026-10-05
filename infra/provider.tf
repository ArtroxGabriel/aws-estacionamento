terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
    random = {
      source = "hashicorp/random"
    }
  }
}

variable "use_localstack" {
  type        = bool
  default     = true
  description = "True para rodar no Floci local, False para a AWS real (AWS Academy ou conta própria)"
}

variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "Região AWS onde os recursos são criados"
}

variable "instance_type" {
  type        = string
  default     = "t3.micro"
  description = "Tipo das instâncias EC2 do Auto Scaling Group (x86_64)"
}

variable "instance_profile_name" {
  type        = string
  default     = ""
  description = "Instance profile já existente para as EC2 (\"LabInstanceProfile\" na AWS Academy). Vazio cria uma role IAM própria."
}

variable "aws_root" {
  type        = bool
  default     = false
  description = "Definido como true só pelo root infra/aws (state remoto no S3). Impede criar recursos da AWS com o state local de infra/."
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Tag das imagens no ECR usada pelas instâncias. O GitHub Actions passa o SHA do commit, o que gera um instance refresh a cada deploy."
}

locals {
  is_aws    = !var.use_localstack
  aws_count = local.is_aws ? 1 : 0
}

provider "aws" {
  region                      = var.aws_region
  access_key                  = var.use_localstack ? "mock_key" : null
  secret_key                  = var.use_localstack ? "mock_secret" : null
  skip_credentials_validation = var.use_localstack
  skip_metadata_api_check     = var.use_localstack
  skip_requesting_account_id  = var.use_localstack
  s3_use_path_style           = var.use_localstack

  # Tags em todos os recursos da AWS (filtro no Cost Explorer / Resource Groups).
  dynamic "default_tags" {
    for_each = var.use_localstack ? [] : [1]
    content {
      tags = {
        Project   = "aws-estacionamento"
        ManagedBy = "opentofu"
      }
    }
  }

  # Redireciona chamadas para o Floci quando local
  dynamic "endpoints" {
    for_each = var.use_localstack ? [1] : []
    content {
      s3          = "http://localhost:4566"
      sqs         = "http://localhost:4566"
      dynamodb    = "http://localhost:4566"
      rds         = "http://localhost:4566"
      elasticache = "http://localhost:4566"
    }
  }
}
