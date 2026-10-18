output "master_instance_id" {
  value = aws_instance.master.id
}

output "connect" {
  description = "Open a shell on the Salt master (needs the Session Manager plugin)."
  value       = "aws ssm start-session --region ${var.region} --target ${aws_instance.master.id}"
}

output "bucket" {
  value = aws_s3_bucket.lab.id
}

output "minions" {
  value = local.all_minions
}

output "next_steps" {
  value = <<-EOT
    1. Wait ~10 minutes for bootstrap (Windows takes longest).
    2. aws ssm start-session --region ${var.region} --target ${aws_instance.master.id}
    3. sudo patchpilot-lab-init        # accept minions, build the lab with highstate
    4. sudo patchpilot validate --customer globex-industrial --env prod
    Everything terminates itself ${var.ttl_minutes} minutes after boot. `terraform destroy` when done.
  EOT
}
