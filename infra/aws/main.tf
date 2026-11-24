# Root da AWS real: mesma infraestrutura de infra/ (usada como módulo), com o
# state no S3 criado por infra/bootstrap. Compartilhado entre as tasks
# tf:*:aws e o workflow "Deploy AWS" do GitHub Actions.
#
# O bucket do state vem por -backend-config (o nome leva o account id):
#   tofu init -backend-config="bucket=estacionamento-tofu-state-<account_id>"

terraform {
  backend "s3" {
    key          = "aws-estacionamento/aws.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

variable "instance_profile_name" {
  type        = string
  default     = ""
  description = "Instance profile já existente (\"LabInstanceProfile\" na AWS Academy). Vazio cria a role estacionamento-app."
}

variable "instance_type" {
  type        = string
  default     = "t3.micro"
  description = "Tipo das instâncias EC2 do ASG. A instância roda api, worker (Tesseract), web e RabbitMQ: com 1 GiB (t3.micro) fica no limite."
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Tag das imagens no ECR (o GitHub Actions passa o SHA do commit)."
}

module "app" {
  source = "../"

  use_localstack        = false
  aws_root              = true
  instance_profile_name = var.instance_profile_name
  image_tag             = var.image_tag
  instance_type         = var.instance_type
}
