# Bootstrap (aplicado uma vez, por alguém com acesso de admin na conta):
# - bucket S3 do state remoto de infra/aws;
# - provider OIDC do GitHub Actions;
# - role assumida pelo workflow "Deploy AWS" (sem chaves de acesso no GitHub).
#
# O state deste diretório fica local (terraform.tfstate, fora do git).
# Não funciona na AWS Academy, que não permite criar IAM.

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "github_repository" {
  type        = string
  default     = "ArtroxGabriel/aws-estacionamento"
  description = "Repositório (owner/nome) autorizado a assumir a role de deploy"
}

variable "github_environment" {
  type        = string
  default     = "aws"
  description = "Environment do GitHub exigido no token OIDC (permite exigir aprovação antes do deploy)"
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "aws-estacionamento"
      ManagedBy = "opentofu"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
}

# --- State remoto ---

resource "aws_s3_bucket" "state" {
  bucket = "estacionamento-tofu-state-${local.account_id}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Versões antigas do state somem depois de 30 dias.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expira-versoes-antigas"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "SomenteTLS"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.state]
}

# --- GitHub OIDC ---

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

# Só jobs deste repositório rodando no environment configurado podem assumir.
resource "aws_iam_role" "deploy" {
  name                 = "estacionamento-github-deploy"
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:environment:${var.github_environment}"
        }
      }
    }]
  })
}

# Serviços da aplicação (EC2, ELB, ASG, RDS, ElastiCache, S3, SQS, DynamoDB,
# ECR, CloudWatch, SSM). O PowerUserAccess não inclui IAM, liberado abaixo só
# para os recursos estacionamento-*.
resource "aws_iam_role_policy_attachment" "deploy_power_user" {
  role       = aws_iam_role.deploy.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

resource "aws_iam_role_policy" "deploy_iam" {
  name = "estacionamento-iam"
  role = aws_iam_role.deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RoleDaAplicacao"
        Effect = "Allow"
        Action = [
          "iam:CreateRole",
          "iam:DeleteRole",
          "iam:GetRole",
          "iam:TagRole",
          "iam:UntagRole",
          "iam:UpdateAssumeRolePolicy",
          "iam:PutRolePolicy",
          "iam:GetRolePolicy",
          "iam:DeleteRolePolicy",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies",
          "iam:ListInstanceProfilesForRole",
        ]
        Resource = "arn:aws:iam::${local.account_id}:role/estacionamento-app"
      },
      {
        Sid      = "PoliticasGerenciadasPermitidas"
        Effect   = "Allow"
        Action   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
        Resource = "arn:aws:iam::${local.account_id}:role/estacionamento-app"
        Condition = {
          ArnEquals = {
            "iam:PolicyARN" = [
              "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
              "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
            ]
          }
        }
      },
      {
        Sid    = "InstanceProfile"
        Effect = "Allow"
        Action = [
          "iam:CreateInstanceProfile",
          "iam:DeleteInstanceProfile",
          "iam:GetInstanceProfile",
          "iam:TagInstanceProfile",
          "iam:UntagInstanceProfile",
          "iam:AddRoleToInstanceProfile",
          "iam:RemoveRoleFromInstanceProfile",
        ]
        Resource = "arn:aws:iam::${local.account_id}:instance-profile/estacionamento-app"
      },
      {
        Sid      = "PassRoleSoParaEC2"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "arn:aws:iam::${local.account_id}:role/estacionamento-app"
        Condition = {
          StringEquals = { "iam:PassedToService" = "ec2.amazonaws.com" }
        }
      },
      {
        Sid      = "StateRemoto"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      },
    ]
  })
}

output "state_bucket" {
  description = "Bucket do state remoto (tofu init -backend-config=\"bucket=...\")"
  value       = aws_s3_bucket.state.bucket
}

output "deploy_role_arn" {
  description = "Valor da variável AWS_ROLE_ARN no environment \"aws\" do GitHub"
  value       = aws_iam_role.deploy.arn
}
