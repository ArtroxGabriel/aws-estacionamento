# Parte 2 (somente AWS): imagens no ECR, instâncias EC2 num Auto Scaling Group
# (1 a 3) atrás de um Application Load Balancer, com alarmes de CPU de 1 minuto.

locals {
  app_images = toset(["api", "worker", "web"])
  registry   = local.is_aws ? "${data.aws_caller_identity.current[0].account_id}.dkr.ecr.${var.aws_region}.amazonaws.com" : ""
}

# --- Imagens ---

resource "aws_ecr_repository" "app" {
  for_each = local.is_aws ? local.app_images : toset([])

  name                 = "estacionamento-${each.key}"
  image_tag_mutability = "MUTABLE"
  # Permite o `tofu destroy` com imagens publicadas (infra efêmera).
  force_delete = true
}

# Cada push da tag latest deixa a imagem anterior sem tag, e cada deploy do
# GitHub Actions cria uma tag com o SHA: limpa ambos para não acumular custo.
resource "aws_ecr_lifecycle_policy" "app" {
  for_each   = aws_ecr_repository.app
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Remove imagens sem tag"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 1
      }
      action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Mantem as 10 imagens mais recentes"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
    }]
  })
}

# --- IAM (conta própria; na AWS Academy usar instance_profile_name = "LabInstanceProfile") ---

locals {
  create_iam            = local.is_aws && var.instance_profile_name == ""
  instance_profile_name = local.create_iam ? aws_iam_instance_profile.app[0].name : var.instance_profile_name
}

resource "aws_iam_role" "app" {
  count = local.create_iam ? 1 : 0
  name  = "estacionamento-app"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "app" {
  count = local.create_iam ? 1 : 0
  name  = "estacionamento-app"
  role  = aws_iam_role.app[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.fotos.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.ocr_queue.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"]
        Resource = aws_dynamodb_table.logs.arn
      },
    ]
  })
}

# Pull das imagens e acesso via Session Manager (debug e stress de CPU no vídeo).
resource "aws_iam_role_policy_attachment" "app" {
  for_each = local.create_iam ? toset([
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
  ]) : toset([])

  role       = aws_iam_role.app[0].name
  policy_arn = each.value
}

resource "aws_iam_instance_profile" "app" {
  count = local.create_iam ? 1 : 0
  name  = "estacionamento-app"
  role  = aws_iam_role.app[0].name
}

# --- Launch Template ---

data "aws_ami" "al2023" {
  count       = local.aws_count
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

resource "aws_launch_template" "app" {
  count                  = local.aws_count
  name                   = "estacionamento-app"
  image_id               = data.aws_ami.al2023[0].id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.app[0].id]
  update_default_version = true

  iam_instance_profile {
    name = local.instance_profile_name
  }

  # Métricas de 1 em 1 minuto: sem isso o CloudWatch só recebe CPU a cada
  # 5 minutos e os alarmes de 1 minuto da especificação não disparam.
  monitoring {
    enabled = true
  }

  # Hop limit 2: os containers (rede bridge) precisam alcançar o IMDSv2 para
  # obter as credenciais do instance profile.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  user_data = base64encode(templatefile("${path.module}/templates/user_data.sh.tftpl", {
    region       = var.aws_region
    registry     = local.registry
    api_image    = "${aws_ecr_repository.app["api"].repository_url}:${var.image_tag}"
    worker_image = "${aws_ecr_repository.app["worker"].repository_url}:${var.image_tag}"
    web_image    = "${aws_ecr_repository.app["web"].repository_url}:${var.image_tag}"
    bucket       = aws_s3_bucket.fotos.bucket
    queue_url    = aws_sqs_queue.ocr_queue.id
    table        = aws_dynamodb_table.logs.name
    db_user      = aws_db_instance.postgres.username
    db_password  = local.db_password
    db_host      = aws_db_instance.postgres.address
    db_port      = aws_db_instance.postgres.port
    db_name      = aws_db_instance.postgres.db_name
    redis_host   = aws_elasticache_replication_group.redis.primary_endpoint_address
    total_spots  = 50
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "estacionamento-app" }
  }
}

# --- Load Balancer ---

resource "aws_lb" "app" {
  count              = local.aws_count
  name               = "estacionamento-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb[0].id]
  subnets            = local.app_subnet_ids
}

resource "aws_lb_target_group" "app" {
  count                = local.aws_count
  name                 = "estacionamento-tg"
  port                 = 80
  protocol             = "HTTP"
  vpc_id               = data.aws_vpc.default[0].id
  deregistration_delay = 30

  # O nginx repassa /api/health para o GET /health da API: a instância só fica
  # saudável quando web e api estão de pé.
  health_check {
    path                = "/api/health"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  count             = local.aws_count
  load_balancer_arn = aws_lb.app[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app[0].arn
  }
}

# --- Auto Scaling Group ---

resource "aws_autoscaling_group" "app" {
  count                     = local.aws_count
  name                      = "estacionamento-asg"
  min_size                  = 1
  max_size                  = 3
  desired_capacity          = 1
  vpc_zone_identifier       = local.app_subnet_ids
  target_group_arns         = [aws_lb_target_group.app[0].arn]
  health_check_type         = "ELB"
  health_check_grace_period = 180

  # Métricas do grupo no console (instâncias em serviço etc.) para o vídeo.
  enabled_metrics = [
    "GroupDesiredCapacity",
    "GroupInServiceInstances",
    "GroupPendingInstances",
    "GroupTerminatingInstances",
    "GroupTotalInstances",
  ]

  launch_template {
    id      = aws_launch_template.app[0].id
    version = aws_launch_template.app[0].latest_version
  }

  # Troca as instâncias aos poucos quando o launch template muda.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
    }
  }

  tag {
    key                 = "Name"
    value               = "estacionamento-app"
    propagate_at_launch = true
  }

  lifecycle {
    # Não desfazer o que o scaling decidiu a cada `tofu apply`.
    ignore_changes = [desired_capacity]
  }

  # As instâncias rodam migrations e leem o Redis no boot.
  depends_on = [
    aws_db_instance.postgres,
    aws_elasticache_replication_group.redis,
  ]
}

# --- Políticas e alarmes (regras da especificação) ---
# Simple scaling + alarmes próprios: o target tracking cria alarmes com 3
# (scale-out) e 15 (scale-in) datapoints, o que não atende "por mais de 1 min".

resource "aws_autoscaling_policy" "scale_out" {
  count                  = local.aws_count
  name                   = "estacionamento-scale-out"
  autoscaling_group_name = aws_autoscaling_group.app[0].name
  policy_type            = "SimpleScaling"
  adjustment_type        = "ChangeInCapacity"
  scaling_adjustment     = 1
  cooldown               = 60
}

resource "aws_autoscaling_policy" "scale_in" {
  count                  = local.aws_count
  name                   = "estacionamento-scale-in"
  autoscaling_group_name = aws_autoscaling_group.app[0].name
  policy_type            = "SimpleScaling"
  adjustment_type        = "ChangeInCapacity"
  scaling_adjustment     = -1
  cooldown               = 60
}

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  count               = local.aws_count
  alarm_name          = "estacionamento-cpu-alta"
  alarm_description   = "CPU media do ASG acima de 70% por mais de 1 minuto: +1 instancia"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 70
  alarm_actions       = [aws_autoscaling_policy.scale_out[0].arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.app[0].name
  }
}

resource "aws_cloudwatch_metric_alarm" "cpu_low" {
  count               = local.aws_count
  alarm_name          = "estacionamento-cpu-baixa"
  alarm_description   = "CPU media do ASG abaixo de 25% por mais de 1 minuto: -1 instancia"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 25
  alarm_actions       = [aws_autoscaling_policy.scale_in[0].arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.app[0].name
  }
}
