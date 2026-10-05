# Rede da Parte 2 (somente AWS): usa a VPC default para não pagar NAT Gateway.
# As instâncias ficam em subnets públicas com IP público, e o acesso de entrada
# é restrito pelos security groups abaixo.

data "aws_vpc" "default" {
  count   = local.aws_count
  default = true
}

# Nem toda AZ oferece todo tipo de instância (ex.: us-east-1e não tem t3.micro).
data "aws_ec2_instance_type_offerings" "app" {
  count         = local.aws_count
  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

data "aws_subnets" "app" {
  count = local.aws_count

  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }

  filter {
    name   = "availability-zone"
    values = data.aws_ec2_instance_type_offerings.app[0].locations
  }
}

locals {
  # Duas AZs bastam (mínimo do ALB): cada subnet do ALB e cada instância têm um
  # IPv4 público cobrado por hora, então menos AZs = menos custo.
  app_subnet_ids = local.is_aws ? slice(sort(data.aws_subnets.app[0].ids), 0, 2) : []
}

resource "aws_security_group" "alb" {
  count       = local.aws_count
  name        = "estacionamento-alb"
  description = "HTTP publico para o Application Load Balancer"
  vpc_id      = data.aws_vpc.default[0].id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "app" {
  count       = local.aws_count
  name        = "estacionamento-app"
  description = "Instancias da aplicacao: HTTP somente a partir do ALB"
  vpc_id      = data.aws_vpc.default[0].id

  ingress {
    description     = "HTTP vindo do ALB"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb[0].id]
  }

  # Saída liberada: pull das imagens no ECR e APIs da AWS (S3, SQS, DynamoDB).
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds" {
  count       = local.aws_count
  name        = "estacionamento-rds"
  description = "PostgreSQL somente a partir das instancias da aplicacao"
  vpc_id      = data.aws_vpc.default[0].id

  ingress {
    description     = "PostgreSQL"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app[0].id]
  }
}

resource "aws_security_group" "redis" {
  count       = local.aws_count
  name        = "estacionamento-redis"
  description = "Redis somente a partir das instancias da aplicacao"
  vpc_id      = data.aws_vpc.default[0].id

  ingress {
    description     = "Redis"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.app[0].id]
  }
}
