# HTTPS (somente AWS): o ALB só aceitaria HTTPS com um certificado ACM de um
# domínio próprio, e *.elb.amazonaws.com não pode ter certificado. O CloudFront
# entrega https://<id>.cloudfront.net com o certificado padrão da AWS e repassa
# para o ALB em HTTP. Sem cache: a aplicação é dinâmica e os deploys trocam o
# index.html. O ALB segue acessível direto para o teste de carga do vídeo.

data "aws_cloudfront_cache_policy" "disabled" {
  count = local.aws_count
  name  = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  count = local.aws_count
  name  = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "app" {
  count           = local.aws_count
  enabled         = true
  comment         = "estacionamento"
  is_ipv6_enabled = true
  # Só América do Norte e Europa: a opção mais barata.
  price_class = "PriceClass_100"

  origin {
    origin_id   = "alb"
    domain_name = aws_lb.app[0].dns_name

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled[0].id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer[0].id
    compress                 = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
