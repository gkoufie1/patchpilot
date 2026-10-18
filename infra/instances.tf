# Red Hat's pay-as-you-go RHEL 9 images (the subscription is in the hourly price).
data "aws_ami" "rhel9" {
  most_recent = true
  owners      = ["309956199498"]
  filter {
    name   = "name"
    values = ["RHEL-9.*_HVM-*-x86_64-*-Hourly2-GP3"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

data "aws_ami" "windows" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["Windows_Server-2022-English-Full-Base-*"]
  }
}

locals {
  ttl = templatefile("${path.module}/templates/ttl.sh.tftpl", { ttl_minutes = var.ttl_minutes })

  linux_minions = merge(
    { "globex-app01" = { role = "app", type = var.app_type, disk = 20 } },
    var.enable_db ? { "globex-db01" = { role = "db", type = var.db_type, disk = 30 } } : {},
  )
  all_minions = concat(keys(local.linux_minions), var.enable_windows ? ["globex-win01"] : [])
}

resource "aws_instance" "master" {
  ami                                  = data.aws_ami.rhel9.id
  instance_type                        = var.master_type
  subnet_id                            = aws_subnet.lab.id
  vpc_security_group_ids               = [aws_security_group.lab.id]
  iam_instance_profile                 = aws_iam_instance_profile.node.name
  instance_initiated_shutdown_behavior = var.use_spot ? null : "terminate" # one-time Spot already terminates on shutdown

  dynamic "instance_market_options" {
    for_each = var.use_spot ? [1] : []
    content {
      market_type = "spot"
      spot_options {
        spot_instance_type             = "one-time"
        instance_interruption_behavior = "terminate"
      }
    }
  }

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  user_data = templatefile("${path.module}/templates/master.sh.tftpl", {
    ttl     = local.ttl
    bucket  = aws_s3_bucket.lab.id
    region  = var.region
    minions = join(" ", local.all_minions)
  })
  user_data_replace_on_change = true

  tags       = { Name = "${var.project}-master" }
  depends_on = [aws_s3_object.bundle, aws_iam_role_policy.bucket]
}

resource "aws_instance" "linux" {
  for_each = local.linux_minions

  ami                                  = data.aws_ami.rhel9.id
  instance_type                        = each.value.type
  subnet_id                            = aws_subnet.lab.id
  vpc_security_group_ids               = [aws_security_group.lab.id]
  iam_instance_profile                 = aws_iam_instance_profile.node.name
  instance_initiated_shutdown_behavior = var.use_spot ? null : "terminate" # one-time Spot already terminates on shutdown

  dynamic "instance_market_options" {
    for_each = var.use_spot ? [1] : []
    content {
      market_type = "spot"
      spot_options {
        spot_instance_type             = "one-time"
        instance_interruption_behavior = "terminate"
      }
    }
  }

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1 # Podman containers on the DB node can't reach the instance role
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = each.value.disk
    encrypted   = true
  }

  user_data = templatefile("${path.module}/templates/minion.sh.tftpl", {
    ttl       = local.ttl
    master_ip = aws_instance.master.private_ip
    minion_id = each.key
    role      = each.value.role
  })
  user_data_replace_on_change = true

  tags = { Name = "${var.project}-${each.key}" }
}

resource "aws_instance" "windows" {
  count = var.enable_windows ? 1 : 0

  ami                                  = data.aws_ami.windows.id
  instance_type                        = var.win_type
  subnet_id                            = aws_subnet.lab.id
  vpc_security_group_ids               = [aws_security_group.lab.id]
  iam_instance_profile                 = aws_iam_instance_profile.node.name
  instance_initiated_shutdown_behavior = var.use_spot ? null : "terminate" # one-time Spot already terminates on shutdown

  dynamic "instance_market_options" {
    for_each = var.use_spot ? [1] : []
    content {
      market_type = "spot"
      spot_options {
        spot_instance_type             = "one-time"
        instance_interruption_behavior = "terminate"
      }
    }
  }

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 40
    encrypted   = true
  }

  user_data = templatefile("${path.module}/templates/windows.ps1.tftpl", {
    ttl_minutes = var.ttl_minutes
    master_ip   = aws_instance.master.private_ip
    minion_id   = "globex-win01"
  })
  user_data_replace_on_change = true

  tags = { Name = "${var.project}-globex-win01" }
}
