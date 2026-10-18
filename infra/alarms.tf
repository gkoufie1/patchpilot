# Status-check alarms: hear about a frozen or unreachable server within minutes,
# instead of discovering it when the next patch run aborts.
#
# StatusCheckFailed_Instance  = the OS stopped answering (hung, out of memory, kernel crash)
# StatusCheckFailed_System    = AWS's host/hardware under the instance has a problem
#
# Notify-only on purpose: an automatic reboot would wipe the evidence (console,
# memory state) you need to find out *why* the server froze.

variable "alert_email" {
  description = "Email for status-check alarms. Empty = alarms exist but notify nobody."
  type        = string
  default     = ""
}

locals {
  monitored = merge(
    { "patchpilot-master" = aws_instance.master.id },
    { for k, i in aws_instance.linux : k => i.id },
    var.enable_windows ? { "globex-win01" = aws_instance.windows[0].id } : {},
  )
  alarm_actions = var.alert_email == "" ? [] : [aws_sns_topic.alerts[0].arn]
}

resource "aws_sns_topic" "alerts" {
  count = var.alert_email == "" ? 0 : 1
  name  = "${var.project}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.alert_email # AWS emails a confirmation link; alerts start after you click it
}

resource "aws_cloudwatch_metric_alarm" "status_check" {
  for_each = local.monitored

  alarm_name          = "${var.project}-${each.key}-status-check"
  alarm_description   = "${each.key} failed an EC2 status check (OS or host not responding)"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { InstanceId = each.value }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3 # 3 minutes in a row, so a reboot during patching doesn't page anyone
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching" # no data yet at boot; a frozen OS still reports a *failed* check
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}
